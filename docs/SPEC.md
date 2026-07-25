# 日常灵感智能助手本地部署 SPEC

## 0. 文档信息

| 项目 | 内容 |
|---|---|
| 文档版本 | 1.0 |
| 适用项目版本 | MVP 0.1.0 |
| 目标环境 | Windows 10/11 本地开发环境 |
| 前端 | Vue 3、TypeScript、Vite、Pinia、Element Plus |
| 后端 | Python 3.11+、FastAPI、SQLAlchemy 2、Alembic |
| 数据库 | MySQL 8，`utf8mb4` |
| Agent 接口 | MCP stdio |
| AI 接口 | 兼容 Chat Completions 请求与响应格式的 HTTP API |

本文档中的密码、Token、API Key 都是占位符。真实凭据只能写入本机 `.env`，不得提交到 Git。

## 1. 部署目标

本地部署完成后，应能够：

1. 在浏览器打开灵感助手并注册用户。
2. 登录后创建、编辑、搜索、删除和恢复灵感。
3. 创建标签，并按一个或多个标签筛选灵感。
4. 使用外部 AI API 生成摘要或扩展草稿。
5. AI API 不可用时自动使用本地 fallback，不阻塞灵感管理。
6. 通过 MCP stdio 让本机 Agent 调用灵感 Tools。
7. 使用 MySQL 8 持久化用户、灵感、标签和 AI 生成记录。

本次部署不包括：图片和音频上传、OCR、向量数据库、多人协作、刷新令牌、SAP 集成和生产集群部署。

## 2. 系统架构

```mermaid
flowchart LR
    U["浏览器用户"] -->|"HTTP :5173"| F["Vue 3 / Vite"]
    F -->|"/api 代理"| B["FastAPI :8000"]
    B -->|"SQLAlchemy + asyncmy"| M[("MySQL 8 :3306")]
    B -->|"HTTPS Bearer API Key"| A["外部 AI API"]
    C["本机 MCP Client / Agent"] -->|"stdio + local_token"| S["MCP Server"]
    S -->|"复用 Service 层"| M
```

### 2.1 端口

| 服务 | 默认地址 | 用途 |
|---|---|---|
| 前端 | `http://127.0.0.1:5173` | 用户界面 |
| 后端 | `http://127.0.0.1:8000` | REST API |
| API 文档 | `http://127.0.0.1:8000/docs` | Swagger UI |
| MySQL | `127.0.0.1:3306` | 数据持久化 |
| MCP | 无 TCP 端口 | 通过 stdio 通信 |

## 3. 环境要求

### 3.1 必需软件

- Git。
- Python 3.11 或更高版本。
- Node.js 20.19+ 或 Node.js 22.12+。
- npm。
- MySQL Community Server 8.x。

检查命令：

```powershell
git --version
python --version
node --version
npm --version
Get-Service MySQL80
```

若 `python`、`node` 或 `npm` 提示找不到命令，需要先安装对应运行时或把安装目录加入 PATH。

### 3.2 项目目录

```text
日常灵感智能助手开发/
├── frontend/            # Vue 用户界面
├── backend/             # FastAPI、数据库和 MCP
├── docs/SPEC.md         # 本部署规范
├── .codex/agents/       # 项目子 Agent 配置
├── AGENTS.md            # 主 Agent 工作流
└── README.md            # 快速启动说明
```

## 4. MySQL 部署

### 4.1 启动 MySQL

管理员 PowerShell：

```powershell
Start-Service MySQL80
Get-Service MySQL80
```

状态应为 `Running`。

### 4.2 创建数据库和最小权限账号

使用 MySQL 管理员账号登录：

```powershell
mysql -u root -p
```

执行以下 SQL，并替换密码：

```sql
CREATE DATABASE IF NOT EXISTS inspiration_assistant
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'inspiration'@'localhost'
  IDENTIFIED BY '请替换为高强度数据库密码';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX,
      DROP, REFERENCES
ON inspiration_assistant.*
TO 'inspiration'@'localhost';

FLUSH PRIVILEGES;
```

验证：

```powershell
mysql -u inspiration -p -h 127.0.0.1 inspiration_assistant
```

进入 MySQL 后执行：

```sql
SELECT VERSION(), DATABASE(), @@character_set_database;
```

