"""PDF report generation (ReportLab platypus).

Produces a professional report: branded cover with summary metrics, then a
paginated table with repeating headers, page numbers and a generation stamp.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.models.models import Business


def _esc(value) -> str:
    """HTML-escape any text that flows into a reportlab Paragraph.

    None and empty values render as an em-dash; a missing field must never
    crash generation of a client-facing report.
    """
    import html

    return html.escape(str(value), quote=False) if value not in (None, "") else "—"

_BRAND = "LocalLead Intelligence"
_ACCENT = colors.HexColor("#1d4ed8")
_MUTED = colors.HexColor("#64748b")
_LINE = colors.HexColor("#e2e8f0")


def _header_footer(canvas, doc):
    """Draw page furniture: brand, footer, page numbers."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_MUTED)
    canvas.drawString(15 * mm, 12 * mm, f"{_BRAND} — Business Research Report")
    canvas.drawRightString(A4[0] - 15 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(_LINE)
    canvas.line(15 * mm, 16 * mm, A4[0] - 15 * mm, 16 * mm)
    canvas.restoreState()


def build_pdf_report(out_path: Path, *, search_query: str, category: str,
                     location: str, businesses: list[Business],
                     provider: str) -> None:
    """Write the report PDF to *out_path*."""
    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=20 * mm,
        title="Business Research Report",
        author=_BRAND,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([
        PageTemplate(id="page", frames=[frame], onPage=_header_footer),
    ])

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("BrandH1", parent=styles["Title"], fontSize=20,
                        textColor=_ACCENT, spaceAfter=2 * mm)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10,
                         textColor=_MUTED, alignment=1)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13,
                        spaceBefore=6 * mm, spaceAfter=2 * mm)
    cell = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=7.5, leading=9)
    cell_head = ParagraphStyle("CellHead", parent=styles["Normal"], fontSize=7.5,
                               leading=9, textColor=colors.white)

    story: list = []

    # ---- cover summary ---------------------------------------------------
    story.append(Paragraph(_BRAND, h1))
    story.append(Paragraph("BUSINESS RESEARCH REPORT", sub))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"<b>Search:</b> {_esc(search_query)}", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Category:</b> {_esc(category or '—')} &nbsp;·&nbsp; "
        f"<b>Location:</b> {_esc(location or '—')}", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Generated:</b> {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}",
        styles["Normal"]))
    story.append(Paragraph(
        f"<b>Data source:</b> Google Places API ({provider}) + public business websites",
        styles["Normal"]))
    story.append(Spacer(1, 5 * mm))

    total = len(businesses)
    with_phone = sum(1 for b in businesses if b.phone_normalized or b.phone)
    with_website = sum(1 for b in businesses if b.website_normalized)
    with_email = sum(1 for b in businesses if b.email)
    high_conf = sum(1 for b in businesses if b.email and b.email_confidence == "HIGH")
    summary_rows = [
        ["Total businesses", str(total)],
        ["Businesses with phone", str(with_phone)],
        ["Businesses with website", str(with_website)],
        ["Businesses with email", str(with_email)],
        ["High-confidence emails", str(high_conf)],
    ]
    summary_table = Table(summary_rows, colWidths=[70 * mm, 30 * mm], hAlign="LEFT")
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#334155")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "Contact data is collected only from official business listings and the "
        "businesses' own public websites, respecting robots.txt. Emails are shown "
        "as published by the businesses; no addresses are guessed.", sub))

    # ---- results table ----------------------------------------------------
    story.append(Paragraph("Business Directory", h2))
    header = [Paragraph(f"<b>{h}</b>", cell_head) for h in
              ["Business Name", "Address", "Phone", "Email", "Website"]]
    rows = [header]
    for b in businesses:
        email_cell = "—"
        if b.email:
            email_cell = (
                f"{_esc(b.email)}<br/><font size=6 color='#64748b'>"
                f"{_esc(b.email_confidence or '')}</font>"
            )
        website = b.website or ""
        rows.append([
            Paragraph(_esc(b.name or "Unnamed"), cell),
            Paragraph(_esc(b.address), cell),
            Paragraph(_esc(b.phone_normalized or b.phone), cell),
            Paragraph(email_cell, cell),
            Paragraph(
                _esc(website.replace("https://", "").replace("http://", "")[:60]),
                cell,
            ),
        ])
    table = Table(
        rows,
        colWidths=[38 * mm, 52 * mm, 26 * mm, 34 * mm, 30 * mm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)
    doc.build(story)
