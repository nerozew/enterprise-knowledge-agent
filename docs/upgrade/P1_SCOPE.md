# P1：Workspace、RBAC 与审计

状态：`implemented-and-gate-passed`  
上游契约：[`P0_CONTRACT.md`](./P0_CONTRACT.md)  
范围：仅 P1，不包含 Redis、MinIO、Milvus、文档摄取、协同编辑、GraphRAG、LangGraph、SSE 或 MCP HTTP。

## 目标

- 将业务授权边界从 `user_id` 升级为 `workspace_id`。
- 注册用户自动获得唯一的个人 Workspace 和 `OWNER` 成员关系。
- 现有用户、灵感、标签、灵感标签关系和 AI 生成记录迁入个人 Workspace。
- 支持 `OWNER / ADMIN / EDITOR / VIEWER` 四种角色。
- Workspace、成员和已有内容写操作生成同事务、只追加、脱敏的审计记录。
- 旧的灵感与标签 URL 保留一个兼容周期，固定访问用户个人 Workspace。

## 领域规则

### Workspace

- `PERSONAL` Workspace 必须绑定唯一 `personal_owner_user_id`。
- `TEAM` Workspace 不绑定个人所有者。
- P1 只支持 `ACTIVE` Workspace，不提供删除、归档或组织树。
- Workspace 名称不是授权依据，也不要求全局唯一。

### 成员

- 成员状态冻结为 `INVITED / ACTIVE / SUSPENDED / REMOVED`；P1 创建成员时仅产生 `ACTIVE`。
- 非 `ACTIVE` 成员按非成员处理。
- `PERSONAL` Workspace 的个人所有者不得退出、降级、停用或移除。
- 任何减少 `ACTIVE OWNER` 数量的操作必须锁定 Workspace 与 Owner 成员行；操作后至少保留一名 `ACTIVE OWNER`。
- 成员不硬删除，删除操作将状态改为 `REMOVED`。

### 隐藏存在性

- 未认证返回 `401 authentication_required`。
- 非成员、`SUSPENDED`、`REMOVED` 或路径 Workspace 与资源 Workspace 不一致时返回 `404 resource_not_found`。
- `ACTIVE` 成员权限不足时返回 `403 permission_denied`。
- Repository 必须使用 `workspace_id + resource_id` 查询，禁止先按全局 ID 查询再判断租户。

## RBAC

| 动作 | OWNER | ADMIN | EDITOR | VIEWER |
|---|---:|---:|---:|---:|
| 查看 Workspace、灵感和标签 | 是 | 是 | 是 | 是 |
| 管理灵感、标签和 AI 生成 | 是 | 是 | 是 | 否 |
| 查看成员和审计 | 是 | 是 | 否 | 否 |
| 添加或管理 EDITOR、VIEWER | 是 | 是 | 否 | 否 |
| 添加或管理 ADMIN、OWNER | 是 | 否 | 否 | 否 |
| 修改 Workspace 名称 | 是 | 是 | 否 | 否 |
| 退出 TEAM Workspace | 受 last-owner 限制 | 是 | 是 | 是 |

ADMIN 不得操作 ADMIN 或 OWNER，也不得授予 ADMIN 或 OWNER。

## 数据库迁移

采用 expand–backfill–contract：

1. `0003_p1_workspace_expand.py`
   - 新建 `workspaces`、`workspace_members`、`audit_logs`、`idempotency_records`。
   - 为 `inspirations`、`tags`、`inspiration_tags`、`ai_generations` 添加可空 `workspace_id`。
2. `0004_p1_workspace_backfill.py`
   - 为每个现有用户创建唯一个人 Workspace 和 ACTIVE OWNER 成员关系。
   - 回填已有业务数据并验证关联一致性。
   - 发现归属矛盾时失败，不猜测修复。
3. `0005_p1_workspace_contract.py`
   - 将业务 `workspace_id` 收紧为非空。
   - 建立 Workspace 范围的唯一约束、索引和复合外键。
   - 标签名唯一域改为 `(workspace_id, normalized_name)`。
   - AI 幂等域改为 `(workspace_id, user_id, idempotency_key)`。

MySQL 数据库、连接与文本统一使用 `utf8mb4`。迁移必须验证空库升级与包含中文 MVP 数据的升级。

