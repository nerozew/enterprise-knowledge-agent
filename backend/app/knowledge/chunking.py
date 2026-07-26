"""Deterministic structure-v1 / parent-child-v1 chunking."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.knowledge.types import (
    BlockKind,
    ChunkKind,
    KnowledgeChunk,
    ParsedDocument,
    SourceBlock,
)


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    parent_max_tokens: int = 1200
    child_target_tokens: int = 384
    child_min_tokens: int = 96
    child_max_tokens: int = 512
    child_overlap_tokens: int = 64

    def __post_init__(self) -> None:
        if not (1 <= self.child_min_tokens <= self.child_target_tokens <= self.child_max_tokens):
            raise ValueError("child token limits are inconsistent")
        if self.child_overlap_tokens >= self.child_target_tokens:
            raise ValueError("child overlap must be smaller than target size")


class ParentChildChunker:
    """Create stable parent and child chunks without model-specific tokenizers.

    The estimator treats a CJK character or a latin word as one token. Production
    embedding adapters may enforce a stricter model token limit before indexing.
    """

    profile = "parent-child-v1"

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(
        self,
        parsed: ParsedDocument,
        *,
        document_id: str,
        document_version_id: str,
    ) -> tuple[KnowledgeChunk, ...]:
        parents = self._group_parents(parsed.blocks)
        result: list[KnowledgeChunk] = []
        ordinal = 0
        for group in parents:
            parent = self._make_chunk(
                group,
                document_id=document_id,
                version_id=document_version_id,
                kind=ChunkKind.PARENT,
                ordinal=ordinal,
                parent_id=None,
            )
            result.append(parent)
            ordinal += 1
            for child_blocks in self._split_children(group):
                child = self._make_chunk(
                    child_blocks,
                    document_id=document_id,
                    version_id=document_version_id,
                    kind=ChunkKind.CHILD,
                    ordinal=ordinal,
                    parent_id=parent.chunk_id,
                )
                result.append(child)
                ordinal += 1
        return tuple(result)

    def _group_parents(self, blocks: tuple[SourceBlock, ...]) -> list[list[SourceBlock]]:
        groups: list[list[SourceBlock]] = []
        current: list[SourceBlock] = []
        current_tokens = 0
        current_heading: tuple[str, ...] | None = None
        for block in blocks:
            block_tokens = estimate_tokens(block.text)
            boundary = (
                current and block.heading_path != current_heading and bool(block.heading_path)
            )
            if current and (
                boundary or current_tokens + block_tokens > self.config.parent_max_tokens
            ):
                groups.append(current)
                current = []
                current_tokens = 0
            current.append(block)
            current_tokens += block_tokens
            current_heading = block.heading_path
        if current:
            groups.append(current)
        return groups

    def _split_children(self, blocks: list[SourceBlock]) -> list[list[SourceBlock]]:
        if sum(estimate_tokens(block.text) for block in blocks) <= self.config.child_max_tokens:
            return [blocks]
        pieces: list[SourceBlock] = []
        for block in blocks:
            if estimate_tokens(block.text) <= self.config.child_max_tokens:
                pieces.append(block)
            else:
                pieces.extend(self._hard_split(block))
        groups: list[list[SourceBlock]] = []
        current: list[SourceBlock] = []
        tokens = 0
        for piece in pieces:
            piece_tokens = estimate_tokens(piece.text)
            if current and tokens + piece_tokens > self.config.child_target_tokens:
                groups.append(current)
                overlap = self._overlap(current)
                current = overlap
                tokens = sum(estimate_tokens(item.text) for item in current)
            current.append(piece)
            tokens += piece_tokens
        if current:
            if groups and tokens < self.config.child_min_tokens:
                groups[-1].extend(current)
            else:
                groups.append(current)
        return groups

    def _hard_split(self, block: SourceBlock) -> list[SourceBlock]:
        if block.kind is BlockKind.TABLE and "\n" in block.text:
            return self._split_table(block)
        spans = _sentence_spans(block.text)
        result: list[SourceBlock] = []
        current: list[tuple[int, int]] = []
        tokens = 0
        for start, end in spans:
            span_tokens = estimate_tokens(block.text[start:end])
            if current and tokens + span_tokens > self.config.child_target_tokens:
                result.append(self._slice_block(block, current[0][0], current[-1][1]))
                current = []
                tokens = 0
            if span_tokens > self.config.child_max_tokens:
                width = max(
                    1,
                    int(len(block.text[start:end]) * self.config.child_target_tokens / span_tokens),
                )
                for char_start in range(start, end, width):
                    result.append(
                        self._slice_block(block, char_start, min(end, char_start + width))
                    )
                continue
            current.append((start, end))
            tokens += span_tokens
        if current:
            result.append(self._slice_block(block, current[0][0], current[-1][1]))
        return result

    def _split_table(self, block: SourceBlock) -> list[SourceBlock]:
        lines = block.text.splitlines()
        header = lines[0]
        groups: list[list[str]] = []
        current = [header]
        tokens = estimate_tokens(header)
        for line in lines[1:]:
            line_tokens = estimate_tokens(line)
            if len(current) > 1 and tokens + line_tokens > self.config.child_target_tokens:
                groups.append(current)
                current = [header]
                tokens = estimate_tokens(header)
            current.append(line)
            tokens += line_tokens
        if len(current) > 1 or not groups:
            groups.append(current)
        result: list[SourceBlock] = []
        search_from = 0
        for group in groups:
            last_line = group[-1]
            last_start = block.text.find(last_line, search_from)
            if last_start < 0:
                last_start = search_from
            end = last_start + len(last_line)
            result.append(
                SourceBlock(
                    BlockKind.TABLE,
                    "\n".join(group),
                    block.page,
                    block.heading_path,
                    block.char_start,
                    block.char_start + end,
                )
            )
            search_from = end
        return result

    def _overlap(self, blocks: list[SourceBlock]) -> list[SourceBlock]:
        overlap: list[SourceBlock] = []
        tokens = 0
        for block in reversed(blocks):
            count = estimate_tokens(block.text)
            if tokens and tokens + count > self.config.child_overlap_tokens:
                break
            overlap.insert(0, block)
            tokens += count
        return overlap

    @staticmethod
    def _slice_block(block: SourceBlock, start: int, end: int) -> SourceBlock:
        text = block.text[start:end].strip()
        leading = len(block.text[start:end]) - len(block.text[start:end].lstrip())
        actual_start = start + leading
        return SourceBlock(
            block.kind,
            text,
            block.page,
            block.heading_path,
            block.char_start + actual_start,
            block.char_start + actual_start + len(text),
        )

    @staticmethod
    def _make_chunk(
        blocks: list[SourceBlock],
        *,
        document_id: str,
        version_id: str,
        kind: ChunkKind,
        ordinal: int,
        parent_id: str | None,
    ) -> KnowledgeChunk:
        text = "\n\n".join(block.text for block in blocks)
        start = min(block.char_start for block in blocks)
        end = max(block.char_end for block in blocks)
        pages = {block.page for block in blocks}
        page = pages.pop() if len(pages) == 1 else None
        heading = next((block.heading_path for block in blocks if block.heading_path), ())
        span = stable_source_span_id(version_id, page, start, end)
        chunk_id = hashlib.sha256(
            f"{version_id}:{kind}:{ordinal}:{span}:{hashlib.sha256(text.encode()).hexdigest()}".encode()
        ).hexdigest()[:32]
        return KnowledgeChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            document_version_id=version_id,
            kind=kind,
            ordinal=ordinal,
            text=text,
            page=page,
            heading_path=heading,
            char_start=start,
            char_end=end,
            source_span_id=span,
            parent_id=parent_id,
        )


def stable_source_span_id(version_id: str, page: int | None, start: int, end: int) -> str:
    """Hash immutable version and normalized half-open source range."""
    normalized_page = page if page is not None else 0
    return hashlib.sha256(f"{version_id}:{normalized_page}:{start}:{end}".encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]|[\w]+|[^\s\w]", text, re.UNICODE))


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"(?<=[。！？.!?])\s+|\n+", text):
        if match.start() > start:
            spans.append((start, match.start()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans or [(0, len(text))]
