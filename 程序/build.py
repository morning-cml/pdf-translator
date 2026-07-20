"""一键打包脚本（C1）。

用法（在 程序/ 目录下）：
    py build.py                     # 完整版（含 OCR），输出到 release/v1.0.0/
    py build.py --profile lite      # 精简版（无 OCR，体积小很多）
    py build.py --set-version 1.1.0 # 改版本号后再构建
    py build.py --zip               # 额外打包成 zip 便于分发
    py build.py --clean             # 只清理构建中间产物
    py build.py --list              # 查看已构建的历史版本

产物布局（多版本互不干扰，不会把文件夹搞乱）：
    release/
      v1.0.0/
        PDF翻译工具/          ← 给用户的整个文件夹（内含 exe）
        PDF翻译工具-v1.0.0-full.zip
        SHA256SUMS.txt         ← 完整性校验
        build_info.json        ← 版本/提交/时间/profile
      v1.1.0/…
    build/                     ← 中间产物，可随时删

关于"防逆向"：本项目采用 AGPL-3.0，**分发时必须提供完整源代码**，因此对
二进制做混淆在法律与实际上都无意义（源码本来就公开），还可能妨碍 AGPL
赋予用户的权利。故默认只做正当加固：
  · 不把任何密钥/配置打进包（config.json 运行时才在用户目录生成）
  · 入包的是字节码而非 .py 源文件（PyInstaller 默认行为）
  · 产出 SHA256 校验清单，便于用户验证未被篡改
  · 预留代码签名钩子（--sign），签名才是对抗"被人二次打包投毒"的正解
若将来改走闭源路线，再启用 --obfuscate（需自行安装 PyArmor 等商业工具）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.version import (APP_NAME, APP_NAME_EN, COPYRIGHT,  # noqa: E402
                         HOMEPAGE, PUBLISHER, __version__)

BUILD_DIR = ROOT / "build"
RELEASE_DIR = ROOT / "release"
ENTRY = ROOT / "webui.py"

# 随程序分发的只读资源（源 → 包内相对路径）
DATA_ITEMS = [
    ("web", "web"),
    ("glossary", "glossary"),
    ("config.example.json", "."),
]
# 放在用户可见目录（不进 exe，便于查看/编辑）
DOC_ITEMS = [
    (ROOT.parent / "LICENSE", "LICENSE.txt"),
    (ROOT.parent / "NOTICE.md", "NOTICE.md"),
    (ROOT.parent / "使用说明.html", "使用说明.html"),
]

# OCR 与版面模型体积极大，精简版不含
OCR_MODULES = ["rapidocr_onnxruntime", "onnxruntime", "cv2"]
HIDDEN_IMPORTS = [
    "pdfplumber", "reportlab", "pypdf", "requests", "fitz", "docx",
    "webview", "tkinter", "tkinter.filedialog",
]


def log(msg: str):
    print(f"  {msg}", flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(ROOT), capture_output=True, text=True)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                             capture_output=True, text=True)
        return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 版本管理
# ---------------------------------------------------------------------------

def set_version(new: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        sys.exit(f"版本号格式应为 主.次.修订，例如 1.2.0（收到 {new!r}）")
    f = ROOT / "src" / "version.py"
    text = f.read_text(encoding="utf-8")
    text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', text)
    f.write_text(text, encoding="utf-8", newline="\n")
    log(f"版本号已更新为 {new}（记得提交 src/version.py）")


def list_releases() -> None:
    if not RELEASE_DIR.is_dir():
        print("尚无任何已构建版本。")
        return
    print(f"{'版本':<12}{'构建时间':<22}{'profile':<8}{'大小':>10}")
    print("-" * 54)
    for d in sorted(RELEASE_DIR.iterdir()):
        info = d / "build_info.json"
        if not info.is_file():
            continue
        meta = json.loads(info.read_text(encoding="utf-8"))
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"{meta.get('version', '?'):<12}"
              f"{meta.get('built_at', '?')[:19]:<22}"
              f"{meta.get('profile', '?'):<8}{size / 1048576:>9.1f}M")


# ---------------------------------------------------------------------------
# 图标：从皮肤里的原创吉祥物 SVG 生成 .ico（全自动，无需美术资源）
# ---------------------------------------------------------------------------

def make_icon(out: Path) -> Path | None:
    try:
        import urllib.parse

        import fitz
        from PIL import Image
        css = (ROOT / "web" / "themes" / "sponge" / "theme.css").read_text(
            encoding="utf-8")
        m = re.search(r'url\("data:image/svg\+xml,([^"]+)"\)', css)
        if not m:
            return None
        svg = urllib.parse.unquote(m.group(1))
        tmp_svg = BUILD_DIR / "icon.svg"
        tmp_svg.parent.mkdir(parents=True, exist_ok=True)
        tmp_svg.write_text(svg, encoding="utf-8")
        doc = fitz.open(str(tmp_svg))
        png = BUILD_DIR / "icon.png"
        doc[0].get_pixmap(matrix=fitz.Matrix(16, 16), alpha=True).save(str(png))
        doc.close()
        img = Image.open(png).convert("RGBA")
        img.save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        log(f"图标已生成：{out.name}")
        return out
    except Exception as e:  # noqa: BLE001
        log(f"[i] 图标生成跳过（{e}）")
        return None


def make_version_file(path: Path, version: str) -> Path:
    """Windows 文件属性（右键→属性→详细信息 里显示的信息）。"""
    v = version.split(".") + ["0"]
    quad = ", ".join(v[:4])
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404B0', [
      StringStruct('CompanyName', '{PUBLISHER}'),
      StringStruct('FileDescription', '{APP_NAME} - 保留排版的文档翻译工具'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{APP_NAME_EN}'),
      StringStruct('LegalCopyright', '{COPYRIGHT}'),
      StringStruct('OriginalFilename', '{APP_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('Comments', '{HOMEPAGE}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def build(profile: str, do_zip: bool, sign_cmd: str | None,
          obfuscate: bool) -> Path:
    version = __version__
    # full 与 lite 是同一版本的两个产物，各自独立目录，互不覆盖
    out_root = RELEASE_DIR / f"v{version}-{profile}"
    app_dir = out_root / APP_NAME

    if out_root.exists():
        log(f"清理已存在的 {out_root.relative_to(ROOT)}")
        shutil.rmtree(out_root)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    meta = {
        "version": version, "profile": profile, "commit": git_commit(),
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "python": sys.version.split()[0],
        "dirty_worktree": git_dirty(),
    }
    (BUILD_DIR / "build_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if meta["dirty_worktree"]:
        log("[!] 工作区有未提交改动——发布版建议先提交，便于追溯来源")

    if obfuscate:
        log("[!] --obfuscate 已请求，但当前许可证为 AGPL-3.0：分发必须附源码，"
            "混淆无实际意义且可能违反许可。已跳过。")

    icon = make_icon(BUILD_DIR / "app.ico")
    verfile = make_version_file(BUILD_DIR / "version_info.txt", version)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--name", APP_NAME, "--windowed",
           "--distpath", str(out_root), "--workpath", str(BUILD_DIR),
           "--specpath", str(BUILD_DIR),
           "--version-file", str(verfile)]
    if icon:
        cmd += ["--icon", str(icon)]
    for src, dst in DATA_ITEMS:
        p = ROOT / src
        if p.exists():
            cmd += ["--add-data", f"{p}{os.pathsep}{dst}"]
    cmd += ["--add-data",
            f"{BUILD_DIR / 'build_info.json'}{os.pathsep}."]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    if profile == "lite":
        for mod in OCR_MODULES:
            cmd += ["--exclude-module", mod]
    cmd += ["--exclude-module", "pytest", "--exclude-module", "matplotlib"]
    cmd.append(str(ENTRY))

    log(f"开始打包（profile={profile}，PyInstaller）……")
    run(cmd)

    # 用户可见的文档与启动说明
    for src, name in DOC_ITEMS:
        if Path(src).exists():
            shutil.copy2(src, app_dir / name)
    (app_dir / "首次使用请看我.txt").write_text(
        f"""{APP_NAME} v{version}

