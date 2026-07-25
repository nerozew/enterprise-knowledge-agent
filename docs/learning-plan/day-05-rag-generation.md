# Day 05：检索增强生成与来源引用

## 今日目标

把 Retriever 与 LLM 连接起来，生成只基于检索上下文、带可靠引用且能拒绝无答案问题的回答。

## 今日时间表

1. 09:00-10:30：学习 RAG 输入输出和上下文构造。
2. 10:45-12:00：完成最小 Retrieval Chain。
3. 14:00-15:30：建立引用结构化输出。
4. 15:45-17:00：实现无答案拒答和上下文去重。
5. 17:00-18:00：测试事实问题、综合问题和域外问题。

## 学习策略

Day 5 是项目核心内容，不跳过以下检查点：

1. 最小 RAG 生成链：明确检索与生成的边界。
2. 上下文构造与预算：证据编号、排序、去重和长度控制。
3. Pydantic 结构化回答：答案、引用和证据充分性。
4. 服务端引用校验：模型只能引用本次检索到的 Chunk ID。
5. 无答案拒答：区分“没有召回”与“召回后证据不足”。
6. 三类测试与错误归因：事实、综合、域外问题。

可以加速的内容：

1. 已经掌握的 Milvus 连接、Embedding 和 `search()` 参数。
2. 重复的项目路径、环境变量和打印代码。
3. 暂不展开混合检索、Reranker 和 Query Rewrite，它们分别安排在 Day 8 和 Day 9。

## 必学知识

- `【非常重点】证据约束`：答案只能来自传入上下文，模型常识不能冒充文档事实。
- `【非常重点】引用可追溯`：引用必须关联真实返回的 Chunk ID，不能让模型自由编造来源。
- `【非常重点】检索和生成分开调试`：先检查召回，再检查生成，不能把所有错误都归因于 Prompt。
- `【重点】无答案拒答`：证据不足时返回“不知道”，不是强行生成。
- `【重点】上下文预算`：按相关度、去重和长度控制注入模型的内容。

## 检查点 1：最小 RAG 生成链

练习文件：`D:/Code_all/agent_test/langchain_demo/Day5/day_5.1.py`

Day 4 的向量检索只负责找到证据，检索结果不会自动进入 LLM。程序必须显式完成：

```text
用户问题
  -> embed_query()
  -> Milvus search()
  -> Top-K hits
  -> format_context()
  -> context 字符串
  -> ChatPromptTemplate
  -> LLM
  -> 带证据编号的回答或拒答
```

核心变量：

```text
hits      Milvus 返回的 Top-K 检索结果，包含主键、分数、正文和元数据
context   程序将 hits 格式化后的证据字符串
prompt    规定只能依据 context 回答、证据不足必须拒答
response  LLM 返回的 AIMessage
```

每个检索结果由服务端分配证据编号：

```text
[S1]
chunk_id: ...
source: leave-policy.pdf
page: 1
content: ...
```

事实问题实际结果：

```text
申请年假需要至少提前三个工作日提交申请，由直属主管完成审批；
若连续休假超过五个工作日，还需部门负责人复核 [S1]。
```

域外问题实际结果：

```text
问题：公司食堂几点开始供应午餐？
回答：根据当前知识库无法回答。
```

当前 `[S1]` 仍然是模型生成的文本标记。下一检查点会改为结构化引用，并由服务端校验引用的 Chunk ID，防止模型引用不存在的证据。

## 检查点 2：上下文构造、去重与预算

练习文件：`D:/Code_all/agent_test/langchain_demo/Day5/day_5.2.py`

检索结果不能不加处理地全部交给 LLM。服务端应先完成：

```text
Top-K hits
  -> 保持相似度顺序
  -> 按稳定 chunk_id 去重
  -> 分配本次请求内的证据编号 S1、S2……
  -> 控制上下文预算
  -> 生成 context 和 evidence_registry
```

本次运行结果：

```text
原始命中数量：3
最终证据数量：2
上下文字符数：270
```

