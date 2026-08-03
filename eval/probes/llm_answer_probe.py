from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
os.chdir(BACKEND_ROOT)
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.knowledge.providers import EvidenceItem  # noqa: E402
from app.services.knowledge import _llm  # noqa: E402


async def main() -> None:
    provider = _llm(get_settings())
    print("provider:", type(provider).__name__ if provider else None)
    if provider is None:
        return

    evidence = [
        EvidenceItem(
            hit_id="test-span-1",
            text=(
                "设备遗失或疑似被盗后，员工应在30分钟内"
                "通知IT服务台和直属负责人。"
            ),
        )
    ]
    cases = [
        (
            "A-原文措辞",
            "设备遗失或疑似被盗后，员工应在多长时间内通知谁？",
        ),
        (
            "B-同义改写",
            "公司电脑丢失后应在多长时间内报告给谁？",
        ),
    ]

    for name, query in cases:
        print()
        print("=" * 60)
        print(name)
        print("问题：", query)
        print("问题 Unicode：", query.encode("unicode_escape").decode("ascii"))
        print(
            "证据 Unicode：",
            evidence[0].text.encode("unicode_escape").decode("ascii"),
        )
        try:
            result = await provider.answer(query, evidence)
            print("状态：", result.status)
            print("答案：", result.text)
            print("引用：", result.cited_hit_ids)
        except Exception as exc:  # noqa: BLE001 - diagnostic probe prints safe error types
            print("异常类型：", type(exc).__name__)
            print("异常信息：", str(exc))
            cause = exc.__cause__
            if cause is not None:
                print("底层异常：", type(cause).__name__)
                print("底层信息：", str(cause))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
