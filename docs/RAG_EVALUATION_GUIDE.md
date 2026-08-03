# 企业知识库 RAG 评测实施指南

## 1. 评测目标

本项目的评测不是为了得到一个孤立的“准确率”，而是回答以下问题：

1. 文档是否被正确解析、切分、发布和索引？
2. 正确证据能否进入 Top K？
3. 正确证据能否尽量排在前面？
4. 引用能否定位到固定文档版本和 Source Span？
5. 没有证据时，系统能否正确拒答？
6. Reranker、Query Rewrite、Context Expansion 是否带来可量化收益？
7. 收益需要付出多少延迟、Token 和费用？
8. 在并发、依赖故障、权限隔离和版本切换时，系统是否仍然可靠？

所有结果必须来自可复现的原始报告。计划值、FIXTURE 结果和单次手工演示不得写成简历成果。

## 2. 当前已有资产与限制

当前 `eval/dev-v1` 包含：

- 8 份合成企业制度文档；
- 20 个可回答问题；
- 4 个应拒答问题；
- 文档级 qrels；
- 原文 quote / Source Span 标注；
- 固定语料、问题集 Hash 和随机种子。

当前 `eval/reports/dev-v1-fixture` 中的 Dense、Hybrid 和 Hybrid + Rerank 报告使用 FIXTURE Embedding/Reranker，只能用于：

- 检查评测程序能否运行；
- 防止指标计算逻辑回归；
- 检查报告字段是否完整；
- 检查失败案例是否能够落盘。

这些 FIXTURE 数字不能用于简历。正式数据必须至少使用：

- 真实 Milvus；
- 真实 BM25 Analyzer；
- 真实 Embedding；
- 真实 Reranker（对应实验开启时）；
- 固定 Git Commit、配置、语料和问题集；
- 隔离 Workspace；
- 可复现原始报告。

## 3. 总体测试流程

| 阶段 | 测试内容 | 目的 | 主要产物 |
|---|---|---|---|
| T0 | 冻结版本与环境 | 保证实验可复现 | Commit、配置快照、模型与机器信息 |
| T1 | 基础设施与摄取冒烟 | 证明完整链路可运行 | 健康检查、摄取任务记录 |
| T2 | 摄取质量 | 验证四格式解析、切分、版本和幂等 | 文档/Chunk 统计、失败案例 |
| T3 | 小规模人工检索 | 在自动评测前排除明显链路错误 | 逐题命中与引用截图 |
| T4 | 开发集自动评测 | 建立真实 Provider 基线 | dev-v1 原始报告 |
| T5 | 检索消融实验 | 证明每项优化的独立价值 | 对照实验表 |
| T6 | 可信度与拒答评测 | 验证引用、门禁和拒答 | Citation/Refusal 指标 |
| T7 | 答案质量评测 | 验证答案正确且有证据支持 | Correctness/Faithfulness |
| T8 | 性能、成本与稳定性 | 测量延迟、吞吐、费用和故障行为 | 压测与成本报告 |
| T9 | 安全与隔离 | 验证无越权、无草稿泄露 | 权限矩阵测试报告 |
| T10 | 100 题正式盲测 | 产生可写入简历的最终数字 | 版本化正式报告 |

## 4. 每个阶段具体做什么

### T0：冻结实验身份

每次实验开始前记录：

- Git Commit；
- 数据集版本、语料 Hash、问题集 Hash；
- Embedding 模型、维度和服务地址类型；
- Reranker 模型与 Profile；
- LLM 模型、Prompt 版本；
- Chunk 策略、Chunk 大小、重叠、Parent-Child 参数；
- Retrieval Mode、Fusion、Candidate K、Top K；
- Evidence Gate 阈值；
- Python、Milvus、MySQL 版本；
- CPU、内存、操作系统；
- 日期、随机种子；
- 是否冷启动、并发数和重复次数。

作用：如果结果变好或变差，可以确定是代码、模型、数据还是参数发生了变化。

### T1：基础设施和完整链路冒烟

检查：

- MySQL、Redis、MinIO、Milvus 健康；
- Embedding、Reranker、LLM 配置状态；
- 文件上传成功；
- Knowledge Worker 能领取任务；
- 文档状态从 QUEUED 经过解析、切分、索引到 PUBLISHED；
- 问题能返回固定版本引用；
- 服务重启后仍能检索。

作用：只证明系统能运行，不产生准确率结论。

### T2：摄取质量

使用 PDF、DOCX、Markdown、TXT 各准备若干固定文件，验证：

- 解析成功率；
- 标题、章节、页码、字符范围是否正确；
- Parent/Child Chunk 数量；
- Source Span 完整率；
- 重复上传是否复用或明确创建新版本；
- 新版本索引失败时旧发布版本是否仍可检索；
- 失败任务能否重试；
- MinIO/Milvus 产物是否能通过补偿恢复；
- 草稿和未发布版本是否不会进入检索。

