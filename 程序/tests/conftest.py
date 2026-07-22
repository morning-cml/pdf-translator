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


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_config(tmp_path_factory):
    """让所有用例都从**纯默认配置**起步，不读开发机上的真实 config.json / 环境变量。

    源码模式下 `load_config()` 会读 `程序/config.json`——你自己用软件时存下的
    设置（如 output_mode=sidebyside、batch_size=12）会渗进断言，导致"CI 干净所以
    全绿、本机却红"的假象。把 CONFIG_PATH 指到空临时目录、清掉相关环境变量即可
    彻底隔离。需要特定配置的用例仍可显式 `load_config(output_mode=...)`。

    必须是 **session 级**：docx/pptx 等用例的 `translated` 夹具是 module 级、
    在建立时就调用 `load_config()`；只有比它更早建立的 session 夹具才拦得住。
    """
    import src.config as _config
    mp = pytest.MonkeyPatch()
    fake = tmp_path_factory.mktemp("home") / "config.json"
    mp.setattr(_config, "CONFIG_PATH", fake)
    for var in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        mp.delenv(var, raising=False)
    yield
    mp.undo()


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
