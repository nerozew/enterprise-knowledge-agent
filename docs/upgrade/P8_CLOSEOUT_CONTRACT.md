# P8 单 Agent 垂直切片收口契约

状态：`PROPOSED_FOR_CLOSEOUT`

契约版本：`p8-agent-closeout-v1`

基线：`P8_AGENT_MVP_CONTRACT.md` 的增补契约；如两者冲突，以本文对调度、Checkpoint、审批发布顺序和断线恢复的规定为准。本文描述待实现与待验证内容，不代表 P8 已完成。

## 1. 目标、用户故事、非目标与验收标准

### 1.1 阶段、前置门禁与目标

本次只收口 P8 已批准的单 Agent 垂直切片。前置条件为 P0 契约、P1 Workspace/RBAC/审计、当前可用的 Basic RAG，以及 `0011_p8_agent_dispatch` 迁移可执行。它不豁免 P5-P7 的正式阶段门禁，也不得用于宣称完整 P8 或 P10 已通过。

唯一业务闭环为：

```text
用户发消息
  -> REST 原子创建 Run、用户消息、run.created、Dispatch(PENDING)
  -> Dispatch Worker 租约领取并执行四路由 LangGraph
  -> TODO_CREATE 产生确定性待办提案
  -> 准备 Approval（尚不对外发布）
  -> LangGraph interrupt 以 durability=sync 保存 Checkpoint
  -> 原子绑定 Run/Approval/Checkpoint，切换 WAITING_APPROVAL，写入审批事件
  -> SSE 发出 tool.proposed、approval.required
  -> 用户批准
  -> 从绑定 Checkpoint 恢复 LangGraph
  -> 重新鉴权、验哈希，以来源唯一约束幂等创建 Todo
  -> 原子写入审批、Todo、Run 终态和完成事件
  -> SSE 发出 approval.resolved、todo.created、tool.completed、run.completed
```

### 1.2 用户故事

- Workspace 成员发送消息后立即得到 `202 OperationAccepted`，请求线程不执行模型或检索；刷新、API 进程重启或 Redis 丢失后，Worker 仍能从 MySQL 继续。
- EDITOR 发出创建待办意图后，先看到服务器规范化且哈希固定的审批卡；未经批准没有 Todo 副作用。
- 审批人在刷新、断网或服务重启后，可从 Run 快照和 SSE 游标恢复审批；批准只产生一个 Todo。
- 被撤销 Workspace 成员资格的用户不能继续收到业务事件、心跳或执行审批；降级为 VIEWER 后只能查看自己已有的审批，不能决策。
- Worker 崩溃、租约过期、重复领取、审批请求重放和响应丢失都不会重复消息、审批、Todo 或终态事件。

### 1.3 非目标与隐私边界

- 不扩展 GraphRAG Local/Global、长期记忆、Multi-Agent、MCP、外部第三方写操作或后台主动任务。
- 不把 Agent 工作台改造成任务管理后台；待办仅作为对话内批准动作的结果和轻量列表展示。
- Checkpoint 仅保存本会话恢复所需的最多 12 条短期消息；不得保存其他 Workspace、其他会话正文、访问令牌、模型思维链或完整检索语料。
- `workspace_id`、`actor_id`、`conversation_id`、`run_id` 只能来自认证事实和数据库事实，模型输出不得覆盖。
- 检索文本是不可信数据，不得改变路由权限、审批要求或 Tool 参数的服务端校验。

### 1.4 硬性验收标准

1. 从创建 Run 到 `WAITING_APPROVAL`，以及从批准到 `COMPLETED` 的真实 MySQL E2E 通过；事件顺序严格符合本文第 4 节。
2. `approval.required` 可见时，Approval 和 Run 必须已经绑定到同一条存在且可反序列化、包含匹配 interrupt 的 Checkpoint；不存在“审批可见但 Checkpoint 尚未就绪”窗口。
3. Worker 在模型调用前、Checkpoint 保存后、审批发布前、业务终态提交后分别被强杀，恢复后均达到唯一自然终态。
4. 同一审批并发批准 20 次、Worker 租约重放和 HTTP 响应丢失后重试，`todos`、`approval_requests` 和各业务事件的逻辑实例数均为 1。
5. 跨 Workspace、非 Run 请求者、VIEWER 写入、哈希篡改、Checkpoint 错绑和未批准写入均为 0。
6. SSE 持久化业务事件序号连续率为 100%；`Last-Event-ID` 重放不重不漏，历史不可用返回 410 并可由快照恢复。
7. 撤权后下一次授权检查不再查询或发送事件，且不再发送 heartbeat；前端确定性停止重连并清理旧 Workspace 请求。
8. 后端测试、迁移 upgrade/downgrade/upgrade、真实 MySQL 并发测试，前端 lint、类型检查、单测、构建和真实浏览器 REST/SSE E2E 均通过。未执行项必须明确记录。

