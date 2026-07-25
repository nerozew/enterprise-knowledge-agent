# Python 基础补充讲义

## 1. 类、对象、实例和方法

### 【非常重点】一句话定义

```text
类 Class：定义一种类型具有什么数据和行为。
对象 Object：Python 运行期间存在的一个具体值。
实例 Instance：强调某个对象是由哪个类创建的。
属性 Attribute：对象保存或提供的数据。
方法 Method：通过类或对象访问的可调用行为。
```

“对象”是宽泛称呼，“实例”强调对象与类的关系。所有实例都是对象；当说“`xiaoming` 是 `Student` 的实例”时，重点是它属于 `Student` 类。

## 2. 最小示例

```python
class Student:
    def __init__(self, name: str):
        self.name = name

    def study(self, topic: str) -> str:
        return f"{self.name} 正在学习 {topic}"


xiaoming = Student("小明")

print(type(xiaoming))
print(xiaoming.name)
print(xiaoming.study("Tool Calling"))
```

逐项对应：

```text
Student                 类
xiaoming                对象，也是 Student 的实例
xiaoming.name           属性
xiaoming.study          绑定方法对象
xiaoming.study(...)     调用方法
```

## 3. `self` 是什么

实例方法定义：

```python
def study(self, topic: str):
```

调用时：

```python
xiaoming.study("Tool Calling")
```

概念上近似：

```python
Student.study(xiaoming, "Tool Calling")
```

Python 自动把点号左边的 `xiaoming` 作为第一个参数 `self` 传入，因此平时调用时不需要手动填写 `self`。

## 4. 函数和方法的区别

```python
def greet(name: str) -> str:
    return f"你好，{name}"
```

`greet` 是独立函数：

```python
greet("小明")
```

定义在类中并通过实例访问：

```python
xiaoming.study("Agent")
```

这里的 `study` 是绑定到 `xiaoming` 的实例方法。

## 5. 对照当前 LangChain 代码

```python
@tool
def save_idea(...):
    ...
```

装饰完成后：

```text
StructuredTool             类
save_idea                  StructuredTool 对象/实例
save_idea.name             属性
save_idea.description      属性
save_idea.invoke           方法
save_idea.invoke({...})    调用方法
```

其他例子：

```text
llm                         模型对象/实例
llm.bind_tools              方法
response                    AIMessage 对象/实例
response.tool_calls         属性
messages                    list 对象/实例
messages.append             方法
```

## 6. 属性与方法的直观判断

```python
save_idea.name
```

读取数据，通常称为属性。

```python
save_idea.invoke({...})
```

带括号执行行为，通常称为调用方法。

这个判断适合入门，但要知道方法本身也可以先被读取：

```python
method_object = save_idea.invoke
result = method_object({...})
```

## 7. 自测

观察以下代码：

```python
class Calculator:
    def __init__(self, name: str):
        self.name = name

    def add(self, a: int, b: int) -> int:
        return a + b


calculator = Calculator("整数计算器")
answer = calculator.add(1, 2)
```

回答：

1. 哪个是类？
2. 哪个是对象和实例？
3. 哪个是属性？
4. 哪个是方法？
5. 调用 `calculator.add(1, 2)` 时，`self` 是什么？

## 8. 自测答案与执行过程

```python
calculator = Calculator("整数计算器")
```

执行时概念上发生：

```text
创建一个 Calculator 对象
-> self 指向这个新对象
-> 参数 name 接收字符串 "整数计算器"
-> 执行 self.name = name
-> 变量 calculator 指向这个对象
```

因此：

```text
Calculator       类
calculator       Calculator 的对象/实例
calculator.name  属性，值是 "整数计算器"
Calculator.__init__  初始化方法
calculator.add   绑定到 calculator 的方法
```

调用：

```python
answer = calculator.add(1, 2)
```

概念上近似：

```python
answer = Calculator.add(calculator, 1, 2)
```

此时参数对应关系：

```text
self -> calculator
a    -> 1
b    -> 2
```

