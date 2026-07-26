# P2-P4 可运行 RAG 基线冻结契约

- 契约版本：`p2-p4-v1`
- 状态：`frozen-for-implementation`
- 冻结日期：`2026-07-26`
- 前置状态：P0 已冻结并审查通过；P1 已实现并通过门禁

## 1. 交付边界

本轮按 `P2 → P3 → P4` 实现：

1. Redis、MinIO、Milvus 与 MySQL 的 Docker Compose 开发环境、健康检查、超时、降级和恢复说明。
2. PDF、DOCX、Markdown、TXT 的上传、解析、结构化父子切分、摄取状态机、幂等、Outbox 与补偿。
3. Dense、BM25、Hybrid、RRF/Weighted 融合、Reranker、上下文扩展、Query Rewrite、多查询和固定开发评估。
4. 知识文档与知识问答前端，展示摄取状态、检索配置、分数、固定版本来源和降级状态。

P5 及之后的多人协同、GraphRAG、正式证据门禁、LangGraph、HITL、SSE 和 MCP Streamable HTTP 不属于本轮。
本轮可以提供 Basic RAG 的实验性引用与无证据拒答，但不得宣称 P7 已完成。

## 2. 角色与隔离

| 动作 | OWNER | ADMIN | EDITOR | VIEWER |
|---|---:|---:|---:|---:|
| 浏览发布文档、检索、查看固定版本来源 | 是 | 是 | 是 | 是 |
| 上传、创建版本、重试或取消摄取 | 是 | 是 | 是 | 否 |
| 软删除或恢复文档、查看原始评估报告 | 是 | 是 | 否 | 否 |

所有文档、版本、任务、Chunk、Outbox、补偿和向量查询都必须绑定 `workspace_id`。
非成员、路径 Workspace 与资源 Workspace 不一致统一返回 404。检索必须复核 MySQL 当前发布指针，
未发布版本和已被替换版本不得命中。

## 3. 状态与事实源

MySQL 是唯一业务事实源。Redis 只负责通知、短期进度和缓存；Milvus 可由 MySQL 中的已发布
Chunk 重建；MinIO 保存私有原文件和派生产物，所有外部副作用由 Outbox 驱动并可补偿。

```text
DocumentVersion:
PUBLISHING → PUBLISHED
          └→ FAILED
旧 PUBLISHED ──成功切换后──→ SUPERSEDED
```

```text
Ingestion:
QUEUED → PARSING → CHUNKING → STORING → INDEXING → SUCCEEDED
   └────────────→ RETRY_WAIT ─→ 原失败阶段
任一非终态 → COMPENSATING → FAILED/CANCELLED
```

失败版本不得替换旧发布版本。重试创建新 Job 并保留原尝试历史；租约超时进入 `RETRY_WAIT`。

## 4. MySQL 模型

实现以下 Workspace 级实体及 Alembic 迁移：

- `documents`：逻辑文档、软删除、乐观版本、当前 `published_version_id`。
- `document_versions`：格式、MIME、文件哈希、对象键、解析/切分/索引 Profile、发布状态。
- `ingestion_jobs` 与 `ingestion_job_attempts`：状态、进度、租约、重试链、脱敏错误和追加式历史。
- `document_chunks`：同一版本中的 `PARENT`/`CHILD`、标题路径、页码、字符范围、稳定
  `source_span_id`、正文和向量索引状态。只索引 CHILD。
- `outbox_events`：只含 ID、哈希、对象键和配置版本，不含文件正文。
- `compensation_tasks`：删除孤儿对象、停用/删除向量、验证对象、重建索引。
- 扩展 `idempotency_records`：`IN_PROGRESS/SUCCEEDED/FAILED`、请求哈希算法、操作 ID 和响应引用。

上传请求哈希由规范化表单与文件 SHA-256 组成；不保存原始幂等键。默认
`REUSE_EXISTING` 不创建重复版本，只有显式 `CREATE_NEW` 才允许同内容建立新版本。

