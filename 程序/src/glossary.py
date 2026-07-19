"""术语库：加载英中术语对照，并为待译文本挑选相关术语注入提示词。

CSV 格式（含表头）：en,zh,note
匹配采用「按单词边界、忽略大小写」的方式，优先匹配较长的短语，
以保证例如 "convolutional neural network" 先于 "neural network" 命中。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Tuple


class Glossary:
    def __init__(self, entries: Dict[str, str]):
        # entries: 小写英文 -> 中文
        self.entries = entries
        # 按短语长度降序，便于长短语优先匹配
        self._sorted_terms = sorted(entries.keys(), key=len, reverse=True)
        # 预编译匹配正则（整词/短语边界）
        if self._sorted_terms:
            pattern = "|".join(re.escape(t) for t in self._sorted_terms)
            # (?<![A-Za-z0-9]) ... (?![A-Za-z0-9]) 保证边界，避免 "index" 命中 "indexed"
            self._regex = re.compile(
                r"(?<![A-Za-z0-9])(" + pattern + r")(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
        else:
            self._regex = None

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        path = Path(path)
        entries: Dict[str, str] = {}
        if not path.exists():
            print(f"[glossary] 术语库不存在：{path}，将不使用术语库。")
            return cls(entries)
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                en = (row.get("en") or "").strip()
                zh = (row.get("zh") or "").strip()
                if en and zh:
                    entries[en.lower()] = zh
        print(f"[glossary] 已加载术语 {len(entries)} 条：{path}")
        return cls(entries)

    def relevant_terms(self, text: str) -> List[Tuple[str, str]]:
        """返回文本中出现的术语列表 [(原文形态, 中文)]，去重、保序。"""
        if not self._regex:
            return []
        seen = set()
        found: List[Tuple[str, str]] = []
        for m in self._regex.finditer(text):
            surface = m.group(0)
            zh = self.entries.get(surface.lower())
            key = surface.lower()
            if zh and key not in seen:
                seen.add(key)
                found.append((surface, zh))
        return found

    def prompt_block(self, texts: List[str], limit: int = 40) -> str:
        """汇总多段文本中出现的术语，生成注入提示词的对照表；无则返回空串。"""
        collected: Dict[str, str] = {}
        for t in texts:
            for surface, zh in self.relevant_terms(t):
                collected.setdefault(surface.lower(), zh)
                if len(collected) >= limit:
                    break
        if not collected:
            return ""
        lines = [f"- {en} → {zh}" for en, zh in collected.items()]
        return "术语对照表（翻译时必须严格遵守以下译法）：\n" + "\n".join(lines)

    def __len__(self) -> int:
        return len(self.entries)
