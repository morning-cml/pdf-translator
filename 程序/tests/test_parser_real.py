"""真实论文解析的结构性回归（改内核后最容易被打破的一批不变量）。"""
import pytest


def _translatable(layouts):
    return [b for L in layouts for b in L.blocks if b.translatable]


def test_page_count(paper_layouts):
    assert len(paper_layouts) == 15


def test_no_overlapping_translatable_blocks(paper_layouts):
    """可译块两两不重叠——重叠会导致译文叠印（历史上出过两次）。"""
    bad = []
    for L in paper_layouts:
        t = [b for b in L.blocks if b.translatable]
        for i in range(len(t)):
            for j in range(i + 1, len(t)):
                a, c = t[i], t[j]
                if (min(a.x1, c.x1) - max(a.x0, c.x0) > 2
                        and min(a.bottom, c.bottom) - max(a.top, c.top) > 2):
                    bad.append((L.page_index + 1, a.text[:30], c.text[:30]))
    assert not bad, f"发现重叠可译块：{bad[:3]}"


def test_block_count_in_expected_range(paper_layouts):
    n = len(_translatable(paper_layouts))
    assert 120 <= n <= 145, f"可译块数 {n} 偏离预期区间，解析行为可能已改变"


def test_headings_detected_with_bold(paper_layouts):
    bold = [b for b in _translatable(paper_layouts) if b.bold]
    assert len(bold) >= 30, "粗体标题识别数量异常（章节层级会丢失）"


def test_red_section_heading_keeps_color(paper_layouts):
    """DISCUSSION 等红色大标题必须独立成块并保留红色。"""
    reds = [b for b in _translatable(paper_layouts)
            if b.color[0] > 0.4 and b.color[1] < 0.3 and b.color[2] < 0.3]
    assert reds, "未捕获到任何红色标题块"
    assert any(b.text.strip().upper() == "DISCUSSION" for b in reds)


def test_references_excluded_from_translation(paper_layouts):
    """参考文献条目区应被标记为不译（保留原版精排）。"""
    refs = [b for L in paper_layouts for b in L.blocks
            if not b.translatable and len(b.text) > 200]
    assert refs, "参考文献区未被识别为免译"
    joined = " ".join(b.text for b in refs)
    assert "Vanlehn" in joined or "Kapur" in joined


def test_last_page_is_two_columns(paper_layouts):
    """p14 曾因分栏阈值过严被拧成全宽乱序，此处锁死修复。"""
    blocks = [b for b in paper_layouts[13].blocks if b.translatable]
    mid = paper_layouts[13].width / 2
    assert any(b.x1 <= mid + 20 for b in blocks), "缺左栏"
    assert any(b.x0 >= mid - 20 for b in blocks), "缺右栏"
    full = [b for b in blocks if b.x0 < mid - 60 and b.x1 > mid + 60
            and b.top > 50]
    assert not full, "末页出现跨栏全宽块（分栏检测回归）"


def test_inline_formulas_detected(paper_layouts):
    total = sum(len(b.formulas) for b in _translatable(paper_layouts))
    assert total >= 30, f"行内公式仅检出 {total} 处，保护机制可能失效"


def test_cross_column_pairing(paper_layouts):
    """跨栏/跨页断句配对应命中若干组，且不得配到页脚。"""
    from src.pipeline import _make_units
    blocks = _translatable(paper_layouts)
    units = _make_units(paper_layouts, blocks)
    pairs = [u for u in units if len(u) > 1]
    assert 5 <= len(pairs) <= 30, f"配对组数异常：{len(pairs)}"
    assert sum(len(u) for u in units) == len(blocks), "配对不得丢块"
    for a, b in pairs:
        assert "eadu5257" not in a.text and "eadu5257" not in b.text, "误配页脚"
