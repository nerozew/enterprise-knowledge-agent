from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.knowledge.chunking import ParentChildChunker
from app.knowledge.evidence import EvidenceGate, EvidenceGateConfig
from app.knowledge.metrics import (
    mean_metric,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
)
from app.knowledge.parsers import DocumentParser
from app.knowledge.providers import FixtureEmbeddingProvider, LexicalRerankProvider
from app.knowledge.retrieval import (
    FusionMode,
    HybridRetriever,
    RetrievalConfig,
    RetrievalMode,
)
from app.knowledge.types import ChunkKind, IndexedChunk, KnowledgeChunk


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def evaluate(
    dataset: Path,
    output: Path,
    *,
    mode: RetrievalMode,
    fusion: FusionMode,
    rerank: bool,
    top_k: int,
) -> dict[str, Any]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("allowed_claim") != "development-regression-only":
        raise ValueError("development runner requires development-regression-only dataset")
    cases = _jsonl(dataset / "cases.jsonl")
    parser = DocumentParser()
    chunker = ParentChildChunker()
    embedding = FixtureEmbeddingProvider(seed=manifest["dataset_version"])
    chunks: list[KnowledgeChunk] = []
    document_keys: dict[str, str] = {}
    for path in sorted((dataset / "corpus").glob("*.md")):
        document_id = str(uuid5(NAMESPACE_URL, f"{manifest['dataset_version']}:{path.stem}"))
        version_id = str(uuid5(NAMESPACE_URL, f"{document_id}:{manifest['corpus_snapshot']}"))
        document_keys[document_id] = path.stem
        parsed = parser.parse(
            filename=path.name,
            content=path.read_bytes(),
            mime_type="text/markdown",
        )
        chunks.extend(
            chunker.chunk(
                parsed,
                document_id=document_id,
                document_version_id=version_id,
            )
        )
    vectors = await embedding.embed_documents([chunk.text for chunk in chunks])
    indexed = [
        IndexedChunk(
            chunk=chunk,
            workspace_id="corp-demo-v1",
            vector=tuple(vector),
            active=True,
            metadata={"document_key": document_keys[chunk.document_id]},
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    retriever = HybridRetriever(
        chunks=indexed,
        embedding=embedding,
        reranker=LexicalRerankProvider() if rerank else None,
    )
    gate = EvidenceGate(EvidenceGateConfig(**manifest.get("evidence_gate", {})))
    results: list[dict[str, Any]] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    refusal_tp = refusal_fp = refusal_fn = 0
    false_refusal_case_ids: list[str] = []
    span_recalls: list[float] = []
    span_reciprocal_ranks: list[float] = []
    span_ndcgs: list[float] = []
    span_annotated_case_count = 0
    span_unmapped_source_count = 0
    for case in cases:
        started = time.perf_counter()
        result = await retriever.search(
            case["question"],
            RetrievalConfig(
                mode=mode,
                fusion=fusion,
                top_k=top_k,
                candidate_k=max(30, top_k),
                rerank=rerank,
                expand_context=True,
            ),
        )
        latency = (time.perf_counter() - started) * 1000
        latencies.append(latency)
        ranked_documents: list[str] = []
        for hit in result.hits:
            key = document_keys[hit.chunk.document_id]
            if key not in ranked_documents:
                ranked_documents.append(key)
        qrels = {item["document_key"]: float(item["relevance"]) for item in case.get("qrels", [])}
        relevant = set(qrels)
        evidence = gate.evaluate(case["question"], result.hits)
        refused = not evidence.accepted
        expected_refusal = bool(case.get("expected_refusal", not case.get("answerable", True)))
        if expected_refusal and refused:
            refusal_tp += 1
        elif not expected_refusal and refused:
            refusal_fp += 1
            false_refusal_case_ids.append(case["case_id"])
        elif expected_refusal and not refused:
            refusal_fn += 1
        if relevant:
            recalls.append(recall_at_k(ranked_documents, relevant, 5))
            reciprocal_ranks.append(reciprocal_rank_at_k(ranked_documents, relevant, 10))
            ndcgs.append(ndcg_at_k(ranked_documents, qrels, 10))
        gold_spans, unmapped_count = _gold_span_ids(case, chunks, document_keys)
        if gold_spans is not None:
            span_annotated_case_count += 1
            span_unmapped_source_count += unmapped_count
            ranked_spans = [hit.chunk.source_span_id for hit in result.hits]
            span_recalls.append(recall_at_k(ranked_spans, gold_spans, 5))
            span_reciprocal_ranks.append(reciprocal_rank_at_k(ranked_spans, gold_spans, 10))
            span_ndcgs.append(ndcg_at_k(ranked_spans, gold_spans, 10))
        results.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "answerable": case["answerable"],
                "expected_refusal": expected_refusal,
                "refused": refused,
                "evidence_gate": {
                    "code": evidence.code,
                    "top_score": evidence.top_score,
                    "lexical_coverage": evidence.lexical_coverage,
                    "usable_hit_ids": evidence.usable_hit_ids,
                },
                "ranked_document_keys": ranked_documents,
                "hits": [
                    {
                        "chunk_id": hit.chunk.chunk_id,
                        "document_key": document_keys[hit.chunk.document_id],
                        "document_version_id": hit.chunk.document_version_id,
                        "source_span_id": hit.chunk.source_span_id,
                        "score": hit.score,
                        "scores": hit.scores,
                    }
                    for hit in result.hits
                ],
                "latency_ms": latency,
            }
        )
    precision_denominator = refusal_tp + refusal_fp
    recall_denominator = refusal_tp + refusal_fn
    refusal_precision = refusal_tp / precision_denominator if precision_denominator else 1.0
    refusal_recall = refusal_tp / recall_denominator if recall_denominator else 1.0
    refusal_f1 = (
        2 * refusal_precision * refusal_recall / (refusal_precision + refusal_recall)
        if refusal_precision + refusal_recall
        else 0.0
    )
    child_ids = [chunk.chunk_id for chunk in chunks if chunk.kind is ChunkKind.CHILD]
    metrics = {
        "allowed_claim": "development-regression-only",
        "provider_mode": "FIXTURE",
        "retrieval": {
            "recall_at_5": mean_metric(recalls),
            "mrr_at_10": mean_metric(reciprocal_ranks),
            "ndcg_at_10": mean_metric(ndcgs),
        },
        "refusal": {
            "precision": refusal_precision,
            "recall": refusal_recall,
            "f1": refusal_f1,
            "false_refusal_count": len(false_refusal_case_ids),
            "false_refusal_case_ids": false_refusal_case_ids,
        },
        "source_span_retrieval": (
            {
                "status": "available",
                "annotated_case_count": span_annotated_case_count,
                "unmapped_gold_source_count": span_unmapped_source_count,
                "recall_at_5": mean_metric(span_recalls),
                "mrr_at_10": mean_metric(span_reciprocal_ranks),
                "ndcg_at_10": mean_metric(span_ndcgs),
            }
            if span_annotated_case_count
            else {
                "status": "unavailable",
                "reason": "dataset_has_no_gold_sources",
                "annotated_case_count": 0,
                "unmapped_gold_source_count": 0,
                "recall_at_5": None,
                "mrr_at_10": None,
                "ndcg_at_10": None,
            }
        ),
        "integrity": {
            "duplicate_chunk_id_count": len(child_ids) - len(set(child_ids)),
            "citation_span_completeness": (
                sum(
                    bool(hit["document_version_id"] and hit["source_span_id"])
                    for item in results
                    for hit in item["hits"]
                )
                / max(sum(len(item["hits"]) for item in results), 1)
            ),
        },
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "p95": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0,
        },
        "case_count": len(cases),
    }
    output.mkdir(parents=True, exist_ok=True)
    run_manifest = {
        **manifest,
        "provider_mode": "FIXTURE",
        "allowed_claim": "development-regression-only",
        "retrieval_mode": mode.value,
        "fusion": fusion.value,
        "rerank": rerank,
        "top_k": top_k,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "evaluation_manifest.json").write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "retrieval_results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results),
        encoding="utf-8",
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failures = [
        item
        for item in results
        if item["expected_refusal"] != item["refused"]
        or (item["answerable"] and not item["ranked_document_keys"])
    ]
    (output / "failures.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in failures),
        encoding="utf-8",
    )
    return metrics


