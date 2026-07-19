"""翻译层：持久缓存（T1）、失败降级（T2）、占位符保护、成本预估入口。"""
import json

import pytest

from src.glossary import Glossary
from src.transcache import TransCache
from src.translator import BaseTranslator


class StubTranslator(BaseTranslator):
    """可控桩：记录调用次数，可指定第 N 批抛异常。"""

    def __init__(self, fail_batches=(), bad_placeholder=False, **kw):
        super().__init__(**kw)
        self.calls = 0
        self.fail_batches = set(fail_batches)
        self.bad_placeholder = bad_placeholder
        self.strict_calls = 0

    def _translate_batch(self, batch, glossary_block):
        self.calls += 1
        if self.calls in self.fail_batches:
            raise RuntimeError("simulated network failure")
        if self.bad_placeholder:
            return [t.replace("⟦F1⟧", "") for t in batch]   # 故意丢占位符
        return ["译" + t for t in batch]

    def _translate_strict(self, text, glossary_block):
        self.strict_calls += 1
        return "译" + text          # 强化重试保住占位符


@pytest.fixture
def gloss():
    return Glossary({})


@pytest.fixture
def texts():
    return [f"para {i}" for i in range(10)]


def test_cache_persists_and_second_run_costs_nothing(tmp_path, gloss, texts):
    cache_file = tmp_path / "c.json"
    t1 = StubTranslator(batch_size=4, persist=TransCache(cache_file),
                        cache_scope="m|d|ctx")
    out1 = t1.translate_texts(texts, gloss)
    assert out1 == ["译" + t for t in texts]
    assert cache_file.exists()

    t2 = StubTranslator(batch_size=4, persist=TransCache(cache_file),
                        cache_scope="m|d|ctx")
    out2 = t2.translate_texts(texts, gloss)
    assert out2 == out1
    assert t2.calls == 0, "第二次应全部命中缓存，零请求"
    assert t2.cache_hits == len(texts)


def test_cache_scope_isolates(tmp_path, gloss, texts):
    """换模型/领域/上下文 → 缓存键变化，不得串用旧译文。"""
    cache_file = tmp_path / "c.json"
    StubTranslator(batch_size=4, persist=TransCache(cache_file),
                   cache_scope="A").translate_texts(texts, gloss)
    t = StubTranslator(batch_size=4, persist=TransCache(cache_file),
                       cache_scope="B")
    t.translate_texts(texts, gloss)
    assert t.calls > 0 and t.cache_hits == 0


def test_failed_batch_degrades_and_rerun_completes(tmp_path, gloss, texts):
    cache_file = tmp_path / "c.json"
    t1 = StubTranslator(batch_size=4, fail_batches={1},
                        persist=TransCache(cache_file), cache_scope="s")
    out = t1.translate_texts(texts, gloss)
    fallback = [o for o, s in zip(out, texts) if o == s]
    assert len(fallback) == 4, "失败批应回退原文而非中断任务"
    assert t1.failed_texts == 4

    # 重跑：成功段走缓存，失败段补齐
    t2 = StubTranslator(batch_size=4, persist=TransCache(cache_file),
                        cache_scope="s")
    out2 = t2.translate_texts(texts, gloss)
    assert out2 == ["译" + t for t in texts]
    assert t2.cache_hits == 6, "只有成功过的 6 段该命中缓存"


def test_failed_text_not_cached(tmp_path, gloss, texts):
    cache_file = tmp_path / "c.json"
    StubTranslator(batch_size=4, fail_batches={1},
                   persist=TransCache(cache_file),
                   cache_scope="s").translate_texts(texts, gloss)
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert len(data) == 6, "失败段不得写入缓存（否则永远补不回来）"


def test_placeholder_loss_triggers_strict_retry(gloss):
    src = ["公式 ⟦F1⟧ 在此"]
    t = StubTranslator(batch_size=1, bad_placeholder=True)
    out = t.translate_texts(src, gloss)
    assert t.strict_calls == 1, "占位符丢失应触发一次强化重试"
    assert "⟦F1⟧" in out[0]


def test_pending_texts_reflects_cache(tmp_path, gloss, texts):
    cache_file = tmp_path / "c.json"
    t = StubTranslator(batch_size=4, persist=TransCache(cache_file),
                       cache_scope="s")
    assert len(t.pending_texts(texts)) == len(texts)
    t.translate_texts(texts, gloss)
    t2 = StubTranslator(batch_size=4, persist=TransCache(cache_file),
                        cache_scope="s")
    assert t2.pending_texts(texts) == [], "全命中时预估应显示零请求"
