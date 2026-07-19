"""配置加载。

优先级（从高到低）：
    1. 传入的显式参数
    2. 项目根目录的 config.json
    3. 环境变量 DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL
    4. 内置默认值
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from .paths import data_file, resource_dir, user_path

# 只读资源根（源码运行=程序/；打包运行=解包目录）
ROOT = resource_dir()
# config.json 必须写在**可写且跨版本保留**的位置，打包后不能落在临时解包目录
CONFIG_PATH = user_path("config.json")

DEFAULTS = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-pro",
    "temperature": 1.0,
    "source_lang": "英文",
    "target_lang": "中文",
    # translated = 纯译文（覆盖原文）；bilingual = 双语对照（原文页 + 译文页）
    "output_mode": "translated",
    "glossary_path": "glossary/cs_terms.csv",
    "batch_size": 8,       # 每次 API 调用翻译的段落数
    "max_workers": 8,      # 并发请求数（越大越快，占用更多 token/连接）
    "thinking": True,      # 默认开启=质量优先（更准）；设 False 可明显提速
    "use_system_proxy": True,  # False=忽略系统/VPN代理，直连
    "proxy": "",               # 如 "http://127.0.0.1:7890"，留空表示不指定
    "verify_ssl": True,        # False=跳过证书校验（安全软件MITM时用，谨慎）
    # 回填后端：auto=优先 PyMuPDF、缺则回退 reportlab；可强制 pymupdf / reportlab
    "render_backend": "auto",
    # 中文字体：留空则自动找 fonts/ 目录（思源/Noto 等 ttf/otf），找不到用内置
    "font_path": "",
    # 持久化翻译缓存（cache/translations.json）：重跑同一文档不重复计费
    "use_cache": True,
    # 学科领域：影响翻译提示词口径（计算机科学/通用学术/生物医学/物理学…）
    "domain": "计算机科学",
    # 试译页数：0=翻译整篇；N=只翻译前 N 页（其余页保留原文，便宜预览）
    "max_pages": 0,
    # 计费单价（元/百万 token，用于翻译前成本预估显示；0=只显示 token 数）
    "price_in": 0.0,
    "price_out": 0.0,
}


@dataclass
class Config:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    temperature: float = 1.0
    source_lang: str = "英文"
    target_lang: str = "中文"
    output_mode: str = "translated"
    glossary_path: str = "glossary/cs_terms.csv"
    batch_size: int = 8
    max_workers: int = 8
    thinking: bool = True
    use_system_proxy: bool = True
    proxy: str = ""
    verify_ssl: bool = True
    render_backend: str = "auto"
    font_path: str = ""
    use_cache: bool = True
    domain: str = "计算机科学"
    max_pages: int = 0
    price_in: float = 0.0
    price_out: float = 0.0

    def resolved_glossary_path(self) -> Path:
        p = Path(self.glossary_path)
        if p.is_absolute():
            return p
        # 相对路径：优先用用户自己编辑过的副本，否则用随程序分发的默认术语库
        return data_file(*p.parts)

    def to_dict(self) -> dict:
        return asdict(self)


def _from_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[config] 读取 config.json 失败，忽略：{e}")
    return {}


def _from_env() -> dict:
    out = {}
    if os.environ.get("DEEPSEEK_API_KEY"):
        out["api_key"] = os.environ["DEEPSEEK_API_KEY"]
    if os.environ.get("DEEPSEEK_MODEL"):
        out["model"] = os.environ["DEEPSEEK_MODEL"]
    if os.environ.get("DEEPSEEK_BASE_URL"):
        out["base_url"] = os.environ["DEEPSEEK_BASE_URL"]
    return out


def load_config(**overrides) -> Config:
    """按优先级合并配置。overrides 中值为 None 的键会被忽略。"""
    merged = dict(DEFAULTS)
    merged.update(_from_file())
    merged.update(_from_env())
    merged.update({k: v for k, v in overrides.items() if v is not None})
    # 仅保留 Config 已知字段
    known = {f for f in Config().__dict__}
    merged = {k: v for k, v in merged.items() if k in known}
    return Config(**merged)


def save_config(cfg: Config) -> None:
    """把当前配置写入 config.json（含 API Key，请勿提交到版本库）。"""
    data = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)  # 原子替换，避免写一半导致文件损坏