第三条命中是代码故意加入的重复 Chunk。`build_context()` 根据 `chunk_id`
识别并跳过它。在单次纯向量检索中通常不会出现重复项，但将来组合向量检索、
关键词检索和 Reranker 时，同一 Chunk 可能从多条召回通道重复出现。

### 两个输出

```python
context, evidence_registry = build_context(hits_with_duplicate)
```

- `context: str`：真正传给 LLM 的证据文本。
- `evidence_registry: dict`：服务端保存的证据注册表，用来把 `S1`
  映射回真实 `chunk_id`、文档、页码和分数。

`S1` 只是当前请求中的短标签，不是数据库主键；稳定身份仍然是 `chunk_id`。
后续会用注册表验证模型引用的 `S1` 是否真实存在，防止模型编造引用。

### Python 语法

```python
seen_chunk_ids: set[str] = set()

if chunk_id in seen_chunk_ids:
    continue

seen_chunk_ids.add(chunk_id)
```

- `set()` 是集合，适合判断某个值是否已经出现。
- `in` 检查成员是否存在。
- `continue` 跳过本轮循环。
- `add()` 把新 ID 放入集合。

```python
hits_with_duplicate = [*hits, hits[0]]
```

`*hits` 是列表解包：先放入 `hits` 中的全部元素，再追加第一条命中。
这里仅用于模拟重复召回。

```python
if context_blocks and used_chars + block_chars > max_context_chars:
    break
```

`break` 结束整个循环。程序选择保留完整 Chunk，不把超出预算的 Chunk
从中间截断，以免破坏证据语义。

### 预算限制

`MAX_CONTEXT_CHARS` 和 `build_context()` 都是本项目自定义内容，不是
LangChain 或 Milvus 的固定 API。当前用 `len(block)` 统计 Python 字符数，
便于学习；生产环境应根据所用模型的 tokenizer 控制 Token 预算，因为
字符数不等于 Token 数。

## 建议响应结构

```python
class Citation(BaseModel):
    document_id: str
    chunk_id: str
    quote: str


class RagAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    has_sufficient_context: bool
```

## 检查点 3：Pydantic 结构化 RAG 输出

练习文件：`D:/Code_all/agent_test/langchain_demo/Day5/day_5.3.py`

官方参考：
`https://reference.langchain.com/python/langchain-core/language_models/chat_models/BaseChatModel/with_structured_output`

### 官方固定 API

```python
structured_llm = llm.with_structured_output(RagAnswer)
```

`with_structured_output()` 是 ChatModel 的官方方法。传入 Pydantic 类且
`include_raw=False`（默认值）时，`invoke()` 返回经过 Pydantic 验证的该类实例：

```text
结果类型：<class '__main__.RagAnswer'>
```

它不是立即调用模型，而是返回一个新的 Runnable：

```python
generation_chain = prompt | structured_llm
result = generation_chain.invoke({...})
```

### 本项目自定义 Schema

```python
class Citation(BaseModel):
    evidence_id: str
    quote: str


class RagAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    has_sufficient_context: bool
```

- `Citation` 是嵌套模型，表示一条引用。
- `citations: list[Citation]` 表示引用列表中的每个元素都必须符合
  `Citation` 的结构。
- `Field(description=...)` 为模型提供字段语义，也会进入输出 Schema。
- `result.model_dump()` 将 Pydantic 对象转换成普通 Python 字典。

### 实际结果

知识库内问题：

```python
RagAnswer(
    answer="申请年假需要至少提前三个工作日提交申请……",
    citations=[
        Citation(evidence_id="S1", quote="员工申请年假……")
    ],
    has_sufficient_context=True,
)
```

知识库外问题：

```python
RagAnswer(
    answer="根据当前知识库无法回答",
    citations=[],
    has_sufficient_context=False,
)
```

### 非常重要的边界

Pydantic 主要验证结构和类型，不会自动证明业务语义正确。例如：

- `evidence_id="S99"` 仍然是合法字符串，Pydantic 不知道它是否真实存在。
- `quote` 即使不是证据原文，也仍然是合法字符串。
- 模型漏掉统一拒答文字末尾的句号，字段类型依然合法。

