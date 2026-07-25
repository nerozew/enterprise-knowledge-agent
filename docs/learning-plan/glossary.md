# RAG + Agent 中英术语词典

## 使用方法

每个术语记录四类信息：

- `IPA`：标准音标。
- `重音读法`：使用大写标出重读部分，帮助初学时开口。
- `中文含义`：在 Agent 开发中的常见翻译。
- `代码语境`：这个词在当前项目里具体指什么。

重音读法只是辅助，准确发音以 IPA 和正规词典音频为准。

## Day 01：Tool Calling

| English | IPA | 重音读法 | 中文含义 | 代码语境 |
|---|---|---|---|---|
| agent | /ˈeɪdʒənt/ | AY-juhnt | 智能体 | 能循环决策并调用工具的运行系统 |
| tool | /tuːl/ | TOOL | 工具 | 提供给模型选择的可执行能力 |
| tool calling | /tuːl ˈkɔːlɪŋ/ | TOOL CALL-ing | 工具调用 | 模型生成工具名称和参数的过程 |
| structured | /ˈstrʌktʃərd/ | STRUC-tured | 结构化的 | 数据遵守明确字段和类型 |
| schema | /ˈskiːmə/ | SKEE-muh | 模式、结构定义 | 描述工具参数名称、类型和必填规则 |
| object | /ˈɑːbdʒekt/ | OB-ject | 对象 | Python 中带有类型和行为的值 |
| class | /klæs/ | CLASS | 类 | 用于创建对象的类型定义 |
| function | /ˈfʌŋkʃən/ | FUNC-tion | 函数 | 接收参数并执行逻辑的代码单元 |
| decorator | /ˈdekəreɪtər/ | DEC-uh-ray-ter | 装饰器 | 包装或增强函数、类的 Python 语法 |
| description | /dɪˈskrɪpʃən/ | de-SCRIP-tion | 描述 | 告诉模型工具用途的文字 |
| parameter | /pəˈræmɪtər/ | puh-RAM-i-ter | 参数 | 调用函数或工具时需要提供的数据 |
| property | /ˈprɑːpərti/ | PROP-er-ty | 属性、字段 | Schema 中定义的参数字段 |
| required | /rɪˈkwaɪərd/ | re-QUIRED | 必需的 | 调用工具时必须提供的参数 |
| invoke | /ɪnˈvoʊk/ | in-VOKE | 调用、执行 | 使用 `.invoke()` 执行 Runnable |
| runtime | /ˈrʌntaɪm/ | RUN-time | 运行时 | 真正调度模型和执行工具的环境 |
| runnable | /ˈrʌnəbəl/ | RUN-uh-buhl | 可运行组件 | LangChain 中统一支持 `invoke` 的接口 |
| chain | /tʃeɪn/ | CHAIN | 链 | 按顺序组合多个 Runnable 的执行流程 |
| return | /rɪˈtɜːrn/ | re-TURN | 返回 | 函数执行后交给调用方的值 |
| persist | /pərˈsɪst/ | per-SIST | 持久保存 | 将数据保存到进程结束后仍存在的存储中 |
| inheritance | /ɪnˈherɪtəns/ | in-HAIR-i-tuhns | 继承 | 子类获得父类接口和行为的机制 |
| bind | /baɪnd/ | BIND | 绑定 | 把工具定义绑定到模型可用的工具集合 |
| response | /rɪˈspɑːns/ | re-SPONCE | 响应 | 模型或服务返回的结果对象 |
| execute | /ˈeksɪkjuːt/ | EX-i-cute | 执行 | 真正运行函数或工作流中的操作 |
| message | /ˈmesɪdʒ/ | MESS-ij | 消息 | 对话中带有角色和内容的数据对象 |
| content | /ˈkɑːntent/ | CON-tent | 内容 | 消息中的文本或其他内容块 |
| argument | /ˈɑːrɡjəmənt/ | AR-gyuh-ment | 实参 | 调用函数或工具时实际传入的值 |
| metadata | /ˈmetədeɪtə/ | MET-uh-day-tuh | 元数据 | 描述调用本身的信息，如模型名和 Token 用量 |
| tool message | /tuːl ˈmesɪdʒ/ | TOOL MESS-ij | 工具消息 | 把工具执行结果返回给模型的消息 |
| correlation | /ˌkɔːrəˈleɪʃən/ | cor-uh-LAY-shun | 关联、对应 | 使用 Tool Call ID 配对请求与工具结果 |
| list | /lɪst/ | LIST | 列表 | 按顺序保存多个 Tool 或 Message 的集合 |
| map | /mæp/ | MAP | 映射 | 使用名称快速查找对应 Tool 对象的关系 |
| attribute | /ˈætrɪbjuːt/ | AT-ri-byoot | 属性 | 使用点号读取的对象信息，如 `save_idea.name` |
| collection | /kəˈlekʃən/ | kuh-LEK-shun | 集合 | 统一保存多个相关对象的数据结构 |
| instance | /ˈɪnstəns/ | IN-stuhns | 实例 | 由某个类创建出来的具体对象 |
| method | /ˈmeθəd/ | METH-uhd | 方法 | 绑定在类或对象上的可调用行为 |
| self | /self/ | SELF | 实例自身 | 实例方法中接收当前对象的第一个参数 |
| identifier | /aɪˈdentɪfaɪər/ | eye-DEN-ti-fire | 标识符 | 用于区分消息、工具调用或资源的名称或 ID |
| implementation | /ˌɪmplɪmenˈteɪʃən/ | im-pluh-men-TAY-shun | 实现 | 函数体中真正完成业务操作的代码 |
| function body | /ˈfʌŋkʃən ˈbɑːdi/ | FUNC-tion BODY | 函数体 | 函数被调用时实际执行的代码块 |
| docstring | /ˈdɑːkstrɪŋ/ | DOC-string | 文档字符串 | 函数体第一条字符串，用于说明函数或工具用途 |
| comprehension | /ˌkɑːmprɪˈhenʃən/ | com-pri-HEN-shun | 推导式 | 使用紧凑语法遍历并生成新集合 |
| validation | /ˌvælɪˈdeɪʃən/ | val-i-DAY-shun | 校验 | 检查输入是否符合类型、范围和业务规则 |
| state | /steɪt/ | STATE | 状态 | 工作流当前共享的数据快照 |
| node | /noʊd/ | NODE | 节点 | 读取 State、执行逻辑并返回状态更新的函数 |
| edge | /edʒ/ | EDGE | 边 | 指定节点之间执行顺序或条件路由的连接 |
| deterministic | /dɪˌtɜːrmɪˈnɪstɪk/ | de-ter-mi-NIS-tic | 确定性的 | 相同输入和环境下产生相同结果 |
| TypedDict | /taɪpt dɪkt/ | TYPED-DICT | 带类型的字典结构 | 描述字典应包含哪些键和值类型的 Python 类型工具 |

