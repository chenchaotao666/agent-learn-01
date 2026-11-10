import asyncio
from pathlib import Path

import httpx
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from fastapi import BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from src.feedback import FeedbackService
from src.memory import MemoryService
from src.parsing import parse_document
from src.repository import Conflict, Repository
from src.runtime import Runtime
from src.schemas import (Confirmation, EvaluationInput, FeedbackInput, FeedbackReview, MemoryInput,
                         MemoryUpdate, RunInput, RunRevision, SpeechInput, TranslationInput)
from src.speech import SpeechService
from src.translation import translate
from src.schemas import SearchInput, IngestionInput
from src.ingestions import Ingestions
from typing import Literal


def attach(app, settings, store, rag, model):
    repo = Repository(store)
    runtime = Runtime(repo, rag, settings)
    memory = MemoryService(repo)
    feedback = FeedbackService(repo, rag, model)
    speech = SpeechService(settings, repo)
    app.state.repo, app.state.runtime, app.state.memory = repo, runtime, memory
    jobs = Ingestions(store, rag)
    app.state.ingestions = jobs

    @app.post("/v1/ingestions")
    def enqueue(body: IngestionInput):
        return jobs.create(body)

    @app.post("/v1/ingestions/upload")
    async def enqueue_file(file: UploadFile = File(...), client_request_id: str = Form(...),
                           strategy: Literal["fixed", "structure"] = Form("structure"), document_id: str | None = Form(None)):
        try:
            text = parse_document(file.filename or "", await file.read(10_000_001))
        except Exception:
            raise HTTPException(422, "文件解析失败，请检查格式、大小和文本层") from None
        # The queue stores parsed UTF-8 text; original filenames remain in document metadata.
        body = IngestionInput(title=Path(file.filename).stem, filename=Path(file.filename).name, text=text,
                              strategy=strategy, document_id=document_id, client_request_id=client_request_id)
        return jobs.create(body)

    @app.get("/v1/ingestions")
    def ingestion_list():
        return jobs.list()

    @app.get("/v1/ingestions/{rid}")
    def ingestion(rid: str):
        return jobs.get(rid)

    @app.post("/v1/ingestions/{rid}/retry")
    def retry_ingestion(rid: str):
        return jobs.change(rid, "retry")

    @app.post("/v1/ingestions/{rid}/cancel")
    def cancel_ingestion(rid: str):
        return jobs.change(rid, "cancel")

    @app.get("/workbench", include_in_schema=False)
    def workbench():
        from src.config import ROOT
        return FileResponse(ROOT / "apps/web/workbench.html")

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "资源不存在"})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)[:500]})

    @app.exception_handler(httpx.HTTPError)
    @app.exception_handler(ModelHTTPError)
    @app.exception_handler(UnexpectedModelBehavior)
    async def upstream(request, exc):
        return JSONResponse(status_code=502, content={"detail": "外部服务未完成请求，请检查对应配置"})

    @app.exception_handler(TimeoutError)
    async def timeout(request, exc):
        return JSONResponse(status_code=504, content={"detail": "请求超时，请稍后重试"})

    @app.get("/v1/projects")
    def projects():
        from src.business import PROJECTS, get_budget
        return [get_budget(pid) for pid in PROJECTS]

    @app.post("/v1/search/inspect")
    async def inspect(body: SearchInput):
        return await rag.inspect(body.query, body.top_k, body.mode, body.rewrite, body.rerank)

    @app.get("/v1/skills")
    def skills():
        return runtime.skills.catalog()

    @app.post("/v1/runs")
    def create_run(body: RunInput, background: BackgroundTasks):
        run = runtime.create(body)
        if run["status"] == "created":
            background.add_task(runtime.execute, run["id"])
        return run

    @app.get("/v1/runs")
    def runs():
        return repo.list("run")

    @app.get("/v1/runs/{rid}")
    def run(rid: str):
        return repo.get("run", rid)

    @app.post("/v1/runs/{rid}/input")
    def revise(rid: str, body: RunRevision, background: BackgroundTasks):
        run = runtime.revise(rid, body)
        background.add_task(runtime.execute, rid)
        return run

    @app.post("/v1/runs/{rid}/resume")
    def resume(rid: str, background: BackgroundTasks):
        run = repo.get("run", rid)
        if runtime.locks.get(rid) and runtime.locks[rid].locked():
            raise Conflict("任务正在执行")
        if run["status"] in {"cancelled", "succeeded", "awaiting_confirmation"}:
            raise Conflict("当前状态无需恢复")
        # An empty retrieval is not a permanent cached result after documents are added.
        if not run["results"].get("search_knowledge"):
            run["results"].pop("search_knowledge", None)
        run = runtime.patch(rid, status="created", results=run["results"])
        background.add_task(runtime.execute, rid)
        return run

    @app.post("/v1/runs/{rid}/confirm")
    def confirm(rid: str, body: Confirmation):
        return runtime.confirm(rid, body)

    @app.post("/v1/runs/{rid}/cancel")
    def cancel(rid: str):
        return runtime.cancel(rid)

    @app.get("/v1/memories")
    def memories():
        return memory.active_records()

    @app.post("/v1/memories")
    def remember(body: MemoryInput):
        return memory.create(body)

    @app.patch("/v1/memories/{rid}")
    def update_memory(rid: str, body: MemoryUpdate):
        return memory.update(rid, body)

    @app.delete("/v1/memories/{rid}")
    def forget(rid: str):
        return memory.delete(rid)

    @app.get("/v1/conversations/{cid}/context")
    def context(cid: str):
        return memory.context(cid, "")[1]

    @app.post("/v1/feedback")
    def add_feedback(body: FeedbackInput):
        return feedback.create(body)

    @app.get("/v1/feedback")
    def list_feedback():
        return repo.list("feedback")

    @app.post("/v1/feedback/{rid}/review")
    def review(rid: str, body: FeedbackReview):
        return feedback.review(rid, body)

    @app.post("/v1/datasets")
    def publish():
        return feedback.publish()

    @app.get("/v1/datasets")
    def datasets():
        return repo.list("dataset")

    @app.post("/v1/evaluations")
    async def evaluate(body: EvaluationInput):
        async with asyncio.timeout(settings.timeout * 5):
            return await feedback.evaluate(body)

    @app.get("/v1/evaluations")
    def evaluations():
        return repo.list("evaluation")

    @app.get("/v1/evaluations/compare")
    def compare(first: str, second: str):
        return feedback.compare(first, second)

    @app.get("/v1/evaluations/{rid}")
    def evaluation(rid: str):
        return repo.get("evaluation", rid)

    @app.post("/v1/translations")
    async def translation(body: TranslationInput):
        try:
            async with asyncio.timeout(settings.timeout):
                return await translate(settings, body)
        except Exception:
            raise HTTPException(502, "翻译未完成，请检查模型配置；不会自动改用模拟结果") from None

    @app.post("/v1/documents/upload")
    async def upload(file: UploadFile = File(...), document_id: str | None = Form(None)):
        data = await file.read(10_000_001)
        try:
            text = parse_document(file.filename or "", data)
        except Exception:
            raise HTTPException(422, "无法解析文件，请使用 UTF-8 文本、原生文本 PDF 或 DOCX，并检查大小限制") from None
        return await rag.ingest(Path(file.filename).stem, Path(file.filename).name, text, document_id)

    @app.post("/v1/audio/transcriptions")
    async def transcribe(file: UploadFile = File(...)):
        return await speech.transcribe(await file.read(20_000_001), file.filename or "recording.webm", file.content_type or "")

    @app.post("/v1/audio/speech")
    async def speak(body: SpeechInput):
        result = await speech.speak(body)
        return {k: v for k, v in result.items() if k != "path"}

    @app.get("/v1/audio/{rid}")
    def audio(rid: str):
        value = repo.get("audio", rid)
        return FileResponse(value["path"], media_type=value["mime"])
