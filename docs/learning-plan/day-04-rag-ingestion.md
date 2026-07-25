# Day 04：RAG 文档解析、切分与向量入库

## 今日目标

完成从原始文档到可检索分块的摄取流程，理解 RAG 的数据基础。

## 今日时间表

1. 09:00-10:30：学习 Embedding、相似度、Chunk 和 Top-K。
2. 10:45-12:00：解析 TXT、Markdown 和简单 PDF。
3. 14:00-15:30：比较三组切分参数。
4. 15:45-17:00：写入 Milvus 并执行向量搜索。
5. 17:00-18:00：检查元数据和中文召回结果。

## 必学知识

- `【非常重点】Embedding`：将文本映射为向量，语义相近不代表关键词相同。
- `【非常重点】Chunking`：分块决定答案信息是否完整，也决定噪声和成本。
- `【非常重点】元数据`：至少保留 `user_id/document_id/source/page/chunk_index`。
- `【非常重点】数据隔离`：检索必须带当前用户或知识库过滤条件。
- `【重点】Top-K`：召回数量过小会漏信息，过大会引入噪声。

## 检查点 1：RAG 摄取链路与 Document 分块

### RAG 的两条链

离线摄取链路负责建设知识库：

```text
原始文件
  -> Loader/Parser 解析
  -> Document
  -> Text Splitter 切分
  -> Chunk Documents
  -> Embedding 向量化
  -> Vector Store 入库
```

在线问答链路负责使用知识库：

```text
用户问题
  -> Query Embedding
  -> 向量检索 Top-K Chunks
  -> 将相关 Chunks 作为上下文
  -> LLM 生成带来源的回答
```

今晚先完成前一条链中的 `Document -> Chunk Documents`。

### 为什么不能把整份 PDF 当作一个 Chunk

1. 一份文档可能包含多个主题，一个向量会把多个主题压在一起，检索指向不精确。
2. 检索命中后需要把整份文档交给模型，容易超过上下文限制并增加 token 成本。
3. 无法精确返回页码、段落和片段引用。
4. 文档局部更新时需要重新处理整份大块。

分块也不能无限小。块太小会切断定义、条件和结论；块太大又会混入无关内容。因此 Chunking 是召回质量的重要参数，不只是文本预处理。

### Document 是什么

官方导入：

```python
from langchain_core.documents import Document
```

当前 `langchain-core 1.4.9` 中核心构造签名：

```text
Document(page_content: str, *, id: str | None = None,
         metadata: dict = ..., type: Literal['Document'] = 'Document')
```

最重要的两个属性：

```text
page_content  文本正文
metadata      来源、页码、用户和文档标识等元数据
```

示例：

```python
document = Document(
    page_content="文档正文",
    metadata={
        "user_id": "user-1",
        "document_id": "doc-001",
        "source": "guide.md",
        "page": 1,
    },
)
```

`Document`、`page_content` 和 `metadata` 是固定类/属性；`user_id`、`document_id`、`source`、`page` 是当前项目自定义的 metadata 键。

### RecursiveCharacterTextSplitter

官方导入：

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
```

它是通用文本推荐的起始切分器。它按照 separators 的先后顺序尝试保留较大的语义单元：先段落，再换行、句子、标点，最后必要时按字符切开。

```python
splitter = RecursiveCharacterTextSplitter(
    separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    chunk_size=90,
    chunk_overlap=20,
    length_function=len,
    add_start_index=True,
)
```

参数：

```text
separators       按优先级尝试的分隔符；中文应加入中文句号、逗号等
chunk_size       分块目标最大长度
chunk_overlap    相邻分块的目标重叠长度，用于缓解边界语义断裂
length_function  如何测量长度；len 表示按 Python 字符数
add_start_index  在 metadata 中记录分块在原文中的起始位置
```

`chunk_overlap` 是目标重叠量，实际重叠会受分隔符和文本结构影响，不保证每两个分块都恰好重叠指定字符数。

### split_text 与 split_documents

```python
splitter.split_text(raw_text)               # list[str]
splitter.split_documents([document])         # list[Document]
splitter.create_documents([raw_text], [...]) # list[Document]
```

当前项目优先使用 `split_documents()`，因为它会让分块继续携带原 Document 的 metadata，便于检索结果追溯来源和执行用户隔离。

当前 `langchain-text-splitters 1.1.2` 的方法签名：

```text
split_text(text: str) -> list[str]
split_documents(documents: Iterable[Document]) -> list[Document]
create_documents(texts: list[str], metadatas: list[dict] | None) -> list[Document]
```

### 今晚练习

文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.1.py`

运行：

