# Day 06：异步知识库数据管道

## 今日目标

把一次性脚本改造成可查询状态、可重试、可删除、数据一致的知识库摄取服务。

## 今日时间表

1. 09:00-10:30：设计文档状态机和数据表。
2. 10:45-12:00：完成上传、状态查询接口。
3. 14:00-15:30：接入后台任务和 Redis 状态。
4. 15:45-17:00：实现失败重试和幂等。
5. 17:00-18:00：实现删除与补偿测试。

## 必学知识

- `【非常重点】幂等`：重复提交同一个任务不会生成重复文档或向量。
- `【非常重点】一致性`：MySQL、MinIO、Milvus 之间没有天然事务，必须设计顺序和补偿。
- `【非常重点】状态机`：`pending -> parsing -> indexing -> ready/failed`。
- `【非常重点】资源归属`：所有上传、查询和删除均校验 `user_id`。
- `【重点】重试边界`：只重试暂时性错误，参数错误不应无限重试。

## 检查点 1：文档状态机

练习文件：`D:/Code_all/agent_test/langchain_demo/Day6/day_6.1.py`

### 为什么需要状态机

后台文档摄取不是一次函数调用，而是一个可能持续数秒或数分钟的过程。
前端需要随时知道文档正在排队、解析、向量入库、完成还是失败。

```text
pending -> parsing -> indexing -> ready
                  \-> failed
                           \-> pending（重试）
```

状态机由三部分组成：

```text
有限状态：pending、parsing、indexing、ready、failed
合法转换：每个状态允许进入哪些下一状态
转换守卫：进入 ready 前必须已有 Chunk，进入 failed 必须有错误摘要
```

### Python 官方固定语法

```python
from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
```

`StrEnum` 是 Python 3.11 提供的字符串枚举。`DocumentStatus.PENDING`
是枚举成员，其 `.name` 是 `PENDING`，`.value` 是 `"pending"`。

官方文档：`https://docs.python.org/3.11/library/enum.html?highlight=strenum`

### 项目自定义规则

```python
ALLOWED_TRANSITIONS: dict[DocumentStatus, set[DocumentStatus]] = {
    DocumentStatus.PENDING: {DocumentStatus.PARSING},
    DocumentStatus.PARSING: {
        DocumentStatus.INDEXING,
        DocumentStatus.FAILED,
    },
    DocumentStatus.INDEXING: {
        DocumentStatus.READY,
        DocumentStatus.FAILED,
    },
    DocumentStatus.READY: set(),
    DocumentStatus.FAILED: {DocumentStatus.PENDING},
}
```

字典的键是当前状态，集合中的值是允许进入的下一状态。该变量名和转换规则
都不是框架固定 API，而是项目业务设计。

### 转换守卫

```python
if target_status not in allowed_targets:
    raise InvalidStatusTransition(...)

if target_status == DocumentStatus.READY and document.chunk_count == 0:
    raise InvalidStatusTransition(...)
```

状态机不仅限制顺序，还检查进入某个状态前必须满足的业务条件。
例如向量没有入库时不能把文档标记为 `ready`。

实际运行结果：

```text
正常：pending -> parsing -> indexing -> ready
非法：ready -> parsing，被拦截
失败：pending -> parsing -> failed
重试：failed -> pending
```

## 检查点 2：上传登记、资源归属与幂等

练习文件：`D:/Code_all/agent_test/langchain_demo/Day6/day_6.2.py`

### 先理解“上传登记”

用户上传文件后，不应立刻在 HTTP 请求中完成解析、切分和向量化。服务端先在
MySQL 中创建一条 `pending` 文档记录，然后把 `document_id` 交给后台任务。

```text
接收文件
  -> 计算 file_hash
  -> 查询或创建 MySQL 文档记录
  -> 立即返回 document_id
  -> 后台任务根据 document_id 开始解析
```

MySQL 是文档状态的最终事实来源。MinIO 保存原始文件，Milvus 保存 Chunk
及向量，Redis 只保存短期进度、缓存或任务协调信息。

### 【非常重点】什么是幂等

幂等不是“函数只执行一次”，而是：

> 同一个业务请求执行一次或重复执行多次，最终产生的业务效果相同。

本项目可以把下面三项组成幂等键：

```python
idempotency_key = (user_id, knowledge_base_id, file_hash)
```

因此：

- 同一用户向同一知识库重复上传相同内容：返回已有文档，不重复建向量。
- 文件名改变但内容不变：`file_hash` 不变，仍视为重复内容。
- 不同用户上传相同内容：`user_id` 不同，是不同的业务资源。
- 同一用户上传到不同知识库：`knowledge_base_id` 不同，也是不同资源。

