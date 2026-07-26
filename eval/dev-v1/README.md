# RAG 开发评估集 dev-v1

`dev-v1` 是用于验证摄取、租户隔离、发布版本、来源字段和检索消融的开发集，不是 P10 的
100 题盲测集，也不能作为简历准确率数字。

目录：

- `corpus/`：8 份完全虚构的企业制度与运行手册，使用固定 `document_key`。
- `cases.jsonl`：20 个可回答问题和 4 个不可回答问题。
- `manifest.json`：版本、语言、用途与评估约束。

运行前应由评估 Runner：

1. 在隔离 Workspace 中摄取 `corpus/`。
2. 把 `document_key` 映射到本次产生的 Document/Version/Chunk ID。
3. 依次执行 Dense、Parent-Child、Hybrid、Rerank、Context、Rewrite 消融。
4. 保存逐题命中、原始分数、组件耗时、Provider/Profile、Git Commit 和所有 Hash。

FIXTURE Provider 结果只用于流程回归。只有真实 Milvus、真实 BM25 Analyzer 和非 FIXTURE
Embedding Profile 的原始报告，才可用于讨论检索质量。
