"""Ranking metrics used by the versioned development evaluation runner."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int = 5) -> float:
    if not relevant_ids:
        return 1.0
    return len(set(ranked_ids[:k]) & relevant_ids) / len(relevant_ids)


def reciprocal_rank_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int = 10) -> float:
    for rank, item_id in enumerate(ranked_ids[:k], start=1):
        if item_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked_ids: Sequence[str],
    relevance: Mapping[str, float] | set[str],
    k: int = 10,
) -> float:
    gains = relevance if isinstance(relevance, Mapping) else {item_id: 1.0 for item_id in relevance}
    dcg = sum(
        (2 ** gains.get(item_id, 0.0) - 1) / math.log2(rank + 1)
        for rank, item_id in enumerate(ranked_ids[:k], start=1)
    )
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 1.0


def mean_metric(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
