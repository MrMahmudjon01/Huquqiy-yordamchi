"""Murojaatni Word (.docx) hujjatiga aylantirish."""
from __future__ import annotations

import io
from datetime import date

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

from .ai import FilePart

TITLES = {"ariza": "ARIZA", "shikoyat": "SHIKOYAT", "taklif": "TAKLIF"}


def _p(doc, text="", *, align=None, bold=False, size=14, indent=False, space_after=6):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    fmt = p.paragraph_format
    fmt.space_after = Pt(space_after)
    if indent:
        fmt.first_line_indent = Cm(1.25)
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    return p


def build_docx(data: dict, images: list[FilePart]) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(14)
    for s in doc.sections:
        s.left_margin, s.right_margin = Cm(3), Cm(1.5)
        s.top_margin, s.bottom_margin = Cm(2), Cm(2)

    right = WD_ALIGN_PARAGRAPH.RIGHT
    _p(doc, data.get("kimga", ""), align=right, bold=True)
    kimdan = [x for x in [data.get("fio"), data.get("manzil"), data.get("telefon") and f"Tel: {data['telefon']}"] if x]
    _p(doc, "\n".join(kimdan) or "[F.I.Sh., yashash manzili, telefon]", align=right, space_after=18)

    _p(doc, TITLES.get(data.get("turi", ""), "MUROJAAT"), align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, space_after=4)
    if data.get("mavzu"):
        _p(doc, data["mavzu"], align=WD_ALIGN_PARAGRAPH.CENTER, size=12, space_after=12)

    for para in [x.strip() for x in data.get("matn", "").split("\n") if x.strip()]:
        _p(doc, para, align=WD_ALIGN_PARAGRAPH.JUSTIFY, indent=True)

    ilovalar = [x for x in data.get("ilovalar", []) if x]
    if ilovalar:
        _p(doc, "Ilova:", bold=True, space_after=2)
        for i, item in enumerate(ilovalar, 1):
            _p(doc, f"{i}. {item}", space_after=2)

    _p(doc, space_after=12)
    _p(doc, f"Sana: {data.get('sana') or date.today().strftime('%d.%m.%Y')}")
    _p(doc, f"Imzo: ______________   {data.get('fio') or '[F.I.Sh.]'}")

    for i, img in enumerate(images, 1):
        doc.add_page_break()
        _p(doc, f"{i}-ilova", align=right, bold=True)
        try:
            doc.add_picture(io.BytesIO(img.data), width=Cm(15))
        except Exception:
            _p(doc, f"[{img.name} faylini hujjatga joylab bo'lmadi]")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