## REST API

统一前缀 `/api/v1`。

### Workspace

- `GET /workspaces`
- `POST /workspaces`，需要 `Idempotency-Key`
- `GET /workspaces/{workspace_id}`
- `PATCH /workspaces/{workspace_id}`，请求包含 `version`

### 成员

- `GET /workspaces/{workspace_id}/members`
- `POST /workspaces/{workspace_id}/members`，需要 `Idempotency-Key`
- `PATCH /workspaces/{workspace_id}/members/{user_id}`，需要 `Idempotency-Key` 和 `version`
- `DELETE /workspaces/{workspace_id}/members/{user_id}`，需要 `Idempotency-Key` 和 `version`

P1 仅允许通过已注册且启用用户的精确邮箱添加成员；未知用户返回 `404 member_candidate_not_found`。

### 审计

- `GET /workspaces/{workspace_id}/audit-logs`
- 支持动作、操作者、目标、日期过滤和 `(created_at, id)` 游标分页。
- 仅提供列表能力，不提供修改或删除接口。

### Workspace 范围业务 API

- `/workspaces/{workspace_id}/inspirations/**`
- `/workspaces/{workspace_id}/tags/**`
- `/workspaces/{workspace_id}/inspirations/{inspiration_id}/ai-generations`

旧 URL 固定解析到登录用户的个人 Workspace，并返回 `Deprecation: true` 与 successor `Link`。

## 审计安全

允许记录操作名、资源 ID、角色快照、版本变化、状态变化和请求 ID。

禁止记录：

- 密码、JWT、Cookie、API Key 和幂等键原文；
- 灵感正文、标题、摘要、扩展内容和来源 URL；
- 标签名；
- AI Prompt、instruction、输入和输出；
- 完整邮箱、原始 IP、原始 User-Agent 或请求体快照。

IP 和 User-Agent 如需关联，只保存服务端带密钥 HMAC。业务写入与对应审计必须在同一 MySQL 事务中提交。

## 前端范围

- 顶部 Workspace 切换器显示空间类型与当前角色。
- 支持新建团队空间。
- `/workspace` 解析后进入 `/w/{workspace_id}`。
- `/w/{workspace_id}` 继续承载灵感工作台。
- OWNER/ADMIN 可进入成员管理与审计页面。
- 切换时清空旧 Workspace 缓存，并防止迟到请求污染新空间。
- 最近 Workspace 按用户保存；失效后回落到 `is_default=true` 的个人 Workspace。

## 阶段门

- 后端 Ruff、mypy、pytest 通过。
- 前端 ESLint、Vue TypeScript、Vitest 和生产构建通过。
- 空库与 MVP 数据 Alembic 升级可验证。
- RBAC、跨 Workspace、last-owner、个人所有者、幂等与审计脱敏有自动化测试。
- Reviewer 结果为 `S1 = 0`、`S2 <= 2`，剩余风险有明确记录。

P1 门通过前不进入 P2。

## 验证记录

验证日期：2026-07-25

- Backend Ruff：通过。
- Backend mypy：43 个源码文件通过。
- Backend pytest：`32 passed, 3 skipped`；3 个跳过项为默认环境未显式启用的真实 MySQL 集成用例。
- 显式设置独立 `P1_TEST_MYSQL_URL` 后，真实 MySQL 迁移与并发套件：`5 passed`。
- 真实 MySQL 验证包含 `utf8mb4`、MVP 中文数据回填、复合约束和双 `AsyncSession` 幂等竞争。
- Frontend ESLint 与 Vue TypeScript：通过。
- Frontend Vitest：17 个测试文件、52 个测试通过。
- Frontend 生产构建：通过；保留既有的大包体积提示，不影响产物生成。
- `git diff --check`、UTF-8 与 BOM 检查：通过。
- Reviewer 最终结果：`S1 = 0`、`S2 = 0`，P1 阶段门通过。

已知非阻塞风险：

- 前端主包仍超过 Vite 默认 500 kB 提示阈值，后续可通过路由懒加载和 `manualChunks` 优化。
- 默认测试命令不会连接真实 MySQL；CI 或发布门必须提供隔离数据库的 `P1_TEST_MYSQL_URL`。
