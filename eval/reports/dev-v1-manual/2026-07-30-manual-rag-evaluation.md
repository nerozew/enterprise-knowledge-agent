# dev-v1 真实 Provider 人工 RAG 测试记录

> 状态：开发集人工冒烟与消融记录，不是 P10 正式盲测报告，不可直接作为简历最终指标。

## 1. 实验身份

| 字段 | 记录 |
|---|---|
| 日期 | 2026-07-30 |
| Git 分支 | `codex/enterprise-rag-upgrade` |
| Git Commit | `0b3d7086d10e8357f77f6b29134df564a7205b30` |
| 数据集 | `eval/dev-v1` |
| 语料 | 8 份 Markdown 企业制度文档 |
| 人工评测 Workspace | `RAG评测-dev-v1` |
| Workspace ID | `4a790b5e-3e9d-45d0-a387-4a4cc56de656` |
| 向量数据库 | Milvus |
| Embedding | `text-embedding-v4`，远程 OpenAI-compatible Provider |
| Reranker | `qwen3-rerank`，Alibaba Qwen3 Profile |
| LLM | `qwen-plus`，Alibaba Qwen JSON Profile |
| 默认 Top K | 5 |
| 默认 Candidate K | 30 |
| RRF k | 60 |
| Weighted 权重 | Dense 0.65 / BM25 0.35 |
| Evidence 最低最终分 | 0.015 |
| Evidence 最低词法覆盖率 | 0.2 |

### 记录限制

- 当前数据来自人工单次或少量页面测试，没有完成每个配置 3～5 次重复。
- 页面总耗时是单次观察值，不能据此计算稳定的 P50/P95。
- 未记录的字段统一标记为“未记录”，禁止反向推断。
- 不同算法的原始分数尺度不同，RRF、Weighted、Reranker 分数不能直接横向比较大小。
- 当前开发集已经用于调试和发现失败案例，不属于独立盲测集。

## 2. 已确认的评分链路

Milvus Hybrid + RRF + Rerank 路径：

```text
Dense/BM25 通道
→ 通道融合
→ 查询排序 RRF
→ Reranker
→ 最终排序分
→ Evidence Gate
```

启用 Reranker 时，当前最终分公式为：

```text
final_score = 0.4 × query_fusion_score + 0.6 × rerank_score
```

RRF 结果是排名融合值，不是置信度或正确率。

## 3. 早期 Hybrid + RRF 基线冒烟

共同配置：

- Retrieval：Hybrid；
- Fusion：RRF，`k=60`；
- Top K：5；
- Candidate K：30；
- Reranker：关闭；
- Query Rewrite：关闭；
- Multi Query：关闭；
- Context Expansion：关闭；
- Response Mode：HITS。

| 编号 | 问题 | 预期文档/行为 | 实际结果 | 单次总耗时 | 判定 |
|---|---|---|---|---:|---|
| B-01 | 普通生产权限最长可以保留多久？ | `security-access`，30 天 | `security-access` 第 1 | 未记录 | 正确召回 |
| B-02 | 公司电脑丢了应该多久内告诉哪些人？ | `remote-work`，30 分钟内通知 IT 服务台和直属负责人 | `remote-work` 第 1 | 376.9 ms | 正确召回；后续发现词法门禁误拒绝 |
| B-03 | Break Glass 权限有效时间和事后补充材料期限是什么？ | `security-access`，2 小时、24 小时 | `security-access` 第 1 | 322.9 ms | 正确召回 |
| B-04 | 公司的春节放假日期是哪几天？ | 应拒答 | 返回了无关候选；后续 Answer 模式由模型拒答 | 约 322 ms | 检索候选不相关；门禁能力不足 |

早期页面曾把 Milvus Hybrid 第一层融合分错误显示为 `Dense`。该显示问题后来已修复，因此早期截图中的 `Dense 0.033` 应解释为“通道融合 RRF”，不能作为原始 Dense 相似度。

## 4. Reranker 人工消融

共同配置：

- Retrieval：Hybrid；
- Fusion：RRF，`k=60`；
- Top K：5；
- Candidate K：30；
- Query Rewrite：关闭；
- Multi Query：关闭；
- Context Expansion：关闭；
- Response Mode：HITS。

### R-01：精确事实问题，Reranker 开启

问题：

```text
普通生产权限最长可以保留多久？
```

| 字段 | 结果 |
|---|---|
| 正确文档 | `security-access` |
| 最终排名 | 1 |
| 通道融合 RRF | 0.033，排名 1 |
| 查询排序 RRF | 0.016，排名 1 |
| Reranker | 0.961，排名 1 |
| 最终排序分 | 0.583，排名 1 |
| 单次总耗时 | 3298 ms |
| 结论 | 正确证据保持第一；Reranker 高相关 |

公式复核：

```text
0.4 × 0.016 + 0.6 × 0.961 ≈ 0.583
```

### R-02：同义口语问题，Reranker 成功但门禁误拒绝

问题：

```text
公司电脑丢了应该多久内告诉哪些人？
```

| 字段 | 结果 |
|---|---|
| 正确文档 | `remote-work` |
| 最终排名 | 1 |
| 最终排序分 | 0.558 |
| 单次总耗时 | 833 ms |
| Evidence Gate | 拒绝 |
| 拒绝代码 | `lexical_coverage_below_threshold` |
| 判定 | False Refusal |