方法返回整数 `3`，所以 `answer` 指向一个 `int` 对象。`answer` 是对象，但不是 Calculator 的实例，而是 `int` 的实例。

## 9. Python Runtime 与变量名

### Python Runtime 不是一条语法

在当前课程中，`Python Runtime` 泛指正在执行代码的 Python 解释器和程序环境。它负责按顺序运行语句、调用函数、创建对象和捕获异常。

当前手动工具循环中没有一个必须命名为 `runtime` 的变量。以下几行代码共同承担了“读取模型请求并执行工具”的运行时工作：

```python
selected_tool = tool_map[tool_call["name"]]
tool_result = selected_tool.invoke(tool_call["args"])
```

### 自定义名称与固定 API

```text
selected_tool  自定义变量名，可以改成 chosen_tool
tool_result    自定义变量名，可以改成 result
idea_store     自定义变量名，可以改成 saved_ideas
tool_map       自定义变量名，可以改成 tools_by_name
invoke         LangChain Runnable 提供的方法名
append         Python list 提供的方法名
```

### 为什么函数体会执行

```python
tool_result = selected_tool.invoke(tool_call["args"])
```

概念上会执行：

```text
StructuredTool.invoke
-> 根据 Schema 校验 args
-> 调用被 @tool 包装的原函数
-> 按顺序执行原函数体
-> 将原函数的 return 值返回为 tool_result
```

如果原函数体包含：

```python
idea_store.append(title)
```

Python 就会调用列表对象 `idea_store` 的 `append` 方法，把 `title` 追加到列表末尾。没有这行代码就不会发生内存保存。

## 10. 列表推导式与消息遍历

### 列表推导式

```python
return [item for item in idea_store if keyword in str(item)]
```

等价于：

```python
results = []

for item in idea_store:
    if keyword in str(item):
        results.append(item)

return results
```

含义是遍历 `idea_store`，将包含关键词的元素放入新列表并返回。`str(item)` 只是教学示例中的简化搜索，生产代码应明确搜索标题、标签等字段。

### 遍历消息

```python
for message in result["messages"]:
    message.pretty_print()
```

含义是从 Agent 返回字典中取出消息列表，逐条取得 Message 对象，再调用 LangChain Message 提供的 `pretty_print()` 方法显示易读内容。变量名 `message` 可以修改，`pretty_print()` 是框架提供的方法名。

## 11. for 循环中的元组解包

```python
test_cases = [
    ("保存这个灵感", "save"),
    ("搜索历史灵感", "search"),
]

for user_text, expected_intent in test_cases:
    print(user_text, expected_intent)
```

`test_cases` 是列表，每个列表元素都是包含两个值的元组。循环每次先取得一个完整元组，再把两个位置分别赋给两个变量：

```python
user_text, expected_intent = ("保存这个灵感", "save")
```

不使用解包时的等价写法：

```python
for case in test_cases:
    user_text = case[0]
    expected_intent = case[1]
    print(user_text, expected_intent)
```

固定语法是 `for 变量1, 变量2 in 可迭代对象`；变量名称由开发者自定义。每个元素包含的值数量必须和左侧变量数量一致，否则会产生 `ValueError`。

## 12. assert 与 isinstance

```python
assert 条件, "条件失败时的说明"
```

`assert` 是 Python 固定语法。条件结果为 `True` 时继续执行；为 `False` 时立即抛出 `AssertionError`，后续的“全部测试通过”不会执行。

```python
assert isinstance(tool_message, ToolMessage)
```

`isinstance(对象, 类)` 是 Python 内置函数，检查对象是否是该类或其子类的实例。这里验证 ToolNode 返回的是 ToolMessage，而不只是内容相同的字符串或字典。

```python
assert tool_message.tool_call_id == "call_test_1"
```

验证工具结果仍关联原始 AIMessage 中的工具调用 ID。ID 错误会导致模型无法知道 ToolMessage 回答的是哪次调用。

```python
assert tool_message.content == "保存成功：测试灵感"
```

