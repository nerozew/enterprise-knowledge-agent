# P8 Agent MVP 工作检查点

更新时间：2026-08-03

状态：`WIP`，当前分支用于保存阶段性实现，尚未达到发布门禁。

## 已完成

- LangGraph 四路由领域内核：直接回答、知识问答、灵感检索、待办创建。
- Pydantic 结构化输出、Tool Calling 适配、Checkpoint 与 HITL 审批领域逻辑。
- Conversation、Run、Approval、Todo、Run Snapshot 与 SSE REST 契约和基础实现。
- 前端 Agent 工作台、审批卡、引用卡、待办页、SSE 断线续传与 410 快照恢复。
- 可信 RAG：Dense/BM25/Hybrid、Weighted/RRF、Reranker、Query Rewrite、上下文扩展、引用与拒答诊断。
- 冻结并人工复核 100 题评测集，保存 7 组消融实验及原始报告。

## 已验证

- 前端：lint、类型检查、109 项单元测试和生产构建通过。
- 后端：Ruff 通过；当前中断检查为 `205 passed, 8 skipped, 2 failed`。
- 两个失败均为新增 `0011_p8_agent_dispatch` 后迁移测试仍断言旧的 `0010` head。
- Mypy 当前被本地 Python 3.11 配置与虚拟环境内 NumPy Python 3.12 stub 语法冲突阻断。

## 未完成 / 阻塞项

1. Run 持久化调度闭环仍在开发中：已经加入 dispatch 数据模型、仓储与迁移草稿，但 Worker/恢复执行/自然终态测试尚未完成。
2. SSE 连接建立后需要持续复查 Workspace 成员资格，确保撤权后不再发送业务事件或心跳。
3. 前端需完成确定性 SSE 错误终止重连、Workspace 切换取消旧写请求及 VIEWER 审批只读态。
4. 需要补真实 MySQL 并发、Checkpoint、审批撤权竞态和真实浏览器 REST/SSE E2E。

继续开发时应先修复上述 S1/S2，并由 Reviewer 增量复审后再宣告 P8 完成。