分析：问题使用“电脑丢了、告诉”，证据使用“设备遗失、通知”。Embedding 与 Reranker 能识别语义关系，但基于中文二元词组重合的词法覆盖率低于 0.2，造成错误拒答。

### R-03：接近原文的最小对照

问题：

```text
设备遗失后，应在多少分钟内通知哪些人？
```

| 字段 | 结果 |
|---|---|
| 正确文档 | `remote-work` |
| 最终排名 | 1 |
| 通道融合 RRF | 0.033，排名 1 |
| 查询排序 RRF | 0.016，排名 1 |
| Reranker | 0.950，排名 1 |
| 最终排序分 | 0.576，排名 1 |
| 单次总耗时 | 882 ms |
| Evidence Gate | 通过 |
| 判定 | 正确通过 |

公式复核：

```text
0.4 × 0.016 + 0.6 × 0.950 ≈ 0.576
```

R-02 与 R-03 形成最小问法对照：正确文档都排第 1，但只有接近原文的表达通过词法门禁，确认当前主要问题在 Evidence Gate，而不是召回。

### R-04：真正无答案问题

问题：

```text
公司的春节放假日期是哪几天？
```

| 字段 | 结果 |
|---|---|
| 预期 | 拒答 |
| 实际 | 拒答 |
| 拒绝代码 | `lexical_coverage_below_threshold` |
| 第一候选 | `expense-travel`，无关 |
| 通道融合 RRF | 0.015，原排名 19 |
| 查询排序 RRF | 0.013，原排名 19 |
| Reranker | 0.394，重排排名 1 |
| 最终排序分 | 0.242，最终排名 1 |
| 单次总耗时 | 566 ms |
| 判定 | True Refusal |

公式复核：

```text
0.4 × 0.013 + 0.6 × 0.394 ≈ 0.242
```

重要解释：候选总会产生第一名，但第一名不代表存在可回答证据。Reranker 把弱候选中的 `expense-travel` 排到第一，Evidence Gate 最终阻止了错误回答。

### R-05：多来源问题，Reranker 开启

问题：

```text
安全审计日志需要保存多长时间？
```

Gold：

1. `data-retention`，相关度 3；
2. `security-access`，相关度 2。

| 文档 | 通道融合 | 查询排序 | Reranker | 最终分/排名 |
|---|---|---|---|---|
| `data-retention` | 0.033，排名 2 | 0.016，排名 2 | 0.902，排名 1 | 约 0.548，排名 1 |
| `security-access` | 0.033，排名 1 | 0.016，排名 1 | 0.698，排名 2 | 0.426，排名 2 |

单次总耗时：未记录。

观察：两份相关文档都进入 Top 5。Reranker 没有提高此题的 Recall@5，但把主要证据 `data-retention` 从第 2 提升到第 1，改善了 graded ranking。

### R-06：同题关闭 Reranker

| 文档 | 通道融合 | 查询排序 | 最终排名 |
|---|---|---|---:|
| `security-access` | 0.033，排名 1 | 0.016，排名 1 | 1 |
| `data-retention` | 约 0.033，排名 2 | 约 0.016，排名 2 | 2 |

| 字段 | 结果 |
|---|---|
| 两份相关文档 Recall@5 | 2/2 = 100% |
| 主要证据 `data-retention` 排名 | 2 |
| 主要证据 Reciprocal Rank | 0.5 |
| 单次总耗时 | 268 ms |

与 R-05 对比：开启 Reranker 后主要证据 Reciprocal Rank 从 0.5 提升到 1.0。该结论只适用于当前单题，不能外推为开发集总体提升。

## 5. RRF 与 Weighted 融合对照

共同配置：

- Retrieval：Hybrid；
- Top K：5；
- Candidate K：30；
- Reranker：关闭；
- Query Rewrite：关闭；
- Multi Query：关闭；
- Context Expansion：关闭；
- Response Mode：HITS。

问题：

```text
安全审计日志需要保存多长时间？
```

| 配置 | 第 1 名 | 第 2 名 | 两份相关文档 Recall@5 | 单次总耗时 |
|---|---|---|---:|---:|
| RRF，k=60 | `security-access` | `data-retention` | 100% | 268 ms |
| Weighted，0.65/0.35 | `data-retention` | `security-access` | 100% | 308 ms |

Weighted 主要证据明细：

| 字段 | 结果 |
|---|---|
| 文档 | `data-retention` |
| 通道加权融合 | 0.893，排名 1 |
| 查询排序 RRF | 0.016，排名 1 |
| 最终排名 | 1 |

观察：在这一道多来源问题上，Weighted 与 Reranker 都把主要证据排到第一；RRF 与 Weighted 的分数尺度不同，不能比较 0.893 与 0.033 的大小。单次延迟差 40 ms，不足以形成性能结论。

### W-02：Weighted 补充样本

问题：

```text
公司电脑丢失后应在多长时间内报告给谁？
```

实际截图链路为 `MILVUS_HYBRID + WEIGHTED`，并不是原计划的 Dense 单通道测试。

| 字段 | 结果 |
|---|---|
| 正确文档 | `remote-work` |
| 最终排名 | 1 |
| 通道加权融合 | 0.829，排名 1 |
| 查询排序 RRF | 0.016，排名 1 |
| 最终排序分 | 0.016，排名 1 |
| 第二名 | `data-retention` |
| 单次总耗时 | 330 ms |
| 判定 | Weighted 正确召回；Dense 单通道实验仍未执行 |