## 2. 领域模型、角色矩阵、状态机、MySQL 表结构、索引、兼容迁移与回滚策略

### 2.1 领域事实与不变量

- MySQL 是 Conversation、Message、Run、Dispatch、Approval、Checkpoint、Event、Todo、幂等记录和审计的唯一业务事实源。
- 一个 Conversation 同时最多有一个非终态 Run。创建 Run 时锁定 Conversation 并在同一事务检查；不得依赖前端防重。
- 一个 Run 只有一个 Dispatch；一个 `(workspace_id, run_id, tool_call_id)` 只有一个 Approval；一个 Approval/Tool Call 只有一个 Todo。
- `approval.required` 不是命令，而是已落库且已绑定可恢复 Checkpoint 的事实通知。
- 任何 Worker 在提交业务结果前，必须校验自己仍持有未过期租约；失去租约的计算结果直接丢弃。
- Run、Approval、Todo 使用版本 CAS。事件只在导致该事件的事实状态迁移事务内追加。

### 2.2 角色矩阵

| 能力 | VIEWER | EDITOR | ADMIN/OWNER | 附加范围 |
|---|---:|---:|---:|---|
| 创建/读取自己的 Conversation、Run、SSE | 是 | 是 | 是 | 必须仍是 Workspace 成员且 `requested_by_user_id=actor_id` |
| 执行只读路由 | 是 | 是 | 是 | Tool 再次校验 Workspace |
| 查看自己的审批卡 | 是 | 是 | 是 | 仅 Run 请求者；不存在代审批 |
| 创建待办提案 | 否 | 是 | 是 | Worker 发布审批前再次检查角色 |
| 批准/拒绝自己的审批 | 否 | 是 | 是 | 决策和 Tool 执行前各重新鉴权 |
| 读取自己的 Todo | 是 | 是 | 是 | 仅创建者或受理人 |

VIEWER 请求被路由为 `TODO_CREATE` 时不得创建 Approval 或 Todo；Run 进入 `FAILED`，`error_code=permission_denied`，并写 `run.failed`。已创建审批的用户被降级为 VIEWER 后，审批保持 `PENDING` 供只读查看，但决策返回 403。

### 2.3 状态机

Run 状态机：

```text
（创建事务） -> RUNNING
RUNNING -> WAITING_APPROVAL       仅 Checkpoint 已保存并绑定、审批发布成功
RUNNING -> COMPLETED              三个只读/回答路由自然完成，或 TODO 参数需澄清
RUNNING -> FAILED                 不可恢复错误或重试耗尽
RUNNING -> CANCELLED              用户取消
WAITING_APPROVAL -> COMPLETED     批准、恢复 Checkpoint、Todo 提交成功
WAITING_APPROVAL -> CANCELLED     拒绝、过期或用户取消
WAITING_APPROVAL -> FAILED        Checkpoint 永久损坏且经过显式失败补偿
```

`CREATED` 保留为兼容枚举，不在新建路径暴露。终态不可逆。

Dispatch 状态机：

```text
PENDING -> PROCESSING             成功领取租约，attempts += 1
PROCESSING -> SUCCEEDED           Run 已 WAITING_APPROVAL 或已达任一终态
PROCESSING -> PENDING             可重试错误，设置下一 available_at
PROCESSING -> FAILED              永久错误或 attempts 达上限；同时使非终态 Run FAILED
PROCESSING -(lease timeout)-> PROCESSING  新 claim token 接管，attempts += 1
```

Approval 状态机：

```text
PENDING(unbound, internal) -> PENDING(bound, externally visible)
PENDING(bound) -> APPROVED | REJECTED | EXPIRED
```

`PENDING(unbound)` 不出现在 Approval 列表、Run 快照或 SSE 中。相同决定允许事实级重放；相反决定返回 409。批准不得早于 Checkpoint 恢复校验，Approval 的终态、Todo 和 Run 终态在同一事务提交。

### 2.4 事务边界

