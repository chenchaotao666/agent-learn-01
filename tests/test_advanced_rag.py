import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from src.config import Settings
from src.rag.chunking import split_text
from src.rag.embedding import Embedder
from src.rag.external import ExternalIndexes
from src.rag.ranking import bm25, fuse
from src.rag.refinement import rerank
from src.schemas import IngestionInput


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(database=str(tmp_path/'test.db')))) as c:
        yield c


def add(client, text, **extra):
    r = client.post('/v1/documents', json={'title':'采购规则', 'filename':'rules.md', 'text':text, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_exact_identifier_bm25_and_rrf():
    docs = [{'id':'a','title':'订单', 'text':'PO1001 采购'}, {'id':'b','title':'订单', 'text':'PO1002 采购'}]
    assert [h['id'] for h in bm25('PO1002', docs)] == ['b']
    a, b = {**docs[0], 'score':100}, {**docs[1], 'score':.001}
    hits = fuse([('first',[a,b]),('second',[b])])
    assert hits[0]['id'] == 'b'  # rank fusion, not addition of incomparable scores


@pytest.mark.parametrize('mode', ['vector','keyword','hybrid'])
def test_each_retriever_honors_update_delete(client, mode):
    first = add(client, 'PO1001 的采购金额为 100 元。')
    body = {'query':'PO1001 采购金额','mode':mode}
    original = client.post('/v1/search/inspect', json=body).json()['hits'][0]
    add(client, 'PO1001 的采购金额改为 200 元。', document_id=first['id'])
    hits = client.post('/v1/search/inspect', json=body).json()['hits']
    assert all(h['version']==2 for h in hits)
    assert all('vector' not in h for h in hits)
    assert client.get('/v1/citations/'+original['id']).json()['version'] == 1
    client.delete('/v1/documents/'+first['id'])
    assert client.post('/v1/search/inspect', json=body).json()['hits'] == []


def test_rewrite_and_rerank_function_models(client):
    add(client, 'P1001 的采购金额为 100 元。')
    r=client.post('/v1/search/inspect',json={'query':'P1001 采购金额','rewrite':'model','rerank':'model'})
    assert r.status_code==200,r.text
    assert r.json()['model_mode']=='mock'
    assert r.json()['rewritten_query']=='P1001 采购金额'
    assert r.json()['hits'][0]['rerank_rank']==1


def test_structure_chunks_are_source_substrings_with_correct_lines():
    text = '# 标题一\n第一节规则。\n\n# 标题二\n' + '采购条款。\n'*80
    chunks = split_text(text, size=80, overlap=10, strategy='structure')
    assert len(chunks)>2
    assert chunks[0]['text'].startswith('# 标题一')
    for chunk in chunks:
        assert chunk['text'] in text and len(chunk['text'])<=80
        lines=text.split('\n')[chunk['start_line']-1:chunk['end_line']]
        assert chunk['text'] in '\n'.join(lines)
    with pytest.raises(ValueError):
        split_text(text,size=10,overlap=10)


def job(client, key='one', **extra):
    body={'title':'测试任务', 'filename':'job.md', 'text':'采购预算规则。', 'client_request_id':key, **extra}
    r=client.post('/v1/ingestions',json=body)
    assert r.status_code==200,r.text
    return r.json()


def test_queue_idempotency_and_atomic_publication(client):
    record=job(client)
    assert job(client)['id']==record['id']
    assert client.get('/v1/documents').json()==[]
    asyncio.run(client.app.state.ingestions.work_once())
    done=client.get('/v1/ingestions/'+record['id']).json()
    assert done['status']=='published' and done['attempts']==1
    assert len(client.get('/v1/documents').json())==1
    assert client.post('/v1/ingestions/'+record['id']+'/cancel').status_code==409
    changed={'title':'测试任务','filename':'job.md','text':'changed','client_request_id':'one'}
    assert client.post('/v1/ingestions',json=changed).status_code==409


def test_cancelled_claim_cannot_publish(client, monkeypatch):
    jobs=client.app.state.ingestions
    record=job(client)
    encode=jobs.rag.embedder.encode
    async def cancel(texts):
        jobs.change(record['id'],'cancel')
        return await encode(texts)
    monkeypatch.setattr(jobs.rag.embedder,'encode',cancel)
    asyncio.run(jobs.work_once())
    assert jobs.get(record['id'])['status']=='cancelled'
    assert client.get('/v1/documents').json()==[]


def test_expired_claim_recovered_by_new_worker(client):
    jobs=client.app.state.ingestions
    record=job(client)
    first=jobs.claim()
    assert first and jobs.claim() is None
    with jobs.store.connect() as db:
        db.execute('UPDATE ingestions SET lease_until=? WHERE id=?',(time.time()-1,record['id']))
    asyncio.run(jobs.work_once())
    assert jobs.get(record['id'])['status']=='published'
    assert jobs.get(record['id'])['attempts']==2


def test_embedding_failure_keeps_old_version_and_job_retries(client, monkeypatch):
    first=add(client,'采购预算 100 元')
    jobs=client.app.state.ingestions
    record=job(client,document_id=first['id'])
    encode=jobs.rag.embedder.encode
    async def fail(texts):
        raise httpx.ConnectError('injected')
    monkeypatch.setattr(jobs.rag.embedder,'encode',fail)
    asyncio.run(jobs.work_once())
    assert jobs.get(record['id'])['status']=='failed'
    assert client.get('/v1/documents').json()[0]['active_version']==1
    monkeypatch.setattr(jobs.rag.embedder,'encode',encode)
    jobs.change(record['id'],'retry')
    asyncio.run(jobs.work_once())
    assert jobs.get(record['id'])['status']=='published'
    assert client.get('/v1/documents').json()[0]['active_version']==2


def test_chunk_strategy_change_is_a_new_version(client):
    first=add(client,'# 第一节\n采购预算。\n\n# 第二节\n采购流程。')
    rag=client.app.state.rag
    new=asyncio.run(rag.ingest('采购规则','rules.md','# 第一节\n采购预算。\n\n# 第二节\n采购流程。',first['id'],strategy='structure'))
    assert new['version']==2


def test_cloud_embedding_reorders_indices(monkeypatch):
    original=httpx.AsyncClient
    def handler(request):
        assert request.url.path=='/v1/embeddings'
        assert request.headers['authorization']=='Bearer embedding-test'
        return httpx.Response(200,json={'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[3,0]}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    settings=Settings(embedding_mode='compatible',embedding_url='https://embedding.invalid/v1',embedding_api_key='embedding-test')
    assert asyncio.run(Embedder(settings).encode(['a','b']))==[[1,0],[0,1]]


@pytest.mark.parametrize('indices', [[0,0], [2,0], [0]])
def test_rerank_rejects_bad_remote_permutations(monkeypatch, indices):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,json={'results':[{'index':i,'relevance_score':.5} for i in indices]})),**kw))
    settings=Settings(rerank_url='https://rerank.invalid/v1',rerank_model='test')
    with pytest.raises(ValueError):
        asyncio.run(rerank(settings,'query',[{'title':'a','text':'a'},{'title':'b','text':'b'}],'compatible'))