## 5. 对象与向量存储

开发基线：

- MySQL `8.4` LTS
- Redis `8.x`，镜像必须固定具体版本
- Milvus `2.6.20`、PyMilvus `2.6.x`
- etcd `3.5.x`
- MinIO 仅批准用于本地/CI；其上游归档风险不允许直接当作生产方案

MinIO Bucket 必须私有：

- `knowledge-source`
- `knowledge-derived`

对象键由服务端生成：

```text
workspaces/{workspace_id}/documents/{document_id}/versions/{version_id}/source/{sha256}.{ext}
```

Milvus Collection 按 Embedding Profile 版本化为 `knowledge_chunks__{profile_hash}`，字段至少包括
Chunk/Workspace/Document/Version/Parent ID、格式、页码、时间、`active`、正文、Dense Vector 和
BM25 Sparse Vector。Dense 使用 COSINE + HNSW；Sparse 使用 Milvus BM25 Function。客户端不得
传入原始 Milvus Filter 表达式。

发布顺序：

1. 新版本向量以 `active=false` 写入并校验。
2. 激活新版本向量。
3. MySQL 事务切换发布指针、版本状态和 Job 终态。
4. Outbox 异步停用旧版本向量。
5. 每次搜索再与 MySQL 发布指针复核。

## 6. 解析与父子切分

限制：上传最大 50 MiB，TXT/Markdown 最大 10 MiB，PDF 最大 500 页；校验扩展名、MIME 与魔数。
拒绝 DOCM、加密或无文本扫描 PDF、嵌套归档、外部关系和路径穿越。文档内容是不可信数据，
不得当作系统指令或触发工具。

`structure-v1` 解析标题、段落、列表、表格、代码块、页码与字符范围。`parent-child-v1`：

- Parent：章节级，最大约 1200 tokens。
- Child：目标 384、最小 96、最大 512、重叠 64 tokens。
- 优先在标题、段落和句界切分；超长块才硬切。
- 表格尽量原子保存，过长按行切分并重复表头。
- `source_span_id` 由版本、页码和规范化半开字符范围生成。

## 7. REST 契约

沿用 `/api/v1`、Bearer、统一错误 Envelope、`Idempotency-Key`、`If-Match` 和 `X-Request-ID`。

- `POST /workspaces/{wid}/documents`
- `GET /workspaces/{wid}/documents`
- `GET/PATCH/DELETE /workspaces/{wid}/documents/{document_id}`
- `GET /workspaces/{wid}/documents/{document_id}/versions`
- `POST /workspaces/{wid}/documents/{document_id}/versions`
- `GET /workspaces/{wid}/ingestion-jobs/{job_id}`
- `POST .../ingestion-jobs/{job_id}/retry`
- `POST .../ingestion-jobs/{job_id}/cancel`
- `POST /workspaces/{wid}/search`

摄取 Job 至少返回版本 ID、状态、阶段、进度、尝试次数、是否可重试、脱敏错误、乐观版本和更新时间。

搜索请求支持：

- `retrieval_mode=DENSE|SPARSE|HYBRID`
- `top_k`、`candidate_k`
- `fusion=RRF|WEIGHTED`
- `rerank=OFF|AUTO|ON`
- `rewrite=OFF|AUTO|ON`
- `multi_query`
- `expand_context`
- `response_mode=HITS|ANSWER`
- 文档、格式和发布时间过滤

Hit 必含 Chunk/Document/Version ID、标题、页码、章节、短引用、`source_span_id`、总分、
分数分解和上下文。响应必须标记实际 Provider/Profile、实际启用组件、耗时分解、Warnings 与
`degraded`。无可用 LLM 时 `ANSWER` 明确返回依赖不可用，不得生成模板答案；`HITS` 仍可工作。

## 8. P4 检索流水线