| 边界 | 同一 MySQL 事务内必须完成 | 事务外动作与恢复规则 |
|---|---|---|
| T1 接收消息 | 锁 Conversation；检查版本/活动 Run；插入 Run、用户 Message、Dispatch、`run.created`、幂等响应、审计；更新 Conversation | 提交后可选 Redis 唤醒；通知丢失由 MySQL 轮询恢复 |
| T2 Claim | `SELECT ... FOR UPDATE SKIP LOCKED`；写 `PROCESSING`、claim token、租约、attempts | 提交后执行 LangGraph；模型调用期间不得持有 DB 锁 |
| T3 路由/提案落盘 | 校验当前 claim；锁 Run；首次写 route/reason；追加唯一的 `route.selected`；TODO 路由插入或读取确定性 Approval 草稿 | 重试只读取已落盘提案，不再次改变已批准参数 |
| C1 Checkpoint | Checkpointer 以 `durability="sync"` 保存 interrupt Checkpoint 和 pending writes | 不与 T3/T4 假装原子；失败时 Approval 仍为内部草稿，Worker 重试 |
| T4 发布审批 | 校验 claim；确认 Checkpoint 可读取且 interrupt 四元组匹配；按顺序更新 Run 绑定、Approval 绑定；Run 转 `WAITING_APPROVAL`；写 `tool.proposed`、`approval.required` | 提交后 SSE/Redis 通知；崩溃前未提交则完全不可见，崩溃后重放为只读 |
| T5 决策预检 | 读取认证事实；按 Run 后 Approval 顺序加锁；验证版本、状态、有效期、哈希、Checkpoint 绑定 | 不持锁执行 LangGraph；任何失败不得改变 Approval |
| C2 Resume | 从精确 Checkpoint 调用 `Command(resume=...)`，`durability="sync"` | 节点在副作用前再次执行 T6 鉴权和验参 |
| T6 批准终态 | 锁 Run 后 Approval；复验角色、状态、版本、哈希和 Checkpoint；CAS Approval；插入或读取唯一 Todo；CAS Run；追加完成事件和审计 | 提交后构造 REST 响应；若响应/幂等记录丢失，从领域事实重建同一响应 |
| T7 Dispatch 收尾 | 以 claim token CAS 为 `SUCCEEDED`，或重排/失败 | 若业务状态已 WAITING/终态，重领 Worker 只做收尾，不重复执行 |

拒绝/过期不恢复写 Tool：同一事务将 Approval 改为 `REJECTED/EXPIRED`、Run 改为 `CANCELLED`，写 `approval.resolved`、`run.cancelled` 和审计。

### 2.5 表、字段、约束与索引

沿用 `0008` 至 `0011` 表结构，收口实现不得另建平行业务事实表：

| 表 | 本闭环使用的关键字段 | 必须保持的约束/索引 |
|---|---|---|
| `conversations` | `workspace_id,id,created_by_user_id,status,version,last_message_at` | `uq_conversations_workspace_id`；`ix_conversations_workspace_updated` |
| `messages` | `workspace_id,conversation_id,run_id,role,content,sequence_no` | `(workspace_id,conversation_id,sequence_no)` 唯一；Run 终态事务最多插入一条 ASSISTANT 结果 |
| `agent_runs` | `workspace_id,id,conversation_id,status,route,reason_code,graph_version,checkpoint_ns,checkpoint_id,version` | Workspace/Conversation 复合外键；Checkpoint 成对非空；Checkpoint 复合外键；状态/version 检查 |
| `agent_run_dispatches` | `workspace_id,run_id,status,attempts,available_at,lease_owner,lease_expires_at,last_error_code` | `(workspace_id,run_id)` 唯一；`ix_agent_dispatch_due(status,available_at,lease_expires_at)` |
| `approval_requests` | `workspace_id,id,run_id,tool_call_id,arguments_json,arguments_hash,checkpoint_ns,checkpoint_id,status,version,expires_at` | `(workspace_id,run_id,tool_call_id)` 唯一；与 Run Checkpoint 的复合外键；状态/version 检查 |
| `agent_checkpoints` | `workspace_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,*_blob` | 四列主键；Workspace/Conversation 外键；scope/created 索引 |
| `agent_checkpoint_writes` | Checkpoint 四元组、`task_id,write_idx,channel,value_blob` | 复合主键；scope 与孤儿清理索引 |
| `agent_run_events` | `workspace_id,run_id,sequence_no,event_type,payload_json` | `(workspace_id,run_id,sequence_no)` 唯一；在锁 Run 后分配连续序号 |
| `todos` | `workspace_id,id,source_run_id,source_tool_call_id,approval_id,title,description,due_at,assignee_user_id,status,version` | 来源 Tool Call 唯一；Approval 唯一；Workspace 复合外键；状态/受理人索引 |
| `idempotency_records` / `audit_logs` | 既有通用字段 | 所有写 REST 沿用 7 天幂等记录；审计不保存正文或完整参数 |

逻辑事件去重不新增 `dedupe_key`：必须由 Run/Approval 状态迁移、行锁和上述唯一约束保证只有状态迁移赢家追加事件。若实现无法证明这一点，需先提出新的 Alembic 增量迁移，不得使用“先查后插”替代唯一约束。

### 2.6 Dispatch 租约与重试

