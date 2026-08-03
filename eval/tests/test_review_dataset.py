from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_reviewer() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "review_dataset.py"
    spec = importlib.util.spec_from_file_location("review_dataset", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load dataset reviewer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reviewer = load_reviewer()


def test_decision_summary_counts_missing_as_pending() -> None:
    cases = [{"case_id": "test-001"}, {"case_id": "test-002"}]
    decisions = {
        "test-001": {
            "status": "APPROVED",
            "reviewer": "reviewer",
            "reviewed_at": "2026-07-30T00:00:00+00:00",
        }
    }

    assert reviewer.decision_summary(cases, decisions) == {
        "APPROVED": 1,
        "REJECTED": 0,
        "PENDING": 1,
    }


def test_export_requires_every_case_to_be_approved(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="尚不能导出"):
        reviewer.export_reviewed(
            tmp_path,
            [{"case_id": "test-001", "review_status": "PENDING"}],
            {},
        )