验证工具返回文本被正确包装进 ToolMessage。

```python
assert idea_store == ["测试灵感"]
```

验证真实副作用：工具确实把正确内容保存了一次。空列表表示没有保存；两个相同元素表示意外执行了两次；错误文字表示参数传递错误。

测试应同时验证返回值与副作用，因为函数可能返回“成功”文本但没有真正保存，也可能已经保存却返回错误结果。

## 13. try、except、as 与异常处理

### 异常是什么

异常是 Python 运行期间产生的对象，用来表示“当前代码无法按正常路径继续”。常见异常类包括：

```text
ValueError       值不符合要求
TypeError        对象类型不符合要求
KeyError         字典中不存在指定键
ZeroDivisionError 发生除以零
```

异常类与普通类一样可以创建实例：

```python
error = ValueError("年龄不能是负数")
```

这里：

```text
ValueError                    异常类
error                         ValueError 实例/异常对象
"年龄不能是负数"             异常携带的信息
```

### raise：主动抛出异常

```python
def set_age(age: int) -> int:
    if age < 0:
        raise ValueError("年龄不能是负数")

    return age
```

`raise` 是 Python 固定关键字。执行到 `raise` 后，当前函数的正常执行立即停止，后续代码不会执行，异常沿调用关系向外传播，直到遇到能够处理它的 `except`；如果一直没人处理，程序打印 traceback 并结束当前任务。

### try 和 except：尝试执行并捕获异常

```python
try:
    age = set_age(-1)
    print("设置成功", age)
except ValueError as error:
    print("设置失败：", error)
```

执行顺序：

```text
进入 try
-> 调用 set_age(-1)
-> raise ValueError(...)
-> try 中后面的 print 不再执行
-> 寻找匹配 ValueError 的 except
-> 将异常对象赋值给变量 error
-> 执行 except 代码块
```

输出：

```text
设置失败： 年龄不能是负数
```

固定语法结构：

```python
try:
    可能出现异常的代码
except 异常类 as 自定义变量名:
    处理异常的代码
```

- `try`：Python 固定关键字，表示“尝试执行这个代码块”。
- `except`：Python 固定关键字，表示“如果出现匹配异常，就执行这里”。
- `ValueError`：要捕获的异常类。
- `as`：Python 固定关键字，把异常对象绑定给后面的变量。
- `error`：自定义变量名，可以改成 `exc` 或 `e`。

`as` 不是专门给异常使用的单词，它的通用作用是“给对象绑定一个当前代码中使用的名称”。在 `except ValueError as error` 中，它绑定的是刚刚抛出的 ValueError 实例。

### 多种异常分别处理

```python
try:
    value = data["count"]
    result = 10 / value
except KeyError as error:
    print("缺少字典字段：", error)
except ZeroDivisionError as error:
    print("不能除以零：", error)
```

Python 会从上到下寻找第一个类型匹配的 `except`。更具体的异常应写在前面，更宽泛的异常写在后面：

```python
try:
    run_task()
except ValueError as error:
    print("参数错误：", error)
except Exception as error:
    print("其他普通异常：", error)
```

不要轻易使用不指定类型的 `except:`，也不要捕获异常后什么都不做，否则真实错误会被隐藏。

### else 和 finally

完整结构还可以包含 `else` 和 `finally`：

```python
try:
    result = 10 / 2
except ZeroDivisionError as error:
    print("失败：", error)
else:
    print("没有异常，结果是：", result)
finally:
    print("无论成功失败，最后都执行清理")
```

含义：

```text
try       尝试执行
except    出现匹配异常时执行
else      try 没有异常时执行
finally   无论成功或异常，通常都会执行
```

`finally` 常用于关闭文件、释放数据库连接或清理临时资源。

### try/except 与函数 return 的区别

```python
return "失败"
```

是函数正常返回一个字符串。

```python
raise ValueError("失败")
```

是中断正常流程并向调用方传播异常对象。调用方可以选择捕获，也可以让异常继续向外传播。

### LangGraph interrupt 为什么和异常有关

