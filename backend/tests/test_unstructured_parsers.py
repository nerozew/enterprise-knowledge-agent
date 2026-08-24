from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from app.knowledge.errors import (
    OCRProviderUnavailableError,
    UnsafeDocumentError,
    UnsupportedDocumentError,
)
from app.knowledge.parsers import (
    DoclingParserProvider,
    DocumentParser,
    RapidOCRAvailability,
)
from app.knowledge.types import BlockKind, DocumentFormat, OCRMode


def _pptx(*, external_relationship: bool = False) -> bytes:
    presentation = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldSz cx="1000" cy="800"/></p:presentation>'
    )
    slide = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree>'
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="100" y="80"/><a:ext cx="800" cy="120"/>'
        '</a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>安全制度</a:t></a:r></a:p>'
        '</p:txBody></p:sp>'
        '<p:graphicFrame><p:xfrm><a:off x="100" y="240"/><a:ext cx="800" cy="300"/>'
        '</p:xfrm><a:graphic><a:graphicData><a:tbl>'
        '<a:tr><a:tc><a:txBody><a:p><a:r><a:t>角色</a:t></a:r></a:p></a:txBody></a:tc>'
        '<a:tc><a:txBody><a:p><a:r><a:t>权限</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
        '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
        '</p:spTree></p:cSld></p:sld>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="https://example.com/object" TargetMode="External"/>'
        '</Relationships>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)
        if external_relationship:
            archive.writestr("ppt/slides/_rels/slide1.xml.rels", relationships)
    return output.getvalue()