```powershell
python D:\Code_all\agent_test\langchain_demo\Day4\day_4.1.py
```

重点观察：

1. `chunks` 是 `list`，每一个 `chunk` 是 `Document`。
2. 文本位于 `chunk.page_content`，不是 `chunk.content`。
3. 原始 metadata 被传递到每一个 Chunk。
4. `add_start_index=True` 自动添加 `start_index`。
5. `chunk_index` 是我们遍历后手动添加的业务字段。

官方依据：

- https://docs.langchain.com/oss/python/langchain/retrieval
- https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter
- https://reference.langchain.com/python/langchain-text-splitters/character/RecursiveCharacterTextSplitter

本检查点验收：

1. 能画出离线摄取链路和在线问答链路。
2. 能解释整份文档为什么不能只生成一个向量。
3. 能解释 `Document.page_content` 与 `Document.metadata`。
4. 能说出 `chunk_size`、`chunk_overlap` 和 `separators` 的作用。

### 为什么本次正好切成三个连贯段落

这次没有调用 LLM、Embedding 或语义模型。结果看起来“理解语义”，是因为原文预先使用两个换行符 `\n\n` 分隔了自然段，而它正好是 separators 中优先级最高的分隔符：

```python
separators=["\n\n", "\n", "。", "；", "，", " ", ""]
```

当前版本的核心逻辑可以简化为：

```text
1. 从左到右寻找文本中存在的第一个 separator
2. 使用它把文本切成较小片段
3. 尝试把相邻小片段合并，但不超过 chunk_size
4. 某个片段仍然过大时，递归使用后续 separator 继续切
5. 对最后得到的 chunks 尝试保留 chunk_overlap
```

本例首先使用 `\n\n`：

```text
标题
第一自然段
第二自然段
第三自然段
```

标题与第一自然段合并后是 88 个字符，没有超过 90，因此成为 Chunk 0。第二、第三自然段分别是 76 和 74 个字符；它们单独都小于 90，但两段合并会超过 90，因此分别成为 Chunk 1、Chunk 2。

本例没有明显出现 20 字符重叠，是因为每个自然段都是一个较大的完整 split。为了加入下一个自然段并保持总长度不超过 90，合并算法必须把上一个完整自然段移出当前窗口。`chunk_overlap=20` 是目标值，不是强制从任意字符串末尾复制 20 个字符。

如果删除原文中的空行，或者把 `chunk_size` 改成 40，切分器才会继续尝试 `\n`、`。`、`；`、`，`，最终必要时按字符切分。

### day_4.1.py 中的固定 API 与 Python 语法

固定 LangChain API：

```python
Document(...)
RecursiveCharacterTextSplitter(...)
splitter.split_documents(...)
document.page_content
document.metadata
```

项目自定义内容：

```text
raw_text、original_document、splitter、chunks、chunk_index  自定义变量名
user_id、document_id、source、page、chunk_index              自定义 metadata 键
```

普通 Python 语法：

```python
from package import Name
```

从模块导入类或函数。

```python
Document(page_content=raw_text, metadata={...})
```

调用类创建对象；`page_content=`、`metadata=` 是关键字参数。

```python
length_function=len
```

把 `len` 函数对象交给切分器。这里不能写 `len()`，因为不是现在计算某个值，而是让切分器以后需要测量文本时调用它。

```python
splitter.split_documents([original_document])
```

点号表示调用 splitter 对象的方法；`[original_document]` 创建只包含一个 Document 的列表。

```python
for chunk_index, chunk in enumerate(chunks):
```

`enumerate(chunks)` 每轮产生 `(编号, 元素)`；左侧两个变量使用元组拆包分别接收编号和 Document。

```python
chunk.metadata["chunk_index"] = chunk_index
```

先通过点号取得 Document 的 metadata 字典，再使用 `[...]` 给字典键赋值。

```python
repr(chunk.page_content)
```

返回便于调试的字符串表示，因此换行会显示成 `\n`，不是正文真的包含反斜杠字符。

```python
"=" * 60
```

Python 字符串重复，生成 60 个等号。

构造器签名中的 `**kwargs` 是 Python 的关键字参数收集语法。`RecursiveCharacterTextSplitter` 自己接收 separators 等参数，并通过 `**kwargs` 把 `chunk_size`、`chunk_overlap`、`length_function`、`add_start_index` 等参数交给父类 `TextSplitter` 初始化。

## 检查点 2：比较三组 Chunk 参数

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.2.py`

同一份文档比较：

```text
小块无重叠    chunk_size=70，chunk_overlap=0
中块有重叠    chunk_size=110，chunk_overlap=30
大块有重叠    chunk_size=180，chunk_overlap=40
```

运行：

```powershell
python D:\Code_all\agent_test\langchain_demo\Day4\day_4.2.py
```

### 参数影响

```text
Chunk 较小
  优点：定位精确、单块噪声少
  缺点：上下文容易断裂、向量数量和检索组合成本增加