- `lease_owner` 存 claim token，格式为 `<worker-instance-id>:<uuid4>`，每次领取必须变化；不能只存固定进程名。
- 默认租约 30 秒，每 10 秒续租；续租与业务提交都以 `id + status=PROCESSING + lease_owner=claim_token + lease_expires_at>now` CAS。
- 默认最大 5 次 attempts。可重试错误使用指数退避 `min(2^(attempts-1)*2s, 60s)` 加 0%-25% jitter；错误码写 `last_error_code`，不得写隐私正文。
- 网络超时、数据库死锁/连接中断、模型 429/5xx 是可重试错误；权限拒绝、非法状态、哈希/Checkpoint 绑定冲突和结构损坏是永久错误。模型结构化输出不合法由图安全终止，不作为 Worker 重试。
- 领取查询仅处理到期的 `PENDING` 或租约已过期的 `PROCESSING`，使用数据库 UTC 时间与 `SKIP LOCKED`。SQLite 单测不能替代真实 MySQL 领取测试。
- Worker 启动和每轮空闲时轮询 MySQL；Redis 只缩短唤醒延迟。Redis 故障不得阻塞领取。

### 2.7 Checkpointer、`thread_id` 与审批哈希

- LangGraph `thread_id = str(conversation_id)`；所有查询还必须带受信 `workspace_id`，仅凭 `thread_id` 不足以定位 Checkpoint。
- 新写 Namespace 固定为 `p8-agent-mvp-v1:<todo-hitl-graph-version>:todo:<run_id>`。`graph_version` 必须同时写入 Run；不同图版本禁止恢复。
- 恢复必须使用 Run 和 Approval 已绑定的精确 `checkpoint_ns + checkpoint_id`，不得扫描“最新 Checkpoint”作为正常路径。扫描旧 Namespace 只允许在第 2.8 节兼容窗口内执行，并要求唯一匹配。
- interrupt 至少包含 `approval_id,run_id,tool_call_id,tool_name,arguments_hash,approval_version,expires_at`；恢复时逐项匹配数据库事实。
- Todo 参数先以 Pydantic `extra=forbid` 校验，再规范化：字符串去首尾空白；`due_at` 转 UTC RFC3339；缺省 `assignee_id` 固化为当前 actor；对象固定含 `title,description,due_at,assignee_id` 四键。以 UTF-8、键排序、无多余空白、保留实际中文的 JSON 计算 SHA-256 小写十六进制哈希。
- `tool_call_id = UUIDv5(run_id, "todo_create:" + arguments_hash)`。批准请求必须回传服务端 `tool_args_hash`；服务端对请求、Approval JSON、Checkpoint proposal 三方重新计算并恒等比较。
- HTTP `Idempotency-Key` 负责响应重放；`todos` 的两个唯一约束负责领域副作用幂等。两者不能互相替代。

### 2.8 兼容迁移与回滚

- `0011_p8_agent_dispatch` 必须保持为 `0010` 的单一后继，MySQL 表使用 `utf8mb4`；先修复仍断言 `0010` head 的测试。
- 本契约默认不要求新增表列。部署顺序为：先升级数据库至 `0011`，再部署能写 Dispatch 的 API，最后启动 Worker；旧 API 不认识新表时不得与新 API 混跑写请求。
- 兼容一个发布窗口读取现有 `TODO_CHECKPOINT_NAMESPACE` 和当前带 `run_id` Namespace，但只在 `workspace_id,conversation_id,run_id,approval_id,tool_call_id,arguments_hash` 唯一匹配时恢复；新 Checkpoint 一律写新 Namespace。窗口结束后删除兼容读取代码前，先确认不存在未决旧审批。
- 应用回滚时先停 Worker，再回滚 API。只要库保留 `0011`，PENDING/PROCESSING Dispatch 可由恢复后的新版本继续；不得在存在非终态 Run 时 downgrade `0011`。
- 允许 downgrade 的门禁：所有 Dispatch 为 `SUCCEEDED/FAILED`、所有 Run 为终态或已有人工处置记录、无待恢复审批，并完成备份。随后才可执行 Alembic downgrade 删除 Dispatch 表；Conversation、Run、Approval、Checkpoint、Todo 事实不删除。
- P8 领域对象不做硬删除；Conversation 使用 `ARCHIVED`，事件和审计按既有留存策略。Checkpoint 清理只能删除未绑定且超过 24 小时的孤儿 writes；绑定到非终态 Run/Approval 的 Checkpoint 禁止清理。

## 3. Redis、MinIO、Milvus 和协同服务的用途、可靠性与降级

