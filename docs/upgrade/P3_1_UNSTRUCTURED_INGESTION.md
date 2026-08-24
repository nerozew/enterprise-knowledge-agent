# P3.1 非结构化文档解析与入库契约

更新时间：2026-08-24  
实施分支：`codex/nonstructured-ingestion-v2`  
安全基线：`f216ae2 wip(p9): checkpoint MCP streamable HTTP progress`

## 1. 目标与边界

P3.1 在现有文档摄取链路上增加版面感知解析、PPTX、扫描 PDF 页级 OCR、父子
Chunk v2 与可解析 Source Span：

```text
PDF / DOCX / PPTX / Markdown / TXT / 扫描 PDF
→ 安全校验 → 统一 LayoutDocumentV2 → AUTO/FORCE/OFF OCR
→ 父子 Chunk → Embedding → Milvus 暂存索引
→ MySQL 发布指针切换 → 版本化 Source Span 引用
```

首期扫描件仅指扫描 PDF。独立 JPG/PNG/TIFF、PPTX 图片全文 OCR、复杂图表与
SmartArt 识别不属于本阶段完成范围。

## 2. 数据事实与存储边界

- MySQL 是版本、可见性、Block、Chunk、Source Span、Artifact、Job 和 Outbox 的事实源。
- MinIO 保存不可变原文件及可重建的 Layout/OCR/预览派生产物。
- Milvus 只保存子 Chunk 向量和过滤字段；命中后必须回 MySQL 校验租户、文档状态及
  当前发布版本。
- Redis 只保存短期缓存和心跳，不承担任务或发布事实。
- 解析、OCR、Embedding 或索引失败不得删除 canonical source，也不得影响旧发布版本。

## 3. 统一中间表示

`LayoutDocumentV2` 记录格式、源 SHA256、页/幻灯片数、Parser/OCR Profile、告警和
按阅读顺序排列的 Blocks。每个 Block 至少记录：

- 稳定 `block_key`、类型、正文和正文 SHA256；
- `page_no` 或 `slide_no`；DOCX 无可靠分页时不得伪造页码；
- 归一化 bbox、reading order、heading path、字符范围；
- OCR 是否执行及置信度。

同一版本、同一解析配置重试时 Block、Chunk 与 Source Span ID 必须稳定。

## 4. API 兼容契约

- 上传及创建版本接口增加 multipart 字段 `ocr_mode=AUTO|FORCE|OFF`，默认 `AUTO`。
- Job 响应兼容增加 `stage_detail`、页数/OCR 进度、warnings 与 retryable。
- SearchHit 保留旧 `source_span_id`，兼容增加 `source_spans[]`。
- 新增 Source Span 解析接口：

```http
GET /api/v1/workspaces/{workspace_id}/documents/{document_id}/versions/{version_id}/source-spans/{source_span_id}
```

Viewer 只能解析当前已发布版本；Editor 访问历史或失败版本仍须通过 Workspace 归属与角色校验。

## 5. 可靠性与安全门禁

- OCR 使用专用执行线程，Worker 对长任务续租；超过页数、DPI、超时或资源限制时整版本失败。
- AUTO 仅对原生有效文本不足或乱码比例超阈值的页面 OCR；不得无条件 OCR 整份混合 PDF。
- 支持 PDF magic、Office ZIP 安全、宏、外链、路径穿越、压缩炸弹和加密 PDF 检查。
- 失败只清理临时/派生对象及未激活向量，不清理 canonical source。
- 不允许部分页面成功后发布；两 Worker 并发必须收敛到同一个版本结果。
- 日志不得记录密钥、完整正文或第三方原始响应。

## 6. 功能开关

```env
UNSTRUCTURED_PIPELINE_V2_ENABLED=false
RAG_PPTX_ENABLED=true
RAG_OCR_ENABLED=true
RAG_OCR_ENGINE=RAPIDOCR
RAG_OCR_LANGUAGES=ch,en
RAG_OCR_MODE_DEFAULT=AUTO
RAG_OCR_MAX_PAGES=200
RAG_OCR_RENDER_DPI=300
```

