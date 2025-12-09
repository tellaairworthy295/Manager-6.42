import re
import time
import random
import json
import os
from typing import List, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import setup_logging

# Load translation config from config.json
def _load_translation_config():
    with open("json/config.json", 'r', encoding='utf-8') as f:
        config = json.load(f)
    tcfg = config.get("TranslationConfig", {})
    return {
        "ENABLE_TRANSLATION": tcfg.get("ENABLE_TRANSLATION", True),
        "USE_WEB_SCRAPING": tcfg.get("USE_WEB_SCRAPING", True),
        "MAX_CHUNK_SIZE": tcfg.get("MAX_CHUNK_SIZE", 4000),
        "REQUEST_DELAY": tcfg.get("REQUEST_DELAY", 1.0),
        "DISABLE_PROXY": tcfg.get("DISABLE_PROXY", False),
        "TIMEOUT_SECONDS": tcfg.get("TIMEOUT_SECONDS", 15),
    }

_translation_config = _load_translation_config()

# Optional: try deep-translator, fallback gracefully
try:
    from deep_translator import LingueeTranslator
    HAS_DEEP_TRANSLATOR = True
except ImportError:
    HAS_DEEP_TRANSLATOR = False

# Fallback: googletrans
try:
    from googletrans import Translator
    HAS_GOOGLETRANS = True
except ImportError:
    HAS_GOOGLETRANS = False

logger = setup_logging("logs/translation", "translation")

# Simple in-memory cache
_translation_cache = {}


