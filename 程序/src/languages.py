"""多语言支持（B5）：语言注册中心 + 目标语判定 + 模型推荐。

统一管理"支持哪些语言、每种语言的排版特性、译文是否真的产出、该用哪个模型"。
其余模块（translator/pipeline/layout/quality/前端）都从这里取，避免各处散落。

语言用短代码（en/zh/ja…）标识；`auto` 表示源语言自动检测。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


def _in(ch: str, *ranges) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in ranges)


def _has_han(t: str) -> bool:      # CJK 表意文字（中日共用）
    return any(_in(c, (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF)) for c in t)


def _has_kana(t: str) -> bool:     # 日文假名
    return any(_in(c, (0x3040, 0x30FF)) for c in t)


def _has_hangul(t: str) -> bool:   # 韩文谚文
    return any(_in(c, (0xAC00, 0xD7A3), (0x1100, 0x11FF)) for c in t)


def _has_cyrillic(t: str) -> bool:
    return any(_in(c, (0x0400, 0x04FF)) for c in t)


def _has_latin_letter(t: str) -> bool:
    return any(c.isalpha() and ord(c) < 0x250 for c in t)


@dataclass(frozen=True)
class Lang:
    code: str
    name: str                       # 界面显示名（中文）
    prompt_name: str                # 写进提示词的目标语名
    compresses: bool                # 译文偏紧凑（表意文字）→ 影响质量长度带
    pangu: bool                     # 是否套用中西文加空格（仅中文）
    # 判断"文本是否为本语言的产出"；None 表示按"非源文逐字回显"判定
    script_check: Optional[Callable[[str], bool]] = None


# 目标语（可译入）。源语言额外含 auto。
_TARGETS: List[Lang] = [
    Lang("zh", "中文", "简体中文", True, True, _has_han),
    Lang("en", "英语", "英语", False, False, None),
    Lang("ja", "日语", "日语", True, False, lambda t: _has_han(t) or _has_kana(t)),
    Lang("ko", "韩语", "韩语", False, False, _has_hangul),
    Lang("de", "德语", "德语", False, False, None),
    Lang("fr", "法语", "法语", False, False, None),
    Lang("es", "西班牙语", "西班牙语", False, False, None),
    Lang("ru", "俄语", "俄语", False, False, _has_cyrillic),
    Lang("pt", "葡萄牙语", "葡萄牙语", False, False, None),
    Lang("it", "意大利语", "意大利语", False, False, None),
]

BY_CODE: Dict[str, Lang] = {L.code: L for L in _TARGETS}

# 源语言：auto + 全部目标语（源用其自然名，不用"简体中文"这种目标态叫法）
_SOURCE_NAMES = {"auto": "自动检测", "zh": "中文", "en": "英语"}


def targets() -> List[dict]:
    return [{"code": L.code, "name": L.name} for L in _TARGETS]


def sources() -> List[dict]:
    out = [{"code": "auto", "name": "自动检测"}]
    out += [{"code": L.code, "name": L.name} for L in _TARGETS]
    return out


def target_prompt_name(code: str) -> str:
    L = BY_CODE.get(code)
    return L.prompt_name if L else "中文"


def source_prompt_name(code: str) -> str:
    if code == "auto" or not code:
        return "原文"
    L = BY_CODE.get(code)
    return L.name if L else "原文"


def uses_pangu(target_code: str) -> bool:
    L = BY_CODE.get(target_code)
    return bool(L and L.pangu)


# ---------------------------------------------------------------------------
# "模型是否真的把它翻译成了目标语"——多语言下取代旧的 _has_cjk
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return "".join(s.split()).casefold()


def looks_like(text: str, code: str) -> bool:
    """text 是否"已经是"该语言（用于判断源片段要不要送翻译）。

    · 中文：有汉字且无假名/谚文（排除日文，日→中仍需翻译）；
    · 日/韩/俄：出现其特征脚本；
    · 拉丁系（英/德/法…）：有拉丁字母且不含 CJK/假名/谚文/西里尔。
    """
    if not text:
        return False
    if code == "zh":
        return _has_han(text) and not _has_kana(text) and not _has_hangul(text)
    L = BY_CODE.get(code)
    if L and L.script_check is not None:
        return L.script_check(text)
    # 拉丁系目标
    return (_has_latin_letter(text) and not _has_han(text)
            and not _has_kana(text) and not _has_hangul(text)
            and not _has_cyrillic(text))


def is_translated(src: str, tgt: str, target_code: str) -> bool:
    """判断 tgt 是否为 src 的有效译文（而非模型原样回退）。

    · 目标语有脚本特征（中/日/韩/俄）→ 译文应出现该脚本；
    · 拉丁系目标（英/德/法…）无法靠脚本区分（源可能也是拉丁），
      改判"是否与源文逐字相同"：完全相同视为未翻译（引用/专名回退），
      不同即视为已翻译。
    """
    if not tgt or not tgt.strip():
        return False
    L = BY_CODE.get(target_code)
    if L and L.script_check is not None:
        return L.script_check(tgt)
    return _norm(tgt) != _norm(src)


# ---------------------------------------------------------------------------
# 质量自检的长度带（B3 阈值按语言对调整，避免非中英对误报）
# ---------------------------------------------------------------------------

def length_band(source_code: str, target_code: str) -> Optional[Tuple[float, float]]:
    """返回 (下限, 上限)；译文/原文字符比落在带外才算长度异常。
    返回 None 表示信息不足（源为 auto 等），跳过长度判定。"""
    tgt = BY_CODE.get(target_code)
    if tgt is None:
        return None
    if source_code == "auto" or not source_code:
        return (0.08, 10.0)                 # 未知源：只兜极端截断/暴涨
    src = BY_CODE.get(source_code)
    if src is None:
        return (0.08, 10.0)
    if not src.compresses and tgt.compresses:      # 拉丁→表意：压缩
        return (0.12, 1.6)                          # 按 en→zh 真实语料标定
    if src.compresses and not tgt.compresses:      # 表意→拉丁：膨胀
        return (0.5, 8.0)
    return (0.3, 3.5)                                # 同类


# ---------------------------------------------------------------------------
# 模型推荐（经验参考；映射到本工具实际提供的服务）
# ---------------------------------------------------------------------------

_RECO: Dict[str, Tuple[str, str]] = {
    "zh": ("DeepSeek / Kimi", "国产模型中文地道、便宜，默认即可"),
    "en": ("DeepSeek / OpenAI", "DeepSeek 中英互译强且便宜；追求极致选 OpenAI"),
    "ja": ("Kimi / OpenAI", "日语 Kimi 与 GPT 系列较稳（通义 Qwen 亦强，可自定义接入）"),
    "ko": ("Kimi / OpenAI", "韩语同日语，亚洲语言 Kimi / GPT 更稳"),
    "de": ("OpenAI", "欧洲语言 GPT 系列一致性更好（Mistral 亦佳，可自定义接入）"),
    "fr": ("OpenAI", "欧洲语言 GPT 系列一致性更好（Mistral 亦佳，可自定义接入）"),
    "es": ("OpenAI", "欧洲语言 GPT 系列一致性更好（Mistral 亦佳，可自定义接入）"),
    "it": ("OpenAI", "欧洲语言 GPT 系列一致性更好"),
    "pt": ("OpenAI", "欧洲语言 GPT 系列一致性更好"),
    "ru": ("OpenAI", "俄语 GPT 系列更稳"),
}
_RECO_DEFAULT = ("OpenAI", "综合能力最强，但价格较高")
RECO_NOTE = "以上为经验参考，具体以你的文档实测为准。"


def recommend(target_code: str) -> dict:
    svc, reason = _RECO.get(target_code, _RECO_DEFAULT)
    return {"services": svc, "reason": reason}


def all_recommend() -> Dict[str, dict]:
    return {L.code: recommend(L.code) for L in _TARGETS}
