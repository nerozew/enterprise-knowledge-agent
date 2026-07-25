# P0 冻结契约

状态：`frozen-approved`  
契约版本：`p0-v1`  
适用分支：`codex/enterprise-rag-upgrade`  
批准日期：`2026-07-25`  
审查结论：`S1=0，S2=0，P0 契约门禁通过`  
冻结提交：首个包含本状态变更的 Git commit

## 1. 不变量

- MySQL 是用户、权限、版本、任务、审批、事件、审计与引用关系的事实源。
- MinIO 保存原文件、解析产物和必要的协同快照。
- Milvus 只保存可重建的 Dense、Sparse 与 Graph 检索索引。
- Redis 只用于队列、短期状态、限流、锁、SSE/协同跨实例广播。
- REST、SSE、MCP 与协同发布调用同一 Service 和授权层。
- 知识、灵感、会话、运行、待办、协同房间、图索引、引用与审计均绑定 `workspace_id`。
- 越权读取统一表现为 404；日志不得记录密钥、完整令牌或私密正文。

## 2. 领域与状态

### 2.1 核心表族

| 表族 | 核心实体 |
|---|---|
| 租户 | `workspaces`、`workspace_members` |
| 审计/幂等 | `audit_logs`、`idempotency_records` |
| 文档 | `documents`、`document_versions`、`document_chunks` |
| 摄取 | `ingestion_jobs`、`outbox_events`、`compensation_tasks` |
| 协同 | `collab_states`；Yjs 草稿状态与已发布版本分离 |
| GraphRAG | `graph_builds`、`graph_entities`、`graph_relations`、`graph_communities`、`graph_reports`、`graph_evidence_links` |
| Agent | `conversations`、`messages`、`agent_runs`、`agent_run_events`、`approval_requests`、`todos`、`citations` |
| 评估 | 版本化 JSONL；可选 `eval_runs`、`eval_results` |

现有 `users`、`inspirations`、`tags`、`inspiration_tags`、`ai_generations` 保留。P1 采用“新增 Workspace → 每用户创建默认私有 Workspace → 可空列回填 → 新索引/约束 → 非空”迁移；过渡期旧 `/api/v1/inspirations` 和 `/tags` 映射到默认 Workspace，并标记弃用。

### 2.2 安全关键逻辑模型

所有 Workspace 资源必须包含 `workspace_id`。父表提供唯一键 `(workspace_id, id)`；子资源使用 `(workspace_id, parent_id)` 复合外键，禁止只用全局 `id` 后在应用层猜测租户归属。

| 表 | 必需字段与约束 | 删除/审计 |
|---|---|---|
| `workspaces` | `id` PK、`name`、`status`、`version`、时间戳 | 软删除；创建、改名、停用写审计 |
| `workspace_members` | `(workspace_id,user_id)` 唯一、`role`、`status`、`version`、邀请人与时间戳 | 只允许状态迁移；角色和成员变化写审计；最后一个 OWNER 不得移除或降级 |
| `documents` | `(workspace_id,id)` 唯一、`title`、`source_type`、`published_version_id`、`version`、`deleted_at` | 软删除；发布指针变更写审计 |
| `document_versions` | `(workspace_id,document_id,version_no)` 唯一、`status`、`sha256`、`object_key`、`parser_profile`、作者和时间戳 | 发布版本不可变；不得硬删除被引用版本 |
| `document_chunks` | `(workspace_id,document_version_id,ordinal)` 唯一、`source_span_id`、页码、标题路径、字符范围、内容哈希、向量 ID | 可从发布版本重建；引用删除前做依赖检查 |
| `ingestion_jobs` | `(workspace_id,document_version_id,request_hash)` 唯一、`state`、`attempt`、租约、错误和时间戳 | 状态变化写审计；终态只追加尝试记录 |
| `collab_states` | `(workspace_id,document_id)` 唯一、`state_blob`、`state_vector`、`last_update_seq`、`snapshot_hash`、`version` | MySQL 为草稿恢复权威；发布和恢复写审计 |
| `graph_builds` | `(workspace_id,document_version_id,profile_hash)` 唯一、`state`、`graph_version`、错误和时间戳 | 图可重建；失败不得删除 Basic 索引 |
| `agent_runs` | `(workspace_id,id)` 唯一、会话、路由、状态、预算、Checkpoint 引用和时间戳 | Run 终态不可覆盖；状态变化可追踪 |
| `agent_run_events` | `(workspace_id,run_id,seq)` 唯一、事件类型、时间戳、完整脱敏 `payload_json` 或不可变 `payload_ref + sha256` | 只追加；正文按保留策略裁剪 |
| `approval_requests` | `(workspace_id,id)` 唯一、Run、Tool、参数哈希、申请/审批人、状态、过期和版本 | 一次性状态迁移；完整审计 |
| `idempotency_records` | `(workspace_id,actor_id,scope,key)` 唯一、请求哈希、状态、HTTP/MCP状态、完整脱敏响应 envelope 或不可变 `response_ref + sha256`、过期时间 | 只按保留策略清理；不得记录密钥或私密正文 |
| `audit_logs` | `(workspace_id,id)` 唯一、主体、动作、目标、结果、request/trace ID、时间戳 | 只追加；禁止业务接口修改或删除 |

