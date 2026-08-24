# P9 MCP 实施计划与契约（不含部署）

状态：`PLANNED_NOT_IMPLEMENTED`  
契约版本：`p9-mcp-v1-draft`  
目标协议版本：`2025-11-25`  
适用范围：P9 本地 `stdio` 与 FastAPI 内挂载的 Streamable HTTP；不含 Docker、Nginx、TLS、域名、生产 OAuth 和多实例部署。

> 事实边界：本文中的“现有”来自 2026-08-10 的仓库检查；“必须、将、新增”均为待实施契约，不代表已经完成。当前安装环境的 `mcp` SDK 为 1.28.1，支持协议 `2024-11-05`、`2025-03-26`、`2025-06-18`、`2025-11-25`，而 `pyproject.toml` 只声明了 `mcp>=1.9,<2`。P9 在代码与测试中冻结 `2025-11-25`，不得自动追随更新协议。2026-07-28 协议取消 `initialize/initialized` 与传输 Session，当前 SDK 尚不支持，列入后续兼容评审，不纳入本期。

## 0. 现状核查与差距

### 0.1 已有证据

| 能力 | 仓库证据 | 现状判断 |
|---|---|---|
| MCP SDK 与 stdio | `backend/app/mcp/server.py`、`backend/pyproject.toml` | 已有一个 FastMCP stdio 进程，入口为 `mcp.run(transport="stdio")` |
| 旧 MCP Tools | `backend/app/mcp/server.py` | 已注册 11 个 Tool：6 个灵感、4 个标签、1 个 AI 生成；没有 Resource、Prompt、知识检索、P8 Run/HITL Tool |
| 旧 MCP 身份 | `backend/app/mcp/server.py`、`backend/app/core/config.py` | 固定 `MCP_USER_ID`，且每次 Tool 参数暴露 `local_token`；仅校验用户有效，不绑定显式 Workspace，不是 HTTP Bearer 身份模型 |
| 旧 MCP 测试 | `backend/tests/test_services_and_mcp.py` | 只验证错误本地令牌、灵感创建复用 Service、标签 usage count；没有真实 stdio JSON-RPC、initialize/list/call、stdout 纯净、HTTP、Origin、跨租户或 HITL 测试 |
| REST 身份 | `backend/app/api/deps.py` | 已有 JWT Bearer、停用用户拒绝 |
| Workspace/RBAC | `WorkspaceAccessService` | 已有 `OWNER > ADMIN > EDITOR > VIEWER`，无成员/非 ACTIVE Workspace 返回 404，角色不足返回 403 |
| 可信 RAG | `KnowledgeService.search`、`KnowledgeServiceToolAdapter` | 已有 Workspace 作用域、发布版本可见性、引用、拒答与 P8 `KnowledgeSearchInput` |
| 灵感检索 | `InspirationServiceToolAdapter` | 已有 Workspace 作用域、只读搜索与 P8 `InspirationSearchInput` |
| P8 Todo HITL | `TodoApprovalService`、`TodoApprovalResumeWorker` | 已有结构化提案、参数哈希、Checkpoint、审批过期/版本/归属/角色复核、唯一约束与恢复执行 |
| P8 REST/SSE | `backend/app/api/routes/agent.py` | 已有 Conversation/Run/Approval/Todo REST 与持久化 SSE 事件回放；MCP 尚未复用 |
| FastAPI MCP HTTP | `backend/app/main.py` | 尚未挂载；无 `/mcp` Streamable HTTP Endpoint |
| Origin、限流、MCP 观测 | 全仓库检查 | 尚未实现 MCP 专用校验、限流、方法级日志/指标 |
| 数据迁移 | Alembic `0001` 至 `0011` | P8 事实表和通用审计/幂等表可复用；当前没有 P9 迁移 |

### 0.2 必须关闭的缺口

1. 新建 P9 capability registry 与统一调用上下文；HTTP 和 stdio 只能做传输适配，业务语义必须相同。
2. Tool Schema 中移除 `local_token`、`user_id`、`workspace_id` 等可信身份字段；身份和 Workspace 必须由传输边界注入。
3. 挂载协议版本冻结的 Streamable HTTP，并完成 `initialize`、`notifications/initialized`、`tools/list`、`tools/call`、`resources/list/read`、`prompts/list/get`。
4. 每次 HTTP 请求重新验证 Bearer、用户状态、Workspace 成员状态和 Origin/Host；Transport Session 不能代替认证。
5. `todo_create` 只能生成 P8 Run/Proposal/Approval，不能直接插入 `todos`，不能暴露任何 MCP 审批决定 Tool。
6. 建立结构化 MCP 错误、幂等、超时、限流、审计与最小可观测性。
7. 用真实 MCP ClientSession 分别对 stdio 和 Streamable HTTP 做协议级与安全级测试。

## 1. 目标、用户故事、非目标与验收标准

### 1.1 目标与前置门禁

P9 的目标是让 Codex、Claude Code 或其他 MCP Client 通过统一协议安全复用企业知识、灵感与 P8 行动能力。知识工作台仍以问答、引用和审批卡片为主要交互，不新增传统“工具管理后台”。

前置门禁：

- P1 Workspace、成员、RBAC 与审计必须可用。
- P4/P7 当前 Basic RAG 的发布版本可见性、引用和拒答必须保持通过；P6 GraphRAG 未完成不阻塞 P9，但 MCP 不得宣称 Local/Global 可用。
- P8 Conversation、Run、Dispatch、Checkpoint、HITL、Todo 与 SSE 恢复链路必须通过现有回归。
- MySQL 8/utf8mb4 是业务事实源；Redis、MinIO、Milvus 均不可成为 MCP 授权事实源。

### 1.2 用户故事

1. 作为 Workspace VIEWER，我能在 MCP 客户端询问企业知识并获得固定到发布版本和 Source Span 的答案或明确拒答。
2. 作为 Workspace VIEWER，我能搜索当前 Workspace 的未删除灵感，且不能看到其他 Workspace 内容。
3. 作为 Workspace EDITOR，我能让 MCP 提议创建待办；调用结果只告诉我审批已生成，必须回到现有审批界面由人决定。
4. 作为调用者，我能查询自己发起的 Run、Approval 和 Todo 状态；未知或他人的资源统一表现为不存在。
5. 作为本地开发者，我能用 stdio 启动同一能力集合；凭据只来自进程环境，stdout 始终只有合法 MCP 消息。
6. 作为安全/运维人员，我能按 request ID、actor、workspace、transport、method、capability、result 和 latency 追踪调用，但日志中没有 token、查询正文、文档正文或 Tool 完整参数。

