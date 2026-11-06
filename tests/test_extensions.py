import asyncio
import io
import json
import wave
from dataclasses import replace

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from apps.api.main import create_app
from src.config import Settings
from src.rag.agent import Model
from src.tool_transport import ToolTransport


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(database=str(tmp_path / "db.sqlite3")))) as c:
        c.post('/v1/demo/seed')
        yield c


def chat(client, question="国内差旅住宿报销上限是多少？", cid=None):
    r = client.post('/v1/chat', json={"question": question, "conversation_id": cid})
    frames = [json.loads(f.splitlines()[1][6:]) for f in r.text.strip().split('\n\n')]
    return frames[0]['conversation_id']


def create_run(client, **changes):
    body = {"goal": "检查项目采购预算并生成草稿", "project_id": "P1001", "amount": "200000.00", "client_request_id": "case-1", **changes}
    response = client.post('/v1/runs', json=body)
    assert response.status_code == 200, response.text
    return client.get('/v1/runs/' + response.json()['id']).json()


def test_agent_uses_four_tools_and_confirmation_is_idempotent(client):
    run = create_run(client)
    assert run['status'] == 'awaiting_confirmation', run
    assert [s['action'] for s in run['steps']] == ['get_budget', 'search_knowledge', 'calculate_gap', 'create_draft']
    assert run['draft']['gap'] == '50000.00'
    assert run['skill']['name'] == 'budget-review'
    body = {"version": run['version'], "draft_hash": run['draft_hash']}
    first = client.post(f"/v1/runs/{run['id']}/confirm", json=body)
    second = client.post(f"/v1/runs/{run['id']}/confirm", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()['approval_id'] == second.json()['approval_id']
    assert client.post(f"/v1/runs/{run['id']}/cancel", json={}).status_code == 409
    with client.app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM approvals').fetchone()[0] == 1


def test_replan_invalidates_old_confirmation(client):
    run = create_run(client)
    old = {"version": run['version'], "draft_hash": run['draft_hash']}
    r = client.post(f"/v1/runs/{run['id']}/input", json={"version": run['version'], "project_id": "P2002", "amount": "300000.00"})
    assert r.status_code == 200
    assert client.post(f"/v1/runs/{run['id']}/confirm", json=old).status_code == 409
    new = client.get(f"/v1/runs/{run['id']}").json()
    assert new['draft']['project_id'] == 'P2002' and new['draft']['gap'] == '0.00'
    assert new['plan_version'] == 2


def test_missing_fields_clarify_and_duplicate_request_conflicts(client):
    run = create_run(client, project_id=None, amount=None)
    assert run['status'] == 'awaiting_input'
    assert create_run(client, project_id=None, amount=None)['id'] == run['id']
    r = client.post('/v1/runs', json={"goal": "different", "client_request_id": "case-1"})
    assert r.status_code == 409
    client.post(f"/v1/runs/{run['id']}/input", json={"version": run['version'], "project_id": "P1001", "amount": "100"})
    assert client.get(f"/v1/runs/{run['id']}").json()['status'] == 'awaiting_confirmation'


def test_deleted_evidence_blocks_confirmation(client):
    run = create_run(client)
    citation = client.get('/v1/citations/' + run['draft']['citations'][0]).json()
    client.delete('/v1/documents/' + citation['document_id'])
    assert client.post(f"/v1/runs/{run['id']}/confirm", json={"version": run['version'], "draft_hash": run['draft_hash']}).status_code == 409


def test_resume_after_tool_failure_uses_checkpoint(client, monkeypatch):
    transport = client.app.state.runtime.transport
    original = transport.call
    async def fail(name, arguments):
        if name == 'calculate_gap':
            raise ValueError('injected failure')
        return await original(name, arguments)
    monkeypatch.setattr(transport, 'call', fail)
    run = create_run(client)
    assert run['status'] == 'failed'
    assert 'get_budget' in run['results']
    monkeypatch.setattr(transport, 'call', original)
    client.post(f"/v1/runs/{run['id']}/resume", json={})
    resumed = client.get(f"/v1/runs/{run['id']}").json()
    assert resumed['status'] == 'awaiting_confirmation'
    assert sum(s['action'] == 'get_budget' for s in resumed['steps']) == 1


def test_multiturn_project_switch_and_memory_forgetting(client):
    cid = chat(client, '查询 P1001 的预算')
    memory = client.app.state.memory
    resolved, _ = memory.context(cid, '它还能采购多少？')
    assert 'P1001' in resolved
    chat(client, '改为 P2002', cid)
    resolved, context = memory.context(cid, '它还能采购多少？')
    assert context['project_id'] == 'P2002' and 'P2002' in resolved
    source_id = client.get('/v1/conversations/' + cid).json()[0]['id']
    m = client.post('/v1/memories', json={"value": "先给结论", "project_id": "P2002", "source_message_id": source_id, "confirmed": True}).json()
    assert memory.list('P1001') == []
    assert memory.list('P2002')[0]['value'] == '先给结论'
    r = client.patch('/v1/memories/' + m['id'], json={"version": m['version'], "value": "先列证据"})
    assert r.status_code == 200
    assert memory.context(cid, '它如何')[1]['memories'][0]['value'] == '先列证据'
    client.delete('/v1/memories/' + m['id'])
    assert memory.context(cid, '它如何')[1]['memories'] == []


def test_feedback_review_dataset_and_comparison(client):
    cid = chat(client)
    messages = client.get('/v1/conversations/' + cid).json()
    msg = messages[-1]
    f = client.post('/v1/feedback', json={"message_id": msg['id'], "category": "fact", "correction": "600"}).json()
    assert client.post('/v1/datasets', json={}).status_code == 422
    review = {"version": f['version'], "decision": "accepted", "expected_answer": "600", "evidence_ids": [msg['citations'][0]['id']], "reason": "已核对原文"}
    assert client.post(f"/v1/feedback/{f['id']}/review", json=review).status_code == 200
    dataset = client.post('/v1/datasets', json={}).json()
    assert dataset['split'] == 'regression'
    assert client.post('/v1/datasets', json={}).json()['id'] == dataset['id']
    a = client.post('/v1/evaluations', json={"dataset_id": dataset['id'], "top_k": 1}).json()
    b = client.post('/v1/evaluations', json={"dataset_id": dataset['id'], "top_k": 3}).json()
    comparison = client.get('/v1/evaluations/compare', params={"first": a['id'], "second": b['id']})
    assert comparison.status_code == 200
    assert a['results'][0]['semantic_review'] == 'pending'


@pytest.mark.parametrize('amount', [0, 1.5, '-2', 'NaN', '0', '1.111', '100000000000'])
def test_money_rejects_invalid_types_and_values(client, amount):
    assert client.post('/v1/runs', json={"goal": "审查预算", "amount": amount, "client_request_id": "invalid"}).status_code == 422


@pytest.mark.parametrize('body', [{"question":"hi","top_k":"3"},{"question":"hi","extra":"x"},{"question":"   "}])
def test_strict_api_boundary(client, body):
    assert client.post('/v1/chat', json=body).status_code == 422


def test_deepseek_missing_key_is_explicit(tmp_path):
    with TestClient(create_app(Settings(database=str(tmp_path/'ds.db'), model_mode='deepseek'))) as c:
        assert c.get('/health').json()['configured'] is False
        assert 'DEEPSEEK_API_KEY' in c.post('/v1/chat', json={"question":"hello"}).text
        run = create_run(c)
        assert run['status'] == 'failed' and 'DEEPSEEK_API_KEY' in run['error']


@pytest.mark.parametrize('mode', ['deepseek','ollama'])
def test_pydantic_ai_real_provider_stream_contract(monkeypatch, mode):
    import src.ai
    def handler(request):
        assert request.url.path.endswith('/chat/completions')
        body = json.loads(request.content)
        assert body['stream'] is True
        assert body['model'] == ('deepseek-flash' if mode=='deepseek' else 'qwen3:4b')
        if mode == 'deepseek':
            assert request.headers['authorization'] == 'Bearer unit-test-placeholder'
        chunks = [
            {"id":"c1","object":"chat.completion.chunk","created":1,"model":body['model'],"choices":[{"index":0,"delta":{"content":"结果 [1]"},"finish_reason":None}]},
            {"id":"c1","object":"chat.completion.chunk","created":1,"model":body['model'],"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]},
        ]
        return httpx2.Response(200, headers={'content-type':'text/event-stream'}, text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
    def factory(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(src.ai,'AsyncOpenAI',factory)
    async def run():
        return ''.join([s async for s in Model(Settings(model_mode=mode,deepseek_api_key='unit-test-placeholder')).stream('q',[{'title':'x','version':1,'text':'原文'}])])
    assert asyncio.run(run()) == '结果 [1]'


def test_mcp_stdio_matches_local():
    async def run():
        local = await ToolTransport(Settings()).call('get_budget',{'project_id':'P1001'})
        remote = await ToolTransport(Settings(tool_transport='stdio')).call('get_budget',{'project_id':'P1001'})
        assert remote == local
    asyncio.run(run())


def test_document_upload_docx(client):
    from docx import Document
    doc = Document(); doc.add_paragraph('测试文档：采购说明。'); buff=io.BytesIO();doc.save(buff)
    r=client.post('/v1/documents/upload',files={'file':('sample.docx',buff.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
    assert r.status_code==200


def test_translation_skill_keeps_identifiers_and_labels_mock(client):
    r=client.post('/v1/translations',json={'text':'P1001 采购金额 200 元','target_language':'English'})
    assert r.status_code==200,r.text
    assert r.json()['mode']=='mock' and r.json()['identifiers_preserved'] is True
    assert '模拟' in r.json()['notes'][0]


def test_audio_is_not_faked_when_unconfigured(client):
    response=client.post('/v1/audio/transcriptions',files={'file':('x.wav',b'RIFFabc','audio/wav')})
    assert response.status_code==422 and 'ASR' in response.json()['detail']


def test_audio_adapter_contract(tmp_path,monkeypatch):
    from src.speech import SpeechService
    from src.repository import Repository
    from src.store import Store
    from src.schemas import SpeechInput
    store=Store(str(tmp_path/'audio.db'));repo=Repository(store)
    cid=store.create_conversation('speech');store.add_message(cid,'assistant','金额 100 元 [1]','complete','mock')
    mid=store.messages(cid)[0]['id']
    wav=io.BytesIO()
    with wave.open(wav,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\x00\x00'*160)
    original=httpx.AsyncClient
    def handler(request):
        assert 'DEEPSEEK' not in str(request.headers)
        if request.url.path.endswith('transcriptions'):
            return httpx.Response(200,json={'text':'查询 P1001 预算'})
        assert json.loads(request.content)['input']=='金额 100 元 '
        return httpx.Response(200,content=wav.getvalue(),headers={'content-type':'audio/wav'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    service=SpeechService(Settings(database=store.path,speech_url='https://audio.invalid/v1',asr_model='asr',tts_model='tts'),repo)
    async def run():
        transcript=await service.transcribe(wav.getvalue(),'a.wav','audio/wav')
        assert transcript['status']=='needs_review' and 'P1001' in transcript['critical_fields']
        result=await service.speak(SpeechInput(message_id=mid))
        assert result['url'].startswith('/v1/audio/')
    asyncio.run(run())