def _gold_span_ids(
    case: dict[str, Any],
    chunks: list[KnowledgeChunk],
    document_keys: dict[str, str],
) -> tuple[set[str] | None, int]:
    """Resolve explicit gold source annotations without deriving them from qrels."""
    sources = case.get("gold_sources")
    if sources is None:
        return None, 0
    if not isinstance(sources, list):
        raise ValueError("gold_sources must be a list")
    child_chunks = [chunk for chunk in chunks if chunk.kind is ChunkKind.CHILD]
    resolved: set[str] = set()
    unmapped = 0
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each gold source must be an object")
        direct = source.get("source_span_id")
        direct_many = source.get("source_span_ids", [])
        if isinstance(direct, str) and direct:
            resolved.add(direct)
            continue
        if isinstance(direct_many, list) and all(isinstance(item, str) for item in direct_many):
            if direct_many:
                resolved.update(direct_many)
                continue
        document_key = source.get("document_key")
        candidates = [
            chunk for chunk in child_chunks if document_keys.get(chunk.document_id) == document_key
        ]
        quote = source.get("quote")
        if isinstance(quote, str) and quote.strip():
            matches = [
                chunk.source_span_id
                for chunk in candidates
                if _normalize_text(quote) in _normalize_text(chunk.text)
            ]
        elif isinstance(source.get("char_start"), int) and isinstance(source.get("char_end"), int):
            start = int(source["char_start"])
            end = int(source["char_end"])
            matches = [
                chunk.source_span_id
                for chunk in candidates
                if max(start, chunk.char_start) < min(end, chunk.char_end)
            ]
        else:
            matches = []
        if matches:
            resolved.update(matches)
        else:
            unmapped += 1
    return resolved, unmapped


def _normalize_text(value: str) -> str:
    return "".join(value.casefold().split())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic P4 development retrieval evaluation"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=[item.value for item in RetrievalMode], default="HYBRID")
    parser.add_argument("--fusion", choices=[item.value for item in FusionMode], default="RRF")
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    metrics = asyncio.run(
        evaluate(
            args.dataset,
            args.output,
            mode=RetrievalMode(args.mode),
            fusion=FusionMode(args.fusion),
            rerank=args.rerank,
            top_k=args.top_k,
        )
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
