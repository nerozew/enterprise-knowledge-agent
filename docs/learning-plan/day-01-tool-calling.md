 Day 01：Tool Calling 与 create_agent

## 今日目标

理解 Chain、结构化输出、Tool Calling 和 Agent 的边界，完成一个能自主选择“保存灵感”或“搜索灵感”工具的 Agent。

## 今日时间表

1. 09:00-10:00：检查版本，学习 Tool Schema。
2. 10:00-11:30：编写两个 `@tool`，直接调用并检查参数。
3. 11:30-12:00：观察 `AIMessage.tool_calls`。
4. 14:00-16:00：使用 `create_agent()` 形成完整工具循环。
5. 16:00-17:00：测试调用、误调用和工具异常。
6. 17:00-18:00：整理消息链和面试答案。

## 必学知识

### 固定 API：`create_agent`

官方导入路径：

```python
from langchain.agents import create_agent
```

在当前 `langchain 1.3.11` 环境中：

```text
实际模块：langchain.agents.factory
固定函数名：create_agent
返回类型：CompiledStateGraph
职责：创建一个循环调用工具直到满足停止条件的 Agent Graph
官方文档：https://docs.langchain.com/oss/python/langchain/agents
```

最小调用：

```python
agent = create_agent(
    model=llm,
    tools=[save_idea],
    system_prompt="你是灵感整理助手。",
)
```

名称分类：

```text
create_agent   LangChain 固定 API
model          create_agent 固定参数名
tools          create_agent 固定参数名
system_prompt  create_agent 固定参数名
agent          自定义变量名，可以改成 idea_agent
llm            自定义变量名
save_idea      开发者定义的 Tool 名称
```

### 【非常重点】四个概念的区别

- Prompt：告诉模型角色、任务和约束。
- Structured Output：约束模型最终返回的数据结构。
- Tool Calling：模型生成“调用哪个函数及其参数”的结构化请求。
- Agent：循环执行“模型决策 -> 工具执行 -> 返回工具结果 -> 模型继续决策”。

工具不是模型内部执行的。模型只生成 Tool Call，真正执行 Python 函数的是 Agent Runtime。#

### 【非常重点】Tool Schema

`@tool` 会根据函数名、类型注解和 docstring 生成工具 Schema。模型根据工具名称和描述判断何时使用，因此描述必须说明：

- 工具做什么。
- 什么时候应该调用。
- 每个参数的含义。
- 有哪些限制或副作用。

### 【非常重点】消息链

一次工具调用通常包含：

```text
HumanMessage
-> AIMessage(tool_calls=[...])
-> ToolMessage(工具执行结果)
-> AIMessage(最终回答或下一次工具调用)
```

只打印最后一条消息会隐藏 Agent 的关键过程。学习阶段必须遍历 `result["messages"]`。

### 【重点】安全边界

模型生成的参数始终属于外部输入。工具内部仍需做身份认证、资源归属校验、参数校验和高风险操作确认，不能因为参数来自模型就直接信任。

### 简单 Schema 与高级 Schema

简单工具通常直接使用函数类型注解和 docstring：

```python
@tool
def search_ideas(keyword: str, limit: int = 10) -> list[dict]:
    """搜索用户保存的灵感。

    Args:
        keyword: 要搜索的关键词。
        limit: 最多返回多少条结果。
    """
    return []
```

需要字段级描述、范围校验、枚举和复杂嵌套结构时，使用 Pydantic `args_schema`：

```python
from pydantic import BaseModel, Field


class SearchIdeasInput(BaseModel):
    keyword: str = Field(min_length=1, description="要搜索的关键词")
    limit: int = Field(default=10, ge=1, le=50, description="最多返回条数")


@tool(args_schema=SearchIdeasInput)
def search_ideas(keyword: str, limit: int = 10) -> list[dict]:
    """搜索用户保存的灵感。"""
    return []
```

`【非常重点】`：Pydantic 参数类不是所有工具都必须定义。简单参数优先函数签名；复杂验证和清晰字段描述再使用 `args_schema`。

## 最小实验

先检查版本：

```powershell
python -c "from importlib.metadata import version; print('langchain', version('langchain')); print('langchain-core', version('langchain-core')); print('langgraph', version('langgraph'))"
```

当前学习环境已确认：

```text
检查日期：2026-07-18
langchain = 1.3.11
langchain-core = 1.4.9
langgraph = 1.2.8
检查结果：通过，可以使用 LangChain v1 的 create_agent API。
```

### 检查点 1：先理解 Tool，不连接 LLM