Chunk 较大
  优点：上下文完整、跨句关系保留较多
  缺点：可能混入多个主题、检索精度下降、送入模型的 token 增加

Overlap 较大
  优点：降低重要信息恰好跨边界而丢失的概率
  缺点：重复文本、向量数量、存储和检索噪声增加
```

`chunk_size` 是目标最大长度，不是要求每块长度完全相等。切分器会优先保持段落或句子完整；如果某个不可继续拆分的单元本身过长，也可能产生与目标不完全一致的结果。

### keep_separator="end"

```python
keep_separator="end"
```

这是固定参数，表示把分隔符保留在前一个片段的末尾。本实验用中文句号切分时，句号会留在对应句子末尾，打印结果更自然。

### experiments 列表

```python
experiments = [
    {"name": "小块无重叠", "chunk_size": 70, "chunk_overlap": 0},
    ...
]
```

这是 Python 的 `list[dict]`：外层列表保存三组实验，内层每个字典保存一组参数。

遍历：

```python
for experiment in experiments:
    size = experiment["chunk_size"]
```

每一轮 `experiment` 都是一个字典，使用 `[...]` 按键读取参数。

### 实际重叠辅助函数

```python
shared_boundary_size(left, right)
```

这是项目自定义的普通 Python 函数，不是 LangChain API。它比较上一块的末尾和下一块的开头，返回完全相同的最大字符数，仅用于观察实验结果。

当前项目可先把“中文通用文档按字符计数”的候选基线设为：

```text
chunk_size=600
chunk_overlap=100
```

这只是需要通过评估调整的项目起点，不是官方标准答案。不同文档结构、Embedding 模型、问题粒度和召回策略会需要不同参数。

本检查点验收：

1. 能解释小块、大块和 overlap 的主要取舍。
2. 能说明为什么 chunk_size 不是每块固定长度。
3. 能解释 experiments 为什么是 `list[dict]`。
4. 能根据打印结果选择一个初始参数，并说明理由。

## 检查点 3：Embedding 与余弦相似度

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.3.py`

### Embedding 是什么

Embedding 模型把文本映射为固定长度的浮点数向量：

```text
"员工如何申请年假？"
    -> Embedding Model
    -> [-0.01, 0.03, ..., 0.02]
```

这些数字不能逐位翻译成人类含义。模型训练的目标使语义相关文本在向量空间中更接近，因此可以用数学相似度寻找相关 Chunk。Embedding 不负责生成答案。

### 文本如何变成固定长度向量

具体供应商内部实现可能不同，通用原理可以理解为：

```text
原始文本
  -> Tokenizer 切成 token 并转成 token ID
  -> 神经网络把每个 token 映射成初始向量
  -> Transformer 根据上下文更新每个 token 的表示
  -> Pooling/聚合把整段 token 表示压缩
  -> Projection/投影到指定维度
  -> 得到一个固定长度的文本向量
```

token 不是简单等同于汉字。它可能是一个字、词、子词、标点或其他片段，由模型自己的 tokenizer 决定。

1024 表示输出向量有 1024 个坐标，不表示最多处理 1024 个字，也不表示每个字对应一个坐标：

```text
"年假"                         -> 1024 维
一段 600 字的制度说明          -> 1024 维
一段更长但未超过模型上限的文本 -> 1024 维
```

这是有损语义压缩，目标是保留适合相似度比较的信息，而不是从向量完整还原原文。每个维度通常没有可单独命名的人类含义；语义分布在大量维度的组合模式中。

### Embedding 模型如何学会语义接近

通用训练方式会使用相关与不相关文本对：

```text
Query：怎样申请年假？
正样本：年假需提前三个工作日申请
负样本：数据库每天凌晨两点备份
```

训练目标让 Query 与正样本向量更接近，让 Query 与负样本更远。这类方法通常称为对比学习。真正调用 `embed_query()` 时不再训练，只执行模型前向计算并返回向量。

### 1024 维是否很小

1024 维已经是高维空间。维度与文本字数没有一一对应关系。以 float32 估算：

```text
1024 维 × 4 字节 ≈ 4 KB/向量
100 万向量原始数值约 4 GB
```

实际向量数据库还需要索引、metadata、正文和管理开销。维度更大可能保留更多信息，但也增加存储、内存、距离计算和网络成本。阿里云 `text-embedding-v4` 支持多种输出维度，并将 1024 作为多数语义搜索场景的推荐平衡点。