def test_external_index_contracts_filter_live_chunk_ids(monkeypatch):
    original=httpx.AsyncClient
    cid='12345678123456781234567812345678'
    candidates=[{'id':cid,'title':'项目','text':'P1001 采购','vector':'[1,0]'}]
    seen=set()
    def handler(request):
        path=request.url.path
        if path.endswith('/_search'):
            body=json.loads(request.content)
            assert body['query']['bool']['filter'][0]['ids']['values']==[cid]
            seen.add('es')
            return httpx.Response(200,json={'hits':{'hits':[{'_id':cid,'_score':2}]}})
        if path.endswith('/points/query'):
            body=json.loads(request.content)
            assert body['filter']['must'][0]['has_id']==['12345678-1234-5678-1234-567812345678']
            seen.add('qdrant')
            return httpx.Response(200,json={'result':{'points':[{'id':'12345678-1234-5678-1234-567812345678','score':1}]}})
        if request.method in ('HEAD','GET'):
            return httpx.Response(404)
        if '/_bulk' in path:
            assert request.content.endswith(b'\n')
            return httpx.Response(200,json={'errors':False})
        return httpx.Response(200,json={'result':True})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    indexes=ExternalIndexes(Settings(),'test')
    async def run():
        assert (await indexes.keyword('P1001',candidates))[0]['id']==cid
        assert (await indexes.vector('P1001',[1,0],candidates))[0]['id']==cid
    asyncio.run(run())
    assert seen=={'es','qdrant'}