```text
授权与输入校验
→ 原查询 + 最多两个 Rewrite 查询
→ 每个查询的 Dense + BM25 candidate_k
→ RRF(k=60) 或 Weighted(默认 Dense 0.65 / BM25 0.35)
→ Rerank 前 30
→ Top K
→ 同版本 Parent + 相邻 CHILD ±1 上下文扩展
→ MySQL 当前发布指针复核
→ Hits 或结构化 Answer
```

扩展上下文不改变原 Hit 排名，也不重复计入 Recall/nDCG；默认总上下文预算为 2500 tokens。

## 9. Provider 与本地测试模式

统一抽象：

- `EmbeddingProvider.embed_documents/embed_query`
- `RerankProvider.rerank`
- `LLMProvider.rewrite`
- `LLMProvider.answer`

模式：

- `REMOTE`：OpenAI-compatible 或专用 Adapter，密钥只来自环境。
- `LOCAL`：固定模型路径与 revision/hash。
- `FIXTURE`：只允许测试环境，用确定性向量和固定输出验证协议、幂等、故障和评估 Runner。
- `DISABLED`：相关能力明确降级或失败。

所有响应和报告必须记录 Provider 模式。FIXTURE 的 Recall、延迟和答案质量不能作为真实 RAG
效果或简历数字。旧灵感助手的中文 fallback 不得复用成 RAG 答案。

## 10. 前端范围

- `/w/:workspaceId/knowledge`：文档卡片、上传、格式/状态筛选、进度和重试。
- `/w/:workspaceId/ask`：问题、实验性答案/拒答、来源卡片和可折叠检索设置。
- 主导航：`灵感 / 知识 / 问答`，路由懒加载。

Workspace 切换要取消旧 Workspace 请求和轮询，清空文档与搜索状态，并复用既有 404 权限撤销
恢复。轮询从 2 秒退避到 10 秒，页面隐藏时降频；刷新后可按状态 URL 恢复。

## 11. 开发评估

固定 `dev-v1` JSONL，记录 Dataset/Corpus Hash、问题、是否可答、qrels、Gold Source、过滤器、
预期拒答和标签。每次运行输出 Manifest、逐题检索结果、答案结果、Metrics、Latency 和 Failures。

消融顺序：

1. Dense Top-5。
2. 加父子 Chunk。
3. 加 BM25/RRF。
4. 加 Reranker。
5. 加父/邻接扩展。
6. 加条件 Rewrite/多查询。

硬性开发门：

- 跨 Workspace 泄露率为 0。
- 非当前发布版本命中率为 0。
- 返回引用的版本与 Span 完整率为 100%。
- 相同上传的重复索引率为 0。
- Hybrid Recall@5 不比 Dense 低超过 0.01，并且 Recall@5、MRR@10、nDCG@10 至少一项提高 0.03，
  否则默认仍使用 Dense。
- Rerank nDCG@10 不比 Hybrid 低超过 0.01。
- Rewrite 只有 Recall@5 提高至少 0.02 才能默认开启。
- 零证据开发用例必须全部拒答；完整拒答 F1 和 Groundedness 只报告，不提前通过 P7。

## 12. 本机无 Docker 时的验证分层

- L0：Ruff、mypy、pytest、ESLint、vue-tsc、Vitest、生产构建和配置静态检查。
- L1：Fake Redis/S3/Milvus、FIXTURE Provider、四类 Parser 和本地确定性评估。
- L2：连接外部隔离的真实 MySQL/MinIO/Milvus，记录版本和配置。
- L3：完整 Compose、卷重启、停容器、恢复、补偿与端到端。

当前主机未安装 Docker，因此本轮可以完成 L0/L1，但在 L2/L3 之前不得宣称 Compose、MinIO S3
语义、Milvus 原生 BM25/Hybrid、卷恢复、跨存储补偿、真实性能或 P2/P3/P4 阶段门已经通过。
