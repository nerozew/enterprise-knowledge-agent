from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交互式复核 RAG 评测候选题。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--cases-name", default="cases.draft.jsonl")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--export", action="store_true")
    return parser.parse_args(argv)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_decisions(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("review.decisions.json 必须是 JSON object")
    return data


def save_decisions(path: Path, decisions: dict[str, dict[str, str]]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def decision_summary(
    cases: list[dict[str, Any]], decisions: dict[str, dict[str, str]]
) -> dict[str, int]:
    summary = {"APPROVED": 0, "REJECTED": 0, "PENDING": 0}
    for case in cases:
        state = decisions.get(str(case["case_id"]), {}).get("status", "PENDING")
        summary[state] = summary.get(state, 0) + 1
    return summary


def render_case(case: dict[str, Any], index: int, total: int) -> None:
    print("\n" + "=" * 72)
    print(f"[{index}/{total}] {case['case_id']}  |  {', '.join(case.get('tags', []))}")
    print(f"问题：{case['question']}")
    print(f"应答性：{'可回答' if case['answerable'] else '应拒答'}")
    if case["answerable"]:
        print("相关文档：")
        for qrel in case.get("qrels", []):
            print(f"  - {qrel['document_key']} (relevance={qrel['relevance']})")
        print("原文证据：")
        for source in case.get("gold_sources", []):
            print(f"  - [{source['document_key']}] {source['quote']}")
    else:
        print("原文证据：无；请确认 8 份已发布语料确实没有直接答案。")


def export_reviewed(
    dataset: Path,
    cases: list[dict[str, Any]],
    decisions: dict[str, dict[str, str]],
) -> Path:
    summary = decision_summary(cases, decisions)
    if summary["APPROVED"] != len(cases):
        raise ValueError(f"尚不能导出：{summary}")
    output = dataset / "cases.reviewed.jsonl"
    rows: list[str] = []
    for case in cases:
        decision = decisions[str(case["case_id"])]
        reviewed = dict(case)
        reviewed["review_status"] = "APPROVED"
        reviewed["reviewer"] = decision["reviewer"]
        reviewed["reviewed_at"] = decision["reviewed_at"]
        rows.append(json.dumps(reviewed, ensure_ascii=False, separators=(",", ":")))
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset = args.dataset.resolve()
    cases = load_jsonl(dataset / args.cases_name)
    decisions_path = dataset / "review.decisions.json"
    decisions = load_decisions(decisions_path)

    if args.summary:
        print(
            json.dumps(
                decision_summary(cases, decisions), ensure_ascii=False, indent=2
            )
        )
        return 0

    if args.export:
        output = export_reviewed(dataset, cases, decisions)
        print(f"已导出：{output}")
        return 0

    pending = [
        case
        for case in cases
        if decisions.get(str(case["case_id"]), {}).get("status") != "APPROVED"
    ]
    if not pending:
        print("全部题目已经 APPROVED；使用 --export 生成 cases.reviewed.jsonl。")
        return 0

    print("A=通过，R=驳回并记录原因，S=暂时跳过，Q=保存并退出")
    for index, case in enumerate(pending, start=1):
        render_case(case, index, len(pending))
        while True:
            choice = input("决定 [A/R/S/Q]：").strip().upper()
            if choice in {"A", "R", "S", "Q"}:
                break
            print("请输入 A、R、S 或 Q。")
        if choice == "Q":
            break
        if choice == "S":
            continue

        case_id = str(case["case_id"])
        decision = {
            "status": "APPROVED" if choice == "A" else "REJECTED",
            "reviewer": args.reviewer,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "note": "",
        }
        if choice == "R":
            decision["note"] = input("驳回原因：").strip()
        decisions[case_id] = decision
        save_decisions(decisions_path, decisions)
        print(
            "当前进度："
            + json.dumps(decision_summary(cases, decisions), ensure_ascii=False)
        )

    save_decisions(decisions_path, decisions)
    print(
        "已保存："
        + json.dumps(decision_summary(cases, decisions), ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
