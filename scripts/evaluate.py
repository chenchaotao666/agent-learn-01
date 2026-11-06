"""A deterministic development smoke set, not the frozen E01 quality evaluation."""
import asyncio
import hashlib
import json
import platform
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.config import ROOT, Settings
from src.model import Model
from src.rag.embedding import Embedder
from src.rag.service import RagService
from src.store import Store


async def evaluate(output: Path):
    fixtures = json.loads((ROOT / "fixtures/documents.json").read_text(encoding="utf-8"))
    dataset = ROOT / "evals/smoke.json"
    cases = json.loads(dataset.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(database=str(Path(tmp) / "eval.sqlite3"))
        rag = RagService(Store(settings.database), Embedder(settings), settings.min_score)
        for doc in fixtures:
            await rag.ingest(doc["title"], doc["filename"], doc["text"])
        results = []
        for case in cases:
            hits = await rag.search(case["question"])
            answer = "".join([part async for part in Model(settings).stream(case["question"], hits)])
            retrieved = [h["filename"] for h in hits]
            retrieval_pass = case["expected_file"] in retrieved if case["expected_file"] else not hits
            results.append({**case, "retrieved": retrieved, "scores": [h["score"] for h in hits],
                            "retrieval_pass": retrieval_pass,
                            "extract_contains_expected": case["answer_contains"] in answer,
                            "answer": answer})
    output.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        commit = "uncommitted"
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "commit": commit,
                "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
                "python": platform.python_version(), "platform": platform.platform(),
                "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "fixtures_sha256": hashlib.sha256((ROOT / "fixtures/documents.json").read_bytes()).hexdigest(),
                "mode": "mock", "embedding": "hash-char-bigram-v1:1024", "top_k": 3,
                "min_score": settings.min_score, "chunk_size": 450, "chunk_overlap": 60,
                "dependencies": subprocess.check_output([__import__('sys').executable, "-m", "pip", "freeze"], text=True).splitlines()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "results.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8")
    answerable = [r for r in results if r["expected_file"]]
    unanswerable = [r for r in results if not r["expected_file"]]
    summary = (
        "# 首版开发集冒烟评测\n\n"
        "使用字符哈希向量 + Mock 原文摘录，仅验证召回与流程，不代表真实模型回答准确率。\n\n"
        f"- 可回答题文档 Hit@3：{sum(r['retrieval_pass'] for r in answerable)}/{len(answerable)}\n"
        f"- 无答案题正确拒答：{sum(r['retrieval_pass'] for r in unanswerable)}/{len(unanswerable)}\n"
        f"- 摘录包含预期字符串：{sum(r['extract_contains_expected'] for r in results)}/{len(results)}（不等于语义正确率）\n\n"
        "## 未通过样例\n\n"
    )
    failed = [r for r in results if not r["retrieval_pass"] or not r["extract_contains_expected"]]
    summary += "\n".join(f"- {r['id']}：{r['question']}；实际召回：{', '.join(r['retrieved']) or '无'}" for r in failed) or "无。"
    summary += "\n\n该题集是开发用的小型合成样本，尚未建立正式冻结测试集与人工评分。仅一条无答案题不足以评估拒答能力，字符重合也不能可靠判断资料是否足以回答问题。\n"
    (output / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    asyncio.run(evaluate(ROOT / "reports/runs" / datetime.now().strftime("%Y%m%d-%H%M%S")))