因此，结构化输出不等于可信输出。下一检查点必须由服务端根据
`evidence_registry` 校验证据编号和原文，并统一规范拒答结果。

## 检查点 4：服务端引用校验

练习文件：`D:/Code_all/agent_test/langchain_demo/Day5/day_5.4.py`

这一检查点没有新的 LangChain 固定 API。`validate_rag_answer()`、
`CitationValidationError` 和 `REFUSAL_MESSAGE` 都是项目自定义实现。

### 两层校验

```text
第一层：Pydantic
检查字段、类型和嵌套结构

第二层：服务端业务校验
检查 evidence_id 是否属于本次召回
检查 quote 是否来自对应 Chunk 原文
统一无答案拒答结果
```

核心代码：

```python
evidence = evidence_registry.get(citation.evidence_id)

if evidence is None:
    raise CitationValidationError("证据编号不存在")

quote = citation.quote.strip()
if not quote or quote not in evidence["text"]:
    raise CitationValidationError("quote 不是对应 Chunk 原文")
```

- `dict.get(key)` 在键不存在时返回 `None`，不会抛出 `KeyError`。
- `is None` 明确判断没有找到证据。
- `quote not in evidence["text"]` 执行字符串子串检查。
- `raise` 主动抛出异常，立即停止正常放行流程。
- 只有全部引用通过后，函数才执行最后的 `return result`。

自定义异常：

```python
class CitationValidationError(ValueError):
    """模型返回的引用没有通过服务端证据校验。"""
```

它继承 Python 内置 `ValueError`，用于区分普通程序错误和引用校验失败。
上层 FastAPI 可以捕获该异常并返回统一错误响应、触发重试或改为拒答。

### 失败即拒绝

当模型判断证据不足时，服务端不直接信任模型生成的拒答文字，而是构造统一结果：

```python
RagAnswer(
    answer="根据当前知识库无法回答。",
    citations=[],
    has_sufficient_context=False,
)
```

真实运行结果：

```text
真实 S1 + 真实 quote：通过
不存在的 S99：拦截
真实 S1 + 伪造 quote：拦截
```

### 当前边界

精确子串检查是可靠的基础版本，但生产环境还要考虑 PDF OCR 空格、换行、
全角标点等文本归一化。它可以证明 quote 出现在 Chunk 中，却不能单独证明
整段 answer 的每个结论都被 quote 支持；后者需要测试集、LLM Judge 或
自然语言推断等评估手段。

## 检查点 5-6：拒答策略、测试与错误归因

练习文件：`D:/Code_all/agent_test/langchain_demo/Day5/day_5.5.py`

### 为什么有 Top-K 仍可能无答案

向量数据库执行最近邻搜索，即使知识库没有正确答案，也可能返回若干
“相对最接近”的结果。因此不能将“返回了 Top-K”直接等同于“证据充分”。

项目先获取较多候选，再执行相关度过滤：

```python
RETRIEVAL_LIMIT = 5
MAX_CONTEXT_HITS = 2
MIN_RELEVANCE_SCORE = 0.55

relevant_hits = [
    hit
    for hit in hits
    if hit["distance"] >= MIN_RELEVANCE_SCORE
][:MAX_CONTEXT_HITS]
```

这段代码先过滤低分命中，再截取最多两个 Chunk。`0.55` 是当前小型测试集
上的项目参数，不是 Milvus 或 COSINE 的通用标准；生产环境必须通过标注
问题集评估不同阈值的召回率和误召回率。

如果过滤后为空：

```python
if not relevant_hits:
    return {
        "answer": refusal_answer(),
        "llm_called": False,
        "stage": "retrieval",
    }
```

程序直接拒答，既降低幻觉风险，也节省一次 LLM 请求。

### 三类测试

```text
事实问题：检查单个或少量明确事实
综合问题：检查是否召回多个 Chunk 并综合回答
知识库外问题：检查是否稳定拒答
```

实际结果：

```text
事实问题       top_score=0.7802  调用 LLM   通过
综合问题       top_score=0.6794  调用 LLM   通过
知识库外问题   top_score=0.3375  不调用 LLM 通过
总计：3/3
```