| 组件 | 本闭环用途 | 可靠性、幂等与降级 |
|---|---|---|
| MySQL 8 | 全部业务事实、Dispatch、Checkpoint、事件回放 | 唯一事实源；事务只覆盖 MySQL 内表；统一 `utf8mb4` |
| Redis | 可选的 Dispatch 唤醒、SSE 新事件提示和短 TTL 限流 | Key 必须含 Workspace/Run；通知可丢、可重复；TTL 建议 60 秒；MySQL 轮询兜底，不保存唯一游标、审批或锁事实 |
| MinIO | 本闭环无新增用途 | 不读写；故障不影响 DIRECT/INSPIRATION/TODO。KNOWLEDGE_QA 沿用既有 RAG 降级契约 |
| Milvus | 仅现有 Basic RAG Dense 检索 | 不是 Run/引用事实源；失败按现有可信 RAG 拒答/失败处理，不扩展 GraphRAG |
| 协同服务 | 无新增用途 | Yjs/Hocuspocus 草稿不得进入 Agent Checkpoint 或检索；故障不影响本闭环 |

不存在 MySQL/Redis/MinIO/Milvus 跨存储原子事务。MySQL 事务提交后才发送 Redis 通知；通知失败仅记录脱敏指标并由轮询补偿。

## 4. REST/SSE/协同契约：认证、请求、响应、事件、错误码、幂等、并发和断线恢复

### 4.1 REST 兼容边界

沿用现有路径和 JSON 字段，不删除、不改名、不改变类型：

1. `POST /api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs`
   - Bearer 认证；`Idempotency-Key` 必填；请求沿用 `conversation_version,message,knowledge_base_ids,retrieval_strategy,budget`。
   - `LOCAL/GLOBAL` 继续返回 422 `retrieval_strategy_not_available`，不得借收口启用 GraphRAG。
   - 成功仍返回 202 `OperationAccepted`，`status` 保持兼容值 `RUNNING`。返回前 T1 已提交，绝不内联执行 LangGraph。
2. `GET /api/v1/workspaces/{workspace_id}/agent-runs/{run_id}`
   - 返回既有 `AgentRunSnapshot`；仅绑定后的 Approval 才进入 `tool_proposal/approval`。
   - `last_event_id` 为十进制 `last_sequence_no` 的字符串；无事件时为 null/0。
3. `POST /api/v1/workspaces/{workspace_id}/approvals/{approval_id}/decision`
   - `Idempotency-Key` 必填；`If-Match` 必须与 body `version` 相同；body 保持 `decision,version,tool_args_hash,comment?`。
   - 保持同步 200 语义：`APPROVED` 只有在 Checkpoint 已恢复且 Todo/Run 完成事务提交后返回，`todo_id` 必须非空；`REJECTED` 返回 terminal Run 且 `todo_id=null`。
   - 相同幂等键和请求返回字节语义相同的领域响应；键相同而请求不同返回 409 `idempotency_conflict`。领域事实已提交但幂等记录缺失时，应由事实恢复成功响应。

前端可继续忽略新增响应字段；本闭环不新增必填请求字段。`OperationAccepted.status='RUNNING'` 与 Run 快照事实状态分开解释。

### 4.2 SSE 契约与事件顺序

路径保持 `GET /api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/events`，请求头为 `Authorization: Bearer ...`、`Accept: text/event-stream` 和可选 `Last-Event-ID`。

业务帧：

```text
id: <sequence_no>
event: <event_type>
data: {"run_id":"...","seq":<sequence_no>,"timestamp":"...","payload":{...}}
```

heartbeat 不带 `id` 和 `seq`，不能推进游标。TODO 成功主路径的事件顺序和最小 payload 为：

| 顺序 | event | 最小 payload |
|---:|---|---|
| 1 | `run.created` | `conversation_id,status,retrieval_strategy,knowledge_base_ids,budget` |
| 2 | `route.selected` | `route,reason_code` |
| 3 | `tool.proposed` | `tool_call_id,tool_name,arguments,tool_args_hash,requires_approval` |
| 4 | `approval.required` | `approval_id,tool_call_id,status,version,expires_at` |
| 5 | `approval.resolved` | `approval_id,decision,resolved_by_user_id,resolved_at` |
| 6 | `tool.started` | `tool_call_id,tool_name` |
| 7 | `todo.created` | `todo_id,approval_id,tool_call_id` |
| 8 | `tool.completed` | `tool_call_id,tool_name,todo_id` |
| 9 | `run.completed` | `outcome,todo_id` |

序号以每个 Run 从 1 连续递增。`tool.proposed` 和 `approval.required` 在同一事务；5-9 在 T6 同一事务，因此客户端不会看到批准但看不到 Todo 的中间事实。客户端仍必须以快照为事实，不依据事件自行伪造资源。

### 4.3 SSE 鉴权、撤权与回放

