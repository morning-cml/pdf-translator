"""可选版面分析插件（T11）：DocLayout-YOLO（DocStructBench，onnx 版）。

PDFMathTranslate / BabelDOC 与启发式方案的本质差距在这里——用检测模型识别
表格 / 图区 / 独立公式，复杂版式鲁棒性更好。本插件保持**保守接入**：

  · `table` / `isolate_formula` 区内的可译块 → 改为不译（保留原样，防止
    表格内文字被重排搅乱）；
  · `table` / `figure` 框 → 并入页面障碍物（补启发式图形检测的盲区）。

模型文件（约 40MB）放 `程序/models/doclayout_yolo_docstructbench_imgsz1024.onnx`
即自动启用；没有该文件则完全不参与（零行为差异）。下载（国内镜像）：
  https://hf-mirror.com/wybxc/DocLayout-YOLO-DocStructBench-onnx
推理用 onnxruntime（随 OCR 组件已装），无需 torch / 显卡。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = ROOT / "models" / "doclayout_yolo_docstructbench_imgsz1024.onnx"

_IMGSZ = 1024
_CONF = 0.40
# DocStructBench 类别表
_CLASSES = {0: "title", 1: "plain_text", 2: "abandon", 3: "figure",
            4: "figure_caption", 5: "table", 6: "table_caption",
            7: "table_footnote", 8: "isolate_formula", 9: "formula_caption"}
_KEEP = {"table", "figure", "isolate_formula"}

Rect = Tuple[float, float, float, float]


def model_path(explicit: str = "") -> Optional[Path]:
    p = Path(explicit) if explicit else DEFAULT_MODEL
    if explicit and not p.is_absolute():
        p = ROOT / p
    return p if p.exists() else None


def available(explicit: str = "") -> bool:
    if model_path(explicit) is None:
        return False
    try:
        import onnxruntime  # noqa: F401
        import fitz  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class LayoutModel:
    """懒加载 onnx 会话；detect() 返回 [(类别, x0, top, x1, bottom)]（pt）。"""

    def __init__(self, path: str = ""):
        self._path = model_path(path)
        self._sess = None
        self._doc = None
        self.broken = False

    def _ensure(self, pdf_path: str):
        if self._sess is None:
            import onnxruntime as ort
            self._sess = ort.InferenceSession(
                str(self._path), providers=["CPUExecutionProvider"])
        if self._doc is None:
            import fitz
            self._doc = fitz.open(pdf_path)

    def close(self):
        try:
            if self._doc is not None:
                self._doc.close()
        except Exception:  # noqa: BLE001
            pass
        self._doc = None

    def detect(self, pdf_path: str, page_index: int) -> List[Tuple[str, Rect]]:
        if self.broken:
            return []
        try:
            return self._detect(pdf_path, page_index)
        except Exception:  # noqa: BLE001
            self.broken = True   # 任何一次失败即整体停用，绝不影响主流程
            return []

    def _detect(self, pdf_path: str, page_index: int) -> List[Tuple[str, Rect]]:
        import fitz
        import numpy as np

        self._ensure(pdf_path)
        page = self._doc[page_index]
        scale = 2.0                       # ~144dpi 渲染，1024 输入足够
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8)
        img = img.reshape(pix.height, pix.width, pix.n)[:, :, :3]

        h0, w0 = img.shape[:2]
        r = min(_IMGSZ / w0, _IMGSZ / h0)
        nw, nh = int(round(w0 * r)), int(round(h0 * r))
        import cv2
        resized = cv2.resize(img, (nw, nh))
        canvas = np.full((_IMGSZ, _IMGSZ, 3), 114, dtype=np.uint8)
        dx, dy = (_IMGSZ - nw) // 2, (_IMGSZ - nh) // 2
        canvas[dy:dy + nh, dx:dx + nw] = resized
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0

        name = self._sess.get_inputs()[0].name
        out = self._sess.run(None, {name: blob})[0]
        det = out[0] if out.ndim == 3 else out    # (300, 6): x1,y1,x2,y2,conf,cls
        if det.ndim != 2 or det.shape[-1] < 6:
            raise ValueError(f"unexpected output shape {out.shape}")

        results: List[Tuple[str, Rect]] = []
        for x1, y1, x2, y2, conf, cls in det:
            if conf < _CONF:
                continue
            label = _CLASSES.get(int(cls), "")
            if label not in _KEEP:
                continue
            # letterbox 逆变换 → 渲染像素 → pt
            px0 = (min(x1, x2) - dx) / r / scale
            px1 = (max(x1, x2) - dx) / r / scale
            py0 = (min(y1, y2) - dy) / r / scale
            py1 = (max(y1, y2) - dy) / r / scale
            if px1 - px0 < 8 or py1 - py0 < 8:
                continue
            results.append((label, (px0, py0, px1, py1)))
        return results


def apply_to_layout(layout, dets: List[Tuple[str, Rect]]) -> int:
    """把检测结果保守地并入 PageLayout：返回被改为不译的块数。

    表格的处理与 B1（逐单元格翻译）协同：
      · 若解析器已在该表格区检出单元格 → **交给逐格路径翻译**，此处不干预；
      · 若没检出（无框线表格等）→ 退回"整区保留原样"，避免整行合并重排把
        表格结构搅坏。独立公式区始终保留原样。
    """
    changed = 0
    cells = getattr(layout, "table_cells", None) or []

    def has_cells(r: Rect) -> bool:
        x0, y0, x1, y1 = r
        return any(x0 - 2 <= (c[0] + c[2]) / 2 <= x1 + 2
                   and y0 - 2 <= (c[1] + c[3]) / 2 <= y1 + 2 for c in cells)

    preserve = []
    for label, r in dets:
        if label in ("table", "figure"):
            layout.obstacles.append(r)
        if label == "isolate_formula" or (label == "table" and not has_cells(r)):
            preserve.append(r)

    for b in layout.blocks:
        if not b.translatable or getattr(b, "cell_rect", None):
            continue
        cx, cy = (b.x0 + b.x1) / 2, (b.top + b.bottom) / 2
        if any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in preserve):
            b.translatable = False
            changed += 1
    return changed