### 1.3 范围

- 协议 `2025-11-25` 的 stdio 与 Streamable HTTP。
- P9 能力注册表、Pydantic 输入输出、Tool annotations、Resources、Prompts。
- 复用现有 JWT、WorkspaceAccessService、KnowledgeService、InspirationService、AgentApi/HITL 服务。
- Streamable HTTP 的 Bearer、Workspace 绑定、Origin/Host、请求体大小、超时、限流。
- 真实 MCP 协议测试、跨租户/降权/停用/审批绕过测试、最小指标和结构化日志。
- 为现有 Vue 审批流补充 MCP 来源展示所需的兼容字段时，由后续开发任务评估；原则上不新增 P9 专用管理页面。

### 1.4 非目标

- Docker、Nginx、TLS、域名、生产部署、多实例 Session 共享与 WAF。
- OAuth 2.1 授权服务器、动态客户端注册、第三方身份提供商和 token exchange。HTTP 首版复用现有第一方 JWT，因此只保证支持自定义 Bearer Header 的客户端；不能宣称完整 MCP OAuth 自动发现兼容。
- 旧 HTTP+SSE transport；仅为确有旧客户端证据时另立兼容阶段。
- GraphRAG Local/Global、P5 协同编辑、跨会话长期记忆、后台主动任务、MCP sampling/elicitation/completion。
- 通过 MCP 上传/修改/删除知识文档、灵感或标签；现有旧 stdio 写 Tool 不进入 P9 allowlist。
- MCP 中批准/拒绝审批、直接创建 Todo、更新 Todo 或任何可绕过人工确认的写能力。
- 生产级 tracing 平台、Prometheus/Grafana/Sentry 接入；本期只提供可测试的日志和内存/应用级指标接口基础。

### 1.5 总体验收标准

- 两种 transport 的 capability 名称、Schema、业务返回和错误码一致；仅身份载入方式不同。
- 真实 Client 完成 initialize → initialized → list → call/read/get；协议与 Schema 用例 100% 通过。
- 未认证、无成员、成员停用、Workspace 错配、角色不足、他人 Run/Approval/Todo、非法 Origin 全部拒绝；跨 Workspace 泄露为 0。
- `todo_create` 在人工审批前 `todos` 新增数恒为 0；MCP 没有 approval decision capability；审批通过后的创建继续满足 P8 唯一约束与审计。
- 相同 actor/workspace/idempotency key/请求哈希只产生一个 P8 Run/Approval；同 key 不同参数返回冲突。
- stdio stdout 非 MCP 字节为 0；所有日志写 stderr。
- P8、RAG、灵感现有回归不退化；未执行的测试不得标记通过。

## 2. 领域模型、角色矩阵、状态机、MySQL 表结构、索引、兼容迁移与回滚策略

### 2.1 领域模型

```text
AuthenticatedActor + BoundWorkspace + TransportMetadata
                         |
                         v
                    McpCallContext
                         |
                  CapabilityRegistry
             / tools / resources / prompts
             |           |          |
             v           v          v
        Existing Business Services and P8 HITL
                         |
                    MySQL facts
```

`McpCallContext` 是可信运行时对象，不进入模型可编辑参数：

```python
class McpCallContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str
    transport: Literal["stdio", "streamable_http"]
    actor_id: UUID
    workspace_id: UUID
    actor_role: Literal["OWNER", "ADMIN", "EDITOR", "VIEWER"]
    client_name: str | None = None
    client_version: str | None = None
    protocol_version: Literal["2025-11-25"]
```

Tool 的 `arguments` 中出现 `workspace_id`、`actor_id`、`local_token`、原始 SQL、Milvus expression 或审批决定字段时，必须由 `extra="forbid"` 拒绝，不能“忽略后继续”。

### 2.2 角色与风险矩阵

| 能力 | VIEWER | EDITOR | ADMIN | OWNER | 风险 | 额外归属规则 |
|---|---:|---:|---:|---:|---|---|
| `knowledge_search` | 是 | 是 | 是 | 是 | R0 只读 | 只检索当前 Workspace 已发布可见版本 |
| `inspiration_search` | 是 | 是 | 是 | 是 | R0 只读 | 不含软删除，Tag 必须属于当前 Workspace |
| `agent_run_get` | 是 | 是 | 是 | 是 | R0 只读 | 仅 `requested_by_user_id == actor_id` |
| `approval_get` | 是 | 是 | 是 | 是 | R1 敏感只读 | 仅请求人；返回参数但不返回 Checkpoint blob |
| `todo_list`/`todo_get` | 是 | 是 | 是 | 是 | R1 敏感只读 | 仅创建人或受派人，沿用 P8 过滤 |
| `todo_create` | 否 | 是 | 是 | 是 | R2 有副作用提议 | 只创建 P8 Run/Approval；最终写入必须人工审批 |
| `approval_decide` | 否 | 否 | 否 | 否 | R3 禁止 | 仅现有 REST + 人类界面 |
| 旧灵感/标签/AI 写 Tool | P9 不暴露 | P9 不暴露 | P9 不暴露 | P9 不暴露 | R2/R3 | 旧 stdio 兼容进程隔离，默认关闭 |

`tools/list` 应按当前角色过滤；`tools/call` 必须再次查 MySQL 复核，不能信任此前 list 或 Session 缓存。权限变化在下一次调用立即生效。

### 2.3 状态机

#### Transport 生命周期（协议 2025-11-25）

```text
NEW -> initialize -> INITIALIZED_NEGOTIATED
    -> notifications/initialized -> READY
READY -> list/read/get/call -> READY
READY -> client disconnect/restart -> CLOSED
```

本期 HTTP 采用 `stateless_http=true` 与 JSON response；Transport Session 不持久化，服务重启后客户端重新 initialize。业务 Run/Approval/Checkpoint 仍由 MySQL 恢复。未启用 server-initiated notification 时，HTTP GET 与 DELETE 按 SDK/协议返回 405；不得伪装支持 resumable MCP SSE。

#### `todo_create` 业务状态

