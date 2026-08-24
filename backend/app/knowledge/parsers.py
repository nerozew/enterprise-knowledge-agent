"""Security-conscious V1 and structure-v2 document parser providers."""

from __future__ import annotations

import hashlib
import importlib
import io
import re
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable
from xml.etree import ElementTree

from app.knowledge.errors import (
    OCRProviderUnavailableError,
    UnsafeDocumentError,
    UnsupportedDocumentError,
)
from app.knowledge.types import (
    BlockKind,
    DocumentFormat,
    LayoutBlock,
    LayoutDocumentV2,
    NormalizedBBox,
    OCRMode,
    ParsedDocument,
    SourceBlock,
)

_MIB = 1024 * 1024
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_ALLOWED_MIMES: dict[DocumentFormat, set[str]] = {
    DocumentFormat.PDF: {"application/pdf"},
    DocumentFormat.DOCX: {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    DocumentFormat.PPTX: {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    },
    DocumentFormat.MARKDOWN: {"text/markdown", "text/plain"},
    DocumentFormat.TXT: {"text/plain"},
}
_EXTENSIONS = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".pptx": DocumentFormat.PPTX,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".txt": DocumentFormat.TXT,
}
_DOCX_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOCX_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PPTX_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_upload_bytes: int = 50 * _MIB
    max_text_bytes: int = 10 * _MIB
    max_pdf_pages: int = 500
    max_zip_entries: int = 2_000
    max_zip_uncompressed_bytes: int = 100 * _MIB
    max_zip_ratio: int = 100


@runtime_checkable
class DocumentParserProvider(Protocol):
    """Provider contract for producing the versioned structure-v2 artifact."""

    def parse_layout(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        ocr_mode: OCRMode = OCRMode.AUTO,
    ) -> LayoutDocumentV2: ...


@runtime_checkable
class OCRProvider(Protocol):
    """Extension point for page OCR engines such as RapidOCR or PaddleOCR."""

    @property
    def profile(self) -> str: ...

    def is_available(self) -> bool: ...


