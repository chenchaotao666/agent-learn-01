# 0.3：高级检索、异步入库与对照实验

2026-11-10。此次在现有 Pydantic / Pydantic AI / DeepSeek 基础上扩展实现，不增加统一网关、权限审计、性能成本治理或 AI Coding 模块。

## 检索与分块

`RETRIEVAL_MODE=vector|keyword|hybrid`，默认 `vector`。本地 BM25 使用中文双字与英文/数字标识分词；它是可复现的关键词基线，不等于专业中文分词。融合使用 RRF，每路贡献 `1/(60+rank)`，避免直接相加不同量纲的分数。

`QUERY_REWRITE=model` 使用 Pydantic AI 调用当前模型进行改写，校验原问题中的编号和数字是否保留；原始查询同时保留。Mock 下改写保持原文，因此不代表改写质量。每路最多召回 30 个片段，最多将融合后的 20 个片段送入重排。

`RERANK_MODE=model` 使用当前模型返回候选编号排列，严格检查完整性和唯一性；`compatible` 调用独立 `/rerank` 服务，配置 `RERANK_BASE_URL`、`RERANK_MODEL`、`RERANK_API_KEY`。默认 `off`。重排服务的响应格式参照 [Jina API](https://docs.jina.ai/)。模型重排与专用交叉编码器不是同一种实现，评测时需区分。

`CHUNK_STRATEGY=fixed|structure`，默认固定 450 字符、重叠 60 字符。结构模式按标题/空段边界组织片段，过长块仍用滑动窗口；片段保持原文子串及行号，不伪造原文。切换策略并更新同一文档会发布新版本，历史引用仍可查看。它不是完整的表格语义解析或 Token 分块。

`POST /v1/search/inspect` 可按请求选择策略，返回原查询、改写、每路排名、融合分数和最终片段。页面入口为工作台“检索与入库”。普通问答及预算 Agent 使用环境默认策略。

## 外部索引与云端向量

```powershell
docker compose -f infra/compose.yml up -d
```

配置 `KEYWORD_BACKEND=elasticsearch`、`VECTOR_BACKEND=qdrant` 后重启 API/Worker。Compose 将服务仅绑定本机端口，使用持久化 Docker 卷。不要使用 `down -v`，除非明确需要删除索引数据。

ES 按预分词文本进行关键词搜索；Qdrant 使用余弦向量查询。协议依据：[Elasticsearch 索引 API](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-indices-create)、[Qdrant 查询 API](https://api.qdrant.tech/api-reference/search/query-points)。

当前适配器在查询时批量同步有效片段，按当前有效片段 ID 过滤，再由 SQLite 二次检查版本/删除状态。这样可以验证数据一致性，但属于小数据学习实现：旧索引片段暂不物理清理、同步不是增量 CDC，大知识库应改为发布事件驱动的增量索引。外部服务失败会返回错误，不伪装为已完成的外部检索。

`EMBEDDING_MODE=compatible` 支持独立云端 `/embeddings` API，按响应 `index` 恢复输入顺序、验证向量数量及维度并归一化。配置向量服务自己的地址、模型与密钥；不将 DeepSeek 对话服务当作向量服务。切换向量命名空间后需重新导入。

## 持久化入库队列

API 上传解析后的文本进入队列，Worker 独立运行：

```powershell
.\.venv\Scripts\python.exe -m apps.worker.main
```

主要接口：`POST /v1/ingestions`、`POST /v1/ingestions/upload`、`GET /v1/ingestions/{id}`、`POST /v1/ingestions/{id}/retry`、`POST /v1/ingestions/{id}/cancel`。同一请求编号重放返回原任务；内容不一致返回 409。

Worker 通过 SQLite 事务领取租约，处理受配置超时约束。进程中断后，租约过期的任务可以被另一个 Worker 接管；连续接管最多三次，之后标记失败。过期 Worker 或已取消任务不能发布。文档版本发布和任务完成在同一事务内提交；向量失败时旧版本不变。失败任务可手动重试，已发布任务不可通过取消来删除文档。

PDF/DOCX 解析目前在上传请求中完成，队列处理分块、向量与发布；尚未将扫描 OCR/原文件解析搬到独立进程。此队列不是业务 Agent 的跨进程调度器。

## Agent、记忆和反馈

- `mode=multi_agent`：调查 Agent 仅可查询预算与制度，完成后记录交接证据及预算版本，再由草稿 Agent 计算与生成草稿。单 Agent、固定工作流共用业务校验及人工确认接口。
- 连续三次重复已完成工具动作会停止执行，避免没有进展的工具循环。
- 保存记忆可填写 `ttl_days`，到期后从上下文及有效列表排除，并使关联摘要失效；不自动刷新有效期。
- 反馈发布检测同一问题的冲突答案，要求人工重新复核；评测保存检索配置与排名记录，页面可比较同一数据集的两次结果。

## 实测与限制

Windows / Python 3.13：63 项自动测试通过，包含版本/删除隔离、改写及重排契约、外部索引过滤、云端向量顺序、任务取消/租约接管/旧 Worker 防发布、失败保持旧版本、记忆到期、三种 Agent 模式及重复动作停止。浏览器验证检索面板、独立 Worker 发布、双 Agent 交接与移动布局，无 JavaScript 页面错误。

运行 `python -m scripts.evaluate_retrieval`，报告写入 `reports/runs/retrieval-<时间>/`，包含逐题结果、数据与源码哈希及指标。现有 20 条合成开发题的结果：

| 分块 | 召回 | 有答案题 Hit@3 | MRR@3 | 无答案题未召回 |
| --- | --- | --- | --- | --- |
| 固定 | 向量 | 19/19 | 1.0 | 1/1 |
| 固定 | BM25 | 19/19 | 1.0 | 0/1 |
| 固定 | RRF | 19/19 | 1.0 | 0/1 |
| 结构 | 向量 | 19/19 | 1.0 | 1/1 |
| 结构 | BM25 | 19/19 | 1.0 | 0/1 |
| 结构 | RRF | 19/19 | 1.0 | 0/1 |

“公司的火星出差补贴是多少？”在关键词和融合模式下误召回了正常差旅资料。因此保留向量模式为默认；这些数据不支持宣称融合检索更准确。正式评估还需扩充同名实体、跨文档、长文档、否定条件与无答案题，并增加语义向量及人工回答评分。结构分块在当前短文集上没有体现收益。

本机未配置 DeepSeek/语音/向量/重排服务凭据，Docker 引擎未运行。ES/Qdrant 和云端适配器已通过模拟 HTTP 契约测试，未声称完成真实服务联调；模型改写、重排、双 Agent 的真实质量尚无数据。OCR、多模态理解与完整 E01–E17 验收仍未完成。