列表索引统一优先 `(workspace_id,status,updated_at,id)`；关系表和过滤列按真实查询补充复合索引。所有时间使用 UTC，API 输出带时区 ISO 8601。

### 2.3 RBAC 动作矩阵

`OWNER` 包含全部能力；`ADMIN` 不得删除 Workspace、转移所有权或移除最后一个 OWNER。

| 动作 | OWNER | ADMIN | EDITOR | VIEWER |
|---|---:|---:|---:|---:|
| 查看 Workspace、已发布知识、灵感、引用 | ✓ | ✓ | ✓ | ✓ |
| 创建/编辑灵感与待办 | ✓ | ✓ | ✓ | 仅查看 |
| 上传文档、创建草稿、进入协同 Room | ✓ | ✓ | ✓ | 只读 Room |
| 发布文档、重试摄取、触发图重建 | ✓ | ✓ | ✓ | — |
| 删除文档、恢复历史版本 | ✓ | ✓ | — | — |
| 查看审计日志和评估报告 | ✓ | ✓ | — | — |
| 邀请/停用 VIEWER 或 EDITOR | ✓ | ✓ | — | — |
| 任命 ADMIN、移除 ADMIN | ✓ | — | — | — |
| 转移所有权、删除 Workspace | ✓ | — | — | — |
| 审批高风险 Tool | 请求人且具目标动作权限，或显式指定的授权审批者 |

服务端从成员状态和角色推导 scopes；前端隐藏按钮不能代替授权。REST、SSE、WebSocket、Worker、Milvus Filter 和 MCP 使用同一授权服务。跨 Workspace 请求统一拒绝并覆盖测试。

### 2.4 冻结枚举

- Member：`INVITED | ACTIVE | SUSPENDED | REMOVED`
- Role：`OWNER | ADMIN | EDITOR | VIEWER`
- DocumentVersion：`DRAFT | PUBLISHING | PUBLISHED | SUPERSEDED | FAILED`
- Ingestion：`QUEUED | PARSING | CHUNKING | STORING | INDEXING | SUCCEEDED | RETRY_WAIT | FAILED | COMPENSATING | CANCELLED`
- GraphBuild：`QUEUED | RUNNING | READY | FAILED | STALE`
- AgentRun：`QUEUED | RUNNING | WAITING_APPROVAL | COMPLETED | FAILED | CANCELLED`
- Approval：`PENDING | APPROVED | REJECTED | EXPIRED | CANCELLED`
- Todo：`OPEN | DONE | CANCELLED`
- RetrievalStrategy：`AUTO | BASIC | LOCAL | GLOBAL`

枚举在数据库约束、Pydantic、OpenAPI、前端联合类型、SSE 和 MCP 中必须同名同义。

## 3. 协同发布边界