class TranslationService:
    """
    Translation service with chunking, retries, and multiple fallback translators.
    Priority:
        1️⃣ Deep Translator
        2️⃣ Google Web API (scraping)
        3️⃣ googletrans
        4️⃣ Original text (last resort)
    """

    def __init__(
        self,
        use_web_scraping=_translation_config["USE_WEB_SCRAPING"],
        max_chunk_size=_translation_config["MAX_CHUNK_SIZE"],
        enable_translation=_translation_config["ENABLE_TRANSLATION"],
        request_delay=_translation_config["REQUEST_DELAY"],
        disable_proxy=_translation_config["DISABLE_PROXY"],
        timeout_seconds=_translation_config["TIMEOUT_SECONDS"]
    ):
        self.use_web_scraping = use_web_scraping
        self.max_chunk_size = max_chunk_size
        self.enable_translation = enable_translation
        self.request_delay = request_delay
        self.disable_proxy = disable_proxy
        self.timeout_seconds = timeout_seconds

        # Initialize translators
        self.translator = Translator() if HAS_GOOGLETRANS else None

        # Session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=2,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.7339.128 Safari/537.36'
        })
        if self.disable_proxy:
            self.session.proxies = {}

    # ------------------------
    # Chunking logic
    # ------------------------
    def _chunk_text(self, text: str) -> List[str]:
        """Split long text into manageable chunks with sentence awareness."""
        if len(text) <= self.max_chunk_size:
            return [text]

        chunks, current_chunk = [], ""
        paragraphs = text.split('\n\n')

        for paragraph in paragraphs:
            if len(current_chunk + paragraph) > self.max_chunk_size:
                sentences = re.split(r'(?<=[.!?]) +', paragraph)
                for s in sentences:
                    if len(current_chunk + s) > self.max_chunk_size:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = s
                    else:
                        current_chunk += " " + s
            else:
                current_chunk += ("\n\n" + paragraph) if current_chunk else paragraph

        if current_chunk:
            chunks.append(current_chunk.strip())
        return chunks

    # ------------------------
    # Translation methods
    # ------------------------
    def _translate_with_deep_translator(self, text: str) -> Optional[str]:
        if not HAS_DEEP_TRANSLATOR:
            return None
        try:
            return LingueeTranslator(source='auto', target='chinese').translate(text)
        except Exception as e:
            logger.warning(f"deep-translator error: {e}")
            return None

    def _translate_with_googletrans(self, text: str) -> Optional[str]:
        if not HAS_GOOGLETRANS or not self.translator:
            return None
        try:
            result = self.translator.translate(text, dest='zh-CN')
            return result.text
        except Exception as e:
            logger.warning(f"googletrans error: {e}")
            return None

    def _translate_with_web_scraping(self, text: str) -> Optional[str]:
        """Translate using unofficial Google Translate web endpoint."""
        endpoints = [
            "https://translate.googleapis.com/translate_a/single",
            "https://translate.google.com/translate_a/single",
            "https://translate.google.com.hk/translate_a/single",
        ]
        random.shuffle(endpoints)
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'zh-CN', 'dt': 't', 'q': text}

        for endpoint in endpoints:
            try:
                resp = self.session.get(endpoint, params=params, timeout=(5, self.timeout_seconds))
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError:
                    logger.warning("JSON parse failed, skipping endpoint")
                    continue
                if not data or not data[0]:
                    continue
                translated = ''.join(item[0] for item in data[0] if item and item[0])
                if translated.strip():
                    return translated
            except Exception as e:
                logger.warning(f"Endpoint {endpoint} failed: {e}")
                continue
        return None

    # ------------------------
    # High-level translation with retries
    # ------------------------
    def translate_text(self, text: str) -> str:
        if not self.enable_translation:
            return text
        if not text or not text.strip():
            return text

        text = text.strip()

        # Use cache
        if text in _translation_cache:
            return _translation_cache[text]

        if len(text) <= self.max_chunk_size:
            translated = self._translate_with_fallbacks(text)
            _translation_cache[text] = translated
            return translated

        chunks = self._chunk_text(text)
        translated_chunks = []

        logger.info(f"Translating {len(chunks)} chunks ({len(text)} chars total)")
        for i, chunk in enumerate(chunks):
            translated_chunk = self._translate_with_fallbacks(chunk, chunk_index=i + 1)
            translated_chunks.append(translated_chunk)
            time.sleep(self.request_delay)  # Configurable delay

        result = '\n\n'.join(translated_chunks)
        _translation_cache[text] = result
        return result

    def _translate_with_fallbacks(self, text: str, chunk_index=None) -> str:
        """Try translators in priority order with retries."""
        for attempt in range(3):
            try:
                if HAS_DEEP_TRANSLATOR:
                    result = self._translate_with_deep_translator(text)
                    if result and self._is_valid_translation(text, result):
                        return result

                if self.use_web_scraping:
                    result = self._translate_with_web_scraping(text)
                    if result and self._is_valid_translation(text, result):
                        return result

                if HAS_GOOGLETRANS:
                    result = self._translate_with_googletrans(text)
                    if result and self._is_valid_translation(text, result):
                        return result

            except Exception as e:
                logger.warning(f"Attempt {attempt+1} failed for chunk {chunk_index}: {e}")

            delay = 2 ** attempt
            logger.info(f"Retrying in {delay}s (attempt {attempt+2}/3)...")
            time.sleep(delay)

        logger.error(f"Translation failed after 3 attempts for chunk {chunk_index}. Returning original text.")
        return text

    def _is_valid_translation(self, original: str, translated: str) -> bool:
        """Basic heuristic to ensure output isn't untranslated or empty."""
        if not translated.strip():
            return False
        # Detect if too much English remains
        english_ratio = len(re.findall(r'[a-zA-Z]', translated)) / max(1, len(translated))
        return english_ratio < 0.5  # more than 50% Chinese content

    # ------------------------
    # Article translation
    # ------------------------
    def translate_article(self, title: str, content: str) -> dict:
        result = {'title_zh': '', 'content_zh': ''}
        try:
            if title:
                logger.info("Translating article title...")
                result['title_zh'] = self.translate_text(title)
            if content:
                logger.info("Translating article content...")
                result['content_zh'] = self.translate_text(content)
        except Exception as e:
            logger.error(f"Error translating article: {e}")
        return result


# ------------------------
# Global instance and helpers
# ------------------------
_translation_service = None


def get_translation_service() -> TranslationService:
    global _translation_service
    if _translation_service is None:
        try:
            _translation_service = TranslationService(
                use_web_scraping=_translation_config["USE_WEB_SCRAPING"],
                max_chunk_size=_translation_config["MAX_CHUNK_SIZE"],
                enable_translation=_translation_config["ENABLE_TRANSLATION"],
                request_delay=_translation_config["REQUEST_DELAY"],
                disable_proxy=_translation_config["DISABLE_PROXY"],
                timeout_seconds=_translation_config["TIMEOUT_SECONDS"],
            )
        except ImportError:
            _translation_service = TranslationService()
    return _translation_service


def translate_to_chinese(text: str) -> str:
    return get_translation_service().translate_text(text)


def translate_article_to_chinese(title: str, content: str) -> dict:
    return get_translation_service().translate_article(title, content)
