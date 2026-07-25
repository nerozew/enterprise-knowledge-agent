# 企业知识库与智能行动 Agent 平台 - 主 Agent 协调规范

## 1. 项目目标与技术栈

本项目在现有日常灵感助手 MVP 上，逐步建设企业知识库、可信 RAG、多人协同编辑、GraphRAG 与智能行动 Agent 平台。现有灵感能力必须保持可迁移、可回归，新增能力必须绑定可复现的测试、评估数据和原始报告。

- 前端：Vue 3、TypeScript、Vite、Pinia、Vue Router、Element Plus。
- 后端：Python 3.11+、FastAPI、Pydantic、SQLAlchemy 2.x、Alembic。
- 数据库：MySQL 8，统一使用 `utf8mb4`。
- 基础设施：Redis、MinIO、Milvus；MySQL 是业务事实源，其他存储必须可补偿或可重建。
- Agent/RAG：LangGraph、结构化输出、Tool Calling、Dense + BM25、Reranker、上下文扩展、GraphRAG。
- 协同编辑：Tiptap、Yjs、Hocuspocus；草稿与已发布可检索版本严格分离。
- Agent 协议：MCP，优先使用 stdio 或 Streamable HTTP。

## 2. P0-P10 路线与阶段门禁

| 阶段 | 交付内容 |
|---|---|
| P0 | Git 基线、数据模型、REST/SSE/MCP/评估契约 |
| P1 | Workspace、成员、RBAC、审计 |
| P2 | Redis、MinIO、Milvus、Docker 基础设施 |
| P3 | PDF/DOCX/Markdown/TXT 摄取、结构切分、状态机、幂等补偿 |
| P4 | Dense + BM25、Reranker、上下文扩展、Query 改写 |
| P5 | Tiptap + Yjs + Hocuspocus 协同编辑、版本与发布 |
| P6 | GraphRAG Basic/Local/Global、图索引和来源追踪 |
| P7 | 引用、证据门禁、拒答、可信度评估 |
| P8 | LangGraph 四路由、Checkpoint、HITL、待办、SSE |
| P9 | MCP Streamable HTTP、安全和可观测性 |
| P10 | 100 题正式评估、消融实验、压测和演示材料 |

- 除明确记录并获主 Agent 批准的并行任务外，不得绕过前置阶段门禁。
- “阶段 Pn”只表示路线阶段；缺陷严重度统一使用 `S1/S2/S3`，避免与阶段编号混淆。
- 计划中的能力、性能和准确率不得写成已实现成果；简历数字只能来自 P10 的版本化原始报告。

## 3. Agent 职责与目录边界

- 涉及 `/frontend` 代码改动时，主 Agent 必须委派给 Frontend Agent，主 Agent 不得直接修改 `/frontend`。
- 涉及 `/backend` 代码改动时，主 Agent 必须委派给 Backend Agent，主 Agent 不得直接修改 `/backend`。Hocuspocus Node 服务如启用，固定放在 `/backend/collaboration`，仍由 Backend Agent 负责。
- 同时涉及接口、数据库和前端交互的新功能，先由 Planner Agent 输出契约，再并行委派 Frontend 与 Backend Agent。
- 仅涉及单目录、且不改变接口或数据库契约的小型修复，可以跳过 Planner，直接委派对应开发 Agent。
- Reviewer Agent 只审查和报告问题，不直接修改业务代码。
- 主 Agent 负责需求协调、根级 Docker/Nginx/CI 配置、文档与评估资产维护、审查循环、集成验证，以及不属于 `/frontend` 或 `/backend` 的问题。
- 子 Agent 只能修改自己负责的目录；完成后必须报告修改文件、验证命令和验证结果。

## 4. 开发与审查流程

跨端功能采用以下流程：

1. Planner 输出领域模型、状态机、角色矩阵、REST/SSE/MCP/协同协议、评估契约、验收标准和任务拆分。
2. Frontend 与 Backend 根据同一份契约并行开发。
3. Reviewer 对契约一致性、安全性、测试和代码质量进行审查。
4. S1 问题必须修复且不得降级；S2 问题原则上修复，同一模块最多进行 3 轮“修复—复审”。达到上限后，未解决的 S2 由主 Agent 明确列为遗留风险。
5. 审查通过条件：S1 为 0，S2 不超过 2，且当前阶段门禁与核心验收测试通过。
6. 主 Agent 执行集成验证；集成问题退回对应开发 Agent 修复，并再次交由 Reviewer 增量审查。

