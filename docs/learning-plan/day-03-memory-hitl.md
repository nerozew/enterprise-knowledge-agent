# Day 03：Memory、Checkpointer 与人工确认

## 今日目标

让同一会话可以恢复历史状态，并在保存、删除等写操作前暂停等待用户确认。

## 今日时间表

1. 09:00-10:30：学习短期记忆、长期记忆和业务数据库的区别。
2. 10:45-12:00：接入 `InMemorySaver` 与 `thread_id`。
3. 14:00-15:30：学习 `interrupt()` 和 `Command(resume=...)`。
4. 15:45-17:00：完成“确认后保存”工作流。
5. 17:00-18:00：测试会话隔离和恢复。

## 必学知识

- `【非常重点】短期记忆`：同一 `thread_id` 中的消息和 Graph State。
- `【非常重点】Checkpointer`：在每个步骤保存状态，使流程可恢复。
- `【非常重点】Human-in-the-loop`：高风险操作暂停、展示参数、等待确认后恢复。
- `【重点】长期记忆`：跨 thread 保存用户偏好，通常使用 Store 或业务数据库。
- `【重点】会话隔离`：不同用户、不同会话不得复用错误的 `thread_id`。

旧教程里的 `ConversationBufferMemory` 不作为本项目主线。使用 LangGraph State + Checkpointer。

## 先理解概念：Checkpoint 与 Checkpointer

### Checkpoint 是什么

Checkpoint 是 Graph 在某个执行步骤结束时的 State 快照。它不只是聊天文本，还可以包含当前 State 字段、下一步节点、配置和执行元数据。可以把它理解成工作流的一张“存档”。

```text
步骤 1 完成 -> Checkpoint 1
步骤 2 完成 -> Checkpoint 2
发生暂停/失败 -> 从已保存的 Checkpoint 恢复
```

### Checkpointer 是什么

Checkpointer 是负责保存和读取 Checkpoint 的组件/接口。`compile(checkpointer=...)` 表示把一个具体的保存器接入 Graph Runtime。

```text
StateGraph        定义状态与流程
Checkpoint        某一步的状态快照数据
Checkpointer      保存/读取快照的组件
thread_id         快照属于哪个会话的标识
```

Checkpointer使同一 thread 的多次调用恢复历史，也为 Human-in-the-loop、故障恢复和时间旅行提供基础。它不会让模型参数产生记忆；模型之所以能参考过去，是因为历史 State 被恢复并再次传给模型。

### InMemorySaver 是什么

```python
from langgraph.checkpoint.memory import InMemorySaver
```

`InMemorySaver` 是 LangGraph提供的固定类，是 `BaseCheckpointSaver` 的一个具体实现。它使用当前 Python 进程内的内存字典保存 Checkpoint：

```text
BaseCheckpointSaver       抽象保存器/父类
└── InMemorySaver          内存实现，适合学习、调试和测试
```

因此它不是 Checkpointer 的唯一固定搭配。`checkpointer` 是能力角色，`InMemorySaver` 只是当前选择的实现：

```python
checkpointer = InMemorySaver()
```

- `checkpointer`：自定义变量名，可改成 `memory`。
- `InMemorySaver`：官方固定类名。
- `InMemorySaver()`：创建保存器实例。

官方文档还列出 SQLite/Postgres 等持久化实现。当前项目的 MySQL 业务表负责保存灵感等业务数据，不应与 Graph Checkpoint 概念混在一起；生产 Checkpointer需要选择官方支持的持久化实现或实现兼容的 Saver。

### 官方最小用法

```python
checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)

config = {
    "configurable": {
        "thread_id": "thread-1"
    }
}

result = graph.invoke(input_state, config=config)
```

### config 与 configurable 是什么

```python
config = {
    "configurable": {
        "thread_id": "thread-1"
    }
}
```

外层 `config` 是开发者自定义的 Python 变量名，可以改成 `run_config`。传给 `invoke(..., config=...)` 的值遵循 LangChain固定的 `RunnableConfig` 结构，用于控制“这次怎么运行”，而不是描述业务 State。

当前 `RunnableConfig` 的常见固定顶层字段包括：

```text
tags、metadata、callbacks、run_name、max_concurrency、
recursion_limit、configurable、run_id
```

`"configurable"` 是 `RunnableConfig` 中的固定键，值是一个字典，用来放运行组件需要读取的可配置值。对于 Checkpointer：

```text
configurable  固定配置容器
thread_id     Checkpointer约定的固定键
thread-1      开发者自定义的会话 ID 值
```

State 与 Config 的边界：

```python
input_state = {
    "messages": [...],
    "intent": "save",
}

run_config = {
    "configurable": {"thread_id": "thread-1"},
    "recursion_limit": 20,
    "tags": ["learning"],
}

result = graph.invoke(input_state, config=run_config)
```

- `input_state`：节点需要读取和更新的业务/工作流数据。
- `run_config`：本次执行的身份、追踪和运行控制信息。

默认情况下 `thread_id` 不会作为聊天内容传给模型；Checkpointer在 Graph Runtime 层使用它查找正确的会话快照。

执行时的概念过程：

```text
根据 thread_id 读取最新 Checkpoint
  -> 与本次 input_state 合并
  -> 执行 Graph
  -> 每个步骤保存新的 Checkpoint
  -> 返回最终 State
```

没有 Checkpointer时，Graph仍然可以运行，但多次 `invoke()` 不会自动恢复上一轮 State。接入 Checkpointer后必须提供 `thread_id`，否则保存器不知道应该读取和写入哪个会话。

## 官方 API 依据

官方文档：https://docs.langchain.com/oss/python/langgraph/persistence

当前 `langgraph 1.2.8` 环境确认：

```python
from langgraph.checkpoint.memory import InMemorySaver
```

```text
InMemorySaver()                         创建内存 Checkpointer
builder.compile(checkpointer=...)       编译时接入 Checkpointer
{"configurable": {"thread_id": ...}}  调用时指定会话标识
```

固定与自定义：

```text
InMemorySaver、compile、checkpointer、configurable、thread_id  固定 API/配置键
checkpointer、config_a、session-a                             自定义变量或值
```

## 检查点 1：同 thread 恢复，不同 thread 隔离

这个实验不调用 LLM，只验证 Checkpointer 如何保存和恢复 MessagesState。