## 6. Dense 单通道测试

### D-01：同义表达正确召回，但词法门禁误拒绝

问题：

```text
公司电脑丢失后应在多长时间内报告给谁？
```

配置：

- Retrieval：Dense；
- Top K：5；
- Candidate K：30；
- Reranker：关闭；
- Query Rewrite：关闭；
- Multi Query：关闭；
- Context Expansion：关闭；
- Response Mode：HITS。

| 字段 | 结果 |
|---|---|
| 实际链路 | `MILVUS_DENSE` |
| 正确文档 | `remote-work` |
| 最终排名 | 1 |
| Dense 语义相似度 | 0.639，排名 1 |
| 查询排序 RRF | 0.016，排名 1 |
| 最终排序分 | 0.016，排名 1 |
| 单次总耗时 | 340 ms |
| Evidence Gate | 拒绝 |
| 拒绝代码 | `lexical_coverage_below_threshold` |
| 判定 | Dense 召回成功；Evidence Gate False Refusal |

解释：`0.016 > 0.015`，所以分数门禁已经通过。系统随后还会检查当前 Workspace 的发布版本与 Source Span 是否可追踪，并计算原问题与最多 3 条候选证据的词法覆盖率。问题使用“电脑丢失、报告给谁”，证据使用“设备遗失、通知 IT 服务台和直属负责人”，二元词组重合不足 0.2，因此在第三层门禁被拒绝。

该结果再次确认：当前失败点不在 Dense Embedding，而在只依赖词面重合的 Evidence Gate。

## 7. Sparse/BM25 单通道测试

### S-01：正确文档进入 Top 2，但错误 Chunk 排在正确证据之前

问题：

```text
公司电脑丢失后应在多长时间内报告给谁？
```

配置：

- Retrieval：Sparse/BM25；
- Top K：5；
- Candidate K：30；
- Reranker：关闭；
- Query Rewrite：关闭；
- Multi Query：关闭；
- Context Expansion：关闭；
- Response Mode：HITS。

| 字段 | 结果 |
|---|---|
| 实际链路 | `MILVUS_SPARSE` |
| 正确文档 | `remote-work` |
| Top 1 Chunk | `终端设备`，不能回答报告时限与报告对象 |
| Top 1 关键词相关度 | 6.165 |
| Top 1 查询排序 RRF | 0.016 |
| 正确证据 Chunk | `网络与遗失` |
| 正确证据排名 | 2 |
| 正确证据关键词相关度 | 4.114，排名 2 |
| 正确证据查询排序 RRF | 0.016，排名 2 |
| 单次总耗时 | 275 ms |
| Evidence Gate | 通过，没有出现证据不足 |
| 判定 | 文档级 Recall@1 命中，但 Chunk/Span 级排名为 2 |

解释：Top 1 的“终端设备”片段包含“公共电脑”等直接词面，因此 BM25 给出较高关键词相关度；真正包含“设备遗失后 30 分钟内通知 IT 服务台和直属负责人”的“网络与遗失”片段只排第 2。这说明只按文档 ID 评估会高估检索质量，后续正式评估必须以相关 Chunk/Source Span 为 ground truth。

Evidence Gate 没有拒绝，是因为当前词法覆盖率按前几条可追溯候选证据的词法特征并集计算，而不是只检查 Top 1。Top 1 提供“电脑”等词面，Top 2 提供“30 分钟、通知、负责人”等回答证据，聚合覆盖率达到阈值。该结果能够回答问题，但也说明当前门禁没有验证“单个支持片段是否独立支撑答案”，后续需要用 Answerability 输出明确的 supporting span。

单次延迟上 Sparse 为 275 ms、Dense 为 340 ms，但样本量只有一次，当前不能据此声称 Sparse 性能更好。

### S-02：Sparse 实验答案调用失败

在 S-01 相同检索配置下切换到实验答案，前端显示：

```text
当前未配置可用的大模型，无法生成实验性答案。
```

故障发生时 `/health/capabilities` 返回：

```text
provider.mode = REMOTE
provider.components.embedding = true
provider.components.reranker = true
provider.components.llm = true
provider.profiles.llm = ALIBABA_QWEN
```

因此本次不能判定为 LLM 配置缺失。后端当前把“LLM 对象不存在”和“已配置 LLM 调用失败”都映射成 `llm_dependency_unavailable`，前端再把该错误码统一显示为“未配置大模型”。当前更准确的判定是：LLM 已配置，但答案生成调用失败，具体原因可能位于上游 HTTP、超时或结构化 JSON 解析，需要保留底层阶段化错误后才能继续定位。

随后使用单条明确证据直接探测答案 Provider，得到：

```text
provider_created: True OpenAICompatibleLLMProvider
success: StructuredAnswer(status='refused', text='', cited_hit_ids=())
```

后续改用 UTF-8 文件版脚本，并输出 Unicode 转义，确认原文问题、同义改写和证据在 Python 内部均为完整中文。两条请求都得到：

```text
ProviderUnavailableError: chat completion JSON does not match the required schema
ValueError: $.status is not an allowed value
```

因此中文编码没有问题，当前根因是 `ALIBABA_QWEN` 使用 JSON Object 模式时只保证返回 JSON，不会执行项目本地 JSON Schema 中的枚举约束。模型返回的 `status` 值不是后端要求的英文小写 `answered` 或 `refused`，被本地严格校验拒绝。需要先查看原始 `status`，再通过强化 Prompt、一次有限结构修复或安全的显式枚举归一化解决，不能把该错误继续显示成“未配置大模型”。

