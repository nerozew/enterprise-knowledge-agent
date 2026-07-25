# 企业知识库与智能行动 Agent 平台

项目正在从 Vue 3、FastAPI、MySQL 与 MCP 构建的日常灵感助手 MVP，分阶段升级为包含企业知识库、可信 RAG、多人协同编辑、GraphRAG 与 HITL 行动闭环的平台。

> 当前代码基线为 `v0.1.0-mvp`。计划中的 Redis、MinIO、Milvus、文档摄取、协同编辑、GraphRAG、LangGraph 和评估能力尚未实现，不应当作现有功能使用或写入已完成成果。

当前 MVP 支持：

- 注册、登录和用户数据隔离。
- 灵感快速记录、编辑、组合搜索、软删除和恢复。
- 标签创建、编辑、筛选和安全删除。
- AI 摘要与扩展预览；未配置外部模型时提供确定性中文 fallback。
- 用户确认后保存 AI 结果，使用版本号防止并发覆盖。
- MCP stdio Tools，可供本机 Agent 调用。

升级工作在 `codex/enterprise-rag-upgrade` 分支进行：

- [P0-P10 升级路线](docs/upgrade/ROADMAP.md)
- [P0 冻结契约](docs/upgrade/P0_CONTRACT.md)

## 项目结构

```text
frontend/       Vue 3 + TypeScript + Vite
backend/        FastAPI + SQLAlchemy + Alembic + MCP
.codex/agents/  Planner、Frontend、Backend、Reviewer 配置
AGENTS.md       主 Agent 协调与质量门禁
```

## 1. 准备 MySQL 8

使用有建库权限的 MySQL 账号执行：

```sql
CREATE DATABASE inspiration_assistant
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'inspiration'@'localhost' IDENTIFIED BY '请替换为高强度密码';
GRANT ALL PRIVILEGES ON inspiration_assistant.* TO 'inspiration'@'localhost';
FLUSH PRIVILEGES;
```

不要把真实数据库密码提交到 Git。

## 2. 启动后端

进入 `backend`，复制 `.env.example` 为 `.env`，然后修改：

- `DATABASE_URL`：填入真实 MySQL 用户和密码。
- `JWT_SECRET`：使用密码学安全随机源生成至少 32 字节的高熵值。
- 可选 AI Provider 配置；保持为空时使用本地 fallback。

```powershell
cd backend
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

后端默认地址为 `http://127.0.0.1:8000`，API 文档位于 `/docs`。

## 3. 启动前端

另开一个终端：

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。Vite 默认把 `/api` 代理到后端的 `8000` 端口。

## 4. 运行质量检查

后端：

```powershell
cd backend
ruff check .
mypy app
pytest
```

前端：

```powershell
cd frontend
npm run lint
npm run type-check
npm run test
npm run build
```

## 5. 启动 MCP stdio

先在后端创建一个真实用户，再把该用户 ID 与本机高熵令牌写入后端 `.env`：

```dotenv
MCP_USER_ID=用户 UUID
MCP_LOCAL_TOKEN=本机高熵令牌
```

然后在 `backend` 中运行：

```powershell
python -m app.mcp.server
```

所有 MCP Tool 调用都必须提供匹配的 `local_token`；删除操作还需要显式设置 `confirm=true`。当前 MVP 不开放未认证的 MCP Resource。

完整本地部署、MySQL、AI API 和 MCP 规范见 [本地部署 SPEC](docs/SPEC.md)。更多实现细节见 [后端说明](backend/README.md) 和 [前端说明](frontend/README.md)。
