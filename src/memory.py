import re
from datetime import datetime, timedelta, timezone

from src.repository import Conflict


class MemoryService:
    def __init__(self, repo):
        self.repo = repo

    def list(self, project_id=None):
        return [m for m in self.active_records()
                if m["project_id"] is None or m["project_id"] == project_id]

    def active_records(self):
        active = []
        for record in self.repo.list("memory"):
            if record["status"] != "active":
                continue
            if record.get("expires_at") and datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc):
                try:
                    self.repo.mutate("memory", record["id"], record["version"], lambda m: m.update(status="expired"))
                except Conflict:
                    continue
                self.invalidate_summaries(record["source_message_id"])
            else:
                active.append(record)
        return active

    def create(self, body):
        source = self.repo.message(body.source_message_id)
        if source["role"] != "user":
            raise ValueError("记忆来源必须是用户消息，并由用户明确确认")
        # Volatile balances are queried from tools, never treated as long-term truth.
        if re.search(r"余额|已使用|已支付|剩余预算", body.value):
            raise ValueError("动态预算数据应实时查询，不能保存为长期记忆")
        expires_at = (datetime.now(timezone.utc) + timedelta(days=body.ttl_days)).isoformat() if body.ttl_days else None
        return self.repo.create("memory", {**body.model_dump(), "status": "active", "expires_at": expires_at})

    def update(self, rid, body):
        if re.search(r"余额|已使用|已支付|剩余预算", body.value):
            raise ValueError("动态预算数据不能保存为长期记忆")
        def change(value):
            if value["status"] != "active":
                raise Conflict("记忆已删除")
            value["value"] = body.value
        result = self.repo.mutate("memory", rid, body.version, change)
        self.invalidate_summaries(result["source_message_id"])
        return result

    def delete(self, rid):
        record = self.repo.get("memory", rid)
        result = self.repo.mutate("memory", rid, record["version"], lambda m: m.update(status="deleted", value=""))
        self.invalidate_summaries(result["source_message_id"])
        return result

    def invalidate_summaries(self, source_id):
        for summary in self.repo.list("summary"):
            if source_id in summary.get("source_message_ids", []):
                with self.repo.store.connect() as db:
                    db.execute("DELETE FROM records WHERE kind='summary' AND id=?", (summary["id"],))

    def context(self, cid, question):
        messages = self.repo.store.messages(cid)
        # Current user message may already be appended by the streaming endpoint.
        prior = messages[:-1] if messages and messages[-1]["role"] == "user" and messages[-1]["content"] == question else messages
        project = None
        for message in prior + [{"role": "user", "content": question}]:
            if message["role"] == "user":
                matches = re.findall(r"\bP\d{4}\b", message["content"], re.I)
                if matches:
                    project = matches[-1].upper()
        resolved = question
        if project and not re.search(r"\bP\d{4}\b", question, re.I) and re.search(r"它|这个项目|该项目|刚才|还能", question):
            resolved = f"{project}：{question}"
        # Do not reintroduce an edited/forgotten memory through its original user message.
        memory_sources = {m["source_message_id"] for m in self.repo.list("memory")}
        prior = [m for m in prior if m["id"] not in memory_sources and m["status"] == "complete"]
        recent = prior[-8:]
        older = prior[:-8]
        # Extractive summary with original message references, never synthesized financial facts.
        summary = [{"message_id": m["id"], "text": m["content"][:250]}
                   for m in older if m["role"] == "user"][-12:]
        data = {"project_id": project, "recent": [{"role": m["role"], "text": m["content"][:1500]} for m in recent],
                "summary": summary, "memories": self.list(project)}
        # Memory values are resolved fresh every turn, never copied into durable summaries.
        summary_record = {"project_id": project, "items": summary, "source_message_ids": [m["message_id"] for m in summary]}
        try:
            old = self.repo.get("summary", cid)
            self.repo.mutate("summary", cid, old["version"], lambda v: v.update(summary_record))
        except KeyError:
            self.repo.create("summary", summary_record, cid)
        return resolved, data