```text
Yjs 可变草稿
→ 用户显式发布
→ 生成不可变 document_version
→ 摄取、切分和 Basic 索引
→ 成功后切换 documents.published_version_id
→ 新版本对检索可见
→ 异步 Graph 构建，成功后开放 Local/Global
```

- 自动保存不等于发布，草稿不得进入 RAG/GraphRAG。
- 发布校验 Workspace 权限、草稿状态向量、资源版本和 `Idempotency-Key`。
- 同一文档同时只允许一个 `PUBLISHING`。
- 发布失败不影响上一已发布版本。
- 历史引用固定 `document_version_id + chunk_id`，不得随最新版漂移。
- Hocuspocus 服务放在 `/backend/collaboration`，由 Backend Agent 负责；Redis只广播在线状态和跨实例更新，持久化由数据库/对象存储负责。
- Graph 构建是绑定发布版本的可重建派生任务，不阻塞 Basic 发布。图失败标记 `FAILED/STALE` 并降级 Basic，不回退已成功发布版本。

### 3.1 Yjs 权威状态与恢复

- MySQL `collab_states.state_blob` 是草稿恢复权威，保存完整 Yjs 状态、状态向量、内容哈希、单调 `last_update_seq` 和乐观锁版本。
- 可选 `collab_updates(workspace_id,document_id,seq)` 保存自上次压缩后的增量更新，`seq` 唯一且单调；事务同时写更新和推进 `last_update_seq`。
- Hocuspocus 收到更新后先完成 MySQL 持久化，再向客户端确认；Redis只做跨实例广播和 Awareness，不参与恢复事实。
- MinIO只保存不可变的周期备份与已发布快照，键包含 Workspace、文档、序号和哈希。备份通过 Outbox 在数据库提交后异步写入，不是恢复必需条件。
- 恢复顺序固定为：加载 MySQL 完整状态 → 按序应用未压缩增量 → 校验状态向量与哈希 → 加入 Room。序号缺口或哈希不一致时停止写入并告警，禁止静默覆盖。
- 压缩在单文档租约下进行：生成新完整状态并以 CAS 更新版本，成功后再清理已包含的增量；失败保留旧状态。
- MinIO写成功而指针提交失败产生的对象由 `workspace/document/seq/hash` 幂等巡检清理；MySQL提交成功但备份失败由 Outbox 重试。
- 草稿数据遵循 Workspace 删除和保留策略；Awareness/光标状态不持久化。

## 4. GraphRAG 边界

- Basic：P4 Dense + BM25 + Reranker 基线。
- Local：从问题实体扩展实体邻域、关系和原文证据。
- Global：使用社区与社区报告回答跨文档主题和整体总结。
- `AUTO` 根据问题类型和图状态选择；图为 `STALE/FAILED` 或路由失败时降级 Basic，并返回 `degraded_reason`。
- 实体、关系、社区和报告必须有来源边，最终引用回到已发布 Chunk。
- P6 通过 Adapter 比较候选实现；P0 不提前锁死第三方库或图数据库。

## 5. REST 契约

统一前缀 `/api/v1`，除注册登录外均要求身份与 Workspace 权限。

| 组 | 端点 |
|---|---|
| Workspace/RBAC | `/workspaces`、`/workspaces/{id}/members`、`/workspaces/{id}/audit-logs` |
| 灵感 | `/workspaces/{wid}/inspirations`、`/workspaces/{wid}/tags` |
| 文档 | `POST /workspaces/{wid}/documents`、`GET/PATCH/DELETE /documents/{id}` |
| 摄取 | `POST /documents/{id}/versions`、`GET /ingestion-jobs/{id}`、`POST /ingestion-jobs/{id}/retry|cancel` |
| 协同 | `POST /documents/{id}/collaboration-token`、`POST /documents/{id}/publish`、`GET /documents/{id}/versions` |
| 检索 | `POST /workspaces/{wid}/search`，支持 `AUTO/BASIC/LOCAL/GLOBAL` |
| Agent | `/conversations`、`POST /conversations/{id}/runs`、`GET /agent-runs/{id}` |
| HITL | `GET /approvals/{id}`、`POST /approvals/{id}/decision` |
| 待办 | `/workspaces/{wid}/todos` |
| SSE | `GET /agent-runs/{id}/events` |