```python
from langchain.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph


def count_messages(state: MessagesState) -> dict:
    count = len(state["messages"])
    return {
        "messages": [
            AIMessage(content=f"节点执行时共有 {count} 条消息")
        ]
    }


builder = StateGraph(MessagesState)
builder.add_node("count_messages", count_messages)
builder.add_edge(START, "count_messages")
builder.add_edge("count_messages", END)

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)

config_a = {
    "configurable": {
        "thread_id": "session-a"
    }
}

config_b = {
    "configurable": {
        "thread_id": "session-b"
    }
}

result_1 = graph.invoke(
    {"messages": [HumanMessage(content="第一轮")]},
    config=config_a,
)

result_2 = graph.invoke(
    {"messages": [HumanMessage(content="第二轮")]},
    config=config_a,
)

result_3 = graph.invoke(
    {"messages": [HumanMessage(content="新会话")]},
    config=config_b,
)

print(len(result_1["messages"]), result_1["messages"][-1].content)
print(len(result_2["messages"]), result_2["messages"][-1].content)
print(len(result_3["messages"]), result_3["messages"][-1].content)
```

预期输出：

```text
2 节点执行时共有 1 条消息
4 节点执行时共有 3 条消息
2 节点执行时共有 1 条消息
```

精确时序：

```text
session-a 第一次：载入 0 + 新 Human 1 -> 节点看到 1 -> 新 AI -> 保存 2
session-a 第二次：载入 2 + 新 Human 1 -> 节点看到 3 -> 新 AI -> 保存 4
session-b 第一次：载入 0 + 新 Human 1 -> 节点看到 1 -> 新 AI -> 保存 2
```

### Checkpointer 不在 START 节点上

```python
graph = builder.compile(checkpointer=checkpointer)
```

这表示把 Checkpointer接入整张图的 Graph Runtime，而不是把它注册成 START 后面的节点。它不会出现在 `add_node()`、`add_edge()` 或 Mermaid业务节点图中。运行前 Runtime用它加载状态，步骤结束后 Runtime用它保存状态。

当前例子同时依赖三个组件：

```text
thread_id      选择读取哪个会话存档槽
Checkpointer   加载/保存该会话的 State 快照
add_messages   把恢复的旧消息与本轮新消息合并
```

可以近似想象 `InMemorySaver` 内部按 thread 分区保存：

```python
{
    "session-a": [Human1, AI1, Human2, AI2],
    "session-b": [HumanB, AIB],
}
```

真实实现还保存历史版本和执行元数据，上述字典只用于理解最新 messages。

第二次调用 `session-a` 前后的逐条变化：

```text
从 Checkpointer恢复：
1. HumanMessage("第一轮")
2. AIMessage("节点执行时共有 1 条消息")

通过 add_messages合并本轮输入：
3. HumanMessage("第二轮")

此时进入节点，所以 count == 3。

节点返回并由 add_messages合并：
4. AIMessage("节点执行时共有 3 条消息")

最终 result_2 有 4 条，并保存为 session-a 的最新 State。
```

第三次使用 `session-b`，Checkpointer会查找另一个存档槽。该槽为空，因此不会载入 `session-a` 的四条消息，只处理新 HumanMessage并新增一条 AIMessage，最终为 2。

如果移除 Checkpointer，三次调用都是独立运行，结果会近似为 `2、2、2`；接入 Checkpointer并使用 `session-a、session-a、session-b` 后才得到 `2、4、2`。

`thread_id` 不是 State 字段，也不会发送给 LLM。它是 Checkpointer 查找会话检查点的键，可以近似理解成会话状态的主键。

`InMemorySaver` 只把 Checkpoint 保存在当前 Python 进程内，程序退出后数据消失，适合学习和测试，不是生产持久化方案。

`【非常重点】` MessagesState 定义“状态如何合并”，Checkpointer定义“状态保存在哪里、下一次如何恢复”，thread_id定义“保存到哪个会话”。

### 四层关系校准

不要把 `configurable` 理解成消息容器。准确关系是：

```text
configurable   RunnableConfig 中携带可配置运行参数的字典
thread_id      configurable 中用于标识会话的键
Checkpointer   根据 thread_id 读取和保存 Checkpoint
MessagesState  真正保存 messages 等工作流数据的 State
```

因此可以近似理解为：

```text
run_config["configurable"]["thread_id"]
    -> 告诉 Checkpointer 应该操作哪个 thread
    -> Checkpointer 找到该 thread 的最新 Checkpoint
    -> Checkpoint.values 中保存着 messages 等 State 数据
```

`session-a` 不是容器，而是会话 ID 的一个自定义字符串值。消息也不直接存在 `configurable` 中。

本检查点验收：

1. 同一个 `session-a` 第二轮累计为 4 条消息。
2. 新的 `session-b` 不读取 `session-a`，最终只有 2 条消息。
3. 能解释 `MessagesState`、Checkpointer、thread_id 的职责差别。

## 检查点 2：使用 get_state 查看最新快照

`graph.get_state(config)` 是 LangGraph 编译图提供的固定 API。它根据配置中的 `thread_id` 读取该 thread 最新的 Checkpoint，并返回一个 `StateSnapshot` 对象。

在检查点 1 的代码后追加：

```python
snapshot = graph.get_state(config_a)

print("快照类型：", type(snapshot))
print("状态值：", snapshot.values)
print("下一节点：", snapshot.next)
print("快照配置：", snapshot.config)
print("执行元数据：", snapshot.metadata)
print("创建时间：", snapshot.created_at)
```

重点字段：

```text
snapshot.values       此时保存的 State，messages 就在这里
snapshot.next         接下来要执行的节点；空元组 () 表示图已结束
snapshot.config       thread_id、checkpoint_id 等快照标识
snapshot.metadata     步骤编号、最近哪个节点写入了什么等信息
snapshot.created_at   快照创建时间
```

固定与自定义：

```text
graph.get_state、StateSnapshot、values、next、metadata  官方固定 API/属性
snapshot、config_a                                      自定义变量名
```

这一步验证了：`config_a` 只负责定位 thread；真正的消息数据位于 `snapshot.values["messages"]`。

官方依据：https://docs.langchain.com/oss/python/langgraph/persistence#get-state

## 检查点 3：真正的 LLM 多轮短期记忆

### 什么是短期记忆

LangGraph 的短期记忆是 `thread` 范围内的 State。它不是把知识写入模型参数，而是在下一次调用相同 `thread_id` 时恢复旧消息，再把完整消息历史传给模型。

```text
同一 thread_id：恢复旧 messages + 合并新 HumanMessage + 调用模型
不同 thread_id：读取另一个独立的 State，不共享 messages
```

官方文档：https://docs.langchain.com/oss/python/langgraph/add-memory#add-short-term-memory

### 实验代码

新建练习文件 `Day3/day_3.2.py`：

