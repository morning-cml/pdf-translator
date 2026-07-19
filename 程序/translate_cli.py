"""命令行入口：批量/脚本化翻译 PDF。

示例：
    python translate_cli.py paper.pdf                    # 译文覆盖，输出 paper_zh.pdf
    python translate_cli.py paper.pdf -o out.pdf --mode bilingual
    python translate_cli.py paper.pdf --mock             # 离线占位翻译（测试排版）
    python translate_cli.py paper.pdf --api-key sk-xxx --model deepseek-v4-pro
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import load_config
from src.pipeline import output_suffix, translate_document
from src.translator import TranslatorError


def main() -> int:
    ap = argparse.ArgumentParser(description="科研文档英译中工具（PDF / Word）")
    ap.add_argument("input", help="输入文件路径（.pdf 或 .docx）")
    ap.add_argument("-o", "--output", help="输出 PDF 路径（默认 <输入名>_zh.pdf）")
    ap.add_argument("--mode", choices=["translated", "bilingual", "sidebyside"],
                    help="translated=纯译文；bilingual=双语·前后页；sidebyside=双语·左右对照宽页")
    ap.add_argument("--model", help="DeepSeek 模型（如 deepseek-v4-flash / deepseek-v4-pro）")
    ap.add_argument("--api-key", dest="api_key", help="DeepSeek API Key")
    ap.add_argument("--glossary", help="术语库 CSV 路径")
    ap.add_argument("--mock", action="store_true", help="使用离线占位翻译（不联网，用于测试）")
    ap.add_argument("--backend", choices=["auto", "pymupdf", "reportlab"],
                    help="回填后端：auto=优先 PyMuPDF；pymupdf=强制；reportlab=兼容覆盖")
    ap.add_argument("--font", dest="font_path", help="中文字体文件路径（ttf/otf），默认自动找 fonts/ 目录")
    ap.add_argument("--pages", type=int, default=None,
                    help="试译模式：只翻译前 N 页（其余页保留原文，便宜预览）")
    ap.add_argument("--no-cache", action="store_true",
                    help="禁用持久化翻译缓存（默认开启，同段落重跑不重复计费）")
    ap.add_argument("--domain", help="学科领域（影响翻译口径，默认 计算机科学）")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"找不到输入文件：{in_path}")
        return 1
    ext = in_path.suffix.lower()
    if args.output:
        out_path = Path(args.output)
    else:
        mode = args.mode or "translated"
        out_path = in_path.with_name(
            in_path.stem + output_suffix(mode, ext) + ext)

    cfg = load_config(
        output_mode=args.mode,
        model=args.model,
        api_key=args.api_key,
        glossary_path=args.glossary,
        render_backend=args.backend,
        font_path=args.font_path,
        max_pages=args.pages,
        use_cache=(False if args.no_cache else None),
        domain=args.domain,
    )

    def progress(msg: str, frac: float):
        print(f"[{int(frac * 100):3d}%] {msg}")

    try:
        res = translate_document(str(in_path), str(out_path), cfg,
                                 mock=args.mock, progress=progress)
    except TranslatorError as e:
        print(f"\n翻译失败：{e}")
        return 2
    scope = f"{res['pages']} 页，" if res.get("pages") else ""
    print(f"\n完成：{scope}{res['blocks']} 段，输出 → {res['output']}"
          f"（模式：{res['mode']}，后端：{res.get('backend', '?')}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