同一向量集合必须保持模型、维度和主要预处理方式一致。Milvus Collection 一旦按 1024 维建好，不能把 1536 维向量直接写进去；更换模型或维度通常需要重新生成并重建索引。

### Token 向量不等于最终文本向量

假设一段文本被切成 `n` 个 token，模型内部隐藏维度是 `h`：

```text
Token IDs                 形状约为 n
初始 Token 向量矩阵       形状约为 n × h
Transformer 输出矩阵      形状约为 n × h
Pooling 后的段落表示       形状约为 h
Projection 后最终向量      形状为 1024
```

`h` 是模型内部结构参数，不一定等于 API 返回的 1024。每个 Token 的内部向量在 Transformer 中会根据上下文发生变化；“苹果手机”与“吃苹果”中的“苹果”不会只使用完全相同的孤立语义表示。

最终文本向量通常不是把初始 Token 向量简单相加。概念公式：

```text
Q = Normalize(Project(Pool(Transformer(TokenEmbeddings + Positions))))
```

Pooling 可能是特殊 token、平均、加权或其他模型特定方法；供应商未公开的细节不能自行假定。即便某模型使用平均池化，平均的也是已经结合上下文的隐藏表示，之后还可能经过投影和归一化。

如果只做简单加法，词序很容易丢失；Transformer 的注意力与位置表示使“主管审批员工”和“员工审批主管”能够得到不同表示。

当前环境使用阿里云百炼 OpenAI 兼容接口：

```python
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(
    model="text-embedding-v4",
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    dimensions=1024,
    check_embedding_ctx_length=False,
)
```

阿里云当前将 `text-embedding-v4` 推荐用于纯文本检索/RAG；1024 维是通用场景的推荐平衡点。模型、维度或预处理方式变化后，旧向量与新向量不应混在同一索引中比较。

官方依据：

- https://help.aliyun.com/zh/model-studio/embedding
- https://docs.langchain.com/oss/python/integrations/embeddings/openai
- https://docs.langchain.com/oss/python/integrations/embeddings

### 两个固定方法

```python
document_vectors = embeddings.embed_documents(document_texts)
query_vector = embeddings.embed_query(query)
```

类型：

```text
embed_documents(list[str]) -> list[list[float]]
embed_query(str)            -> list[float]
```

文档侧通常批量向量化，查询侧每次把单个问题向量化。接口将两种用途分开，因为部分模型会对 query 与 document 采用不同策略。

### 列表推导式

```python
document_texts = [document.page_content for document in documents]
```

等价于：

```python
document_texts = []
for document in documents:
    document_texts.append(document.page_content)
```

### 余弦相似度

```python
numerator = np.dot(left_array, right_array)
denominator = np.linalg.norm(left_array) * np.linalg.norm(right_array)
score = numerator / denominator
```

数学形式：

```text
cos(A, B) = (A · B) / (||A|| × ||B||)
```

它比较两个向量的方向。通常分数越大表示越相似，但分数不是“答案正确概率”；阈值必须针对所用模型和业务数据评估。

### zip 与排序

```python
for document, vector in zip(documents, document_vectors, strict=True):
```

`zip()` 将同一位置的 Document 和向量配对。`strict=True` 要求两个列表长度一致，不一致时立即报错，避免文档与错误向量错位。

```python
ranked_results.sort(
    key=lambda item: item["score"],
    reverse=True,
)
```

`key` 指定按每个字典的 score 排序；`reverse=True` 表示从高到低。`lambda` 是没有单独命名的简短函数，近似等价于：

```python
def get_score(item):
    return item["score"]
```

### 索引与查询的对应关系

```text
离线：Chunk Documents -> embed_documents -> 文档向量
在线：用户问题       -> embed_query     -> 问题向量
比较：问题向量 vs 所有文档向量 -> 相似度排序 -> Top-K
```

本实验在 Python 内存里手动比较全部向量。向量数据库的作用是保存大量向量，并使用索引快速完成近邻搜索和 metadata 过滤。

本检查点验收：

1. 能解释 Embedding 与 LLM 文本生成的区别。
2. 能说出 `embed_documents()` 与 `embed_query()` 的输入输出。
3. 能解释余弦相似度越大通常意味着什么，以及为什么它不是正确率。
4. 能说明向量数据库比手动遍历全部向量多解决了什么问题。

### 前 8 个数字只是打印预览

```python
print(query_vector[:8])
```

`[:8]` 是 Python 切片，只显示索引 0 到 7 的八个数字。`query_vector` 本身仍有 1024 维，余弦相似度会使用全部 1024 个坐标。

