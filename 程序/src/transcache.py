"""持久化翻译缓存（T1）。

同一段原文在同一模型/领域/上下文下的译文落盘复用——重跑同一篇文档
（改排版内核、断点续译、补失败段）不再重复计费。借鉴 PDFMathTranslate
的磁盘缓存思路。

- 键：sha1(scope | 原文)，scope = "模型|领域|上下文哈希"（由调用方拼好）。
- 值：译文字符串。
- 存储：JSON 单文件，原子替换写入；线程安全（翻译是多线程并发的）。
- 只缓存**成功**的译文；占位符校验失败回退原文的段不缓存（下次应重试）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Optional


class TransCache:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._dirty = False
        self._data: dict = {}
        # 清理上次进程被强杀时可能残留的半截临时文件（精准，只删本文件的 .tmp）
        try:
            self.path.with_name(self.path.name + ".tmp").unlink()
        except OSError:
            pass
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
        except Exception:  # noqa: BLE001 — 缓存损坏则弃用重建，不影响翻译
            self._data = {}

    @staticmethod
    def make_key(scope: str, text: str) -> str:
        h = hashlib.sha1()
        h.update(scope.encode("utf-8"))
        h.update(b"|")
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if self._data.get(key) != value:
                self._data[key] = value
                self._dirty = True

    def flush(self) -> None:
        """原子落盘（每批译完调用一次，中途崩溃也不丢已完成的段）。"""
        with self._lock:
            if not self._dirty:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(self._data, ensure_ascii=False),
                               encoding="utf-8")
                os.replace(tmp, self.path)
                self._dirty = False
            except Exception:  # noqa: BLE001 — 落盘失败不致命，下批再试
                pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
