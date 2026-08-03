from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validate_dataset import sha256_file, validate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="冻结已完成人工复核的 RAG 数据集。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reviewed-name", default="cases.reviewed.jsonl")
    return parser.parse_args(argv)


def directory_snapshot(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(item).removeprefix("sha256:")))
    return f"sha256:{digest.hexdigest()}"


def freeze(dataset: Path, reviewed_name: str) -> dict[str, Any]:
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_count = int(manifest["answerable_cases"]) + int(
        manifest["unanswerable_cases"]
    )
    summary, errors = validate(
        dataset=dataset,
        cases_name=reviewed_name,
        expected_count=expected_count,
        require_approved=True,
    )
    if errors:
        raise ValueError("数据集未通过严格校验：\n" + "\n".join(errors))

    corpus_path = (dataset / str(manifest.get("corpus_path", "corpus"))).resolve()
    actual_corpus_snapshot = directory_snapshot(corpus_path)
    expected_corpus_snapshot = str(manifest.get("corpus_snapshot", ""))
    if expected_corpus_snapshot and expected_corpus_snapshot != actual_corpus_snapshot:
        raise ValueError(
            "语料快照不匹配："
            f"expected={expected_corpus_snapshot}, actual={actual_corpus_snapshot}"
        )

    reviewed_path = dataset / reviewed_name
    cases_path = dataset / "cases.jsonl"
    cases_path.write_bytes(reviewed_path.read_bytes())
    cases_snapshot = sha256_file(cases_path)
    reviewers = sorted(
        {
            str(case["reviewer"])
            for case in (
                json.loads(line)
                for line in cases_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }
    )

    manifest["status"] = "FROZEN"
    manifest["frozen_at"] = datetime.now(UTC).isoformat()
    manifest["cases_snapshot"] = cases_snapshot
    manifest["reviewers"] = reviewers
    manifest["allowed_claim"] = "formal-evaluation-only"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **summary,
        "status": "frozen",
        "cases_file": "cases.jsonl",
        "cases_sha256": cases_snapshot,
        "corpus_sha256": actual_corpus_snapshot,
        "reviewers": reviewers,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = freeze(args.dataset.resolve(), args.reviewed_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