```python
from langchain.tools import tool


@tool
def save_idea(
    title: str,
    tags: list[str],
    next_actions: list[str],
) -> str:
    """保存一条已经整理好的用户灵感。"""
    return f"保存成功：{title}，标签数量：{len(tags)}"


print("对象类型：", type(save_idea))
print("工具名称：", save_idea.name)
print("工具描述：", save_idea.description)
print("参数 Schema：", save_idea.args_schema.model_json_schema())

result = save_idea.invoke({
    "title": "聊天待办助手",
    "tags": ["AI助手", "任务管理"],
    "next_actions": ["明确输入格式", "实现待办提取"],
})
print("执行结果：", result)
```

观察重点：`@tool` 会把普通 Python 函数包装成 Tool 对象；类型注解和 docstring 会进入工具 Schema；不经过模型也可以使用 `.invoke()` 单独测试工具。

检查点 1 实际结果：

```text
完成日期：2026-07-18
对象类型：langchain_core.tools.structured.StructuredTool
工具名称：save_idea
工具描述：保存一条已经整理好的用户灵感。
必填参数：title、tags、next_actions
执行结果：保存成功：聊天待办助手，标签数量：2
状态：通过
```

关键结论：

- `StructuredTool` 继承 `BaseTool`，同时属于 `Runnable` 体系，所以支持 `.invoke()`。
- Schema 的最外层是一个对象，`properties` 定义参数，`required` 定义必填参数。
- 当前示例没有数据库和列表写入操作，“保存成功”只是函数返回的演示字符串，不代表真的持久化。

### 检查点 2：区分“函数返回成功文字”和“数据真的发生变化”

```python
from langchain.tools import tool

idea_store: list[dict] = []


@tool
def save_idea(title: str, tags: list[str]) -> str:
    """把一条灵感保存到当前进程的内存列表。"""
    idea_store.append({"title": title, "tags": tags})
    return f"保存成功：{title}"


print("调用前：", idea_store)

result = save_idea.invoke({
    "title": "聊天待办助手",
    "tags": ["AI助手", "任务管理"],
})

print("函数返回值：", result)
print("调用后：", idea_store)
```

这里真正改变数据的是 `idea_store.append(...)`，不是 `return`，也不是 `print`。这种保存只存在于当前 Python 进程；重新运行程序后列表会重新变为空。持久化保存需要后续接入 MySQL。

### 检查点 3：让模型生成 Tool Call，但暂不执行工具

```python
from langchain.messages import HumanMessage, SystemMessage
from langchain.tools import tool

from demo1 import llm

idea_store: list[dict] = []


@tool
def save_idea(
    title: str,
    tags: list[str],
    next_actions: list[str],
) -> str:
    """把用户明确要求保存的灵感写入当前进程的内存列表。"""
    print(">>> save_idea 工具真正执行了")
    idea_store.append({
        "title": title,
        "tags": tags,
        "next_actions": next_actions,
    })
    return f"保存成功：{title}"


llm_with_tools = llm.bind_tools([save_idea])

response = llm_with_tools.invoke([
    SystemMessage(
        content=(
            "你是灵感整理助手。用户明确要求保存时才调用保存工具。"
            "工具返回成功后，向用户简短确认，不要再次调用工具。"
        )
    ),
    HumanMessage(
        content="请保存一个灵感：开发一个根据聊天记录生成待办事项的助手"
    ),
])

print("响应类型：", type(response))
print("文本内容：", response.content)
print("工具调用：", response.tool_calls)
```

观察重点：

- `bind_tools()` 把 Tool Schema 提供给模型，并没有执行 Python 函数。
- 模型返回 `AIMessage`，工具请求位于 `response.tool_calls`。
- 如果控制台没有出现 `>>> save_idea 工具真正执行了`，说明工具确实尚未执行。
- 模型决定不使用工具时，`response.tool_calls` 通常是空列表。

典型的 Tool Call 结构：

```python
{
    "name": "save_idea",
    "args": {
        "title": "聊天待办助手",
        "tags": ["AI助手", "任务管理"],
        "next_actions": ["明确输入格式", "实现待办提取"],
    },
    "id": "call_xxx",
    "type": "tool_call",
}
```

`【非常重点】`：Tool Call 是模型提出的结构化执行请求，不是工具执行结果。

检查点 3 实际结果：

```text
完成日期：2026-07-18
响应类型：langchain_core.messages.ai.AIMessage
文本内容：空字符串
工具名称：save_idea
Tool Call ID：call_682e08c63d4641e99014f4
工具是否执行：否
模型是否调用：是
状态：通过
```

本次易错点：