统一错误：

```json
{
  "error": {
    "code": "version_conflict",
    "message": "资源已被更新",
    "details": [],
    "retryable": false
  },
  "request_id": "uuid",
  "trace_id": "optional"
}
```

冻结错误码：`validation_error`、`authentication_required`、`permission_denied`、`resource_not_found`、`version_conflict`、`idempotency_conflict`、`idempotency_in_progress`、`invalid_state_transition`、`rate_limited`、`dependency_unavailable`、`evidence_insufficient`、`event_history_expired`、`internal_error`。

REST 创建、上传、发布、重试、Run、审批决定必须使用 `Idempotency-Key` Header；MCP 写 Tool 使用必填输入字段 `idempotency_key`，身份仍只从传输上下文取得。唯一域为 `workspace + actor + endpoint/tool + key`。请求哈希算法冻结为 `sha256-jcs-v1`：JSON 使用确定性键排序/编码，文件另加内容 SHA-256。

- 相同键不同请求哈希：409 `idempotency_conflict`。
- 相同键同哈希且 `IN_PROGRESS`：202，返回既有 `operation_id` 和 `Retry-After`。
- 相同键同哈希且 `SUCCEEDED`：重放首次调用的完整脱敏 envelope，或读取并校验不可变 `response_ref + sha256` 后重放原状态码与响应。
- 相同键同哈希且 `FAILED`：重放已记录失败；显式重试端点创建新 attempt，并使用新幂等键。
- 通用记录至少保留 24 小时；发布、删除、审批和外部动作保留至少 7 天；审计按审计策略长期保留。
- 可变资源使用 `version` 或 `If-Match`；冲突返回 409。

### 5.1 写操作逐项契约

| REST / MCP 写能力 | 最低权限 | 幂等/并发 | 成功与审计 |
|---|---|---|---|
| `POST /workspaces` | 已认证用户 | 幂等键；名称不作为唯一幂等依据 | 201；`workspace.created` |
| 成员邀请/角色/停用 | 见 RBAC 矩阵 | 幂等键 + membership version；最后 OWNER 保护 | 200/201；`membership.*` |
| 创建/修改灵感、待办 | ACTIVE成员；写入需 EDITOR 或该资源的个人权限策略 | 创建用幂等键；修改用 version | 200/201；`inspiration.*`、`todo.*` |
| `POST /workspaces/{wid}/documents` | EDITOR | 幂等键 + 文件 SHA-256 | 201/202；`document.created` |
| `POST /documents/{id}/versions` | EDITOR | 幂等键 + 文档 version + 文件/正文 SHA-256 | 202；`document.version.created` |
| 摄取 retry/cancel | EDITOR | 幂等键 + job version/合法状态 | 202；`ingestion.retried/cancelled` |
| `POST /documents/{id}/publish` | EDITOR | 幂等键 + document version + Yjs state vector/hash；单一 PUBLISHING | 202；`document.publish.requested`，完成后 `published/failed` |
| 删除文档/恢复历史版本 | ADMIN | 幂等键 + version + 影响预览确认 | 202；`document.deleted/restored` |
| `POST /conversations/{id}/runs` | ACTIVE成员且 `agent:run` | 幂等键 + conversation version + 预算 | 202；`agent.run.created` |
| `POST /approvals/{id}/decision` | 请求人且具目标权限或指定审批者 | 幂等键 + approval version + 参数哈希；一次性 | 200；`approval.decided` |
| MCP `document_ingest/publish/delete` | 对应 REST scope | Tool输入 `idempotency_key`；publish/delete另需 `confirm=true` 与资源 version | 与REST相同 Service、状态和审计 |
| MCP `todo_create` | `todo:write` | Tool输入幂等键、审批 ID、参数哈希与 `confirm=true` | 与REST相同；`todo.created` |
| MCP `approval_decide` | 同审批 REST | Tool输入幂等键 + approval version；一次性 | 与REST相同；`approval.decided` |

