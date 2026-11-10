"""Reproducible local retrieval/chunking ablations; no answer-quality claims."""
import asyncio
import hashlib
import json
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from src.config import ROOT, Settings
from src.rag.embedding import Embedder
from src.rag.service import RagService
from src.store import Store


async def evaluate():
    dataset=ROOT/'evals/smoke.json'
    fixture=ROOT/'fixtures/documents.json'
    cases=json.loads(dataset.read_text(encoding='utf-8'))
    docs=json.loads(fixture.read_text(encoding='utf-8'))
    results=[]
    for strategy in ('fixed','structure'):
        with tempfile.TemporaryDirectory() as tmp:
            settings=Settings(vector_backend="local",database=str(Path(tmp)/'eval.db'),chunk_strategy=strategy)
            rag=RagService(Store(settings.database),Embedder(settings),settings.min_score)
            for doc in docs:
                await rag.ingest(**doc)
            for mode in ('vector','keyword','hybrid'):
                for case in cases:
                    trace=await rag.inspect(case['question'],3,mode)
                    files=[h['filename'] for h in trace['hits']]
                    rank=files.index(case['expected_file'])+1 if case['expected_file'] in files else None
                    results.append({'case_id':case['id'],'strategy':strategy,'mode':mode,'question':case['question'],
                        'expected_file':case['expected_file'],'retrieved':files,'hit':bool(rank),
                        'reciprocal_rank':1/rank if rank else 0,'no_answer_pass':not files if not case['expected_file'] else None})
    summary=[]
    for strategy in ('fixed','structure'):
        for mode in ('vector','keyword','hybrid'):
            group=[r for r in results if r['strategy']==strategy and r['mode']==mode]
            answered=[r for r in group if r['expected_file']]
            unanswered=[r for r in group if not r['expected_file']]
            summary.append({'strategy':strategy,'mode':mode,'hit_at_3':sum(r['hit'] for r in answered)/len(answered),
                'mrr_at_3':sum(r['reciprocal_rank'] for r in answered)/len(answered),
                'no_answer_pass':sum(r['no_answer_pass'] for r in unanswered),'no_answer_total':len(unanswered)})
    output=ROOT/'reports/runs'/('retrieval-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    (output/'results.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in results)+'\n',encoding='utf-8')
    manifest={'dataset_sha256':hashlib.sha256(dataset.read_bytes()).hexdigest(),'fixtures_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'embedding':'hash-char-bigram-v1:1024','rewrite':'off','rerank':'off','top_k':3,'min_score':settings.min_score,
              'note':'Development synthetic set, lexical/hash baseline. Not semantic answer accuracy.',
              'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src/rag').glob('*.py')}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'comparisons':summary},ensure_ascii=False,indent=2))


if __name__=='__main__':
    asyncio.run(evaluate())
