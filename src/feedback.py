import hashlib
import json

from src.repository import Conflict


class FeedbackService:
    def __init__(self, repo, rag, model):
        self.repo, self.rag, self.model = repo, rag, model

    def create(self, body):
        message = self.repo.message(body.message_id)
        if message["role"] != "assistant":
            raise ValueError("反馈必须关联助手回答")
        with self.repo.store.connect() as db:
            question = db.execute("SELECT content FROM messages WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
                                  (message["conversation_id"], message["id"])).fetchone()
        return self.repo.create("feedback", {**body.model_dump(), "status": "new", "question": question[0] if question else "",
            "actual_answer": message["content"], "actual_citations": message["citations"], "source_mode": message["mode"]})

    def review(self, rid, body):
        if body.decision == "accepted":
            if not body.expected_answer.strip() or not body.evidence_ids:
                raise ValueError("采纳反馈需填写标准答案与有效证据 ID")
            for cid in body.evidence_ids:
                if not self.repo.store.citation(cid):
                    raise ValueError("证据已删除或不存在")
        def change(record):
            record.update(status=body.decision, review=body.model_dump(exclude={"version"}))
        return self.repo.mutate("feedback", rid, body.version, change)

    def publish(self):
        cases, seen, answers = [], set(), {}
        for feedback in self.repo.list("feedback"):
            if feedback["status"] != "accepted":
                continue
            review = feedback["review"]
            normalized_question = " ".join(feedback["question"].split())
            if normalized_question in answers and answers[normalized_question] != review["expected_answer"].strip():
                raise Conflict("同一问题存在冲突标准答案，请重新复核后发布")
            answers[normalized_question] = review["expected_answer"].strip()
            key = hashlib.sha256((feedback["question"].strip() + review["expected_answer"].strip()).encode()).hexdigest()
            if key in seen:
                continue
            evidence = [self.repo.store.citation(cid) for cid in review["evidence_ids"]]
            if not all(evidence):
                raise Conflict("待发布样本的来源已删除，请重新复核")
            seen.add(key)
            cases.append({"id": key, "feedback_id": feedback["id"], "feedback_version": feedback["version"],
                          "question": feedback["question"], "expected_answer": review["expected_answer"],
                          "evidence": evidence})
        if not cases:
            raise ValueError("尚无可发布的已复核样本")
        cases.sort(key=lambda c: c["id"])
        fingerprint = hashlib.sha256(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        rid = fingerprint[:32]
        try:
            return self.repo.get("dataset", rid)
        except KeyError:
            return self.repo.create("dataset", {"split": "regression", "sha256": fingerprint, "cases": cases}, rid)

    async def evaluate(self, body):
        dataset = self.repo.get("dataset", body.dataset_id)
        results = []
        for case in dataset["cases"]:
            retrieval = await self.rag.inspect(case["question"], body.top_k, body.mode, body.rewrite, body.rerank)
            hits = retrieval["hits"]
            answer = "".join([delta async for delta in self.model.stream(case["question"], hits)])
            expected = {(e["document_id"], e["version"]) for e in case["evidence"]}
            actual = {(h["document_id"], h["version"]) for h in hits}
            results.append({"case_id": case["id"], "question": case["question"], "answer": answer,
                            "retrieval_pass": expected.issubset(actual),
                            "exact_answer_contained": case["expected_answer"] in answer,
                            "semantic_review": "pending", "citation_ids": [h["id"] for h in hits], "retrieval": retrieval})
        settings = self.model.settings
        return self.repo.create("evaluation", {"dataset_id": dataset["id"], "dataset_sha256": dataset["sha256"],
            "top_k": body.top_k, "model_mode": settings.model_mode, "model": settings.deepseek_model if settings.model_mode == "deepseek" else settings.chat_model,
            "embedding": self.rag.embedder.key, "pipeline": body.model_dump(exclude={"dataset_id"}), "prompt_version": "rag-v2", "results": results,
            "retrieval_pass": sum(r["retrieval_pass"] for r in results), "total": len(results),
            "note": "固定回归集；字符串包含检查不等于语义准确率，标准答案仍需人工复核。"})

    def compare(self, first, second):
        a, b = self.repo.get("evaluation", first), self.repo.get("evaluation", second)
        if a["dataset_sha256"] != b["dataset_sha256"]:
            raise Conflict("只能比较同一数据集快照")
        return {"dataset_id": a["dataset_id"], "first": a, "second": b,
                "retrieval_delta": b["retrieval_pass"] - a["retrieval_pass"]}
