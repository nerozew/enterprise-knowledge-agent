from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def load_validator() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "validate_dataset.py"
    spec = importlib.util.spec_from_file_location("validate_dataset", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load dataset validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()


def write_dataset(root: Path, *, quote: str = "最长 30 天") -> None:
    dataset = root / "test-v1"
    corpus = dataset / "corpus"
    corpus.mkdir(parents=True)
    (dataset / "manifest.json").write_text(
        json.dumps({"dataset_version": "test-v1"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (corpus / "policy.md").write_text(
        "---\ndocument_key: policy\n---\n普通权限最长 30 天。\n",
        encoding="utf-8",
    )
    cases = [
        {
            "case_id": "test-001",
            "dataset_version": "test-v1",
            "review_status": "APPROVED",
            "question": "普通权限多久到期？",
            "answerable": True,
            "expected_refusal": False,
            "qrels": [{"document_key": "policy", "relevance": 3}],
            "gold_sources": [{"document_key": "policy", "quote": quote}],
        },
        {
            "case_id": "test-002",
            "dataset_version": "test-v1",
            "review_status": "APPROVED",
            "question": "年假有多少天？",
            "answerable": False,
            "expected_refusal": True,
            "qrels": [],
            "gold_sources": [],
        },
    ]
    (dataset / "cases.draft.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in cases),
        encoding="utf-8",
    )


def test_validate_accepts_answerable_and_unanswerable_cases(tmp_path: Path) -> None:
    write_dataset(tmp_path)

    summary, errors = validator.validate(
        dataset=tmp_path / "test-v1",
        cases_name="cases.draft.jsonl",
        expected_count=2,
        require_approved=True,
    )

    assert errors == []
    assert summary["status"] == "valid"
    assert summary["answerable_count"] == 1
    assert summary["unanswerable_count"] == 1


def test_validate_rejects_quote_that_is_not_in_corpus(tmp_path: Path) -> None:
    write_dataset(tmp_path, quote="不存在的原文")

    summary, errors = validator.validate(
        dataset=tmp_path / "test-v1",
        cases_name="cases.draft.jsonl",
        expected_count=2,
        require_approved=True,
    )

    assert summary["status"] == "invalid"
    assert any("quote 不存在" in error for error in errors)