### 固定语法与项目规则

```python
from hashlib import sha256
from uuid import UUID, uuid5

file_hash = sha256(file_content).hexdigest()
document_id = str(uuid5(namespace, name))
```

- `sha256()` 和 `.hexdigest()` 是 Python `hashlib` 的官方 API。
- `uuid5(namespace, name)` 是 Python `uuid` 的官方 API。
- 相同的 `namespace + name` 会稳定生成相同 UUID。
- 幂等键由哪些字段组成，是本项目的业务规则，不是 LangChain 固定语法。

参考文档：

- `https://docs.python.org/3/library/hashlib.html`
- `https://docs.python.org/3/library/uuid.html#uuid.uuid5`

### 为什么还要 MySQL 唯一约束

下面的代码逻辑只能帮助理解，不能单独解决并发：

```python
existing = find_by_idempotency_key(...)
if existing is None:
    insert_document(...)
```

两个并发请求可能同时查到“尚不存在”，随后都执行插入。因此生产数据库还要建立：

```sql
UNIQUE KEY uq_document_owner_kb_hash
    (user_id, knowledge_base_id, file_hash)
```

应用层先查询用于快速返回，数据库唯一约束负责最终兜底。发生唯一键冲突时，
捕获异常并重新查询已有记录，而不是再次创建文档。

### 【非常重点】资源归属

查询文档时不能只根据 `document_id`：

```sql
SELECT *
FROM documents
WHERE document_id = :document_id
  AND user_id = :current_user_id;
```

“文档不存在”和“文档属于其他用户”都对外返回 404，避免攻击者探测其他用户
的资源是否存在。状态查询、重试、删除和检索都必须执行相同的归属检查。

### 运行后观察

```text
第一次上传：created = True
重复上传：created = False，并返回同一个 document_id
其他用户上传相同内容：created = True，并得到不同 document_id
user-002 查询 user-001 文档：被资源归属检查拦截
```

## 检查点 3：FastAPI 上传和状态查询接口

练习文件：`D:/Code_all/agent_test/langchain_demo/Day6/day_6.3.py`

### 接口为什么立即返回

解析 PDF、生成 Embedding 和写入 Milvus 可能需要较长时间。上传接口只完成
文件校验和文档登记，随后返回 `202 Accepted`：

```text
POST /knowledge-bases/{knowledge_base_id}/documents
  -> 202
  -> {"document_id": "...", "status": "pending", "created": true}
```

`202` 表示服务器已经接受请求，但后台处理尚未完成。前端随后查询：

```text
GET /documents/{document_id}
  -> 200
  -> {"document_id": "...", "status": "pending"}
```

### FastAPI 固定 API

```python
@app.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(file: UploadFile):
    file_content = await file.read()
```

- `@app.post()`：注册 POST 路径操作。
- `{knowledge_base_id}`：路径参数。
- `response_model`：响应数据校验与 OpenAPI Schema。
- `UploadFile`：FastAPI/Starlette 的上传文件对象。
- `await file.read()`：异步读取文件内容，结果为 `bytes`。
- `status.HTTP_202_ACCEPTED`：可读的 HTTP 202 常量。

上传文件使用 `multipart/form-data`，运行服务需要安装 `python-multipart`。
相比直接接收 `bytes`，`UploadFile` 会使用内存和临时文件结合的
`SpooledTemporaryFile`，更适合较大的文件。

官方文档：

- `https://fastapi.tiangolo.com/tutorial/request-files/`
- `https://fastapi.tiangolo.com/tutorial/response-status-code/`
- `https://fastapi.tiangolo.com/tutorial/testing/`

### Depends 与当前用户

```python
def get_current_user_id(
    x_user_id: Annotated[str, Header(alias="X-User-ID")],
) -> str:
    return x_user_id


current_user_id: Annotated[str, Depends(get_current_user_id)]
```

`Header`、`Depends` 和 `Annotated` 是固定用法。FastAPI 会先执行依赖函数，
再把返回值注入 `current_user_id`。练习为了直观使用 `X-User-ID` 请求头；
生产环境必须从经过签名和有效期校验的 JWT 中获得用户身份，不能相信客户端
直接提交的任意用户编号。

### HTTPException

```python
raise HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="文档不存在",
)
```

`raise` 会中断当前路径函数，FastAPI 捕获 `HTTPException` 后生成对应 HTTP
错误响应。越权访问和文档不存在统一返回 404。

### TestClient

