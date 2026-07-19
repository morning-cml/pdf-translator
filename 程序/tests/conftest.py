"""pytest 公共夹具。

运行：在 程序/ 目录下 `py -m pytest tests -q`
（首次需 `py -m pip install pytest`）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 真实论文放在 程序/ 的上一级（用户工作区）；没有则相关用例自动跳过
PAPER = ROOT.parent / ("Observing a robot peer's failures facilitates "
                       "students' classroom learning.pdf")
SAMPLE = ROOT / "samples" / "sample_paper.pdf"


@pytest.fixture(scope="session")
def paper_path():
    if not PAPER.exists():
        pytest.skip("真实论文不在工作区，跳过")
    return str(PAPER)


@pytest.fixture(scope="session")
def sample_path():
    if not SAMPLE.exists():
        pytest.skip("samples/sample_paper.pdf 缺失")
    return str(SAMPLE)


@pytest.fixture(scope="session")
def paper_layouts(paper_path):
    """真实论文的解析结果（整份解析较慢，全会话复用）。"""
    from src.pdf_parser import parse_pdf
    return parse_pdf(paper_path)