原始响应进一步确认：

```json
{
  "status": "success",
  "text": "设备遗失或疑似被盗后，员工应在30分钟内通知IT服务台和直属负责人。",
  "cited_hit_ids": ["test-span-1"]
}
```

答案事实和引用均正确，唯一不兼容字段是 `status="success"`。内部规范要求 `answered/refused`，因此适配器应在严格校验前仅把已经观测并测试过的 `success` 显式规范化为 `answered`；规范化后仍必须执行非空答案、已知引用和拒答不得携带引用等现有约束。该案例不属于 Answerability False Refusal，而是 Provider Schema Compatibility Failure。

加入精确 Prompt 约束和 `success -> answered` 的 Alibaba grounded-answer 适配后，真实 Provider 对照复验结果：

| 用例 | 状态 | 答案 | 引用 |
|---|---|---|---|
| 原文措辞 | `answered` | 30 分钟内通知 IT 服务台和直属负责人 | `test-span-1` |
| 同义改写 | `answered` | 30 分钟内报告给 IT 服务台和直属负责人 | `test-span-1` |

该最小链路已经通过。不过新增的自动化回归测试最初被错误缩进到另一个测试函数内部，pytest 未收集；必须调整为模块级独立测试并确认收集数从 21 增加到 22，才能完成代码级回归门禁。

## 8. Query Rewrite 稳定性记录

配置：

- Hybrid + RRF；
- Reranker 开启；
- Query Rewrite 开启；
- Multi Query 关闭。

页面/API 观察：

```text
503
200
503
503
```

当前小样本请求成功率：

```text
1 / 4 = 25%
```

独立调用 `qwen-plus` Rewrite Provider 5 次：

| 次数 | Provider 请求 | 有效改写 |
|---:|---|---|
| 1 | 成功 | 空 |
| 2 | 成功 | 空 |
| 3 | 成功 | 空 |
| 4 | 成功 | `unknown query`，无效 |
| 5 | 成功 | 空 |

结论：

- API Key、URL 和模型并非持续不可用；
- 当前 Query Rewrite 没有稳定产生有效中文改写；
- 完整搜索中的 Provider 失败被统一映射为 503，无法区分 Rewrite、Embedding 或 Reranker；
- Query Rewrite 当前判定为“已接入但未通过可靠性门禁”，后续测试暂时关闭；
- 需要增加分阶段错误、有限重试、无效改写过滤、失败降级和独立 Rewrite 模型配置。

在强化 grounded-answer Prompt 后另行执行一次 UTF-8 原始 Rewrite 探测，得到：

```json
{"queries":["公司电脑丢失报告时限和责任人"]}
```

`queries` 为非空 `list[str]`，且改写不同于原问题，符合 `query_rewrite` Schema。这证明 Rewrite 的字段名、JSON 类型、模型 URL 和 `/chat/completions` Endpoint 在该次调用中正确。不过浏览器完整搜索仍返回 503，因此当前剩余问题可能是间歇性 Rewrite 输出、后续 Embedding 调用或统一错误包装造成的不可观测性，不能继续归因为接口名称错误。

随后连续执行 5 次原始 Rewrite 探测。在前端关闭 Multi Query 的情况下，业务调用使用 `max_queries=1`，但模型返回的数组长度依次为：

```text
3, 1, 4, 4, 1
```

所有响应的 `queries` 类型都是 `list[str]`，但只有第 2、5 次满足 `maxItems=1`。其余 3 次会在 `_chat_json` 的本地 Schema 校验阶段触发 `$.queries has too many items`，并被搜索服务统一包装成 503。该组探测得到结构合规率 `2/5 = 40%`、超量率 `3/5 = 60%`，解释了 Query Rewrite 的间歇性失败。修复方向是把最大改写数明确写入 Prompt，并在 Alibaba JSON Object 适配边界对已验证为字符串数组的超量结果确定性截断后再执行 Schema 校验。

### Q-02：Multi Query 已执行，但无答案问题被错误放行

问题：

```text
公司员工如何请假？
```

当前 8 份开发语料不包含请假制度。请求使用 `HITS`、Hybrid + RRF、Query Rewrite 与 Multi Query，Reranker 关闭。完整响应显示：

| 字段 | 结果 |
|---|---|
| `scoring.query_count` | 3 |
| `components` | `MILVUS_HYBRID`、`RRF`、`QUERY_REWRITE` |
| Query Fusion | `applied=true`，`k=60` |
| Top 1 | `remote-work / 网络与遗失`，与请假制度无关 |
| Top 1 最终分 | 0.048395 |
| `refusal` | `null` |
| 总耗时 | 1734.3 ms |
| 判定 | Query Rewrite/Multi Query 确实执行；Evidence Gate False Acceptance |

Top 1 的三个查询贡献分别为：

```text
query_index=0: rank=2, 1/(60+2)=0.016129
query_index=1: rank=3, 1/(60+3)=0.015873
query_index=2: rank=1, 1/(60+1)=0.016393
合计：0.048395
```

这证明本次不是只执行了原问题：系统实际运行了原问题和两条改写，共 3 次检索，再通过 Query-level RRF 合并。响应顶层的 `query` 只是保留原始用户输入，当前 API 没有返回改写文本，因此只能证明“执行了三条查询”，不能从该响应复核两条改写的实际措辞和质量。

