"""翻译流水线：解析 → 翻译 → 回填。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import Config
from .glossary import Glossary
from .pdf_parser import parse_pdf
from .pdf_writer import build_output
from .textfix import pangu
from .translator import BaseTranslator, DeepSeekTranslator, MockTranslator, TranslatorError


def pick_backend(cfg: Config) -> str:
    """选择回填后端：auto 优先 PyMuPDF，未安装则 reportlab；可强制指定。"""
    choice = (getattr(cfg, "render_backend", "auto") or "auto").lower()
    if choice == "reportlab":
        return "reportlab"
    try:
        from . import pdf_writer_fitz
        if pdf_writer_fitz.available():
            return "pymupdf"
    except Exception:  # noqa: BLE001
        pass
    if choice == "pymupdf":
        raise TranslatorError(
            "已指定 PyMuPDF 后端，但当前环境未安装 pymupdf。"
            "请运行：pip install pymupdf fonttools")
    return "reportlab"

ProgressCB = Callable[[str, float], None]
CancelCB = Callable[[], bool]


class CancelledError(Exception):
    """用户取消翻译。"""


def _has_cjk(text: Optional[str]) -> bool:
    return bool(text) and any(
        "㐀" <= c <= "鿿" or "豈" <= c <= "﫿" for c in text)


# ---------------------------------------------------------------------------
# T12 跨栏段落重排：被栏边/页边腰斩的段落配成同一翻译单元
# ---------------------------------------------------------------------------

_TERMINAL = set("。．.!?！？;；:：…")


def _is_body(b) -> bool:
    return (b.translatable and not getattr(b, "bold", False)
            and not getattr(b, "from_ocr", False) and len(b.text or "") > 60)


def _continues(a, b) -> bool:
    """a 尾不带终止标点 且 b 以小写字母开头 → 视为同一段被腰斩。"""
    ta = (a.text or "").rstrip("'\"’”)]） ")
    tb = (b.text or "").lstrip()
    if not ta or not tb:
        return False
    return ta[-1] not in _TERMINAL and tb[0].islower()


def _make_units(layouts, blocks) -> list:
    """把 blocks 组成翻译单元（多数单块；被腰斩的相邻块配成一对）。"""
    in_set = set(map(id, blocks))
    pair_next: dict = {}
    consumed: set = set()

    def cols(L):
        mid = L.width / 2
        body = [b for b in L.blocks if id(b) in in_set and _is_body(b)
                and b.top < 0.93 * L.height]   # 排除页脚（不以句号结尾易误配）
        left = [b for b in body if b.x1 <= mid + 10 and b.x0 < mid - 40]
        right = [b for b in body if b.x0 >= mid - 10]
        return left, right

    for L in layouts:
        left, right = cols(L)
        if left and right:   # 同页：左栏尾 → 右栏首
            a = max(left, key=lambda x: x.bottom)
            b = min(right, key=lambda x: x.top)
            if id(a) not in consumed and id(b) not in consumed and _continues(a, b):
                pair_next[id(a)] = b
                consumed.update((id(a), id(b)))
    for L, Ln in zip(layouts, layouts[1:]):   # 跨页：本页尾 → 次页首
        lcols = cols(L)
        tail_pool = lcols[1] or lcols[0]
        ncols = cols(Ln)
        head_pool = ncols[0] or ncols[1]
        if not tail_pool or not head_pool:
            continue
        a = max(tail_pool, key=lambda x: x.bottom)
        b = min(head_pool, key=lambda x: x.top)
        if id(a) not in consumed and id(b) not in consumed and _continues(a, b):
            pair_next[id(a)] = b
            consumed.update((id(a), id(b)))

    units, skip = [], set()
    for b in blocks:
        if id(b) in skip:
            continue
        nxt = pair_next.get(id(b))
        if nxt is not None:
            units.append([b, nxt])
            skip.add(id(nxt))
        else:
            units.append([b])
    return units


def _unit_text(unit) -> str:
    if len(unit) == 1:
        return unit[0].text
    a, b = unit[0].text, unit[1].text
    if a.endswith("-") and len(a) > 1 and a[-2].isalpha():
        return a[:-1] + b          # 连字符断词：直接拼接
    return a + " " + b


def _split_translation(tr: str, len_a: int, len_b: int):
    """按源长度比例在最近的句读处把译文拆回两块。"""
    if not tr:
        return tr, ""
    idx = max(1, min(len(tr) - 1, round(len(tr) * len_a / max(len_a + len_b, 1))))
    span = max(6, len(tr) // 4)
    lo, hi = max(1, idx - span), min(len(tr) - 1, idx + span)
    best = None
    for prefer in ("。；！？!?;", "，、,"):
        cands = [i + 1 for i in range(lo, hi) if tr[i] in prefer]
        if cands:
            best = min(cands, key=lambda i: abs(i - idx))
            break
    cut = best if best is not None else idx
    return tr[:cut], tr[cut:]


def make_translator(cfg: Config, mock: bool = False,
                    doc_context: str = "") -> BaseTranslator:
    if mock:
        return MockTranslator(batch_size=cfg.batch_size, max_workers=cfg.max_workers)
    persist = None
    scope = ""
    if getattr(cfg, "use_cache", True):
        from .config import ROOT
        from .transcache import TransCache
        import hashlib
        ctx_h = hashlib.sha1(doc_context.encode("utf-8")).hexdigest()[:10]
        scope = f"{cfg.model}|{getattr(cfg, 'domain', '')}|{ctx_h}"
        persist = TransCache(ROOT / "cache" / "translations.json")
    return DeepSeekTranslator(
        api_key=cfg.api_key,
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        source_lang=cfg.source_lang,
        target_lang=cfg.target_lang,
        batch_size=cfg.batch_size,
        max_workers=cfg.max_workers,
        proxy=cfg.proxy,
        use_system_proxy=cfg.use_system_proxy,
        verify_ssl=cfg.verify_ssl,
        thinking=cfg.thinking,
        persist=persist,
        cache_scope=scope,
        domain=getattr(cfg, "domain", "计算机科学"),
        doc_context=doc_context,
    )


def _doc_context(layouts) -> str:
    """T6：取 p1 最大字号可译块（标题）+ 首个长段（摘要）作全文语境。"""
    if not layouts:
        return ""
    first = [b for b in layouts[0].blocks if b.translatable and b.text]
    if not first:
        return ""
    title = max(first, key=lambda b: b.size).text[:150]
    abstract = next((b.text for b in first if len(b.text) > 300), "")
    ctx = title
    if abstract:
        ctx += "\n摘要节选：" + abstract[:250]
    return ctx


def _estimate_line(translator, texts, cfg: Config) -> str:
    """T5：请求前成本预估（剔除缓存命中；粗估 token，可选换算金额）。"""
    todo = translator.pending_texts(texts)
    chars = sum(len(t) for t in todo)
    tok_in = int(chars / 3.6 * 1.18)          # 英文 ≈3.6 字符/词元 + 提示词开销
    tok_out = int(tok_in * 1.1)               # 中文译文略长
    line = (f"预计需请求 {len(todo)} 段（缓存命中 "
            f"{len(list(dict.fromkeys(texts))) - len(todo)} 段）"
            f"，约 {(tok_in + tok_out) / 10000:.1f} 万 tokens")
    pin, pout = getattr(cfg, "price_in", 0.0), getattr(cfg, "price_out", 0.0)
    if pin > 0 or pout > 0:
        cost = tok_in / 1e6 * pin + tok_out / 1e6 * pout
        line += f"（约 ¥{cost:.2f}）"
    return line


def check_connection(cfg: Config) -> Tuple[bool, str]:
    """用一小段文本快速验证 API Key / 模型是否可用。返回 (是否成功, 提示)。"""
    try:
        tr = DeepSeekTranslator(
            api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url,
            temperature=cfg.temperature, max_retries=1, timeout=30,
            proxy=cfg.proxy, use_system_proxy=cfg.use_system_proxy,
            verify_ssl=cfg.verify_ssl, thinking=cfg.thinking,
        )
        out = tr._chat([
            {"role": "system", "content": "You are a translator."},
            {"role": "user", "content": "把 'hello' 翻译成中文，只输出译文。"},
        ])
        return True, f"连接成功（模型 {cfg.model}）。返回示例：{out.strip()[:20]}"
    except TranslatorError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"连接失败：{e}"


def translate_pdf(
    input_path: str,
    output_path: str,
    cfg: Config,
    translator: Optional[BaseTranslator] = None,
    glossary: Optional[Glossary] = None,
    mock: bool = False,
    progress: Optional[ProgressCB] = None,
    should_cancel: Optional[CancelCB] = None,
) -> dict:
    def report(msg: str, frac: float):
        if progress:
            progress(msg, max(0.0, min(1.0, frac)))

    def check_cancel():
        if should_cancel and should_cancel():
            raise CancelledError("已取消。")

    check_cancel()
    report("正在解析 PDF…", 0.02)
    layouts = parse_pdf(input_path,
                        progress=lambda msg, frac: report(msg, 0.02 + 0.06 * frac))
    blocks = [b for layout in layouts for b in layout.blocks if b.translatable]
    # T3 试译模式：只翻译前 max_pages 页，其余页保留原文（便宜预览）
    max_pages = int(getattr(cfg, "max_pages", 0) or 0)
    if max_pages > 0:
        blocks = [b for b in blocks if b.page_index < max_pages]
    # T12 跨栏段落重排：被腰斩段配成同一翻译单元（整段送译，译后按比例拆回）
    units = _make_units(layouts, blocks)
    n_pairs = sum(1 for u in units if len(u) > 1)
    texts = [_unit_text(u) for u in units]
    n_ocr = sum(1 for L in layouts if any(b.from_ocr for b in L.blocks))
    trial = f"（试译前 {max_pages} 页）" if max_pages > 0 else ""
    pair_note = f"，跨栏/跨页续段配对 {n_pairs} 组" if n_pairs else ""
    if n_ocr:
        report(f"共 {len(layouts)} 页（其中 {n_ocr} 页扫描版经 OCR 识别）、"
               f"{len(texts)} 个待译段落{trial}{pair_note}", 0.08)
    else:
        report(f"共 {len(layouts)} 页、{len(texts)} 个待译段落{trial}{pair_note}", 0.08)

    if glossary is None:
        glossary = Glossary.load(cfg.resolved_glossary_path())
    if translator is None:
        translator = make_translator(cfg, mock=mock,
                                     doc_context=_doc_context(layouts))
    if texts and not mock:
        try:
            report(_estimate_line(translator, texts, cfg), 0.09)   # T5 成本预估
        except Exception:  # noqa: BLE001
            pass

    def tcb(done: int, total: int):
        check_cancel()  # 每批之间检查取消
        report(f"正在翻译… 第 {done}/{total} 批", 0.10 + 0.80 * (done / max(total, 1)))

    if texts:
        translations = translator.translate_texts(texts, glossary, tcb)
        translations = [pangu(tr) for tr in translations]  # 中西文加空格
        kept = 0
        for unit, tr in zip(units, translations):
            # 只重排真正翻译了的内容（商业文档翻译工具的最小干预原则）：
            # 译文里没有任何中文 = 模型原样退回（引用/URL/人名/占位符回退等）
            # → 保留原文原排版，不抹除不重排。
            if not _has_cjk(tr):
                for b in unit:
                    b.translation = None
                kept += len(unit)
            elif len(unit) == 1:
                unit[0].translation = tr
            else:
                a, b = unit
                ca, cb = _split_translation(tr, len(a.text), len(b.text))
                a.translation, b.translation = ca, cb
        if kept:
            report(f"{kept} 段未产生中文译文（引用/专名等），保留原文原排版", 0.90)
        hits = getattr(translator, "cache_hits", 0)
        if hits:
            report(f"持久缓存命中 {hits} 段，未重复计费", 0.90)
        failed = getattr(translator, "failed_texts", 0)
        if failed:
            report(f"注意：{failed} 段因网络/服务错误未翻译，已保留原文——"
                   "重新运行本任务即可补齐（已译段走缓存不重复计费）", 0.90)
        ph_failed = getattr(translator, "ph_failures", 0)
        if ph_failed:
            report(f"提示：{ph_failed} 段公式占位符校验未通过，已保留原文以保住公式位置", 0.90)
    elif any(L.needs_ocr for L in layouts):
        report("未提取到可翻译文字：这是扫描版 PDF，且缺少 OCR 组件。"
               "请运行：pip install rapidocr-onnxruntime 后重试", 0.9)
    else:
        report("未提取到可翻译文字（可能是扫描版 PDF）", 0.9)

    check_cancel()
    backend = pick_backend(cfg)
    label = "PyMuPDF 精确抹除" if backend == "pymupdf" else "reportlab 覆盖（兜底）"
    report(f"正在生成译文 PDF…（回填后端：{label}）", 0.93)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if backend == "pymupdf":
        from . import pdf_writer_fitz
        try:
            pdf_writer_fitz.build_output(
                input_path, output_path, layouts, cfg.output_mode,
                font_path=getattr(cfg, "font_path", ""))
        except pdf_writer_fitz.BackendUnsupported as e:
            report(f"PyMuPDF 后端不适用（{e}），回退 reportlab…", 0.94)
            backend = "reportlab"
        except Exception as e:  # noqa: BLE001
            if (getattr(cfg, "render_backend", "auto") or "auto") == "pymupdf":
                raise  # 用户强制指定时不静默回退
            report(f"PyMuPDF 后端异常（{e}），回退 reportlab…", 0.94)
            backend = "reportlab"
    if backend == "reportlab":
        build_output(input_path, output_path, layouts, cfg.output_mode)
    report("完成", 1.0)

    return {
        "pages": len(layouts),
        "blocks": len(blocks),
        "output": output_path,
        "mode": cfg.output_mode,
        "backend": backend,
    }
