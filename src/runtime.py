import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from pydantic import Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from src.ai import model_backend
from src.business import PROJECTS, get_budget
from src.repository import Conflict, now
from src.schemas import Schema
from src.skills import Skills
from src.tool_transport import ToolTransport


class Outcome(Schema):
    summary: str = Field(min_length=1, max_length=4000)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class RunDeps:
    runtime: object
    run_id: str


class Runtime:
    def __init__(self, repo, rag, settings):
        self.repo, self.rag, self.settings = repo, rag, settings
        self.transport, self.skills, self.locks = ToolTransport(settings), Skills(), {}

    def create(self, body):
        rid = hashlib.sha256(body.client_request_id.encode()).hexdigest()[:32]
        payload = body.model_dump()
        try:
            existing = self.repo.get("run", rid)
        except KeyError:
            existing = None
        if existing:
            if existing["request"] != payload:
                raise Conflict("同一请求编号不能用于不同任务")
            return existing
        data = {"request": payload, "goal": body.goal, "project_id": body.project_id, "amount": body.amount,
                "mode": body.mode, "status": "created", "steps": [], "results": {}, "draft": None,
                "draft_hash": None, "plan_version": 1, "replans": 0, "error": None,
                "skill": self.skills.select(body.goal), "summary": "", "model_mode": self.settings.model_mode}
        try:
            return self.repo.create("run", data, rid)
        except sqlite3.IntegrityError:
            existing = self.repo.get("run", rid)
            if existing["request"] != payload:
                raise Conflict("同一请求编号不能用于不同任务")
            return existing

    def patch(self, rid, **fields):
        current = self.repo.get("run", rid)
        return self.repo.mutate("run", rid, current["version"], lambda v: v.update(fields))

    def revise(self, rid, body):
        def change(run):
            if run["status"] in {"running", "succeeded", "cancelled"}:
                raise Conflict("当前状态不能修改")
            if run["replans"] >= 2:
                raise Conflict("已达到两次调整上限，请新建任务")
            run.update(project_id=body.project_id or run["project_id"], amount=body.amount or run["amount"],
                       status="created", results={}, draft=None, draft_hash=None, error=None,
                       replans=run["replans"] + 1, plan_version=run["plan_version"] + 1)
            run["steps"].append({"action": "replan", "reason": "用户修改目标，旧计算与草稿失效", "at": now()})
        return self.repo.mutate("run", rid, body.version, change)

    async def execute_tool(self, rid, name):
        run = self.repo.get("run", rid)
        if run["status"] != "running":
            raise ValueError("任务已停止")
        if name in run["results"]:
            repeats = run.get("no_progress", 0) + 1
            self.patch(rid, no_progress=repeats)
            if repeats >= 3:
                raise ValueError("连续重复已完成动作，任务停止以等待人工调整")
            return run["results"][name]
        project, amount = run["project_id"], run["amount"]
        if name == "get_budget":
            result = await self.transport.call(name, {"project_id": project})
            if result.get("project_id") != project or "available" not in result:
                raise ValueError("预算工具数据不完整")
        elif name == "search_knowledge":
            result = await self.rag.search("项目采购超过预算时需要什么审批？", 3)
        elif name == "calculate_gap":
            if "get_budget" not in run["results"]:
                raise ModelRetry("先查询预算")
            result = await self.transport.call(name, {"project_id": project, "amount": amount})
            expected = max(Decimal("0"), Decimal(amount) - Decimal(run["results"]["get_budget"]["available"]))
            if result.get("project_id") != project or Decimal(result.get("gap", "NaN")) != expected:
                raise ValueError("差额与预算不一致")
        elif name == "create_draft":
            if not all(k in run["results"] for k in ("get_budget", "search_knowledge", "calculate_gap")):
                raise ModelRetry("先查询预算、检索制度并计算差额")
            citations = [h["id"] for h in run["results"]["search_knowledge"] if self.repo.store.citation(h["id"])]
            if not citations:
                raise ModelRetry("缺少制度依据，请说明需要补充文档")
            result = {"project_id": project, "amount": amount, "currency": "CNY",
                      "gap": run["results"]["calculate_gap"]["gap"], "reason": run["goal"],
                      "budget_version": run["results"]["get_budget"]["source_version"], "citations": citations}
        else:
            raise ValueError("未知动作")
        latest = self.repo.get("run", rid)
        if latest["status"] != "running":
            raise ValueError("任务已停止")
        fields = {"no_progress": 0, "results": {**latest["results"], name: result},
                  "steps": latest["steps"] + [{"action": name, "status": "complete", "plan_version": latest["plan_version"], "at": now()}]}
        if name == "create_draft":
            fields.update(draft=result, draft_hash=digest(result))
        self.repo.mutate("run", rid, latest["version"], lambda v: v.update(fields))
        return result

    async def execute(self, rid):
        lock = self.locks.setdefault(rid, asyncio.Lock())
        if lock.locked():
            return self.repo.get("run", rid)
        async with lock:
            run = self.repo.get("run", rid)
            if run["status"] in {"awaiting_confirmation", "succeeded", "cancelled"}:
                return run
            if run["project_id"] not in PROJECTS or not run["amount"]:
                return self.patch(rid, status="awaiting_input", summary="请选择 P1001/P2002 并填写申请金额。")
            self.patch(rid, status="running", error=None)
            try:
                async with asyncio.timeout(self.settings.timeout):
                    if run["mode"] == "workflow":
                        for action in ("get_budget", "search_knowledge", "calculate_gap", "create_draft"):
                            await self.execute_tool(rid, action)
                        summary = "固定工作流已生成草稿，请核对后确认。"
                    elif run["mode"] == "multi_agent":
                        summary = await self.multi_agent_run(rid)
                    else:
                        summary = await self.agent_run(rid)
                latest = self.repo.get("run", rid)
                if latest["status"] == "cancelled":
                    return latest
                return self.patch(rid, status="awaiting_confirmation" if latest["draft"] else "awaiting_input", summary=summary)
            except asyncio.CancelledError:
                self.patch(rid, status="failed", error="执行中断，可从已完成步骤恢复")
                raise
            except Exception as exc:
                if self.repo.get("run", rid)["status"] == "cancelled":
                    return self.repo.get("run", rid)
                reason = "执行失败，请检查模型配置或补充制度资料，可恢复已完成步骤。"
                if self.settings.model_mode == "deepseek" and not self.settings.deepseek_api_key:
                    reason = "请在 .env 配置 DEEPSEEK_API_KEY 后恢复任务。"
                return self.patch(rid, status="failed", error=reason, error_type=type(exc).__name__)

    async def agent_run(self, rid):
        async def mock(messages, info):
            run = self.repo.get("run", rid)
            for name in ("get_budget", "search_knowledge", "calculate_gap", "create_draft"):
                if name not in run["results"]:
                    if name == "create_draft" and not run["results"].get("search_knowledge"):
                        break
                    return ModelResponse(parts=[ToolCallPart(name, {})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                {"summary": "【模拟 Agent】草稿已生成，请确认。" if run["draft"] else "缺少制度资料，请导入后继续。"})])

        async with model_backend(self.settings, FunctionModel(mock)) as backend:
            agent = Agent(backend, output_type=Outcome, deps_type=RunDeps, retries=2, instructions=(
                "你是预算审查 Agent。取得预算与制度后计算差额，再生成草稿。根据结果调整步骤，"
                "不能捏造工具结果或自动提交。资料不足时说明需要补充的资料。"))
            @agent.tool
            async def get_budget(ctx: RunContext[RunDeps]) -> dict:
                """查询当前任务项目的实时预算。"""
                return await ctx.deps.runtime.execute_tool(ctx.deps.run_id, "get_budget")

            @agent.tool
            async def search_knowledge(ctx: RunContext[RunDeps]) -> list[dict]:
                """检索采购和预算调整制度。"""
                return await ctx.deps.runtime.execute_tool(ctx.deps.run_id, "search_knowledge")

            @agent.tool
            async def calculate_gap(ctx: RunContext[RunDeps]) -> dict:
                """计算申请金额超出可用预算的差额。"""
                return await ctx.deps.runtime.execute_tool(ctx.deps.run_id, "calculate_gap")

            @agent.tool
            async def create_draft(ctx: RunContext[RunDeps]) -> dict:
                """根据已取得的数据生成草稿，不提交审批。"""
                return await ctx.deps.runtime.execute_tool(ctx.deps.run_id, "create_draft")

            @agent.output_validator
            async def validate(ctx: RunContext[RunDeps], output: Outcome) -> Outcome:
                state = self.repo.get("run", rid)
                if state["results"].get("search_knowledge") and not state["draft"]:
                    raise ModelRetry("请完成必要工具调用并生成草稿")
                if not state["results"]:
                    raise ModelRetry("必须先调用工具检查任务资料")
                return output

            state = self.repo.get("run", rid)
            result = await agent.run(json.dumps({"goal": state["goal"], "project_id": state["project_id"],
                    "amount": state["amount"], "completed": state["results"], "handoff": state.get("handoff"), "skill": state["skill"]}, ensure_ascii=False),
                deps=RunDeps(self, rid), usage_limits=UsageLimits(request_limit=14, tool_calls_limit=12))
            return result.output.summary

    async def multi_agent_run(self, rid):
        """A bounded investigator -> drafting agent handoff for comparison with one agent."""
        async def mock(messages, info):
            state = self.repo.get("run", rid)
            for tool in ("get_budget", "search_knowledge"):
                if tool not in state["results"]:
                    return ModelResponse(parts=[ToolCallPart(tool, {})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": "调查完成，交给草稿 Agent。"})])
        async with model_backend(self.settings, FunctionModel(mock)) as backend:
            researcher = Agent(backend, output_type=Outcome, deps_type=RunDeps, retries=2,
                instructions="你负责调查：调用预算查询及制度检索，完成后将证据交给草稿 Agent。不要计算或提交审批。")
            @researcher.tool
            async def get_budget(ctx: RunContext[RunDeps]) -> dict:
                """取得当前项目预算。"""
                return await self.execute_tool(rid, "get_budget")
            @researcher.tool
            async def search_knowledge(ctx: RunContext[RunDeps]) -> list[dict]:
                """检索预算调整与采购规则。"""
                return await self.execute_tool(rid, "search_knowledge")
            @researcher.output_validator
            def check(ctx, output: Outcome) -> Outcome:
                if not all(k in self.repo.get("run", rid)["results"] for k in ("get_budget", "search_knowledge")):
                    raise ModelRetry("交接前必须完成预算查询和制度检索")
                return output
            state = self.repo.get("run", rid)
            result = await researcher.run(json.dumps({"goal": state["goal"], "project_id": state["project_id"]}, ensure_ascii=False),
                deps=RunDeps(self, rid), usage_limits=UsageLimits(request_limit=8, tool_calls_limit=6))
            latest = self.repo.get("run", rid)
            self.patch(rid, handoff={"from": "researcher", "to": "drafter", "summary": result.output.summary,
                "evidence_ids": [h["id"] for h in latest["results"]["search_knowledge"]],
                "budget_version": latest["results"]["get_budget"]["source_version"]})
        return await self.agent_run(rid)

    def confirm(self, rid, body):
        with self.repo.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM records WHERE kind='run' AND id=?", (rid,)).fetchone()
            if not row:
                raise KeyError(rid)
            run = json.loads(row[0])
            previous = db.execute("SELECT * FROM approvals WHERE run_id=?", (rid,)).fetchone()
            if previous:
                if previous["draft_hash"] != body.draft_hash:
                    raise Conflict("确认内容与已提交单据不一致")
                return run
            if run["version"] != body.version or run["status"] != "awaiting_confirmation" or run["draft_hash"] != body.draft_hash:
                raise Conflict("草稿已变更，请刷新后重新确认")
            if get_budget(run["project_id"])["source_version"] != run["draft"]["budget_version"]:
                raise Conflict("预算已变化，请重新生成")
            for cid in run["draft"]["citations"]:
                citation = self.repo.store.citation(cid)
                if not citation or citation["version"] != citation["active_version"]:
                    raise Conflict("制度已更新或删除，请重新生成")
            approval_id = "AP-" + rid[:12]
            db.execute("INSERT INTO approvals VALUES(?,?,?,?)", (approval_id, rid, body.draft_hash, json.dumps(run["draft"])))
            run.update(status="succeeded", approval_id=approval_id, version=run["version"] + 1, updated_at=now())
            db.execute("UPDATE records SET version=?,payload=? WHERE kind='run' AND id=?", (run["version"], json.dumps(run, ensure_ascii=False), rid))
        return run

    def cancel(self, rid):
        run = self.repo.get("run", rid)
        if run["status"] == "succeeded":
            raise Conflict("已提交单据不能通过取消任务撤销")
        return self.repo.mutate("run", rid, run["version"], lambda v: v.update(status="cancelled"))