```text
tools/call(todo_create)
  -> authenticate + workspace EDITOR recheck
  -> validate + canonical hash + idempotency recheck
  -> create/reuse P8 Conversation + TODO_CREATE Run + Dispatch
  -> P8 Worker builds exact ToolProposal
  -> Checkpoint + Approval(PENDING) + Run(WAITING_APPROVAL)
  -> MCP returns APPROVAL_REQUIRED (never Todo created)

Human REST decision:
  PENDING --reject/expire--> REJECTED/EXPIRED -> Run terminal, no Todo
  PENDING --approve + all rechecks--> APPROVED -> Todo(OPEN) -> Run(COMPLETED)
```

若异步 Worker 未在 Tool 超时窗口内生成 Approval，返回 `ACCEPTED` 和 `run_id`，由 `agent_run_get` 轮询；不得因 MCP 超时取消已提交的 MySQL Run。

### 2.4 MySQL 事实表与索引

P9 v1 不要求新增业务表；下列现有表为契约依赖，全部保持 MySQL 8、`utf8mb4`：

| 表 | P9 使用字段 | 关键约束/索引 |
|---|---|---|
| `users` | `id,is_active` | 停用即时拒绝 |
| `workspaces` | `id,status,version` | `ix_workspaces_status_updated` |
| `workspace_members` | `workspace_id,user_id,role,status,version` | 复合主键；`ix_workspace_members_workspace_status_role` |
| `document_versions`/`document_chunks` | published status、版本、Chunk、Source Span | Workspace+version+chunk+span 来源约束；只读已发布版本 |
| `inspirations`/`inspiration_tags`/`tags` | Workspace、软删除、标签关系 | 所有查询带 `workspace_id`；复用现有复合 FK/索引 |
| `conversations` | `workspace_id,created_by_user_id,status,version` | `uq_conversations_workspace_id`、`ix_conversations_workspace_updated` |
| `agent_runs` | `workspace_id,conversation_id,requested_by_user_id,status,route,checkpoint_*,version` | Workspace 复合 FK；状态约束；status/conversation 索引 |
| `agent_run_dispatches` | `workspace_id,run_id,status,lease_*` | `uq_agent_dispatch_workspace_run`、`ix_agent_dispatch_due` |
| `agent_run_events` | `workspace_id,run_id,sequence_no,event_type,payload_json` | `uq_agent_run_events_run_seq`；SSE/轮询事实 |
| `agent_checkpoints`/`agent_checkpoint_writes` | Workspace、thread、namespace、checkpoint | Workspace/Conversation 绑定；禁止 MCP 直接读取 blob |
| `approval_requests` | Workspace、Run、Tool Call、args hash、status/version/expiry | `uq_approval_workspace_run_tool_call`、expiry 索引、Checkpoint 完整性 |
| `todos` | Workspace、来源 Run/Tool/Approval、状态/version | `uq_todos_source_call`、`uq_todos_workspace_approval`、status/due/assignee 索引 |
| `audit_logs` | actor/role/action/target/request ID/白名单 metadata | workspace+created/action/actor 三组索引 |
| `idempotency_records` | workspace/actor/scope/key hash/request hash/response/expiry | `uq_idempotency_workspace_actor_scope_key`、expiry 索引 |

审计策略：`todo_create` 的提议、Approval 与最终 Todo 继续写 P8 审计；MCP adapter 可增加 `mcp.tool.accepted`、`mcp.tool.denied` 的白名单 metadata（transport、capability、duration bucket、result code），不得记录 query、正文、token、完整 arguments。只读成功调用默认写结构化应用日志，不逐条写 MySQL，避免审计表放大；敏感拒绝和所有 R2 调用必须有持久审计。

### 2.5 兼容、迁移与回滚

- v1 目标是零 Schema 迁移；实现者必须先运行 `alembic current/check/upgrade head` 验证模型一致。若实现中确需新表或字段，必须先更新本契约并新增可逆 Alembic，不允许手工改库。
- 新 P9 Server 与旧 `app.mcp.server` 隔离；旧入口暂存为 `legacy_stdio`，不挂 HTTP，默认配置关闭。P9 新入口通过 feature flag（如 `MCP_P9_ENABLED`）启用。
- 不在原工具上原地删除 `local_token`；客户端先迁移到新 P9 Server。旧工具下线前至少保留一版 deprecation 日志和文档。
- 回滚时关闭 P9 Endpoint/入口并恢复旧 stdio 入口，不回滚 P8 事实表，也不删除已产生 Run/Approval/Todo。处于 PENDING 的审批继续由 P8 REST 处理。
- 若 P9 后续增加迁移，downgrade 必须先检查未完成 Run/PENDING Approval；有恢复事实时拒绝破坏性降级。

## 3. Redis、MinIO、Milvus 和协同服务的用途、可靠性与降级

| 组件 | P9 用途 | 事实/TTL/幂等 | 故障降级与补偿 |
|---|---|---|---|
| MySQL | 身份有效性、Workspace/RBAC、知识元数据、Run/HITL/Todo、审计、幂等 | 唯一业务事实源；写幂等保留现有 7 天 | 不可用时写 Tool fail closed；只读返回依赖不可用，不使用缓存伪造授权 |
| Redis | 可选：按 actor/workspace/capability 的分布式 token bucket；P8 Worker 唤醒 | 限流 key TTL 为窗口的 2 倍；数据可丢失可重建 | 不可用时单实例开发环境切本地有界 limiter 并标 `degraded=true`；生产门禁要求 Redis 或网关限流，不影响 MySQL 幂等 |
| MinIO | RAG 已发布源文件/衍生物；MCP 不直接发预签名 URL | MySQL 记录对象事实；可由 Outbox/摄取补偿 | 不可用时 `knowledge_search` 按现有 KnowledgeService 降级/错误；灵感与状态 Tool 不受影响 |
| Milvus | Dense 检索索引 | 可由 MySQL 发布版本/Chunk 重建；禁止直接接受模型 filter | 不可用时按现有 RAG 策略降级 BM25 或结构化报错，必须在返回中保留 `degraded/warnings` |
| 协同服务/Yjs | P9 不直接访问 | 草稿不是发布事实，不可进入 MCP RAG | 不可用不影响 MCP；MCP 只能看到显式发布快照 |

不存在 MySQL/Redis/MinIO/Milvus 跨存储原子事务。MCP adapter 不新增跨存储写链；知识检索沿用既有 Outbox/补偿，Todo 沿用 P8 MySQL 事务与 Dispatch 租约。

