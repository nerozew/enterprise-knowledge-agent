"""Security-conscious parsers for the four P3 source formats."""

from __future__ import annotations

import hashlib
import importlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree

from app.knowledge.errors import UnsafeDocumentError, UnsupportedDocumentError
from app.knowledge.types import BlockKind, DocumentFormat, ParsedDocument, SourceBlock

_MIB = 1024 * 1024
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_ALLOWED_MIMES: dict[DocumentFormat, set[str]] = {
    DocumentFormat.PDF: {"application/pdf"},
    DocumentFormat.DOCX: {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    DocumentFormat.MARKDOWN: {"text/markdown", "text/plain"},
    DocumentFormat.TXT: {"text/plain"},
}
_EXTENSIONS = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".txt": DocumentFormat.TXT,
}
_DOCX_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOCX_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_upload_bytes: int = 50 * _MIB
    max_text_bytes: int = 10 * _MIB
    max_pdf_pages: int = 500
    max_zip_entries: int = 2_000
    max_zip_uncompressed_bytes: int = 100 * _MIB
    max_zip_ratio: int = 100


class DocumentParser:
    """Validate and parse PDF, DOCX, Markdown, and TXT into structure-v1 blocks."""

    def __init__(self, limits: ParserLimits | None = None) -> None:
        self.limits = limits or ParserLimits()

    def parse(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
    ) -> ParsedDocument:
        doc_format = self._validate_envelope(filename, content, mime_type)
        normalized_mime = self._normalized_mime(doc_format, mime_type)
        if doc_format is DocumentFormat.PDF:
            blocks = self._parse_pdf(content)
        elif doc_format is DocumentFormat.DOCX:
            blocks = self._parse_docx(content)
        else:
            blocks = self._parse_text(content, markdown=doc_format is DocumentFormat.MARKDOWN)
        if not any(block.text.strip() for block in blocks):
            raise UnsafeDocumentError("document contains no extractable text")
        return ParsedDocument(
            format=doc_format,
            mime_type=normalized_mime,
            blocks=tuple(blocks),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )

    def _validate_envelope(
        self, filename: str, content: bytes, mime_type: str | None
    ) -> DocumentFormat:
        suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        if suffix == ".docm":
            raise UnsafeDocumentError("macro-enabled DOCM is not accepted")
        try:
            doc_format = _EXTENSIONS[suffix]
        except KeyError as exc:
            raise UnsupportedDocumentError(
                f"unsupported file extension: {suffix or '<none>'}"
            ) from exc
        size_limit = (
            self.limits.max_text_bytes
            if doc_format in {DocumentFormat.TXT, DocumentFormat.MARKDOWN}
            else self.limits.max_upload_bytes
        )
        if not content or len(content) > size_limit:
            raise UnsafeDocumentError(f"file size must be between 1 and {size_limit} bytes")
        if doc_format is DocumentFormat.PDF and not content.startswith(_PDF_MAGIC):
            raise UnsafeDocumentError("PDF magic bytes do not match the extension")
        if doc_format is DocumentFormat.DOCX and not content.startswith(_ZIP_MAGIC):
            raise UnsafeDocumentError("DOCX magic bytes do not match the extension")
        if b"\x00" in content[:4096] and doc_format in {
            DocumentFormat.TXT,
            DocumentFormat.MARKDOWN,
        }:
            raise UnsafeDocumentError("text document contains binary NUL bytes")
        if mime_type:
            media_type = mime_type.split(";", 1)[0].strip().lower()
            if media_type not in _ALLOWED_MIMES[doc_format]:
                raise UnsafeDocumentError("MIME type does not match the extension")
        return doc_format

    @staticmethod
    def _normalized_mime(doc_format: DocumentFormat, supplied: str | None) -> str:
        if supplied:
            return supplied.split(";", 1)[0].strip().lower()
        return next(iter(_ALLOWED_MIMES[doc_format]))

    def _parse_pdf(self, content: bytes) -> list[SourceBlock]:
        try:
            pypdf = importlib.import_module("pypdf")
        except ImportError as exc:
            raise UnsupportedDocumentError(
                "PDF parsing requires the optional 'pypdf' dependency"
            ) from exc
        try:
            reader = pypdf.PdfReader(io.BytesIO(content), strict=True)
        except Exception as exc:
            raise UnsafeDocumentError("invalid or unsupported PDF") from exc
        if bool(getattr(reader, "is_encrypted", False)):
            raise UnsafeDocumentError("encrypted PDF is not accepted")
        pages = list(reader.pages)
        if len(pages) > self.limits.max_pdf_pages:
            raise UnsafeDocumentError("PDF exceeds the configured page limit")
        blocks: list[SourceBlock] = []
        offset = 0
        for page_number, page in enumerate(pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as exc:
                raise UnsafeDocumentError("PDF text extraction failed") from exc
            if not text:
                continue
            page_blocks = self._structure_text(text, markdown=False, page=page_number, base=offset)
            blocks.extend(page_blocks)
            offset += len(text) + 2
        return blocks

    def _parse_docx(self, content: bytes) -> list[SourceBlock]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise UnsafeDocumentError("invalid DOCX archive") from exc
        with archive:
            self._validate_docx_archive(archive)
            try:
                xml = archive.read("word/document.xml")
            except KeyError as exc:
                raise UnsafeDocumentError("DOCX does not contain word/document.xml") from exc
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise UnsafeDocumentError("DOCX document XML is invalid") from exc
        heading_path: list[str] = []
        blocks: list[SourceBlock] = []
        offset = 0
        body = next((node for node in root.iter() if _local_name(node.tag) == "body"), None)
        if body is None:
            raise UnsafeDocumentError("DOCX document XML has no body")
        for child in body:
            tag = _local_name(child.tag)
            if tag == "p":
                text = "".join(
                    node.text or "" for node in child.iter() if _local_name(node.tag) == "t"
                )
                text = text.strip()
                if not text:
                    continue
                style = next(
                    (
                        node.attrib.get(f"{{{_DOCX_W_NS}}}val", "")
                        for node in child.iter()
                        if _local_name(node.tag) == "pStyle"
                    ),
                    "",
                )
                kind = (
                    BlockKind.HEADING
                    if style.lower().startswith("heading")
                    else BlockKind.PARAGRAPH
                )
                if kind is BlockKind.HEADING:
                    heading_path = _update_heading_path(heading_path, text, style)
                blocks.append(
                    SourceBlock(kind, text, None, tuple(heading_path), offset, offset + len(text))
                )
                offset += len(text) + 2
            elif tag == "tbl":
                rows: list[str] = []
                for row in (node for node in child.iter() if _local_name(node.tag) == "tr"):
                    cells = [
                        " ".join(
                            n.text or "" for n in cell.iter() if _local_name(n.tag) == "t"
                        ).strip()
                        for cell in row
                        if _local_name(cell.tag) == "tc"
                    ]
                    if cells:
                        rows.append(" | ".join(cells))
                table = "\n".join(rows).strip()
                if table:
                    blocks.append(
                        SourceBlock(
                            BlockKind.TABLE,
                            table,
                            None,
                            tuple(heading_path),
                            offset,
                            offset + len(table),
                        )
                    )
                    offset += len(table) + 2
        return blocks

    def _validate_docx_archive(self, archive: zipfile.ZipFile) -> None:
        infos = archive.infolist()
        if len(infos) > self.limits.max_zip_entries:
            raise UnsafeDocumentError("DOCX contains too many archive entries")
        total = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise UnsafeDocumentError("DOCX contains an unsafe archive path")
            normalized_name = info.filename.casefold()
            if normalized_name.endswith("vbaproject.bin") or "/activex/" in normalized_name:
                raise UnsafeDocumentError("DOCX active content is not accepted")
            if path.suffix.casefold() in {".zip", ".docx", ".xlsx", ".pptx", ".jar"}:
                with archive.open(info) as nested:
                    if nested.read(4).startswith(_ZIP_MAGIC):
                        raise UnsafeDocumentError("DOCX nested archives are not accepted")
            total += info.file_size
            if total > self.limits.max_zip_uncompressed_bytes:
                raise UnsafeDocumentError("DOCX uncompressed size exceeds the limit")
            if (
                info.compress_size > 0
                and info.file_size > 1 * _MIB
                and info.file_size / info.compress_size > self.limits.max_zip_ratio
            ):
                raise UnsafeDocumentError("DOCX contains a suspicious compression ratio")
        for name in ("_rels/.rels", "word/_rels/document.xml.rels"):
            try:
                relationships = ElementTree.fromstring(archive.read(name))
            except KeyError:
                continue
            except ElementTree.ParseError as exc:
                raise UnsafeDocumentError("DOCX relationship XML is invalid") from exc
            for rel in relationships.findall(f"{{{_DOCX_REL_NS}}}Relationship"):
                if rel.attrib.get("TargetMode", "").lower() == "external":
                    raise UnsafeDocumentError("DOCX external relationships are not accepted")

    def _parse_text(self, content: bytes, *, markdown: bool) -> list[SourceBlock]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsafeDocumentError("text document must be UTF-8") from exc
        return self._structure_text(text, markdown=markdown, page=None, base=0)

    @staticmethod
    def _structure_text(
        text: str, *, markdown: bool, page: int | None, base: int
    ) -> list[SourceBlock]:
        blocks: list[SourceBlock] = []
        heading_path: list[str] = []
        code = False
        cursor = 0
        for match in re.finditer(r"(?:\r?\n){2,}|\Z", text):
            raw = text[cursor : match.start()]
            start = base + cursor
            cursor = match.end()
            value = raw.strip()
            if not value:
                continue
            leading = len(raw) - len(raw.lstrip())
            start += leading
            kind = BlockKind.PARAGRAPH
            if markdown and value.startswith("```"):
                code = not code if value.count("```") % 2 else code
                kind = BlockKind.CODE
            elif markdown and not code and (heading := re.match(r"^(#{1,6})\s+(.+)$", value)):
                level = len(heading.group(1))
                title = heading.group(2).strip()
                heading_path[:] = heading_path[: level - 1]
                heading_path.append(title)
                kind = BlockKind.HEADING
            elif re.match(r"^(?:[-*+]|\d+[.)])\s+", value):
                kind = BlockKind.LIST
            elif "|" in value and "\n" in value:
                kind = BlockKind.TABLE
            blocks.append(
                SourceBlock(
                    kind,
                    value,
                    page,
                    tuple(heading_path),
                    start,
                    start + len(value),
                )
            )
        return blocks


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _update_heading_path(current: list[str], text: str, style: str) -> list[str]:
    match = re.search(r"(\d+)$", style)
    level = int(match.group(1)) if match else 1
    return [*current[: level - 1], text]