```python
client = TestClient(app)
response = client.post(
    "/knowledge-bases/kb-001/documents",
    headers={"X-User-ID": "user-001"},
    files={"file": ("policy.pdf", b"content", "application/pdf")},
)
```

`TestClient` 在测试进程内调用 ASGI 应用，不需要先启动 Uvicorn。它适合验证
状态码、响应 JSON、权限和幂等行为。

## 检查点 4：后台任务、队列与 Redis 进度

练习文件：`D:/Code_all/agent_test/langchain_demo/Day6/day_6.4.py`

### 项目真实结构中的位置

当前项目已经采用“路由调用 Service”的分层。例如：

```python
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_inspiration(
    payload: InspirationCreate,
    session: SessionDep,
    user: CurrentUser,
):
    return await InspirationService(session, user.id).create(payload)
```

知识库上传将沿用相同结构，但当前仓库还没有 Redis 依赖和文档 Worker，以下文件
属于后续计划，不应当误认为已经实现：

```text
backend/app/api/routes/documents.py
backend/app/services/documents.py
backend/app/workers/document_ingestion.py
backend/app/core/redis.py
```

真实接口的职责应保持很薄：

```text
校验用户和文件
-> MySQL 登记 pending 文档
-> 保存原始文件
-> 向队列投递 document_id
-> 返回 202
```

独立 Worker 执行：

```text
取出 document_id
-> parsing
-> 解析和切分
-> indexing
-> 写入 Milvus
-> ready 或 failed
```

### 三类数据不要混淆

| 位置 | 保存内容 | 是否最终事实 |
|---|---|---|
| MySQL | 文档归属、状态、Chunk 数量、错误摘要 | 是 |
| Redis | 百分比、当前步骤、短期提示、任务协调 | 否 |
| 任务队列 | 等待 Worker 执行的 `document_id` | 否 |

Redis 数据可能过期、被淘汰或在故障时丢失，因此状态查询可以优先展示 Redis
的细粒度进度，但文档最终是否 `ready` 必须以 MySQL 为准。

### 最小实验中的固定 API

```python
queue: asyncio.Queue[str] = asyncio.Queue()
await queue.put(document_id)
document_id = await queue.get()
queue.task_done()
await queue.join()
```

- `asyncio.Queue`：Python 官方异步进程内队列。
- `put()`：投递任务。
- `get()`：等待并取出任务。
- `task_done()`：声明一次 `get()` 取出的任务已处理结束。
- `join()`：等待所有已投递任务都调用 `task_done()`。

```python
worker_task = asyncio.create_task(worker_once(queue))
await worker_task
```

`create_task()` 把协程安排到当前事件循环中并发执行。该任务仍在 API 进程内，
进程退出就会丢失，不能替代生产任务队列。

Python 官方文档：

- `https://docs.python.org/3/library/asyncio-queue.html`
- `https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task`

### 为什么不直接依赖 FastAPI BackgroundTasks

`BackgroundTasks` 适合响应后发送邮件、记录日志等较短任务，但任务仍依附当前
Web 进程，不提供持久化队列、跨机器消费、可靠重试和死信处理。文档解析和向量
入库属于重要长任务，生产中应交给独立 Worker，并使用 Celery、RQ、ARQ、
Dramatiq 或 Redis Streams 等经过验证的队列方案。

最小实验使用 `asyncio.Queue` 只为看清生产者、队列和消费者的关系：

```text
FastAPI 路由是生产者
asyncio.Queue 模拟消息队列
worker_once 是消费者
redis_progress 模拟 Redis
mysql_documents 模拟 MySQL
```

## 编码任务

1. MySQL 保存文档记录、哈希、状态、错误摘要和 Chunk 数量。
2. MinIO 保存原始文件，数据库只存对象键。
3. Redis 保存短期任务进度，不作为最终事实来源。
4. 摄取任务使用文档 ID 保证幂等。
5. 删除流程清理向量、对象和业务记录，失败时可重试。

## 验收标准

- 上传后立即返回任务或文档 ID。
- 前端可轮询到解析状态。
- 同一文件重复上传不会重复索引。
- 解析失败可查看安全错误摘要并重试。
- 删除后无法再检索到原文 Chunk。

## 面试自测

1. 为什么 Redis 不应该成为文档最终状态的唯一来源？
2. MySQL 成功但 Milvus 失败时如何处理？
3. 如何保证后台任务幂等？

## 学习记录

```text
完成日期：
实际投入时间：
失败重试测试：
重复上传测试：
删除一致性测试：
对应 Git 提交：
```
