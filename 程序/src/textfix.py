"""译文排版后处理：中西文之间自动加空格（俗称"盘古之白"）。

专业中文排版惯例：汉字与相邻的**西文词**之间留一个空格，显著提升可读性，
尤其本文这类缩写密集（RF / DI / PF / ANCOVA / STEM / Cohen / SD…）的论文。

判据是"这个西文词是否含字母"，而非逐字符看：
  · 含字母的词才加空格：`RF条件`→`RF 条件`、`主题S1-2和S1-3`→`主题 S1-2 和 S1-3`、
    `对STEM的`→`对 STEM 的`、`用DeepSeek翻译`→`用 DeepSeek 翻译`。
  · 纯数字不加（中文学术惯例，加了反而别扭）：`研究1`、`16名`、`第2天` 保持不变。
  · 中文标点（，。：）与括号（）非西文字符，不受影响。
  · 公式占位符 ⟦Fn⟧ 的括号非西文字符，天然不被匹配，位置绝不错动。
"""
from __future__ import annotations

import re

# CJK：统一表意文字 + 扩展A + 兼容表意（覆盖正文可能出现的汉字）
_CJK = "㐀-䶿一-鿿豈-﫿"
# 西文词：字母/数字/连字符/点/撇 的连续串（如 RF-08、S1-2、e.g.、Cohen's、0.594）
_WORD = r"[A-Za-z0-9][A-Za-z0-9.\-']*"

_CJK_WORD = re.compile(rf"([{_CJK}])({_WORD})")
_WORD_CJK = re.compile(rf"({_WORD})([{_CJK}])")


def _has_letter(s: str) -> bool:
    return any(c.isalpha() for c in s)


def pangu(text: str) -> str:
    """在汉字与"含字母的西文词"交界处插入空格。幂等。"""
    if not text:
        return text
    text = _CJK_WORD.sub(
        lambda m: m.group(1) + (" " if _has_letter(m.group(2)) else "") + m.group(2),
        text)
    text = _WORD_CJK.sub(
        lambda m: m.group(1) + (" " if _has_letter(m.group(1)) else "") + m.group(2),
        text)
    return text