### 5.2 冻结 Wire Schema

公共类型：

```json
{
  "OperationAccepted": {
    "operation_id": "uuid",
    "resource_type": "document_version|ingestion_job|agent_run|graph_build",
    "resource_id": "uuid",
    "status": "QUEUED|PUBLISHING|RUNNING",
    "status_url": "/api/v1/...",
    "events_url": "/api/v1/agent-runs/{id}/events or null",
    "retry_after_seconds": 2,
    "created_at": "ISO-8601 UTC"
  },
  "VersionRef": {
    "id": "uuid",
    "version": 3,
    "etag": "\"3\""
  },
  "TodoSummary": {
    "id": "uuid",
    "workspace_id": "uuid",
    "title": "...",
    "description": "optional",
    "status": "OPEN|DONE|CANCELLED",
    "due_at": "ISO-8601 UTC or null",
    "assignee_id": "uuid or null",
    "version": 1,
    "created_at": "ISO-8601 UTC"
  },
  "MemberSummary": {
    "workspace_id": "uuid",
    "user_id": "uuid",
    "role": "OWNER|ADMIN|EDITOR|VIEWER",
    "status": "INVITED|ACTIVE|SUSPENDED|REMOVED",
    "version": 1,
    "updated_at": "ISO-8601 UTC"
  },
  "InspirationSummary": {
    "id": "uuid",
    "workspace_id": "uuid",
    "author_id": "uuid",
    "title": "optional",
    "content": "...",
    "summary": "optional",
    "expanded_content": "optional",
    "source_url": "optional",
    "tag_ids": ["uuid"],
    "version": 1,
    "created_at": "ISO-8601 UTC",
    "updated_at": "ISO-8601 UTC"
  }
}
```

安全关键 REST Schema：

| Endpoint | 请求 | 成功响应 |
|---|---|---|
| `POST /workspaces/{wid}/members` | `{"email":"...","role":"EDITOR|VIEWER"}`；Header `Idempotency-Key` | `201 MemberSummary`；已存在成员按幂等规则重放或返回409 |
| `PATCH /workspaces/{wid}/members/{user_id}` | `{"role?":"ADMIN|EDITOR|VIEWER","status?":"ACTIVE|SUSPENDED|REMOVED","version":N}`；Header 幂等键、`If-Match` | `200 MemberSummary`；最后 OWNER 保护 |
| `POST /workspaces/{wid}/inspirations` | `{"title?":"...","content":"...","source_url?":"...","tag_ids":["uuid"]}`；Header幂等键 | `201 InspirationSummary` |
| `PATCH /workspaces/{wid}/inspirations/{id}` | `{"version":N,"title?":"...","content?":"...","summary?":"...","expanded_content?":"...","source_url?":"...","tag_ids?":["uuid"]}`；Header幂等键、`If-Match` | `200 InspirationSummary` |
| `POST /workspaces/{wid}/documents` | multipart：`file`、`title?`、`knowledge_base_id`、`parser_profile`；Header `Idempotency-Key` | `202 OperationAccepted`，`resource_type=document_version` |
| `POST /documents/{id}/versions` | multipart或JSON：`file`/`content` 二选一、`base_document_version`、`parser_profile`；Header 幂等键、`If-Match` | `202 OperationAccepted` |
| `POST /documents/{id}/collaboration-token` | `{"permission":"read|write","client_id":"uuid"}`；Bearer身份 | `200 {"token":"短期令牌","room":"workspace:document","permission":"...","expires_at":"..."}`；响应 `Cache-Control: no-store` |
| `POST /documents/{id}/publish` | `{"draft_version":N,"state_vector":"base64","content_hash":"sha256","parser_profile":"...","index_profile":"..."}`；Header 幂等键、`If-Match` | `202 OperationAccepted`，`resource_type=document_version`；完成后轮询版本/摄取状态 |
| `POST /ingestion-jobs/{id}/retry` | `{"version":N,"reason?":"..."}`；Header 幂等键 | `202 OperationAccepted`，返回新 `ingestion_job` ID |
| `POST /conversations/{id}/runs` | `{"message":"...","knowledge_base_ids":["uuid"],"retrieval_strategy":"AUTO|BASIC|LOCAL|GLOBAL","budget":{"max_tokens":N,"max_cost?":N}}`；Header 幂等键 | `202 OperationAccepted`，`resource_type=agent_run`，`status_url` 与 `events_url` |
| `POST /approvals/{id}/decision` | `{"decision":"APPROVED|REJECTED","version":N,"tool_args_hash":"sha256","comment?":"..."}`；Header 幂等键 | `200 {"approval_id":"uuid","status":"...","decided_at":"...","run":{"id":"uuid","status":"RUNNING|CANCELLED"}}` |
| `POST /versions/{id}/restore-as-draft` | `{"document_version":N,"confirm":true}`；Header幂等键 | `201 {"document_id":"uuid","draft_version":N,"status":"DRAFT","version":N}`；只创建新草稿，不改已发布指针 |
| `DELETE /documents/{id}` | `{"version":N,"confirm":true}`；Header 幂等键 | `202 OperationAccepted`，清理状态通过 `status_url` 查询 |