字符集应为 `utf8mb4`。

### 4.3 数据表

Alembic 会创建以下表：

| 表 | 作用 |
|---|---|
| `users` | 用户和密码哈希 |
| `inspirations` | 灵感正文、摘要、扩展内容、软删除和版本号 |
| `tags` | 用户标签和标签版本号 |
| `inspiration_tags` | 灵感和标签的多对多关系 |
| `ai_generations` | AI 操作、幂等键、模式、状态和生成结果 |
| `alembic_version` | 当前数据库迁移版本 |

数据归属通过 `user_id` 隔离。访问其他用户的资源统一表现为 404，避免泄露资源是否存在。

## 5. 后端配置与启动

### 5.1 创建虚拟环境

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

如果 PowerShell 禁止激活脚本，可在当前用户范围执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

也可以不激活，直接使用 `.\.venv\Scripts\python.exe`。

### 5.2 创建本地 `.env`

```powershell
Copy-Item .env.example .env
```

建议配置：

```dotenv
APP_NAME=日常灵感智能助手
APP_ENV=development
DEBUG=false
API_V1_PREFIX=/api/v1

DATABASE_URL=mysql+asyncmy://inspiration:数据库密码@127.0.0.1:3306/inspiration_assistant?charset=utf8mb4

JWT_SECRET=至少32字节的密码学安全随机值
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]

AI_PROVIDER_URL=
AI_API_KEY=
AI_MODEL=
AI_TIMEOUT_SECONDS=30

MCP_USER_ID=
MCP_LOCAL_TOKEN=
```

如果数据库密码包含 `@`、`:`、`/`、`?`、`#` 等 URL 特殊字符，需要先做 URL 百分号编码。

### 5.3 生成 JWT_SECRET

PowerShell 示例：

```powershell
$jwtBytes = New-Object byte[] 48
$jwtRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$jwtRng.GetBytes($jwtBytes)
$jwtRng.Dispose()
[Convert]::ToBase64String($jwtBytes)
```

将输出写入 `.env` 的 `JWT_SECRET`。程序会拒绝空值、短密钥和已知占位值。

### 5.4 执行数据库迁移

```powershell
alembic upgrade head
alembic current
```

预期迁移版本：

```text
0001_initial (head)
```

检查表：

```sql
USE inspiration_assistant;
SHOW TABLES;
```

### 5.5 启动 FastAPI

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

预期分别返回：

```json
{"status":"ok"}
```

```json
{"status":"ready"}
```

`live` 只表示应用进程可响应；`ready` 还会执行数据库 `SELECT 1`。

## 6. 外部 AI API 接入

### 6.1 当前适配协议

当前后端调用兼容 Chat Completions 格式的 HTTP Endpoint。`AI_PROVIDER_URL` 必须填写完整的生成 Endpoint，而不是服务首页。

后端发送：

```http
POST {AI_PROVIDER_URL}
Authorization: Bearer {AI_API_KEY}
Content-Type: application/json
```

请求体示例：

```json
{
  "model": "模型名称",
  "messages": [
    {
      "role": "system",
      "content": "你是日常灵感整理助手，只输出结果正文。"
    },
    {
      "role": "user",
      "content": "任务、标题、正文和补充要求"
    }
  ],
  "temperature": 0.2
}
```

API 必须返回以下兼容结构：

```json
{
  "choices": [
    {
      "message": {
        "content": "生成结果"
      }
    }
  ]
}
```

### 6.2 配置示例

```dotenv
AI_PROVIDER_URL=https://你的服务域名/v1/chat/completions
AI_API_KEY=你的真实API密钥
AI_MODEL=你的模型名称
AI_TIMEOUT_SECONDS=30
```

保存 `.env` 后重启后端。

### 6.3 AI 降级行为

以下任一条件发生时，服务会返回本地确定性 fallback：

- 三个 AI 配置项没有全部填写。
- HTTP 连接失败或超时。
- API 返回非 2xx。
- 返回体缺少 `choices[0].message.content`。
- AI 返回空内容。

API 返回字段 `mode`：

- `provider`：使用了真实外部 AI。
- `fallback`：使用了本地规则生成。

AI 生成只产生预览，不会自动覆盖灵感。用户必须在前端点击“保存到灵感”，后端才通过带版本号的 PATCH 持久化结果。