- `llm_with_tools.invoke(...)` 已经向 LLM 发起请求，所以模型已经调用。
- `response` 是模型返回的 `AIMessage`，其中可以同时承载文本内容和 Tool Calls。
- 文本为空不是“没有使用模型”，而是模型本轮选择先请求工具，没有生成最终文本回答。
- `response.tool_calls` 是模型根据 Schema 生成的具体调用请求，不是 Schema 本身。
- 工具函数尚未执行，因为当前代码还没有把 `args` 传给 `save_idea.invoke(...)`。

### 检查点 4：手动完成一次模型与工具循环

`bind_tools()` 只提供工具说明。下面的代码手动完成 Agent Runtime 负责的执行部分：

```python
from langchain.messages import HumanMessage, SystemMessage, ToolMessage
from langchain.tools import tool

from demo1 import llm

idea_store: list[dict] = []


@tool
def save_idea(
    title: str,
    tags: list[str],
    next_actions: list[str],
) -> str:
    """把用户明确要求保存的灵感写入当前进程的内存列表。"""
    print(">>> save_idea 工具真正执行了")
    idea_store.append({
        "title": title,
        "tags": tags,
        "next_actions": next_actions,
    })
    return f"保存成功：{title}"


tools = [save_idea]
tool_map = {current_tool.name: current_tool for current_tool in tools}
llm_with_tools = llm.bind_tools(tools)

messages = [
    SystemMessage(
        content=(
            "你是灵感整理助手。用户明确要求保存时才调用保存工具。"
            "工具返回成功后，向用户简短确认，不要再次调用工具。"
        )
    ),
    HumanMessage(
        content="请保存一个灵感：开发一个根据聊天记录生成待办事项的助手"
    ),
]

# 第一次调用模型：模型只生成 Tool Call。
ai_message = llm_with_tools.invoke(messages)
messages.append(ai_message)
print("第一次模型输出：", ai_message.tool_calls)
print("工具执行前的数据：", idea_store)

# Runtime 读取 Tool Call，找到对应工具并执行。
for tool_call in ai_message.tool_calls:
    selected_tool = tool_map[tool_call["name"]]
    tool_result = selected_tool.invoke(tool_call["args"])

    tool_message = ToolMessage(
        content=str(tool_result),
        tool_call_id=tool_call["id"],
    )
    messages.append(tool_message)
    print("工具返回值：", tool_result)
    print("ToolMessage：", tool_message)

print("工具执行后的数据：", idea_store)

# 第二次调用模型：模型读到 ToolMessage 后生成最终回答。
final_message = llm_with_tools.invoke(messages)
messages.append(final_message)

print("工具结果消息：", messages[-2])
print("最终回答：", final_message.content)
```

完整顺序：

```text
HumanMessage
-> 第一次 LLM 调用
-> AIMessage(tool_calls)
-> Python Runtime 执行 Tool
-> ToolMessage(tool_call_id)
-> 第二次 LLM 调用
-> AIMessage(final content)
```

`【非常重点】`：`tool_call_id` 将 ToolMessage 与对应的 Tool Call 配对；不能跳过带 Tool Call 的 AIMessage，也不能把无关联的工具结果直接发给模型。

#### 四个相似名称的区别

```text
@tool       装饰器：把普通函数包装成 StructuredTool
tools       列表：程序允许模型选择的真实 Tool 对象集合
tool_map    字典：按照名称快速找到真实 Tool 对象
tool_call   字典：模型生成的一次具体工具调用请求
```

`tool_map` 的字典推导式等价于：

```python
tool_map = {}

for current_tool in tools:
    tool_map[current_tool.name] = current_tool
```

如果只有 `save_idea`，结果近似为：

```python
{
    "save_idea": save_idea,
}
```

`current_tool.name` 是 StructuredTool 对象的属性。它就是检查点 1 中打印过的 `save_idea.name`，默认来自被装饰函数的函数名。

#### AIMessage 与 messages

- `ai_message` 是模型一次响应产生的单个 AIMessage 对象。
- `messages` 是按时间排序的整个对话消息列表。
- AIMessage 常见字段包括 `content`、`tool_calls`、`invalid_tool_calls`、`response_metadata`、`usage_metadata` 和消息自身的 `id`。
- `ai_message.id` 标识整条 AIMessage；`tool_call["id"]` 标识其中一次 Tool Call，两者不是同一个 ID。
- 一条 AIMessage 可以同时包含零个、一个或多个 Tool Calls。

