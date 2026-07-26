from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.runner import _gold_span_ids, evaluate
from app.knowledge.evidence import EvidenceGate, EvidenceGateConfig
from app.knowledge.retrieval import FusionMode, RetrievalMode
from app.knowledge.types import ChunkKind, KnowledgeChunk, ScoredHit


def _hit(chunk_id: str, text: str, score: float) -> ScoredHit:
    chunk = KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="doc",
        document_version_id="version",
        kind=ChunkKind.CHILD,
        ordinal=1,
        text=text,
        page=1,
        heading_path=(),
        char_start=0,
        char_end=len(text),
        source_span_id=f"span-{chunk_id}",
    )
    return ScoredHit(chunk, score, {"total": score})


def test_evidence_gate_requires_traceability_score_and_query_coverage() -> None:
    gate = EvidenceGate(EvidenceGateConfig(min_top_score=0.1, min_lexical_coverage=0.3))
    accepted = gate.evaluate(
        "权限保留多久",
        (_hit("a", "普通生产权限最长保留九十天", 0.8),),
    )
    rejected = gate.evaluate(
        "春节放假日期",
        (_hit("b", "普通生产权限最长保留九十天", 0.8),),
    )
    assert accepted.accepted is True
    assert rejected.accepted is False
    assert rejected.code == "lexical_coverage_below_threshold"


def test_gold_source_span_resolution_is_explicit() -> None:
    chunk = _hit("a", "权限最长保留九十天", 1.0).chunk
    document_keys = {"doc": "security-access"}
    assert _gold_span_ids({}, [chunk], document_keys) == (None, 0)
    resolved, unmapped = _gold_span_ids(
        {
            "gold_sources": [
                {"document_key": "security-access", "quote": "保留九十天"},
                {"document_key": "missing", "quote": "不存在"},
            ]
        },
        [chunk],
        document_keys,
    )
    assert resolved == {"span-a"}
    assert unmapped == 1


@pytest.mark.asyncio
async def test_dev_v1_refusal_recall_and_span_metric(tmp_path: Path) -> None:
    dataset = Path(__file__).resolve().parents[2] / "eval" / "dev-v1"
    metrics = await evaluate(
        dataset,
        tmp_path / "report",
        mode=RetrievalMode.HYBRID,
        fusion=FusionMode.RRF,
        rerank=True,
        top_k=5,
    )
    assert metrics["case_count"] == 24
    assert metrics["refusal"]["recall"] == 1.0
    assert "false_refusal_case_ids" in metrics["refusal"]
    span_metrics = metrics["source_span_retrieval"]
    assert span_metrics["status"] == "available"
    assert span_metrics["annotated_case_count"] == 20
    assert span_metrics["unmapped_gold_source_count"] == 0
    assert 0 <= span_metrics["recall_at_5"] <= 1


@pytest.mark.asyncio
async def test_runner_marks_span_metrics_unavailable_without_gold_sources(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    corpus = dataset / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "policy.md").write_text(
        "# 权限\n\n普通权限保留三十天。",
        encoding="utf-8",
    )
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_version": "no-span-v1",
                "corpus_snapshot": "sha256:test",
                "allowed_claim": "development-regression-only",
            }
        ),
        encoding="utf-8",
    )
    (dataset / "cases.jsonl").write_text(
        json.dumps(
            {
                "case_id": "one",
                "question": "普通权限保留多久",
                "answerable": True,
                "expected_refusal": False,
                "qrels": [{"document_key": "policy", "relevance": 3}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    metrics = await evaluate(
        dataset,
        tmp_path / "report-no-span",
        mode=RetrievalMode.HYBRID,
        fusion=FusionMode.RRF,
        rerank=True,
        top_k=1,
    )
    assert metrics["source_span_retrieval"]["status"] == "unavailable"
    assert metrics["source_span_retrieval"]["reason"] == "dataset_has_no_gold_sources"