作用：如果摄取阶段已经丢失正文或页码，后续检索指标没有意义。

### T3：小规模人工检索

先用 5～10 个已知答案检查：

- 正确文档是否进入 Top 5；
- 正确 Chunk 是否进入 Top 5；
- 页面显示的版本、Span 和原文是否一致；
- 无答案问题是否拒答；
- 删除、未发布或无权限内容是否不会出现。

作用：在运行批量实验前发现 UUID 映射、向量维度、版本激活、权限过滤等链路缺陷。

### T4：真实 Provider 开发基线

将 `dev-v1` 的 8 份文档摄取到隔离 Workspace，使用真实 Provider 运行 24 题。

至少记录：

- 逐题候选文档、Chunk、Source Span；
- Dense/Sparse/Fusion/Rerank 分数；
- Evidence Gate 决策；
- 拒答原因；
- Embedding、Milvus、Reranker、LLM 分段耗时；
- 总耗时；
- 失败堆栈和重试次数。

作用：建立后续优化的真实基线。dev-v1 可用于调试和调参，但不能作为最终简历盲测成绩。

### T5：检索消融实验

消融实验必须一次只改变一个变量，其他条件保持一致。

| 编号 | 配置 | 主要回答的问题 |
|---|---|---|
| A | Dense，Top K=5 | 纯语义召回基线有多强？ |
| B | BM25，Top K=5 | 精确关键词和编号检索有多强？ |
| C | Hybrid + RRF | Dense 与 BM25 组合是否提高召回和排序？ |
| D | Hybrid + Weighted | Weighted 与 RRF 哪个更适合当前数据？ |
| E | Hybrid + RRF + Reranker | Reranker 是否把正确证据排得更靠前？ |
| F | E + Parent/Neighbor Context | 上下文扩展是否提高多事实答案和引用覆盖？ |
| G | E + Query Rewrite | 改写是否改善口语化、缺省上下文问题？ |
| H | G + Multi Query | 多查询是否进一步提高困难题召回？ |

对照时重点观察：

- `Recall@5` 的变化：有没有找回来；
- `MRR@10` 的变化：正确结果有没有更靠前；
- `nDCG@10` 的变化：多个不同相关度证据的排序是否更合理；
- Source Span Recall 的变化：有没有找到正确原文位置；
- 拒答 F1 的变化：是否减少错误回答和错误拒答；
- P95 延迟和成本变化：质量提升是否值得。

示例结论形式：

> 在相同语料、Embedding、Top K 和随机种子下，Hybrid 相比 Dense 的 Recall@5 提升 X 个百分点；加入 Reranker 后 MRR@10 提升 Y 个百分点，但 P95 延迟增加 Z ms。

其中 X、Y、Z 必须来自原始报告，不能预先填写。

### T6：引用、证据门禁与拒答

应评估：

- Citation Span Completeness：返回命中是否都有版本和 Span；
- Citation Correctness：引用原文是否真正支持答案；
- Citation Coverage：答案中的关键事实是否都有引用；
- Refusal Precision：系统拒答的问题中，有多少确实应该拒答；
- Refusal Recall：所有应该拒答的问题中，有多少被正确拒答；
- Refusal F1；
- False Refusal：明明有证据却被门禁拒绝的题数；
- Unsafe Answer Rate：没有证据却仍生成答案的比例。

作用：证明系统不是“任何问题都回答”，而是能够基于证据回答并在证据不足时拒答。

### T7：答案质量

只在检索质量稳定后测试答案生成。

每题人工标注：

- 关键事实是否正确；
- 是否遗漏多事实问题中的部分答案；
- 是否出现证据外信息；
- 每个引用是否支持对应陈述；
- 数字、时间、否定和条件是否准确；
- 拒答理由是否合理。

建议同时报告：

- Answer Correctness；
- Faithfulness / Groundedness；
- Citation Precision；
- Citation Recall；
- 人工复核一致率。

LLM-as-Judge 可以用于辅助，但正式报告应人工抽检，且保存 Judge 模型和 Prompt。

### T8：性能、成本和稳定性

分别测量：

- 冷启动与热启动；
- 并发 1、5、10；
- 每个问题重复 3～5 次；
- P50、P95、P99；
- Embedding、Milvus、Reranker、LLM 分段耗时；
- QPS、错误率、超时率；
- 单题平均 Token；
- 每 1000 题估算费用；
- Redis/MinIO/Milvus 短暂不可用时的错误、重试和恢复；
- SSH 隧道中断后的行为。

检索延迟和答案生成延迟必须分开报告，否则无法判断慢在检索还是 LLM。

### T9：权限与数据隔离

必须达到的硬门槛：

- 跨 Workspace 命中数为 0；
- VIEWER 无法看到未发布版本状态；
- 草稿检索命中数为 0；
- 删除文档不再出现在普通检索；
- 历史引用仍固定到原发布版本；
- 新版本失败时继续使用旧发布版本；
- 日志不包含正文、Token 和密钥。