def test_pptx_v1_and_v2_preserve_slide_structure_and_bbox() -> None:
    parser = DocumentParser()
    content = _pptx()
    parsed = parser.parse(
        filename="policy.pptx",
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    assert parsed.format is DocumentFormat.PPTX
    assert [block.kind for block in parsed.blocks] == [BlockKind.TITLE, BlockKind.TABLE]
    assert [block.slide for block in parsed.blocks] == [1, 1]
    assert parsed.blocks[1].text == "角色 | 权限"
    assert parsed.blocks[0].bbox is not None
    assert parsed.blocks[0].bbox.left == pytest.approx(0.1)

    layout = parser.parse_layout(
        filename="policy.pptx", content=content, ocr_mode=OCRMode.OFF
    )
    assert layout.schema_version == 2
    assert layout.slide_count == 1
    assert layout.page_count is None
    assert layout.blocks[0].heading_path == ("安全制度",)
    assert layout.blocks[0].block_key == parsed.blocks[0].block_key
    manifest = json.loads(layout.to_json())
    assert manifest["blocks"][0]["text"] == "安全制度"
    converted = layout.to_parsed_document()
    assert converted.title == "安全制度"
    assert converted.slide_count == 1
    assert converted.to_manifest_dict()["schema_version"] == 2


def test_pptx_rejects_external_relationships() -> None:
    with pytest.raises(UnsafeDocumentError, match="PPTX external relationships"):
        DocumentParser().parse(filename="unsafe.pptx", content=_pptx(external_relationship=True))


def test_legacy_force_ocr_is_explicitly_unavailable() -> None:
    with pytest.raises(UnsupportedDocumentError, match="forced OCR"):
        DocumentParser().parse_layout(
            filename="note.txt", content=b"text", ocr_mode=OCRMode.FORCE
        )


class _UnavailableOCR:
    profile = "rapidocr"

    def is_available(self) -> bool:
        return False


class _AvailableOCR:
    profile = "rapidocr"

    def is_available(self) -> bool:
        return True


def test_docling_auto_allows_native_text_when_rapidocr_is_missing() -> None:
    item = SimpleNamespace(
        text="原生文本",
        label="paragraph",
        self_ref="#/texts/0",
        prov=[],
    )
    document = SimpleNamespace(iterate_items=lambda: iter([(item, 0)]), pages={})
    converter = SimpleNamespace(convert=lambda _: SimpleNamespace(document=document))
    provider = DoclingParserProvider(
        converter_factory=lambda mode: converter,
        ocr_provider=_UnavailableOCR(),
    )
    parsed = provider.parse_layout(
        filename="native.txt", content="原生文本".encode(), ocr_mode=OCRMode.AUTO
    )
    assert parsed.text == "原生文本"
    assert parsed.ocr_profile is None
    assert parsed.warnings == (
        "RapidOCR unavailable; AUTO mode used native text extraction only",
    )


def test_docling_force_ocr_requires_provider_and_maps_provenance() -> None:
    unavailable = DoclingParserProvider(
        converter_factory=lambda mode: pytest.fail("converter must not be created"),
        ocr_provider=_UnavailableOCR(),
    )
    with pytest.raises(UnsupportedDocumentError, match="RapidOCR is unavailable"):
        unavailable.parse_layout(
            filename="scan.pdf", content=b"%PDF-scan", ocr_mode=OCRMode.FORCE
        )

    bbox = SimpleNamespace(l=10, t=20, r=60, b=40)
    item = SimpleNamespace(
        text="扫描证据",
        label="picture",
        self_ref="#/pictures/0",
        prov=[SimpleNamespace(page_no=1, bbox=bbox)],
        ocr=True,
        confidence=96,
    )
    page = SimpleNamespace(size=SimpleNamespace(width=100, height=100))
    document = SimpleNamespace(iterate_items=lambda: iter([(item, 0)]), pages={1: page})
    converter = SimpleNamespace(convert=lambda _: SimpleNamespace(document=document))
    provider = DoclingParserProvider(
        converter_factory=lambda mode: converter,
        ocr_provider=_AvailableOCR(),
    )
    parsed = provider.parse_layout(
        filename="scan.pdf", content=b"%PDF-scan", ocr_mode=OCRMode.FORCE
    )
    assert parsed.page_count == 1
    assert parsed.ocr_profile == "rapidocr"
    assert parsed.blocks[0].kind is BlockKind.IMAGE_OCR
    assert parsed.blocks[0].ocr_confidence == pytest.approx(0.96)
    assert parsed.blocks[0].bbox is not None
    assert parsed.blocks[0].bbox.to_dict() == {
        "left": 0.1,
        "top": 0.2,
        "right": 0.6,
        "bottom": 0.4,
    }


def test_docling_dependency_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = __import__("importlib").import_module

    def fake_import(name: str):
        if name == "docling.document_converter":
            raise ImportError(name)
        return original_import(name)

    monkeypatch.setattr("app.knowledge.parsers.importlib.import_module", fake_import)
    provider = DoclingParserProvider(ocr_provider=_AvailableOCR())
    with pytest.raises(UnsupportedDocumentError, match="optional 'docling'"):
        provider.parse_layout(
            filename="native.txt", content=b"native", ocr_mode=OCRMode.OFF
        )


def test_docling_off_explicitly_disables_only_pdf_ocr() -> None:
    pytest.importorskip("docling")
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter

    provider = DoclingParserProvider(ocr_provider=_UnavailableOCR())
    converter = provider._make_converter(OCRMode.OFF)
    default_converter = DocumentConverter()

    assert converter.format_to_options[InputFormat.PDF].pipeline_options.do_ocr is False
    assert type(converter.format_to_options[InputFormat.DOCX]) is type(
        default_converter.format_to_options[InputFormat.DOCX]
    )
    assert type(converter.format_to_options[InputFormat.PPTX]) is type(
        default_converter.format_to_options[InputFormat.PPTX]
    )


def test_docling_real_provenance_marks_auto_candidate_page_as_ocr() -> None:
    pytest.importorskip("docling_core")
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        DoclingDocument,
        ProvenanceItem,
        Size,
    )

    document = DoclingDocument(name="mixed")
    document.add_page(page_no=1, size=Size(width=100, height=100))
    document.add_page(page_no=2, size=Size(width=100, height=100))
    document.add_text(
        label=DocItemLabel.TEXT,
        text="OCR 识别正文",
        prov=ProvenanceItem(
            page_no=2,
            bbox=BoundingBox(l=10, t=20, r=90, b=50),
            charspan=(0, 8),
        ),
    )

    blocks = DoclingParserProvider._docling_blocks(
        document,
        DocumentFormat.PDF,
        OCRMode.AUTO,
        auto_ocr_pages=(2,),
    )

    assert len(blocks) == 1
    assert blocks[0].page_no == 2
    assert blocks[0].ocr_used is True