LangGraph 的 `interrupt(payload)` 第一次执行时，会在内部抛出一个 `GraphInterrupt` 对象。Graph Runtime 在外层捕获这个特殊异常，保存 Checkpoint，并把中断信息返回给调用者：

```python
# 仅用于理解的伪代码，不是需要自己编写的 LangGraph 源码。
try:
    node_result = review_and_save(state)
except GraphInterrupt as error:
    checkpointer.save(state)
    return {
        **state,
        "__interrupt__": error.args,
    }
```

逐项对应：

```text
GraphInterrupt    LangGraph 内部定义的异常类
error             捕获到的 GraphInterrupt 实例
as error          把异常实例绑定给变量 error
Graph Runtime     真正负责捕获该异常的框架运行时
```

这解释了为什么节点调用 `interrupt()` 后，后面的业务代码暂时不执行，而 `graph.invoke()` 却可以正常返回中断信息。

### 不要自己捕获 interrupt

下面是错误用法：

```python
try:
    approved = interrupt("是否确认？")
except Exception as error:
    print("发生错误：", error)
```

当前版本的 `GraphInterrupt` 是 `Exception` 的子类，因此业务代码中的 `except Exception` 可能提前截获它。这样 Graph Runtime 收不到中断信号，就无法按照正常机制暂停和保存状态。

正确写法：

```python
approved = interrupt("是否确认？")
```

让 `GraphInterrupt` 自然传播到 LangGraph Runtime，由框架处理。需要捕获的普通业务异常，应放在不会包住 `interrupt()` 的独立代码区域中。

## 14. 字典 get、列表索引与对象属性的链式取值

### dict.get

```python
final_title = decision.get("edited_title", "").strip()
```

`decision` 是字典，`dict.get(key, default)` 是 Python 字典的固定方法：存在指定键时返回对应值，不存在时返回默认值。

```python
decision = {
    "action": "edit",
    "edited_title": "  深入学习 HITL  ",
}

raw_title = decision.get("edited_title", "")
# "  深入学习 HITL  "

final_title = raw_title.strip()
# "深入学习 HITL"
```

`.strip()` 是字符串固定方法，删除字符串开头和结尾的空白，不删除字符串中间的空格。

如果字典没有 `edited_title`：

```python
decision = {"action": "edit"}

raw_title = decision.get("edited_title", "")
# ""

final_title = raw_title.strip()
# ""
```

与方括号取值相比：

```python
decision["edited_title"]          # 键不存在会抛出 KeyError
decision.get("edited_title", "")  # 键不存在返回 ""
```

原表达式等价于：

```python
raw_title = decision.get("edited_title", "")
final_title = raw_title.strip()
```

### paused_result 的四层结构

```python
paused_result["__interrupt__"][0].value
```

它不是二维数组，而是依次经过四种对象：

```text
paused_result                  dict
└── ["__interrupt__"]         list[Interrupt]
    └── [0]                    Interrupt 对象
        └── .value             本次 interrupt 的 payload
```

拆开写：

```python
interrupt_list = paused_result["__interrupt__"]
first_interrupt = interrupt_list[0]
approval_payload = first_interrupt.value
```

当前例子中的类型近似为：

```python
type(paused_result)          # dict
type(interrupt_list)         # list
type(first_interrupt)        # langgraph.types.Interrupt
type(approval_payload)       # dict
```

`Interrupt` 对象有两个重要属性：

```text
.value   interrupt(payload) 传入的 payload
.id      LangGraph 生成的中断标识字符串
```

所以还可以继续读取 payload 字典：

```python
title = paused_result["__interrupt__"][0].value["title"]
```

取值顺序为：字典键 `__interrupt__` -> 列表第 0 项 -> 对象的 `value` 属性 -> payload 字典的 `title` 键。

聊天软件可能把 `__interrupt__` 的双下划线渲染成 Markdown 加粗效果 `**interrupt**`。Python 代码中的真实固定键仍然是：

```python
"__interrupt__"
```