- 建连时验证 Token、Workspace 成员资格和 Run 请求者；跨租户或非请求者统一 404，未认证为 401。
- 长连接每次查询事件前都在新 DB 会话重新检查成员资格和 Run 所有权；heartbeat 前也重新检查。检查失败立即结束流，不再发送业务帧或 heartbeat。
- Token 到期时关闭流并返回/触发 401 语义；前端只能在取得有效 Token 后重连，不得无限重试 401/403/404/409/422。
- `Last-Event-ID` 必须是非负整数。服务端只返回 `sequence_no > cursor`；cursor 超出上界返回 422 `invalid_last_event_id`。
- 事件被清理、序号有洞或 payload 标记过期时返回 410 `event_history_expired`，details 保持 `run_id,snapshot_url,earliest_available_event_id,latest_event_id`。前端读取快照，以 `last_event_id/last_sequence_no` 重建游标；非终态才重新连接。
- 可重试仅限网络错误、429 和显式标记 retryable 的 5xx，采用有上限指数退避；终态事件、撤权和确定性 4xx 必须停止重连。Workspace 切换必须 abort 旧 SSE 及旧写/读请求，并以 generation 丢弃迟到响应。

### 4.4 错误码与并发语义

| HTTP | code | 语义/客户端动作 |
|---:|---|---|
| 401 | `not_authenticated` | 清理登录态，停止流 |
| 403 | `permission_denied` | 当前角色不能提案或决策；刷新成员/Run，不自动重试 |
| 404 | `resource_not_found` | 隐藏跨 Workspace、非请求者或已撤权资源；停止流 |
| 409 | `active_run_exists` | 一个会话已有非终态 Run；读取现有 Run |
| 409 | `version_conflict` | Conversation/Run/Approval CAS 失败；刷新快照 |
| 409 | `approval_parameters_tampered` | 三方哈希或标识不一致；禁止执行 Tool |
| 409 | `approval_expired` | 不执行 Tool；刷新终态 |
| 409 | `checkpoint_binding_conflict` / `checkpoint_missing` / `checkpoint_corrupted` / `checkpoint_not_resumable` | 不改变审批；人工重试或失败补偿，绝不绕过 Checkpoint |
| 409 | `idempotency_conflict` | 同一 Key 用于不同请求；停止重试 |
| 410 | `event_history_expired` | 按 snapshot URL 恢复 |
| 422 | `invalid_last_event_id` | 修正/清空非法游标，不盲目重连 |
| 422 | `retrieval_strategy_not_available` | 本阶段不支持 Local/Global |

### 4.5 协同契约

本闭环不增加 WebSocket/Yjs/Hocuspocus 契约。自动保存草稿不触发 Agent Run，待办创建也不发布文档或改变 RAG 可见版本。

## 5. MCP 契约：传输、能力类型、名称、参数 Schema、返回结构、权限与风险等级

P8 收口不暴露任何 MCP Resource、Prompt 或 Tool，不启用 stdio、Streamable HTTP 或旧 HTTP+SSE MCP 传输。`knowledge_search`、`inspiration_search`、`todo_create` 是后端内部 LangGraph Tool 语义，不是 MCP Tool。

| 内部能力 | 参数 Schema | 返回 | 权限 | 风险 |
|---|---|---|---|---|
| `knowledge_search` | 既有严格 Pydantic Schema | 可信 RAG 结果与固定引用 | VIEWER | 低，只读 |
| `inspiration_search` | 既有严格 Pydantic Schema | Workspace 内灵感命中 | VIEWER | 低，只读 |
| `todo_create` | `title,description?,due_at?,assignee_id?`，`extra=forbid` | 唯一 Todo 事实 | EDITOR + HITL | 高，写操作 |

P9 如需暴露 MCP，必须另立契约，复用相同业务 Service、RBAC、审批和审计，不得直接复用本阶段 REST/SSE 作为 MCP 传输。

## 6. 评估契约：数据 Schema、语料快照、指标、对照组、阈值、成本和原始报告

### 6.1 收口测试集 Schema

除既有 100 题路由/RAG 评估外，新增版本化 `p8-closeout-e2e-v1` 场景清单；每条至少记录：

```json
{
  "case_id": "P8-CLOSE-001",
  "workspace_fixture": "fixture-version",
  "actor_role": "EDITOR",
  "input_message": "明天下午提醒我提交周报",
  "fault_point": null,
  "concurrency": 1,
  "expected_route": "TODO_CREATE",
  "expected_run_status": "COMPLETED",
  "expected_event_types": [],
  "expected_todo_count": 1,
  "expected_error_code": null
}
```

语料类用例必须固定 Workspace、发布版本、Chunk/Source Span 和检索配置；本闭环 TODO 用例不依赖 GraphRAG 或协同草稿。

### 6.2 指标、对照组与阈值

