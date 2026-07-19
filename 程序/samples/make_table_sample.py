"""生成带表格的测试 PDF（用于开发/回归表格逐单元格翻译）。

用法（在 程序/ 目录下）：python samples/make_table_sample.py
输出：samples/sample_table.pdf
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

OUT = Path(__file__).resolve().parent / "sample_table.pdf"

INTRO = ("We evaluated three instructional conditions across two studies. "
         "The following table summarizes participant demographics and the "
         "primary outcome measures for each condition.")
AFTER = ("As shown above, the robot failure condition produced the largest "
         "gain in conceptual understanding while reporting lower social "
         "pressure than the peer failure condition.")

DATA = [
    ["Condition", "Participants", "Mean age", "Conceptual gain", "Reported pressure"],
    ["Robot failure", "46 students", "13.6 years", "High improvement", "Low"],
    ["Peer failure", "45 students", "13.8 years", "Moderate improvement", "High"],
    ["Direct instruction", "44 students", "13.7 years", "Small improvement", "Moderate"],
    ["Total sample", "135 students", "13.7 years", "—", "—"],
]


def build():
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            leftMargin=54, rightMargin=54,
                            topMargin=60, bottomMargin=60)
    table = Table(DATA, colWidths=[95, 80, 62, 105, 88])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#888888")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    doc.build([
        Paragraph("Table Handling Test Document", styles["Title"]),
        Spacer(1, 10),
        Paragraph(INTRO, styles["BodyText"]),
        Spacer(1, 14),
        Paragraph("Table 1. Participant demographics by condition.",
                  styles["BodyText"]),
        Spacer(1, 6),
        table,
        Spacer(1, 14),
        Paragraph(AFTER, styles["BodyText"]),
    ])
    print(f"已生成：{OUT}（{len(DATA)} 行 × {len(DATA[0])} 列表格）")


if __name__ == "__main__":
    build()
