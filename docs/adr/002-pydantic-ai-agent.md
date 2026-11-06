# ADR-002：Pydantic 校验契约，Pydantic AI 实现 Agent

状态：已实施基础闭环；真实模型联调及完整实验验收待完成。

## 决定

根据用户明确选择，Pydantic 负责接口参数及领域数据结构校验，Pydantic AI 负责 Agent 的模型调用、工具调用循环和结构化输出。停止把 LangGraph 作为当前路线的待选编排框架，避免同时学习和维护两套 Agent 抽象。

应用状态机仍负责任务版本、等待用户、检查点、确认一致性和写入幂等；数据库保存原始对话、摘要、长期记忆与反馈。类型正确不保证事实正确，金额、项目、引用和业务终态均需额外验证。

Ollama 是可选的模型服务，与 Pydantic AI 分工不同；可改接云端模型而保持上层 Agent 结构。无需为此增加统一模型网关。

## 实施步骤

1. 保留既有可运行基线，抽出 Pydantic 请求响应与工具结果模型。
2. 选择并锁定兼容的 Pydantic AI 版本，替换 RAG 场景的模型调用与 SSE 映射。
3. 使用确定性模型替身验证工具、结构化输出和状态，再联调真实模型。
4. 加入动态规划和业务工具，再依次实现上下文记忆、MCP/Skill、语音和反馈评测。

框架引入不自动完成业务能力，也不代表已经完成原设计的 Agent 实验。ADR-001 中的 SQLite 和字符向量仍是当前代码的基线，本决策不追溯改变其已实现状态。

## 验收与复评

E17 验证校验与框架契约，E07/E08/E12 验证任务结果和恢复。若升级框架，重新验证消息序列化、SSE、工具结果与检查点兼容性；迁移失败不能直接丢弃历史会话或任务。

设计依据：[Pydantic 校验](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/)、[Pydantic AI Agent](https://pydantic.dev/docs/ai/core-concepts/agent/)。