## 7. 前端配置与启动

### 7.1 安装依赖

另开一个 PowerShell：

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
```

`.env.local`：

```dotenv
VITE_API_BASE_URL=/api/v1
VITE_API_PROXY_TARGET=http://127.0.0.1:8000
VITE_REQUEST_TIMEOUT_MS=15000
```

### 7.2 启动前端

```powershell
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

开发模式下浏览器请求 `/api/*`，Vite 将其代理到 `http://127.0.0.1:8000`。

## 8. 用户使用流程

1. 打开前端页面。
2. 点击注册，填写昵称、邮箱和密码。
3. 登录后在快速记录区填写灵感正文，标题可选。
4. 可创建标签并绑定到灵感。
5. 在灵感卡片点击“AI”。
6. 选择“提炼摘要”或“扩展思路”。
7. 可填写补充要求，点击“开始生成”。
8. 检查生成预览：
   - 显示 `fallback` 提示，表示未使用真实模型。
   - 无 fallback 提示且后端返回 `mode=provider`，表示使用了真实 AI API。
9. 确认内容后点击“保存到灵感”。

## 9. REST API 契约摘要

API 前缀为 `/api/v1`。除注册和登录外，业务接口都使用：

```http
Authorization: Bearer {JWT}
```

| Method | Path | 说明 |
|---|---|---|
| GET | `/health/live` | 进程存活检查，无 API 前缀 |
| GET | `/health/ready` | 数据库就绪检查，无 API 前缀 |
| POST | `/api/v1/auth/register` | 注册并返回 JWT |
| POST | `/api/v1/auth/login` | 登录并返回 JWT |
| GET | `/api/v1/auth/me` | 当前用户 |
| GET | `/api/v1/inspirations` | 搜索、标签、日期、排序和分页 |
| POST | `/api/v1/inspirations` | 创建灵感 |
| GET | `/api/v1/inspirations/{id}` | 获取灵感 |
| PATCH | `/api/v1/inspirations/{id}` | 带版本号更新 |
| DELETE | `/api/v1/inspirations/{id}?version=n` | 软删除 |
| POST | `/api/v1/inspirations/{id}/restore` | 恢复软删除记录 |
| POST | `/api/v1/inspirations/{id}/ai-generations` | AI 生成预览 |
| GET | `/api/v1/tags` | 标签列表 |
| POST | `/api/v1/tags` | 创建标签 |
| PATCH | `/api/v1/tags/{id}` | 带版本号更新标签 |
| DELETE | `/api/v1/tags/{id}` | 安全删除标签 |

统一错误格式：

```json
{
  "error": {
    "code": "错误代码",
    "message": "可读错误信息",
    "details": null
  },
  "request_id": "请求追踪 UUID"
}
```

## 10. MCP stdio 部署

### 10.1 准备用户上下文

先通过前端注册用户，再从 MySQL 查询该用户 ID：

```sql
SELECT id, email FROM users;
```

在后端 `.env` 配置：

```dotenv
MCP_USER_ID=用户UUID
MCP_LOCAL_TOKEN=本机高熵随机令牌
```

### 10.2 启动

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.mcp.server
```

MCP 使用 stdio，因此客户端需要以子进程方式启动该命令。stdout 只能输出 MCP 协议消息，日志写入 stderr。

### 10.3 MCP Tools

| Tool | 作用 |
|---|---|
| `inspiration_search` | 搜索灵感 |
| `inspiration_get` | 获取单条灵感 |
| `inspiration_create` | 创建灵感 |
| `inspiration_update` | 更新灵感 |
| `inspiration_delete` | 软删除，需要 `confirm=true` |
| `inspiration_restore` | 恢复灵感 |
| `tag_list` | 查询标签 |
| `tag_create` | 创建标签 |
| `tag_update` | 更新标签 |
| `tag_delete` | 删除标签，需要 `confirm=true` |
| `ai_generate` | 生成摘要或扩展预览 |

每次 Tool 调用都必须提供与 `MCP_LOCAL_TOKEN` 一致的 `local_token`。当前版本为了避免认证绕过，不开放 MCP Resource。

## 11. 测试与验收

### 11.1 后端

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
ruff check .
mypy app
pytest -q
```

当前基准：12 个后端测试全部通过。

### 11.2 前端

