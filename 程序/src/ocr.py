"""扫描版 PDF 的 OCR 支持（RapidOCR / onnxruntime）。

选型理由（交接文档 §9）：pip 可装、不依赖 torch、检测/识别模型内置在
wheel 里——安装完成后**离线可用**，符合"非程序员双击即用"的定位。

接入方式：每个 OCR 识别出的文本行伪装成一个 pdfplumber 风格的"词"字典
（text/x0/x1/top/bottom/size/color），交回 pdf_parser 现有的分栏/分行/
分段/公式管线原样处理。坐标从渲染像素除以缩放还原为 PDF pt（左上角原点，
与 pdfplumber/fitz 一致）。

写回注意：扫描页没有文字层，redaction 抹不掉像素——写回端对 from_ocr
块改用白底覆盖（fitz 后端 draw_rect；reportlab 后端本来就是覆盖方案）。
"""
from __future__ import annotations

from typing import List, Optional

_DPI = 200          # OCR 渲染分辨率：200dpi 在速度与小字识别率间平衡
_MIN_SCORE = 0.5    # 低于该置信度的识别行丢弃


def available() -> bool:
    """rapidocr 与 pymupdf（渲染位图用）都可导入才认为 OCR 可用。"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        import fitz  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class OcrEngine:
    """按文件持有 fitz 文档与 RapidOCR 实例（均懒加载，模型只初始化一次）。"""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self._doc = None
        self._ocr = None

    def _ensure(self):
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            # use_cls=False：方向分类器会把部分正立文本行误判为 180° 倒置，
            # 旋转后识别成乱码/单字符而被丢弃（实测每页漏掉数行正文）。
            # 页面图由我们自己渲染、必为正立，直接关掉。代价：整页倒扫的
            # 文档不再自动纠正（罕见，记入已知限制）。
            # det_limit 放宽：防止大页面图在检测阶段被缩到丢失小字行。
            self._ocr = RapidOCR(det_limit_side_len=2400,
                                 det_limit_type="max",
                                 use_cls=False)
        if self._doc is None:
            import fitz
            self._doc = fitz.open(self.pdf_path)

    def close(self):
        try:
            if self._doc is not None:
                self._doc.close()
        except Exception:  # noqa: BLE001
            pass
        self._doc = None

    def words_for_page(self, page_index: int) -> List[dict]:
        """渲染该页 → OCR → 每个识别行返回一个"词"字典；失败返回 []。"""
        try:
            return self._words_for_page(page_index)
        except Exception:  # noqa: BLE001
            return []

    def _words_for_page(self, page_index: int) -> List[dict]:
        self._ensure()
        import fitz
        import numpy as np

        page = self._doc[page_index]
        scale = _DPI / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)
        if pix.n == 3:
            img = np.ascontiguousarray(img[:, :, ::-1])  # RGB → BGR（cv2 习惯）

        result, _ = self._ocr(img)
        words: List[dict] = []
        for item in result or []:
            box, text, score = item[0], str(item[1]).strip(), float(item[2])
            if not text or score < _MIN_SCORE:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            x0, x1 = min(xs) / scale, max(xs) / scale
            top, bottom = min(ys) / scale, max(ys) / scale
            h = bottom - top
            if x1 - x0 < 1.0 or h < 1.0:
                continue
            # 竖排/旋转文字（页边"Downloaded from…"等）：窄高框。这类框与
            # 整栏每一行都垂直相交，会把相邻行链式粘成乱序巨行——丢弃，
            # 原像素保持不动（本来也不参与翻译）。
            if h > 2.0 * (x1 - x0) and h > 25.0:
                continue
            words.append({
                "text": text,
                "x0": x0, "x1": x1, "top": top, "bottom": bottom,
                # 检测框高 ≈ 上伸部+下降部，字号取 ~0.8 倍框高
                "size": max(4.0, min(h * 0.8, 48.0)),
                "upright": True,
                "fontname": "OCR",
                "non_stroking_color": (0.0, 0.0, 0.0),
            })
        return words
