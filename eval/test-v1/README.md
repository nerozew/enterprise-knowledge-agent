# test-v1：100 题正式评测候选集

该目录用于把 24 题开发回归扩展为 100 题正式候选集。它与 `dev-v1` 共用同一份 8 文档
固定语料，但问题是独立编写的，不能用于调参后再反复修改答案标签。

当前状态为 `DRAFT`：

- `cases.draft.jsonl`：80 道可回答题和 20 道不可回答题。
- 每题初始 `review_status` 为 `PENDING`。
- 在人工逐题确认问题自然性、可回答性、qrels 和原文引用前，不得生成正式指标。
- 人工复核完成后，将通过校验的题目状态改为 `APPROVED`，复制为 `cases.jsonl`，写入
  `cases_snapshot`，再开始正式消融实验。

## 自动校验

```powershell
.\backend\.venv\Scripts\python.exe .\eval\validate_dataset.py `
  --dataset .\eval\test-v1 `
  --cases-name cases.draft.jsonl `
  --expected-count 100
```

冻结前的严格校验：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\validate_dataset.py `
  --dataset .\eval\test-v1 `
  --cases-name cases.jsonl `
  --expected-count 100 `
  --require-approved
```

## 人工复核要点

1. 问题不能直接照抄 `dev-v1`，也不能依赖语料外常识。
2. 可回答题必须能由 `gold_sources.quote` 直接支持。
3. 多来源题必须确实需要两个来源，不能用单文档就完整回答。
4. 不可回答题必须在当前已发布语料中没有答案。
5. 复核者不能依据某次检索结果反向改 qrels，否则会造成评测集泄漏。

## 断点续审

以下命令逐题显示问题、期望行为、相关文档和原文证据。输入 `A` 通过、`R` 驳回、
`S` 跳过、`Q` 保存退出；决定写入未纳入版本控制的 `review.decisions.json`。

```powershell
.\backend\.venv\Scripts\python.exe .\eval\review_dataset.py `
  --dataset .\eval\test-v1 `
  --reviewer NeroZe
```

查看进度：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\review_dataset.py `
  --dataset .\eval\test-v1 `
  --reviewer NeroZe `
  --summary
```

全部通过后导出带复核人和时间的候选冻结文件：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\review_dataset.py `
  --dataset .\eval\test-v1 `
  --reviewer NeroZe `
  --export
```

严格校验通过后冻结数据集：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\freeze_dataset.py `
  --dataset .\eval\test-v1
```

冻结操作会生成 `cases.jsonl`，并把题集及共享语料的 SHA-256 写入
`manifest.json`。冻结后如需修改任何题目，必须创建新的数据集版本，不能覆盖
`test-v1`。
