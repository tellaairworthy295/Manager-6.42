# translation_service_hybrid.py
import re
import time
import random
import json
import threading
from typing import List, Optional
import requests
from openai import OpenAI, APIError, RateLimitError, APITimeoutError
from utils.logging_config import get_news_task_logger
from deep_translator import GoogleTranslator
logger = get_news_task_logger()


# ── Config Loading ─────────────────────────────────────────────────────────────
def _load_translation_config():
    with open("json/config.json", 'r', encoding='utf-8') as f:
        config = json.load(f)
    tcfg = config.get("TranslationConfig", {})
    llm_cfg = config.get("LLMTranslationConfig", {})  # New section

    return {
        # Core translation settings
        "ENABLE_TRANSLATION": tcfg.get("ENABLE_TRANSLATION", True),
        "USE_WEB_SCRAPING": tcfg.get("USE_WEB_SCRAPING", True),
        "NMT_ENABLED": tcfg.get("NMT_ENABLED", True),
        "MAX_CHUNK_SIZE": tcfg.get("MAX_CHUNK_SIZE", 2000),  # Reduced for LLM efficiency
        "REQUEST_DELAY": tcfg.get("REQUEST_DELAY", 1.0),  # Lower delay for API calls
        "TIMEOUT_SECONDS": tcfg.get("TIMEOUT_SECONDS", 20),

        # LLM-specific settings (no retries - single attempt only)
        "LLM_ENABLED": llm_cfg.get("ENABLED", True),
        "LLM_API_BASE_URL": llm_cfg.get("API_BASE_URL", "https://openrouter.ai/api/v1"),
        "LLM_API_KEY_ENV": llm_cfg.get("API_KEY_ENV"),
        "LLM_MODEL_NAME": llm_cfg.get("MODEL_NAME", "openai/gpt-oss-120b:free"),
        "LLM_TEMPERATURE": llm_cfg.get("TEMPERATURE", 0.1),
        "LLM_MAX_TOKENS": llm_cfg.get("MAX_TOKENS", 20480),
        "LLM_TIMEOUT": llm_cfg.get("TIMEOUT", 40.0),
    }


_translation_config = _load_translation_config()

# ── Global State & Lazy Initialization ─────────────────────────────────────────
_llm_client: Optional[OpenAI] = None
_llm_init_lock = threading.Lock()


def _get_llm_client() -> Optional[OpenAI]:
    """Lazy-initialize OpenRouter client (thread-safe)."""
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    with _llm_init_lock:
        if _llm_client is not None:
            return _llm_client

        if not _translation_config["LLM_ENABLED"]:
            return None

        api_key = _translation_config["LLM_API_KEY_ENV"]
        if not api_key:
            logger.warning(f"LLM API key not found: {_translation_config['LLM_API_KEY_ENV']}")
            return None

        try:
            _llm_client = OpenAI(
                base_url=_translation_config["LLM_API_BASE_URL"],
                api_key=api_key,
                timeout=_translation_config["LLM_TIMEOUT"]
            )
            logger.info(f"OpenRouter LLM client initialized: {_translation_config['LLM_MODEL_NAME']}")
            return _llm_client
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
            return None