不会先把四个 Document 向量相加。计算过程是：

```text
query_vector vs document_vector_1 -> score_1
query_vector vs document_vector_2 -> score_2
query_vector vs document_vector_3 -> score_3
query_vector vs document_vector_4 -> score_4
按 score 从高到低排序
```

点积内部才会把对应坐标的乘积相加：

```text
q1*d1 + q2*d2 + ... + q1024*d1024
```

### 长 Chunk 的成本发生在哪里

无论 Chunk 原文有 50 字还是 600 字，只要模型配置为 1024 维，最终向量距离计算的维度都是 1024。长 Chunk 增加的是：

1. 首次 Embedding 的输入 token 与调用成本。
2. 多主题压缩到一个向量造成的语义混杂。
3. 检索后送入 LLM 的上下文 token。

文档向量在离线摄取时生成并保存。在线查询通常只为新问题生成一次 query vector，不会每次重新向量化全部 Chunk。文档新增、内容变化、Embedding 模型或预处理版本变化时，才重建相关向量。

### 向量数据库不会总是线性扫描全部向量

`day_4.3.py` 为了教学，对四个向量逐个计算，复杂度近似为 `O(N × dimensions)`。生产向量数据库通常使用 ANN（Approximate Nearest Neighbor，近似最近邻）索引，例如 HNSW 或 IVF，快速缩小候选范围，再返回 Top-K；同时使用 metadata 过滤限定 user_id、knowledge_base_id 等范围。

### 分层检索是可行的进阶方案

可以先用文档/页面摘要进行粗检索，再在候选文档内部检索细粒度 Chunk：

```text
Query
  -> 文档或页面摘要向量 Top-N
  -> 候选页面中的 Chunk 向量 Top-K
  -> 返回原文片段
```

另一种常见模式是“小块检索、大块返回”：用较小 child chunks 提高定位精度，命中后返回包含它的较大 parent chunk 给 LLM，兼顾检索精度与上下文完整性。

分层检索会增加索引、父子映射和评估复杂度，而且粗检索漏掉文档后细检索无法补救。两天主线先建立直接 Chunk 检索基线，后续根据评估结果再加入层级检索、混合检索和 Reranker。

## 检查点 4：Vector Store、Top-K 与用户过滤

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.4.py`

当前先使用：

```python
from langchain_core.vectorstores import InMemoryVectorStore
```

它把向量、正文和 metadata 保存在当前 Python 进程的字典中，并使用 NumPy 线性计算余弦相似度。适合学习和单元测试，不是 Milvus 的替代品，也不具备生产 ANN 索引、持久化和分布式能力。

官方参考：

- https://reference.langchain.com/python/langchain-core/vectorstores/in_memory/InMemoryVectorStore
- https://docs.langchain.com/oss/python/integrations/vectorstores

### 初始化

```python
vector_store = InMemoryVectorStore(embedding=embeddings)
```

Vector Store 持有 Embeddings 对象，因此后续添加文档和文本查询时会自动完成对应向量化。

### 写入 Documents

```python
added_ids = vector_store.add_documents(
    documents=documents,
    ids=document_ids,
)
```

固定签名核心部分：

```text
add_documents(documents: list[Document], ids: list[str] | None) -> list[str]
```

内部概念过程：

```text
读取 Document.page_content
  -> embed_documents()
  -> 保存 id、vector、text、metadata
  -> 返回实际写入的 IDs
```

`ids` 与 documents 必须按位置对应，生产中应使用稳定、唯一的 chunk_id，支持幂等更新和删除。

### 带分数的 Top-K 搜索

```python
results = vector_store.similarity_search_with_score(
    query=query,
    k=3,
)
```

返回类型：

```text
list[tuple[Document, float]]
```

每个元素是 `(Document, score)`，所以使用元组拆包：

```python
for document, score in results:
    ...
```

`k` 表示最多返回几个结果，即 Top-K。不同 Vector Store 对 score 的定义可能是相似度或距离，不能跨实现假定“越大越好”；当前 `InMemoryVectorStore` 返回余弦相似度，越大通常越相似。

### metadata filter

当前 InMemoryVectorStore 的 filter 是一个函数：

```python
def belongs_to_user_1(document: Document) -> bool:
    return document.metadata.get("user_id") == "user-1"
```

返回 `True` 的 Document 才参与搜索。Milvus 使用过滤表达式而不是同一个 Python callable，迁移时过滤语法会变化，但“先限定当前用户/知识库，再做向量搜索”的安全原则不变。

### as_retriever

```python
retriever = vector_store.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 2, "filter": belongs_to_user_1},
)