```python
from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from demo1 import llm


def call_model(state: MessagesState) -> dict:
    print("模型本次收到的历史消息数：", len(state["messages"]))
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


builder = StateGraph(MessagesState)
builder.add_node("call_model", call_model)
builder.add_edge(START, "call_model")
builder.add_edge("call_model", END)

graph = builder.compile(checkpointer=InMemorySaver())

config_a = {"configurable": {"thread_id": "session-a"}}
config_b = {"configurable": {"thread_id": "session-b"}}

# session-a 第一轮：建立一段会话历史。
result_a1 = graph.invoke(
    {
        "messages": [
            HumanMessage(content="请记住：我叫小明，正在学习 LangGraph。")
        ]
    },
    config=config_a,
)

# session-a 第二轮：Checkpointer 会先恢复上一轮的两条消息。
result_a2 = graph.invoke(
    {
        "messages": [
            HumanMessage(content="我叫什么名字？")
        ]
    },
    config=config_a,
)

# session-b 是全新会话，不应该拥有 session-a 的历史。
result_b1 = graph.invoke(
    {
        "messages": [
            HumanMessage(
                content="我叫什么名字？如果上下文没有提供，请回答不知道。"
            )
        ]
    },
    config=config_b,
)

print("\nsession-a 最终消息数：", len(result_a2["messages"]))
result_a2["messages"][-1].pretty_print()

print("\nsession-b 最终消息数：", len(result_b1["messages"]))
result_b1["messages"][-1].pretty_print()

snapshot_a = graph.get_state(config_a)
snapshot_b = graph.get_state(config_b)
print("\n快照 A 消息数：", len(snapshot_a.values["messages"]))
print("快照 B 消息数：", len(snapshot_b.values["messages"]))
```

### 预期过程

```text
session-a 第一轮：模型收到 1 条，返回后 State 共 2 条
session-a 第二轮：恢复 2 条 + 新 Human 1 条，模型收到 3 条
                    返回新 AI 后 State 共 4 条
session-b 第一轮：没有旧状态，模型收到 1 条，返回后 State 共 2 条
```

关键点：

1. `graph.invoke()` 运行整张图；`llm.invoke()` 只在 `call_model` 节点中调用一次模型。
2. `call_model` 没有手动读取 Checkpointer；Graph Runtime 会在节点执行前自动恢复 State。
3. 模型能回答“小明”，是因为收到的 `state["messages"]` 中包含第一轮消息。
4. `session-b` 不包含“小明”的消息，说明 thread 状态相互隔离。
5. `result_a2` 是本次运行结束后的完整 State；`result_a2["messages"][-1]` 才是最新 AI 回复。

固定与自定义：

```text
MessagesState、StateGraph、compile、invoke、get_state  官方固定 API
call_model、builder、config_a、session-a             自定义名称或值
```

本检查点验收：

1. 能解释为什么模型第二次收到 3 条消息，而最终 State 有 4 条。
2. `session-a` 最终有 4 条消息，`session-b` 最终有 2 条消息。
3. 能解释短期记忆是 thread 范围内恢复的 State，不是模型参数发生改变。

## 检查点 4：interrupt 与 Command(resume=...) 人工审批

### 先理解概念

`interrupt()` 用于在节点内部动态暂停 Graph，并把一段可 JSON 序列化的数据交给外部调用者。暂停时 Checkpointer 保存当前状态，Graph 会一直等待，直到外部使用相同 `thread_id` 和 `Command(resume=...)` 恢复。

```text
节点调用 interrupt(审批信息)
        ↓
Checkpointer 保存当前状态
        ↓
graph.invoke 返回 __interrupt__
        ↓
外部用户查看并作出决定
        ↓
graph.invoke(Command(resume=决定), 相同 config)
        ↓
决定成为 interrupt() 的返回值
        ↓
节点继续完成保存或拒绝
```

官方导入路径：

```python
from langgraph.types import Command, interrupt
```

当前 `langgraph 1.2.8` 环境中的真实签名：

```text
interrupt(value: Any) -> Any
Command(*, graph=None, update=None, resume=None, goto=())
```

官方文档：https://docs.langchain.com/oss/python/langgraph/interrupts

### 最小审批实验

新建 `Day3/day_3.3.py`：

```python
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    title: str
    status: str
    result: str


idea_store: list[str] = []


def review_and_save(state: State) -> dict:
    print("进入审批节点")

    approved = interrupt({
        "question": "是否保存这条灵感？",
        "title": state["title"],
    })

    if not approved:
        return {
            "status": "rejected",
            "result": "用户拒绝保存",
        }

    idea_store.append(state["title"])
    return {
        "status": "saved",
        "result": f"保存成功：{state['title']}",
    }


builder = StateGraph(State)
builder.add_node("review_and_save", review_and_save)
builder.add_edge(START, "review_and_save")
builder.add_edge("review_and_save", END)

graph = builder.compile(checkpointer=InMemorySaver())

config = {
    "configurable": {
        "thread_id": "approval-1"
    }
}

# 第一次调用：运行到 interrupt() 后暂停。
paused_result = graph.invoke(
    {
        "title": "学习 HITL",
        "status": "pending",
        "result": "",
    },
    config=config,
)

print("暂停结果：", paused_result)
print("审批信息：", paused_result["__interrupt__"][0].value)
print("暂停时 idea_store：", idea_store)
print("等待执行的节点：", graph.get_state(config).next)

# 第二次调用：必须使用相同 thread_id 恢复。
final_result = graph.invoke(
    Command(resume=True),
    config=config,
)

print("最终状态：", final_result)
print("恢复后 idea_store：", idea_store)
```

### 预期输出与含义

```text
进入审批节点
暂停结果：... '__interrupt__': [Interrupt(...)]
暂停时 idea_store：[]
等待执行的节点：('review_and_save',)

进入审批节点
最终状态：... 'status': 'saved' ...
恢复后 idea_store：['学习 HITL']
```

第一次调用中，`interrupt(...)` 后面的代码没有执行，所以 `idea_store` 仍为空。恢复时：

```python
approved = interrupt(...)
```

可以近似理解为 `interrupt(...)` 返回了 `Command(resume=True)` 中的 `True`，因此程序通过 `if not approved`，执行真正的保存操作。

### interrupt 为什么能中断：Runtime 的特殊机制

`interrupt()` 在 Python 语法层面确实是一个函数，但它不是普通工具函数。它是 `langgraph.types` 提供、由 LangGraph Runtime 配合识别的固定 API。

当前版本源码的关键逻辑可以简化成：

```python
def interrupt(value):
    if Runtime 中已经存在对应的 resume 值:
        return resume值

    raise GraphInterrupt(Interrupt(value=value, id=自动生成的ID))
```

第一次执行时没有 resume 值：

