# 真实 RAG 评估

`run_live_rag.py` 通过当前 FastAPI REST 接口运行版本化评估集。它与网页调用同一个
`/workspaces/{workspace_id}/search` Endpoint，因此会使用当前配置的真实 Embedding、
Milvus、BM25、Reranker、Query Rewrite 和 Answer Provider。

旧的 `backend/app/evaluation/runner.py` 继续用于确定性的 Fixture 回归。Fixture 报告不得作为
真实检索质量、延迟或简历数字。

## 安全约束

- 不要把邮箱、密码、Access Token、API Key 写入命令、脚本、数据集或报告。
- 脚本只从环境变量读取认证信息，报告不会保存认证信息。
- 输出目录必须为空；每次评估使用新的版本化目录，避免覆盖原始报告。
- `dev-v1` 是开发集。它可以用于调试和选参，但不能冒充 P10 的独立 100 题盲测集。

## 前置条件

1. FastAPI、MySQL、Redis、MinIO、Milvus 和 Knowledge Worker 已按项目说明启动。
2. `http://127.0.0.1:8000/health/ready` 返回 `ready`。
3. 评估 Workspace 中已经发布 `eval/dev-v1/corpus` 的 8 份文档，标题保持为文件名，
   例如 `remote-work`、`security-access`。
4. 使用 `backend/.venv` 运行脚本，其中已经安装 `httpx`。

## 在 PowerShell 中设置本次会话凭据

推荐使用凭据弹窗，避免把密码写入 PowerShell 历史：

```powershell
$cred = Get-Credential -Message '输入本地 RAG 评测账号'
$env:RAG_EVAL_EMAIL = $cred.UserName
$env:RAG_EVAL_PASSWORD = $cred.GetNetworkCredential().Password
$env:RAG_EVAL_WORKSPACE_ID = '4a790b5e-3e9d-45d0-a387-4a4cc56de656'
```

也可以只设置已有的短期 Access Token：

```powershell
$env:RAG_EVAL_ACCESS_TOKEN = '<仅保存在当前 PowerShell 进程>'
$env:RAG_EVAL_WORKSPACE_ID = '4a790b5e-3e9d-45d0-a387-4a4cc56de656'
```

脚本优先使用 `RAG_EVAL_ACCESS_TOKEN`；未设置时才使用邮箱和密码调用登录接口。

## 第一步：无请求 Dry Run

Dry Run 不登录、不调用 Provider，也不创建报告：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --dry-run `
  --mode HYBRID `
  --fusion RRF `
  --rerank OFF `
  --rewrite OFF `
  --response-mode HITS
```

检查输出中的 Workspace、题目数量和首个请求参数。

## 第二步：单题真实冒烟

先运行 `dev-020`，核对结果是否与网页一致：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --case-id dev-020 `
  --mode HYBRID `
  --fusion RRF `
  --rerank OFF `
  --rewrite OFF `
  --response-mode HITS `
  --output .\eval\reports\dev-v1-live\smoke-hybrid-rrf
```

应生成：

- `run.json`：数据集 Hash、Git Commit、运行环境和检索配置；
- `results.jsonl`：逐题请求、真实响应、排名和 Source Span 对照；
- `metrics.json`：Recall、MRR、nDCG、答案门禁和延迟；schema v2 的文档与
  Source Span 检索指标仅统计可回答题，延迟统计全部成功请求，无答案题单独进入
  Answerability 正确拒答、错误拒答和错误放行指标；
- `failures.jsonl`：HTTP 失败、未召回正确文档或拒答错误。

## 第三步：24 题检索基线

每个配置使用独立目录。第一次先 `--repeat 1`；全部成功后再使用 `--repeat 3` 或
`--repeat 5` 统计 P50/P95。

Dense：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --mode DENSE --rerank OFF --rewrite OFF --response-mode HITS `
  --repeat 1 `
  --output .\eval\reports\dev-v1-live\dense-r1
```

Sparse/BM25：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --mode SPARSE --rerank OFF --rewrite OFF --response-mode HITS `
  --repeat 1 `
  --output .\eval\reports\dev-v1-live\sparse-r1
```

Hybrid + RRF：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --mode HYBRID --fusion RRF --rerank OFF --rewrite OFF --response-mode HITS `
  --repeat 1 `
  --output .\eval\reports\dev-v1-live\hybrid-rrf-r1
```

Hybrid + Weighted：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --mode HYBRID --fusion WEIGHTED --dense-weight 0.65 `
  --rerank OFF --rewrite OFF --response-mode HITS `
  --repeat 1 `
  --output .\eval\reports\dev-v1-live\hybrid-weighted-r1
```

## 第四步：高级组件消融

在最佳基础检索配置上一次只改变一个变量：

```text
baseline
baseline + reranker
baseline + query rewrite + multi query
baseline + context expansion
baseline + reranker + query rewrite
```

Query Rewrite 示例：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --mode HYBRID --fusion RRF `
  --rerank OFF --rewrite ON --multi-query `
  --response-mode HITS --repeat 1 `
  --output .\eval\reports\dev-v1-live\hybrid-rrf-rewrite-r1
```

## 第五步：答案与拒答

完成检索选参后再使用 `ANSWER`，避免把答案模型耗时混入检索基线：

```powershell
.\backend\.venv\Scripts\python.exe .\eval\run_live_rag.py `
  --mode HYBRID --fusion RRF `
  --rerank ON --rewrite OFF `
  --response-mode ANSWER --repeat 1 `
  --output .\eval\reports\dev-v1-live\answer-hybrid-rerank-r1
```

该模式额外统计：

- 正确拒答率；
- False Refusal Rate；
- False Acceptance Rate；
- Supporting Hit 是否命中人工 Gold Source。

自动指标暂不能替代人工 Claim Entailment 检查。答案中的数值、主体、比较关系和条件完整率
仍需人工标注或后续增加独立 Judge，并保存 Judge 模型与 Prompt 版本。

## 清理当前 PowerShell 会话凭据

```powershell
Remove-Item Env:RAG_EVAL_ACCESS_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:RAG_EVAL_EMAIL -ErrorAction SilentlyContinue
Remove-Item Env:RAG_EVAL_PASSWORD -ErrorAction SilentlyContinue
Remove-Item Env:RAG_EVAL_WORKSPACE_ID -ErrorAction SilentlyContinue
```
