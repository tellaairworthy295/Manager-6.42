import re
import threading
import time
import random
import asyncio
from typing import List, Optional, Any
import requests
from googletrans import Translator

class TranslationPipeline:
    def __init__(
        self,
        max_chunk_size: int = 3000,
        base_delay: float = 2.5,
        max_retries: int = 2,  # ONLY for network errors
        timeout: int = 10,
        enable_cache: bool = True,
    ):
        self.max_chunk_size = max_chunk_size
        self.base_delay = base_delay
        self.max_retries = max_retries
        self.timeout = timeout
        self.enable_cache = enable_cache

        self.cache = {}
        self.translator = Translator()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
        })

    # ------------------------
    # Public API
    # ------------------------
    def translate(self, text: str) -> str:
        if not text or not text.strip():
            return text

        text = text.strip()

        if self.enable_cache and text in self.cache:
            return self.cache[text]

        chunks = self._chunk(text)
        results = []

        for chunk in chunks:
            translated = self._translate_with_pipeline(chunk)
            results.append(translated)

            time.sleep(self.base_delay + random.uniform(0.5, 1.5))

        final = "\n\n".join(results)

        if self.enable_cache:
            self.cache[text] = final

        return final

    # ------------------------
    # Pipeline logic
    # ------------------------
    def _translate_with_pipeline(self, text: str) -> str:
        """Priority:
        1. Google Web API (NO retry on block)
        2. googletrans (async supported)
        3. original text
        """

        # 1️⃣ Google Web (quick fail)
        result = self._google_web(text)
        if self._valid(result):
            return result

        # 2️⃣ googletrans (retry ONLY on network issues)
        for attempt in range(self.max_retries):
            try:
                result = self._googletrans(text)
                if self._valid(result):
                    return result
                else:
                    break  # logical failure → don't retry

            except requests.exceptions.RequestException:
                delay = (2 ** attempt) + random.uniform(0.5, 1.5)
                time.sleep(delay)
                continue
            except Exception:
                break

        return text

    # ------------------------
    # Providers
    # ------------------------
    def _google_web(self, text: str) -> Optional[str]:
        """NO retries. Fail fast if blocked."""
        endpoint = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": text,
        }

        try:
            resp = self.session.get(endpoint, params=params, timeout=self.timeout)

            # 🚨 Detect block immediately
            if "sorry" in resp.url or resp.status_code == 429:
                return None

            resp.raise_for_status()

            data = resp.json()
            if not data or not data[0]:
                return None

            return "".join(item[0] for item in data[0] if item and item[0])

        except requests.exceptions.RequestException:
            # network error → allow retry at higher level (but we don't retry here)
            return None
        except Exception:
            return None

    def _googletrans(self, text: str) -> Optional[str]:
        if not self.translator:
            return None

        # 1. 原始返回值（可能是协程，可能是对象）
        raw_result = self.translator.translate(text, dest="zh-CN")

        # 2. 解析后的结果，明确告诉 PyCharm 这是 Any 类型
        final_result: Any = self._run_sync_safely(raw_result) if asyncio.iscoroutine(raw_result) else raw_result

        # ✅ 现在 PyCharm 知道 final_result 是 Any，不会再警告缺少 text 属性了
        return final_result.text if final_result else None

    def _run_sync_safely(self, coro):
        """
        安全地将协程在同步环境中运行，完美避开 "Event loop is running" 错误
        """
        try:
            # 检查当前线程是否有正在运行的 loop
            asyncio.get_running_loop()
            has_running_loop = True
        except RuntimeError:
            has_running_loop = False

        if not has_running_loop:
            # 情况 A: 当前没有正在运行的事件循环，直接安全运行
            return asyncio.run(coro)

        # 情况 B: 当前已有正在运行的事件循环
        # 必须开启一个新线程，在新线程的全新事件循环中运行协程
        thread_result = None
        thread_exception = None

        def thread_target():
            nonlocal thread_result, thread_exception
            try:
                # 在新线程中，asyncio.run 会自动创建并管理一个新的事件循环
                thread_result = asyncio.run(coro)
            except Exception as e:
                thread_exception = e

        t = threading.Thread(target=thread_target)
        t.start()
        t.join()  # 阻塞当前同步代码，直到新线程执行完毕

        if thread_exception:
            raise thread_exception

        return thread_result

    # ------------------------
    # Utilities
    # ------------------------
    def _chunk(self, text: str) -> List[str]:
        if len(text) <= self.max_chunk_size:
            return [text]

        chunks, current = [], ""
        sentences = re.split(r"(?<=[.!?]) +", text)

        for s in sentences:
            if len(current) + len(s) > self.max_chunk_size:
                chunks.append(current.strip())
                current = s
            else:
                current += " " + s

        if current:
            chunks.append(current.strip())

        return chunks

    def _valid(self, text: Optional[str]) -> bool:
        if not text or not text.strip():
            return False

        english_ratio = len(re.findall(r"[a-zA-Z]", text)) / max(1, len(text))
        return english_ratio < 0.6


# ------------------------
# Singleton helper
# ------------------------
_pipeline = None


def get_pipeline() -> TranslationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = TranslationPipeline()
    return _pipeline


def translate_to_chinese(text: str) -> str:
    return get_pipeline().translate(text)