| 指标 | 对照/切片 | 门禁 |
|---|---|---:|
| 端到端自然终态率 | 无故障、4 个故障注入点 | 100% |
| Todo 副作用唯一率 | 20 并发批准、重复 HTTP Key、不同 Key 同决定、租约重放 | 100% |
| 未审批 Todo 数 | VIEWER、未决、拒绝、过期、哈希篡改 | 0 |
| 跨 Workspace/撤权泄露 | REST、SSE、Checkpoint | 0 |
| 审批可见但不可恢复数 | `approval.required` 后立即批准 | 0 |
| SSE 序号连续率 | 断线前/后、410 快照恢复 | 100% |
| Dispatch 恢复率 | API/Worker/Redis 重启与租约超时 | 100% |
| 四路由质量 | 沿用冻结评测集 | Macro-F1 >= 0.90；TODO 召回率 100% |
| Tool/参数质量 | 沿用冻结评测集 | Tool 准确率 >= 95%；字段 F1 >= 0.95 |

对照组至少包括：无故障基线、关闭 Redis 的 MySQL 轮询、单 Worker、双 Worker 租约竞争、批准响应提交前断网。不得用 Mock-only 结果替代 MySQL 和浏览器 E2E。

### 6.3 成本与原始报告

原始 JSON/JSONL 报告必须记录：契约/代码/迁移版本、数据集和语料快照、模型与 Prompt 版本、随机种子、Python/Node/MySQL 版本、Worker 数、lease/retry 配置、机器配置、开始时间、各阶段延迟、模型 Token/成本、attempts、fault point、HTTP/SSE 原始状态摘要和断言结果。日志须脱敏，不保存 Token、Cookie、完整私密正文或 Checkpoint blob。

首轮仅报告实测 TTFT、端到端 P50/P95、Token 和成本；不设未经测量的延迟或成本宣传值。P10 才能形成正式可对外数字。

## 7. 前后端任务清单、P0-P10 依赖、阶段门禁和测试清单

### 7.1 Backend 原子任务

| ID | 任务 | 依赖 | 完成证据 |
|---|---|---|---|
| B1 | 修复 Alembic head 断言，验证 `0010 -> 0011 -> 0010 -> 0011` 和 utf8mb4 | P0/P1 | 迁移测试和真实 MySQL 日志 |
| B2 | 实现 Dispatch claim、续租、退避重排、终态收尾和最大重试失败补偿 | B1 | 单元测试 + 双 Worker MySQL 竞争测试 |
| B3 | Worker 装配现有 AgentKernel，持久化 route、结果消息、引用和确定性事件 | B2 | 四路由集成测试；请求线程不执行图 |
| B4 | 将 TODO 审批改为 prepare/checkpoint/publish 三步；未绑定审批不出 API | B3 | checkpoint-ready 竞态与强杀测试 |
| B5 | 固化新 Namespace、精确绑定和兼容读取窗口；统一参数规范化与三方哈希 | B4 | 错绑/篡改/旧 Namespace 测试 |
| B6 | 收口同步决策恢复：恢复后再完成审批/Todo/Run 事务，并支持响应丢失重放 | B5 | 20 并发批准和 crash-recovery 测试 |
| B7 | SSE 每轮重新鉴权、终态关闭、410 回放和事件 payload 对齐 | B4/B6 | 撤权竞态、断线重放测试 |
| B8 | Worker 生命周期、健康/就绪、配置和脱敏指标；Redis 通知失败降级轮询 | B2-B7 | 启停/Redis 故障集成测试 |

Backend 只能修改 `/backend`，不得更改本文冻结的 REST 字段；若必须新增 DB 约束，先提交 Alembic 契约差异供主 Agent 确认。

### 7.2 Frontend 原子任务

| ID | 任务 | 依赖 | 完成证据 |
|---|---|---|---|
| F1 | 保持既有 Schema 解析，按 `approval.required` 刷新快照，按终态事件停止流 | B7 可用契约桩 | API/Store 单测 |
| F2 | 依据当前 Workspace 角色将 VIEWER 审批卡设为只读；403 后刷新快照/成员 | P1 + F1 | 组件/Store 单测 |
| F3 | Workspace/Conversation 切换时取消旧 SSE、Run、审批决定和 Todo 请求，丢弃迟到结果 | F1 | generation/AbortController 竞态测试 |
| F4 | 对 401/403/404/409/422 确定性停止重连；仅网络、429、显式 retryable 5xx 退避 | F1 | SSE 状态机单测 |
| F5 | 410 时从快照恢复游标和审批/Todo；批准响应丢失后可安全恢复 | B6/B7 | 浏览器 E2E |

Frontend 只能修改 `/frontend`，不得把 `tool.proposed` 当成已创建 Todo，也不得在本地计算或修改审批哈希。

### 7.3 并行顺序与 P0-P10 门禁

```text
B1 -> B2 -> B3 -> B4 -> B5 -> B6 -> B7 -> B8
                  \-> F1 -> F2/F3/F4 -> F5
```