```text
interrupt(payload)
  -> 抛出内部 GraphInterrupt
  -> Graph Runtime 捕获它
  -> Checkpointer 保存当前 State 和待执行节点
  -> graph.invoke 返回 __interrupt__ 信息
```

它不是普通业务报错。`GraphInterrupt` 是 LangGraph 用来把控制权交还给外部调用者的内部控制信号。

第二次执行时：

```text
Command(resume=True)
  -> Runtime 根据相同 thread_id 载入暂停任务
  -> 把 True 注册为该 interrupt 的 resume 值
  -> 从 review_and_save 节点开头重新执行
  -> interrupt(payload) 找到 resume 值
  -> 本次直接 return True，不再暂停
```

因此下面这一行具有两种结果：

```python
approved = interrupt(payload)
```

```text
第一次：没有完成赋值，Graph 在这里暂停
恢复后：approved = True（或 Command 中提供的其他值）
```

### 输出对象逐项解释

```python
"__interrupt__": [
    Interrupt(
        value={"question": "是否保存这条灵感？", "title": "学习 HITL"},
        id="b06bc..."
    )
]
```

- `__interrupt__`：LangGraph 返回中断信息时使用的固定特殊键；前后各有两个下划线。
- 列表：一轮 Graph 可能有多个并行节点同时暂停，所以需要保存多个 Interrupt。
- `Interrupt`：LangGraph 的固定类，本次列表里放着它的一个实例。
- `.value`：调用 `interrupt(payload)` 时传入的 payload。
- `.id`：Runtime 自动生成的中断标识，用于复杂情况下匹配中断和恢复值。

聊天界面可能把双下划线渲染成加粗符号，但 Python 字典中的真实键仍是 `"__interrupt__"`。

### 两种“保存”不要混淆

本例中存在两个完全不同的保存动作：

```text
Checkpointer 保存 Checkpoint    LangGraph Runtime 自动完成
idea_store.append(...)          你编写的业务代码完成
```

`print()` 只负责把已有对象显示在终端，不会保存 State，也不会向 `idea_store` 写入数据。

第一次暂停时 Checkpointer 已经保存了工作流快照，但 `idea_store` 仍是空列表。只有恢复后执行到：

```python
idea_store.append(state["title"])
```

业务数据才真正加入列表。

### 非常重要：恢复会从节点开头重新执行

恢复并不是 Python 在原调用栈的下一行继续运行。LangGraph 会从发生中断的节点开头重新执行，并让对应的 `interrupt()` 返回已提供的 resume 值。因此示例中的“进入审批节点”会打印两次。

错误写法：

```python
idea_store.append(state["title"])
approved = interrupt("是否确认？")
```

恢复时节点从头执行，可能重复写入。正确原则：

```text
interrupt 前：只做可重复、无副作用或幂等的准备工作
interrupt 后：执行数据库写入、发消息、扣款等副作用
```

如果副作用必须放在 `interrupt()` 前，则必须使用唯一键、幂等键或写入前查询等方式防止重复执行。

### 固定 API 与自定义名称

```text
interrupt、Command、resume、__interrupt__、Interrupt.value  官方固定 API/字段
review_and_save、approved、paused_result、approval-1          自定义名称或值
```

注意：

1. `interrupt()` 的 payload 应该是字符串、数字、布尔值、列表或字典等可 JSON 序列化数据。
2. 恢复必须使用暂停时相同的 `thread_id`。
3. 当前只学习 `Command(resume=...)`；`update`、`goto`、`graph` 后面再学。
4. `Command(resume=True)` 是本次 `graph.invoke()` 的输入，不是普通 State 字典。

本检查点验收：

1. 第一次运行后 `idea_store` 仍为空。
2. `paused_result["__interrupt__"][0].value` 能看到审批内容。
3. 使用同一 `thread_id` 和 `Command(resume=True)` 后才真正保存。
4. 能解释为什么“进入审批节点”会打印两次。

## 检查点 5：结构化审批与人工修改

上一节只传递布尔值：

```python
Command(resume=True)
Command(resume=False)
```

实际产品通常需要三种操作：批准、拒绝、修改后批准。因此 `resume` 可以传入可 JSON 序列化的字典，而不仅是布尔值。

数据流方向：

```text
interrupt(payload)          Graph -> 调用方/UI，展示待审批信息
Command(resume=response)    调用方/UI -> Graph，提交人的决定
```

### 练习代码

新建 `Day3/day_3.4.py`：

```python
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    title: str
    status: str
    result: str


idea_store: list[str] = []


def review_and_save(state: State) -> dict:
    decision = interrupt({
        "question": "请审核这条灵感",
        "title": state["title"],
        "allowed_actions": ["approve", "reject", "edit"],
    })

    action = decision.get("action")

    if action == "reject":
        return {
            "status": "rejected",
            "result": "用户拒绝保存",
        }

    if action == "edit":
        final_title = decision.get("edited_title", "").strip()
        if not final_title:
            return {
                "status": "rejected",
                "result": "修改后的标题不能为空",
            }
    elif action == "approve":
        final_title = state["title"]
    else:
        return {
            "status": "rejected",
            "result": "无法识别的审批操作",
        }

    idea_store.append(final_title)
    return {
        "title": final_title,
        "status": "saved",
        "result": f"保存成功：{final_title}",
    }


builder = StateGraph(State)
builder.add_node("review_and_save", review_and_save)
builder.add_edge(START, "review_and_save")
builder.add_edge("review_and_save", END)

graph = builder.compile(checkpointer=InMemorySaver())

config = {
    "configurable": {
        "thread_id": "approval-edit-1"
    }
}

paused_result = graph.invoke(
    {
        "title": "学习 HITL",
        "status": "pending",
        "result": "",
    },
    config=config,
)

print("审批单：", paused_result["__interrupt__"][0].value)
print("暂停时：", idea_store)

final_result = graph.invoke(
    Command(resume={
        "action": "edit",
        "edited_title": "深入学习 LangGraph HITL",
    }),
    config=config,
)

print("最终状态：", final_result)
print("最终存储：", idea_store)
```

预期结果：

```text
审批单：{
    'question': '请审核这条灵感',
    'title': '学习 HITL',
    'allowed_actions': ['approve', 'reject', 'edit']
}
暂停时：[]
最终状态：{
    'title': '深入学习 LangGraph HITL',
    'status': 'saved',
    'result': '保存成功：深入学习 LangGraph HITL'
}
最终存储：['深入学习 LangGraph HITL']
```

恢复时可以近似理解为：

```python
decision = {
    "action": "edit",
    "edited_title": "深入学习 LangGraph HITL",
}
```

