"""Run the frozen unstructured-v1 parser/OCR control corpus.

The runner writes both per-case observations and aggregate metrics.  It never
turns a provider error into a passing negative case: only deterministic
``KnowledgeError`` failures satisfy a fixture whose expected outcome is
``failure``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.knowledge.errors import KnowledgeError, ProviderUnavailableError  # noqa: E402
from app.knowledge.chunking import ParentChildChunker  # noqa: E402
from app.knowledge.parsers import DoclingParserProvider  # noqa: E402
from app.knowledge.types import ChunkKind, OCRMode  # noqa: E402


MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "markdown": "text/markdown",
    "txt": "text/plain",
}


@dataclass(slots=True)
class CaseResult:
    case_id: str
    expected: str
    status: str
    passed: bool
    duration_ms: float
    parser_profile: str | None = None
    ocr_profile: str | None = None
    block_count: int | None = None
    page_count: int | None = None
    slide_count: int | None = None
    ocr_page_count: int | None = None
    phrase_matches: dict[str, bool] | None = None
    reading_order_valid: bool | None = None
    block_locations_valid: bool | None = None
    block_kinds: list[str] | None = None
    block_types_valid: bool | None = None
    parent_child_complete: bool | None = None
    source_spans_resolvable: bool | None = None
    deterministic_chunk_ids: bool | None = None
    cer: float | None = None
    warnings: list[str] | None = None
    error_type: str | None = None
    error_message: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).casefold()


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_character in enumerate(left, start=1):
        current = [row]
        for column, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, observed: str) -> float:
    expected = normalize_text(reference)
    actual = normalize_text(observed)
    if not expected:
        return 0.0 if not actual else 1.0
    return edit_distance(expected, actual) / len(expected)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def reading_order_valid(blocks: tuple[Any, ...]) -> bool:
    grouped: dict[tuple[int | None, int | None], list[int]] = {}
    for block in blocks:
        grouped.setdefault((block.page_no, block.slide_no), []).append(
            block.reading_order
        )
    return all(
        values == sorted(values) and len(values) == len(set(values))
        for values in grouped.values()
    )


def block_locations_valid(blocks: tuple[Any, ...], format_name: str) -> bool:
    for block in blocks:
        if format_name == "pdf" and block.page_no is None:
            return False
        if format_name == "pptx" and block.slide_no is None:
            return False
        if block.bbox is not None:
            coordinates = (
                block.bbox.left,
                block.bbox.top,
                block.bbox.right,
                block.bbox.bottom,
            )
            if any(value < 0.0 or value > 1.0 for value in coordinates):
                return False
            if block.bbox.left > block.bbox.right or block.bbox.top > block.bbox.bottom:
                return False
    return True


def chunk_integrity(layout: Any, case_id: str) -> tuple[bool, bool, bool]:
    """Exercise the real parent-child chunker without requiring external stores."""

    document_id = f"eval-document-{case_id}"
    version_id = f"eval-version-{case_id}"
    parsed = layout.to_parsed_document()
    chunker = ParentChildChunker()
    first = chunker.chunk(
        parsed,
        document_id=document_id,
        document_version_id=version_id,
    )
    second = chunker.chunk(
        parsed,
        document_id=document_id,
        document_version_id=version_id,
    )
    parent_ids = {chunk.chunk_id for chunk in first if chunk.kind is ChunkKind.PARENT}
    children = [chunk for chunk in first if chunk.kind is ChunkKind.CHILD]
    parent_child_complete = bool(children) and all(
        child.parent_id in parent_ids for child in children
    )
    source_spans_resolvable = bool(children) and all(
        any(
            block.char_start < child.char_end and block.char_end > child.char_start
            for block in parsed.blocks
        )
        for child in children
    )
    deterministic_ids = [chunk.chunk_id for chunk in first] == [
        chunk.chunk_id for chunk in second
    ] and [chunk.source_span_id for chunk in first] == [
        chunk.source_span_id for chunk in second
    ]
    return parent_child_complete, source_spans_resolvable, deterministic_ids


def run_case(provider: DoclingParserProvider, case: dict[str, Any]) -> CaseResult:
    source = ROOT / "corpus" / case["file"]
    started = time.perf_counter()
    if sha256(source) != case["sha256"]:
        return CaseResult(
            case_id=case["case_id"],
            expected=case["expect"],
            status="HASH_MISMATCH",
            passed=False,
            duration_ms=0.0,
            error_message="fixture SHA256 does not match cases.jsonl",
        )
    try:
        layout = provider.parse_layout(
            filename=source.name,
            content=source.read_bytes(),
            mime_type=MIME_TYPES[case["format"]],
            ocr_mode=OCRMode(case["ocr_mode"]),
        )
    except ProviderUnavailableError as exc:
        return CaseResult(
            case_id=case["case_id"],
            expected=case["expect"],
            status="PROVIDER_UNAVAILABLE",
            passed=False,
            duration_ms=(time.perf_counter() - started) * 1_000,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    except KnowledgeError as exc:
        expected_failure = case["expect"] == "failure"
        return CaseResult(
            case_id=case["case_id"],
            expected=case["expect"],
            status="EXPECTED_REJECTION" if expected_failure else "PARSER_ERROR",
            passed=expected_failure,
            duration_ms=(time.perf_counter() - started) * 1_000,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    except Exception as exc:  # A crash is never an accepted negative result.
        return CaseResult(
            case_id=case["case_id"],
            expected=case["expect"],
            status="UNEXPECTED_EXCEPTION",
            passed=False,
            duration_ms=(time.perf_counter() - started) * 1_000,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    duration_ms = (time.perf_counter() - started) * 1_000
    phrases = {
        phrase: normalize_text(phrase) in normalize_text(layout.text)
        for phrase in case.get("phrases", [])
    }
    expected_counts_match = (
        ("page_count" not in case or layout.page_count == case["page_count"])
        and ("slide_count" not in case or layout.slide_count == case["slide_count"])
        and (
            "ocr_page_count" not in case
            or layout.ocr_page_count == case["ocr_page_count"]
        )
    )
    order_valid = reading_order_valid(layout.blocks)
    locations_valid = block_locations_valid(layout.blocks, case["format"])
    block_kinds = sorted({block.kind.value for block in layout.blocks})
    required_block_kinds = set(case.get("required_block_kinds", []))
    block_types_valid = required_block_kinds.issubset(block_kinds)
    parent_child_complete, spans_resolvable, deterministic_ids = chunk_integrity(
        layout, case["case_id"]
    )
    cer = (
        character_error_rate(case["reference_text"], layout.text)
        if "reference_text" in case
        else None
    )
    passed = (
        case["expect"] == "success"
        and bool(layout.blocks)
        and all(phrases.values())
        and expected_counts_match
        and order_valid
        and locations_valid
        and block_types_valid
        and parent_child_complete
        and spans_resolvable
        and deterministic_ids
        and (cer is None or cer <= 0.08)
    )
    return CaseResult(
        case_id=case["case_id"],
        expected=case["expect"],
        status="COMPLETED",
        passed=passed,
        duration_ms=duration_ms,
        parser_profile=layout.parser_profile,
        ocr_profile=layout.ocr_profile,
        block_count=len(layout.blocks),
        page_count=layout.page_count,
        slide_count=layout.slide_count,
        ocr_page_count=layout.ocr_page_count,
        phrase_matches=phrases,
        reading_order_valid=order_valid,
        block_locations_valid=locations_valid,
        block_kinds=block_kinds,
        block_types_valid=block_types_valid,
        parent_child_complete=parent_child_complete,
        source_spans_resolvable=spans_resolvable,
        deterministic_chunk_ids=deterministic_ids,
        cer=cer,
        warnings=list(layout.warnings),
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    successful = [result for result in results if result.expected == "success"]
    negatives = [result for result in results if result.expected == "failure"]
    ocr_results = [result for result in successful if result.cer is not None]
    durations = [result.duration_ms for result in results]

    def ratio(values: list[bool]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "case_count": len(results),
        "passed_count": sum(result.passed for result in results),
        "failed_count": sum(not result.passed for result in results),
        "supported_format_success_rate": ratio(
            [result.passed for result in successful]
        ),
        "unsafe_fixture_rejection_rate": ratio([result.passed for result in negatives]),
        "reading_order_valid_rate": ratio(
            [result.reading_order_valid is True for result in successful]
        ),
        "block_location_valid_rate": ratio(
            [result.block_locations_valid is True for result in successful]
        ),
        "block_type_accuracy": ratio(
            [result.block_types_valid is True for result in successful]
        ),
        "parent_child_complete_rate": ratio(
            [result.parent_child_complete is True for result in successful]
        ),
        "source_span_resolvable_rate": ratio(
            [result.source_spans_resolvable is True for result in successful]
        ),
        "deterministic_chunk_id_rate": ratio(
            [result.deterministic_chunk_ids is True for result in successful]
        ),
        "ocr_cer_mean": (
            sum(result.cer for result in ocr_results if result.cer is not None)
            / len(ocr_results)
            if ocr_results
            else None
        ),
        "ocr_cer_max": max(
            (result.cer for result in ocr_results if result.cer is not None),
            default=None,
        ),
        "latency_ms": {
            "p50": median(durations) if durations else None,
            "p95": percentile(durations, 0.95),
        },
        "failed_case_ids": [result.case_id for result in results if not result.passed],
    }


def main() -> int:
    arguments = parse_args()
    cases = [
        json.loads(line)
        for line in (ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if arguments.case_id:
        selected = set(arguments.case_id)
        cases = [case for case in cases if case["case_id"] in selected]
        missing = selected.difference(case["case_id"] for case in cases)
        if missing:
            raise SystemExit(f"unknown case ids: {', '.join(sorted(missing))}")

    provider = DoclingParserProvider()
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        result = run_case(provider, case)
        results.append(result)
        print(
            f"[{index}/{len(cases)}] {result.case_id}: {result.status} ({result.duration_ms:.1f} ms)"
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (
        arguments.output
        or REPOSITORY_ROOT / "eval" / "reports" / "unstructured-v1" / timestamp
    )
    output.mkdir(parents=True, exist_ok=True)
    metrics = aggregate(results)
    report = {
        "schema_version": 1,
        "dataset_version": "unstructured-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "command": " ".join(sys.argv),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "packages": {
                "docling": package_version("docling"),
                "rapidocr": package_version("rapidocr"),
                "onnxruntime": package_version("onnxruntime"),
                "python-pptx": package_version("python-pptx"),
            },
        },
        "metrics": metrics,
        "cases": [asdict(result) for result in results],
    }
    (output / "raw-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"report_dir: {output}")
    return 0 if metrics["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