使用 `create_agent(system_prompt="...")` 时，静态 System Prompt 属于 Agent Harness 的配置，会在每次模型调用时注入上下文；默认返回的 `result["messages"]` 主要是 Agent State 中累积的对话和工具消息，因此打印结果中通常看不到这条静态 System Prompt。如果将 `SystemMessage` 直接作为输入消息放入 `messages`，它才属于输入消息序列本身。

#### 手动工具循环的严格执行顺序

```text
1. @tool 把普通函数包装成 StructuredTool 实例。
2. tools 列表保存本地允许使用的真实 Tool 实例。
3. tool_map 字典建立“工具名称 -> 真实 Tool 实例”的映射。
4. bind_tools(tools) 把工具 Schema 绑定到模型请求能力中。
5. 第一次调用 LLM，得到包含具体 Tool Calls 的 AIMessage。
6. 把 AIMessage 追加到 messages，保留模型的调用请求。
7. Runtime 使用 tool_call["name"] 从 tool_map 找到真实 Tool 实例。
8. Runtime 把 tool_call["args"] 作为实际参数传给 Tool 的 invoke 方法。
9. Tool 校验参数、执行函数体，return 值成为 tool_result。
10. 使用 tool_result 和 tool_call["id"] 构造 ToolMessage。
11. 把 ToolMessage 追加到 messages，与原 Tool Call 配对。
12. 第二次调用 LLM，模型根据完整消息历史生成最终 AIMessage。
13. 把最终 AIMessage 追加到 messages，形成完整对话记录。
```

注意：Tool Schema 是参数规则，`tool_call["args"]` 是模型生成的实际参数，`tool_result` 是工具执行后的返回值，三者不能混为一谈。

#### 为什么通常需要第二次 LLM 调用

第一次 LLM 调用发生在工具执行之前，只能决定工具名称和参数，无法知道工具的真实执行结果。工具可能成功、失败、超时、返回空数据，或者返回需要继续分析的数据。

```text
第一次 LLM：决定做什么
Tool Runtime：真正执行并获得事实结果
第二次 LLM：根据事实结果决定如何回答或下一步做什么
```

第二次 LLM 调用不是语法强制要求。以下简单场景可以直接把 `tool_result` 返回给调用方：

```python
tool_result = selected_tool.invoke(tool_call["args"])
print(tool_result)
```

以下场景通常需要再次调用 LLM：

- 工具结果需要转换成自然语言回答。
- 一次生成了多个 Tool Calls，需要汇总多个结果。
- 工具返回复杂 JSON、文档或数据库记录，需要解释。
- 工具失败后需要判断重试、降级或追问。
- 根据第一个工具结果决定是否调用第二个工具。

`【非常重点】`：不能在工具执行前宣称操作成功。最终回答必须基于真实 ToolMessage，而不是基于模型对未来结果的猜测。

#### Tool 名称与 Tool Call ID

```text
tool.name              标识工具定义，例如 save_idea
tool_call["id"]        标识某一次具体调用，例如 call_123
ToolMessage.tool_call_id  指向对应的那一次 Tool Call
```

同一个工具可以在一条 AIMessage 中被调用多次：

```python
[
    {"name": "search_ideas", "args": {"keyword": "AI"}, "id": "call_1"},
    {"name": "search_ideas", "args": {"keyword": "RAG"}, "id": "call_2"},
]
```

两个调用的工具名称相同，但参数和结果不同，因此 ToolMessage 必须使用 `call_1`、`call_2` 分别配对，不能只使用工具名称。

#### 一个 Tool 的三个组成部分

```text
description   告诉模型“什么时候选择这个工具”
schema        规定“调用这个工具要传什么参数”
function body 规定“工具真正执行什么操作”
```

描述和 Schema 都不会自动实现业务功能。当前演示函数只 `return` 成功文字，并没有保存数据。内存保存至少需要：

```python
idea_store: list[dict] = []


@tool
def save_idea(title: str, tags: list[str]) -> str:
    """把一条灵感保存到当前进程的内存列表。"""
    idea_store.append({"title": title, "tags": tags})
    return f"保存成功：{title}"
```

真正持久化到 MySQL 时，函数体必须执行数据库写入，并且只在事务提交成功后返回成功。工具内部仍需完成参数校验、身份认证和资源归属检查。

#### Docstring 如何成为工具描述

```python
@tool
def search_ideas(keyword: str) -> list[dict]:
    """根据关键词搜索当前用户已经保存的灵感。

    仅当用户明确要求查找历史灵感时使用，不用于互联网搜索。
    """
    return []
```

三引号字符串位于函数体第一条语句时，Python 将它保存为函数的 `__doc__`。`@tool` 默认读取这个 docstring，生成 Tool 的 `description`，再由 `bind_tools()` 连同工具名称和参数 Schema 一起提供给 LLM。