- B4 冻结审批可见性和事件 payload 后，Frontend 可并行执行 F1-F4；F5 等待 B6/B7。
- 本契约复用 P0、P1 和当前 Basic RAG，是已批准的 P8 垂直切片例外；不得据此跳过 P5/P6/P7 正式门禁。
- 收口通过后，只能声称“P8 单 Agent MVP 垂直切片通过”。P9 MCP 和 P10 正式报告仍被阻塞。
- Reviewer 审查通过条件仍为 S1=0、S2<=2，且所有剩余 S2 有责任人和回归测试。

### 7.4 测试清单

Backend：

- 创建 Run 的事务原子性、Conversation 版本冲突、活动 Run 竞争、HTTP 幂等冲突。
- Dispatch `SKIP LOCKED`、claim token、续租失败、过期接管、退避、5 次耗尽、终态 no-op。
- 四路由自然终态；TODO 参数不足不产生 Approval/Todo；VIEWER TODO 失败。
- checkpoint 保存前/后、T4 前/后强杀；`approval.required` 后立即恢复成功。
- Namespace/graph version、Workspace/thread/run/approval/tool/hash 任一错配均拒绝。
- 同决定重放、相反决定、过期、撤权、降权、20 并发批准、响应丢失；Todo 和事件唯一。
- SSE 鉴权、每轮撤权、heartbeat 撤权、连续回放、非法/越界 cursor、410 快照恢复、终态关闭。
- 真实 MySQL utf8mb4 中文参数/哈希、锁顺序、死锁重试；Redis 关闭时继续完成。

Frontend：

- 202 后快照/SSE 启动；审批卡只在绑定 Approval 出现。
- VIEWER 只读、EDITOR 决策 loading/防双击、409 后刷新、批准后加载 Todo。
- 事件去重、乱序/重复帧防护、heartbeat 不推进 cursor、终态停止。
- 确定性 4xx 不重连，网络/429/retryable 5xx 有界退避，重试耗尽可手动恢复。
- Workspace 切换取消所有旧请求；旧 Workspace 的迟到响应不能更新新状态。
- 410、刷新、断网、浏览器重开后，从快照恢复待审批和已完成状态。

集成：

- 两个真实 Worker + MySQL、API 重启、Worker 强杀、Redis 停机、浏览器断网恢复。
- 撤销成员与事件发送、审批决定并发；验证撤权后无新业务帧和 heartbeat。
- 完整事件序列及每个事件对应的 MySQL 事实；不得只断言 UI 文案。

## 8. 风险、开放问题与需要主 Agent 确认的决策

### 8.1 主要风险

- 当前实现的 Approval 创建/事件提交早于 Checkpoint 最终绑定；若不按 T3/C1/T4 拆分，会保留 `checkpoint_not_ready` 用户竞态，属于 S2，若导致绕过审批补偿则升级为 S1。
- 现有 Dispatch 只有模型和仓储草稿，尚缺 Worker 自然终态；在 Worker 落地前，202 Run 会永久停留 RUNNING，属于核心闭环阻塞。
- SSE 当前仅建连鉴权，长连接内未持续检查成员资格；撤权后继续发送是 S1。
- 仅靠固定 `lease_owner` 不能区分同一 Worker 的两次领取；必须使用每次 claim 唯一 token，并在业务提交前做租约 CAS。
- MySQL Checkpoint 与业务事务分离，无法实现跨事务原子；必须以“未绑定审批不可见 + 可重试准备 + 原子发布”补偿，禁止宣称原子保存。
- 同步批准 REST 在 DB 慢或 Checkpoint blob 较大时可能延迟；本切片无批准后的模型调用，仍需设置请求超时并记录实测数据。

### 8.2 默认决策（主 Agent 若不反对即按此实施）

1. 保持审批决定接口同步 200，不新增异步 Resume Dispatch，避免破坏现有前端和 `ApprovalDecisionResult`。
2. `agent_run_dispatches` 只调度首次 LangGraph 执行；审批恢复由现有决定服务执行。若未来改异步，必须新增 operation 状态契约，不能静默改变 200 语义。
3. 使用现有字段承载 claim token，不为租约新增列；用业务状态和唯一约束实现 fencing。若 Backend 证明现有字段不足，再提出 `0012`。
4. 旧 Checkpoint Namespace 兼容读取一个发布窗口，新写只用版本化且含 `run_id` 的 Namespace。
5. Worker 可先作为独立进程入口部署；开发环境允许 API 生命周期旁路启动，但生产不得依赖单个 API 进程的内存任务。

### 8.3 需要主 Agent 明确确认

- 是否接受上述一个发布窗口的旧 Namespace 兼容期；若当前没有未决旧审批，建议直接禁用旧 Namespace 扫描以减小错绑面。
- 生产 Worker 的部署拓扑和副本数；契约默认至少 2 副本以验证租约接管，但不据此承诺吞吐。
- 真实 MySQL、Redis 和浏览器 E2E 的可用环境与报告落点。缺少环境时不得将 Mock/SQLite 结果标记为已验证。
