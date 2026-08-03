# P8 单 Agent MVP 契约

状态：`FROZEN_FOR_VERTICAL_SLICE`

契约版本：`p8-agent-mvp-v1`

## 1. 阶段说明

本契约批准在现有 Basic RAG、Workspace/RBAC、审计和幂等能力之上实施 P8 单 Agent 垂直切片，用于尽早验证 LangGraph、Tool Calling、Checkpoint、HITL 与 SSE 的完整闭环。

该例外不代表 P5、P6、P7 或完整 P8 已通过，不得据此声称 GraphRAG、多人协同、长期记忆、Multi-Agent 或 MCP Streamable HTTP 已实现。知识问答只调用当前可信 Basic RAG；后续仍需按路线补齐阶段门禁。

## 2. MVP 目标

单 Agent 支持四类结构化路由：

1. `DIRECT_ANSWER`：普通解释或非企业事实对话；
2. `KNOWLEDGE_QA`：调用当前 Workspace 的可信 RAG；
3. `INSPIRATION_SEARCH`：调用当前 Workspace 的灵感检索；
4. `TODO_CREATE`：生成待办参数，强制人工审批后执行。

主流程：

```text
START -> authorize/load_context -> classify_route
  |-> DIRECT_ANSWER -> model_answer -> finalize
  |-> KNOWLEDGE_QA -> knowledge_search -> grounded_result -> finalize
  |-> INSPIRATION_SEARCH -> inspiration_search -> compose_result -> finalize
  `-> TODO_CREATE -> propose_tool -> authorize
                         |-> 参数不足 -> clarify -> finalize
                         `-> interrupt/approval -> execute_todo -> finalize
```

## 3. Agent State 与结构化路由

```python
class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal[
        "DIRECT_ANSWER",
        "KNOWLEDGE_QA",
        "INSPIRATION_SEARCH",
        "TODO_CREATE",
    ]
    confidence: float
    reason_code: Literal[
        "CASUAL_OR_GENERAL",
        "WORKSPACE_KNOWLEDGE",
        "INSPIRATION_LOOKUP",
        "ACTION_REQUEST",
        "AMBIGUOUS",
    ]
    normalized_query: str
    needs_clarification: bool = False
```

Agent State 至少包含：

- `messages`；
- `workspace_id`、`conversation_id`、`run_id`、`actor_id`；
- `route`；
- `pending_tool_call` 与 `approval_id`；
- `retrieval_result` 与固定版本 `citations`；
- `final_answer` 与 `error_code`。

`workspace_id`、`actor_id` 和权限只能由认证上下文注入，模型不得生成或覆盖。`reason_code` 只记录分类代码，不保存模型思维链。

## 4. Tool 契约

| Tool | 模型参数 | 权限 | HITL |
|---|---|---|---|
| `knowledge_search` | `query, top_k?, filters?` | VIEWER | 否 |
| `inspiration_search` | `query?, tag_ids?, limit?` | VIEWER | 否 |
| `todo_create` | `title, description?, due_at?, assignee_id?` | EDITOR | 强制 |

Tool 只调用业务 Service，不允许模型构造 SQL、Milvus Filter、Workspace 或用户身份。运行时注入 `workspace_id`、`actor_id`、`run_id`、`tool_call_id` 和幂等键。

`todo_create` 只能先产生 `ToolProposal`。服务端规范化参数并计算参数哈希，创建审批后中断；批准时重新校验参数哈希、审批版本、有效期、当前角色和资源归属，再以 `run_id + tool_call_id` 为幂等域创建待办。Agent 不得自行审批。

## 5. Checkpoint、记忆与事件

- `conversation_id` 作为 LangGraph `thread_id`；
- MySQL 是 Conversation、Message、Run、Approval、Todo、Event 与 Checkpoint 的事实源；
- Redis 只负责 Worker 唤醒、运行锁和 SSE 通知，丢失后必须可由 MySQL 恢复；
- MVP 只实现会话内短期记忆和执行 Checkpoint，不实现跨会话长期记忆；
- Checkpoint Namespace 必须包含图版本，禁止新图错误恢复旧状态；
- SSE 业务事件先持久化，再通知客户端，并按 `Last-Event-ID` 回放。

首批事件：

- `run.created`、`route.selected`；
- `retrieval.started`、`retrieval.completed`；
- `tool.proposed`、`tool.started`、`tool.completed`、`tool.failed`；
- `approval.required`、`approval.resolved`；
- `todo.created`；
- `run.completed`、`run.failed`、`run.cancelled`。

## 6. 明确延期

- GraphRAG Basic/Local/Global；
- Supervisor/Researcher/Action/Reviewer Multi-Agent；
- 跨会话长期记忆和用户画像；
- 自主循环规划、并行 Tool 和外部第三方写操作；
- MCP Streamable HTTP；
- WebSocket、语音和后台主动任务。

## 7. 实施批次

1. Agent 内核：依赖兼容性、Agent State、四路由、两个只读 Tool 和单元测试；
2. 领域与 HITL：Conversation、Run、Approval、Todo、审计、幂等和迁移；
3. 持久化恢复：MySQL Checkpointer、Outbox Resume Worker、短期记忆；
4. API 与前端：REST、SSE、审批卡、刷新恢复和待办展示；
5. Agent Evals：路由、Tool、参数、副作用、恢复、延迟、Token 与成本。

## 8. 硬性验收

- 四路由 Macro-F1 不低于 `0.90`，待办路由召回率 `100%`；
- Tool 选择准确率不低于 `95%`，参数字段级 F1 不低于 `0.95`；
- 跨 Workspace 泄露、VIEWER 写入和未审批写入均为 `0`；
- 重复审批或 Worker 重放不会重复创建待办；
- 审批前后重启可恢复或确定性补偿；
- SSE 持久化业务事件连续率 `100%`，支持断线回放；
- 知识回答继续通过现有证据门禁，引用固定到发布版本与 Source Span；
- 首轮只记录实际 TTFT、P50/P95、Token 和成本，不预设未经测试的性能结论。

