# P9 MCP 当前进度与恢复说明

更新时间：2026-08-21  
当前分支：`codex/enterprise-rag-upgrade`  
P8 基线提交：`37a8364 feat(agent): close P8 checkpoint and HITL todo loop`

## 1. 当前暂停点

P9 按 `docs/upgrade/P9_MCP_PLAN.md` 拆为 B1-B5 五批。当前 **B1 已完成并通过复审**，B2 尚未开始落盘。本次暂停后不要继续扩展或优化，下一次应从 B2 的认证和 Workspace 安全边界开始。

当前 P9 文件仍在工作区，**尚未 Git commit，也尚未 push**。不要清理、覆盖或重置这些修改。

## 2. 已完成：B1 契约冻结与 Server 骨架

- 新增独立 P9 capability registry 与不可变 `McpCallContext`。
- 固定 `mcp==1.28.1` 与 MCP 协议 `2025-11-25`，stdio/HTTP 均拒绝旧协议初始化。
- 新增 P9 stdio 入口，身份材料来自启动环境，不进入 Tool Schema；credential 不写 stdout、stderr 或健康检查。
- 新增 stateless + JSON-response Streamable HTTP 骨架。
- 使用真实 `create_app()` 管理 mount 与 MCP session-manager lifespan。
- 新增 feature flag；B2 鉴权完成前，production/prod 环境禁止启用 P9。
- 健康信息明确标记 `skeleton_only=true`、`authenticated=false`、`business_capabilities_registered=false`。
- P9 Server 使用稳定名称 `enterprise-knowledge-agent`。
- 旧 `app.mcp.server` 保持不变，仍可作为 legacy stdio 入口。
- 当前 B1 不注册任何业务 Tool、Resource 或 Prompt，没有伪装为已经可用于业务的 MCP Server。

## 3. 已获得的验证证据

- P9 B1 协议测试：`11 passed`。
- P9 + legacy MCP 回归：`19 passed`。
- 真实 ClientSession 验证：
  - P9 stdio initialize/list 成功；
  - P9 Streamable HTTP initialize/list 成功；
  - 两种传输拒绝非 `2025-11-25` 协议；
  - HTTP 混合“旧协议拒绝 -> 合法初始化 -> 再拒绝旧协议 -> 再合法初始化 -> tools/list”无挂起、无丢消息；
  - legacy stdio 能真实启动并列出原有 11 个工具，stdout 未被日志污染，token 未进入 stderr。
- 测试加固前的后端全量回归：`226 passed, 9 skipped`；最后一次加固只修改测试文件，未再次执行全量。
- Ruff：通过。
- Reviewer 最终结论：S1=0、S2=0；两项 S3 测试建议已补齐。
- 标准 `mypy app` 仍受环境问题阻塞：项目 target 为 Python 3.11，但当前 NumPy 2.5.1 stub 含 Python 3.12 `type` 语法。不要把它表述为已通过；本次源码曾在 Python 3.13 target 下通过。

## 4. 尚未完成

### B2：认证、Workspace 与传输安全

- HTTP Bearer JWT 认证。
- Workspace 只从 `/api/v1/workspaces/{workspace_id}/mcp` 路径绑定。
- 每请求重新校验用户、Workspace、成员状态与角色。
- stdio 每次业务调用复核 MySQL 身份与成员事实，不能信任环境中的 role。
- Origin/Host allowlist、256 KiB body 上限、协议 Header、deadline、request ID、结构化错误与脱敏。
- capability list 的角色过滤，以及 call/read/get 的二次授权。
- 无认证、坏/过期 JWT、非法 Origin/Host、跨租户、停用、降权等安全矩阵测试。

### B3：只读 MCP 能力

- `knowledge_search`
- `inspiration_search`
- `agent_run_get`
- `approval_get`
- `todo_list`
- `todo_get`
- Resources 与 `knowledge_qa` Prompt
- stdio/HTTP 与现有 REST 业务语义对照测试。

### B4：Todo HITL

- `todo_create` 只生成 P8 Run/Proposal/Approval，不直接写 Todo。
- 幂等、审计、重启恢复、重复调用收敛。
- 永不注册 `approval_decide` MCP Tool；审批继续使用现有 Vue/REST 人工界面。
- 检查现有审批卡是否需要最小化展示 MCP 来源；目前没有前端改动。

### B5：限流、可观测性与最终回归

- Redis token bucket 与开发环境本地有界 fallback。
- 超时、并发、429/retry-after、结构化日志、指标和敏感信息脱敏。
- stdio/HTTP 完整端到端、故障、断连和并发测试。
- `.env.example`、客户端配置、使用文档和原始验证报告收口。
- Reviewer 最终安全审查与主 Agent 集成验收。

部署、Docker、Nginx 和网关不在当前 P9 范围内，按用户要求后置。

## 5. 进度估算

- 按批次数量：完成 1/5，剩余 4/5。
- 按工程工作量：约完成 20%-25%，剩余约 75%-80%。
- 剩余工作中 B2 和 B4 风险最高；B3 业务适配工作量最大；B5 负责工程收口。

## 6. 下次恢复顺序

1. 先执行 `git status --short`，确认本文件、P9 计划和 B1 修改仍在。
2. 运行 B1 定向回归：
   `cd backend`
   `./.venv/Scripts/python.exe -m pytest tests/test_p9_mcp_b1.py -q`
3. 只从 B2 开始，不重复实现 B1，不提前注册 B3/B4 业务工具。
4. B2 完成后先交 Reviewer；S1=0、S2<=2 且安全矩阵通过，才能进入 B3。

