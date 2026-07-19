"""运行路径解析：源码运行与打包运行（PyInstaller）共用一套规则。

打包后目录结构变了，必须区分两类路径，否则程序一冻结就找不到文件：

* **资源目录**（只读，随程序分发）：web 前端、默认术语库、皮肤
  - 源码运行 → `程序/`
  - 打包运行 → PyInstaller 的临时解包目录 `sys._MEIPASS`（每次启动重建）

* **用户数据目录**（可写，跨版本保留）：config.json（含 API Key）、翻译缓存、
  版面模型、用户字体
  - 源码运行 → `程序/`（保持现状，开发时一切在眼前）
  - 打包运行 → exe 同级的 `data/`（便携优先）；若该处不可写（如装在
    Program Files），退回 `%APPDATA%/PDF翻译工具/`

**关键**：用户数据绝不能放进 `_MEIPASS`——那是临时目录，程序一退出就被删，
API Key 和翻译缓存会凭空消失。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PDF翻译工具"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_dir() -> Path:
    """只读资源根目录。"""
    if is_frozen():
        return Path(sys._MEIPASS)          # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


_user_dir_cache: Path | None = None


def user_dir() -> Path:
    """可写的用户数据根目录（结果缓存，避免重复探测写权限）。"""
    global _user_dir_cache
    if _user_dir_cache is not None:
        return _user_dir_cache

    if not is_frozen():
        _user_dir_cache = Path(__file__).resolve().parent.parent
        return _user_dir_cache

    portable = Path(sys.executable).resolve().parent / "data"
    if _writable(portable):
        _user_dir_cache = portable
    else:
        appdata = Path(os.environ.get("APPDATA")
                       or Path.home() / "AppData" / "Roaming")
        fallback = appdata / APP_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        _user_dir_cache = fallback
    return _user_dir_cache


def resource(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def user_path(*parts: str) -> Path:
    return user_dir().joinpath(*parts)


def data_file(*parts: str) -> Path:
    """优先用用户目录下的副本（用户可自行编辑，如术语库），
    没有则回退到随程序分发的只读资源。"""
    p = user_path(*parts)
    if p.exists():
        return p
    return resource(*parts)


def build_info() -> dict:
    """读取打包时写入的版本信息；源码运行时返回开发态标记。"""
    import json
    f = resource("build_info.json")
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"version": "dev", "commit": "", "built_at": "", "profile": "source"}
