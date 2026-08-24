"""Generate the project-authored unstructured-v1 control corpus.

Run this script with the bundled artifact Python runtime documented in the
dataset README.  The generated files contain synthetic policy text only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
GOLD = ROOT / "gold"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reset_output() -> None:
    for directory in (CORPUS, GOLD):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)


def make_native_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    pdf.setTitle("Native access policy")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(72, 780, "Production Access Policy")
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 742, "A production grant lasts no more than 30 days.")
    pdf.drawString(72, 720, "Manager and system owner approval are both required.")
    pdf.save()


def make_two_column_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(54, 790, "Incident Response Handbook")
    pdf.setFont("Helvetica", 11)
    left = [
        "SEV-1 response target: 5 minutes.",
        "Open an incident channel within 10 minutes.",
    ]
    right = [
        "SEV-1 and SEV-2 require a blameless review.",
        "The review is due within three workdays.",
    ]
    for index, line in enumerate(left):
        pdf.drawString(54, 750 - index * 24, line)
    for index, line in enumerate(right):
        pdf.drawString(310, 750 - index * 24, line)
    pdf.save()


def make_table_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(72, 790, "Retention Matrix")
    rows = (
        ("Application logs", "30 days"),
        ("Security audit logs", "365 days"),
        ("Financial evidence", "7 years"),
    )
    x0, y0, width, row_height = 72, 742, 430, 30
    pdf.setFont("Helvetica", 11)
    for row_index, row in enumerate(rows):
        y = y0 - row_index * row_height
        pdf.rect(x0, y - row_height, width, row_height)
        pdf.line(x0 + 280, y - row_height, x0 + 280, y)
        pdf.drawString(x0 + 8, y - 20, row[0])
        pdf.drawString(x0 + 290, y - 20, row[1])
    pdf.save()


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    raise RuntimeError(
        "No CJK font found; install Noto Sans CJK or run on the project Windows host"
    )


def scan_image(lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (1654, 1169), "white")
    draw = ImageDraw.Draw(image)
    font = load_font(48)
    for index, line in enumerate(lines):
        draw.text((110, 260 + index * 110), line, font=font, fill="black")
    return image


def make_scanned_pdf(path: Path, lines: list[str]) -> None:
    scan_image(lines).save(path, "PDF", resolution=150.0)


def make_mixed_pdf(path: Path, native: Path, scanned: Path) -> None:
    writer = PdfWriter()
    for source in (native, scanned):
        reader = PdfReader(str(source))
        for page in reader.pages:
            writer.add_page(page)
    with path.open("wb") as stream:
        writer.write(stream)


def make_docx(path: Path) -> None:
    document = Document()
    document.add_heading("差旅与费用报销制度", level=1)
    document.add_paragraph("国内差旅至少提前 3 个工作日提交申请。")
    document.add_heading("申请材料", level=2)
    document.add_paragraph("出差目的与预算", style="List Bullet")
    document.add_paragraph("直属负责人审批", style="List Bullet")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "城市级别"
    table.cell(0, 1).text = "住宿上限"
    table.cell(1, 0).text = "一线城市"
    table.cell(1, 1).text = "800 元/晚"
    document.save(path)


def make_pptx(path: Path) -> None:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[1])
    first.shapes.title.text = "企业发布流程"
    first.placeholders[1].text = "灰度 5% 流量，至少观察 30 分钟"
    second = presentation.slides.add_slide(presentation.slide_layouts[5])
    second.shapes.title.text = "回滚门槛"
    box = second.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(5.8), Inches(1.3))
    box.text_frame.text = "错误率增加 1 个百分点，或 P95 延迟超标 10 分钟"
    table = second.shapes.add_table(
        2, 2, Inches(0.8), Inches(3.5), Inches(6), Inches(1.2)
    ).table
    table.cell(0, 0).text = "检查项"
    table.cell(0, 1).text = "动作"
    table.cell(1, 0).text = "P95 延迟"
    table.cell(1, 1).text = "停止扩量并回滚"
    presentation.save(path)


def rewrite_zip(source: Path, target: Path, additions: dict[str, bytes]) -> None:
    with (
        zipfile.ZipFile(source) as reader,
        zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as writer,
    ):
        for info in reader.infolist():
            writer.writestr(info, reader.read(info.filename))
        for name, content in additions.items():
            writer.writestr(name, content)


def make_encrypted_pdf(source: Path, target: Path) -> None:
    reader = PdfReader(str(source))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("fixture-password")
    with target.open("wb") as stream:
        writer.write(stream)


def write_gold(cases: list[dict[str, object]]) -> None:
    for case in cases:
        (GOLD / f"{case['case_id']}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    with (ROOT / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False) + "\n")


def main() -> None:
    reset_output()
    native = CORPUS / "native-access.pdf"
    two_column = CORPUS / "two-column-incident.pdf"
    table_pdf = CORPUS / "table-retention.pdf"
    scanned_zh = CORPUS / "scanned-zh.pdf"
    scanned_zh_en = CORPUS / "scanned-zh-en.pdf"
    mixed = CORPUS / "mixed-native-scanned.pdf"
    docx = CORPUS / "structured-policy.docx"
    pptx = CORPUS / "release-policy.pptx"
    markdown = CORPUS / "markdown-regression.md"
    text = CORPUS / "text-regression.txt"

    make_native_pdf(native)
    make_two_column_pdf(two_column)
    make_table_pdf(table_pdf)
    make_scanned_pdf(scanned_zh, ["扫描件安全制度", "普通生产权限最长 30 天"])
    make_scanned_pdf(scanned_zh_en, ["设备遗失 Device Lost", "30 分钟内通知 IT 服务台"])
    make_mixed_pdf(mixed, native, scanned_zh)
    make_docx(docx)
    make_pptx(pptx)
    markdown.write_text(
        "# 知识发布\n\n只有已发布版本可以进入 RAG。\n", encoding="utf-8"
    )
    text.write_text("软删除知识文档可以在 30 天内恢复。\n", encoding="utf-8")

    make_encrypted_pdf(native, CORPUS / "negative-encrypted.pdf")
    rewrite_zip(
        docx, CORPUS / "negative-macro.docx", {"word/vbaProject.bin": b"fixture"}
    )
    rewrite_zip(
        docx, CORPUS / "negative-zip-bomb.docx", {"word/bomb.bin": b"0" * 2_000_000}
    )
    with zipfile.ZipFile(CORPUS / "negative-path-traversal.docx", "w") as archive:
        archive.writestr("../escape.txt", "fixture")

    cases: list[dict[str, object]] = [
        {
            "case_id": "native-pdf",
            "file": native.name,
            "format": "pdf",
            "ocr_mode": "OFF",
            "expect": "success",
            "page_count": 1,
            "phrases": ["production grant", "30 days"],
            "required_block_kinds": ["heading", "paragraph"],
        },
        {
            "case_id": "two-column-pdf",
            "file": two_column.name,
            "format": "pdf",
            "ocr_mode": "OFF",
            "expect": "success",
            "page_count": 1,
            "phrases": ["SEV-1 response", "blameless review"],
        },
        {
            "case_id": "table-pdf",
            "file": table_pdf.name,
            "format": "pdf",
            "ocr_mode": "OFF",
            "expect": "success",
            "page_count": 1,
            "phrases": ["Security audit logs", "365 days"],
            "required_block_kinds": ["heading", "table"],
        },
        {
            "case_id": "scanned-zh",
            "file": scanned_zh.name,
            "format": "pdf",
            "ocr_mode": "FORCE",
            "expect": "success",
            "page_count": 1,
            "ocr_page_count": 1,
            "reference_text": "扫描件安全制度普通生产权限最长30天",
        },
        {
            "case_id": "scanned-zh-en",
            "file": scanned_zh_en.name,
            "format": "pdf",
            "ocr_mode": "FORCE",
            "expect": "success",
            "page_count": 1,
            "ocr_page_count": 1,
            "reference_text": "设备遗失DeviceLost30分钟内通知IT服务台",
        },
        {
            "case_id": "mixed-pdf",
            "file": mixed.name,
            "format": "pdf",
            "ocr_mode": "AUTO",
            "expect": "success",
            "page_count": 2,
            "ocr_page_count": 1,
            "phrases": ["Production Access Policy", "普通生产权限"],
        },
        {
            "case_id": "docx-structure",
            "file": docx.name,
            "format": "docx",
            "ocr_mode": "OFF",
            "expect": "success",
            "phrases": ["差旅与费用报销制度", "800 元/晚"],
            "required_block_kinds": ["heading", "list", "table"],
        },
        {
            "case_id": "pptx-structure",
            "file": pptx.name,
            "format": "pptx",
            "ocr_mode": "OFF",
            "expect": "success",
            "slide_count": 2,
            "phrases": ["企业发布流程", "停止扩量并回滚"],
            "required_block_kinds": ["title", "table"],
        },
        {
            "case_id": "markdown-regression",
            "file": markdown.name,
            "format": "markdown",
            "ocr_mode": "OFF",
            "expect": "success",
            "phrases": ["只有已发布版本"],
        },
        {
            "case_id": "text-regression",
            "file": text.name,
            "format": "txt",
            "ocr_mode": "OFF",
            "expect": "success",
            "phrases": ["30 天内恢复"],
        },
        {
            "case_id": "encrypted-pdf",
            "file": "negative-encrypted.pdf",
            "format": "pdf",
            "ocr_mode": "OFF",
            "expect": "failure",
        },
        {
            "case_id": "macro-docx",
            "file": "negative-macro.docx",
            "format": "docx",
            "ocr_mode": "OFF",
            "expect": "failure",
        },
        {
            "case_id": "zip-bomb-docx",
            "file": "negative-zip-bomb.docx",
            "format": "docx",
            "ocr_mode": "OFF",
            "expect": "failure",
        },
        {
            "case_id": "path-traversal-docx",
            "file": "negative-path-traversal.docx",
            "format": "docx",
            "ocr_mode": "OFF",
            "expect": "failure",
        },
    ]
    for case in cases:
        case["sha256"] = sha256(CORPUS / str(case["file"]))
        case["license"] = "project-authored-test-fixture"
    write_gold(cases)
    print(
        json.dumps({"generated": len(cases), "corpus": str(CORPUS)}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