该案例同时暴露静态证据分阈值的尺度问题：单查询 RRF Top 结果约为 `0.016`，三查询累加后无关但反复出现的候选可达到约 `0.048`。若仍用固定 `RAG_EVIDENCE_MIN_SCORE=0.015`，多查询会天然更容易通过基础分门禁。后续应让门禁使用与 `query_count` 无关的规范化分数，或校准不同检索配置的独立阈值，并增加 Answerability 判定。API 还应增加受权限保护的 `query_trace`，返回本次实际使用的 Original/Rewrite 文本，支持复核与消融实验。

### Q-03：Query Trace、归一化门禁与 Grounded Answerability 修复

针对 Q-02 已完成最小修复：

1. `SearchRequest` 新增默认关闭的 `include_query_trace`。
2. 显式开启检索诊断后，`SearchResponse.query_trace` 返回实际进入检索的 Original/Rewrite 文本；索引与 `score_details.*.query_index` 对齐。
3. 未显式开启时不返回查询正文；Query Trace 不持久化，也不写入应用日志。
4. 多查询门禁分改为 Query contribution mean：

```text
normalized_query_score = raw_query_fusion_score / actual_query_count
```

5. 有 Reranker 时只归一化 Query Fusion 部分：

```text
gate_score = 0.4 × normalized_query_score + 0.6 × rerank_score
```

6. 原始 `hit.score`、候选顺序、分阶段排序分和 Reranker 公式保持不变。
7. `HITS` 模式不调用 Answer LLM，并固定标记为 `NOT_EVALUATED`；它只表示候选检索，不再声明问题已有可靠答案。
8. `ANSWER` 模式先执行可追溯性和归一化分数硬门禁；词法覆盖率降为诊断信号。通过硬门禁后复用现有一次 Grounded Answer LLM 调用完成可回答性判断和答案生成：
   - `answered` → `SUPPORTED`；
   - `refused` → `REJECTED`；
   - Provider 故障或未知状态 → 脱敏 503，fail closed。
9. 响应增加原始排序分、归一化门禁分、门槛、词法覆盖率、词法阈值和通过状态；前端明确提示这些值不是正确率或置信度。
10. Qwen JSON Object 返回 `status=success` 的已观测兼容分支已恢复为模块级自动化测试，并继续执行非空答案、已知引用等严格校验。

自动化回归结果：

```text
Backend Ruff: passed
Backend Mypy: passed, 62 source files
Backend Pytest: 131 passed, 3 skipped
Frontend ESLint: passed
Frontend Type Check: passed
Frontend Vitest: 98 passed
Frontend Build: passed
Reviewer: S1=0, S2=0, S3=0
```

自动化回归完成时尚未执行真实 Alibaba Qwen + Milvus 手动复测，因此当时不能声称 Q-02 的“请假”案例已经在真实 Provider 上正确拒绝。后续手动复测项目为：

- `HITS`：应显示三条实际查询、候选片段和 `NOT_EVALUATED`，不得显示“已证明可回答”；
- `ANSWER`：应由 Grounded Answerability 返回 `REJECTED/model_refused`；
- “公司笔记本找不到了该怎么办？”：即使词法覆盖率不足，只要候选包含直接证据，`ANSWER` 应返回 `SUPPORTED` 和合法引用。

### Q-04：真实 Alibaba Qwen + Milvus 请假问题复测

修复后使用真实远程 Provider 和 Milvus 重新测试：

```text
Original: 公司员工如何请假？
Rewrite 1: 员工请假流程
Rewrite 2: 公司请假政策和手续
```

检索与门禁诊断：

| 字段 | 结果 |
|---|---|
| 实际查询数 | 3 |
| 原始排序分 | 0.048 |
| 归一化门禁分 | 0.016 |
| 分数阈值 | 0.015 |
| 词法覆盖率 | 28.6% |
| 词法阈值 | 20.0% |
| 词法预检 | 通过 |
| Top 1 候选 | `remote-work / 网络与遗失`，与请假制度无直接关系 |
| Grounded Answerability | `REJECTED` |
| 拒答代码 | `model_refused` |
| 检索耗时 | 2163.6 ms（另一次页面显示约 2086 ms） |
| 总耗时 | 3966.5 ms |

结论：

- Query Rewrite 的两条中文改写真正进入了三路检索，Query Trace 与 `query_count=3` 一致。
- Query mean 归一化消除了三查询线性累加导致的 `0.048` 分数尺度放大；门禁实际比较约为 `0.016`。
- 本案例的分数门禁和词法门禁均通过，说明启发式预检本身仍不足以证明可回答。
- Grounded Answer LLM 在第二层识别出候选没有直接提供请假制度，返回 `model_refused`；系统没有生成答案，候选片段仅保留用于核对。
- 该案例验证了“检索排序/低成本预检”和“最终语义可回答性”必须分层，不能把 RRF 分数或关键词覆盖率当成答案正确率。
- 单条成功拒答案例只能作为功能联调证据，不能据此声称整体拒答准确率；仍需在版本化无答案问题集上统计拒答准确率、误拒率和错误放行率。

### Q-05：真实 Alibaba Qwen + Milvus 同义正向问题复测

问题与实际改写：

```text
Original: 公司笔记本找不到了该怎么办？
Rewrite 1: 公司笔记本丢失处理流程
Rewrite 2: 企业资产笔记本遗失应对措施
```

结果：