### 哪些固定，哪些自定义

```text
interrupt、Command、resume、__interrupt__  LangGraph 固定 API/字段
action、approve、reject、edit               当前项目自定义审批协议
edited_title、allowed_actions               当前项目自定义字段
```

LangGraph 不理解 `"approve"` 或 `"edit"` 的业务含义。它只负责把 `resume` 的值交还给 `interrupt()`；之后如何判断和处理，完全由 `review_and_save` 的 Python 代码决定。

### 三种测试输入

批准原内容：

```python
Command(resume={"action": "approve"})
```

拒绝：

```python
Command(resume={"action": "reject"})
```

修改后批准：

```python
Command(resume={
    "action": "edit",
    "edited_title": "深入学习 LangGraph HITL",
})
```

每次测试应使用新的 `thread_id`，因为一个已经完成的 thread 不再处于等待该审批的状态。

官方模式依据：https://docs.langchain.com/oss/python/langgraph/interrupts#review-and-edit-state

本检查点验收：

1. 能解释 payload 和 resume value 的数据方向。
2. 能说明 `action` 等字段是业务自定义协议，不是 LangGraph 固定字段。
3. 能分别跑通 approve、reject、edit 三条路径。
4. edit 路径最终保存修改后的标题，而不是原始标题。

## 检查点 6：在 Agent 工具内部使用 interrupt

### 为什么把审批放进工具

前面的 `review_and_save` 是普通 Graph 节点。真实 Agent 中，保存、删除、发送邮件等能力通常由工具提供。将 `interrupt()` 放在高风险工具内部，可以让审批规则与工具绑定：无论哪个 Agent 调用该工具，真正产生副作用前都会暂停。

```text
节点级审批：某一条 Graph 路径负责审批
工具级审批：工具本身保证每次执行前都审批
```

官方模式：https://docs.langchain.com/oss/python/langgraph/interrupts#interrupts-in-tools

当前版本固定导入与签名：

```python
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.types import Command, interrupt
```

```text
create_agent(..., checkpointer=...) -> CompiledStateGraph
```

### 练习代码

新建 `Day3/day_3.5.py`：

```python
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from demo1 import llm


idea_store: list[str] = []


@tool
def save_idea(title: str) -> str:
    """保存一条用户明确要求保存的灵感。"""

    decision = interrupt({
        "action_name": "save_idea",
        "question": "是否确认保存这条灵感？",
        "title": title,
        "allowed_actions": ["approve", "reject", "edit"],
    })

    action = decision.get("action")

    if action == "reject":
        return f"用户取消保存：{title}"

    if action == "edit":
        final_title = decision.get("edited_title", "").strip()
        if not final_title:
            return "保存失败：修改后的标题不能为空"
    elif action == "approve":
        final_title = title
    else:
        return "保存失败：无法识别的审批操作"

    # 真正的副作用必须位于 interrupt() 后面。
    idea_store.append(final_title)
    return f"保存成功：{final_title}"


agent = create_agent(
    model=llm,
    tools=[save_idea],
    system_prompt=(
        "你是灵感助手。用户明确要求保存灵感时，必须调用 save_idea；"
        "工具完成后，根据工具结果向用户回复。"
    ),
    checkpointer=InMemorySaver(),
)

config = {
    "configurable": {
        "thread_id": "tool-approval-1"
    }
}

# 第一阶段：模型产生 tool_call，工具执行到 interrupt 后暂停。
paused_result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "请保存灵感：学习工具内的 interrupt",
            }
        ]
    },
    config=config,
)

print("审批内容：", paused_result["__interrupt__"][0].value)
print("暂停时存储：", idea_store)

# 第二阶段：使用同一 thread_id 批准并恢复。
final_result = agent.invoke(
    Command(resume={"action": "approve"}),
    config=config,
)

print("恢复后存储：", idea_store)

for message in final_result["messages"]:
    message.pretty_print()
```

### 完整执行顺序

```text
1. HumanMessage：请保存灵感
2. 模型返回 AIMessage，其中包含 save_idea 的 tool_call
3. Agent Runtime 开始执行 save_idea
4. 工具运行到 interrupt(payload)，Graph 暂停
5. 此时没有执行 idea_store.append，也还没有 ToolMessage
6. 外部使用 Command(resume={"action": "approve"}) 恢复
7. 工具函数从开头重新执行，interrupt 返回审批字典
8. 执行 idea_store.append，工具返回“保存成功”
9. Agent Runtime 将工具结果包装为 ToolMessage
10. 模型读取 ToolMessage，生成最终 AIMessage
```

暂停时消息中通常已经存在 HumanMessage 和带 tool_call 的 AIMessage，但不存在工具执行结果。ToolMessage 只有在工具恢复并真正返回后才会产生。

### 两种职责再次分开

```text
模型                 决定调用 save_idea，并生成 title 参数
Agent/Graph Runtime   调度工具、处理中断、保存和恢复状态
人类/UI               提交 approve、reject 或 edit 决定
save_idea 函数体      根据决定执行真实业务逻辑
```

### 固定 API 与自定义协议

```text
@tool、create_agent、checkpointer、interrupt、Command、resume  固定 API
save_idea、idea_store、action_name、allowed_actions              自定义名称/字段
approve、reject、edit、edited_title                              自定义审批协议
```

本检查点验收：

1. 暂停时 `idea_store` 为空，恢复批准后只有一条内容。
2. 能解释为什么暂停时还没有 ToolMessage。
3. 能按顺序说明 HumanMessage、AIMessage、Interrupt、ToolMessage、AIMessage。
4. 将 resume 改为 reject 时，工具不产生保存副作用。

## 检查点 7：短期记忆、长期记忆与业务数据库

### 三种数据不要混淆

```text
Checkpointer / Checkpoint   保存某个 thread 的 Graph State，属于短期记忆
LangGraph Store            按 namespace/key 保存跨 thread 的 Agent 记忆
MySQL 业务数据库           保存用户、灵感、任务、文档等权威业务数据
```

示例：

```text
对话消息、当前中断位置       -> Checkpointer
用户喜欢简洁回答、常用语言   -> Store
真实灵感、待办、账号和权限   -> MySQL 业务表
```

`长期记忆` 中的“长期”主要表示可以跨 thread 访问。`InMemoryStore` 仍然只存在于当前 Python 进程，程序退出后会消失；生产环境必须换成持久化 Store。

官方文档：https://docs.langchain.com/oss/python/langgraph/add-memory#add-long-term-memory

### InMemoryStore 固定 API

```python
from langgraph.store.memory import InMemoryStore
```

当前 `langgraph 1.2.8` 实际签名的核心部分：