## 4. REST/SSE/协同契约：认证、请求、响应、事件、错误码、幂等、并发和断线恢复

### 4.1 REST 与 MCP 边界

- Vue 继续使用现有 `/api/v1/workspaces/{workspace_id}/...` REST 与 Agent SSE；不通过 MCP 驱动浏览器主工作台。
- Streamable HTTP MCP 挂载建议为 `POST /api/v1/workspaces/{workspace_id}/mcp`。`workspace_id` 来自受控 URL 路由并经成员校验，不出现在 Tool arguments。
- 同一 URL 仅承载 MCP JSON-RPC；不能混入 REST envelope。FastAPI 通用 `AppError` 需由 MCP adapter 映射为 Tool result/error。
- 审批决定保持现有 `POST /api/v1/workspaces/{workspace_id}/approvals/{approval_id}/decision`，必须由登录用户在 Vue 审批卡片提交 `version + tool_args_hash + Idempotency-Key`。
- MCP 返回 `approval_url`、`status_url`、`events_url` 只能是同源相对路径，不能返回携带 token 的 URL。

### 4.2 Streamable HTTP 请求约束

- 每个请求都要 `Authorization: Bearer <JWT>`；禁止 query/cookie 中 token。
- initialize 后的请求携带 `MCP-Protocol-Version: 2025-11-25`；无效/不支持版本返回 HTTP 400。
- POST `Accept` 同时接受 `application/json` 与 `text/event-stream`；本期服务器实际用 `application/json`。
- Body 上限建议 256 KiB；单个 Tool 参数由 Pydantic 进一步限制。Content-Type 非 JSON、批量 JSON-RPC、未知 method 按 SDK/协议拒绝。
- `Origin` 存在时必须精确匹配 `MCP_ALLOWED_ORIGINS`；非法值 HTTP 403。原生客户端可不发送 Origin，但仍必须 Bearer 且 Host 在 allowlist；不得用 CORS allowlist 代替 MCP Origin 校验。
- 每请求重新解析用户、Workspace 和成员；Transport Session ID（若 SDK 发出）不承担认证与授权。

### 4.3 stdio 请求约束

- 客户端以子进程启动；actor/workspace/本地进程凭据从环境或只读配置注入，绝不成为 Tool Schema 字段。
- 启动时校验配置格式；每次业务调用仍查 MySQL 用户与 membership，防止运行期间降权/停用。
- stdin/stdout 仅 newline-delimited JSON-RPC；日志、traceback、启动提示全部 stderr；禁止 `print()` 污染 stdout。
- stdio 不使用 HTTP Origin/OAuth；安全边界是本机进程启动权限、环境凭据和 MySQL RBAC。

### 4.4 REST SSE 与断线恢复

- MCP 不复制 P8 SSE；`todo_create` 返回 `run_id`，MCP Client 使用 `agent_run_get` 轮询。
- Vue 仍可订阅 `/agent-runs/{run_id}/events`，使用 `Last-Event-ID` 从 MySQL `agent_run_events` 回放。
- MCP HTTP 连接中断不取消已经提交的 P8 Run；相同幂等键重试返回原 operation/run。
- MCP Transport Session 丢失只要求重新 initialize，不影响 MySQL Run/Approval/Checkpoint。

### 4.5 幂等、并发、超时与限流

- 只读 Tool 不要求业务幂等键；可安全重试，但仍受限流。
- `todo_create` 输入必须有 `idempotency_key: str`（8–128 ASCII），scope 为 `mcp.todo_create:v1`，唯一域为 workspace+actor+scope+key hash；request hash 覆盖 canonicalized Todo 参数。
- 同 key/同 hash 返回同一 Run/Approval；同 key/不同 hash 返回 `IDEMPOTENCY_CONFLICT`。并发竞争由 `idempotency_records` 唯一约束、P8 run/tool-call/approval/todo 唯一约束共同收敛。
- HTTP 整体 deadline 默认 30 秒；知识搜索默认 25 秒，灵感/状态 5 秒，Todo 入队 10 秒、等待 Approval 最多 3 秒。超时不得回滚已提交业务事实。
- 建议开发默认限流：initialize/list 60 次/分钟/actor，知识 20 次/分钟/actor/workspace，灵感/状态 60 次/分钟，Todo 5 次/分钟；并发知识调用每 actor 2、Todo 1。具体值配置化并在压测后冻结。
- 超限返回 HTTP 429（传输层）或结构化 `RATE_LIMITED`（已进入 Tool 层），含 `retry_after_ms`，不泄露其他租户额度。

### 4.6 错误契约

传输/JSON-RPC 错误使用标准 JSON-RPC code；业务错误在 `tools/call.result.isError=true`，同时提供机器可读 `structuredContent`：

```python
class McpErrorData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    request_id: str
    retryable: bool = False
    retry_after_ms: int | None = None
    details: dict[str, Any] | None = None  # 仅白名单字段
```

| 场景 | HTTP/JSON-RPC | 业务 code | 原则 |
|---|---|---|---|
| 缺失/无效/过期 Bearer | 401 | `AUTHENTICATION_REQUIRED`/`TOKEN_INVALID` | HTTP 层拦截，不进入 Tool |
| 非法 Origin | 403 | `ORIGIN_FORBIDDEN` | HTTP 层，正文不回显 Origin |
| 不支持协议版本 | 400 | `PROTOCOL_VERSION_UNSUPPORTED` | HTTP 层 |
| 未知 method / Tool / Resource / Prompt | -32601 | `METHOD_NOT_FOUND` | 不模糊匹配名称 |
| 参数 Schema 错误 | -32602 或 isError | `INVALID_ARGUMENTS` | 返回字段路径，不返回敏感值 |
| 无 Workspace/资源/他人资源 | isError | `RESOURCE_NOT_FOUND` | 统一 404 语义，防枚举 |
| 角色不足 | isError | `PERMISSION_DENIED` | 只在已确认成员时返回 |
| 幂等冲突/版本冲突 | isError | `IDEMPOTENCY_CONFLICT`/`VERSION_CONFLICT` | retryable=false/按情况 true |
| 需要审批 | success result | `APPROVAL_REQUIRED` | 不是错误；无 Todo |
| 依赖超时/不可用 | isError | `DEPENDENCY_TIMEOUT`/`DEPENDENCY_UNAVAILABLE` | 不暴露 URL、key、traceback |
| 限流 | 429 或 isError | `RATE_LIMITED` | 提供 retry_after |
| 未知内部异常 | isError | `INTERNAL_ERROR` | 日志关联 request_id，客户端无堆栈 |

