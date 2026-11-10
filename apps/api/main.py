import asyncio
import json
import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from src.schemas import ChatInput, DocumentInput

from src.config import ROOT, Settings
from src.rag.agent import Model
from src.rag.embedding import Embedder
from src.rag.service import RagService
from src.store import Store

logger = logging.getLogger("office")


def event(name, payload):
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    embedder = Embedder(settings)
    rag = RagService(store, embedder, settings.min_score)
    model = Model(settings)
    app = FastAPI(title="知行 · AI 办公实验室", version="0.3.0")
    app.state.store, app.state.rag, app.state.model = store, rag, model
    from apps.api.extensions import attach
    attach(app, settings, store, rag, model)
    busy_conversations = set()
    app.mount("/static", StaticFiles(directory=ROOT / "apps/web"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "apps/web/index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "model_mode": settings.model_mode,
                "embedding_mode": settings.embedding_mode, "embedding_key": embedder.key,
                "chat_model": settings.deepseek_model if settings.model_mode == "deepseek" else settings.chat_model if settings.model_mode == "ollama" else "mock",
                'configured': settings.model_mode != 'deepseek' or bool(settings.deepseek_api_key),
                'agent_framework': 'pydantic-ai', 'tool_transport': settings.tool_transport,
                'asr_configured': bool(settings.speech_url and settings.asr_model),
                'tts_configured': bool(settings.speech_url and settings.tts_model),
                'retrieval_mode': settings.retrieval_mode, 'keyword_backend': settings.keyword_backend,
                'vector_backend': settings.vector_backend, 'rewrite': settings.rewrite_mode, 'rerank': settings.rerank_mode,
                'version': '0.3.0'}

    @app.get("/v1/documents")
    def documents():
        return store.documents()

    @app.post("/v1/documents")
    async def ingest(body: DocumentInput):
        try:
            async with asyncio.timeout(settings.timeout):
                result = await rag.ingest(body.title.strip(), body.filename, body.text, body.document_id)
            return {**result, "status": "published"}
        except KeyError:
            raise HTTPException(404, "待更新的文档不存在") from None
        except (httpx.HTTPError, TimeoutError):
            raise HTTPException(502, "Embedding 服务不可用或超时；原文档版本保持不变") from None
        except (ValueError, TypeError):
            raise HTTPException(422, "入库失败，请检查文档和 Embedding 响应格式") from None

    @app.delete("/v1/documents/{document_id}")
    def delete_document(document_id: str):
        if not store.delete(document_id):
            raise HTTPException(404, "文档不存在")
        return {"deleted": True}

    @app.get("/v1/citations/{chunk_id}")
    def citation(chunk_id: str):
        result = store.citation(chunk_id)
        if not result:
            raise HTTPException(404, "引用来源已删除或不存在")
        return result

    @app.post("/v1/demo/seed")
    async def seed():
        records = json.loads((ROOT / "fixtures/documents.json").read_text(encoding="utf-8"))
        existing = {d["filename"] for d in store.documents()}
        imported = 0
        try:
            for record in records:
                # Repeated seeding never overwrites user updates.
                if record["filename"] in existing:
                    continue
                async with asyncio.timeout(settings.timeout):
                    await rag.ingest(record["title"], record["filename"], record["text"])
                imported += 1
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
            raise HTTPException(502, f"演示资料已导入 {imported} 份，其余失败；检查模型服务后可重试") from None
        return {"imported": imported, "total": len(store.documents())}

    @app.get("/v1/conversations")
    def conversations():
        return store.conversations()

    @app.get("/v1/conversations/{conversation_id}")
    def messages(conversation_id: str):
        try:
            return store.messages(conversation_id)
        except KeyError:
            raise HTTPException(404, "会话不存在") from None

    @app.post("/v1/chat")
    async def chat(body: ChatInput):
        cid = body.conversation_id
        if cid:
            try:
                store.messages(cid)
            except KeyError:
                raise HTTPException(404, "会话不存在") from None
        else:
            cid = store.create_conversation(body.question)
        if cid in busy_conversations:
            raise HTTPException(409, "该会话仍在生成回答，请完成或停止后再发送")
        busy_conversations.add(cid)

        async def generate():
            answer = ""
            status = "interrupted"
            citation_ids = []
            try:
                store.add_message(cid, "user", body.question, "complete", settings.model_mode)
                yield event("meta", {"conversation_id": cid, "mode": settings.model_mode})
                async with asyncio.timeout(settings.timeout):
                    resolved, context = app.state.memory.context(cid, body.question)
                    hits = await rag.search(resolved, body.top_k)
                    # Recheck before exposing sources in case an import/delete overlapped embedding.
                    hits = [h for h in hits if (c := store.citation(h["id"])) and c["version"] == c["active_version"]]
                    citation_ids = [h["id"] for h in hits]
                    yield event("sources", hits)
                    async for delta in app.state.model.stream(json.dumps({"question": resolved, "context": context}, ensure_ascii=False), hits):
                        answer += delta
                        yield event("delta", {"text": delta})
                    if not answer.strip():
                        raise ValueError("empty model output")
                status = "complete"
            except asyncio.CancelledError:
                logger.info("chat_cancelled conversation=%s", cid)
                raise
            except Exception:
                status = "failed"
                logger.warning("chat_failed conversation=%s", cid)
                message = '请在 .env 配置 DEEPSEEK_API_KEY 后重启服务。' if settings.model_mode == 'deepseek' and not settings.deepseek_api_key else '模型或检索未完成响应，请检查配置；已有输出已保存。'
                yield event('error', {'message': message})
            finally:
                store.add_message(cid, "assistant", answer, status, settings.model_mode, citation_ids)
                busy_conversations.discard(cid)
            yield event("done", {"status": status, "conversation_id": cid})

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