```text
put(namespace: tuple[str, ...], key: str, value: dict) -> None
get(namespace: tuple[str, ...], key: str) -> Item | None
search(namespace_prefix: tuple[str, ...], ...) -> list[SearchItem]
```

数据组织方式：

```text
namespace   类似多层文件夹，用于隔离用户和记忆类别
key         类似文件名，在 namespace 中唯一标识一条记忆
value       真正保存的 JSON 风格字典
```

### 最小练习

新建 `Day3/day_3.6.py`：

```python
from langgraph.store.memory import InMemoryStore


store = InMemoryStore()

user_1_namespace = (
    "users",
    "user-1",
    "preferences",
)

user_2_namespace = (
    "users",
    "user-2",
    "preferences",
)

store.put(
    user_1_namespace,
    "response-style",
    {
        "language": "中文",
        "style": "简洁",
    },
)

store.put(
    user_2_namespace,
    "response-style",
    {
        "language": "英文",
        "style": "详细",
    },
)

user_1_item = store.get(
    user_1_namespace,
    "response-style",
)

user_2_item = store.get(
    user_2_namespace,
    "response-style",
)

print("user-1 Item：", user_1_item)
print("user-1 value：", user_1_item.value)
print("user-2 value：", user_2_item.value)

user_1_memories = store.search(user_1_namespace)
print("user-1 搜索结果：", user_1_memories)
```

### put 与 get 逐行拆解

```python
store = InMemoryStore()
```

执行 `InMemoryStore()` 创建一个 Store 对象，变量 `store` 指向这个对象。它的内部能够保存 namespace、key、value 和时间等信息。

```python
store.put(
    user_1_namespace,
    "response-style",
    {
        "language": "中文",
        "style": "简洁",
    },
)
```

这里调用 `store` 对象的固定方法 `put()`。三个位置参数按照方法签名依次对应：

```text
user_1_namespace       -> namespace
"response-style"      -> key
{...}                  -> value
```

用关键字参数写出来会更直观：

```python
store.put(
    namespace=user_1_namespace,
    key="response-style",
    value={
        "language": "中文",
        "style": "简洁",
    },
)
```

两种写法含义相同。`put()` 完成写入后返回 `None`，真正数据保存在 `store` 对象内部。

仅用于理解，可以把内部结构近似想象成：

```python
{
    ("users", "user-1", "preferences"): {
        "response-style": {
            "value": {
                "language": "中文",
                "style": "简洁",
            },
            "created_at": "...",
            "updated_at": "...",
        }
    }
}
```

这不是 InMemoryStore 的真实源码结构，只是帮助理解“namespace 是分区，key 定位记录，value 是内容”。

读取：

```python
user_1_item = store.get(
    user_1_namespace,
    "response-style",
)
```

Python 先执行等号右侧：

```python
store.get(user_1_namespace, "response-style")
```

它使用：

```text
namespace = ("users", "user-1", "preferences")
key       = "response-style"
```

寻找刚才保存的记录。找到后返回 `Item` 对象，最后等号把这个对象赋值给自定义变量 `user_1_item`。

等价的分步理解：

```python
returned_item = store.get(
    user_1_namespace,
    "response-style",
)

user_1_item = returned_item
```

`get()` 只是读取，不会删除记录。找不到时返回 `None`。

地址必须匹配：

```python
store.get(user_1_namespace, "response-style")  # 找到 user-1 的记录
store.get(user_2_namespace, "response-style")  # 读取 user-2 的另一条记录
store.get(user_1_namespace, "missing")         # 返回 None
```

同一个 namespace 和 key 再次执行 `put()`，表示更新该地址的记录；不同 namespace 或不同 key 则表示另一条记录。

预期核心输出：

```text
user-1 value：{'language': '中文', 'style': '简洁'}
user-2 value：{'language': '英文', 'style': '详细'}
```

### get 为什么返回 Item

```python
user_1_item = store.get(namespace, key)
```

返回的不是 value 字典本身，而是 `Item` 对象，里面同时包含：

```text
item.namespace    所属 namespace
item.key          记忆键
item.value        保存的业务字典
item.created_at   创建时间
item.updated_at   更新时间
```

所以：

```python
user_1_item.value
```

才得到：

```python
{"language": "中文", "style": "简洁"}
```

这和刚学过的 `Interrupt.value` 属于同一种 Python 点号属性访问语法，但 `Item` 与 `Interrupt` 是两个不同的类。

严格代码还应处理不存在的情况，因为 `get()` 可能返回 `None`：

```python
item = store.get(user_1_namespace, "missing-key")

if item is None:
    print("没有找到这条记忆")
else:
    print(item.value)
```

### namespace 不是 thread_id

```text
thread_id    Checkpointer 的固定查找键，区分会话
namespace    Store 的业务分区元组，结构由开发者设计
```

同一个用户可以有多个 thread，但都使用相同用户 namespace 读取偏好：

```text
thread-a ─┐
thread-b ─┼─> ("users", "user-1", "preferences")
thread-c ─┘
```

因此短期消息彼此隔离，长期用户偏好仍可跨会话共享。

### 固定与自定义

```text
InMemoryStore、put、get、search、Item.value  官方固定类/API/属性
store、user_1_namespace                       自定义变量名
("users", "user-1", "preferences")        自定义 namespace 设计
"response-style"、language、style            自定义 key/业务字段
```

本检查点验收：

1. 能解释 Checkpointer、Store 和 MySQL 的职责差别。
2. 能说明 namespace、key、value 三层结构。
3. user-1 与 user-2 的偏好不会混淆。
4. 能解释为什么 `store.get()` 后需要 `.value`。

## 检查点 8：将 Store 注入 Graph 节点

上一节是在 Graph 外直接执行：

```python
store.put(...)
item = store.get(...)
```

现在要让节点在运行时读取 Store。官方推荐通过 `Runtime` 获取运行上下文和 Store：

```python
from dataclasses import dataclass

from langgraph.runtime import Runtime


@dataclass
class Context:
    user_id: str


def node(state: State, runtime: Runtime[Context]) -> dict:
    user_id = runtime.context.user_id
    item = runtime.store.get(namespace, key)
    return {...}
```

### 四种数据的职责

```text
State      会在节点之间传递和更新的工作流数据
Context    本次运行所需的身份或依赖，例如 user_id
Config     thread_id、回调、递归限制等运行控制信息
Store      跨 thread 保存并读取的长期记忆
```

`Context` 与 `State` 最重要的区别：节点通常返回部分 State 来更新它；`Context` 是本次调用提供的运行上下文，节点读取它来确定“当前是谁”，通常不由节点修改。

### 四处固定连接

