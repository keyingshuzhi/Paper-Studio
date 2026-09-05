"""Local report export helpers.

The Web/Electron process deliberately owns exports so reports never have to be
sent to a third-party conversion service.  The DOCX writer uses a small,
standards-compliant OOXML package rather than a heavyweight office runtime.
PDF output uses ReportLab's built-in Chinese CID font when that optional app
dependency is installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape as xml_escape
import os
from pathlib import Path
import re
import zipfile
from typing import Iterable


def _safe_stem(title: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title).strip(" ._")
    return (value or "research-report")[:96]


def _plain(value: str) -> str:
    """Keep report text readable when an export format has no Markdown parser."""
    value = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return value.replace("|", "  ").strip()


def _paragraphs(markdown: str) -> Iterable[tuple[str, str]]:
    """Yield (style, text) pairs for the intentionally simple report layout."""
    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or re.fullmatch(r"-{3,}", line):
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            yield ("Title" if level == 1 else f"Heading{level - 1}",
                   _plain(heading.group(2)))
        elif line.startswith(">"):
            yield ("Quote", _plain(line[1:].strip()))
        elif re.match(r"^[-*+]\s+", line):
            yield ("List", _plain(re.sub(r"^[-*+]\s+", "", line)))
        elif re.match(r"^\d+\.\s+", line):
            yield ("List", _plain(re.sub(r"^\d+\.\s+", "", line)))
        else:
            yield ("Normal", _plain(line))


def export_docx(markdown: str, title: str, destination: Path) -> Path:
    """Write a compact, Word-compatible report without external conversion."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Use the native CJK family for the two desktop targets. Office will use
    # its normal fallback when a user has removed that system font.
    east_asian_font = "Microsoft YaHei" if os.name == "nt" else "PingFang SC"

    body: list[str] = []
    for style, text in _paragraphs(markdown):
        if not text:
            continue
        safe = xml_escape(text)
        body.append(
            '<w:p><w:pPr><w:pStyle w:val="%s"/></w:pPr>'
            '<w:r><w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos" '
            'w:eastAsia="%s"/><w:lang w:eastAsia="zh-CN"/>'
            '</w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (style, east_asian_font, safe)
        )
    if not body:
        body.append(
            '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
            '<w:r><w:t>研究报告</w:t></w:r></w:p>'
        )

    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>%s<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body>
</w:document>""" % "".join(body)
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos" w:eastAsia="Microsoft YaHei"/><w:lang w:eastAsia="zh-CN"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:color w:val="000000"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:spacing w:before="260" w:after="130"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="000000"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/><w:pPr><w:spacing w:before="200" w:after="100"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="000000"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:pPr><w:ind w:left="480"/><w:spacing w:after="100"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="List"><w:name w:val="List"/><w:pPr><w:ind w:left="420" w:hanging="210"/></w:pPr></w:style>
</w:styles>"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>"""
    document_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
    core = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>%s</dc:title><dc:creator>Paper Studio</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>""" % (xml_escape(title), created, created)
    app = """<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Paper Studio</Application></Properties>"""
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app)
    return destination


def export_pdf(markdown: str, title: str, destination: Path) -> Path:
    """Render an offline, selectable-text PDF with a Chinese-capable font."""
    try:
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as error:  # pragma: no cover - package check is external
        raise RuntimeError("PDF 导出组件未安装，请重新安装或更新 Paper Studio") from error

    destination.parent.mkdir(parents=True, exist_ok=True)
    font_name = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    except Exception:  # already registered on later exports
        pass
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("PaperStudio", parent=styles["BodyText"],
                            fontName=font_name, fontSize=10.5, leading=17,
                            alignment=TA_LEFT, spaceAfter=7)
    title_style = ParagraphStyle("PaperStudioTitle", parent=normal,
                                 fontSize=20, leading=28, spaceAfter=18)
    h1 = ParagraphStyle("PaperStudioH1", parent=normal,
                        fontSize=15, leading=23, spaceBefore=16, spaceAfter=8)
    h2 = ParagraphStyle("PaperStudioH2", parent=normal,
                        fontSize=12.5, leading=20, spaceBefore=12, spaceAfter=6)
    story = [Paragraph(xml_escape(_plain(title) or "研究报告"), title_style)]
    first_title = True
    for kind, text in _paragraphs(markdown):
        if kind == "Title" and first_title:
            first_title = False
            continue
        style = h1 if kind == "Heading1" else h2 if kind == "Heading2" else normal
        prefix = "• " if kind == "List" else ""
        if kind == "Quote":
            prefix = "注："
        story.append(Paragraph(xml_escape(prefix + text).replace("\n", "<br/>"), style))
    story.append(Spacer(1, 6 * mm))
    document = SimpleDocTemplate(str(destination), pagesize=A4,
                                 rightMargin=19 * mm, leftMargin=19 * mm,
                                 topMargin=18 * mm, bottomMargin=18 * mm,
                                 title=title, author="Paper Studio")
    document.build(story)
    return destination


def export_name(title: str, extension: str) -> str:
    return _safe_stem(title) + extension
