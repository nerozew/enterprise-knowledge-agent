# P8 单 Agent MVP 收口检查点

更新时间：2026-08-10

状态：`CODE_COMPLETE / ENVIRONMENT_GATE_PENDING`。代码增量审查为 `S1=0、S2=1`；剩余 S2 是缺少隔离真实环境下的双 Worker、并发审批、故障注入和浏览器 E2E 证据，因此尚不能宣称完整 P8 阶段门禁通过。

## 本轮已完成

- 冻结 `p8-agent-closeout-v1` 契约，闭环为：创建 Run 与 Dispatch、Worker 执行 LangGraph、待办提案、同步保存并精确绑定 Checkpoint、发布审批、SSE 通知、批准恢复、幂等创建 Todo、提交完成事件。
- 新增 MySQL-first Dispatch Worker，支持唯一 claim token、`SKIP LOCKED`、DB UTC 租约、续租 fencing、过期接管、指数退避、最大重试补偿和终态 no-op；Redis 仅作为后续可选唤醒，不作为事实源。
- TODO 审批采用 internal prepare、Checkpoint sync、原子 publish 三阶段；T3/T4 都校验 Dispatch claim，未绑定审批不对 API/SSE 可见。
- Checkpoint 使用 `thread_id=conversation_id`，Namespace 绑定图版本与 `run_id`；恢复时校验 Workspace、Run、Approval、Tool、参数哈希和图版本。
- 批准路径在同一事务完成 Approval、Todo、Run 与完成事件；Todo 通过来源唯一约束和 HTTP 幂等双层防重，受理人固定为当前 Workspace 内的操作用户并在发布/T6 双重校验。
- SSE 在查询和 heartbeat 前重新鉴权，支持连续游标回放、410 快照恢复和 `stream.closed` 控制帧；撤权、Token 到期和资源失效后前端确定性停流。
- 前端补齐 Workspace/Conversation 切换时取消旧请求、generation 隔离迟到响应、VIEWER 只读审批、确定性 4xx 停止重连、批准响应丢失后的事实恢复。
- 修复 P8 迁移表 collation，`0008/0009/0011` 显式使用 `utf8mb4_unicode_ci`；`0011` downgrade 在存在非终态 Dispatch/Run 或待恢复 Approval 时拒绝删除事实表。

## 已验证

- 后端：`ruff check app tests` 通过；全量 Pytest `217 passed, 9 skipped`。
- 后端类型检查：`mypy --follow-imports=skip app` 通过；标准 `mypy app` 仍被本机 NumPy Python 3.12 stub 与项目 Python 3.11 target 的语法冲突阻断。
- 真实 MySQL 迁移链：`0007 -> 0011 -> 0010 -> 0011` 通过，当前为 `0011_p8_agent_dispatch (head)`，代表表均为 `utf8mb4_unicode_ci`。
- Worker：`python -m app.workers.agent --once` 空轮询可正常启动并退出。
- 前端：lint、类型检查、37 个测试文件共 137 项单元测试、生产构建全部通过。
- Reviewer 三轮增量复审：代码问题 `S1=0、S2=0`；阶段证据风险 `S2=1`。

## 仍需验证

1. 当前 MySQL 用户无 `CREATE DATABASE` 权限，未能创建隔离 Schema，因此真实双 Worker `SKIP LOCKED` 竞争与 20 并发批准尚未执行。
2. 尚未执行 Worker 四个强杀点、Redis 停机、API/Worker 重启后的版本化故障注入报告。
3. 项目未配置 Playwright，尚未执行真实浏览器 REST/SSE、撤权和 410 回放 E2E。
4. 上述证据补齐前，只能表述为“P8 单 Agent MVP 代码闭环完成”，不能表述为“完整 P8 发布门禁通过”。