所有响应都携带 `X-Request-ID`；错误使用第5节统一 envelope。`202` 不代表业务完成，客户端只能通过 `status_url`、SSE或最终资源快照判断终态。

核心 MCP Tool Schema：

| Tool | 必填输入 | `data` 返回 |
|---|---|---|
| `knowledge_search` | `workspace_id,query,strategy,top_k,filters` | `query,strategy_used,hits[{chunk_id,document_id,version_id,page,section,quote,score}],citations[],degraded,degraded_reason?` |
| `inspiration_create` | `workspace_id,content,title?,source_url?,tag_ids?,idempotency_key` | `InspirationSummary` |
| `inspiration_update` | `workspace_id,inspiration_id,version,title?,content?,summary?,expanded_content?,source_url?,tag_ids?,idempotency_key` | `InspirationSummary` |
| `document_ingest` | `workspace_id,document_id,source_ref,parser_profile,idempotency_key` | `OperationAccepted` |
| `document_publish` | `workspace_id,document_id,draft_version,state_vector,content_hash,parser_profile,index_profile,idempotency_key,confirm` | `OperationAccepted` |
| `agent_run_create` | `workspace_id,conversation_id,message,knowledge_base_ids,strategy,budget,idempotency_key` | `OperationAccepted + events_url` |
| `todo_create` | `workspace_id,title,description?,due_at?,assignee_id?,approval_id,tool_args_hash,idempotency_key,confirm` | `{"todo":"TodoSummary","audit_id":"uuid"}` |
| `approval_decide` | `workspace_id,approval_id,decision,version,tool_args_hash,idempotency_key` | 与审批 REST 成功响应相同 |
| `document_delete` | `workspace_id,document_id,version,idempotency_key,confirm` | `OperationAccepted` |

`source_ref` 只能引用已授权上传会话或服务端对象，禁止客户端让 Worker 读取任意文件路径/URL。Tool具体 Schema 在Pydantic与MCP生成物中实现时不得改变字段语义；新增字段必须可选，破坏性变更提升契约版本。

## 6. SSE 契约

```text
id: 42
event: retrieval.completed
data: {"run_id":"...","seq":42,"timestamp":"...","payload":{}}
```

事件：`run.created`、`route.selected`、`retrieval.started/completed`、`graph.started/completed/degraded`、`model.started/delta/completed`、`citation.added`、`tool.proposed`、`approval.required/resolved`、`tool.started/completed/failed`、`todo.created`、`run.completed/failed/cancelled`、`heartbeat`。