```python
builder = StateGraph(State, context_schema=Context)
graph = builder.compile(store=store)

result = graph.invoke(
    {"question": "解释 Store", "response": ""},
    context=Context(user_id="user-1"),
)
```

执行过程：

```text
context_schema=Context
    -> 声明本图的运行上下文结构

compile(store=store)
    -> 把 Store 接入 Graph Runtime

context=Context(user_id="user-1")
    -> 为本次运行提供当前用户身份

runtime: Runtime[Context]
    -> LangGraph 执行节点时自动注入 Runtime 对象
```

`Runtime[Context]` 是类型标注：

```text
Runtime           LangGraph 固定类
[Context]         表示 runtime.context 应符合自定义 Context 类型
runtime           自定义参数名
```

它不是在这里手动创建 Runtime。调用 `graph.invoke()` 后，Graph Runtime 会构造并传入该参数。

节点中的核心代码：

```python
user_id = runtime.context.user_id
namespace = ("users", user_id, "preferences")

assert runtime.store is not None
item = runtime.store.get(namespace, "response-style")
```

逐步含义：

1. 从本次 Context 读取 `user_id`。
2. 使用用户 ID 组成该用户的 namespace。
3. 确认 Graph 已接入 Store。
4. 从 Store 读取该用户的 `response-style`。

`assert runtime.store is not None` 不负责创建 Store。它表示：“运行到这里时 Store 必须存在，否则立即报错。”同时它也帮助类型检查器确认后面可以安全调用 `.get()`。

练习文件：`D:/Code_all/agent_test/langchain_demo/Day3/day_3.7.py`

预期输出：

```text
用户=user-1；语言=中文；风格=简洁；问题=解释一下 Store
用户=user-2；语言=英文；风格=详细；问题=Explain Store
用户=user-3；语言=中文；风格=正常；问题=解释一下 Store
```

第三个用户没有保存偏好，所以 `get()` 返回 `None`，代码使用默认值。这是生产代码必须考虑的分支。

### 固定 API 与自定义内容

```text
StateGraph、context_schema、compile、store、Runtime、context、invoke
    LangGraph 固定 API/参数名

Context、user_id、personalize_response、response-style、namespace 的层级
    项目自定义类型、变量、函数和数据设计
```

官方依据：

- https://docs.langchain.com/oss/python/langgraph/add-memory#access-the-store-inside-nodes
- https://reference.langchain.com/python/langgraph/runtime/Runtime
- https://reference.langchain.com/python/langgraph/graph/state/StateGraph

本检查点验收：

1. 能解释 `compile(store=store)` 与 `runtime.store` 的关系。
2. 能解释 `context=Context(user_id=...)` 如何到达 `runtime.context.user_id`。
3. 能区分 State、Context、Config 和 Store。
4. user-1、user-2 读取不同偏好，user-3 使用默认值。

## Day 3 核心对照：Checkpoint、Interrupt 与 Store

这三个概念不在同一层：

```text
Checkpoint   工作流状态快照（数据）
Checkpointer 保存和读取快照的组件
interrupt    让工作流暂停的控制操作
Store        保存跨 thread 长期记忆的存储组件
```

完整审批流程：

```text
Graph 正在运行
    -> 节点调用 interrupt(payload)
    -> Graph 暂停
    -> Checkpointer 按 thread_id 保存 Checkpoint
    -> 调用方收到 __interrupt__ 审批信息
    -> 人提交 Command(resume=...)
    -> Checkpointer 按相同 thread_id 找回 Checkpoint
    -> Graph 从暂停节点恢复执行
```

Store 不负责暂停和恢复。它可以在不同 thread 中按 `namespace + key` 读取同一用户的长期偏好：

```text
thread-a ─┐
thread-b ─┼─> Store: ("users", "user-1", "preferences")
thread-c ─┘
```

典型数据归属：

```text
messages、当前节点、待恢复位置     Checkpoint
是否暂停、等待什么人工决定         interrupt
语言偏好、回复风格、用户长期画像   Store
灵感、待办、账号、权限等权威数据   MySQL 业务数据库
```

`interrupt` 通常需要 Checkpointer 才能跨调用可靠恢复；Store 与 interrupt 没有这种绑定关系。`InMemorySaver` 和 `InMemoryStore` 都会在进程退出后丢失数据，只适合学习和测试。

### Context 不是存储系统

```python
context=Context(user_id="user-1")
```

这里只是为当前一次运行携带“当前用户是谁”。Context 没有 `put/get/search`，也不会自动写入 Checkpoint 或 Store；下一次调用通常仍要由认证层重新提供 `user_id`。

```text
Context.user_id   当前运行的用户身份/查询条件
Store namespace   使用这个身份定位长期记忆
Store value       真正长期保存的偏好数据
```

数据链：

```text
后端认证得到 user-1
  -> invoke(context=Context(user_id="user-1"))
  -> runtime.context.user_id
  -> 组成 namespace
  -> runtime.store.get(namespace, key)
  -> 读取 Store 中的长期记忆
```

Context 可类比为本次请求携带的身份证；`user_id` 是身份证号；Store 是档案柜；namespace/key 是查档案的地址。携带身份证不会自动创建档案，必须显式调用 `store.put()` 才会写入。

## 检查点 9：综合连接 Context、Store、Checkpointer 与 Interrupt

练习文件：`D:/Code_all/agent_test/langchain_demo/Day3/day_3.8.py`

本实验把 Day 3 的四条数据通道放入同一个流程：

```text
Context.user_id
    -> 确定当前用户

Store
    -> 按 user_id 读取该用户长期偏好

interrupt
    -> 在真正写入前暂停并展示审批信息

Checkpointer + thread_id
    -> 保存暂停位置，并在收到 Command(resume=...) 后恢复
```

编译时同时接入两个组件：

```python
graph = builder.compile(
    checkpointer=InMemorySaver(),
    store=store,
)
```

它们彼此独立：

```text
checkpointer  保存 Graph State 和暂停位置
store         保存跨 thread 的用户长期记忆
```

第一次运行：

```python
paused_result = graph.invoke(
    input_state,
    config=config,
    context=Context(user_id="user-1"),
)
```

第二次恢复：

```python
final_result = graph.invoke(
    Command(resume={"action": "approve"}),
    config=config,
    context=Context(user_id="user-1"),
)
```

恢复时的两个身份信息：

```text
相同 thread_id   让 Checkpointer 找到暂停的工作流
相同 user_id     让重新执行的节点仍代表正确用户
```

`Context` 是 run-scoped 数据，不应把它误认为 Checkpoint 中自动持久化的 State。生产服务中，每次请求都应从已认证的用户身份重新构造 Context，不能直接相信请求正文传来的 user_id。

