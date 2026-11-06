# 知行 · AI 办公实验室

使用 **Pydantic 校验接口参数、Pydantic AI 实现 Agent、DeepSeek API 提供模型推理**。支持知识问答、预算审查、任务恢复、记忆、MCP/Skill、翻译及反馈回归评测。业务数据和审批为本地模拟。

## 启动（Windows PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 仅首次配置时复制，已有 .env 时不要覆盖
Copy-Item .env.example .env
```

编辑 `.env`，填入自己的密钥：

```dotenv
MODEL_MODE=deepseek
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

模型名称可替换为账号可用且支持工具调用的 DeepSeek 模型。地址和模型示例参照 [DeepSeek 官方文档](https://api-docs.deepseek.com/)。密钥保存在服务端，`.env` 已被 Git 忽略。配置修改后重启服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8010
```

- [知识问答](http://127.0.0.1:8010)：先导入 20 份演示资料，再提问或上传文件。
- [Agent 工作台](http://127.0.0.1:8010/workbench)：任务、记忆、反馈、翻译和语音。
- [接口文档](http://127.0.0.1:8010/docs)：Pydantic 参数校验及全部接口。

无密钥时明确提示配置错误，不自动切换模拟模型。离线验证请显式设置 `MODEL_MODE=mock`；该模式通过 Pydantic AI 的 FunctionModel 执行工具循环，但决策与回答是确定性模拟，不代表 DeepSeek 效果。

## 实践流程

1. 导入资料，测试流式回答、引用、版本与删除。支持 UTF-8 MD/TXT、文本层 PDF、DOCX；最大 10 MB、解析结果最多 10 万字符，不支持扫描件 OCR。
2. 工作台创建 P1001、申请金额 `200000.00` 的预算任务。核对预算、制度、差额和草稿，手动确认后生成模拟审批单。可选择固定工作流作对照。
3. 修改项目或金额验证旧草稿失效；故障后恢复会复用已完成工具结果。Decimal 计算金额，重复确认不会重复生成审批单。
4. 连续询问项目，再到“对话与记忆”加载会话，选择来源消息、确认保存偏好，测试修正和遗忘。
5. 对助手消息提交反馈，人工复核答案及证据，发布固定回归集并评测。结果区分文档召回与答案字符串包含检查，语义评价仍需人工进行。
6. 术语翻译加载采购 Skill，并检查数字和项目编号是否保留。Mock 输出明确标记为模拟。

## MCP 与 Skill

默认 `TOOL_TRANSPORT=local`。设为 `stdio` 后自动启动本仓库 MCP 子进程，完成初始化、工具发现和调用。HTTP 模式先另开终端运行：

```powershell
.\.venv\Scripts\python.exe -m src.mcp_server --http
```

设置 `TOOL_TRANSPORT=http`、`MCP_URL=http://127.0.0.1:8011/mcp`，重启应用。三种方式共用模拟预算数据。Skill 位于 `agent_skills/`，包含版本、说明和参考术语，加载时记录内容哈希；不执行任意脚本。

## 语音与向量

DeepSeek 用于对话、Agent 和翻译。ASR/TTS 单独配置兼容 `/audio/transcriptions` 和 `/audio/speech` 的服务：`SPEECH_BASE_URL`、`SPEECH_API_KEY`、`ASR_MODEL`、`TTS_MODEL`。支持录音或上传、核对转写、提问及朗读完成回答。未配置时明确报错，尚未与真实语音服务联调。

默认 `EMBEDDING_MODE=hash` 为字符哈希基线，**不具备语义理解能力**。可选 `EMBEDDING_MODE=ollama` 并配置本机向量模型；切换向量模型应使用新数据库重新导入。对话保留可选 `MODEL_MODE=ollama`，通过兼容 API 连接。使用 DeepSeek 对话无需安装 Ollama。

## 验证与范围

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.evaluate
```

冒烟报告位于 `reports/runs/<时间>/`。测试覆盖参数校验、Pydantic AI 工具循环、检查点、确认幂等、记忆、MCP stdio、反馈集及模拟 HTTP 契约。真实 DeepSeek 效果需配置密钥后验证。

本版是单用户、单进程学习实现，使用 SQLite。尚未实现 ES 混合检索、Rerank、Qdrant、OCR、多模态理解、异步入库及完整 E01–E17 验收。预算 Agent 是限定工具集的执行循环。保持排除统一模型网关、权限审计、性能成本治理和 SDD/AI Coding。

[实施状态](docs/04-implementation-status.md) · [设计与实验](docs/README.md)
