"""译文回填 —— PyMuPDF (fitz) 首选后端：精确抹除 + CJK 嵌入 + 公式矢量回贴。

相对 reportlab 覆盖方案的本质优势：
  · **真正抹除**原文 glyph（redaction），不画白框——深色/带底纹背景零露白，
    输出 PDF 的文字层干净（原文字符被删除而非被盖住）；
  · 中文字体**嵌入**输出文件（内置 CJK 或 fonts/ 目录下的思源字体），
    任何阅读器渲染一致；
  · 行内公式用 `show_pdf_page` 从原始副本**矢量回贴**，无限清晰、无白底；
  · 排版复用 layout.py（与兜底后端行为一致）。

版本兼容：核心 API（add_redact_annot / apply_redactions / insert_text /
show_pdf_page / insert_font）自 PyMuPDF 1.18 起稳定；较新的可选参数
（fill=False、graphics=...、subset_fonts）逐级 try 降级。
坐标系：fitz 与 pdfplumber 同为「左上角原点、y 向下」，无需翻转。
旋转页（page.rotation != 0）暂不支持精确路径——检测到即抛
BackendUnsupported，由 pipeline 整体回退 reportlab 后端。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from .layout import (collect_avoid_rects, compute_target_box, layout_block,
                     ocr_line_shape_avoids)
from .pdf_parser import PageLayout

_ASCENT = 0.85     # 基线相对字号的近似上高（与兜底后端一致）
_ERASE_PAD_X = 0.5
_ERASE_PAD_Y = 0.2
_CLIP_PAD = 1.0    # 公式矢量回贴的裁剪外扩

# 内置 CJK 字体（MuPDF 自带，无需外部文件；简体宋风格）
_BUILTIN_CJK = "china-ss"
# ASCII 专用西文字体（Base-14，比例宽度）。注意：insert_text 的内置
# "china-ss" 实际嵌入 Song CID 字体，其 ASCII 字形是**全宽 1em**，与
# fitz.Font("china-ss") 的比例测宽不一致——ASCII 必须单独用西文字体
# 测宽并绘制，否则绘制比排版预留宽约一倍，导致压字/叠印/整块溢出。
_LATIN = "helv"


def _script_runs(text: str):
    """把文本切成 (is_ascii, 段) 序列：ASCII 与非 ASCII 分属不同字体。"""
    runs = []
    cur = ""
    cur_ascii = None
    for ch in text:
        a = ord(ch) < 128
        if cur_ascii is None or a == cur_ascii:
            cur += ch
            cur_ascii = a
        else:
            runs.append((cur_ascii, cur))
            cur, cur_ascii = ch, a
    if cur:
        runs.append((cur_ascii, cur))
    return runs

ROOT = Path(__file__).resolve().parent.parent


class BackendUnsupported(Exception):
    """当前文件/环境不适用本后端，应回退 reportlab。"""


def available() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def find_font_file(explicit: str = "") -> Optional[str]:
    """定位外置中文字体：显式路径 > fonts/ 目录（偏好思源/Noto 简体）。"""
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            return str(p)
    fdir = ROOT / "fonts"
    if not fdir.is_dir():
        return None
    cands = [p for p in sorted(fdir.iterdir())
             if p.suffix.lower() in (".ttf", ".otf")]
    if not cands:
        return None

    def rank(p: Path):
        n = p.name.lower()
        score = 0
        for i, kw in enumerate(("sc", "cn", "han", "song", "serif", "hei", "sans")):
            if kw in n:
                score -= (10 - i)
        return score

    return str(sorted(cands, key=rank)[0])


def _make_measure(fontfile: Optional[str]) -> Callable[[str, float], float]:
    import fitz
    try:
        f_cjk = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font(_BUILTIN_CJK)
        f_lat = fitz.Font(_LATIN)

        def measure(t: str, s: float) -> float:
            return sum((f_lat if is_a else f_cjk).text_length(seg, fontsize=s)
                       for is_a, seg in _script_runs(t))
        return measure
    except Exception:  # noqa: BLE001
        # 极端兜底：按 CJK=1em / ASCII=0.5em 估宽
        return lambda t, s: sum(s * (0.5 if ord(c) < 128 else 1.0) for c in t)


def _redact_page(page, blocks) -> None:
    import fitz
    added = 0
    for b in blocks:
        if getattr(b, "from_ocr", False):
            continue   # 扫描页无文字层可抹，改在 _draw_page 里白底覆盖
        for (x0, top, x1, bottom) in b.line_rects:
            rect = fitz.Rect(x0 - _ERASE_PAD_X, top - _ERASE_PAD_Y,
                             x1 + _ERASE_PAD_X, bottom + _ERASE_PAD_Y)
            if rect.is_empty or not rect.is_valid:
                continue
            try:
                page.add_redact_annot(rect, fill=False)   # 不填充 → 背景零露白
            except Exception:  # noqa: BLE001
                page.add_redact_annot(rect)               # 旧版：默认白填充
            added += 1
    if not added:
        return
    img_none = getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0)
    try:
        page.apply_redactions(
            images=img_none,
            graphics=getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0))
    except TypeError:
        try:
            page.apply_redactions(images=img_none)   # 图片一律保留
        except TypeError:
            page.apply_redactions()


def _draw_page(page, layout: PageLayout, blocks, src_doc,
               fontname: str, fontfile: Optional[str],
               measure: Callable[[str, float], float]) -> None:
    import fitz
    try:
        if fontfile:
            page.insert_font(fontname=fontname, fontfile=fontfile)
        else:
            page.insert_font(fontname=fontname)  # fontname 为内置保留名
    except Exception:  # noqa: BLE001
        pass  # insert_text 时仍可用内置名自动加载

    placed: List[tuple] = []   # 本页已放置译文/公式的矩形，后续块逐行避让
    for b in blocks:
        if getattr(b, "from_ocr", False):
            # 扫描页：原文是图像像素，先用白底盖住原文各行再写译文
            for (x0, top, x1, bottom) in b.line_rects:
                r = fitz.Rect(x0 - 1.0, top - 0.6, x1 + 1.0, bottom + 0.6)
                if not r.is_empty and r.is_valid:
                    page.draw_rect(r, color=None, fill=(1, 1, 1))
        fdims = {f.idx: (f.width + 2.0, f.height + 2.0) for f in b.formulas}
        box = compute_target_box(b, layout.blocks, layout.obstacles, layout.height)
        avoid = list(collect_avoid_rects(b, layout.blocks, layout.obstacles))
        if getattr(b, "from_ocr", False):
            avoid += ocr_line_shape_avoids(b, box)
        start_size = min(max(b.size, 5.0), 20.0)
        # 表格单元格空间紧、不可越格，允许缩得更小以塞进本格
        min_size = 4.0 if getattr(b, "cell_rect", None) else 5.0
        laid = layout_block(b.translation or "", fdims, box, start_size,
                            measure, avoid=avoid + placed, min_size=min_size)
        placed.extend((it.x, it.y_top, it.x + it.w, it.y_top + it.h)
                      for it in laid.items)
        color = tuple(getattr(b, "color", (0, 0, 0)) or (0, 0, 0))
        # 粗体块（章节标题）：render_mode=2（填充+描边）合成加粗，描边同色、
        # 线宽取字号的 4.5%（实测 9~10pt 标题清晰有力且不糊）。
        bold_kw = (dict(render_mode=2, fill=color, border_width=0.045)
                   if getattr(b, "bold", False) else {})
        frects = {f.idx: (f.x0, f.top, f.x1, f.bottom) for f in b.formulas}
        for it in laid.items:
            if it.kind == "text":
                baseline = it.y_top + 0.5 * (it.h - it.size) + _ASCENT * it.size
                x = it.x
                for is_a, seg in _script_runs(it.text):
                    page.insert_text((x, baseline), seg,
                                     fontname=_LATIN if is_a else fontname,
                                     fontsize=it.size, color=color, **bold_kw)
                    x += measure(seg, it.size)
            else:
                r = frects.get(it.fidx)
                if r is None:
                    continue
                clip = fitz.Rect(r[0] - _CLIP_PAD, r[1] - _CLIP_PAD,
                                 r[2] + _CLIP_PAD, r[3] + _CLIP_PAD)
                target = fitz.Rect(it.x, it.y_top, it.x + it.w, it.y_top + it.h)
                if target.is_empty or clip.is_empty:
                    continue
                try:
                    page.show_pdf_page(target, src_doc, layout.page_index,
                                       clip=clip)
                except Exception:  # noqa: BLE001
                    pass  # 单个公式回贴失败不影响整页


def build_output(input_path: str, output_path: str,
                 layouts: List[PageLayout], mode: str = "translated",
                 font_path: str = "") -> None:
    import fitz

    doc = fitz.open(input_path)   # 工作文档：redact + 写中文
    src = fitz.open(input_path)   # 原始副本：公式矢量回贴来源

    try:
        for page in doc:
            if getattr(page, "rotation", 0):
                raise BackendUnsupported(
                    f"第 {page.number + 1} 页含旋转（rotation="
                    f"{page.rotation}），PyMuPDF 精确路径暂不支持")

        fontfile = find_font_file(font_path)
        fontname = "zhCJK" if fontfile else _BUILTIN_CJK
        measure = _make_measure(fontfile)

        for layout in layouts:
            if layout.page_index >= len(doc):
                continue
            page = doc[layout.page_index]
            blocks = [b for b in layout.blocks if b.translatable and b.translation]
            if not blocks:
                continue
            _redact_page(page, blocks)
            _draw_page(page, layout, blocks, src, fontname, fontfile, measure)

        if mode == "bilingual":
            out = fitz.open()
            for i in range(len(doc)):
                out.insert_pdf(src, from_page=i, to_page=i)   # 原文页
                out.insert_pdf(doc, from_page=i, to_page=i)   # 译文页
            _subset_fonts(out)
            out.save(output_path, garbage=3, deflate=True)
            out.close()
        elif mode == "sidebyside":
            # T4 左右对照：2W×H 宽页，左原文右译文，中缝细分隔线
            out = fitz.open()
            for i in range(len(doc)):
                r = src[i].rect
                w, h = r.width, r.height
                page = out.new_page(width=2 * w, height=h)
                page.show_pdf_page(fitz.Rect(0, 0, w, h), src, i)
                page.show_pdf_page(fitz.Rect(w, 0, 2 * w, h), doc, i)
                page.draw_line(fitz.Point(w, 0), fitz.Point(w, h),
                               color=(0.8, 0.8, 0.8), width=0.7)
            _subset_fonts(out)
            out.save(output_path, garbage=3, deflate=True)
            out.close()
        else:
            _subset_fonts(doc)
            doc.save(output_path, garbage=3, deflate=True)
    finally:
        doc.close()
        src.close()


def _subset_fonts(doc) -> None:
    """裁剪嵌入字体子集，显著减小文件体积（需 fontTools；失败不致命）。"""
    try:
        doc.subset_fonts()
    except Exception:  # noqa: BLE001
        pass