## Day 03：Memory 与 HITL

| English | IPA | 重音读法 | 中文含义 | 代码语境 |
|---|---|---|---|---|
| human-in-the-loop | /ˈhjuːmən ɪn ðə luːp/ | HUMAN-in-the-LOOP | 人在回路、人工介入 | Agent 在关键决策处暂停，由人审批、修改或补充信息后继续 |
| HITL | /ˌeɪtʃ aɪ tiː ˈel/ | H-I-T-L | Human-in-the-loop 的缩写 | 当前项目在保存、删除等写操作前要求用户确认 |
| checkpoint | /ˈtʃekpɔɪnt/ | CHECK-point | 检查点、状态快照 | Graph 每个步骤保存的 State 版本 |
| checkpointer | /ˈtʃekpɔɪntər/ | CHECK-point-er | 检查点保存器 | 负责读写 Checkpoint 的组件 |
| thread | /θred/ | THRED | 会话线程 | 使用 `thread_id` 隔离并恢复同一会话的状态 |
| interrupt | /ˌɪntəˈrʌpt/ | in-ter-RUPT | 中断、暂停 | 在节点内部暂停 Graph 并向调用方展示审批信息 |
| resume | /rɪˈzuːm/ | re-ZOOM | 恢复、继续 | 使用 `Command(resume=...)` 为中断提供回答并继续执行 |
| exception | /ɪkˈsepʃən/ | ex-CEP-tion | 异常 | 程序执行期间产生、可以抛出和捕获的错误或控制信号对象 |
| approval | /əˈpruːvəl/ | uh-PROOV-al | 审批、批准 | 人工决定是否允许 Agent 执行写入等高风险操作 |

## 容易混淆的词

### object 和 class

- `class` 是类型定义，例如 `StructuredTool`。
- `object` 是根据某种类型存在的具体值，例如变量 `save_idea` 指向的 StructuredTool 对象。

### call 和 invoke

- `call` 是“调用”的通用说法。
- `invoke` 是 LangChain Runnable 使用的统一执行方法名。

### save 和 persist

- `save` 可以泛指保存，也可能只是写入内存列表。
- `persist` 强调持久化，程序退出后数据仍然存在。

## 后续词汇

Day 02 开始继续追加 State、Node、Edge、Reducer、Checkpoint、Embedding、Retrieval、Reranker 等术语。
