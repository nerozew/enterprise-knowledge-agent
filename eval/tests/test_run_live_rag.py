from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_runner() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "run_live_rag.py"
    spec = importlib.util.spec_from_file_location("run_live_rag", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load live evaluation runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def search_response(*, refusal: bool = False) -> dict[str, object]:
    return {
        "query": "公司电脑丢失后怎么办？",
        "response_mode": "ANSWER",
        "answer": None if refusal else "30 分钟内通知 IT 服务台。",
        "refusal": {"code": "model_refused", "message": "拒答"} if refusal else None,
        "hits": [
            {
                "chunk_id": "chunk-1",
                "title": "remote-work",
                "quote": "设备遗失后，员工应在 30 分钟内通知 IT 服务台。",
                "source_span_id": "span-1",
            },
            {
                "chunk_id": "chunk-2",
                "title": "security-access",
                "quote": "普通生产权限最长有效 30 天。",
                "source_span_id": "span-2",
            },
        ],
        "answerability": {
            "status": "REJECTED" if refusal else "SUPPORTED",
            "supporting_hit_ids": [] if refusal else ["chunk-1"],
        },
        "timings_ms": {"retrieval": 100.0, "total": 300.0},
    }


def case(*, answerable: bool = True) -> dict[str, object]:
    return {
        "case_id": "dev-020" if answerable else "dev-021",
        "question": "公司电脑丢失后怎么办？",
        "answerable": answerable,
        "expected_refusal": not answerable,
        "qrels": (
            [{"document_key": "remote-work", "relevance": 3}]
            if answerable
            else []
        ),
        "gold_sources": (
            [
                {
                    "document_key": "remote-work",
                    "quote": "员工应在 30 分钟内通知 IT 服务台",
                }
            ]
            if answerable
            else []
        ),
    }


def test_evaluate_response_matches_document_span_and_supporting_citation() -> None:
    evaluation = runner.evaluate_response(case(), search_response(), top_k=5)

    assert evaluation["document_recall_at_1"] == 1.0
    assert evaluation["document_recall_at_k"] == 1.0
    assert evaluation["document_mrr_at_k"] == 1.0
    assert evaluation["span_recall_at_1"] == 1.0
    assert evaluation["span_mrr_at_k"] == 1.0
    assert evaluation["has_gold_supporting_hit"] is True


def test_compute_metrics_reports_answerability_failures_and_latency() -> None:
    accepted_unanswerable = search_response(refusal=False)
    records = [
        {
            "status": "completed",
            "case": case(),
            "response": search_response(),
            "evaluation": runner.evaluate_response(
                case(), search_response(), top_k=5
            ),
            "client_elapsed_ms": 320.0,
        },
        {
            "status": "completed",
            "case": case(answerable=False),
            "response": accepted_unanswerable,
            "evaluation": runner.evaluate_response(
                case(answerable=False), accepted_unanswerable, top_k=5
            ),
            "client_elapsed_ms": 340.0,
        },
    ]

    metrics = runner.compute_metrics(records, top_k=5, response_mode="ANSWER")

    assert metrics["schema_version"] == 2
    assert metrics["completed_count"] == 2
    assert metrics["observations"] == {
        "answerable": 1,
        "unanswerable": 1,
        "retrieval_metrics_population": "ANSWERABLE_ONLY",
        "latency_metrics_population": "ALL_COMPLETED",
    }
    assert metrics["document"]["recall_at_1"] == 1.0
    assert metrics["latency_ms"]["retrieval_p50"] == 100.0
    assert metrics["answerability"]["false_refusal_rate"] == 0.0
    assert metrics["answerability"]["false_acceptance_rate"] == 1.0
    assert metrics["answerability"]["false_acceptance_case_ids"] == ["dev-021"]


def test_parse_args_supports_secret_free_dry_run() -> None:
    args = runner.parse_args(
        [
            "--dry-run",
            "--workspace-id",
            "workspace-1",
            "--mode",
            "DENSE",
            "--no-expand-context",
        ]
    )

    assert args.output is None
    assert args.workspace_id == "workspace-1"
    assert args.mode == "DENSE"
    assert args.expand_context is False
