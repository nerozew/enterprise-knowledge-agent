from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_snapshot(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_sha256(item)))
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    return "".join(value.casefold().split())


def unique_ranked_titles(hits: Sequence[dict[str, Any]]) -> list[str]:
    ranked: list[str] = []
    for hit in hits:
        title = str(hit.get("title", ""))
        if title and title not in ranked:
            ranked.append(title)
    return ranked


def recall_at_k(
    ranked_keys: Sequence[str],
    relevant_keys: set[str],
    k: int,
) -> float:
    if not relevant_keys:
        return 0.0
    return len(set(ranked_keys[:k]) & relevant_keys) / len(relevant_keys)


def reciprocal_rank_at_k(
    ranked_keys: Sequence[str],
    relevant_keys: set[str],
    k: int,
) -> float:
    for rank, key in enumerate(ranked_keys[:k], start=1):
        if key in relevant_keys:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked_keys: Sequence[str],
    relevance_by_key: dict[str, int],
    k: int,
) -> float:
    gains = [relevance_by_key.get(key, 0) for key in ranked_keys[:k]]
    ideal = sorted(relevance_by_key.values(), reverse=True)[:k]

    def discounted(values: Iterable[int]) -> float:
        return sum(
            (2**relevance - 1) / math.log2(rank + 1)
            for rank, relevance in enumerate(values, start=1)
        )

    ideal_score = discounted(ideal)
    return discounted(gains) / ideal_score if ideal_score else 0.0


def _gold_source_matches_hit(source: dict[str, Any], hit: dict[str, Any]) -> bool:
    document_key = source.get("document_key")
    if document_key and document_key != hit.get("title"):
        return False
    source_span_id = source.get("source_span_id")
    if source_span_id:
        return source_span_id == hit.get("source_span_id")
    source_span_ids = source.get("source_span_ids")
    if isinstance(source_span_ids, list) and source_span_ids:
        return hit.get("source_span_id") in source_span_ids
    quote = source.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return False
    normalized_gold = normalize_text(quote)
    normalized_hit = normalize_text(str(hit.get("quote", "")))
    return normalized_gold in normalized_hit


def span_recall_at_k(
    hits: Sequence[dict[str, Any]],
    gold_sources: Sequence[dict[str, Any]],
    k: int,
) -> float | None:
    if not gold_sources:
        return None
    matched = {
        source_index
        for source_index, source in enumerate(gold_sources)
        if any(_gold_source_matches_hit(source, hit) for hit in hits[:k])
    }
    return len(matched) / len(gold_sources)


def span_reciprocal_rank_at_k(
    hits: Sequence[dict[str, Any]],
    gold_sources: Sequence[dict[str, Any]],
    k: int,
) -> float | None:
    if not gold_sources:
        return None
    for rank, hit in enumerate(hits[:k], start=1):
        if any(_gold_source_matches_hit(source, hit) for source in gold_sources):
            return 1.0 / rank
    return 0.0