def test_rapidocr_probe_requires_cpu_inference_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rapidocr = SimpleNamespace(RapidOCR=lambda: None)

    def missing_runtime(name: str):
        if name == "rapidocr":
            return rapidocr
        raise ImportError(name)

    monkeypatch.setattr("app.knowledge.parsers.importlib.import_module", missing_runtime)
    assert RapidOCRAvailability().is_available() is False

    runtime = SimpleNamespace(InferenceSession=lambda: None)

    def complete_runtime(name: str):
        if name == "rapidocr":
            return rapidocr
        if name == "onnxruntime":
            return runtime
        raise ImportError(name)

    monkeypatch.setattr("app.knowledge.parsers.importlib.import_module", complete_runtime)
    assert RapidOCRAvailability().is_available() is True


def test_docling_ocr_runtime_failure_is_retryable_provider_error() -> None:
    converter = SimpleNamespace(
        convert=lambda _: (_ for _ in ()).throw(ImportError("onnxruntime is not installed"))
    )
    provider = DoclingParserProvider(
        converter_factory=lambda mode: converter,
        ocr_provider=_AvailableOCR(),
    )
    with pytest.raises(OCRProviderUnavailableError, match="inference runtime"):
        provider.parse_layout(
            filename="scan.pdf", content=b"%PDF-scan", ocr_mode=OCRMode.FORCE
        )


def test_docling_auto_page_classifier_disables_or_enables_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bbox = SimpleNamespace(l=10, t=20, r=60, b=40)
    native_item = SimpleNamespace(
        text="原生页面有足够正文",
        label="paragraph",
        self_ref="#/texts/0",
        prov=[SimpleNamespace(page_no=1, bbox=bbox)],
    )
    candidate_native_item = SimpleNamespace(
        text="扫描页原生占位",
        label="paragraph",
        self_ref="#/texts/1",
        prov=[SimpleNamespace(page_no=2, bbox=bbox)],
    )
    ocr_item = SimpleNamespace(
        text="扫描页识别正文",
        label="paragraph",
        self_ref="#/texts/2",
        prov=[SimpleNamespace(page_no=2, bbox=bbox)],
    )
    page = SimpleNamespace(size=SimpleNamespace(width=100, height=100))
    native_document = SimpleNamespace(
        iterate_items=lambda: iter([(native_item, 0), (candidate_native_item, 0)]),
        pages={1: page, 2: page},
    )
    ocr_document = SimpleNamespace(
        iterate_items=lambda: iter([(ocr_item, 0)]), pages={2: page}
    )
    selected_modes: list[OCRMode] = []
    ocr_page_ranges: list[tuple[int, int]] = []

    def converter_factory(mode: OCRMode):
        selected_modes.append(mode)

        def convert(_source, *, page_range=None):
            if mode is OCRMode.FORCE:
                assert page_range is not None
                ocr_page_ranges.append(page_range)
                return SimpleNamespace(document=ocr_document)
            assert page_range is None
            return SimpleNamespace(document=native_document)

        return SimpleNamespace(convert=convert)

    provider = DoclingParserProvider(
        converter_factory=converter_factory,
        ocr_provider=_AvailableOCR(),
    )
    monkeypatch.setattr(
        "app.knowledge.parsers._pdf_pages_requiring_ocr", lambda content: ()
    )
    native = provider.parse_layout(
        filename="native.pdf", content=b"%PDF-native", ocr_mode=OCRMode.AUTO
    )
    assert selected_modes[-1] is OCRMode.OFF
    assert native.ocr_profile is None
    assert native.ocr_page_count == 0
    assert native.blocks[0].ocr_used is False
    assert "no pages requiring OCR" in native.warnings[0]

    monkeypatch.setattr(
        "app.knowledge.parsers._pdf_pages_requiring_ocr", lambda content: (2,)
    )
    mixed = provider.parse_layout(
        filename="mixed.pdf", content=b"%PDF-mixed", ocr_mode=OCRMode.AUTO
    )
    assert selected_modes[-2:] == [OCRMode.OFF, OCRMode.FORCE]
    assert ocr_page_ranges == [(2, 2)]
    assert mixed.ocr_profile == "rapidocr"
    assert mixed.ocr_page_count == 1
    assert mixed.blocks[0].page_no == 1
    assert mixed.blocks[0].ocr_used is False
    assert mixed.blocks[1].page_no == 2
    assert mixed.blocks[1].text == "扫描页识别正文"
    assert mixed.blocks[1].ocr_used is True
    assert mixed.warnings == ("AUTO page classifier selected OCR candidates: 2",)