一个有效工具描述应尽量说明：

- 工具能做什么。
- 什么时候应该调用。
- 什么时候不应该调用。
- 是否产生写入、删除等副作用。
- 关键限制，但不包含密钥或隐私信息。

三引号本身只是 Python 多行字符串语法；只有处在函数体第一条语句的位置时，才会成为该函数的 docstring。也可以使用 `@tool(description="...")` 显式提供或覆盖描述。

使用当前项目的 LLM 初始化方式完成下面的核心代码：

```python
from langchain.agents import create_agent
from langchain.tools import tool

idea_store: list[dict] = []


@tool
def save_idea(title: str, tags: list[str], next_actions: list[str]) -> str:
    """保存用户明确要求保存的灵感。"""
    idea_store.append(
        {"title": title, "tags": tags, "next_actions": next_actions}
    )
    return f"保存成功：{title}"


@tool
def search_ideas(keyword: str) -> list[dict]:
    """按照关键词搜索已经保存的灵感。"""
    return [item for item in idea_store if keyword in str(item)]


agent = create_agent(
    model=llm,
    tools=[save_idea, search_ideas],
    system_prompt=(
        "你是灵感整理助手。只有用户明确要求保存时才调用保存工具；"
        "用户要求搜索时调用搜索工具；普通咨询不调用工具。"
    ),
)

result = agent.invoke({
    "messages": [{
        "role": "user",
        "content": "保存一个灵感：根据聊天记录自动生成待办事项",
    }]
})

for message in result["messages"]:
    message.pretty_print()
```

`idea_store` 只用于理解工具循环，进程结束后会丢失。今天不要接数据库。

## 必做测试

1. “保存一个灵感……”：应调用 `save_idea`。
2. “搜索包含聊天的灵感”：应调用 `search_ideas`。
3. “给我三个建议，不要保存”：不应调用工具。
4. 缺少标题或行动项：观察模型如何补充或追问。
5. 工具主动抛出异常：记录 Agent 的表现，不要吞掉异常。

## 验收标准

- 能解释为什么 `AIMessage.tool_calls` 不等于工具执行结果。
- 能从消息列表找到 Human、AI、Tool 三类消息。
- 两个工具的参数 Schema 正确。
- 三个主要测试场景全部符合预期。
- 能解释为什么写数据库的工具必须校验当前用户。

## 面试自测

1. Tool Calling 和结构化输出有什么区别？
2. Agent 如何知道应该调用哪个工具？
3. 为什么工具 docstring 会影响调用准确率？
4. 工具调用失败时应该在哪一层重试？
5. 为什么删除和付款工具需要人工确认？

## 官方资料

- https://docs.langchain.com/oss/python/langchain/tools
- https://docs.langchain.com/oss/python/langchain/agents

## 学习记录

```text
学习日期：2026-07-18 至 2026-07-19
完成日期：2026-07-19
当前状态：已完成

已经掌握：
1. Class、Object、Instance、Attribute、Method 和 self 的关系。
2. @tool 将普通函数包装成 StructuredTool 实例。
3. Description、Schema 和 Function Body 的不同职责。
4. tools 列表、tool_map 字典、tool_call 请求的区别。
5. bind_tools 只向模型提供工具说明，不执行工具。
6. 第一次 LLM 调用返回带 Tool Calls 的 AIMessage。
7. Python 运行时根据名称找到真实工具，并使用 args 执行 invoke。
8. Tool Call ID 用于将具体调用与 ToolMessage 配对。
9. ToolMessage 是工具真实结果，第二次 LLM 返回最终 AIMessage。
10. 测试工具只打印和 return，不等于真正写入数据库。

已完成实验：
- LangChain/LangGraph 版本检查。
- StructuredTool 类型、名称、描述和 Schema 检查。
- bind_tools 后生成 AIMessage.tool_calls。
- create_agent 自动执行 save_idea，并得到 HumanMessage -> AIMessage -> ToolMessage -> AIMessage 完整消息序列。
- search_ideas 根据关键词“聊天”从 idea_store 返回 1 条匹配记录，Agent 将列表结果整理为自然语言回答。
- “分析想法，但不要保存，也不要搜索”场景只产生 HumanMessage 和 AIMessage，没有 Tool Call 或 ToolMessage。

三个行为场景：
- 明确保存：通过，调用 save_idea。
- 明确搜索：通过，调用 search_ideas。
- 禁止保存和搜索：通过，不调用工具。

Day 01 完成度：100%
对应 Git 提交：尚未提交
```
