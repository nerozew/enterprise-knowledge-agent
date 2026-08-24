# unstructured-v1 评估集

该目录用于 P3.1 版面解析、OCR、父子 Chunk 与 Source Span 的版本化评估。数据集与
现有 `dev-v1`/`test-v1` RAG 问答集相互独立：本目录评估“文档如何被解析和定位”，
原 100 题继续评估“知识是否被正确召回和回答”。

## 目录约定

```text
unstructured-v1/
├── manifest.json
├── cases.jsonl
├── corpus/             # 受控输入文件
├── gold/               # 人工标注 Block/OCR/Span 事实
└── README.md
```

二进制语料必须是项目自建或许可清晰的测试材料，不得加入真实企业机密。每个文件在
`manifest.json` 中记录 SHA256、生成方式、预期 Parser/OCR Profile 和授权信息。

## 首期用例矩阵

- 原生文本、双栏、表格、纯扫描中文、中英混排、原生/扫描混合 PDF；
- DOCX 标题、列表和表格；
- PPTX 标题、文本框、分栏和表格；
- Markdown/TXT 回归；
- 加密 PDF、宏 Office、ZIP bomb、路径穿越和超限文档等负例。

## 指标与门禁

- 支持格式成功率、父子关系完整率、Source Span 可解析率：100%；
- 控制语料 page/slide 定位正确率：100%；
- 清晰中文扫描件 CER：不高于 8%；
- 跨租户、草稿或失败版本泄露：0；
- 原 100 题文档 Recall@5 相对冻结基线下降不超过 1 个百分点。

报告还应记录 Block 类型准确率、阅读顺序准确率、OCR 页检测、摄取阶段 P50/P95、
Parser/OCR/Chunk Profile、机器配置、日期和随机种子。

## 当前状态

`unstructured-v1` 已冻结 14 个项目自建控制用例，基线报告位于
`eval/reports/unstructured-v1/20260824T075149Z`。本次真实运行使用 Docling 2.121、
RapidOCR 和 ONNX Runtime，在本机 CPU 上完成，14/14 用例通过：

- 支持格式成功率、危险文件拒绝率：100%；
- Block 类型、阅读顺序、页/幻灯片定位：100%；
- 父子关系、Source Span 可解析、稳定 Chunk ID：100%；
- 两份清晰合成扫描件 CER：0%，仅代表受控样本，不外推为生产 OCR 精度；
- 解析耗时 P50 157 ms、P95 9.28 s；P95 包含首次模型冷启动。

PDF 样本已渲染并人工检查。当前主机没有 LibreOffice，因此 DOCX/PPTX 未做页面级渲染
复核，只完成了真实结构解析与 Block/定位断言。外部 MinIO/Milvus 及原 100 题 live RAG
回归因对应服务未启动而未执行，不能把本报告描述为完整生产联调。

## 生成与执行

语料由项目脚本生成，评测冻结时会把每个文件的 SHA256 写入 `cases.jsonl`。Office
容器元数据可能随生成时间变化，因此正式报告必须使用仓库中已冻结的文件，不能在运行前
重新生成：

```powershell
python .\eval\unstructured-v1\generate_fixtures.py
```

安装后端 `unstructured` 可选依赖后执行真实 Docling/RapidOCR 评估：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\unstructured-v1\run_parser_eval.py
```

运行器会把逐用例观察保存到 `eval/reports/unstructured-v1/<UTC时间>/raw-results.json`，
并另存聚合 `metrics.json`。Provider 不可用、未知异常或文件哈希漂移都按失败记录；负例
只有被确定性的文档安全校验拒绝时才计为通过。