### 4.7 协同契约

P9 不新增 Yjs/协同 Endpoint。MCP 知识检索必须沿用 `published_version_id` 过滤；自动保存草稿、Yjs Room 内容、失败发布版本均不可被 Tool 或 Resource 返回。

### 4.8 最小可观测性契约（不含部署）

- 每次请求输出一条结构化日志：`timestamp,level,request_id,transport,protocol_version,client_name,method,capability,result,error_code,duration_ms,response_bytes,degraded`；需要排障时 actor/workspace 只写内部 UUID，不进入指标 label。
- 禁止日志字段：Authorization、Cookie、local token、query/Prompt/灵感/文档正文、完整 arguments/response、Checkpoint blob、Provider key/URL。异常只在服务端保留脱敏 stack，客户端只见 request ID。
- 最小指标名：`mcp_requests_total{transport,method,result}`、`mcp_request_duration_seconds{transport,method}`、`mcp_tool_calls_total{tool,result}`、`mcp_tool_duration_seconds{tool}`、`mcp_active_calls{transport}`、`mcp_rate_limit_rejections_total{tool}`、`mcp_dependency_failures_total{dependency}`。
- 指标 label 禁止 request ID、actor、workspace、run、approval 等高基数字段。`/health/capabilities` 可增加 enabled、protocol version、transport、limiter backend 等非敏感状态，但不得把依赖“已配置”误写成“健康”。
- 本期只要求应用内采集器/测试快照和结构化日志；Prometheus exporter、dashboard、alert、集中日志归入部署阶段。

## 5. MCP 契约：传输、能力类型、名称、参数 Schema、返回结构、权限与风险等级

### 5.1 initialize/list/call 协议

首版冻结以下交互：

1. Client `initialize`：`protocolVersion=2025-11-25`、`clientInfo`、client capabilities。
2. Server 返回同版本、`serverInfo.name="enterprise-knowledge-agent"`、版本号与 `capabilities={tools:{},resources:{},prompts:{}}`；不声明 sampling、elicitation、completion、logging subscription。
3. Client 发送 `notifications/initialized` 后才能做普通请求。
4. `tools/list` 返回角色过滤后的 Tool 与 JSON Schema；分页 cursor 首版接受但列表较小，未知 cursor 拒绝。
5. `tools/call` 的 `params.name` 精确匹配 allowlist，`arguments` 先 Pydantic `extra=forbid`，后授权，再调用业务 Service。
6. `resources/list`/`resources/templates/list`、`resources/read` 与 `prompts/list`/`prompts/get` 服从相同上下文和 RBAC；未知 URI/名称不推断。

P9 HTTP 使用 Streamable HTTP；本地使用 stdio。旧 HTTP+SSE 不启用。实现以 `mcp==1.28.1` 的实际行为和集成测试为基线；升级 SDK 必须先运行协议矩阵，不能仅依赖 `<2`。

### 5.2 通用 Tool 返回

所有 Tool 同时返回：

- `content`：给模型阅读的简洁 TextContent，不含隐藏推理、token、内部路径。
- `structuredContent`：下述 Pydantic 输出的 JSON；调用方应以它为机器事实。
- `isError`：仅业务失败为 true；`APPROVAL_REQUIRED` 是成功状态。

```python
class ToolMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    workspace_id: UUID
    capability_version: Literal["v1"] = "v1"
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=20)
```

### 5.3 Tool 清单与 Pydantic Schema

#### `knowledge_search` — R0，只读，VIEWER+

复用 P8 `KnowledgeSearchInput`，不开放 raw retrieval expression。

```python
class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: KnowledgeFilters = Field(default_factory=KnowledgeFilters)

class KnowledgeSearchOutput(BaseModel):
    meta: ToolMeta
    query: str
    answer: str | None
    refusal: Refusal | None
    hits: list[SearchHit]  # 含 document/version/chunk/source_span/quote/score/context
    answerability: Answerability
    provider: ProviderMetadata
    timings_ms: dict[str, float]
```

Tool annotations：`readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true`、`openWorldHint=false`。必须调用 `KnowledgeService.search(response_mode="ANSWER")`，保留 refusal、发布版本引用、degraded/warnings；检索文本是“不可信数据”，只能作为答案证据，不能改变系统指令或触发其他 Tool。

#### `inspiration_search` — R0，只读，VIEWER+

```python
class InspirationSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str | None = Field(default=None, max_length=1_000)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)

class InspirationHit(BaseModel):
    id: UUID
    title: str | None
    content: str
    summary: str | None
    tag_names: list[str]
    updated_at: datetime

class InspirationSearchOutput(BaseModel):
    meta: ToolMeta
    items: list[InspirationHit]
    total: int = Field(ge=0)
```

Tag ID 去重并做 Workspace 归属校验；固定 `include_deleted=false`、`tag_match="any"`。annotations 同知识检索。

#### `todo_create` — R2，写提议，EDITOR+

名称为兼容用户意图保留，但服务端说明必须明确“创建审批提议，不立即创建 Todo”。

```python
class TodoCreateMcpInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4_000)
    due_at: datetime | None = None  # 必须带时区
    assignee_id: UUID | None = None  # v1 仅允许自己或 None
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[\x20-\x7E]+$")

class TodoCreateMcpOutput(BaseModel):
    meta: ToolMeta
    status: Literal["ACCEPTED", "APPROVAL_REQUIRED"]
    run_id: UUID
    conversation_id: UUID
    approval_id: UUID | None
    tool_call_id: UUID | None
    tool_args_hash: str | None
    expires_at: datetime | None
    todo_id: None = None
    status_url: str
    approval_url: str | None
    events_url: str
```

annotations：`readOnlyHint=false`、`destructiveHint=false`（本调用不直接创建/删除业务资源，但会产生审批事实）、`idempotentHint=true`、`openWorldHint=false`。必须复用 P8 durable Run/Dispatch/Checkpoint/HITL；禁止 adapter 直接调用 Repository 插入 Todo，禁止 MCP Client 传 route、run_id、tool_call_id、hash、approval status。审批后重验 actor、role、Workspace、args hash、version、expiry、Checkpoint 和唯一约束。

