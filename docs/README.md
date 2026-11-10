# 企业 AI 办公应用实践设计

本项目用于验证 RAG、动态 Agent、多轮与记忆、Tool Calling/MCP/Skill、翻译、多模态、ASR/TTS 和反馈评测闭环。已采用 Pydantic 校验接口参数、Pydantic AI 实现 Agent，默认真实模型为 DeepSeek；实际交付范围及待完成事项见 [实施状态](./04-implementation-status.md)。

以“查询项目预算 → 检索制度 → 分析异常 → 生成审批草稿 → 经确认后提交”为主线，把知识问答接到业务任务上。采用单用户本地实验环境，使用合成数据，业务写入落到模拟系统。

## 阅读顺序

1. [总体设计](./01-platform-design.md)：目标、场景、架构、技术选择与接口。
2. [实验与验收](./02-experiments-and-evaluation.md)：17 组实验、对照方法、数据集和质量指标。
3. [实施路线](./03-implementation-roadmap.md)：阶段依赖、任务清单、首版范围及验收条件。
4. [实施状态](./04-implementation-status.md)：当前已实现能力、验证结果和待完成事项。
5. [Agent、记忆、语音与反馈设计](./05-agent-memory-voice-feedback.md)：框架职责、动态规划、MCP/Skill、长期记忆和数据闭环。
6. [高级检索与异步入库](./06-advanced-rag-and-ingestion.md)：0.3 实现、外部索引配置、独立 Worker、对照实验及已知限制。

## 实践原则

- 先跑通场景，再扩展能力；先建立基线，再比较改进。
- 每项成果保留配置、样本、执行记录和评测报告，允许结论是“没有改善”。
- 检索质量、回答质量、任务成功率分别测量，不以模型声称完成代替业务结果。
- 不复用截图中的准确率或收益数字；结论以本项目实测为准。

## 默认假设

- 单人学习项目，采用 Python 后端、简单 Web 界面和 Docker Compose；Windows 下可采用 WSL2/Docker Desktop。
- Agent 通过 Pydantic AI 直接连接所选模型服务，Ollama 为可选项；测试使用确定性替身，密钥通过环境变量配置。
- 检索、OCR 和音视频组件按阶段加入，不预设可用 GPU。
- 使用模拟预算与审批服务，不接入真实企业业务系统。
- 技术版本、模型配置和运行硬件在首次实施时记录并锁定。