documents = retriever.invoke(query)
```

Retriever 是统一的检索接口，输入查询字符串并返回 `list[Document]`。它默认不返回 score，适合接入后续 LCEL 或 LangGraph RAG 节点；需要观察分数和调参时仍使用 `similarity_search_with_score()`。

本检查点验收：

1. 能解释 `add_documents()` 内部为何会自动调用 Embedding。
2. 能解释 Top-K 中的 k。
3. 能说出未过滤为什么可能泄露 user-2 文档。
4. 能区分 VectorStore 与 Retriever 的返回值。

## 检查点 5：项目文档摄取入口

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.5.py`

示例文档：`D:/Code_all/agent_test/langchain_demo/Day4/employee-handbook.md`

本检查点把企业知识库的一次上传拆成以下流水线：

```text
文件路径
  -> 校验类型、大小和空文件
  -> 计算 SHA-256 内容指纹
  -> 解析为原始 Document
  -> 补充用户、知识库、文档和来源元数据
  -> 切分为 Chunk Documents
  -> 生成稳定 Chunk ID
  -> 交给 Embedding 和 Vector Store
```

### 固定 API 与项目自定义代码

LangChain 官方固定类型/API：

```python
Document(page_content=..., metadata=...)
splitter.split_documents(source_documents)
```

本项目自己定义的名称和规则：

```python
validate_file(...)
calculate_sha256(...)
load_text_document(...)
build_chunks(...)
user_id
knowledge_base_id
document_id
file_sha256
chunk_index
```

这些自定义函数名和 metadata 字段都可以修改，但生产项目必须稳定约定字段含义。

### SHA-256 去重

```python
digest = sha256()
with file_path.open("rb") as file:
    for block in iter(lambda: file.read(1024 * 1024), b""):
        digest.update(block)
```

这里按 1 MB 分块读取文件，避免一次把大文件全部载入内存。`iter(callable, sentinel)` 会反复调用前面的函数；当结果等于结束标记 `b""` 时停止。

SHA-256 判断的是“文件字节是否相同”，不是语义是否相同。文件只改一个字，其哈希通常也会完全变化。演示代码使用 `set` 保存去重键；生产环境应在 MySQL 中保存文件哈希，并对类似 `(user_id, knowledge_base_id, file_sha256)` 的字段建立唯一约束。

### 稳定 Chunk ID

```python
chunk.id = str(uuid5(NAMESPACE_URL, chunk_key))
```

`uuid5()` 根据同一个命名空间和同一个字符串稳定地产生同一个 UUID。相同文件、相同租户、相同文档和相同 `chunk_index` 再次摄取时会得到相同 ID，便于幂等更新。`Document.id` 是官方字段；UUID 是官方建议格式，但不会被强制校验。

### 多租户元数据

```python
metadata={
    "user_id": user_id,
    "knowledge_base_id": knowledge_base_id,
    "document_id": document_id,
    "source": file_path.name,
    "page": 1,
    "file_sha256": file_hash,
}
```

这些字段不只是为了展示：检索时必须按 `user_id` 和 `knowledge_base_id` 过滤；生成引用时需要 `source`、`page` 和 `document_id`；删除或重建索引时需要稳定定位文档和 Chunk。真实接口中的 `user_id` 应来自认证身份，不能盲目信任客户端自行提交的值。

### PDF 大文件的官方入口

```python
from langchain_community.document_loaders import PyPDFLoader

loader = PyPDFLoader(file_path, mode="page")
for page_document in loader.lazy_load():
    ...
```

`mode="page"` 让每页成为一个 `Document`，便于保留页码和生成引用。`load()` 一次返回全部文档；`lazy_load()` 逐个产生文档，适合大文件。它们是 Document Loader 的统一官方接口。`PyPDFLoader` 依赖 `pypdf`。

官方参考：

- https://docs.langchain.com/oss/python/integrations/document_loaders/index
- https://reference.langchain.com/python/langchain-community/document_loaders/pdf/PyPDFLoader
- https://reference.langchain.com/python/langchain-core/documents/base/Document

本检查点验收：

1. 能解释 SHA-256 解决什么问题，以及它不能判断什么。
2. 能区分原始 `Document` 和切分后的 Chunk `Document`。
3. 能解释 `user_id`、`knowledge_base_id`、`document_id` 与 `chunk.id` 各自定位哪一层。
4. 能说明为什么生产去重不能只使用进程内的 `set`。

## 检查点 6：多格式 Document Loader

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.6.py`

依赖：

```bash
pip install pypdf docx2txt
```

不同格式使用不同解析器，但最终统一返回 `list[Document]`：

```python
if suffix in {".md", ".txt"}:
    return TextLoader(file_path, encoding="utf-8")