#### `agent_run_get` — R0，只读，VIEWER+

```python
class AgentRunGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID

class AgentRunGetOutput(BaseModel):
    meta: ToolMeta
    run: AgentRunSummary
    final_answer: str | None
    refusal: AgentRefusal | None
    citations: list[AgentCitationSummary]
    tool_proposal: ToolProposalSummary | None
    approval: ApprovalSummary | None
    todo: TodoSummary | None
    last_sequence_no: int
```

仅请求人可见；不返回 SYSTEM 消息、Checkpoint blob 或内部异常。

#### `approval_get` — R1，敏感只读，VIEWER+

输入 `approval_id: UUID`，输出 `ApprovalSummary + ToolMeta`。仅请求人可见；可返回白名单 arguments 和 hash 供人核对，但 Tool 描述必须说明“不能据此自动批准”。

#### `todo_list` / `todo_get` — R1，只读，VIEWER+

```python
class TodoListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["OPEN", "COMPLETED", "CANCELLED"] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=512)

class TodoGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    todo_id: UUID
```

只返回 actor 是创建人或受派人的 Todo；cursor 必须签名或使用不透明 keyset，不能接受任意 SQL offset/filter。两个 Tool 均为 readOnly/idempotent。

### 5.4 Resources

Resources 是可读取事实，不是写操作替代品：

| URI/模板 | 输出 | 权限 |
|---|---|---|
| `workspace://current/capabilities` | Workspace ID/name、当前 role、可用 capability 名称、协议/能力版本；不含成员列表 | VIEWER+ |
| `workspace://current/agent-runs/{run_id}` | 与 `agent_run_get` 相同的安全快照 JSON | Run 请求人 |
| `workspace://current/approvals/{approval_id}` | 与 `approval_get` 相同的安全快照 JSON | Approval 请求人 |
| `workspace://current/todos/{todo_id}` | TodoSummary JSON | 创建人或受派人 |

MIME 为 `application/json; charset=utf-8`。URI 中的 `current` 必须解析为传输绑定 Workspace；禁止接收任意 Workspace UUID。Resource 内容不包含 token、Checkpoint、草稿、未发布文档正文或服务配置。

### 5.5 Prompts

| 名称 | 参数 | 返回语义 | 权限/风险 |
|---|---|---|---|
| `knowledge_qa` | `question: 1..1000`、`top_k?:1..20` | 指示 Client 调用 `knowledge_search`、仅依据 citations 回答、证据不足时拒答 | VIEWER+，R0 |
| `todo_proposal` | `title`、`description?`、`due_at?` | 指示 Client 展示将产生审批，并仅在用户明确请求后调用 `todo_create` | EDITOR+，R1 Prompt 本身只读 |

Prompt 只返回模板消息，不能嵌入检索正文、用户隐私、隐藏系统提示或 token；Prompt 不能授予权限，也不能自动触发 Tool。`prompts/list` 按角色过滤，`prompts/get` 再授权。

### 5.6 身份、Workspace、Origin 与 Prompt Injection 边界

- HTTP actor 只来自有效 Bearer；stdio actor/workspace 只来自启动配置。模型参数、Resource URI、Prompt 参数均不能覆盖。
- 对每个 capability 调用 `WorkspaceAccessService.require`，资源查询同时使用复合 `workspace_id` 条件和 owner/requester/assignee 条件。
- RAG/灵感内容视为不可信字符串；其中“忽略规则、调用工具、批准待办”等内容不得改变路由、权限或触发副作用。
- Tool 输出按数据处理，不拼接到系统/开发者指令区；引用正文长度受现有 Schema 限制。
- 不实现通用 URL fetch、文件路径、shell、SQL、Milvus expression、任意 Prompt 执行；`source_url` 也不由 MCP Server 抓取。
- 工具描述必须声明副作用和审批边界，模型的“确认”不是人类审批事实。

## 6. 评估契约：数据 Schema、语料快照、指标、对照组、阈值、成本和原始报告

### 6.1 数据与快照

实施时新增版本化测试资产（文件名仅为契约，不代表已存在）：

- `eval/p9_mcp/protocol_cases.jsonl`：transport、protocol version、method、capability、arguments、expected result/error。
- `eval/p9_mcp/security_cases.jsonl`：actor、workspace、role/status、Origin、攻击字段、expected deny/visibility。
- `eval/p9_mcp/tool_cases.jsonl`：知识/灵感/Todo 输入、预置事实 ID、期望 citations/refusal/HITL 状态。
- `eval/p9_mcp/raw/<timestamp>-<git_sha>/`：原始 JSON-RPC transcript（token/正文脱敏）、服务日志、pytest JUnit、配置快照、SDK/Python/MySQL 版本。

```json
{
  "case_id": "mcp-sec-cross-workspace-001",
  "transport": "streamable_http",
  "protocol_version": "2025-11-25",
  "actor_fixture": "viewer_a",
  "bound_workspace": "workspace_a",
  "method": "tools/call",
  "name": "knowledge_search",
  "arguments": {"query": "fixture marker", "top_k": 5},
  "expected": {"outcome": "deny_or_zero_leak", "code": "RESOURCE_NOT_FOUND"},
  "corpus_snapshot": "p9-fixture-v1",
  "seed": 20260810
}
```

知识语料必须使用固定 P4/P7 published snapshot，包含：同名跨 Workspace 文档、草稿/失败/旧版本、Prompt injection 文本、可回答/不可回答样本。灵感 fixture 包含跨 Workspace、软删除和跨 Workspace Tag。

### 6.2 指标、阈值与对照组

| 维度 | 指标/阈值 | 对照 |
|---|---|---|
| 协议 | 两种 transport 的 initialize/list/call/read/get 合规用例 100%；Schema validation 100% | 直接 Python 函数调用不是通过条件 |
| 安全 | 未授权写入、跨租户泄露、非法 Origin 放行、token/正文日志泄露均为 0 | 旧 stdio 固定用户实现仅用于证明差距，不作为可接受基线 |
| HITL | 审批前 Todo 创建率 0；重复/并发审批重复 Todo 0；过期/篡改参数成功率 0 | P8 REST/HITL 现有测试 |
| 功能 | Tool 选择、结构化输出、Resource/Prompt 名称和错误映射契约用例 100% | REST Service 直接结果做语义对照 |
| RAG | MCP 与同 payload REST 的 answer/refusal/citation IDs 一致率 100%（忽略 request ID/timing） | 现有 Basic RAG REST |
| 可靠性 | HTTP 断连后相同 idempotency key 返回同 Run；stdio restart 后业务状态可查，100% | 无重试路径 |
| 性能 | 记录 protocol overhead、Tool service latency、P50/P95、超时率；本期不预设未测数值 | REST 直接调用延迟，计算 MCP 增量 |

