"""生成"扫描版"测试 PDF：把源 PDF 每页渲染成位图再包成无文字层的 PDF。

用法（在 程序 目录下）：
    python samples/make_scanned.py                     # 取真实论文前 3 页
    python samples/make_scanned.py 源.pdf 输出.pdf 5   # 自定源/输出/页数

用于回归 OCR 管线：输出文件没有任何文字层，只有整页图像。
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT.parent / ("Observing a robot peer's failures facilitates "
                             "students' classroom learning.pdf")
FALLBACK_SRC = ROOT / "samples" / "sample_paper.pdf"
DEFAULT_OUT = ROOT / "samples" / "sample_scanned.pdf"
DPI = 150


def make_scanned(src: Path, out: Path, max_pages: int = 3) -> int:
    doc = fitz.open(str(src))
    scan = fitz.open()
    n = min(len(doc), max_pages)
    for i in range(n):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72), alpha=False)
        new = scan.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    scan.save(str(out), deflate=True)
    scan.close()
    doc.close()
    return n


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        DEFAULT_SRC if DEFAULT_SRC.exists() else FALLBACK_SRC)
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    pages = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    n = make_scanned(src, out, pages)
    print(f"已生成扫描版样张：{out}（{n} 页，来自 {src.name}）")