这类测试不是“准确率优化”，而是企业项目的安全门禁。

### T10：100 题正式人工盲测

正式集应独立于 dev-v1，在调参完成后冻结，禁止根据正式测试失败案例继续调参后仍宣称其为盲测。

建议题型：

| 题型 | 数量 |
|---|---:|
| 精确事实与数字 | 20 |
| 同义改写与口语问法 | 20 |
| 多事实问题 | 15 |
| 多来源问题 | 10 |
| 阈值、条件、否定问题 | 15 |
| 不可回答与越界问题 | 20 |
| 合计 | 100 |

每题至少标注：

- `case_id`；
- 问题；
- 是否可回答；
- 文档级 qrels；
- 正确 Source Span；
- 标准答案和关键事实；
- 题型标签；
- 标注人；
- 复核状态。

## 5. 指标怎样理解

| 指标 | 含义 | 高说明什么 |
|---|---|---|
| Recall@5 | 正确证据是否出现在前 5 | 不容易漏答案 |
| MRR@10 | 第一个正确结果的倒数排名均值 | 正确证据更靠前 |
| nDCG@10 | 考虑相关度等级的排序质量 | 多来源排序更合理 |
| Span Recall@5 | 正确原文位置是否进入前 5 | 引用定位能力更强 |
| Citation Correctness | 引用是否支持对应答案 | 引用不是装饰 |
| Refusal Precision | 拒答中真正该拒答的比例 | 少错误拒答 |
| Refusal Recall | 应拒答问题被拒答的比例 | 少无依据回答 |
| False Refusal Rate | 可回答问题被错误拒答比例 | 门禁不过度保守 |
| P95 Latency | 95% 请求在该时间内完成 | 尾部体验更稳定 |
| Error Rate | 请求失败比例 | 系统更可靠 |

RRF 分数和向量相似度是排序信号，不是准确率或可信概率，不应直接写成“可信度 98%”。

## 6. 建议门槛

以下只是第一版验收目标，不是当前已实现成绩：

| 类别 | 建议初始门槛 |
|---|---:|
| Recall@5 | ≥ 0.85 |
| MRR@10 | ≥ 0.75 |
| Span Recall@5 | ≥ 0.80 |
| Citation Correctness | ≥ 0.90 |
| Refusal F1 | ≥ 0.80 |
| False Refusal Rate | ≤ 0.10 |
| Citation Span Completeness | 1.00 |
| 跨 Workspace / 草稿泄露 | 0 |
| 受控四格式摄取成功率 | 1.00 |

延迟门槛应在第一次真实 Provider 基线后，结合网络和模型服务能力确定。

## 7. 每次实验必须保存的文件

```text
eval/reports/<dataset>/<run-id>/
├── evaluation_manifest.json
├── metrics.json
├── retrieval_results.jsonl
├── answer_results.jsonl
├── failures.jsonl
├── latency_breakdown.json
└── cost_summary.json
```

- `evaluation_manifest.json`：版本、模型、参数、Hash、机器和时间；
- `metrics.json`：聚合指标；
- `retrieval_results.jsonl`：逐题候选、分数、版本和 Span；
- `answer_results.jsonl`：答案、引用和人工评分；
- `failures.jsonl`：所有漏召回、错误拒答、错误引用和超时；
- `latency_breakdown.json`：分组件延迟；
- `cost_summary.json`：Token 和估算费用。

## 8. 消融结果记录模板

| 配置 | Recall@5 | MRR@10 | Span Recall@5 | Refusal F1 | Citation Correctness | P95 检索 | P95 端到端 | 单题成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Dense | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| BM25 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| Hybrid + RRF | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| Hybrid + RRF + Rerank | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| + Context | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| + Rewrite | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |
| + Multi Query | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 | 待测 |

## 9. 简历表述模板

在正式报告产生前不得填写数字。最终可以按以下格式改写：

> 构建 Dense + BM25 + RRF + Reranker 的企业知识检索链路，在 100 题人工标注盲测集上将 Recall@5 从 A 提升至 B、MRR@10 从 C 提升至 D；实现发布版本与 Source Span 固定引用，引用正确率达到 E，拒答 F1 达到 F；在并发 G 下检索 P95 为 H ms。

其中 A～H 必须能追溯到同一 Git Commit 对应的版本化原始报告。

## 10. 后续实际执行顺序

1. 使用当前 `dev-v1` 做真实 Milvus/Embedding 冒烟；
2. 完成真实 Provider Evaluation Runner；
3. 运行 Dense、BM25、Hybrid 三组基线；
4. 加入 Reranker 做第一轮消融；
5. 加入 Context、Rewrite、Multi Query 做后续消融；
6. 校准 Evidence Gate，分析错误拒答；
7. 评测答案正确性、忠实度和引用；
8. 执行并发与故障测试；
9. 冻结 100 题正式盲测集；
10. 运行最终实验并生成简历数据。