- 事件先写 `agent_run_events(run_id,seq)` 再发送，`seq` 单调递增；保存与线上发送完全一致的脱敏 envelope。
- 客户端用 `Last-Event-ID` 回放并按 ID 去重。
- `model.delta` 的完整 payload 保留 24 小时，结构化事件完整 payload 保留 7 天；超过行内大小门槛时写入不可变对象并保存 `payload_ref + sha256 + content_type`。对象必须先可读并校验哈希，事件事务才可提交。
- 历史过期后删除大载荷并保留事件 ID、类型、时间与 `payload_expired=true` 墓碑；回放请求返回 410 `event_history_expired`，客户端读取 Run/消息/引用终态。
- 每 15 秒心跳，终态后关闭。
- 禁止在 URL 查询参数携带长期 JWT。
- 浏览器使用 `fetch` 流携带 Authorization，或使用 Secure/HttpOnly/SameSite Cookie；原生 EventSource 只有在 Cookie 模式下启用。

## 7. MCP 契约

- 本地使用 stdio；stdout 仅协议消息，日志写 stderr。
- 网络使用 Streamable HTTP；校验 Origin、认证、Workspace 与 scopes。
- 首期身份使用用户 JWT 或 Workspace 作用域服务令牌，保留 OAuth2 扩展点。
- P9 起身份从传输上下文取得，不再把 `local_token` 作为每个 Tool 的业务参数。

核心能力：`workspace_list`、`knowledge_search`、`document_get`、`inspiration_search/get/create/update`、`agent_run_create/get`、`document_ingest/publish/delete`、`todo_create`、`approval_decide`。

高风险 Tool 必须同时具备 scope、资源归属、输入字段 `idempotency_key`、影响预览、`confirm=true`、审批或等价人工确认及审计。stdio 与 Streamable HTTP 使用同一 Tool Schema。检索正文不能触发或授权 Tool。

统一返回：

```json
{
  "ok": true,
  "data": {},
  "request_id": "uuid",
  "warnings": [],
  "degraded": false
}
```

幂等成功重放使用与首次调用语义一致的完整脱敏 envelope：HTTP保存状态码、允许列表响应头和JSON正文；MCP保存 `ok/data/warnings/degraded/request_id`。大响应可外置为不可变对象引用并校验哈希。禁止只凭摘要重新计算响应。

## 8. 100 题评估契约

JSONL 最小字段：

```json
{
  "case_id": "eval-001",
  "dataset_version": "v1",
  "workspace_fixture": "corp-demo-v1",
  "question": "...",
  "expected_route": "direct|knowledge|inspiration|todo",
  "graph_strategy": "none|basic|local|global",
  "answerable": true,
  "gold_facts": ["..."],
  "gold_sources": [{
    "source_span_id":"span-001",
    "document_id":"...",
    "version_id":"...",
    "page":3,
    "char_start":120,
    "char_end":176,
    "quote":"..."
  }],
  "qrels": [{"source_span_id":"span-001","relevance":3}],
  "gold_chunk_ids_by_profile": {"parent-child-v1":["chunk-001"]},
  "gold_entities": [],
  "gold_relations": [],
  "expected_tool": null,
  "expected_tool_args": {},
  "requires_approval": false,
  "forbidden_claims": [],
  "tags": ["multi-hop"]
}
```

正式盲测集 `official-100-v1` 固定分层：Knowledge 60（Basic 30、Local 15、Global 15）、Inspiration 15、Direct 15、Todo 10；全体至少 15 题不可回答，并包含跨 Workspace、提示注入、多跳、版本过期和审批绕过子集。

评估隔离：

- 另建 `dev-v1` 开发集（不少于 50 题）用于 Chunk、Prompt、融合权重、Reranker、路由和拒答阈值调参，不计入正式100题。
- `official-100-v1` 的问题可由评估 Runner 读取，gold/qrels 存在独立受控文件中，禁止进入应用 Prompt、检索索引或调参输入。
- 正式运行前冻结 Git commit、依赖锁、语料、模型/Prompt/参数和 `evaluation_manifest.json`，Baseline 与候选最终方案各执行一次盲测。
- 每次访问或重跑正式集记录操作者、时间、commit、原因和运行ID；失败重跑、人工查看 gold 后的运行必须在报告中披露，不得仍称首次盲测。
- 正式结果发布后 `official-100-v1` 转为回归集；下一轮对外比较需要新的隐藏版本，防止持续过拟合。

