import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.main import ChatInput, create_app
from src.config import Settings
from src.model import Model
from src.rag.chunking import split_text
from src.rag.embedding import Embedder, hash_vector


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(qdrant_url="", qdrant_path=":memory:", database=str(tmp_path / "test.sqlite3")))) as client:
        yield client


def document(client, text="国内差旅住宿报销上限为每晚 600 元。", **overrides):
    body = {"title": "差旅住宿标准", "filename": "travel.md", "text": text, **overrides}
    response = client.post("/v1/documents", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def events(response):
    return [(frame.splitlines()[0][7:], json.loads(frame.splitlines()[1][6:]))
            for frame in response.text.strip().split("\n\n")]


def test_seed_is_idempotent_and_ui_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.post("/v1/demo/seed").json()["imported"] == 20
    assert client.post("/v1/demo/seed").json()["imported"] == 0
    assert len(client.get("/v1/documents").json()) == 20


def test_chat_citations_and_persisted_history(client):
    document(client)
    result = events(client.post("/v1/chat", json={"question": "差旅住宿报销上限？"}))
    assert result[-1][1]["status"] == "complete"
    sources = next(data for name, data in result if name == "sources")
    assert sources and sources[0]["start_line"] == 1
    answer = "".join(data["text"] for name, data in result if name == "delta")
    assert "600" in answer and "模拟回答" in answer
    cid = result[0][1]["conversation_id"]
    messages = client.get(f"/v1/conversations/{cid}").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == answer
    assert client.get(f"/v1/citations/{sources[0]['id']}").status_code == 200


def test_update_atomic_versioning_dedup_and_delete(client):
    first = document(client)
    assert document(client)["deduplicated"] is True
    old = events(client.post("/v1/chat", json={"question": "差旅住宿报销上限？"}))
    old_source = next(data for name, data in old if name == "sources")[0]
    second = document(client, "国内差旅住宿报销上限为每晚 800 元。", document_id=first["id"])
    assert second["version"] == 2
    current = events(client.post("/v1/chat", json={"question": "差旅住宿报销上限？"}))
    sources = next(data for name, data in current if name == "sources")
    assert len(sources) == 1 and sources[0]["version"] == 2 and "800" in sources[0]["text"]
    historical = client.get(f"/v1/citations/{old_source['id']}").json()
    assert historical["version"] == 1 and historical["active_version"] == 2
    assert client.delete(f"/v1/documents/{first['id']}").status_code == 200
    assert client.get(f"/v1/citations/{old_source['id']}").status_code == 404
    history = client.get(f"/v1/conversations/{old[0][1]['conversation_id']}").json()
    assert history[-1]["citations"][0]["unavailable"] is True
    after = events(client.post("/v1/chat", json={"question": "差旅住宿报销上限？"}))
    assert next(data for name, data in after if name == "sources") == []


def test_no_sources_refuses_without_calling_remote(client):
    class NeverCalled:
        async def stream(self, question, hits):
            assert not hits
            yield "当前知识库中没有足够依据"
    client.app.state.model = NeverCalled()
    response = client.post("/v1/chat", json={"question": "量子纠缠是什么？"})
    assert "没有足够依据" in response.text


def test_partial_model_failure_is_persisted_as_failed(client):
    document(client)
    class Failing:
        async def stream(self, question, hits):
            yield "部分回答"
            raise httpx.ReadTimeout("timeout")
    client.app.state.model = Failing()
    result = events(client.post("/v1/chat", json={"question": "差旅住宿报销上限？"}))
    assert any(name == "error" for name, _ in result)
    assert result[-1][1]["status"] == "failed"
    cid = result[0][1]["conversation_id"]
    saved = client.get(f"/v1/conversations/{cid}").json()[-1]
    assert saved["status"] == "failed" and saved["content"] == "部分回答"


def test_failed_embedding_does_not_publish_new_version(client, monkeypatch):
    first = document(client)
    async def fail(texts):
        raise httpx.ConnectError("unavailable")
    monkeypatch.setattr(client.app.state.rag.embedder, "encode", fail)
    response = client.post("/v1/documents", json={"title": "差旅住宿标准", "filename": "travel.md",
                           "text": "新内容", "document_id": first["id"]})
    assert response.status_code == 502
    assert client.get("/v1/documents").json()[0]["active_version"] == 1


@pytest.mark.parametrize("body", [
    {"title": "a", "filename": "a.pdf", "text": "text"},
    {"title": " ", "filename": "a.md", "text": "text"},
    {"title": "a", "filename": "a.txt", "text": "   "},
])
def test_invalid_import_rejected(client, body):
    assert client.post("/v1/documents", json=body).status_code == 422


def test_missing_conversation_and_blank_question(client):
    assert client.post("/v1/chat", json={"question": "q", "conversation_id": "missing"}).status_code == 404
    assert client.post("/v1/chat", json={"question": " "}).status_code == 422


def test_chunk_locations_and_stable_vectors():
    text = "第一行\n第二行\n" * 200
    chunks = split_text(text)
    assert len(chunks) > 1
    assert chunks[1]["start_line"] == text.count("\n", 0, 390) + 1
    vector = hash_vector("差旅住宿 P1001")
    assert vector == hash_vector("差旅住宿 P1001")
    assert sum(v * v for v in vector) == pytest.approx(1)


def test_embedding_namespaces_prevent_mixed_search(client):
    document(client)
    assert client.app.state.store.candidates("different-model") == []


def test_ollama_embedding_contract(monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        assert request.url.path == "/api/embed"
        assert json.loads(request.content)["truncate"] is False
        return httpx.Response(200, json={"embeddings": [[3, 4]]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(Embedder(Settings(embedding_mode="ollama")).encode(["q"])) == [[0.6, 0.8]]


def test_stream_cancellation_persists_interrupted_status(tmp_path):
    app = create_app(Settings(qdrant_url="", qdrant_path=":memory:", database=str(tmp_path / "cancel.sqlite3")))
    class Slow:
        async def stream(self, question, hits):
            yield "第一段"
            await asyncio.sleep(20)
    app.state.model = Slow()
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/v1/chat")
    async def run():
        response = await endpoint(ChatInput(question="测试取消"))
        iterator = response.body_iterator
        meta = await anext(iterator)
        cid = json.loads(meta.splitlines()[1][6:])["conversation_id"]
        await anext(iterator)  # sources
        await anext(iterator)  # first content
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        saved = app.state.store.messages(cid)[-1]
        assert saved["content"] == "第一段" and saved["status"] == "interrupted"
        # The lock is released; a subsequent message is allowed.
        followup = await endpoint(ChatInput(question="继续", conversation_id=cid))
        await anext(followup.body_iterator)
        await followup.body_iterator.aclose()
    asyncio.run(run())


def test_total_model_timeout_is_reported(tmp_path):
    app = create_app(Settings(qdrant_url="", qdrant_path=":memory:", database=str(tmp_path / "timeout.sqlite3"), timeout=0.01))
    class Slow:
        async def stream(self, question, hits):
            await asyncio.sleep(1)
            yield "unreachable"
    app.state.model = Slow()
    with TestClient(app) as client:
        result = events(client.post("/v1/chat", json={"question": "测试超时"}))
        assert result[-1][1]["status"] == "failed"
        assert any(name == "error" for name, _ in result)
