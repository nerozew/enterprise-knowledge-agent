# 日常灵感智能助手后端

## 本地启动

需要 Python 3.11+ 与 MySQL 8。创建数据库时使用 `utf8mb4`：

```sql
CREATE DATABASE inspiration_assistant CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

复制 `.env.example` 为 `.env` 并替换数据库密码、JWT 密钥等本地配置，然后执行：

```bash
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

API 文档位于 `http://127.0.0.1:8000/docs`。存活与就绪检查分别为
`/health/live` 和 `/health/ready`。`/health/capabilities` 额外报告 Redis、
对象存储、向量存储与 RAG Provider 的可选能力状态；这些可选依赖失败不会让 P1
就绪检查失败。

## P2-P4 知识库与 RAG

安装四格式解析与本地开发依赖：

```bash
python -m pip install -e ".[dev]"
```

真实 Redis、MinIO、Milvus 适配器是可选依赖：

```bash
python -m pip install -e ".[dev,rag-infra]"
```

知识接口位于 `/api/v1/workspaces/{workspace_id}`：

- `POST /documents`：multipart 上传 PDF/DOCX/Markdown/TXT，要求
  `Idempotency-Key`，默认同步处理便于本地学习。
- `GET /documents`、`GET /documents/{id}/versions`：文档及不可变发布版本。
- `GET /ingestion-jobs/{id}`、`POST .../retry|cancel`：状态机与重试。
- `POST /search`：Dense、BM25、Hybrid、RRF/Weighted、Rerank、Rewrite
  和父/邻接上下文扩展。

`RAG_PROVIDER_MODE=FIXTURE` 只允许 test/development，用于确定性协议和回归，
不能把其 Recall、延迟或回答质量用于简历。`ANSWER` 在没有真实 LLM 时返回
`llm_dependency_unavailable`，不会使用模板伪造答案。生产环境必须配置私有对象
存储、真实向量库和受控 Provider。

## 测试与质量检查

```bash
ruff check .
mypy app
pytest
```

测试使用进程内 SQLite 替身，不需要真实 MySQL。发布前仍需在 MySQL 8 上执行迁移和集成测试。

## MCP stdio

先在环境变量中设置已有用户的 `MCP_USER_ID` 和高熵 `MCP_LOCAL_TOKEN`，再启动：

```bash
python -m app.mcp.server
```

stdio 的 stdout 仅用于 MCP 协议帧；日志写入 stderr。所有 MCP 工具都要求传入
`local_token`。软删除等高风险工具还要求 `confirm=true`。

当前版本不注册 MCP Resource。Resource 读取调用无法可靠携带并校验本地令牌，直接按
`MCP_USER_ID` 读取会形成认证绕过；因此仅开放逐次校验 `local_token` 的 Tools。待引入具备
认证上下文的 Streamable HTTP 后才能恢复 Resource。

## 登录失败退避

MVP 按客户端 IP 和规范化账号分别记录登录失败，并在连续失败后指数退避。该状态仅存于
当前进程内，适合单实例部署；多 Worker 或多实例生产环境必须改用 Redis 等共享存储，并在
可信反向代理层配置统一限流。服务只使用实际 TCP 客户端地址，不直接信任用户提供的
`X-Forwarded-For`。