### 6.3 成本与报告

- 协议/灵感/状态/Todo adapter 不增加模型 token；记录 CPU 时间和 DB query count（若测试设施可得）。
- `knowledge_search` 记录现有 provider/profile、embedding/reranker/LLM 模型、token/成本（Provider 可提供时）、top_k、candidate_k、timings 和 degraded；不可得字段写 `null + reason`，不得估算。
- 原始报告记录 git SHA、协议/SDK、Prompt/Schema version、语料 snapshot、MySQL 版本、机器、日期、时区、随机 seed、并发、P50/P95、失败明细。
- P9 报告是阶段验收证据，不替代 P10 100 题正式评估与简历数字。

## 7. 前后端任务清单、P0-P10 依赖、阶段门禁和测试清单

### 7.1 可分批实施顺序

#### B1：契约冻结与 Server 骨架

- Backend：建立 P9 独立 registry、`McpCallContext`、配置项与新 entrypoint；锁定/约束 SDK 1.28.1 和协议 2025-11-25。
- Backend：实现 stdio context loader，凭据不出现在 Tool Schema；stdout/stderr 隔离。
- Backend：挂载 stateless/json-response Streamable HTTP，完成 initialize/initialized 与 protocol version 检查。
- Gate：真实 Client 能 initialize；无业务 Tool；旧 Server 不受影响，可 feature flag 回滚。

#### B2：认证、Workspace 与传输安全

- Backend：HTTP Bearer adapter、每请求 User/membership recheck、path Workspace 绑定。
- Backend：Origin/Host allowlist、body limit、deadline、结构化 request ID 与 error mapper。
- Backend：实现角色过滤的 list 与 call/read/get 二次授权。
- Gate：未认证/非法 Origin/跨 Workspace/降权/停用矩阵全通过，才允许接入业务 Tool。

#### B3：只读能力

- Backend：接入 `knowledge_search`、`inspiration_search`、`agent_run_get`、`approval_get`、`todo_list/get`；不得复制 Service 查询。
- Backend：接入 Resources 与 `knowledge_qa` Prompt；输出严格 Pydantic 化。
- Gate：stdio/HTTP 与 REST 语义对照、发布版本/拒答/跨租户/软删除测试通过。

#### B4：Todo HITL

- Backend：实现 `todo_create` P8 adapter、幂等、Run/Dispatch/Checkpoint/Approval 返回与轮询。
- Backend：显式禁止 approval decision Tool，补 R2 持久审计。
- Frontend：原则上无需新页面；只检查现有审批卡片能显示 MCP 来源 Run、参数 hash、过期和状态。若缺失，仅在契约确认后做最小兼容展示。
- Gate：审批前无 Todo、篡改/过期/降权拒绝、批准后唯一创建、重启恢复、重复调用收敛。

#### B5：限流、观测、回归与文档

- Backend：Redis token bucket + 本地有界 fallback、方法/能力级结构化日志与指标、敏感字段 redaction。
- Backend：协议 fixture、stdio/HTTP 端到端、并发/超时/断连测试，更新 `.env.example` 和使用说明。
- Reviewer：只读安全与契约审查；S1 清零、S2 不超过 2。
- 主 Agent：运行集成验证并保存 raw report；确认 feature flag 默认值和 legacy 下线节奏。

### 7.2 Frontend / Backend / Reviewer 原子任务

| ID | 负责人 | 原子任务 | 依赖 | 验证 |
|---|---|---|---|---|
| BE-01 | Backend | P9 配置、registry、context、Schema、entrypoint | B1 | unit + mypy/ruff |
| BE-02 | Backend | stdio env identity 与 stdout 纯净 | BE-01 | ClientSession stdio transcript |
| BE-03 | Backend | FastAPI Streamable HTTP mount 与 lifecycle | BE-01 | initialize/initialized |
| BE-04 | Backend | Bearer、Workspace、Origin/Host、body/protocol guard | BE-03 | 401/403/404/400 matrix |
| BE-05 | Backend | MCP error mapper 与 redaction | BE-03 | snapshot tests |
| BE-06 | Backend | 角色过滤 list + call 二次授权 | BE-04 | role downgrade concurrent test |
| BE-07 | Backend | Knowledge/Inspirations 只读 adapters | BE-06 | REST semantic parity |
| BE-08 | Backend | Run/Approval/Todo 只读 tools/resources | BE-06 | ownership/cross-tenant tests |
| BE-09 | Backend | Prompts | BE-06 | prompt list/get RBAC |
| BE-10 | Backend | Todo P8 HITL adapter + idempotency/audit | BE-06, P8 gate | HITL/concurrency/restart suite |
| BE-11 | Backend | limiter、deadline、metrics/logs | BE-07..10 | timeout/429/redaction tests |
| BE-12 | Backend | docs、`.env.example`、client configs | BE-11 | cold-start smoke |
| FE-01 | Frontend | 审批卡片兼容性检查，无缺口则零代码 | BE-10 | existing approval flow |
| FE-02 | Frontend | 如获确认，展示“来自 MCP”与轮询/SSE状态 | FE-01 | type/lint/unit/build |
| RV-01 | Reviewer | 传输/Schema/REST 语义一致性审查 | B3 | report only |
| RV-02 | Reviewer | RBAC/Origin/injection/HITL/日志审查 | B4/B5 | S1/S2 report |
| MA-01 | 主 Agent | 两种 transport 集成验收与 raw report | 全部 | gate checklist |

### 7.3 P0-P10 依赖

- 继承 P0 契约、P1 权限、P3/P4 摄取检索、P7 证据门禁、P8 Agent/HITL。
- P5/P6 未完成时，P9 只暴露已发布 Basic RAG，GraphRAG capability 不注册。
- P9 完成后 P10 才做正式协议压测、100 题评估、成本与演示材料；P10 数字不得回填为 P9 预设成果。

### 7.4 测试清单与阶段门禁

#### 单元/Schema

