"""翻译客户端。

- DeepSeekTranslator：调用 DeepSeek 的 OpenAI 兼容 /chat/completions 接口。
- MockTranslator：离线占位翻译，用于无网络/无密钥时测试整条流水线。

对外主入口：translate_texts(texts, glossary, progress_cb)
    自动去重、按段落批量、并发请求、缓存、注入术语库。
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional

import requests

from .glossary import Glossary


class TranslatorError(Exception):
    pass


_MARKER = re.compile(r"\[\[(\d+)\]\]\s*(.*?)(?=\[\[\d+\]\]|\Z)", re.DOTALL)

# 行内公式占位符（与 pdf_parser.PLACEHOLDER_FMT 一致）
_PH = re.compile(r"⟦F\d+⟧")
_PH_NOTE = (
    "注意：文中形如 ⟦F1⟧、⟦F2⟧ 的标记是公式占位符，"
    "必须原样保留在译文的对应位置，不得翻译、改写、合并或遗漏。\n\n"
)


def _ph_ok(src: str, tgt: str) -> bool:
    """译文中的公式占位符必须与原文完全一致（集合与个数）。"""
    return sorted(_PH.findall(src)) == sorted(_PH.findall(tgt or ""))


class BaseTranslator:
    def __init__(self, batch_size: int = 12, max_workers: int = 4,
                 persist=None, cache_scope: str = ""):
        self.batch_size = max(1, batch_size)
        self.max_workers = max(1, max_workers)
        self._cache: dict[str, str] = {}
        self.ph_failures = 0    # 占位符校验失败（已回退原文）的段数
        self.failed_texts = 0   # 网络/服务失败降级（已回退原文）的段数（T2）
        self.cache_hits = 0     # 持久缓存命中段数（T1）
        # T1 持久化缓存：persist 为 TransCache 实例；scope 参与键（模型/领域/上下文）
        self.persist = persist
        self.cache_scope = cache_scope

    def _persist_key(self, text: str) -> str:
        from .transcache import TransCache
        return TransCache.make_key(self.cache_scope, text)

    # 子类可实现「占位符丢失后的强化重试」；返回 None 表示不支持
    def _translate_strict(self, text: str, glossary_block: str) -> Optional[str]:
        return None

    # 子类实现：翻译一批段落，返回与输入等长的译文列表
    def _translate_batch(self, batch: List[str], glossary_block: str) -> List[str]:
        raise NotImplementedError

    def pending_texts(self, texts: List[str]) -> List[str]:
        """去重并剔除内存/持久缓存命中后，真正需要请求的段（供成本预估）。"""
        unique = list(dict.fromkeys(texts))
        todo = []
        for t in unique:
            if t in self._cache:
                continue
            if self.persist is not None:
                hit = self.persist.get(self._persist_key(t))
                if hit is not None:
                    continue
            todo.append(t)
        return todo

    def translate_texts(
        self,
        texts: List[str],
        glossary: Glossary,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> List[str]:
        # 去重（保留首次出现顺序）；先查内存缓存，再查持久缓存（T1）
        unique = list(dict.fromkeys(texts))
        todo = []
        for t in unique:
            if t in self._cache:
                continue
            if self.persist is not None:
                hit = self.persist.get(self._persist_key(t))
                if hit is not None:
                    self._cache[t] = hit
                    self.cache_hits += 1
                    continue
            todo.append(t)
        batches = [todo[i:i + self.batch_size] for i in range(0, len(todo), self.batch_size)]
        done = 0
        total = len(batches)

        def run(batch):
            block = glossary.prompt_block(batch)
            return self._translate_batch(batch, block)

        if batches:
            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                futures = {ex.submit(run, b): b for b in batches}
                for fut in as_completed(futures):
                    batch = futures[fut]
                    # T2 失败降级：单批彻底失败 → 本批各段回退原文并继续，
                    # 绝不让 90% 进度的任务整体报废；失败段不写缓存，重跑可补。
                    try:
                        results = fut.result()
                    except Exception:  # noqa: BLE001
                        for src in batch:
                            self._cache[src] = src
                        self.failed_texts += len(batch)
                        done += 1
                        if progress_cb:
                            progress_cb(done, total)
                        continue
                    for src, tgt in zip(batch, results):
                        if _ph_ok(src, tgt):
                            self._store(src, tgt)
                            continue
                        # 占位符丢失/变形 → 单段强化重试一次
                        retry = None
                        try:
                            retry = self._translate_strict(src, glossary.prompt_block([src]))
                        except Exception:  # noqa: BLE001
                            retry = None
                        if retry is not None and _ph_ok(src, retry):
                            self._store(src, retry)
                        else:
                            # 保底：保留原文，公式位置绝不丢失；不写持久缓存
                            self.ph_failures += 1
                            self._cache[src] = src
                    if self.persist is not None:
                        self.persist.flush()   # 每批落盘，中途崩溃不丢已译段
                    done += 1
                    if progress_cb:
                        progress_cb(done, total)
        if self.persist is not None:
            self.persist.flush()
        return [self._cache.get(t, t) for t in texts]

    def _store(self, src: str, tgt: str) -> None:
        self._cache[src] = tgt
        if self.persist is not None and tgt and tgt != src:
            self.persist.put(self._persist_key(src), tgt)


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是一名资深学术翻译，专注于{domain}领域，负责将{src}论文准确翻译成{tgt}。"
    "要求：\n"
    "1. 译文专业、准确、通顺，使用学术书面语。\n"
    "2. 保留数学公式、变量符号、代码片段、以及行内英文缩写和专有名词"
    "（如 CNN、GPU、arXiv、URL、引用编号 [12] 等）不翻译。\n"
    "3. 不翻译作者姓名、机构名；参考文献中的英文文献标题保持原样。\n"
    "4. 不要添加解释、注释或原文，只输出译文本身。\n"
    "5. 必须严格遵守用户给出的术语对照表。\n"
    "6. 文中形如 ⟦F1⟧、⟦F2⟧ 的标记是公式占位符：必须原样保留在译文的对应位置，"
    "不得翻译、改写、合并或遗漏。"
)


class DeepSeekTranslator(BaseTranslator):
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
        temperature: float = 1.0,
        source_lang: str = "英文",
        target_lang: str = "中文",
        batch_size: int = 12,
        max_workers: int = 4,
        timeout: int = 120,
        max_retries: int = 3,
        proxy: str = "",
        use_system_proxy: bool = True,
        verify_ssl: bool = True,
        thinking: bool = False,
        persist=None,
        cache_scope: str = "",
        domain: str = "计算机科学",
        doc_context: str = "",
    ):
        super().__init__(batch_size=batch_size, max_workers=max_workers,
                         persist=persist, cache_scope=cache_scope)
        if not api_key:
            raise TranslatorError("未提供 DeepSeek API Key。")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
        self.thinking = thinking  # 翻译无需推理，默认关闭思考模式以大幅提速
        self.system_prompt = SYSTEM_PROMPT.format(
            src=source_lang, tgt=target_lang, domain=domain or "计算机科学")
        if doc_context:
            # T6 全文上下文：标题+摘要片段进 system prompt，各批口径一致
            self.system_prompt += (
                "\n\n本文档主题背景（仅供理解翻译口径，禁止输出到译文中）：\n"
                + doc_context[:400])
        # 独立 Session：可选择是否走系统/环境代理，或指定代理
        self.session = requests.Session()
        self.session.trust_env = use_system_proxy
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        # 连接池随并发扩大，避免高并发时连接排队
        pool = max(10, max_workers + 2)
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ---- 底层 API 调用 ----
    def _chat(self, messages) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
            # 关闭思考链（默认开启会先生成推理再翻译，非常慢）
            "thinking": {"type": "enabled" if self.thinking else "disabled"},
        }
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.post(url, headers=headers, json=payload,
                                         timeout=self.timeout, verify=self.verify_ssl)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                # 兼容非 DeepSeek 服务（Kimi/GLM/豆包/OpenAI/Ollama…）：
                # "thinking" 是 DeepSeek 风格参数，严格校验的服务会报 400，
                # 去掉该字段立刻重试一次。
                if resp.status_code == 400 and "thinking" in payload:
                    payload = {k: v for k, v in payload.items() if k != "thinking"}
                    continue
                # 明确的客户端错误不重试
                if resp.status_code in (401, 402, 403):
                    raise TranslatorError(
                        f"翻译服务返回 {resp.status_code}：{_short(resp.text)}"
                        "（请检查 API Key 是否正确、账户余额是否充足、模型是否已开通）"
                    )
                last_err = TranslatorError(f"HTTP {resp.status_code}: {_short(resp.text)}")
            except requests.exceptions.SSLError as e:
                last_err = TranslatorError(
                    "SSL 连接被中断，通常是 VPN/加速器/代理或安全软件拦截了 HTTPS。\n"
                    "可尝试：① 关闭 VPN/加速器后重试；② 在 config.json 设置 "
                    '"use_system_proxy": false 直连；③ 暂时关闭安全软件的 HTTPS/SSL 扫描。\n'
                    f"（原始错误：{e}）")
            except requests.RequestException as e:
                last_err = TranslatorError(f"网络请求失败：{e}")
            time.sleep(2 ** attempt)  # 指数退避
        raise last_err or TranslatorError("翻译请求失败。")

    # ---- 批量翻译 ----
    def _translate_batch(self, batch: List[str], glossary_block: str) -> List[str]:
        if len(batch) == 1:
            return [self._translate_single(batch[0], glossary_block)]
        numbered = "\n\n".join(f"[[{i + 1}]] {t}" for i, t in enumerate(batch))
        instruction = (
            f"下面有 {len(batch)} 个段落，每段前有形如 [[序号]] 的编号。"
            "请把每个段落翻译成简体中文，并按相同编号输出，格式：\n"
            "[[1]] 译文\n[[2]] 译文\n"
            "务必保证编号数量与输入完全一致，且只输出译文。\n\n"
        )
        if any("⟦F" in t for t in batch):
            instruction += _PH_NOTE
        if glossary_block:
            instruction += glossary_block + "\n\n"
        instruction += "待翻译段落：\n" + numbered
        content = self._chat([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instruction},
        ])
        parsed = {int(i): t.strip() for i, t in _MARKER.findall(content)}
        if len(parsed) == len(batch) and all((i + 1) in parsed for i in range(len(batch))):
            return [parsed[i + 1] for i in range(len(batch))]
        # 编号错位 → 退化为逐段翻译，保证对齐
        return [self._translate_single(t, glossary_block) for t in batch]

    def _translate_single(self, text: str, glossary_block: str) -> str:
        instruction = ""
        if "⟦F" in text:
            instruction += _PH_NOTE
        if glossary_block:
            instruction += glossary_block + "\n\n"
        instruction += "把下面这段文字翻译成简体中文，只输出译文：\n\n" + text
        return self._chat([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instruction},
        ]).strip()

    def _translate_strict(self, text: str, glossary_block: str) -> Optional[str]:
        """占位符校验失败后的强化重试：逐个点名要求保留。"""
        phs = _PH.findall(text)
        instruction = (
            "下面文本包含 " + str(len(phs)) + " 个公式占位符：" + "、".join(phs) +
            "。把文本翻译成简体中文，译文必须一字不差地保留这些占位符，"
            "缺一个都算错误。只输出译文。\n\n"
        )
        if glossary_block:
            instruction += glossary_block + "\n\n"
        instruction += text
        return self._chat([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": instruction},
        ]).strip()


def _short(s: str, n: int = 200) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s[:n]


# ---------------------------------------------------------------------------
# 离线 Mock（用于测试）
# ---------------------------------------------------------------------------

_FILLER = "这是用于测试排版与渲染的中文占位译文内容以便验证图片保留与位置对齐效果"


class MockTranslator(BaseTranslator):
    """不联网的假翻译：套用术语库译法，并生成长度大致相当的中文，便于测试。"""

    def _translate_batch(self, batch: List[str], glossary_block: str) -> List[str]:
        # 从对照表提示块里解析出 en→zh，便于在占位译文中体现术语
        term_map = {}
        for line in glossary_block.splitlines():
            m = re.match(r"-\s*(.+?)\s*→\s*(.+)", line.strip())
            if m:
                term_map[m.group(1).lower()] = m.group(2)
        out = []
        for text in batch:
            phs = _PH.findall(text)
            plain = _PH.sub("", text)
            hits = [zh for en, zh in term_map.items() if re.search(
                r"(?<![a-z0-9])" + re.escape(en) + r"(?![a-z0-9])", plain.lower())]
            n_words = max(1, len(plain.split()))
            target_len = int(n_words * 1.6)
            body = (_FILLER * (target_len // len(_FILLER) + 1))[:target_len]
            prefix = ("【术语：" + "、".join(dict.fromkeys(hits)) + "】") if hits else ""
            # 占位符按原顺序均匀插回，模拟真实译文中公式的位置
            if phs:
                step = max(1, len(body) // (len(phs) + 1))
                chunks = []
                pos = 0
                for i, ph in enumerate(phs):
                    nxt = min(len(body), (i + 1) * step)
                    chunks.append(body[pos:nxt])
                    chunks.append(ph)
                    pos = nxt
                chunks.append(body[pos:])
                body = "".join(chunks)
            out.append(prefix + body)
        return out