预期关键结果：

```text
暂停时数据库： []
等待执行节点： ('review_and_save',)
最终状态：... status='saved' ...
恢复后数据库： [{'user_id': 'user-1', 'title': '学习 LangGraph 综合记忆'}]
```

`idea_database` 当前只是 Python 列表，用于模拟 MySQL。真正的业务灵感应该写入 MySQL；Store 在这个实验中只保存和读取用户偏好。

本检查点验收：

1. 暂停前业务数据库没有变化。
2. 审批信息包含从 Store 读取的用户偏好。
3. 使用相同 thread_id 和 Context 恢复后才写入。
4. 能说出四个组件各自负责哪一段流程。

## 检查点 10：Day 3 验收测试

练习文件：`D:/Code_all/agent_test/langchain_demo/Day3/day_3.9.py`

测试覆盖三条核心规则：

```text
批准路径      暂停时不写入，approve 后恰好写入一次
拒绝路径      reject 后业务数据数量不变
用户隔离      user-1 与 user-2 读取不同 Store 偏好
```

`assert` 是 Python 固定语法，不是 LangGraph API：

```python
assert 实际结果 == 预期结果
```

条件为真时继续执行；条件为假时抛出 `AssertionError`，测试失败。测试中的每个暂停流程都使用不同 `thread_id`，避免某个已完成或仍暂停的 Checkpoint 干扰另一个测试。

运行：

```powershell
python D:\Code_all\agent_test\langchain_demo\Day3\day_3.9.py
```

预期输出：

```text
Day 3 全部验收测试通过
最终业务数据： [{'user_id': 'user-1', 'title': '批准测试'}]
```

只有“批准测试”进入业务数据，说明拒绝路径没有产生副作用；用户隔离用例只读取审批信息，没有执行恢复写入。

完成该脚本并能解释三个断言组后，Day 3 达到 100%。

## 编码任务

1. 同一 `thread_id` 连续对话，验证模型记住前文。
2. 不同 `thread_id` 使用相同问题，验证状态隔离。
3. 在保存节点前调用 `interrupt()` 返回待确认内容。
4. 用户确认后使用 `Command(resume=True)` 恢复。
5. 用户拒绝时不执行写操作，并清理待执行状态。

## 验收标准

- 同一会话可以继续，不同会话互不污染。
- 暂停后进程可获取待确认参数。
- 拒绝确认时数据库没有变化。
- 能解释 Checkpointer 和 MySQL 业务表的不同职责。

## 官方资料

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/oss/python/langchain/short-term-memory

## Day 3 复习地图

一句话总览：

```text
Context 告诉 Graph 当前是谁；Store 保存这个人的长期记忆；
State 保存当前工作流数据；Checkpointer 按 thread_id 保存 State 快照；
interrupt 暂停流程；Command(resume=...) 提交人的决定并恢复流程。
```

核心关系：

```text
本次调用
├── input State：本轮业务输入
├── context：user_id 等运行身份
└── config：thread_id 等运行配置
          ↓
Graph Runtime
├── runtime.context：读取当前用户
├── runtime.store：读取跨 thread 记忆
└── Checkpointer：恢复/保存当前 thread 的 State
          ↓
节点调用 interrupt(payload)
          ↓
返回 __interrupt__，等待人工决定
          ↓
Command(resume=decision) + 相同 thread_id
          ↓
从中断节点开头重新执行；interrupt 返回 decision
          ↓
执行 interrupt 后面的真实写入
```

必须牢记的边界：

```text
Context       当前运行身份，不是长期存储
State         节点之间流转的数据
Checkpoint    某个 thread 的 State 快照
Checkpointer  保存/读取 Checkpoint 的组件
interrupt     暂停控制操作，不负责保存业务数据
Store         跨 thread 的长期 Agent 记忆
MySQL         灵感、待办、用户等权威业务数据
```

安全原则：

1. 数据库写入、发送消息等副作用放在 `interrupt()` 后面。
2. 恢复必须使用暂停时相同的 `thread_id`。
3. Context 是运行级数据，恢复调用也应重新提供认证后的用户身份。
4. Store namespace 必须包含用户或租户隔离信息。
5. `InMemorySaver` 与 `InMemoryStore` 只用于学习测试，进程退出后数据丢失。

## Day 3 闭卷复习题

先不查看答案，使用自己的话回答：

1. `Checkpoint` 和 `Checkpointer` 分别是什么？为什么名字很像但不是同一个东西？
2. `thread_id` 有什么作用？相同 thread 和不同 thread 在消息记忆上有什么区别？
3. `interrupt()`、`__interrupt__`、`Command(resume=...)` 分别位于暂停恢复流程的哪一步？
4. 为什么恢复后节点会从开头重新运行，而不是直接从 `interrupt()` 下一行恢复？这会带来什么风险？
5. 为什么数据库写入必须尽量放在 `interrupt()` 后面？如果必须放在前面，要怎样降低重复写入风险？
6. `Context(user_id="user-1")` 与 Store 中的 namespace 有什么区别？它们怎样配合？
7. 请解释 `runtime: Runtime[Context]` 中参数名、冒号、`Runtime` 和 `[Context]` 各自代表什么。
8. Checkpointer、Store 和 MySQL 分别适合保存：聊天消息、回复风格、真实灵感中的哪一种？为什么？
9. `compile(checkpointer=..., store=...)` 中两个组件分别通过什么方式被节点或 Runtime 使用？
10. 审批暂停时 `idea_database` 为什么仍然为空？什么代码执行后它才会发生变化？
11. 找出下面代码的风险，并说出修改方法：

```python
def save_node(state: State):
    idea_database.append(state["title"])
    approved = interrupt("是否确认保存？")
    if not approved:
        return {"status": "rejected"}
    return {"status": "saved"}
```

12. 一个用户在 `thread-a` 设置了“简洁中文”偏好，随后新建 `thread-b`。哪些数据应该隔离，哪些数据应该共享？请说明原因。

回答格式建议：

```text
1. ...
2. ...
...
12. ...
```

复习判定：10 题以上逻辑正确为掌握；7-9 题进行针对性补强；6 题及以下重新跑一次 Day 3 综合实验。

## 学习记录

```text
完成日期：2026-07-21
实际投入时间：
验收结果：Day 3 代码与三组断言测试通过，课程进度 100%
最重要的理解：Checkpoint/Store/Context 的边界，以及 interrupt 恢复时的副作用安全
复习结果：两轮主动回忆与针对性确认通过
仍然存在的问题：后续复习继续抽查 Config、Context、Checkpoint、Store 的数据边界
对应 Git 提交：
```