- 所有 Input/Output `extra=forbid`、长度/UUID/timezone/enum/cursor/idempotency key 边界。
- Tool annotation、名称、description 与 role/risk registry 一致。
- AppError → MCP error code 映射与 redaction。
- Origin 精确匹配、大小写/默认端口、Host、空/重复 Header、协议版本。

#### 协议集成

- stdio 与 HTTP：initialize、initialized、ping、tools/list/call、resources/list/templates/read、prompts/list/get。
- 不支持版本、初始化前调用、未知 Tool、malformed JSON-RPC、错误 content type/Accept、HTTP GET/DELETE 预期 405。
- stdio stdout 逐行解析为合法 JSON-RPC，故意产生日志/异常仍无污染。

#### 身份/RBAC/租户

- 无 token、坏 token、过期 token、停用用户；无成员、INVITED/SUSPENDED/REMOVED；VIEWER 写入。
- URL Workspace 与资源 Workspace 错配、跨 Workspace ID/Tag/Run/Approval/Todo、枚举不存在资源。
- list 后降权/移除，再 call 必须拒绝；同一 HTTP 逻辑 Session 更换 token 不继承旧身份。
- Tool arguments 注入 `workspace_id/actor_id/local_token/role/approval_status/raw_filter` 全部拒绝。

#### 业务与安全

- RAG 发布/旧版/失败/草稿可见性、citations、拒答、Milvus/MinIO 降级。
- 文档/灵感中的 prompt injection 不触发 Tool、不改变 RBAC、不产生 Approval/Todo。
- 灵感软删除和跨 Workspace Tag 不可见。
- Todo 成功提议、无明确时区、他人 assignee、重复 key、冲突 key、并发 key、Worker 慢/失败、HTTP 断连。
- 审批前/拒绝/过期/参数篡改/降权均无 Todo；批准、重复批准、恢复后只一个 Todo。
- 确认 MCP capability list 中没有 `approval_decide` 和旧危险写 Tool。

#### 质量与回归

- Backend ruff、mypy、全量 pytest；Alembic current/check/upgrade head（即使零新迁移）。
- FastAPI 健康检查、现有 REST、P8 SSE `Last-Event-ID` 回放、P8 Worker 回归。
- 前端仅在发生改动时执行 lint/type/unit/build；零改动也需人工走审批卡片。
- 记录未验证外部客户端及原因；至少用官方 Python MCP Client 完成两种 transport 自动化。

#### P9 完成门禁

- B1–B5 全部通过；S1=0，S2≤2 且有 owner/期限；核心安全用例不得以 S2 遗留。
- 旧 stdio 兼容/默认开关和回滚演练完成。
- raw report 包含真实命令、exit code、版本与失败明细。
- Docker/Nginx/生产 OAuth 未完成必须继续标注非目标，不能因此声称“生产可用”。

## 8. 风险、开放问题与需要主 Agent 确认的决策

### 8.1 主要风险

| 风险 | 等级 | 缓解/回滚 |
|---|---|---|
| 当前依赖范围 `<2` 可能安装不同 1.x，协议行为漂移 | S1 潜在 | 锁定/约束 SDK 与协议集成测试；升级单独评审 |
| 旧 MCP Tool 暴露 token 参数且可直接写灵感/标签 | S1 若挂公网 | 旧 Server 永不挂 HTTP、默认关闭、P9 独立 allowlist |
| MCP Todo adapter 直接插库或暴露审批 Tool | S1 | 代码审查禁止 Repository Todo 写；只复用 P8 HITL；负向测试 |
| Session 误当身份缓存，降权后仍可调用 | S1 | 每请求/每调用查 MySQL；降权竞态测试 |
| Resource/Tool ID 跨租户枚举 | S1 | Workspace 复合条件+owner 条件，未知统一 404 |
| 检索内容 prompt injection 诱导写操作 | S1/S2 | 数据/指令分离、Tool 无链式自动执行、HITL 不可模型批准 |
| stateless HTTP 不提供长连接通知，客户端期待 SSE | S2 | 明确 JSON response/轮询；业务进度用 P8 REST SSE；未来另评估 |
| HTTP 首版非完整 OAuth 2.1，部分客户端无法配置 Bearer | S2 | 本地优先 stdio；记录客户端兼容矩阵；生产 OAuth 后续阶段 |
| 限流 Redis 失败导致节点级额度不一致 | S2 | 开发本地 fallback + degraded；生产部署门禁另要求网关/Redis |
| 只读 Tool 输出正文导致日志或错误回显泄露 | S1 | 默认不记录 arguments/content；redaction tests；request ID 关联 |

### 8.2 需要主 Agent 确认的决策

1. **协议冻结**：批准 P9 v1 固定 `2025-11-25 + mcp 1.28.1`，将 2026-07-28 无 initialize 协议作为后续迁移，而不是本期双栈。
2. **HTTP 身份范围**：批准首版复用现有 JWT Bearer、明确非完整 OAuth 2.1；若目标客户端必须自动 OAuth discovery，则需扩大范围并先规划授权服务器。
3. **HTTP 形态**：批准 `stateless_http=true + json_response=true`，不承诺 MCP SSE/Session 恢复；P8 业务进度继续 REST SSE/Tool 轮询。
4. **Endpoint**：确认 `/api/v1/workspaces/{workspace_id}/mcp` 作为可信 Workspace 绑定方式；若改为 `/mcp` + Header，需另行定义 Header 信任和客户端配置。
5. **工具范围**：确认 P9 allowlist 为 7 个 Tool（知识、灵感、Todo 提议、Run、Approval、Todo list/get），旧灵感/标签写 Tool 不迁入。
6. **审批边界**：确认永不注册 `approval_decide` MCP Tool，哪怕当前用户是 OWNER；审批只在现有 Vue/REST 人类交互中完成。
7. **旧 Server 生命周期**：确认新旧 entrypoint 并行一个兼容版本，P9 默认启用前先完成 client config；旧 Server 不允许 HTTP。
8. **前端范围**：确认优先零前端改动，仅当现有审批卡片不能辨认 MCP 来源/状态时追加最小展示。
9. **限流降级**：确认无 Redis 的本地开发允许进程内有界限流并标记 degraded；生产限流与网关属于部署后续门禁。

主 Agent 确认 1–7 后可冻结本契约并进入 B1；若第 2 项改为完整 OAuth，第 1、4、测试与实施规模都必须重新评估。
