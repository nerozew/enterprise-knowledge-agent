from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="校验 RAG 评测数据集的结构、标注和语料引用。"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--cases-name", default="cases.draft.jsonl")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--require-approved", action="store_true")
    parser.add_argument("--write-summary", type=Path)
    return parser.parse_args(argv)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 必须是 JSON object")
    return data


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: JSON 无效: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number}: 每行必须是 JSON object")
        cases.append(item)
    return cases


def parse_document_key(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    matched = re.search(r"(?m)^document_key:\s*(\S+)\s*$", text)
    if matched is None:
        raise ValueError(f"{path}: 缺少 document_key frontmatter")
    return matched.group(1)


def resolve_corpus_dir(dataset: Path, manifest: dict[str, Any]) -> Path:
    configured = manifest.get("corpus_path", "corpus")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError("manifest.corpus_path 必须是非空字符串")
    return (dataset / configured).resolve()


def validate(
    *,
    dataset: Path,
    cases_name: str,
    expected_count: int | None,
    require_approved: bool,
) -> tuple[dict[str, Any], list[str]]:
    manifest_path = dataset / "manifest.json"
    cases_path = dataset / cases_name
    errors: list[str] = []

    if not manifest_path.is_file():
        return {}, [f"缺少 {manifest_path}"]
    if not cases_path.is_file():
        return {}, [f"缺少 {cases_path}"]

    manifest = load_json(manifest_path)
    cases = load_jsonl(cases_path)
    dataset_version = manifest.get("dataset_version")
    corpus_dir = resolve_corpus_dir(dataset, manifest)
    if not corpus_dir.is_dir():
        return {}, [f"语料目录不存在: {corpus_dir}"]

    corpus: dict[str, str] = {}
    for path in sorted(corpus_dir.glob("*.md")):
        key = parse_document_key(path)
        if key in corpus:
            errors.append(f"语料 document_key 重复: {key}")
        corpus[key] = path.read_text(encoding="utf-8")

    if expected_count is not None and len(cases) != expected_count:
        errors.append(f"题数应为 {expected_count}，实际为 {len(cases)}")

    ids = Counter(str(item.get("case_id", "")) for item in cases)
    questions = Counter(normalize_text(str(item.get("question", ""))) for item in cases)
    for case_id, count in ids.items():
        if not case_id:
            errors.append("存在空 case_id")
        elif count > 1:
            errors.append(f"case_id 重复: {case_id}")
    for normalized, count in questions.items():
        if not normalized:
            errors.append("存在空 question")
        elif count > 1:
            errors.append(f"问题重复（忽略空白和大小写）: {normalized}")

    review_counts: Counter[str] = Counter()
    answerable_count = 0
    unanswerable_count = 0
    for item in cases:
        case_id = str(item.get("case_id", "<unknown>"))
        if item.get("dataset_version") != dataset_version:
            errors.append(f"{case_id}: dataset_version 与 manifest 不一致")

        review_status = str(item.get("review_status", ""))
        review_counts[review_status] += 1
        if review_status not in {"PENDING", "APPROVED", "REJECTED"}:
            errors.append(f"{case_id}: review_status 无效")
        if require_approved and review_status != "APPROVED":
            errors.append(f"{case_id}: 尚未 APPROVED")

        answerable = item.get("answerable")
        expected_refusal = item.get("expected_refusal")
        qrels = item.get("qrels")
        gold_sources = item.get("gold_sources", [])
        if not isinstance(qrels, list):
            errors.append(f"{case_id}: qrels 必须是数组")
            qrels = []
        if not isinstance(gold_sources, list):
            errors.append(f"{case_id}: gold_sources 必须是数组")
            gold_sources = []

        if answerable is True:
            answerable_count += 1
            if expected_refusal is not False:
                errors.append(f"{case_id}: 可回答题 expected_refusal 必须为 false")
            if not qrels:
                errors.append(f"{case_id}: 可回答题必须有 qrels")
            if not gold_sources:
                errors.append(f"{case_id}: 可回答题必须有 gold_sources")
        elif answerable is False:
            unanswerable_count += 1
            if expected_refusal is not True:
                errors.append(f"{case_id}: 不可回答题 expected_refusal 必须为 true")
            if qrels:
                errors.append(f"{case_id}: 不可回答题 qrels 必须为空")
            if gold_sources:
                errors.append(f"{case_id}: 不可回答题 gold_sources 必须为空")
        else:
            errors.append(f"{case_id}: answerable 必须是 boolean")

        qrel_keys: set[str] = set()
        for qrel in qrels:
            if not isinstance(qrel, dict):
                errors.append(f"{case_id}: qrel 必须是 object")
                continue
            document_key = str(qrel.get("document_key", ""))
            qrel_keys.add(document_key)
            if document_key not in corpus:
                errors.append(f"{case_id}: qrel 文档不存在: {document_key}")
            relevance = qrel.get("relevance")
            if not isinstance(relevance, int) or relevance < 1 or relevance > 3:
                errors.append(f"{case_id}: relevance 必须是 1-3 的整数")

        for source in gold_sources:
            if not isinstance(source, dict):
                errors.append(f"{case_id}: gold_source 必须是 object")
                continue
            document_key = str(source.get("document_key", ""))
            quote = source.get("quote")
            if document_key not in corpus:
                errors.append(f"{case_id}: gold_source 文档不存在: {document_key}")
                continue
            if document_key not in qrel_keys:
                errors.append(f"{case_id}: gold_source 未出现在 qrels: {document_key}")
            if not isinstance(quote, str) or not quote.strip():
                errors.append(f"{case_id}: gold_source quote 不能为空")
                continue
            if normalize_text(quote) not in normalize_text(corpus[document_key]):
                errors.append(
                    f"{case_id}: quote 不存在于 {document_key}: {quote!r}"
                )

    summary: dict[str, Any] = {
        "schema_version": 1,
        "dataset_version": dataset_version,
        "status": "valid" if not errors else "invalid",
        "cases_file": cases_name,
        "cases_sha256": sha256_file(cases_path),
        "case_count": len(cases),
        "answerable_count": answerable_count,
        "unanswerable_count": unanswerable_count,
        "review_status": dict(sorted(review_counts.items())),
        "corpus_document_count": len(corpus),
        "error_count": len(errors),
    }
    return summary, errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary, errors = validate(
        dataset=args.dataset.resolve(),
        cases_name=args.cases_name,
        expected_count=args.expected_count,
        require_approved=args.require_approved,
    )
    if args.write_summary is not None:
        args.write_summary.parent.mkdir(parents=True, exist_ok=True)
        args.write_summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
