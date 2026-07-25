# 日常灵感智能助手前端

Vue 3 + TypeScript + Vite 前端，实现注册登录、灵感记录与整理、标签筛选、软删除恢复，以及 AI 摘要/扩展的预览保存。

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
