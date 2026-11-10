# 知行 · AI 办公实验室

使用 **Pydantic 校验接口参数、Pydantic AI 实现 Agent、DeepSeek API 提供模型推理**。支持知识问答、预算审查、任务恢复、记忆、MCP/Skill、翻译及反馈回归评测。业务数据和审批为本地模拟。

## 启动（Windows PowerShell）

首次运行前，先完成下方的 [Ollama 与 Qwen3 Embedding 4B 安装](#ollama-与-qwen3-embedding-4b-安装windows)，并启动 [Qdrant 独立服务](#qdrant-独立服务docker)。日常启动时，保持 Ollama 和 Docker Desktop 后台运行。

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

## 日常启动（已安装，无需重新下载模型）

以下使用本机的 E 盘安装路径。启动顺序为 **Ollama → Docker Desktop → Qdrant → 项目 API**。

### 1. 启动 Ollama

打开 PowerShell 执行，并保持窗口运行：

```powershell
$env:OLLAMA_MODELS = 'E:\work\model'
& 'E:\work\model\Ollama\ollama.exe' serve
```

如果提示 `11434` 端口已占用，可能 Ollama 已在后台运行。另开窗口检查，能返回版本信息就无需重复启动：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/version
```

### 2. 启动 Docker Desktop 和 Qdrant

在另一个 PowerShell 窗口启动 Docker Desktop：

```powershell
Start-Process 'E:\work\WSL-Docker\Docker\Docker Desktop.exe'
```

等待 Docker Desktop 显示引擎运行。可以用下面命令确认，预期返回 `linux`；若连接失败，等待引擎就绪后重试：

```powershell
& 'E:\work\WSL-Docker\Docker\resources\bin\docker.exe' info --format '{{.OSType}}'
```

进入项目目录并启动 Qdrant：

```powershell
cd E:\work\self-projects\agent-learn\agent-learn-01
& 'E:\work\WSL-Docker\Docker\resources\bin\docker.exe' compose up -d qdrant
& 'E:\work\WSL-Docker\Docker\resources\bin\docker.exe' compose ps
Invoke-RestMethod http://127.0.0.1:6333/readyz
```

Qdrant 在后台运行，可以打开 [Qdrant 管理页面](http://127.0.0.1:6333/dashboard)。检查就绪成功后再启动项目；使用期间保持 Docker Desktop 后台运行。

### 3. 启动项目 API

在项目目录执行，并保持窗口运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8010
```

打开 [知识问答](http://127.0.0.1:8010) 或 [Agent 工作台](http://127.0.0.1:8010/workbench)。如果项目已经运行，不需要重复启动；可打开 [服务状态](http://127.0.0.1:8010/health) 检查，预期包含：

```json
{
  "embedding_mode": "ollama",
  "embedding_model": "qwen3-embedding:4b",
  "qdrant_mode": "server"
}
```

日常启动无需再次执行模型下载或重新向量化。前台运行的 API 和 Ollama 可在各自窗口按 `Ctrl+C` 停止；Qdrant 可在项目目录执行以下命令停止，数据卷会保留：

```powershell
& 'E:\work\WSL-Docker\Docker\resources\bin\docker.exe' compose stop qdrant
```

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

## Ollama 与 Qwen3 Embedding 4B 安装（Windows）

Ollama 是本地模型运行工具，`qwen3-embedding:4b` 是本项目使用的语义向量模型。模型约需下载 2.5 GB，输出 2560 维向量；Ollama 安装程序和运行库还需要额外空间。安装方式参考 [Ollama Windows 官方说明](https://docs.ollama.com/windows)，模型信息见 [官方模型页](https://ollama.com/library/qwen3-embedding)。

以下步骤将程序安装到 `E:\work\model\Ollama`，模型权重保存到 `E:\work\model`。已按这些路径安装并下载过模型的机器，可直接执行第 3 步检查，无需重新安装。

### 1. 设置模型目录并下载安装包

在 PowerShell 执行，目录可按需修改：

```powershell
New-Item -ItemType Directory -Path 'E:\work\model' -Force | Out-Null
# 保存为用户环境变量，后续启动的 Ollama 都使用这个目录
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', 'E:\work\model', 'User')
# 同时让当前 PowerShell 及其启动的进程立即使用新目录
$env:OLLAMA_MODELS = 'E:\work\model'

curl.exe -L --fail --output 'E:\work\model\OllamaSetup.exe' https://ollama.com/download/OllamaSetup.exe
if ($LASTEXITCODE -ne 0) { throw 'Ollama 安装包下载失败' }
```

如果已运行 Ollama，修改 `OLLAMA_MODELS` 后需要退出托盘中的 Ollama 并重新启动。该变量只改变存储位置，不会自动搬迁旧目录里的模型。

### 2. 安装并启动 Ollama

```powershell
Start-Process -FilePath 'E:\work\model\OllamaSetup.exe' `
    -ArgumentList '/DIR="E:\work\model\Ollama"' -Wait
```

按安装向导完成安装，随后启动 Ollama，等待后台服务就绪。安装器通常会自动启动它。若选择只运行命令行服务，可在单独的 PowerShell 窗口执行以下命令并保持窗口运行；已有服务时不要重复执行：

```powershell
$env:OLLAMA_MODELS = 'E:\work\model'
& 'E:\work\model\Ollama\ollama.exe' serve
```

### 3. 下载模型并检查服务

在另一个 PowerShell 窗口执行：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/version
& 'E:\work\model\Ollama\ollama.exe' pull qwen3-embedding:4b
& 'E:\work\model\Ollama\ollama.exe' list
```

`list` 应包含 `qwen3-embedding:4b`。这里使用完整程序路径，避免安装后旧终端尚未刷新 PATH；新开的终端也可使用 `ollama pull qwen3-embedding:4b`。

权重位于 `E:\work\model\blobs`，模型清单位于 `E:\work\model\manifests`，这是 Ollama 的正常存储结构。

### 4. 验证实际向量化

```powershell
$embedBody = @{
    model = 'qwen3-embedding:4b'
    input = @('国内差旅住宿报销上限是多少？')
    truncate = $false
} | ConvertTo-Json
$embedding = Invoke-RestMethod -Method Post `
    -Uri 'http://127.0.0.1:11434/api/embed' `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($embedBody)) `
    -TimeoutSec 180
$embedding.embeddings[0].Count
& 'E:\work\model\Ollama\ollama.exe' ps
```

预期向量长度为 `2560`。`ollama ps` 可查看模型是否使用 GPU；首次加载可能比后续请求慢。模型文件大小不等于运行时显存占用。

### 5. 配置项目并处理已有文档

在项目 `.env` 中设置：

```dotenv
EMBEDDING_MODE=ollama
EMBEDDING_MODEL=qwen3-embedding:4b
OLLAMA_URL=http://127.0.0.1:11434
MODEL_TIMEOUT_SECONDS=180
RETRIEVAL_MIN_SCORE=0.5
QDRANT_URL=http://127.0.0.1:6333
```

`OLLAMA_MODELS` 是 Ollama 服务的环境变量，不能只写入项目 `.env` 就期待已启动的 Ollama 自动改变模型存储路径。

首次建库时，启动 Qdrant 和应用后直接导入资料。若已有 hash 向量，先停止应用或暂停上传及问答，保持 Ollama 和 Qdrant 运行，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.reembed_documents
```

该脚本重新调用模型生成向量，备份 SQLite 并写入新的 Qdrant 集合，保留文档与引用 ID。`scripts.migrate_qdrant` 只同步已有向量，不能代替重新向量化。

完成后启动或重启应用，通过 `http://127.0.0.1:8010/health` 确认 `embedding_mode=ollama`、`embedding_model=qwen3-embedding:4b`、`qdrant_mode=server`。

## 语音与向量

DeepSeek 用于对话、Agent 和翻译。ASR/TTS 单独配置兼容 `/audio/transcriptions` 和 `/audio/speech` 的服务：`SPEECH_BASE_URL`、`SPEECH_API_KEY`、`ASR_MODEL`、`TTS_MODEL`。支持录音或上传、核对转写、提问及朗读完成回答。未配置时明确报错，尚未与真实语音服务联调。

当前 `.env` 和示例配置使用 `EMBEDDING_MODE=ollama`、`EMBEDDING_MODEL=qwen3-embedding:4b`，通过本机 Ollama 生成语义向量。请保持 Ollama 运行。文档与问题使用同一模型，Qwen3 的问题输入附加检索任务指令。`hash` 仅用于离线测试，不具备语义理解能力。对话仍可使用 DeepSeek，与向量模型独立配置。

已有文档可运行 `.\.venv\Scripts\python.exe -m scripts.reembed_documents` 切换为 Qwen3 Embedding 4B：脚本先备份 SQLite，再批量生成向量、写入隔离的 Qdrant 集合，最后事务更新向量信息；保留文档、分块和引用 ID。执行期间暂停上传及问答，完成后将 `.env` 设置为上述模型并重启应用。默认语义检索阈值为 0.5，仅是当前样例数据的初始值，新增业务资料后需评测调整。

## Qdrant 独立服务（Docker）

应用必须连接独立 Qdrant 服务，默认地址为 `http://127.0.0.1:6333`。连接失败明确报错，不回退到本地模式。单元测试显式使用隔离的本地索引。

先安装并启动 Docker Desktop（Linux containers），在项目根目录执行：

```powershell
.\scripts\start-qdrant.ps1
```

脚本启动容器、等待就绪并迁移 SQLite 中当前模型的有效文档向量。也可手动执行：

```powershell
docker compose up -d qdrant
.\.venv\Scripts\python.exe -m scripts.migrate_qdrant
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8010
```

Compose 固定使用官方 `qdrant/qdrant:v1.20.0` 镜像并设置自动重启。REST 6333 和 gRPC 6334 仅绑定本机。管理页面为 <http://127.0.0.1:6333/dashboard>。向量保存在 Docker 命名卷 `agent-learn_qdrant_storage`，快照保存在 `agent-learn_qdrant_snapshots`，不再使用 `data/app.qdrant/`。Windows 下使用命名卷避免直接挂载 NTFS 数据目录，参见 [Qdrant 官方安装说明](https://qdrant.tech/documentation/quickstart/)。

```dotenv
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=knowledge
QDRANT_API_KEY=
```

如配置 API Key，Compose 和 Python 客户端读取同一个不提交 Git 的 `.env` 文件。集合名附加数据库路径和向量模型的哈希；`/health` 返回实际集合名称及 `qdrant_mode=server`。

启动应用也会幂等同步已有向量，无需重新上传。Qdrant 不可用会阻止应用启动，上传写入失败会回滚文档版本。SQLite 保留文档、版本、会话及恢复索引的向量备份，检索由 Qdrant 服务执行。旧 `data/app.qdrant/` 保留但不再访问。

查看服务：`docker compose ps`；查看日志：`docker compose logs --tail 100 qdrant`；停止：`docker compose stop`。不要运行 `docker compose down -v`，它会删除数据卷。重要数据应创建 Qdrant 快照并复制到独立位置，单机 Docker 部署不等于高可用集群。

当前向量模型为 Ollama 的 Qwen3 Embedding 4B（2560 维）；旧 hash 集合保留但不参与当前模型的检索。

## 验证与范围

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.evaluate
```

冒烟报告位于 `reports/runs/<时间>/`。测试覆盖参数校验、Pydantic AI 工具循环、检查点、确认幂等、记忆、MCP stdio、反馈集及模拟 HTTP 契约。真实 DeepSeek 效果需配置密钥后验证。

本版是单用户学习实现，SQLite 保存发布状态及任务，独立 Worker 处理异步入库。已加入 BM25/RRF、改写、重排、结构分块、ES/Qdrant 适配器和双 Agent 对照。OCR、多模态理解及完整 E01–E17 真实质量验收仍待完成。保持排除统一模型网关、权限审计、性能成本治理和 SDD/AI Coding。

## 0.3 新增功能

工作台新增“检索与入库”：可切换向量、BM25、融合召回，查看每路排名；支持模型查询改写、模型重排或独立 Rerank 服务。默认仍为向量检索——当前开发集显示融合召回对无答案题存在误召回，不能直接认为效果更好。

异步入库需在另一个终端启动 Worker（与 API 使用相同 `.env`）：

```powershell
.\.venv\Scripts\python.exe -m apps.worker.main
```

队列支持固定/结构分块、失败重试、取消、租约过期接管和原子发布。使用 `--once` 只处理一项任务。已有同步上传功能继续可用。

预算任务可选择“调查 Agent → 草稿 Agent”，与单 Agent、固定工作流对照。记忆支持有效天数；反馈评测支持检索策略选择和同数据集结果比较。

运行六组分块/检索消融：

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_retrieval
```

可选外部索引：`docker compose -f infra/compose.yml up -d elasticsearch`，再设置 `KEYWORD_BACKEND=elasticsearch`、`VECTOR_BACKEND=qdrant`、`RETRIEVAL_MODE=hybrid`。首次检索会同步当前有效片段；外部服务不可用时明确失败，不自动切回本地。

云端向量服务使用独立 `EMBEDDING_MODE=compatible`、`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`；DeepSeek 对话密钥不会被复用。完整配置、适配器限制与实测结果见 [高级检索与异步入库](docs/06-advanced-rag-and-ingestion.md)。

[实施状态](docs/04-implementation-status.md) · [设计与实验](docs/README.md)