V2 默认关闭，便于回滚到现有解析链路。Production 不允许在依赖缺失时静默伪装成
OCR 成功。

## 7. 验收证据

代码完成不等于门禁通过。完成声明必须附带以下实际结果：

- Alembic upgrade 与兼容回滚策略检查；
- PDF/DOCX/PPTX/Markdown/TXT 和扫描/混合 PDF 解析测试；
- 重试、并发、租约、跨存储失败注入和旧版本持续可检索测试；
- 跨租户、草稿、失败版本和未发布 Span 泄露测试；
- 后端 Ruff、Mypy、Pytest；前端 lint、type-check、test、build；
- `eval/unstructured-v1` 的格式成功率、OCR CER、阅读顺序、父子完整率、Span
  可解析率、引用定位和原 100 题 RAG 回归报告。

外部 Docling/RapidOCR、MinIO、Milvus 或真实扫描件未运行时，必须标记为“未验证”，
不得将替身单测写成真实环境成果。

## 8. 实施结果（2026-08-24）

- 后端已实现 PPTX、LayoutDocumentV2、Docling/RapidOCR、AUTO/FORCE/OFF、页级
  AUTO OCR、父子 Chunk、稳定多 Source Span、Artifact 落盘和引用解析 API。
- Worker 使用 Outbox owner/attempt CAS fencing，长任务 heartbeat 覆盖解析、对象存储、
  Embedding 和 Milvus 阶段；可重试失败不会生成破坏成功重放结果的补偿。
- 前端已支持 PPTX、OCR 模式、解析阶段/警告、Parser/OCR 标记及 Source Span 解析弹窗。
- MySQL 开发库已从 `0011_p8_agent_dispatch` 升级到
  `0012_p31_unstructured_ingestion (head)`。
- 后端 Ruff、Mypy 及 244 项测试通过（9 项外部环境测试跳过）；前端 lint、type-check、
  142 项测试和生产构建通过。
- 冻结控制集 `unstructured-v1` 的 14/14 用例通过；原始报告见
  `eval/reports/unstructured-v1/20260824T075149Z`。

尚未关闭的外部验证边界：当前会话没有可用的 Redis/MinIO/Milvus 端口，因此没有执行
真实跨存储摄取、补偿联调和原 100 题 live RAG 回归；当前主机也没有 LibreOffice，
DOCX/PPTX 只完成结构解析检查，未完成页面渲染复核。这些项目不能声称已通过。

## 9. 第三轮复审遗留风险

第三轮 Reviewer 结论为 `S1=0、S2=2、S3=1`，满足本阶段数量门禁，但以下风险必须在
真实外部服务联调前关闭：

- **S2：显式重试没有保留原任务 OCR 模式。** 首次上传事件保存了有效 `ocr_mode`，但
  当前显式重试会读取届时的系统默认值；若默认值发生变化，`FORCE/OFF/AUTO` 语义以及
  解析结果可能随之改变。后续应把解析配置快照持久化到版本/任务，并让重试复用原快照；
  若允许人工覆盖，需要纳入审计和幂等键。
- **S2：旧补偿事件可能与显式重试并发。** 非重试失败产生的派生对象清理或向量停用事件，
  目前只在版本已经 `PUBLISHED` 时跳过；如果旧补偿在新尝试处于 `PUBLISHING` 时执行，
  仍可能清理新尝试的派生产物。后续应让补偿绑定 attempt/generation，或在创建新尝试时
  原子取消旧的待执行补偿。
- **S3：同步 Chunk/Span 构造阶段没有独立心跳。** 极端大文档在父子分块和 Span 批量构造
  期间理论上可能超过租约。后续可将该阶段移入执行线程，或为整个摄取任务增加持续心跳。

因此，本阶段可声明“本地功能、测试与评估门禁通过”，不能声明“真实生产跨存储链路已
完全验收”。
