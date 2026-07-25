# 两周 RAG + Agent 求职项目学习计划

## 1. 最终目标

用 14 天把现有“日常灵感智能助手”升级为一个可以部署、演示、评估和写进简历的 RAG + Agent 项目。

项目最终应具备：

- 用户上传 PDF、DOCX、Markdown、TXT 文档。
- 文档异步解析、切分、向量化并进入知识库。
- 基于知识库回答问题，并返回文档名、页码或片段引用。
- 支持关键词与向量混合检索、Reranker 和无答案拒答。
- 使用 LangGraph 在直接回答、知识库检索、灵感查询、创建待办之间路由。
- 使用 Checkpointer 保存会话状态，高风险写操作需要用户确认。
- 使用 FastAPI、MySQL、Redis、MinIO、Milvus 和 Vue 组成完整系统。
- 使用 Docker Compose 部署，并提供测试、评估、压测和项目文档。

## 2. 学习优先级标记

每份讲义使用以下标记：

- `【非常重点】`：必须能脱离教程解释并独立实现，面试高频。
- `【重点】`：必须理解用途并能在项目中正确使用。
- `【了解】`：知道解决什么问题，现阶段可以查文档实现。
- `【暂时跳过】`：两周内不投入时间。

## 3. 每日固定节奏

建议每天投入 8 小时：

1. 09:00-10:30：学习当天概念和官方文档。
2. 10:45-12:00：完成最小实验，观察中间对象和错误。
3. 14:00-17:00：将知识接入主项目。
4. 17:15-18:15：编写测试并运行质量检查。
5. 20:00-21:00：记录结果、指标、问题和面试答案。

每天只在“验收标准”全部满足后进入下一天。遇到问题时先保存完整报错、输入、版本和预期结果。

### 每日复习机制

每天结束时不只阅读总结，采用主动回忆：

1. 先给出当日知识地图，不立即公布题目答案。
2. 学习者使用自己的话逐题回答概念、流程和代码推理题。
3. 对每题标记“准确、基本正确、需要补强”，校准术语与错误模型。
4. 针对错误点继续提出更短的二次确认题，直到可以独立回答。
5. 下一学习日开始前，抽查前一天最薄弱的 2-3 个知识点。

目标不是背诵标准句子，而是能够解释数据流、预测运行结果并发现危险代码。

### 框架 API 教学规则

从 Day 01 起，每次引入新的 LangChain、LangGraph 或其他框架 API，讲义必须同时提供：

1. 官方导入路径和官方文档链接。
2. 标明哪些名称属于固定 API，哪些只是示例中的自定义变量。
3. 必要时使用当前学习环境打印真实 `inspect.signature()`。
4. 说明 API 的输入、返回类型和最小示例。
5. 遇到旧教程写法时，明确标注当前版本替代方式。

## 4. 十四天索引

配套资料：

- [RAG + Agent 中英术语词典](glossary.md)
- [Python 基础补充讲义](python-basics.md)
- [企业知识库与智能行动 Agent 平台：简历目标稿](../portfolio/resume-project.md)

每一天出现的重要英文术语和相关 Python 基础都会逐步加入这些文件。

| 天数 | 主题 | 核心产出 |
|---|---|---|
| [Day 01](day-01-tool-calling.md) | Tool Calling 与 `create_agent` | Agent 能正确选择并调用工具 |
| [Day 02](day-02-langgraph-basics.md) | LangGraph 状态与路由 | 可观察的条件工作流 |
| [Day 03](day-03-memory-hitl.md) | Memory、持久化与人工确认 | 可恢复会话和确认后写入 |
| [Day 04](day-04-rag-ingestion.md) | RAG 数据摄取 | 文档解析、切分和向量入库 |
| [Day 05](day-05-rag-generation.md) | 检索生成与引用 | 带来源的知识库问答 |
| [Day 06](day-06-data-pipeline.md) | 异步知识库管道 | 上传、状态、重试和删除闭环 |
| [Day 07](day-07-evaluation-baseline.md) | RAG 评估基线 | 可复现的质量与性能基线 |
| [Day 08](day-08-hybrid-search.md) | 混合检索 | Dense + Sparse 检索融合 |
| [Day 09](day-09-rerank-query-rewrite.md) | Rerank 与查询改写 | 提升排序质量与多轮检索能力 |
| [Day 10](day-10-agentic-rag.md) | Agentic RAG | LangGraph 统一知识库与业务工具 |
| [Day 11](day-11-api-security-streaming.md) | API、安全与流式输出 | 可供前端调用的安全接口 |
| [Day 12](day-12-frontend-integration.md) | 前端联调 | 完整浏览器演示流程 |
| [Day 13](day-13-deployment-observability.md) | 部署与可观测性 | 在线地址、日志与链路追踪 |
| [Day 14](day-14-testing-portfolio.md) | 测试、压测与求职包装 | README、指标、视频和简历描述 |

## 5. 两周内严格控制的范围

`【暂时跳过】`：Multi-Agent、CrewAI、AutoGen、模型微调、vLLM、Kubernetes 实战、知识图谱 RAG、复杂 OCR、完整 ELK 和大规模分布式架构。

这些内容不是没价值，而是会分散两周项目的完成度。当前只选择一套技术实现完整闭环。

## 6. 每日学习记录模板

每天结束时，在对应 Day 文件底部填写：

```text
完成日期：
实际投入时间：
完成的验收项：
遇到的三个问题：
今天最重要的理解：
仍然不理解的内容：
测试或指标结果：
对应 Git 提交：
```

## 7. 当前起点

已经具备：FastAPI、Django、MySQL、基础 Redis、Prompt Template、Few-shot、LCEL 和 Pydantic 结构化输出。

因此课程不再重复 Python Web 入门。第一天从 Tool Calling 开始，随后进入 LangGraph 和 RAG 工程。