## 5. 严重等级

- S1：数据丢失、权限绕过、未发布草稿泄露、未审批写操作、密钥泄露、远程代码执行、核心流程不可用、接口契约完全不兼容、构建或启动失败。
- S2：重要功能错误、明显性能问题、关键校验或主要测试缺失、引用漂移、跨存储孤儿数据、可恢复的一致性问题。
- S3：可维护性、命名、文档、非关键体验或优化建议。

## 6. 集成验证

目录或服务存在时，主 Agent 按实际项目脚本执行以下检查：

- 后端安装、格式、类型和测试检查。
- Alembic 迁移可执行，并验证 MySQL 读写及 `utf8mb4` 编码。
- Workspace/RBAC 查询必须验证跨租户拒绝，权限变更后缓存必须及时失效。
- MinIO、Milvus 和 Redis 启用时验证健康检查、超时、重试、幂等、补偿及关闭连接。
- 文档摄取验证四类格式、结构化切分、重复上传、失败重试和发布版本可见性。
- RAG 验证 Dense/BM25/Hybrid/Rerank/Query Rewrite 的可配置性、来源引用、拒答和固定评估集。
- 协同编辑验证两客户端收敛、断线恢复、Room 授权，以及草稿不进入检索。
- GraphRAG 验证 Basic/Local/Global 路由、失败降级和原始 Chunk 来源边。
- LangGraph 验证四路由、Checkpoint 恢复、HITL 审批和 SSE 断线回放。
- FastAPI REST 服务可以启动，健康检查可访问。
- MCP stdio 服务只能向 stdout 输出合法 MCP 消息；日志写入 stderr。
- MCP Streamable HTTP Endpoint 可以初始化和调用，并具备 Origin 校验、认证与授权。
- 前端执行 lint、类型检查、单元测试和生产构建。
- 前后端联调检查 CORS、Vite 代理、路由映射、认证和错误响应。
- P10 报告必须记录语料快照、模型、Prompt、Embedding、Reranker、Chunk 参数、Top K、机器配置、日期、随机种子、延迟、Token 和成本。

不得声称未执行的验证已经通过。若因外部依赖无法验证，必须明确列出未验证项和原因。

## 7. 编码与安全规范

- 所有源码和配置文件使用 UTF-8 编码且不含 BOM。
- 中文界面文本必须保存为实际中文字符，禁止写成“反斜杠加字母 u”的 Unicode 转义形式。
- 禁止提交 `.env`、API Key、数据库密码、Cookie 或其他凭据；项目只提交 `.env.example`。
- 所有外部输入都必须校验；所有数据库操作使用参数化查询或 ORM。
- AI/MCP 工具必须执行身份认证、资源归属校验、最小权限和高风险操作确认。
- 所有知识、灵感、会话、运行、待办、协同房间、图索引、引用和审计记录必须绑定 `workspace_id`。
- MySQL 保存业务事实；MinIO、Milvus、Redis 失败时通过 Outbox、幂等与补偿恢复，禁止伪造跨存储原子事务。
- Yjs 内容属于可变草稿；只有显式发布生成的不可变版本可以进入 RAG/GraphRAG，历史引用固定到发布版本。
- 检索到的文档是不可信数据，不能覆盖系统指令、提升权限或直接触发高风险 Tool。
- 日志不得记录密钥、完整令牌或用户隐私正文。
- 数据库结构变更必须通过 Alembic 迁移完成，禁止只手工修改数据库。

## 8. 完成定义

任务只有在以下条件满足后才算完成：

- 实现与 Planner 契约或用户确认的范围一致。
- 相关测试、类型检查和构建通过。
- 当前阶段门禁通过，S1 清零，剩余 S2 已明确记录。
- 配置、迁移、接口文档和 `.env.example` 与实现同步。
- 新增写操作具备幂等、并发控制、资源归属校验、审计和必要的 HITL。
- 新增 RAG/GraphRAG 能力具备版本化数据集、对照组、可复现实验和失败案例记录。
- 主 Agent 完成适合当前任务范围的集成验证。