def evaluate_response(
    case: dict[str, Any],
    response: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    hits = response.get("hits")
    if not isinstance(hits, list):
        raise ValueError("search response hits must be a list")
    qrels = case.get("qrels", [])
    relevance_by_key = {
        str(item["document_key"]): int(item["relevance"])
        for item in qrels
        if isinstance(item, dict) and item.get("document_key")
    }
    relevant_keys = set(relevance_by_key)
    ranked_titles = unique_ranked_titles(hits)
    gold_sources = [
        item for item in case.get("gold_sources", []) if isinstance(item, dict)
    ]
    supporting_ids = set(response.get("answerability", {}).get("supporting_hit_ids", []))
    supporting_hits = [hit for hit in hits if str(hit.get("chunk_id")) in supporting_ids]
    gold_supporting_hits = [
        hit
        for hit in supporting_hits
        if any(_gold_source_matches_hit(source, hit) for source in gold_sources)
    ]
    refusal = response.get("refusal") is not None
    answerability = response.get("answerability", {})
    return {
        "ranked_document_keys": ranked_titles,
        "document_recall_at_1": recall_at_k(ranked_titles, relevant_keys, 1),
        "document_recall_at_k": recall_at_k(ranked_titles, relevant_keys, top_k),
        "document_mrr_at_k": reciprocal_rank_at_k(ranked_titles, relevant_keys, top_k),
        "document_ndcg_at_k": ndcg_at_k(ranked_titles, relevance_by_key, top_k),
        "span_recall_at_1": span_recall_at_k(hits, gold_sources, 1),
        "span_recall_at_k": span_recall_at_k(hits, gold_sources, top_k),
        "span_mrr_at_k": span_reciprocal_rank_at_k(hits, gold_sources, top_k),
        "refused": refusal,
        "refusal_code": (response.get("refusal") or {}).get("code"),
        "answerability_status": answerability.get("status"),
        "supporting_hit_count": len(supporting_hits),
        "gold_supporting_hit_count": len(gold_supporting_hits),
        "has_gold_supporting_hit": bool(gold_supporting_hits),
    }


def percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def mean_optional(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return statistics.fmean(available) if available else None


def compute_metrics(
    records: Sequence[dict[str, Any]],
    *,
    top_k: int,
    response_mode: str,
) -> dict[str, Any]:
    completed = [record for record in records if record.get("status") == "completed"]
    retrieval_latencies = [
        float(record["response"].get("timings_ms", {}).get("retrieval"))
        for record in completed
        if record["response"].get("timings_ms", {}).get("retrieval") is not None
    ]
    total_latencies = [
        float(record["response"].get("timings_ms", {}).get("total"))
        for record in completed
        if record["response"].get("timings_ms", {}).get("total") is not None
    ]
    client_latencies = [float(record["client_elapsed_ms"]) for record in completed]
    answerable = [record for record in completed if record["case"]["answerable"]]
    unanswerable = [record for record in completed if not record["case"]["answerable"]]
    answerable_evaluations = [record["evaluation"] for record in answerable]
    false_refusals = [
        record for record in answerable if record["evaluation"]["refused"]
    ]
    false_acceptances = [
        record for record in unanswerable if not record["evaluation"]["refused"]
    ]
    correct_refusals = [
        record for record in unanswerable if record["evaluation"]["refused"]
    ]
    metrics: dict[str, Any] = {
        "schema_version": 2,
        "request_count": len(records),
        "completed_count": len(completed),
        "failed_count": len(records) - len(completed),
        "observations": {
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
            "retrieval_metrics_population": "ANSWERABLE_ONLY",
            "latency_metrics_population": "ALL_COMPLETED",
        },
        "document": {
            "recall_at_1": mean_optional(
                evaluation["document_recall_at_1"]
                for evaluation in answerable_evaluations
            ),
            f"recall_at_{top_k}": mean_optional(
                evaluation["document_recall_at_k"]
                for evaluation in answerable_evaluations
            ),
            f"mrr_at_{top_k}": mean_optional(
                evaluation["document_mrr_at_k"]
                for evaluation in answerable_evaluations
            ),
            f"ndcg_at_{top_k}": mean_optional(
                evaluation["document_ndcg_at_k"]
                for evaluation in answerable_evaluations
            ),
        },
        "source_span": {
            "recall_at_1": mean_optional(
                evaluation["span_recall_at_1"]
                for evaluation in answerable_evaluations
            ),
            f"recall_at_{top_k}": mean_optional(
                evaluation["span_recall_at_k"]
                for evaluation in answerable_evaluations
            ),
            f"mrr_at_{top_k}": mean_optional(
                evaluation["span_mrr_at_k"]
                for evaluation in answerable_evaluations
            ),
        },
        "latency_ms": {
            "retrieval_p50": percentile(retrieval_latencies, 0.50),
            "retrieval_p95": percentile(retrieval_latencies, 0.95),
            "total_p50": percentile(total_latencies, 0.50),
            "total_p95": percentile(total_latencies, 0.95),
            "client_p50": percentile(client_latencies, 0.50),
            "client_p95": percentile(client_latencies, 0.95),
        },
    }
    if response_mode == "ANSWER":
        metrics["answerability"] = {
            "answerable_observations": len(answerable),
            "unanswerable_observations": len(unanswerable),
            "correct_refusal_rate": (
                len(correct_refusals) / len(unanswerable) if unanswerable else None
            ),
            "false_refusal_rate": (
                len(false_refusals) / len(answerable) if answerable else None
            ),
            "false_acceptance_rate": (
                len(false_acceptances) / len(unanswerable) if unanswerable else None
            ),
            "gold_citation_support_rate": (
                sum(
                    bool(record["evaluation"]["has_gold_supporting_hit"])
                    for record in answerable
                    if not record["evaluation"]["refused"]
                )
                / sum(
                    not record["evaluation"]["refused"] for record in answerable
                )
                if any(not record["evaluation"]["refused"] for record in answerable)
                else None
            ),
            "false_refusal_case_ids": sorted(
                {record["case"]["case_id"] for record in false_refusals}
            ),
            "false_acceptance_case_ids": sorted(
                {record["case"]["case_id"] for record in false_acceptances}
            ),
        }
    return metrics


def build_search_payload(args: argparse.Namespace, question: str) -> dict[str, Any]:
    return {
        "query": question,
        "retrieval_mode": args.mode,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "fusion": args.fusion,
        "rerank": args.rerank,
        "rewrite": args.rewrite,
        "multi_query": args.multi_query,
        "expand_context": args.expand_context,
        "response_mode": args.response_mode,
        "dense_weight": args.dense_weight,
        "include_query_trace": args.include_query_trace,
        "filters": {},
    }


def _limited_error_body(response: httpx.Response) -> str:
    body = response.text
    return body[:2000] + ("…" if len(body) > 2000 else "")


async def request_with_retries(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict[str, Any],
    *,
    retries: int,
) -> tuple[httpx.Response, int, float]:
    last_response: httpx.Response | None = None
    started = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            response = await client.post(endpoint, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt >= retries:
                raise
            await asyncio.sleep(min(2**attempt, 4))
            continue
        last_response = response
        if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= retries:
            break
        await asyncio.sleep(min(2**attempt, 4))
    if last_response is None:
        raise RuntimeError("request did not produce a response")
    return last_response, attempt + 1, (time.perf_counter() - started) * 1000


async def resolve_access_token(
    client: httpx.AsyncClient,
    *,
    token_env: str,
    email_env: str,
    password_env: str,
) -> str:
    token = os.environ.get(token_env)
    if token:
        return token
    email = os.environ.get(email_env)
    password = os.environ.get(password_env)
    if not email or not password:
        raise RuntimeError(
            f"set {token_env}, or set both {email_env} and {password_env}; "
            "credentials are read from environment variables and are never written to reports"
        )
    response = await client.post("/auth/login", json={"email": email, "password": password})
    if response.is_error:
        raise RuntimeError(
            f"login failed with HTTP {response.status_code}: {_limited_error_body(response)}"
        )
    payload = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("login response did not contain access_token")
    return token


def git_snapshot(project_root: Path) -> dict[str, Any]:
    def git(*arguments: str) -> str | None:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    status = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(status) if status is not None else None,
    }


def select_cases(
    cases: Sequence[dict[str, Any]],
    case_ids: set[str],
    limit: int | None,
) -> list[dict[str, Any]]:
    selected = [
        case for case in cases if not case_ids or str(case.get("case_id")) in case_ids
    ]
    if case_ids:
        found = {str(case.get("case_id")) for case in selected}
        missing = case_ids - found
        if missing:
            raise ValueError(f"unknown case ids: {', '.join(sorted(missing))}")
    return selected[:limit] if limit is not None else selected


def is_failure(record: dict[str, Any], response_mode: str) -> bool:
    if record.get("status") != "completed":
        return True
    case = record["case"]
    evaluation = record["evaluation"]
    if case["answerable"] and evaluation["document_recall_at_k"] == 0:
        return True
    if response_mode == "ANSWER":
        return bool(case["expected_refusal"]) != bool(evaluation["refused"])
    return False


async def run(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parents[1]
    dataset = args.dataset.resolve()
    manifest_path = dataset / "manifest.json"
    cases_path = dataset / "cases.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    corpus_path = (
        dataset / str(manifest.get("corpus_path", "corpus"))
    ).resolve()
    cases = select_cases(
        load_jsonl(cases_path),
        set(args.case_id),
        args.limit,
    )
    if not cases:
        raise ValueError("no evaluation cases selected")
    expected_cases_hash = str(manifest.get("cases_snapshot", "")).removeprefix("sha256:")
    actual_cases_hash = file_sha256(cases_path)
    if expected_cases_hash and expected_cases_hash != actual_cases_hash:
        raise ValueError(
            "cases.jsonl does not match manifest cases_snapshot; version the changed dataset first"
        )
    expected_corpus_hash = str(manifest.get("corpus_snapshot", "")).removeprefix(
        "sha256:"
    )
    actual_corpus_hash = directory_snapshot(corpus_path)
    if expected_corpus_hash and expected_corpus_hash != actual_corpus_hash:
        raise ValueError(
            "corpus does not match manifest corpus_snapshot; "
            "version the changed dataset first"
        )
    workspace_id = args.workspace_id or os.environ.get(args.workspace_id_env)
    if not workspace_id:
        raise RuntimeError(
            f"pass --workspace-id or set {args.workspace_id_env}"
        )
    payload_preview = build_search_payload(args, str(cases[0]["question"]))
    if args.dry_run:
        print(
            json.dumps(
                {
                    "base_url": args.base_url,
                    "workspace_id": workspace_id,
                    "selected_case_count": len(cases),
                    "repeat": args.repeat,
                    "first_payload": payload_preview,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"output directory is not empty: {output}; use a new versioned report directory"
        )
    output.mkdir(parents=True, exist_ok=True)
    run_metadata: dict[str, Any] = {
        "schema_version": 1,
        "run_started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "base_url": args.base_url,
        "workspace_id": workspace_id,
        "dataset": {
            "path": str(dataset),
            "version": manifest.get("dataset_version"),
            "manifest": manifest,
            "manifest_sha256": file_sha256(manifest_path),
            "cases_sha256": actual_cases_hash,
            "corpus_snapshot_actual": actual_corpus_hash,
        },
        "git": git_snapshot(project_root),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "config": {
            key: payload_preview[key]
            for key in payload_preview
            if key != "query"
        },
        "selected_case_ids": [case["case_id"] for case in cases],
        "repeat": args.repeat,
    }
    run_path = output / "run.json"
    run_path.write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    records: list[dict[str, Any]] = []
    results_path = output / "results.jsonl"
    timeout = httpx.Timeout(args.timeout_seconds)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
        headers={"Accept": "application/json"},
    ) as auth_client:
        token = await resolve_access_token(
            auth_client,
            token_env=args.token_env,
            email_env=args.email_env,
            password_env=args.password_env,
        )
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    ) as client:
        with results_path.open("a", encoding="utf-8", newline="\n") as result_file:
            for repetition in range(1, args.repeat + 1):
                for position, case in enumerate(cases, start=1):
                    payload = build_search_payload(args, str(case["question"]))
                    record: dict[str, Any] = {
                        "schema_version": 1,
                        "run_observation": {
                            "repetition": repetition,
                            "position": position,
                        },
                        "case": {
                            "case_id": case["case_id"],
                            "question": case["question"],
                            "answerable": bool(case["answerable"]),
                            "expected_refusal": bool(case["expected_refusal"]),
                            "qrels": case.get("qrels", []),
                            "gold_sources": case.get("gold_sources", []),
                            "tags": case.get("tags", []),
                        },
                        "request": payload,
                    }
                    try:
                        response, attempts, elapsed_ms = await request_with_retries(
                            client,
                            f"/workspaces/{workspace_id}/search",
                            payload,
                            retries=args.retries,
                        )
                        record["client_elapsed_ms"] = elapsed_ms
                        record["http"] = {
                            "status_code": response.status_code,
                            "attempts": attempts,
                            "request_id": response.headers.get("x-request-id"),
                        }
                        if response.is_error:
                            record["status"] = "failed"
                            record["error"] = {
                                "type": "http_error",
                                "body": _limited_error_body(response),
                            }
                        else:
                            response_payload = response.json()
                            record["status"] = "completed"
                            record["response"] = response_payload
                            record["evaluation"] = evaluate_response(
                                case,
                                response_payload,
                                top_k=args.top_k,
                            )
                    except (httpx.HTTPError, ValueError) as exc:
                        record["status"] = "failed"
                        record["client_elapsed_ms"] = None
                        record["error"] = {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        }
                    records.append(record)
                    result_file.write(
                        json.dumps(record, ensure_ascii=False) + "\n"
                    )
                    result_file.flush()
                    print(
                        (
                            f"[{repetition}/{args.repeat}] "
                            f"[{position}/{len(cases)}] {case['case_id']}: "
                            f"{record['status']}"
                        ),
                        file=sys.stderr,
                    )
                    if args.fail_fast and record["status"] != "completed":
                        raise RuntimeError(f"evaluation failed at {case['case_id']}")
                    if args.pause_ms:
                        await asyncio.sleep(args.pause_ms / 1000)

    metrics = compute_metrics(
        records,
        top_k=args.top_k,
        response_mode=args.response_mode,
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = [
        record for record in records if is_failure(record, args.response_mode)
    ]
    (output / "failures.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in failures),
        encoding="utf-8",
    )
    run_metadata["status"] = (
        "completed" if metrics["failed_count"] == 0 else "completed_with_errors"
    )
    run_metadata["run_finished_at"] = datetime.now(UTC).isoformat()
    run_metadata["metrics_file"] = "metrics.json"
    run_metadata["results_file"] = "results.jsonl"
    run_metadata["failures_file"] = "failures.jsonl"
    run_path.write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["failed_count"] == 0 else 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run versioned RAG evaluation through the live FastAPI REST endpoint"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/api/v1",
        help="FastAPI v1 base URL",
    )
    parser.add_argument("--workspace-id")
    parser.add_argument(
        "--workspace-id-env",
        default="RAG_EVAL_WORKSPACE_ID",
        help="environment variable used when --workspace-id is omitted",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=project_root / "eval" / "dev-v1",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=["DENSE", "SPARSE", "HYBRID"], default="HYBRID")
    parser.add_argument("--fusion", choices=["RRF", "WEIGHTED"], default="RRF")
    parser.add_argument("--rerank", choices=["OFF", "AUTO", "ON"], default="OFF")
    parser.add_argument("--rewrite", choices=["OFF", "AUTO", "ON"], default="OFF")
    parser.add_argument("--multi-query", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--expand-context",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--response-mode", choices=["HITS", "ANSWER"], default="HITS")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--dense-weight", type=float, default=0.65)
    parser.add_argument(
        "--include-query-trace",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--pause-ms", type=int, default=0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--token-env", default="RAG_EVAL_ACCESS_TOKEN")
    parser.add_argument("--email-env", default="RAG_EVAL_EMAIL")
    parser.add_argument("--password-env", default="RAG_EVAL_PASSWORD")
    args = parser.parse_args(argv)
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is used")
    if args.top_k < 1 or args.top_k > 20:
        parser.error("--top-k must be between 1 and 20")
    if args.candidate_k < args.top_k or args.candidate_k > 100:
        parser.error("--candidate-k must be between top-k and 100")
    if not 0 <= args.dense_weight <= 1:
        parser.error("--dense-weight must be between 0 and 1")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    return args


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
