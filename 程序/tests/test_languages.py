"""多语言支持（B5）：语言注册、目标语判定、质量长度带、排版分词、推荐。"""
import pytest

from src import languages as L
from src.config import load_config
from src.quality import check


# ---- 语言注册 ----
def test_targets_and_sources():
    tcodes = [t["code"] for t in L.targets()]
    assert "zh" in tcodes and "en" in tcodes and "ja" in tcodes
    assert L.sources()[0]["code"] == "auto"


# ---- is_translated：多语言"是否真的翻译了" ----
@pytest.mark.parametrize("src,tgt,code,expected", [
    ("Hello world foo", "你好世界", "zh", True),        # →中，有汉字
    ("Hello world foo", "Hello world foo", "zh", False),  # 未译（无汉字）
    ("你好世界啊", "Hello world", "en", True),          # →英，与源不同
    ("NASA", "NASA", "en", False),                      # 专名回退（相同）
    ("robot", "こんにちは世界", "ja", True),             # →日，含假名
    ("robot", "테스트입니다", "ko", True),               # →韩，含谚文
    ("robot", "тест работает", "ru", True),             # →俄，含西里尔
    ("anything", "", "zh", False),                      # 空
])
def test_is_translated(src, tgt, code, expected):
    assert L.is_translated(src, tgt, code) is expected


def test_is_translated_latin_echo_vs_translation():
    # 拉丁→拉丁：逐字回显视为未译；不同视为已译
    assert L.is_translated("Das Haus", "Das Haus", "en") is False
    assert L.is_translated("Das Haus", "The house", "en") is True


# ---- 质量长度带随语言对变化 ----
def test_length_band_direction():
    assert L.length_band("en", "zh") == (0.12, 1.6)     # 压缩
    lo, hi = L.length_band("zh", "en")                   # 膨胀
    assert hi >= 5
    assert L.length_band("auto", "zh")[1] >= 8           # 未知源：宽带


def test_quality_no_false_positive_on_expansion():
    """中→英译文天然更长，不能被判 too_long。"""
    zh = "甲" * 60
    en = "We compared three instructional methods across six classes carefully. " * 3
    issues = check(zh, en, "zh", "en")
    assert "too_long" not in {i.code for i in issues}


def test_quality_still_catches_truncation_zh_en():
    zh = "甲" * 200
    issues = check(zh, "We compared.", "zh", "en")
    assert "too_short" in {i.code for i in issues}


def test_quality_numbers_only_cn_forms_for_zh_target():
    # 目标英语时不做中文数字宽松匹配，但阿拉伯数字仍须保留
    assert "missing_numbers" in {i.code for i in
                                 check("20 classes joined the study today here",
                                       "classes joined the study", "en", "en")}


# ---- 排版分词：脚本感知断行（关键：欧洲语言重音不能断词）----
def test_tokenizer_keeps_accented_words_intact():
    from src.layout import _tokenize
    units = _tokenize("Präzisión café naïve")   # 德/法重音
    toks = [p for k, p in units if k == "tok" and p != " "]
    assert "Präzisión" in toks, "带重音的词不得在字符间被拆开"
    assert "café" in toks and "naïve" in toks


def test_tokenizer_breaks_cjk_per_char():
    from src.layout import _tokenize
    units = _tokenize("你好世界")
    chars = [p for k, p in units if k == "tok"]
    assert chars == ["你", "好", "世", "界"], "中文仍逐字可断"


def test_tokenizer_korean_and_cyrillic_word_based():
    from src.layout import _tokenize
    ko = [p for k, p in _tokenize("안녕하세요 세계") if k == "tok" and p != " "]
    assert "안녕하세요" in ko, "韩文按词（空格）断，不逐字"
    ru = [p for k, p in _tokenize("привет мир") if k == "tok" and p != " "]
    assert "привет" in ru and "мир" in ru


# ---- pangu 仅中文目标 ----
def test_pangu_gated_to_chinese():
    assert L.uses_pangu("zh") is True
    assert L.uses_pangu("en") is False and L.uses_pangu("ja") is False


# ---- 推荐 ----
def test_recommendations_map_to_real_services():
    assert "DeepSeek" in L.recommend("zh")["services"]
    assert "OpenAI" in L.recommend("de")["services"]
    assert L.recommend("xx")["services"]         # 未知语言有兜底
    assert L.RECO_NOTE


# ---- 配置迁移 ----
def test_legacy_config_migration():
    c = load_config(source_lang="英文", target_lang="中文")
    assert c.source_lang == "en" and c.target_lang == "zh"


def test_new_codes_pass_through():
    c = load_config(source_lang="ja", target_lang="en")
    assert c.source_lang == "ja" and c.target_lang == "en"


# ---- 缓存 scope 含语言对（换目标语不命中旧译文）----
def test_cache_scope_includes_language_pair():
    from src.pipeline import make_translator
    t_zh = make_translator(load_config(api_key="x", target_lang="zh", use_cache=True))
    t_en = make_translator(load_config(api_key="x", target_lang="en", use_cache=True))
    assert t_zh.cache_scope != t_en.cache_scope, "不同目标语必须用不同缓存键"
