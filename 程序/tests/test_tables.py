"""表格逐单元格翻译（B1）。

固定不变量：一格 = 一个翻译单元；译文不得越格；短词格不得丢内容
（曾因把目标框夹到"原文文字宽度"导致 "Low" 这类窄词整格空白）。
"""
import subprocess
import sys

import pdfplumber
import pytest

from src.config import load_config
from src.pdf_parser import parse_pdf
from src.pipeline import translate_pdf
from tests.conftest import ROOT

TABLE_PDF = ROOT / "samples" / "sample_table.pdf"


@pytest.fixture(scope="module")
def table_pdf():
    if not TABLE_PDF.exists():
        subprocess.run([sys.executable, "samples/make_table_sample.py"],
                       cwd=str(ROOT), check=True, capture_output=True)
    if not TABLE_PDF.exists():
        pytest.skip("无法生成表格样张")
    return str(TABLE_PDF)


@pytest.fixture(scope="module")
def table_layout(table_pdf):
    return parse_pdf(table_pdf)[0]


def test_cells_detected(table_layout):
    assert len(table_layout.table_cells) >= 20, "5×5 表格应检出约 25 个单元格"


def test_one_block_per_cell(table_layout):
    cells = [b for b in table_layout.blocks if b.cell_rect]
    assert 20 <= len(cells) <= 30
    # 每个单元格块必须落在自己的格内
    for b in cells:
        x0, top, x1, bottom = b.cell_rect
        assert x0 - 2 <= b.x0 and b.x1 <= x1 + 2
        assert top - 2 <= b.top and b.bottom <= bottom + 2


def test_cell_text_not_merged_across_columns(table_layout):
    """整行被合并成一个块 = 表格结构必崩，这里锁死。"""
    for b in table_layout.blocks:
        if b.cell_rect:
            assert "Robot failure 46 students" not in b.text


def test_short_labels_are_translatable(table_layout):
    texts = {b.text.strip(): b.translatable
             for b in table_layout.blocks if b.cell_rect}
    for label in ("Low", "High", "Condition"):
        assert texts.get(label) is True, f"表格短标签 {label!r} 应可译"


def test_pure_symbol_cells_skipped(table_layout):
    dash = [b for b in table_layout.blocks
            if b.cell_rect and b.text.strip() in ("—", "-", "–")]
    assert dash and all(not b.translatable for b in dash), "纯符号格不该翻译"


def test_body_paragraphs_still_normal(table_layout):
    body = [b for b in table_layout.blocks if not b.cell_rect and b.translatable]
    assert any("instructional conditions" in b.text for b in body)


def test_translated_table_keeps_every_cell(table_pdf, tmp_path):
    out = tmp_path / "t.pdf"
    translate_pdf(table_pdf, str(out), load_config(), mock=True)
    with pdfplumber.open(str(out)) as d:
        page = d.pages[0]
        cells_y = (170, 280)
        words = [w for w in page.extract_words() if cells_y[0] < w["top"] < cells_y[1]]
        cjk = [w for w in words if any("一" <= c <= "鿿" for c in w["text"])]
        assert len(cjk) >= 20, "表格内应有约 23 格中文（短词格不得丢）"
        # 最后一列（窄词 Low/High/Moderate 所在）必须有内容
        last_col = [w for w in words if w["x0"] > 425]
        assert len(last_col) >= 4, "窄词单元格丢失（目标框被错误夹到文字宽度）"


def test_translation_stays_inside_cell(table_pdf, tmp_path):
    """译文比原文长时也不得越格串到下一行。"""
    out = tmp_path / "t2.pdf"
    layouts = parse_pdf(table_pdf)
    cells = layouts[0].table_cells
    translate_pdf(table_pdf, str(out), load_config(), mock=True)
    with pdfplumber.open(str(out)) as d:
        words = [w for w in d.pages[0].extract_words()
                 if any("一" <= c <= "鿿" for c in w["text"])
                 and 170 < w["top"] < 280]
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        assert any(c[0] - 3 <= cx <= c[2] + 3 and c[1] - 3 <= cy <= c[3] + 3
                   for c in cells), f"译文 {w['text']!r} 跑到单元格外"