class DocumentParser:
    """Validate and parse supported files while preserving the V1 public contract."""

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
        elif doc_format is DocumentFormat.PPTX:
            blocks = self._parse_pptx(content)
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

    def parse_layout(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        ocr_mode: OCRMode = OCRMode.AUTO,
    ) -> LayoutDocumentV2:
        """Return a deterministic V2 artifact without requiring optional AI runtimes.

        The legacy provider does not perform OCR. ``FORCE`` therefore fails explicitly,
        while ``AUTO`` records a warning so callers can decide whether to retry with a
        Docling/OCR provider.
        """

        if ocr_mode is OCRMode.FORCE:
            raise UnsupportedDocumentError(
                "forced OCR requires the optional Docling/RapidOCR parser provider"
            )
        parsed = self.parse(filename=filename, content=content, mime_type=mime_type)
        page_count = None
        slide_count = None
        if parsed.format is DocumentFormat.PDF:
            page_count = max((block.page or 0 for block in parsed.blocks), default=0)
        elif parsed.format is DocumentFormat.PPTX:
            slide_count = max((block.slide or 0 for block in parsed.blocks), default=0)
        warnings = (
            ("legacy provider does not perform OCR",)
            if ocr_mode is OCRMode.AUTO and parsed.format is DocumentFormat.PDF
            else ()
        )
        return LayoutDocumentV2(
            format=parsed.format,
            mime_type=parsed.mime_type,
            source_sha256=parsed.content_sha256,
            blocks=tuple(_layout_block(block, index) for index, block in enumerate(parsed.blocks)),
            page_count=page_count,
            slide_count=slide_count,
            parser_profile="legacy-layout-v2",
            warnings=warnings,
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
        if doc_format in {DocumentFormat.DOCX, DocumentFormat.PPTX} and not content.startswith(
            _ZIP_MAGIC
        ):
            raise UnsafeDocumentError(
                f"{doc_format.value.upper()} magic bytes do not match the extension"
            )
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

    def _parse_pptx(self, content: bytes) -> list[SourceBlock]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise UnsafeDocumentError("invalid PPTX archive") from exc
        with archive:
            self._validate_ooxml_archive(archive, format_name="PPTX")
            slide_entries = sorted(
                (
                    (int(match.group(1)), info.filename)
                    for info in archive.infolist()
                    if (match := _PPTX_SLIDE_RE.match(info.filename))
                ),
                key=lambda item: item[0],
            )
            if not slide_entries:
                raise UnsafeDocumentError("PPTX contains no slides")
            if len(slide_entries) > self.limits.max_pdf_pages:
                raise UnsafeDocumentError("PPTX exceeds the configured slide limit")
            slide_width, slide_height = self._pptx_slide_size(archive)
            blocks: list[SourceBlock] = []
            offset = 0
            for slide_no, entry in slide_entries:
                try:
                    root = ElementTree.fromstring(archive.read(entry))
                except ElementTree.ParseError as exc:
                    raise UnsafeDocumentError("PPTX slide XML is invalid") from exc
                heading_path: tuple[str, ...] = ()
                order = 0
                for shape in root.findall(f".//{{{_P_NS}}}spTree/*"):
                    tag = _local_name(shape.tag)
                    if tag not in {"sp", "graphicFrame"}:
                        continue
                    kind, text = self._pptx_shape_content(shape)
                    if not text:
                        continue
                    if kind is BlockKind.TITLE:
                        heading_path = (text,)
                    bbox = self._pptx_bbox(shape, slide_width, slide_height)
                    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    block_key = f"slide-{slide_no}-block-{order}-{digest[:12]}"
                    blocks.append(
                        SourceBlock(
                            kind=kind,
                            text=text,
                            page=None,
                            heading_path=heading_path,
                            char_start=offset,
                            char_end=offset + len(text),
                            block_key=block_key,
                            slide=slide_no,
                            bbox=bbox,
                            reading_order=order,
                            content_sha256=digest,
                        )
                    )
                    offset += len(text) + 2
                    order += 1
        return blocks

    @staticmethod
    def _pptx_slide_size(archive: zipfile.ZipFile) -> tuple[int, int]:
        try:
            root = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
        except (KeyError, ElementTree.ParseError):
            return (1, 1)
        size = root.find(f".//{{{_P_NS}}}sldSz")
        if size is None:
            return (1, 1)
        try:
            return max(1, int(size.attrib["cx"])), max(1, int(size.attrib["cy"]))
        except (KeyError, ValueError):
            return (1, 1)

    @staticmethod
    def _pptx_shape_content(shape: ElementTree.Element) -> tuple[BlockKind, str]:
        is_table = shape.find(f".//{{{_A_NS}}}tbl") is not None
        if is_table:
            rows: list[str] = []
            for row in shape.findall(f".//{{{_A_NS}}}tr"):
                cells = [
                    " ".join(
                        value.strip()
                        for node in cell.findall(f".//{{{_A_NS}}}t")
                        if (value := (node.text or "")).strip()
                    )
                    for cell in row.findall(f"{{{_A_NS}}}tc")
                ]
                if cells:
                    rows.append(" | ".join(cells))
            return BlockKind.TABLE, "\n".join(rows).strip()
        paragraphs: list[str] = []
        for paragraph in shape.findall(f".//{{{_A_NS}}}p"):
            text = "".join(node.text or "" for node in paragraph.findall(f".//{{{_A_NS}}}t"))
            if text.strip():
                paragraphs.append(text.strip())
        text = "\n".join(paragraphs).strip()
        placeholder = shape.find(f".//{{{_P_NS}}}ph")
        placeholder_type = placeholder.attrib.get("type", "") if placeholder is not None else ""
        kind = (
            BlockKind.TITLE
            if placeholder_type in {"title", "ctrTitle"}
            else BlockKind.PARAGRAPH
        )
        return kind, text

    @staticmethod
    def _pptx_bbox(
        shape: ElementTree.Element, slide_width: int, slide_height: int
    ) -> NormalizedBBox | None:
        offset = shape.find(f".//{{{_A_NS}}}off")
        extent = shape.find(f".//{{{_A_NS}}}ext")
        if offset is None or extent is None or (slide_width, slide_height) == (1, 1):
            return None
        try:
            x = int(offset.attrib["x"])
            y = int(offset.attrib["y"])
            width = int(extent.attrib["cx"])
            height = int(extent.attrib["cy"])
        except (KeyError, ValueError):
            return None
        return NormalizedBBox(
            left=max(0.0, min(1.0, x / slide_width)),
            top=max(0.0, min(1.0, y / slide_height)),
            right=max(0.0, min(1.0, (x + width) / slide_width)),
            bottom=max(0.0, min(1.0, (y + height) / slide_height)),
        )

    def _validate_docx_archive(self, archive: zipfile.ZipFile) -> None:
        self._validate_ooxml_archive(archive, format_name="DOCX")
        for name in ("_rels/.rels", "word/_rels/document.xml.rels"):
            self._validate_relationships(archive, name, format_name="DOCX")

    def _validate_ooxml_archive(
        self, archive: zipfile.ZipFile, *, format_name: str
    ) -> None:
        infos = archive.infolist()
        if len(infos) > self.limits.max_zip_entries:
            raise UnsafeDocumentError(f"{format_name} contains too many archive entries")
        total = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise UnsafeDocumentError(f"{format_name} contains an unsafe archive path")
            normalized_name = info.filename.casefold()
            if normalized_name.endswith("vbaproject.bin") or "/activex/" in normalized_name:
                raise UnsafeDocumentError(f"{format_name} active content is not accepted")
            if path.suffix.casefold() in {".zip", ".docx", ".xlsx", ".pptx", ".jar"}:
                with archive.open(info) as nested:
                    if nested.read(4).startswith(_ZIP_MAGIC):
                        raise UnsafeDocumentError(
                            f"{format_name} nested archives are not accepted"
                        )
            total += info.file_size
            if total > self.limits.max_zip_uncompressed_bytes:
                raise UnsafeDocumentError(
                    f"{format_name} uncompressed size exceeds the limit"
                )
            if (
                info.compress_size > 0
                and info.file_size > 1 * _MIB
                and info.file_size / info.compress_size > self.limits.max_zip_ratio
            ):
                raise UnsafeDocumentError(
                    f"{format_name} contains a suspicious compression ratio"
                )
        for info in infos:
            if info.filename.endswith(".rels"):
                self._validate_relationships(archive, info.filename, format_name=format_name)

    @staticmethod
    def _validate_relationships(
        archive: zipfile.ZipFile, name: str, *, format_name: str
    ) -> None:
        try:
            relationships = ElementTree.fromstring(archive.read(name))
        except KeyError:
            return
        except ElementTree.ParseError as exc:
            raise UnsafeDocumentError(f"{format_name} relationship XML is invalid") from exc
        for rel in relationships.findall(f"{{{_DOCX_REL_NS}}}Relationship"):
            if rel.attrib.get("TargetMode", "").lower() == "external":
                raise UnsafeDocumentError(
                    f"{format_name} external relationships are not accepted"
                )

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


class RapidOCRAvailability:
    """Small dependency probe used by deployment health and parser construction."""

    @property
    def profile(self) -> str:
        return "rapidocr"

    def is_available(self) -> bool:
        try:
            rapidocr = importlib.import_module("rapidocr")
        except ImportError:
            # Legacy rapidocr_onnxruntime bundled the matching runtime itself.
            try:
                importlib.import_module("rapidocr_onnxruntime")
                return True
            except ImportError:
                return False
        try:
            onnxruntime = importlib.import_module("onnxruntime")
        except ImportError:
            return False
        return callable(getattr(rapidocr, "RapidOCR", None)) and callable(
            getattr(onnxruntime, "InferenceSession", None)
        )


class DoclingParserProvider:
    """Optional local Docling adapter with explicit RapidOCR capability checks.

    Docling is intentionally imported lazily so the normal API process and the V1
    parser remain usable in lightweight installations. A converter factory can be
    injected for deterministic tests and for deployment-specific pipeline options.
    """

    def __init__(
        self,
        *,
        limits: ParserLimits | None = None,
        converter_factory: Any | None = None,
        ocr_provider: OCRProvider | None = None,
    ) -> None:
        self.limits = limits or ParserLimits()
        self._legacy = DocumentParser(self.limits)
        self._converter_factory = converter_factory
        self._ocr_provider = ocr_provider or RapidOCRAvailability()

    def parse_layout(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        ocr_mode: OCRMode = OCRMode.AUTO,
    ) -> LayoutDocumentV2:
        doc_format = self._legacy._validate_envelope(filename, content, mime_type)
        if doc_format in {DocumentFormat.DOCX, DocumentFormat.PPTX}:
            try:
                archive = zipfile.ZipFile(io.BytesIO(content))
            except zipfile.BadZipFile as exc:
                raise UnsafeDocumentError(f"invalid {doc_format.value.upper()} archive") from exc
            with archive:
                self._legacy._validate_ooxml_archive(
                    archive, format_name=doc_format.value.upper()
                )
        ocr_available = self._ocr_provider.is_available()
        if ocr_mode is OCRMode.FORCE and not ocr_available:
            raise UnsupportedDocumentError(
                "OCR requested but RapidOCR is unavailable; install the Docling RapidOCR extra"
            )
        auto_ocr_pages: tuple[int, ...] | None = None
        if (
            doc_format is DocumentFormat.PDF
            and ocr_mode is OCRMode.AUTO
            and ocr_available
        ):
            auto_ocr_pages = _pdf_pages_requiring_ocr(content)
            if auto_ocr_pages is None:
                raise UnsafeDocumentError(
                    "AUTO OCR could not classify PDF pages safely"
                )
        effective_ocr_mode = (
            OCRMode.OFF
            if ocr_mode is OCRMode.AUTO
            and (not ocr_available or auto_ocr_pages == ())
            else ocr_mode
        )
        suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        try:
            with tempfile.TemporaryDirectory(prefix="knowledge-parse-") as directory:
                source_path = Path(directory) / f"source{suffix}"
                source_path.write_bytes(content)
                if (
                    doc_format is DocumentFormat.PDF
                    and ocr_mode is OCRMode.AUTO
                    and auto_ocr_pages
                ):
                    # AUTO is deliberately a two-pass pipeline.  The complete
                    # document is first converted with PDF OCR explicitly off;
                    # only pages selected by the native-text preflight are then
                    # submitted to the OCR-enabled converter.  This avoids the
                    # surprisingly common behaviour where an "auto" OCR engine
                    # still inspects every page.
                    native_result = self._make_converter(OCRMode.OFF).convert(
                        str(source_path)
                    )
                    native_document = getattr(native_result, "document", native_result)
                    native_blocks = self._docling_blocks(
                        native_document,
                        doc_format,
                        OCRMode.OFF,
                    )
                    ocr_converter = self._make_converter(OCRMode.FORCE)
                    ocr_blocks: list[LayoutBlock] = []
                    for page_no in auto_ocr_pages:
                        page_result = ocr_converter.convert(
                            str(source_path), page_range=(page_no, page_no)
                        )
                        page_document = getattr(page_result, "document", page_result)
                        page_blocks = self._docling_blocks(
                            page_document,
                            doc_format,
                            OCRMode.FORCE,
                        )
                        ocr_blocks.extend(
                            _remap_single_page_blocks(page_blocks, page_no)
                        )
                    blocks = _merge_page_blocks(
                        native_blocks,
                        ocr_blocks,
                        auto_ocr_pages,
                    )
                    document = native_document
                else:
                    converter = self._make_converter(effective_ocr_mode)
                    result = converter.convert(str(source_path))
                    document = getattr(result, "document", result)
                    blocks = self._docling_blocks(
                        document,
                        doc_format,
                        effective_ocr_mode,
                        auto_ocr_pages=auto_ocr_pages,
                    )
        except UnsupportedDocumentError:
            raise
        except Exception as exc:
            if _is_ocr_runtime_failure(exc):
                raise OCRProviderUnavailableError(
                    "RapidOCR inference runtime is unavailable or failed to initialize"
                ) from exc
            raise UnsafeDocumentError("Docling document conversion failed") from exc
        if not blocks:
            if ocr_mode is OCRMode.AUTO and not ocr_available:
                raise UnsupportedDocumentError(
                    "document needs OCR but RapidOCR is unavailable; "
                    "install the Docling RapidOCR extra"
                )
            raise UnsafeDocumentError("document contains no extractable text")
        page_count = _docling_page_count(document)
        warnings: tuple[str, ...] = ()
        if ocr_mode is OCRMode.AUTO and not ocr_available:
            warnings = ("RapidOCR unavailable; AUTO mode used native text extraction only",)
        elif auto_ocr_pages == ():
            warnings = ("AUTO page classifier found no pages requiring OCR",)
        elif auto_ocr_pages:
            warnings = (
                "AUTO page classifier selected OCR candidates: "
                + ",".join(str(page) for page in auto_ocr_pages),
            )
        return LayoutDocumentV2(
            format=doc_format,
            mime_type=self._legacy._normalized_mime(doc_format, mime_type),
            source_sha256=hashlib.sha256(content).hexdigest(),
            blocks=tuple(blocks),
            page_count=page_count if doc_format is DocumentFormat.PDF else None,
            slide_count=page_count if doc_format is DocumentFormat.PPTX else None,
            parser_profile="docling-layout-v2",
            ocr_profile=(
                self._ocr_provider.profile
                if effective_ocr_mode is not OCRMode.OFF
                else None
            ),
            warnings=warnings,
        )

    def _make_converter(self, ocr_mode: OCRMode) -> Any:
        if self._converter_factory is not None:
            return self._converter_factory(ocr_mode)
        try:
            converter_module = importlib.import_module("docling.document_converter")
        except ImportError as exc:
            raise UnsupportedDocumentError(
                "structure-v2 parsing requires the optional 'docling' dependency"
            ) from exc
        document_converter = getattr(converter_module, "DocumentConverter", None)
        if document_converter is None:
            raise UnsupportedDocumentError("installed Docling does not expose DocumentConverter")
        if ocr_mode is OCRMode.OFF:
            # Docling's PdfPipelineOptions enables OCR by default. Constructing a
            # bare DocumentConverter therefore still initializes an OCR engine for
            # native PDFs. Override only the PDF format so DOCX/PPTX keep their
            # normal Docling format options.
            try:
                models = importlib.import_module("docling.datamodel.base_models")
                options = importlib.import_module("docling.datamodel.pipeline_options")
                pdf_format_option = converter_module.PdfFormatOption
                pipeline = options.PdfPipelineOptions()
                pipeline.do_ocr = False
                return document_converter(
                    format_options={
                        models.InputFormat.PDF: pdf_format_option(
                            pipeline_options=pipeline
                        )
                    }
                )
            except (AttributeError, ImportError) as exc:
                raise UnsupportedDocumentError(
                    "installed Docling build cannot explicitly disable PDF OCR"
                ) from exc
        try:
            models = importlib.import_module("docling.datamodel.base_models")
            options = importlib.import_module("docling.datamodel.pipeline_options")
            pdf_format_option = converter_module.PdfFormatOption
            pipeline = options.PdfPipelineOptions()
            pipeline.do_ocr = True
            rapid_options = options.RapidOcrOptions()
            # Docling performs native text extraction first and does not replace a
            # complete text layer with full-page OCR in AUTO mode.
            if hasattr(rapid_options, "force_full_page_ocr"):
                rapid_options.force_full_page_ocr = ocr_mode is OCRMode.FORCE
            pipeline.ocr_options = rapid_options
            return document_converter(
                format_options={
                    models.InputFormat.PDF: pdf_format_option(pipeline_options=pipeline)
                }
            )
        except (AttributeError, ImportError) as exc:
            raise OCRProviderUnavailableError(
                "installed Docling/RapidOCR build cannot initialize the OCR runtime"
            ) from exc

    @staticmethod
    def _docling_blocks(
        document: Any,
        doc_format: DocumentFormat,
        ocr_mode: OCRMode,
        *,
        auto_ocr_pages: tuple[int, ...] | None = None,
    ) -> list[LayoutBlock]:
        iterator = getattr(document, "iterate_items", None)
        if not callable(iterator):
            raise UnsupportedDocumentError(
                "installed Docling document does not expose iterate_items()"
            )
        blocks: list[LayoutBlock] = []
        heading_path: list[str] = []
        offset = 0
        for order, entry in enumerate(iterator()):
            item = entry[0] if isinstance(entry, tuple) else entry
            text = _docling_item_text(item, document).strip()
            if not text:
                continue
            kind = _docling_block_kind(getattr(item, "label", None))
            if kind in {BlockKind.TITLE, BlockKind.HEADING}:
                heading_path = [text]
            page_no, bbox = _docling_provenance(item, document)
            slide_no = page_no if doc_format is DocumentFormat.PPTX else None
            if slide_no is not None:
                page_no = None
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            ref = str(getattr(item, "self_ref", "")).strip()
            block_key = ref or f"block-{order}-{digest[:12]}"
            # Docling's final DoclingDocument provenance identifies the page and
            # coordinates but does not consistently retain a per-item OCR flag.
            # The preflight classifier is therefore the authoritative provenance
            # for AUTO candidates; FORCE marks every PDF block. Preserve a native
            # provider flag when a Docling version exposes one.
            ocr_used = bool(getattr(item, "ocr", False)) or (
                doc_format is DocumentFormat.PDF
                and (
                    ocr_mode is OCRMode.FORCE
                    or (
                        ocr_mode is OCRMode.AUTO
                        and auto_ocr_pages is not None
                        and page_no in auto_ocr_pages
                    )
                )
            )
            confidence = _normalized_confidence(getattr(item, "confidence", None))
            blocks.append(
                LayoutBlock(
                    block_key=block_key,
                    kind=kind,
                    text=text,
                    page_no=page_no,
                    slide_no=slide_no,
                    bbox=bbox,
                    reading_order=order,
                    heading_path=tuple(heading_path),
                    char_start=offset,
                    char_end=offset + len(text),
                    ocr_used=ocr_used,
                    ocr_confidence=confidence if ocr_used else None,
                    content_sha256=digest,
                )
            )
            offset += len(text) + 2
        return blocks


def _layout_block(block: SourceBlock, reading_order: int) -> LayoutBlock:
    digest = block.content_sha256 or hashlib.sha256(block.text.encode("utf-8")).hexdigest()
    location = block.page or block.slide or 0
    key = block.block_key or f"block-{location}-{reading_order}-{digest[:12]}"
    return LayoutBlock(
        block_key=key,
        kind=block.kind,
        text=block.text,
        page_no=block.page,
        slide_no=block.slide,
        bbox=block.bbox,
        reading_order=block.reading_order if block.reading_order is not None else reading_order,
        heading_path=block.heading_path,
        char_start=block.char_start,
        char_end=block.char_end,
        ocr_used=block.ocr_used,
        ocr_confidence=block.ocr_confidence,
        content_sha256=digest,
    )


def _remap_single_page_blocks(
    blocks: list[LayoutBlock], requested_page_no: int
) -> list[LayoutBlock]:
    """Preserve the source page number for a page-range Docling conversion.

    Docling currently keeps original page numbers for ``page_range``.  Some
    compatible converter test doubles (and older builds) renumber a one-page
    result to page 1, so remap only that unambiguous case.
    """

    observed_pages = {block.page_no for block in blocks if block.page_no is not None}
    if observed_pages not in ({1}, {requested_page_no}):
        raise UnsafeDocumentError("page OCR returned ambiguous source provenance")
    return [
        replace(
            block,
            page_no=requested_page_no,
            ocr_used=True,
            block_key=f"page-{requested_page_no}-ocr-{index}-{block.content_sha256[:12]}",
        )
        for index, block in enumerate(blocks)
    ]


def _merge_page_blocks(
    native_blocks: list[LayoutBlock],
    ocr_blocks: list[LayoutBlock],
    ocr_pages: tuple[int, ...],
) -> list[LayoutBlock]:
    """Replace only selected native pages with OCR blocks and reflow offsets."""

    selected = set(ocr_pages)
    by_page = {block.page_no for block in ocr_blocks if block.page_no is not None}
    missing = selected - by_page
    if missing:
        raise UnsafeDocumentError(
            "OCR produced no extractable text for selected pages: "
            + ",".join(str(page) for page in sorted(missing))
        )
    merged = [block for block in native_blocks if block.page_no not in selected]
    merged.extend(ocr_blocks)
    merged.sort(
        key=lambda block: (
            block.page_no if block.page_no is not None else 2**31,
            block.reading_order,
            block.block_key,
        )
    )
    result: list[LayoutBlock] = []
    offset = 0
    for order, block in enumerate(merged):
        result.append(
            replace(
                block,
                reading_order=order,
                char_start=offset,
                char_end=offset + len(block.text),
            )
        )
        offset += len(block.text) + 2
    return result


def _docling_item_text(item: Any, document: Any) -> str:
    text = getattr(item, "text", None)
    if isinstance(text, str):
        return text
    exporter = getattr(item, "export_to_markdown", None)
    if callable(exporter):
        try:
            exported = exporter(doc=document)
        except TypeError:
            exported = exporter()
        return exported if isinstance(exported, str) else ""
    return ""


def _pdf_pages_requiring_ocr(content: bytes) -> tuple[int, ...] | None:
    """Classify PDF pages for AUTO OCR using native text quality.

    ``None`` means the lightweight classifier itself could not inspect the PDF,
    in which case Docling keeps control. An empty tuple explicitly disables OCR.
    """

    try:
        pypdf = importlib.import_module("pypdf")
        reader = pypdf.PdfReader(io.BytesIO(content), strict=True)
    except Exception:
        return None
    candidates: list[int] = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            native_text = (page.extract_text() or "").strip()
        except Exception:
            candidates.append(page_no)
            continue
        if len(native_text) < 40 or _text_gibberish_ratio(native_text) > 0.30:
            candidates.append(page_no)
    return tuple(candidates)


def _text_gibberish_ratio(text: str) -> float:
    meaningful = [character for character in text if not character.isspace()]
    if not meaningful:
        return 1.0
    suspicious = sum(
        character == "\ufffd"
        or ord(character) < 32
        or (0xE000 <= ord(character) <= 0xF8FF)
        for character in meaningful
    )
    return suspicious / len(meaningful)


def _docling_block_kind(label: Any) -> BlockKind:
    value = str(getattr(label, "value", label) or "").casefold()
    if value in {"title", "document_title"}:
        return BlockKind.TITLE
    if value in {"section_header", "heading"}:
        return BlockKind.HEADING
    if "list" in value:
        return BlockKind.LIST
    if "table" in value:
        return BlockKind.TABLE
    if "code" in value:
        return BlockKind.CODE
    if "caption" in value:
        return BlockKind.CAPTION
    if "header" in value:
        return BlockKind.HEADER
    if "footer" in value:
        return BlockKind.FOOTER
    if "picture" in value or "image" in value:
        return BlockKind.IMAGE_OCR
    return BlockKind.PARAGRAPH


def _docling_page_count(document: Any) -> int | None:
    pages = getattr(document, "pages", None)
    if isinstance(pages, dict):
        return len(pages)
    if isinstance(pages, (list, tuple)):
        return len(pages)
    return None


def _docling_provenance(
    item: Any, document: Any
) -> tuple[int | None, NormalizedBBox | None]:
    provenance = getattr(item, "prov", None) or ()
    if not provenance:
        return None, None
    prov = provenance[0]
    page_no = getattr(prov, "page_no", None)
    if not isinstance(page_no, int) or page_no < 1:
        page_no = None
    raw_bbox = getattr(prov, "bbox", None)
    if raw_bbox is None or page_no is None:
        return page_no, None
    pages = getattr(document, "pages", {})
    page = pages.get(page_no) if isinstance(pages, dict) else None
    size = getattr(page, "size", None)
    width = float(getattr(size, "width", 0.0) or 0.0)
    height = float(getattr(size, "height", 0.0) or 0.0)
    if width <= 0 or height <= 0:
        return page_no, None
    try:
        left = float(raw_bbox.l) / width
        right = float(raw_bbox.r) / width
        top = float(raw_bbox.t) / height
        bottom = float(raw_bbox.b) / height
    except (AttributeError, TypeError, ValueError):
        return page_no, None
    return page_no, NormalizedBBox(
        left=max(0.0, min(1.0, min(left, right))),
        top=max(0.0, min(1.0, min(top, bottom))),
        right=max(0.0, min(1.0, max(left, right))),
        bottom=max(0.0, min(1.0, max(top, bottom))),
    )


def _normalized_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence > 1.0 and confidence <= 100.0:
        confidence /= 100.0
    return max(0.0, min(1.0, confidence))


def _is_ocr_runtime_failure(exc: BaseException) -> bool:
    """Recognize optional OCR/runtime failures without exposing vendor details.

    Docling may wrap the original import or session-initialization exception, so
    walk the exception chain and match only dependency/backend-specific signals.
    Malformed input remains an ``UnsafeDocumentError``.
    """

    current: BaseException | None = exc
    seen: set[int] = set()
    markers = (
        "onnxruntime",
        "rapidocr",
        "inferencesession",
        "execution provider",
    )
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ImportError, ModuleNotFoundError)):
            return True
        if any(marker in str(current).casefold() for marker in markers):
            return True
        current = current.__cause__ or current.__context__
    return False


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _update_heading_path(current: list[str], text: str, style: str) -> list[str]:
    match = re.search(r"(\d+)$", style)
    level = int(match.group(1)) if match else 1
    return [*current[: level - 1], text]