```powershell
cd frontend
npm run lint
npm run type-check
npm run test
npm run build
```

当前基准：12 个前端测试全部通过，生产构建成功。

### 11.3 本地部署验收清单

- [ ] MySQL80 状态为 Running。
- [ ] `alembic current` 显示 `0001_initial (head)`。
- [ ] `/health/live` 返回 200。
- [ ] `/health/ready` 返回 200。
- [ ] 前端首页返回 200。
- [ ] 用户可注册和登录。
- [ ] 用户之间的数据互相不可见。
- [ ] 灵感创建、搜索、编辑、删除和恢复正常。
- [ ] 标签创建和筛选正常。
- [ ] AI 生成接口返回 `provider` 或明确的 `fallback`。
- [ ] AI 结果只有在用户确认后才被保存。
- [ ] 后端 Ruff、mypy、pytest 通过。
- [ ] 前端 lint、类型检查、测试和构建通过。

## 12. 数据备份与恢复

备份时让 `mysqldump` 交互式询问密码，不要把密码写在命令行：

```powershell
mysqldump -u inspiration -p --single-transaction --routines --triggers `
  --default-character-set=utf8mb4 inspiration_assistant `
  > inspiration_assistant_backup.sql
```

恢复到已创建的空数据库：

```powershell
mysql -u inspiration -p --default-character-set=utf8mb4 `
  inspiration_assistant < inspiration_assistant_backup.sql
```

恢复后执行：

```powershell
cd backend
alembic current
alembic upgrade head
```

## 13. 安全要求

- `.env`、数据库密码、JWT_SECRET 和 AI_API_KEY 不得提交 Git。
- MySQL 业务账号不得授予全局权限。
- 本地服务默认绑定 `127.0.0.1`，不要直接绑定公网地址。
- 不要在日志中输出 JWT、API Key、数据库密码或完整隐私正文。
- AI 输出可能不准确，必须预览后再保存。
- 删除操作使用版本号和确认机制，避免陈旧页面覆盖新数据。
- 当前登录失败退避保存在单进程内存中；多 Worker 或多实例部署必须改为 Redis 等共享限流存储。
- MCP 仅用于可信本机客户端，并为每次 Tool 调用校验 `local_token`。

## 14. 常见故障

### 14.1 `/health/ready` 返回 503

检查：

```powershell
Get-Service MySQL80
Test-NetConnection 127.0.0.1 -Port 3306
```

随后核对 `.env` 的数据库名、账号、密码和 URL 编码。

### 14.2 MySQL 1045 Access denied

原因通常是账号、密码或允许登录的 host 不匹配。使用管理员账号检查：

```sql
SELECT user, host FROM mysql.user WHERE user = 'inspiration';
SHOW GRANTS FOR 'inspiration'@'localhost';
```

### 14.3 后端拒绝启动并提示 JWT_SECRET

`.env` 仍为空、过短或使用占位值。重新生成至少 32 字节的随机密钥。

### 14.4 AI 始终显示 fallback

确认以下三项全部存在：

- `AI_PROVIDER_URL`
- `AI_API_KEY`
- `AI_MODEL`

再确认 API Endpoint 兼容 `choices[0].message.content` 返回结构，并查看后端日志是否存在网络或格式错误。

### 14.5 前端无法连接后端

确认：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Get-Content frontend\.env.local
```

前端代理目标应为 `http://127.0.0.1:8000`。

### 14.6 端口占用

```powershell
Get-NetTCPConnection -LocalPort 5173,8000 -State Listen
```

关闭对应开发进程或为 Vite/Uvicorn 指定其他端口，并同步修改代理配置。

## 15. 当前限制与后续建议

- JWT 只有访问令牌，没有刷新令牌和主动注销列表。
- 搜索使用 MySQL `LIKE`，数据量变大后应升级全文或向量检索。
- AI API 仅适配 Chat Completions 格式；其他返回格式需要新增 Provider Adapter。
- 登录限流仅适合单进程开发部署。
- MCP 仅支持 stdio Tools，尚未提供带认证上下文的 Streamable HTTP。
- 前端主包体积约 1.09 MB，后续可使用路由懒加载和 Element Plus 按需导入优化。
- 正式部署前应增加 HTTPS、反向代理、进程守护、集中日志、自动备份和监控告警。