3/3 只证明当前三个样例通过，不代表生产准确率。正式项目应扩充人工标注
测试集，并分别统计检索和生成指标。

### 错误归因

```text
预期事实没有出现在 context：
    检索阶段问题

预期事实已在 context，但答案错误、拒答错误或引用无效：
    生成或引用校验阶段问题
```

`trace` 是本项目自定义的诊断字典，记录：

```text
raw_hits
relevant_hits
context
llm_called
stage
error
answer
```

它让开发者能看到问题停在哪一层，而不是把所有错误都归因于 Prompt。

## 编码任务

1. Retriever 返回文档及元数据，不只返回字符串。
2. 给每个上下文片段分配稳定 Chunk ID。
3. 使用结构化输出生成答案和引用。
4. 服务端校验引用的 Chunk ID 确实存在于本次上下文中。
5. 对证据不足问题返回统一拒答结果。

## 验收标准

- 每个事实答案至少包含一个有效引用。
- 引用可以定位到原始文档。
- 域外问题不会伪造答案。
- 能分别说明一次错误属于召回问题还是生成问题。

## 面试自测

1. 有引用是否意味着答案一定正确？
2. 如何防止模型编造文档 ID？
3. 为什么 RAG 仍然可能产生幻觉？

## 面试自测参考答案

### 1. 有引用是否意味着答案一定正确

不意味着。引用存在只说明回答附带了一个来源标记，还必须继续验证：

```text
引用编号是否属于本次检索
quote 是否确实来自对应 Chunk
quote 是否真正支持 answer 中的结论
来源是否权威、有效且版本正确
回答是否遗漏了重要条件
```

向量相似度高只表示语义接近，不等于事实一定正确。

### 2. 如何防止模型编造文档 ID

文档 ID、Chunk ID 和文件哈希都由服务端在摄取阶段生成，不能让模型负责生成。
检索后，服务端为本次证据分配 `S1`、`S2` 等临时标签，并保存：

```python
evidence_registry = {
    "S1": {
        "document_id": "...",
        "chunk_id": "...",
        "text": "...",
    }
}
```

模型只能返回证据标签。服务端校验标签存在后，再从注册表映射到真实 ID。
不存在的标签、越权文档或不匹配的 quote 一律拦截，不能把模型输出的 ID
直接用于数据库查询或返回给用户。

相关标识的区别：

```text
file_hash    SHA-256 内容指纹，用于重复文件识别
document_id  MySQL 文档业务记录 ID
chunk_id     某个文本块的稳定 ID，可使用 UUID5
evidence_id  S1、S2，本次请求内的临时证据标签
```

### 3. 为什么 RAG 仍然可能产生幻觉

幻觉是模型把没有可靠证据支持的内容当成事实输出。RAG 只能向模型提供证据，
不能保证模型一定正确使用证据。常见原因包括：

```text
正确 Chunk 没有被召回
召回内容相关但不能完整回答
上下文存在冲突、过期或错误信息
上下文预算截掉了关键条件
模型忽略证据并使用自身记忆补充
模型错误综合多个 Chunk
模型生成不存在的引用编号或伪造 quote
文档中含有提示注入内容
```

需要区分两个指标：

```text
忠实性/有依据性：answer 是否由本次 context 支持
事实正确性：answer 是否符合真实世界或标准答案
```

一个回答可能忠实于过期文档，但事实已经错误；也可能结论碰巧正确，却没有
得到当前知识库证据支持。生产系统需要结合检索评估、引用校验、来源治理、
拒答策略和人工标注测试集降低风险。

## 学习记录

```text
完成日期：2026-07-24
实际投入时间：按检查点分段学习
事实问题结果：通过，能够生成结构化回答并返回真实引用
综合问题结果：通过，能够综合年假和病假两个 Chunk
域外问题结果：通过，相关度低于阈值时不调用 LLM 并统一拒答
最重要的理解：结构正确不等于内容可信；模型生成候选答案，后端负责证据校验与最终放行
对应 Git 提交：
```

Day5 状态：已完成。