# ── Translation Service Class ──────────────────────────────────────────────────
class TranslationService:
    """
    Hybrid translation service with priority fallback:
        1️⃣ Google Web API (scraping) - FAST, free, no auth
        2️⃣ deeptrans
        3️⃣ OpenRouter LLM API - HIGH QUALITY, requires API key
        4️⃣ Local NMT

    ⚠️ LLM and NMT methods make SINGLE ATTEMPTS only (no internal retries).
       Retry logic should be handled at the service/caller level if needed.
    """

    def __init__(
            self,
            use_web_scraping=_translation_config["USE_WEB_SCRAPING"],
            max_chunk_size=_translation_config["MAX_CHUNK_SIZE"],
            enable_translation=_translation_config["ENABLE_TRANSLATION"],
            request_delay=_translation_config["REQUEST_DELAY"],
            timeout_seconds=_translation_config["TIMEOUT_SECONDS"],
            llm_enabled=_translation_config["LLM_ENABLED"],
            nmt_enabled=_translation_config["NMT_ENABLED"]
    ):
        self.use_web_scraping = use_web_scraping
        self.max_chunk_size = max_chunk_size
        self.enable_translation = enable_translation
        self.request_delay = request_delay
        self.timeout_seconds = timeout_seconds
        self.llm_enabled = llm_enabled
        self.nmt_enabled = nmt_enabled

        # HTTP Session for web scraping
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.7339.128 Safari/537.36'
        })

    # ── Chunking (optimized for mixed translation methods) ─────────────────────
    def _chunk_text(self, text: str) -> List[str]:
        """Split text into chunks respecting semantic boundaries."""
        if len(text) <= self.max_chunk_size:
            return [text]

        chunks, current_chunk = [], ""
        paragraphs = [p for p in text.split('\n\n') if p.strip()]

        for para in paragraphs:
            if len(para) > self.max_chunk_size:
                # Split long paragraph on sentences
                sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para)
                for sent in sentences:
                    if len(current_chunk) + len(sent) > self.max_chunk_size:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = sent
                    else:
                        current_chunk += (" " + sent) if current_chunk else sent
            else:
                separator = "\n\n" if current_chunk else ""
                if len(current_chunk) + len(separator) + len(para) > self.max_chunk_size:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = para
                else:
                    current_chunk += separator + para

        if current_chunk:
            chunks.append(current_chunk.strip())

        logger.debug(f"Chunked {len(text)} chars → {len(chunks)} chunks")
        return chunks

    # ── Translation Methods (SINGLE ATTEMPT ONLY - No Internal Retries) ────────

    def _translate_with_web_scraping(self, text: str) -> Optional[str]:
        """Google Translate web endpoint - FAST, no auth, priority #1."""
        if not self.use_web_scraping:
            return None

        endpoints = [
            "https://translate.googleapis.com/translate_a/single",
            "https://translate.google.com/translate_a/single",
            "https://translate.google.com.hk/translate_a/single",
        ]
        random.shuffle(endpoints)
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'zh-CN', 'dt': 't', 'q': text}

        for endpoint in endpoints:
            try:
                # SINGLE ATTEMPT per endpoint - fail fast on 429
                resp = self.session.get(
                    endpoint,
                    params=params,
                    timeout=(3, self.timeout_seconds)
                )
                resp.raise_for_status()
                data = resp.json()

                if data and data[0]:
                    translated = ''.join(item[0] for item in data[0] if item and item[0])
                    if translated.strip():
                        return translated

            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, 'status_code', None)
                if status == 429:
                    logger.debug("Google scraping: 429 rate limit - skipping to next method")
                    return None  # Fail fast, don't try other Google endpoints
                logger.debug(f"Scraping endpoint failed: {status}")
                continue
            except Exception as e:
                logger.debug(f"Scraping error: {e}")
                continue

        return None

    def _translate_with_llm(self, text: str) -> Optional[str]:
        """OpenRouter LLM translation - HIGH QUALITY, SINGLE ATTEMPT ONLY."""
        if not self.llm_enabled:
            return None

        client = _get_llm_client()
        if not client:
            return None

        system_prompt = (
            "You are a professional English-to-Chinese translator. "
            "Translate the user's text into Simplified Chinese (zh-CN). "
            "Return ONLY the translated text with no explanations, markdown, "
            "greetings, code blocks, or extra formatting. Preserve line breaks."
        )
        user_prompt = f"Translate to Simplified Chinese:\n\n{text}"

        try:
            response = client.chat.completions.create(
                model=_translation_config["LLM_MODEL_NAME"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=_translation_config["LLM_TEMPERATURE"],
                stream=False
            )

            result = response.choices[0].message.content
            if result and result.strip():
                return result.strip()
            logger.debug("LLM returned empty/no content")
            return None

        except (RateLimitError, APITimeoutError, APIError) as e:
            logger.debug(f"LLM API error (no retry): {type(e).__name__}: {e}")
            return None
        except Exception as e:
            logger.debug(f"LLM unexpected error: {type(e).__name__}: {e}")
            return None

    def _translate_with_deeptrans(self, text: str) -> Optional[str]:
        """
        Translate text to Simplified Chinese using deep-translator (sync, no dependency conflicts).
        """
        try:
            # deep-translator is fully sync - no asyncio needed
            return GoogleTranslator(source='auto', target='zh-CN').translate(text)

        except Exception as e:
            logger.warning(f"deep-translator error: {type(e).__name__}: {e!r}")
            return None

    def _translate_with_libretranslate(self, text: str) -> Optional[str]:
        """Translate via local LibreTranslate API - lightweight NMT fallback."""
        url = "http://127.0.0.1:5000/translate"
        payload = {
            "q": text,
            "source": "en",
            "target": "zh-Hans",
            "format": "text"
        }

        headers = {"Content-Type": "application/json"}

        try:
            resp = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=60
            )
            resp.raise_for_status()
            result = resp.json().get("translatedText")
            return result.strip() if result and result.strip() else None
        except requests.exceptions.RequestException as e:
            logger.debug(f"LibreTranslate API error: {type(e).__name__}: {e}")
            return None
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"LibreTranslate response parse error: {e}")
            return None

    # ── Validation ─────────────────────────────────────────────────────────────
    def _is_valid_translation(self, original: str, translated: str) -> bool:
        """Reject empty, unchanged outputs."""
        if not translated or not translated.strip():
            return False
        if translated.strip() == original.strip():
            return False
        return True

    # ── Fallback Chain (Priority Order) ─────────────────────────────────────────
    def _translate_with_fallbacks(self, text: str, chunk_index=None) -> str:
        """
        Try translation methods in priority order.
        Each method makes ONE attempt only - no internal retries.
        """
        label = f"chunk {chunk_index}" if chunk_index else "text"
        logger.debug(f"Starting fallback chain for {label} ({len(text)} chars)")
        strategies = [1, 2, 3]
        random.shuffle(strategies)
        for s in strategies:
            # 1️⃣ Google Web Scraping (FAST, free)
            if self.use_web_scraping and s == 1:
                result = self._translate_with_web_scraping(text)
                if result and self._is_valid_translation(text, result):
                    logger.debug(f"✓ Scraping succeeded for {label}")
                    return result

            #2️⃣ deeptrans
            if s == 2:
                result = self._translate_with_deeptrans(text)
                if result and self._is_valid_translation(text, result):
                    logger.debug(f"✓ NMT succeeded for {label}")
                    return result

            #3️⃣ OpenRouter LLM (HIGH QUALITY, requires API key)
            if self.llm_enabled and s == 3:
                result = self._translate_with_llm(text)
                if result and self._is_valid_translation(text, result):
                    logger.debug(f"✓ LLM succeeded for {label}")
                    return result

        # 4️⃣ Local nmt
        if self.nmt_enabled:
            result = self._translate_with_libretranslate(text)
            if result and self._is_valid_translation(text, result):
                return result

        logger.warning(f"All translation methods failed for {label}, returning original")
        return text

    # ── Public API ─────────────────────────────────────────────────────────────
    def translate_text(self, text: str) -> str:
        if not self.enable_translation:
            return text
        if not text or not text.strip():
            return text

        text = text.strip()

        # Short-circuit for very short texts
        if len(text) <= 150:
            result = self._translate_with_fallbacks(text)
            return result

        # Chunk long texts
        chunks = self._chunk_text(text)

        if len(chunks) == 1:
            result = self._translate_with_fallbacks(text)
            return result

        # Multi-chunk translation with rate limiting
        logger.info(f"Translating {len(chunks)} chunks ({len(text)} chars)")
        translated_chunks = []

        for i, chunk in enumerate(chunks, start=1):
            translated = self._translate_with_fallbacks(chunk, chunk_index=i)
            translated_chunks.append(translated)

            # Rate limiting between API calls
            if i < len(chunks) and self.request_delay > 0:
                time.sleep(self.request_delay)

        result = '\n\n'.join(translated_chunks)
        return result

    def translate_article(self, title: str, content: str) -> dict:
        result = {'title_zh': '', 'content_zh': ''}
        try:
            if title:
                logger.info("Translating title...")
                result['title_zh'] = self.translate_text(title)
            if content:
                logger.info("Translating content...")
                result['content_zh'] = self.translate_text(content)
        except Exception as e:
            logger.error(f"Article translation error: {e}")
        return result


# ── Singleton & Helpers (Unchanged API) ────────────────────────────────────────
_translation_service: Optional[TranslationService] = None


def get_translation_service() -> TranslationService:
    global _translation_service
    if _translation_service is None:
        _translation_service = TranslationService(
            use_web_scraping=_translation_config["USE_WEB_SCRAPING"],
            max_chunk_size=_translation_config["MAX_CHUNK_SIZE"],
            enable_translation=_translation_config["ENABLE_TRANSLATION"],
            request_delay=_translation_config["REQUEST_DELAY"],
            timeout_seconds=_translation_config["TIMEOUT_SECONDS"],
            llm_enabled=_translation_config["LLM_ENABLED"],
            nmt_enabled=_translation_config["NMT_ENABLED"]
        )
    return _translation_service


def translate_to_chinese(text: str) -> str:
    return get_translation_service().translate_text(text)


def translate_article_to_chinese(title: str, content: str) -> dict:
    return get_translation_service().translate_article(title, content)
