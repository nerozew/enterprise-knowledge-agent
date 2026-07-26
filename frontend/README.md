# 企业知识工作台前端

Vue 3 + TypeScript + Vite 前端。在原有灵感记录、标签和 AI 整理能力上，增加 Workspace 隔离的知识文档摄取与可追溯 RAG 检索体验。

## P3-P4 页面

- `/w/:workspaceId/knowledge`：上传 PDF、DOCX、Markdown、TXT，查看不可变发布版本、摄取进度、失败重试与取消。
- `/w/:workspaceId/ask`：Dense、BM25、Hybrid、RRF/Weighted、Rerank、Query Rewrite、多查询和上下文扩展实验；展示发布版本、页码、Source Span 和分数分解。
- `VIEWER` 只读；Workspace 切换会中止旧请求与轮询并清除旧来源。
- 轮询从 2 秒退避到 10 秒，页面隐藏时降频；未终态 Job ID 保存在当前浏览器会话中用于刷新恢复。

实验答案属于 P4 Basic RAG，不代表 P7 正式证据门禁。界面会明确标记 FIXTURE Provider 和降级响应，这些结果不能作为真实准确率或简历指标。

## 本地运行

```bash
cp .env.example .env.local
npm install
npm run dev
```

默认通过 Vite 将 `/api` 代理到 `http://127.0.0.1:8000`，API 前缀为 `/api/v1`。

## 质量检查

```bash
npm run lint
npm run type-check
npm run test
npm run build
```

浏览器只持久化短期 JWT 和用户展示信息，不保存服务端密钥。收到 401 后会清除本地会话并由路由守卫返回登录页。