双击 {APP_NAME}.exe 即可启动，无需安装 Python。

· 首次运行会在本文件夹下创建 data\\ 目录，保存你的设置与翻译缓存。
  （若本程序放在 Program Files 等受保护位置，则改存到 %APPDATA%\\{APP_NAME}）
· 详细使用方法见 使用说明.html（双击用浏览器打开）。
· 本软件为自由软件，依据 AGPL-3.0 发布，源代码：
  {HOMEPAGE}
  你有权获取、修改并再分发本程序的完整源代码，详见 LICENSE.txt。

{COPYRIGHT}
""", encoding="utf-8")

    if sign_cmd:
        exe = app_dir / f"{APP_NAME}.exe"
        log(f"代码签名：{sign_cmd} {exe.name}")
        run(sign_cmd.split() + [str(exe)])

    shutil.copy2(BUILD_DIR / "build_info.json", out_root / "build_info.json")

    if do_zip:
        zip_path = out_root / f"{APP_NAME}-v{version}-{profile}.zip"
        log("正在压缩……")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for f in app_dir.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(app_dir.parent))
        log(f"已生成 {zip_path.name}（{zip_path.stat().st_size / 1048576:.1f} MB）")

    write_checksums(out_root)
    return out_root


def write_checksums(out_root: Path) -> None:
    lines = []
    for f in sorted(out_root.rglob("*")):
        if f.is_file() and f.name != "SHA256SUMS.txt":
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            lines.append(f"{h}  {f.relative_to(out_root).as_posix()}")
    (out_root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")
    log(f"完整性清单：SHA256SUMS.txt（{len(lines)} 个文件）")


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{APP_NAME} 打包工具")
    ap.add_argument("--profile", choices=["full", "lite"], default="full",
                    help="full=含扫描版 OCR；lite=不含（体积小很多）")
    ap.add_argument("--zip", action="store_true", help="额外打包 zip")
    ap.add_argument("--set-version", metavar="X.Y.Z", help="更新版本号后再构建")
    ap.add_argument("--sign", metavar="CMD",
                    help='代码签名命令，如 "signtool sign /fd SHA256 /a"')
    ap.add_argument("--obfuscate", action="store_true",
                    help="代码混淆（AGPL 下无意义，当前会被忽略）")
    ap.add_argument("--clean", action="store_true", help="仅清理中间产物")
    ap.add_argument("--list", action="store_true", help="列出已构建版本")
    args = ap.parse_args()

    if args.list:
        list_releases()
        return 0
    if args.clean:
        for d in (BUILD_DIR,):
            if d.exists():
                shutil.rmtree(d)
                log(f"已删除 {d.name}/")
        return 0
    if args.set_version:
        set_version(args.set_version)
        import importlib

        import src.version as v
        importlib.reload(v)
        globals()["__version__"] = v.__version__

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("缺少打包组件，请先运行：py -m pip install pyinstaller")

    out = build(args.profile, args.zip, args.sign, args.obfuscate)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\n构建完成 ✔  {out.relative_to(ROOT)}  共 {size / 1048576:.1f} MB")
    print(f"给用户的文件夹：{(out / APP_NAME).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