if suffix == ".pdf":
    return PyPDFLoader(file_path, mode="page")
if suffix == ".docx":
    return Docx2txtLoader(file_path)
```

`TextLoader`、`PyPDFLoader` 和 `Docx2txtLoader` 都继承 `BaseLoader`。`BaseLoader` 规定了统一接口：

```python
documents = loader.load()

for document in loader.lazy_load():
    ...
```

`load()` 一次得到全部 `list[Document]`；`lazy_load()` 返回迭代器，逐个产生 `Document`，适合大批量文档。PDF 使用 `mode="page"` 时，一页对应一个 `Document`，所以两页 PDF 得到两个对象；DOCX Loader 当前把整份文件作为一个对象，后续再由 Text Splitter 切成 Chunk。

`choose_loader()`、`load_source_documents()` 是项目自定义函数；各 Loader、`BaseLoader`、`load()` 和 `lazy_load()` 是框架固定类型或接口。

当前 `langchain-community==0.4.2` 仍可运行这些 Loader，但从 2026 年 5 月起该仓库已停止维护，并提示迁移到独立集成包。教学实验保留现有 API 以理解统一 Loader 接口；项目落地时应锁定依赖，并优先选择仍在维护的独立解析包，或直接用 `pypdf`、`python-docx` 实现项目解析适配器，再统一构造 `Document`。

官方参考：

- https://docs.langchain.com/oss/python/integrations/document_loaders/index
- https://reference.langchain.com/python/langchain-community/document_loaders/pdf/PyPDFLoader
- https://reference.langchain.com/python/langchain-community/document_loaders/word_document/Docx2txtLoader
- https://github.com/langchain-ai/langchain-community/issues/674

## 检查点 7：连接独立部署的 Milvus

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.7.py`

当前环境：

```text
Milvus Server = 2.6.20
langchain-milvus = 0.3.3
pymilvus = 2.6.16
Milvus URI = http://192.168.203.129:19530
```

最小连接代码：

```python
from pymilvus import MilvusClient

client = MilvusClient(
    uri="http://192.168.203.129:19530",
    timeout=10,
)

try:
    collections = client.list_collections()
    print(collections)
finally:
    client.close()
```

`MilvusClient` 是 PyMilvus 官方客户端类，实例化时会根据 `uri` 建立到 Milvus 服务的连接。`uri` 和 `timeout` 是构造方法规定的参数名；`list_collections()` 与 `close()` 是官方方法。

`client`、`collections` 和 `MILVUS_URI` 是项目自定义变量名，可以修改。`http://192.168.203.129:19530` 中，`192.168.203.129` 是虚拟机 IP，`19530` 是 Milvus SDK 默认服务端口。

当前输出：

```text
连接成功
已有集合： []
连接已关闭
```

空列表 `[]` 表示连接成功且当前数据库中还没有 Collection。Collection 可以理解为关系数据库中的表，但它主要保存向量、文本和元数据，并提供向量索引与相似度搜索。

官方参考：

- https://milvus.io/docs/connect-to-milvus-server.md
- https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Client/close.md

## 检查点 8：向量持久化、索引与多租户检索

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.8.py`

完整流程：

```text
Document
  -> Embedding
  -> 创建 Collection
  -> 创建向量索引
  -> Upsert 文本、向量与元数据
  -> Load Collection
  -> 将问题转换成向量
  -> 元数据过滤 + 向量检索
```

核心 API：

```python
client.has_collection(...)
client.create_collection(...)
client.list_indexes(...)
client.create_index(...)
client.upsert(...)
client.load_collection(...)
client.search(...)
client.close()
```

这些都是 PyMilvus 官方固定方法。`documents`、`rows`、`query`、`filter_expression` 等是项目自定义变量。

Milvus 中需要区分三个阶段：

1. `upsert()` 将数据持久化，主键已存在时更新，不存在时新增。
2. `create_index()` 为向量字段建立检索索引。
3. `load_collection()` 将 Collection 加载到查询节点，使其可以被搜索。

项目中的多用户隔离不能只依赖向量相似度，必须在搜索时加入资源归属过滤：

```python
filter_expression = (
    'user_id == "user-1" and '
    'knowledge_base_id == "kb-company-a"'
)
```

搜索代码：

```python
search_results = client.search(
    collection_name=COLLECTION_NAME,
    data=[query_vector],
    filter=filter_expression,
    limit=2,
    output_fields=["text", "user_id", "source"],
    search_params={
        "metric_type": "COSINE",
        "params": {},
    },
)
```

`data=[query_vector]` 是二维列表，因为一次搜索请求可以同时提交多个问题向量。当前只有一个问题，所以结果使用 `search_results[0]` 读取第一组命中。

`COSINE` 分数越大表示越相似。当前实验中，年假片段得分约为 `0.85`，数据库备份片段约为 `0.33`。虽然 `user-2` 也存在年假片段，但因为过滤条件限制为 `user-1`，它不会出现在结果中。

官方参考：

- https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Collections/create_collection.md
- https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Management/create_index.md
- https://milvus.io/docs/v2.6.x/metric.md

## 检查点 9：真实 PDF 摄取到 Milvus

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.9.py`