| 字段 | 结果 |
|---|---|
| 实际查询数 | 3 |
| 原始排序分 | 0.049 |
| 归一化门禁分 | 0.016 |
| 分数阈值 | 0.015 |
| 词法覆盖率 | 8.3% |
| 词法阈值 | 20.0% |
| 词法预检 | 未通过 |
| Top 1 | `remote-work / 网络与遗失` |
| Grounded Answerability | `SUPPORTED` |
| Supporting Hit | Top 1 `remote-work / 网络与遗失` |
| 检索耗时 | 1691.9 ms |
| 总耗时 | 3851.2 ms |

生成答案：

```text
公司笔记本找不到了，员工应在 30 分钟内通知 IT 服务台和直属负责人，
以便远程锁定、撤销会话并评估数据影响。
```

结论：

- 检索返回的固定发布版本片段直接包含“设备遗失或疑似被盗后，30 分钟内通知 IT 服务台和直属负责人”，答案和引用一致。
- 问题使用“笔记本找不到了”，证据使用“设备遗失或疑似被盗”，词面重合不足导致 lexical coverage 只有 8.3%。
- 旧的 lexical 硬门禁会产生 False Refusal；修复后 ANSWER 模式将 lexical coverage 降为诊断，允许 Grounded Answerability 进行语义判断，最终正确返回 `SUPPORTED`。
- Q-04 与 Q-05 构成一组正反对照：无请假证据时拒答，有设备遗失直接证据时即使词法预检失败也能回答。
- 该结果证明链路行为符合设计，但尚不能量化 Query Rewrite 的独立增益；下一步必须对同一问题关闭 Rewrite 运行基线，比较 Source Span 排名、支持状态和延迟。

### Q-06：同一正向问题关闭 Query Rewrite 的消融对照

问题保持不变：

```text
公司笔记本找不到了该怎么办？
```

配置：

```text
Hybrid + RRF
Reranker: OFF
Query Rewrite: OFF
Multi Query: OFF
Context Expansion: OFF
Response Mode: ANSWER
```

实际查询只有 Original：

```text
公司笔记本找不到了该怎么办？
```

与 Q-05 的对照结果：

| 指标 | Rewrite ON（Q-05） | Rewrite OFF（Q-06） | 变化 |
|---|---:|---:|---:|
| Query Count | 3 | 1 | -2 |
| 正确 Source Span 排名 | 1 | 1 | 无变化 |
| Answerability | `SUPPORTED` | `SUPPORTED` | 无变化 |
| 原始排序分 | 0.049 | 0.016 | 分数尺度随查询数变化 |
| 归一化门禁分 | 0.016 | 0.016 | 无变化 |
| 词法覆盖率 | 8.3% | 8.3% | 无变化 |
| 检索耗时 | 1691.9 ms | 237.6 ms | -1454.3 ms |
| 总耗时 | 3851.2 ms | 2177.1 ms | -1674.1 ms |

相对延迟：

```text
Rewrite ON 检索耗时约为 OFF 的 7.12 倍
Rewrite ON 总耗时约为 OFF 的 1.77 倍
```

结论：

- 对该问题，原始 Query 已能把正确 Source Span 排在第 1，Rewrite 没有提高排名、Answerability 或答案正确性。
- Rewrite 增加了一次 LLM 改写、两次额外 Embedding 和两路额外检索，带来明显延迟。
- 原始排序分从 0.016 增加到 0.049 只是三查询 RRF contribution 累加，不代表可信度提升。
- 两组归一化门禁分均为 0.016，说明 Query mean 修复消除了 Query 数量造成的门禁尺度漂移。
- 该单题支持“简单问题可跳过 Rewrite”的工程假设，但不能据此永久关闭 Rewrite；仍应在困难同义问法、缩写、术语歧义和多跳问题上运行完整消融集，再决定 AUTO 路由策略。

### Q-07：困难口语问题 Query Rewrite 开关消融

问题：

```text
临时救火权限能开多久，结束后多久补材料？
```

预期证据为 `security-access / 生产系统访问` 中的 Break Glass 临时权限条款：

```text
临时权限最长保留 2 小时；
故障结束后 24 小时内补充事故编号、执行命令摘要和影响说明。
```

除 Query Rewrite/Multi Query 开关外，两组均使用 Hybrid + RRF、Reranker OFF、
Context Expansion OFF 和 ANSWER 模式。

Rewrite ON 的实际检索查询：

```text
Original: 临时救火权限能开多久，结束后多久补材料？
Rewrite 1: 临时救火权限有效期及材料补交时限
Rewrite 2: 临时救火权限时长与后续材料提交要求
```

对照结果：

| 指标 | Rewrite OFF | Rewrite ON | 变化 |
|---|---:|---:|---:|
| Query Count | 1 | 3 | +2 |
| 正确 Source Span 排名 | 1 | 1 | 无变化 |
| Answerability | `SUPPORTED` | `SUPPORTED` | 无变化 |
| 原始排序分 | 0.016 | 0.049 | 分数尺度随查询数变化 |
| 归一化门禁分 | 0.016 | 0.016 | 无变化 |
| 分数阈值 | 0.015 | 0.015 | 无变化 |
| 词法覆盖率 | 26.7% | 26.7% | 无变化 |
| 词法阈值 | 20.0% | 20.0% | 无变化 |
| 检索耗时 | 310.2 ms | 1636.6 ms | +1326.4 ms |
| 总耗时 | 2288.5 ms | 3626.3 ms | +1337.8 ms |

