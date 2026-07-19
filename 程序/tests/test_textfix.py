"""中西文加空格（盘古之白）。"""
import pytest

from src.textfix import pangu


@pytest.mark.parametrize("src,expected", [
    ("RF条件下有16名学生", "RF 条件下有16名学生"),
    ("主题S1-2和S1-3", "主题 S1-2 和 S1-3"),
    ("对STEM教育的研究", "对 STEM 教育的研究"),
    ("使用DeepSeek翻译", "使用 DeepSeek 翻译"),
    ("如RF-08所说", "如 RF-08 所说"),
    ("用了Cohen's d指标", "用了 Cohen's d 指标"),
    # 纯数字紧贴汉字：中文学术惯例，不加空格
    ("研究1和研究2", "研究1和研究2"),
    ("第2天到第4天", "第2天到第4天"),
    ("110名学生", "110名学生"),
    # 公式占位符必须原样不动
    ("值为⟦F1⟧的效应", "值为⟦F1⟧的效应"),
    ("⟦F2⟧显著", "⟦F2⟧显著"),
    # 边界
    ("", ""),
    ("纯中文没有变化", "纯中文没有变化"),
    ("pure english unchanged", "pure english unchanged"),
])
def test_pangu(src, expected):
    assert pangu(src) == expected


@pytest.mark.parametrize("src", [
    "RF条件下有16名学生", "主题S1-2和S1-3", "值为⟦F1⟧的效应", "对STEM的研究",
])
def test_pangu_idempotent(src):
    once = pangu(src)
    assert pangu(once) == once, "重复应用必须幂等"