def test_expired_memory_not_in_context(client):
    repo=client.app.state.repo
    cid=repo.store.create_conversation('memory')
    repo.store.add_message(cid,'user','记住我喜欢列表','complete','mock')
    mid=repo.store.messages(cid)[0]['id']
    r=client.post('/v1/memories',json={'value':'喜欢列表','source_message_id':mid,'confirmed':True,'ttl_days':1})
    assert r.status_code==200
    record=r.json()
    repo.mutate('memory',record['id'],record['version'],lambda m:m.update(expires_at='2000-01-01T00:00:00+00:00'))
    assert client.get('/v1/memories').json()==[]
    assert client.app.state.memory.context(cid,'hi')[1]['memories']==[]


@pytest.mark.parametrize('mode', ['workflow', 'agent', 'multi_agent'])
def test_three_execution_modes_produce_same_validated_draft(client, mode):
    client.post('/v1/demo/seed')
    r=client.post('/v1/runs',json={'goal':'检查采购预算','project_id':'P1001','amount':'200000.00',
                                'client_request_id':mode,'mode':mode})
    run=client.get('/v1/runs/'+r.json()['id']).json()
    assert run['status']=='awaiting_confirmation',run
    assert run['draft']['gap']=='50000.00'
    if mode=='multi_agent':
        assert run['handoff']['to']=='drafter' and run['handoff']['evidence_ids']


def test_repeated_tool_calls_stop_without_creating_draft(client):
    from src.schemas import RunInput
    runtime=client.app.state.runtime
    run=runtime.create(RunInput(goal='检查预算',project_id='P1001',amount='100',client_request_id='loop'))
    runtime.patch(run['id'],status='running')
    async def repeat():
        for _ in range(4):
            await runtime.execute_tool(run['id'],'get_budget')
    with pytest.raises(ValueError,match='连续重复'):
        asyncio.run(repeat())
    assert runtime.repo.get('run',run['id'])['draft'] is None


def test_stale_worker_cannot_publish_after_takeover(client, monkeypatch):
    jobs=client.app.state.ingestions
    record=job(client)
    encode=jobs.rag.embedder.encode
    async def takeover(texts):
        with jobs.store.connect() as db:
            db.execute('UPDATE ingestions SET lease_until=0 WHERE id=?',(record['id'],))
        assert jobs.claim() is not None
        return await encode(texts)
    monkeypatch.setattr(jobs.rag.embedder,'encode',takeover)
    asyncio.run(jobs.work_once())
    assert jobs.get(record['id'])['status']=='running'
    assert jobs.get(record['id'])['attempts']==2
    assert jobs.store.documents()==[]


def test_invalid_queue_file_and_search_parameters(client):
    r=client.post('/v1/ingestions/upload',files={'file':('bad.pdf',b'invalid','application/pdf')},data={'client_request_id':'bad'})
    assert r.status_code==422
    assert client.post('/v1/search/inspect',json={'query':'test','mode':'invented'}).status_code==422
    assert client.post('/v1/search/inspect',json={'query':'test','top_k':99}).status_code==422