`source_span_id` 基于冻结语料的文档版本、页码与半开字符范围 `[start,end)` 生成，跨 Chunk 策略稳定。`gold_chunk_ids_by_profile` 只用于同一切分配置内的诊断，不能代替稳定 Source Span。

Chunk → Source Span 映射冻结为：

1. 只比较相同 `document_id + version_id + page` 的半开字符范围。
2. `coverage = overlap_chars / gold_span_chars`；`coverage >= 0.5` 才形成命中。零长度和缺少定位信息的 Chunk 不形成确定性命中，只进入人工诊断。
3. 一个 Chunk 可映射多个 Span；候选记录保留原始 `chunk_rank`、归一化检索分数和 `source_span_id`。
4. 多个 Chunk 映射同一 Span 时，只保留最小 `chunk_rank`；同排名取最高归一化分数，再以 `source_span_id` 字典序稳定决胜。
5. 唯一 Span 按 `chunk_rank ASC, normalized_score DESC, source_span_id ASC` 排序，去重后再截取 Top K；不得重复累计同一 Span 的 gain。
6. qrels relevance 只参与计分，不参与运行时排序或平局决策。映射器版本与代码哈希写入报告。

指标：

- 检索：Recall@5、MRR@10、nDCG@10。
- 回答：事实正确率、引用 Precision/Recall、引用版本正确率、Groundedness。
- Graph：实体/关系召回、Local/Global 对 Basic 的分层增益、降级正确率。
- Agent：四路由 Accuracy/Macro-F1、工具选择与参数匹配率。
- 安全：拒答 Precision/Recall/F1、跨 Workspace 泄露率、未审批写入率；后二者必须为 0。
- 可靠性：重复上传去重率、补偿成功率、SSE 回放完整率。
- 性能：TTFT、P50/P95、错误率、超时率、Token/请求和成本。

指标口径：

- Recall@5：按上述去重排序后，每个可回答问题的相关 Source Span 在前 5 唯一 Span 中的召回率，报告 Macro Average。
- MRR@10：第一个 relevance > 0 的 Source Span 的倒数排名，未命中为 0。
- nDCG@10：使用 qrels 的 0–3 relevance 和 `2^rel-1` gain，理想 DCG 为 0 时该题不进入检索宏平均并单独报告数量。
- 引用 Precision/Recall：以引用 Claim 是否由目标 Source Span 支持为单位；同时报告 Macro 和逐 Claim Micro。
- 事实正确率/Groundedness：固定 Judge 模型、Prompt、阈值和版本，并对固定人工样本复核；Judge 不得访问系统答案之外的隐藏数据。
- Tool 参数匹配：先按契约做日期、空值、无序集合和标识符规范化，再比较必需字段；同时报告 exact match 与字段级 F1。
- 拒答指标对 `answerable=false` 子集计算 Precision/Recall/F1，并同时报告可回答题误拒率。

每次报告固定：Git commit、依赖锁文件哈希、语料内容哈希、数据集版本、评估器/裁判版本与 Prompt 哈希、模型、Embedding、Reranker、Chunk/Graph profile、Top K、机器配置、日期、随机种子、原始逐题检索/回答/引用/延迟/Token/成本。比较顺序为 Dense Baseline → Hybrid → Rerank → Query Rewrite → Graph Local/Global → Evidence Gate。

## 9. P0 验收

- 本文档、路线与 Agent 配置通过 Reviewer 审查；通过后状态改为 `frozen-approved`，记录批准日期和对应 Git commit。
- 领域枚举、REST、SSE、MCP 和评估 Schema 只有一个冻结版本。
- 所有写接口明确幂等、并发、授权和审计。
- 所有检索明确 Workspace 与发布版本边界。
- P1-P10 能追溯到本文契约。
- P0 不实现或声称任何尚未落地的业务能力与性能数字。
