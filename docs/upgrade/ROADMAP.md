# 企业知识库与智能行动 Agent 平台升级路线

## 1. 升级原则

当前 `v0.1.0-mvp` 是可运行的日常灵感助手基线。升级采用阶段门禁，不推倒重写：

- `main` 与 `v0.1.0-mvp` 保存初始版本。
- `codex/enterprise-rag-upgrade` 承载升级工作。
- 每个阶段先冻结契约，再并行开发，再审查与集成验证。
- MySQL 是业务事实源；Redis、MinIO、Milvus 失败必须可补偿或可重建。
- 所有资源强制 Workspace 隔离。
- 任何计划中的功能和数字，在实现与实测前不得写成完成成果。

## 2. P0-P10

| 阶段 | 交付内容 | 关键验收门 |
|---|---|---|
| P0 | Git 基线、领域模型、REST/SSE/MCP/评估契约 | 契约经 Reviewer 批准并记录版本；现有回归基线可复现 |
| P1 | Workspace、成员、RBAC、审计 | 现有数据迁入默认 Workspace；跨租户测试全部拒绝 |
| P2 | Redis、MinIO、Milvus、Docker | Compose 可启动；健康检查、数据卷、降级和恢复明确 |
| P3 | 四格式摄取、结构切分、状态机、幂等补偿 | PDF/DOCX/Markdown/TXT 全链路；重复上传不重复索引 |
| P4 | Dense + BM25、Reranker、上下文扩展、Query 改写 | 组件可配置；Baseline 与优化实验可复现 |
| P5 | Tiptap + Yjs + Hocuspocus、版本与发布 | 两客户端收敛；草稿不可检索；发布幂等且失败保留旧版本 |
| P6 | GraphRAG Basic/Local/Global 与来源追踪 | 图可重建；失败降级 Basic；答案引用回到发布 Chunk |
| P7 | 引用、证据门禁、拒答、可信度评估 | 引用版本固定；低证据拒答；不可回答集有报告 |
| P8 | LangGraph 四路由、Checkpoint、HITL、待办、SSE | 未审批写入为 0；重启可恢复；SSE 可回放 |
| P9 | MCP Streamable HTTP、安全与可观测性 | Origin、认证、Workspace、限流、审计与 Trace 通过 |
| P10 | 100 题评估、消融、压测、演示 | 原始数据、配置、结果、环境和真实数字齐全 |

## 3. 关键依赖

```text
P0 → P1 → P2 → P3 ┬→ P4 ─┐
                  └→ P5 ─┴→ P6 → P7 → P8 → P9 → P10
```

- P5 可以在 P3 发布模型冻结后，与 P4 的检索实验部分并行。
- P6 必须保留 P4 Basic 检索作为对照组和故障降级路径。
- P6 同时依赖 P4 的 Basic 基线和 P5 的发布边界。
- P7 同时依赖 P4 与 P6，统一 Basic/Local/Global 的引用、拒答和证据门禁；P4 可以先实现仅面向 Basic 的实验性门禁，但不能提前通过 P7。
- P8 的写工具只能在 P1 权限与 P7 证据语义稳定后接入。

## 4. RAG 优化实验顺序

| 实验 | 配置 |
|---|---|
| A | 固定 Chunk + Dense Top-5 |
| B | A + 结构化父子 Chunk |
| C | B + Dense/BM25 Hybrid |
| D | C + Reranker |
| E | D + 父章节与邻接扩展 |
| F | E + 条件式 Query 改写 |
| G | F + Graph Local/Global |
| H | G + 引用、证据门禁与拒答 |

每次实验必须记录语料快照、模型、Embedding、Reranker、Prompt、Chunk 参数、Top K、机器配置、日期、随机种子、Recall@5、MRR、nDCG、引用指标、Groundedness、拒答指标、延迟、Token 和成本。

## 5. 当前状态

- [x] `v0.1.0-mvp` Git 基线。
- [x] 升级分支。
- [x] P0 契约已冻结为 `p0-v1`。
- [x] P0 Reviewer 审查通过（S1=0，S2=0）。
- [ ] P1 Workspace/RBAC 实现。

## 6. P0 验证记录

验证日期：`2026-07-25`

- Agent TOML：Python `tomllib` 解析通过。
- 编码：本次变更文件均为 UTF-8 且无 BOM。
- Git：`git diff --check` 通过。
- 后端：Ruff 通过；mypy 检查 38 个源码文件通过；pytest 17 项通过。
- 前端：ESLint 通过；Vue TypeScript 检查通过；Vitest 9 个文件、16 项测试通过；Vite 生产构建成功。
- Reviewer：三轮增量复审后，S1=0、S2=0，P0 契约门禁通过。

已知非阻塞项：

- Vite 主包约 1.09 MB，后续阶段通过路由懒加载和依赖拆分优化。
- Rollup 对第三方 `@vueuse/core` 的 PURE 注释给出位置警告，构建仍成功。