这一检查点不再手写 `Document`，而是使用真实文件：

```text
D:/Code_all/agent_test/langchain_demo/Day4/leave-policy.pdf
```

完整管道：

```text
PDF 文件
  -> SHA-256 文件指纹
  -> PyPDFLoader 按页解析
  -> list[Document]
  -> RecursiveCharacterTextSplitter
  -> list[Document Chunk]
  -> 稳定 UUID5 Chunk ID
  -> Embedding
  -> Milvus upsert
  -> 元数据过滤 + 向量检索
  -> 返回正文、页码与来源
```

实际结果：

```text
文件 SHA-256：
4f6d485dc05bcc635b7be7476371164c5a12ffa9a36677bd6c2ec4665a902b68

Chunk 数量：2
首次写入：创建 Collection
第二次写入：复用 Collection
```

问题：

```text
病假超过一天需要提供什么材料？
```

Top-1 命中：

```text
病假超过一天时，需要补充医院出具的证明材料。
来源：leave-policy.pdf，第 2 页
COSINE 相似度：约 0.669
```

相同文件重复运行时，SHA-256、Chunk 顺序和 UUID5 输入不变，因此生成相同 Chunk ID。`upsert()` 根据相同主键更新原有 Entity，不产生另一套重复记录。

## 检查点 10：五题检索评估与 Hit@K

练习文件：`D:/Code_all/agent_test/langchain_demo/Day4/day_4.10.py`

本检查点为每个测试问题人工标注目标页码和目标关键词：

```python
{
    "question": "申请年假需要提前多久？",
    "expected_page": 1,
    "expected_keyword": "三个工作日",
}
```

`expected_page` 和 `expected_keyword` 不会交给 Embedding 模型或 Milvus，只在检索结束后用于自动判分，因此这不是 Few-shot，也不会帮助模型找到答案。

核心指标：

```text
Hit@1 = 正确片段排在第一名的问题数 / 总问题数
Hit@K = 正确片段进入前 K 名的问题数 / 总问题数
```

当前每个问题只有一个标注的目标片段，所以 Hit@K 与单相关片段场景下的 Recall@K 等价。若一个问题有多个相关片段，Recall@K 需要计算前 K 名找回了多少个相关片段，不能再直接等同于 Hit@K。

实际运行结果：

```text
评估问题数：5
Hit@1：100.00% (5/5)
Hit@2：100.00% (5/5)
```

这个结果只能说明当前 5 个问题在当前 PDF 上检索正确。候选库只有 2 个 Chunk，`Hit@2` 很容易满分，不能据此宣称生产检索准确率为 100%。项目后续应扩展到更多文档和约 100 条人工标注问题，再比较切分参数、Top-K、混合检索和 Reranker。

## 编码任务

1. 为文档计算 SHA-256，识别重复上传。
2. 解析文本并进行规范化，但保留段落结构。
3. 比较 `chunk_size` 和 `chunk_overlap` 对召回的影响。
4. 将文本、向量和元数据写入 Milvus。
5. 输入五个问题，打印 Top-K 文本、分数和来源。

## 验收标准

- 能解释为什么不能只把整份 PDF 作为一个 Chunk。
- 每个向量可以追溯到用户、文档和原始位置。
- 重复文件不会产生两套有效索引。
- 五个测试问题中，目标片段大部分进入 Top-K。

## 官方资料

- https://docs.langchain.com/oss/python/langchain/retrieval
- https://milvus.io/docs

## 学习记录

```text
完成日期：2026-07-24
实际投入时间：分阶段完成
使用的切分参数：chunk_size=100，chunk_overlap=20，中文分隔符递归切分
五个问题的召回结果：Hit@1=100%（5/5），Hit@2=100%（5/5）
最重要的理解：Loader 将文件统一解析为 Document；Splitter 将 Document 切成 Chunk；
Chunk 经过 Embedding 后与正文、元数据一起写入 Milvus；在线查询将问题向量化，
带 user_id 和 knowledge_base_id 过滤执行 Top-K 搜索，并返回正文、页码和来源。
对应 Git 提交：
```
