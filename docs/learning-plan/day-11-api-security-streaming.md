# Day 11：FastAPI 接口、安全与流式输出

## 今日目标

把 Agentic RAG 暴露为稳定、安全、可取消的后端接口，并复用现有认证和用户隔离。

## 今日时间表

1. 09:00-10:00：确定 API 契约和错误结构。
2. 10:00-12:00：实现会话与问答接口。
3. 14:00-15:30：实现 SSE 流式输出。
4. 15:45-17:00：完成权限、限流、超时和取消。
5. 17:00-18:00：接口测试与 OpenAPI 检查。

## 必学知识

- `【非常重点】认证不等于授权`：登录后仍需校验文档和会话是否属于当前用户。
- `【非常重点】SSE 生命周期`：客户端断开后应取消下游任务，避免继续消耗 Token。
- `【非常重点】错误契约`：参数错误、权限错误、模型错误和检索错误使用稳定错误码。
- `【非常重点】Prompt Injection`：检索文档属于不可信输入，不能获得系统权限或调用高风险工具。
- `【重点】超时和限流`：模型、Embedding、Reranker 和外部工具分别配置边界。

## 建议接口

```text
POST   /api/knowledge/documents
GET    /api/knowledge/documents/{id}
DELETE /api/knowledge/documents/{id}
POST   /api/agent/sessions
POST   /api/agent/sessions/{id}/messages
POST   /api/agent/sessions/{id}/resume
GET    /api/agent/sessions/{id}/events
```

## 编码任务

1. 使用 Pydantic 定义所有请求和响应。
2. 从认证上下文获取 `user_id`，不接受客户端指定资源所有者。
3. SSE 区分 token、citation、tool_call、interrupt、error 和 done 事件。
4. 日志不得输出完整文档正文、Token 或密码。
5. 测试越权访问、非法文件、超长输入和客户端中断。

## 验收标准

- OpenAPI 契约完整且无模糊 `dict` 响应。
- 用户 A 无法访问用户 B 的文档和会话。
- SSE 可以逐步返回文本和引用。
- 客户端断开后服务器停止相关生成任务。
- 错误响应不泄露内部堆栈或密钥。

## 学习记录

```text
完成日期：
越权测试：
SSE 中断测试：
超时测试：
对应 Git 提交：
```
