# P2-P4 本地运行与验证

## 1. 当前验证边界

仓库提供 P2-P4 Compose 与适配器配置，但创建本文件时的 Windows 主机没有安装 Docker。
因此 Compose、真实 MinIO、Milvus 原生 BM25/Hybrid、卷恢复和停容器补偿尚未在本机验证。
先完成 L0/L1 代码与确定性开发测试，再在安装 Docker Desktop（WSL2 后端，至少 8 GiB 内存）
后执行 L3。

## 2. 准备

```powershell
Copy-Item .env.example .env
notepad .env
docker compose config
```

必须替换 MySQL、JWT 和 MinIO 密码。真实 Provider Key 只能写入忽略提交的 `.env`。
`RAG_PROVIDER_MODE=FIXTURE` 只允许测试，不能作为产品运行或准确率声明。

## 3. 启动与诊断

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail 100 backend
```

入口：

- 前端：`http://127.0.0.1:8080`
- API：`http://127.0.0.1:8000/docs`
- MinIO Console：`http://127.0.0.1:9001`
- Milvus：`127.0.0.1:19530`

`docker compose down` 保留命名卷。只有确认不再需要本地数据时才能显式执行
`docker compose down --volumes`；该操作不可恢复。

## 4. L3 验证清单

1. `docker compose config` 无错误，所有服务变为 healthy。
2. 注册、Workspace、灵感与审计回归通过。
3. 分别上传 PDF、DOCX、Markdown、TXT，检查中文标题、父子 Chunk 和固定版本来源。
4. 相同文件和幂等键重放不产生重复版本或向量。
5. 分别停止 Redis、MinIO、Milvus：P1 仍可用；新写入按契约降级；恢复后 Outbox 幂等追平。
6. 重启所有容器，确认 MySQL、Redis、MinIO、Milvus 命名卷数据仍存在。
7. 用真实 Embedding Profile 重建 Collection，执行 Dense、BM25、Hybrid、Rerank 消融。
8. 保存完整命令、镜像 digest、数据集 Hash、逐题结果、延迟、机器配置和失败案例。

## 5. MinIO 风险

本 Compose 从冻结源码 Tag 构建 MinIO，只用于本地学习和 CI。由于上游社区仓库归档与分发策略变化，
生产部署不得直接沿用；正式部署前必须选择受支持的 AIStor、受控 fork 或重新批准的 S3 兼容对象存储。