相对延迟：

```text
Rewrite ON 检索耗时约为 OFF 的 5.28 倍
Rewrite ON 总耗时约为 OFF 的 1.58 倍
```

结论：

- 两条改写均围绕“权限有效期”和“材料补交时限”，语义保持正确，没有出现偏题或无效改写。
- 原始口语 Query 已能将正确 Source Span 排在第 1，并通过词法预检和 Grounded Answerability；本题开启 Rewrite 没有带来 Recall、排名或答案支持状态增益。
- 原始排序分由 0.016 增加到 0.049 是三查询贡献累加，不是置信度提升；归一化门禁分保持 0.016，再次验证 Query mean 的尺度稳定性。
- Rewrite 带来约 1326.4 ms 的额外检索耗时和约 1337.8 ms 的额外端到端耗时。该单次耗时只能作为功能联调样本，正式性能结论仍需重复 3～5 次并报告 P50/P95。
- Q-06 与 Q-07 两个正向问题均在 Rewrite OFF 时 Top 1 命中，说明当前样例尚未证明 Rewrite 的独立召回增益。下一轮应使用“原始 Query 排名较低或未进入 Top K、Rewrite 后提升”的困难样例，否则不能把 Rewrite 收益写入简历。

### Q-08：灰度回滚口语问题 Query Rewrite 开关消融

问题：

```text
小流量试水时，错误率比平时高多少就必须撤回？
延迟持续多久也得停止？
```

预期证据为 `release-change / 灰度与回滚`：

```text
错误率相对基线增加 1 个百分点，
或 P95 延迟连续 10 分钟超过既定 SLO 时，
应停止扩量并执行回滚。
```

Rewrite ON 的实际检索查询：

```text
Original: 小流量试水时，错误率比平时高多少就必须撤回？延迟持续多久也得停止？
Rewrite 1: 小流量试水错误率阈值
Rewrite 2: 小流量试水延迟容忍时长
```

对照结果：

| 指标 | Rewrite OFF | Rewrite ON | 变化 |
|---|---:|---:|---:|
| Query Count | 1 | 3 | +2 |
| 正确 Source Span 排名 | 1 | 1 | 无变化 |
| Answerability | `SUPPORTED` | `SUPPORTED` | 无变化 |
| 原始排序分 | 0.016 | 0.049 | 分数尺度随查询数变化 |
| 归一化门禁分 | 0.016 | 0.016 | 无变化 |
| 分数阈值 | 0.015 | 0.015 | 无变化 |
| 词法覆盖率 | 22.2% | 22.2% | 无变化 |
| 词法阈值 | 20.0% | 20.0% | 无变化 |
| 检索耗时 | 397.8 ms | 1468.7 ms | +1070.9 ms |
| 总耗时 | 2496.7 ms | 3182.6 ms | +685.9 ms |

相对延迟：

```text
Rewrite ON 检索耗时约为 OFF 的 3.69 倍
Rewrite ON 总耗时约为 OFF 的 1.27 倍
```

检索结论：

- 两条改写分别提炼“错误率阈值”和“延迟容忍时长”，正确覆盖了原问题的两个子意图。
- 原始 Query 已将正确 Source Span 排在第 1；Rewrite 没有改善 Recall、排名、门禁结果或 Answerability。
- 原始排序分从 0.016 增加到 0.049 仍是三查询 RRF contribution 累加；归一化门禁分稳定为 0.016。
- Rewrite 增加约 1070.9 ms 检索耗时。本次 ANSWER 模式的总耗时还包含生成模型波动，不能用单次总耗时精确归因。

答案忠实度发现：

- Rewrite OFF 的答案保留了完整条件：“P95 延迟连续 10 分钟超过既定 SLO”。
- Rewrite ON 的答案简化成“延迟持续 10 分钟也得停止”，遗漏了“P95”和“超过既定 SLO”两个关键限定条件。
- 两组证据片段相同且均为 Top 1，因此该差异不能归因于召回结果；更可能来自生成模型单次采样或当前答案约束不足。
- 页面显示“已通过直接证据支持检查”只能证明引用的固定版本片段存在且被答案引用，不能单独证明答案中每个事实、数值和条件都被证据完整蕴含。
- 该案例应进入 Faithfulness/Claim Entailment 失败集。后续评估需要把答案拆成原子主张，分别核验数值、比较关系、时间范围、主体和触发条件，统计 Citation Correctness 与 Claim Support Rate。

### Q-09：灰度回滚答案忠实度重复测试

为判断 Q-08 中遗漏 `P95` 和 `超过既定 SLO` 是否为可重复缺陷，在相同 Rewrite ON
配置下连续重复运行 5 次。

必要事实检查表：

| 必要事实 | 5 次结果 |
|---|---:|
| 错误率相对基线增加 | 5/5 |
| 阈值为 1 个百分点 | 5/5 |
| 指标为 P95 延迟 | 5/5 |
| 连续 10 分钟 | 5/5 |
| 超过既定 SLO | 5/5 |
| 停止扩量并执行回滚 | 5/5 |
| 正确 Source Span 为 Top 1 | 5/5 |

5 次答案均表达了完整规则：

```text
错误率相对基线增加 1 个百分点，
或 P95 延迟连续 10 分钟超过既定 SLO 时，
必须停止扩量并执行回滚。
```

本轮结果：

```text
必要事实完整率 = 30 / 30 = 100%
完整答案通过率 = 5 / 5 = 100%
```

