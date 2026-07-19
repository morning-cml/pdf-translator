"""管线纯函数：跨栏配对（T12）、译文拆回、CJK 判定、全文语境。"""
import pytest

from src.pipeline import (_continues, _doc_context, _has_cjk,
                          _split_translation, _unit_text)


class FakeBlock:
    def __init__(self, text, x0=36, top=100, x1=290, bottom=200,
                 translatable=True, bold=False, size=10.0, page_index=0):
        self.text, self.x0, self.top, self.x1, self.bottom = text, x0, top, x1, bottom
        self.translatable, self.bold, self.size = translatable, bold, size
        self.page_index, self.from_ocr, self.translation = page_index, False, None


# ---- _has_cjk ----
@pytest.mark.parametrize("text,expected", [
    ("这是中文", True), ("mixed 中文 text", True),
    ("pure english", False), ("12345 (2025)", False), ("", False), (None, False),
])
def test_has_cjk(text, expected):
    assert _has_cjk(text) is expected


# ---- _continues：判断是否被腰斩 ----
def test_continues_true_when_unterminated_and_lowercase():
    a = FakeBlock("...completed contributions to software and")
    b = FakeBlock("methodology during a summer internship")
    assert _continues(a, b)


@pytest.mark.parametrize("ta,tb", [
    ("A full sentence ends here.", "next paragraph starts"),   # 前句已结束
    ("unterminated tail", "Uppercase start follows"),          # 后句是新句
    ("", "something"), ("something", ""),                      # 空
])
def test_continues_false(ta, tb):
    assert not _continues(FakeBlock(ta), FakeBlock(tb))


# ---- _unit_text：合并 ----
def test_unit_text_single():
    assert _unit_text([FakeBlock("only one")]) == "only one"


def test_unit_text_joins_with_space():
    out = _unit_text([FakeBlock("first half"), FakeBlock("second half")])
    assert out == "first half second half"


def test_unit_text_repairs_hyphenation():
    out = _unit_text([FakeBlock("method-"), FakeBlock("ology works")])
    assert out == "methodology works", "连字符断词应直接拼接"


# ---- _split_translation：按比例在句读处拆回 ----
def test_split_prefers_sentence_boundary():
    tr = "第一部分讲了方法与数据来源。第二部分给出实验结果与讨论内容"
    a, b = _split_translation(tr, 50, 50)
    assert a + b == tr, "拆分不得丢字"
    assert a.endswith("。"), "应优先在句号处切开"


def test_split_without_punctuation_still_lossless():
    tr = "无标点连续文本" * 10
    a, b = _split_translation(tr, 30, 70)
    assert a + b == tr
    assert a and b


def test_split_respects_length_ratio():
    tr = "甲" * 50 + "，" + "乙" * 50
    a, b = _split_translation(tr, 50, 50)
    assert a + b == tr
    assert 0.25 < len(a) / len(tr) < 0.75


def test_split_empty():
    assert _split_translation("", 10, 10) == ("", "")


# ---- _doc_context ----
class FakeLayout:
    def __init__(self, blocks):
        self.blocks = blocks
        self.width, self.height = 594.0, 756.0


def test_doc_context_picks_title_and_abstract():
    title = FakeBlock("A Study of Robots in Classrooms", size=18.0)
    abstract = FakeBlock("According to productive failure theory, " * 20, size=9.5)
    ctx = _doc_context([FakeLayout([title, abstract])])
    assert "A Study of Robots" in ctx
    assert "摘要节选" in ctx


def test_doc_context_empty_is_safe():
    assert _doc_context([]) == ""
    assert _doc_context([FakeLayout([])]) == ""