结论：

- Q-08 首次观察到的条件遗漏在连续 5 次重复测试中未复现，因此当前证据不支持把它判定为确定性的 Rewrite 缺陷。
- 更合理的分类是“单次生成波动/偶发忠实度异常”：检索片段始终正确，但答案模型曾有一次遗漏限定条件。
- `5/5` 是本次小样本点估计，只能用于联调记录，不能写成系统整体 100% 忠实度；正式结果必须在版本化题集上统计并给出样本量和置信区间。
- 原始异常仍保留在失败案例库，用于未来 Claim Entailment 回归测试，避免只报告成功样例。

## 9. 当前阶段性结论

### 已得到支持的结论

1. `text-embedding-v4 + Milvus Hybrid` 能在多条人工问题上召回正确文档。
2. Reranker 能在“安全审计日志”多来源问题上把主要证据从第 2 提升到第 1。
3. Weighted 在同一多来源问题上也把主要证据从第 2 提升到第 1。
4. 当前 Evidence Gate 能正确拒绝春节日期这类无答案问题。
5. 当前 Evidence Gate 会错误拒绝语义正确但词面不同的口语问题。
6. Query Rewrite 曾因 Provider 契约和结构化输出问题出现 503；修复后近期手工样例已能稳定暴露有效中文改写，但尚未完成重复运行与全量开发集验证，暂不能声称整体稳定。
7. Dense 单通道已把 `remote-work` 排到第 1，语义相似度为 0.639，但仍被词法门禁误拒绝。
8. Sparse/BM25 文档级命中第 1，但正确证据 Chunk 排第 2；文档级指标会掩盖片段级排序错误。
9. Sparse 的 Evidence Gate 通过，说明聚合多个候选的词法覆盖率可以弥补单个片段的词面不足，但也不能证明 Top 1 本身可回答。
10. 三个 Rewrite 开关消融样例中，Rewrite 文本语义正确，但正确 Source Span 在关闭 Rewrite 时已经排第 1；目前只观察到额外延迟，尚未观察到独立召回增益。
11. “灰度回滚”首次运行曾遗漏 `P95 超过既定 SLO` 的关键条件；随后相同配置连续 5 次均完整回答，说明该问题当前表现为偶发生成波动，而不是已确认的确定性 Rewrite 缺陷。

### 尚不能声称

- 不能声称 Reranker 在整个数据集上提高了多少百分点；
- 不能声称 Weighted 总体优于 RRF；
- 不能用单次延迟声称某配置性能更好；
- 不能把当前 24 题开发集结果当成 100 题正式盲测结果；
- 不能把 RRF、Weighted 或 Reranker 分数称为置信度；
- 不能声称 Query Rewrite 已稳定实现。

## 10. 已发现失败案例

| ID | 类型 | 描述 | 后续处理 |
|---|---|---|---|
| F-01 | False Refusal | 同义口语问题正确文档排第 1，但词法覆盖率不足导致拒答 | 引入 Answerability/语义证据门禁，重新校准阈值 |
| F-02 | Query Rewrite Reliability | Rewrite 搜索间歇性 503 | 分阶段错误、重试、降级 |
| F-03 | Query Rewrite Quality | 5 次探测未产生有效中文改写，出现 `unknown query` | 强化 Prompt、模型拆分、无效输出过滤 |
| F-04 | Weak Candidate Promotion | 无答案问题中 Reranker 把无关候选从第 19 提升到第 1 | 不能把 Top 1 当成可回答；保留独立 Evidence Gate |
| F-05 | Document/Chunk Metric Mismatch | Sparse 返回的 Top 1 与正确证据来自同一文档，但 Top 1 Chunk 无法回答问题 | 正式评估按 Source Span 标注相关性，同时保留文档级指标 |
| F-06 | Misleading LLM Error | LLM capability 显示已配置，但答案 Provider 调用失败被前端显示为“未配置” | 拆分 `llm_not_configured`、`llm_provider_unavailable`、`llm_invalid_payload` 与超时错误 |
| F-07 | LLM Schema Enum Mismatch | Qwen 返回事实和引用均正确的 `status=success`，内部契约只接受 `answered/refused` | Prompt 明确枚举；适配器将已验证的 `success` 显式规范化为 `answered`，保留全部引用校验 |
| F-08 | Multi-query False Acceptance | 无请假制度的语料中，三查询 RRF 累加把无关候选推至 0.048，静态 0.015 门禁被放行 | 规范化 Query Fusion、按配置校准阈值、增加 Answerability，并暴露受控 `query_trace` |
| F-09 | Citation Entailment Gap（偶发，5 次复测未复现） | 答案引用正确 Source Span，但一次把“P95 延迟连续 10 分钟超过既定 SLO”简化为“延迟持续 10 分钟”；随后相同配置 5/5 完整回答 | 保留为回归样例；增加原子主张抽取与逐主张证据蕴含检查，统计 Claim Support Rate、Citation Correctness，并对数值与条件做确定性校验 |

## 11. 下一步测试

1. 对 Dense、Sparse、Hybrid RRF、Hybrid Weighted 各运行完整 24 题；
2. 同时计算文档级与 Source Span 级 Recall@K、MRR、nDCG@K；
3. 每个性能配置重复 3～5 次；
4. 修复 Answerability 与 Query Rewrite 后重新运行同一开发集；
5. 调参完成后冻结独立 100 题正式盲测集。
