import asyncio
import html
import json
from pathlib import Path
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from utils.redis_utils import get_aioredis_client
from utils.logging_config import get_agent_task_logger

logger = get_agent_task_logger()

UNIFIED_STREAM_OBSERVER_JS = r"""
(() => {
  if (window.__DOM_STREAM__) return;

  const STREAM = window.__DOM_STREAM__ = {
    buffer: "",
    dirty: false,
    active: true,
    observer: null,
  };

  const SELECTORS = [
    ".cp-document-block",
    ".entity-section",
    ".mind-map-container",
  ];

  let targets = [];
  let timer = null;
  let lastLength = 0;

  const refreshTargets = () => {
    targets = Array.from(document.querySelectorAll(SELECTORS.join(",")));
  };

  const rebuildBuffer = () => {
    if (!STREAM.active || !STREAM.dirty) return;
    STREAM.dirty = false;

    let text = "";
    for (const el of targets) {
      if (el.isConnected && el.textContent) {
        text += el.textContent + "\\n\\n";
      }
    }

    if (text.length <= lastLength) return;
    lastLength = text.length;
    STREAM.buffer = text.trim();
  };

  const scheduleRebuild = () => {
    STREAM.dirty = true;
    if (timer) return;

    timer = setTimeout(() => {
      rebuildBuffer();
      timer = null;
    }, 500);
  };

  refreshTargets();
  setInterval(refreshTargets, 3000);

  const observer = STREAM.observer = new MutationObserver(scheduleRebuild);
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
})();
"""

async def wait_for_finish_selector(page: Page, finish_locator: str, max_wait_seconds: int, finished_event: asyncio.Event):
    try:
        await page.wait_for_selector(
            finish_locator,
            state="visible",
            timeout=max_wait_seconds * 1000,
        )
        logger.info("Finish locator appeared — answer finished.")
    except PlaywrightTimeoutError:
        logger.error("Timed out waiting for finish locator.")
    finally:
        finished_event.set()

async def stream_dom_text(
    page: Page,
    redis,
    user_id: str,
    conversation_id: str,
    source: str,
    dia_count: int,
    finished_flag: asyncio.Event,
):
    last_text = ""
    stable_rounds = 0
    sleep = 0.5

    while not finished_flag.is_set():
        text = await page.evaluate(
            "() => window.__DOM_STREAM__?.buffer || ''"
        )

        if text and len(text) - len(last_text) > 20:
            last_text = text
            stable_rounds = 0
            sleep = 0.5

            await redis.xadd(
                f"sse:{user_id}_{conversation_id}_{source}",
                {
                    "data": json.dumps({
                        "content": text,
                        "dia_count": dia_count,
                    }),
                },
                maxlen=1000,
            )
            logger.info("PUBLISH %s chars (%s)", len(text), source)
        elif text:
            stable_rounds += 1
            if stable_rounds > 4:
                sleep = min(sleep * 1.25, 0.8)

        await asyncio.sleep(sleep)


async def stream_via_mutation_observer(
    page: Page,
    source: str,
    user_id: str,
    conversation_id: str,
    finish_locator: str,
    dia_count: int,
    prompt: str,
):
    MAX_WAIT_SECONDS = 15 * 60
    redis = get_aioredis_client()
    finished_event = asyncio.Event()

    # 1️⃣ Inject unified observer
    await page.evaluate(UNIFIED_STREAM_OBSERVER_JS)

    # 2️⃣ Start finish watcher
    asyncio.create_task(wait_for_finish_selector(page, finish_locator, MAX_WAIT_SECONDS, finished_event))

    # 3️⃣ Stream until finished
    try:
        await stream_dom_text(
            page,
            redis,
            user_id,
            conversation_id,
            source,
            dia_count,
            finished_event,
        )
    except Exception:
        logger.exception("DOM streaming crashed")

    # 4️⃣ Stop observer cleanly
    await page.evaluate("""
      () => {
        if (window.__DOM_STREAM__) {
          window.__DOM_STREAM__.active = false;
        }
      }
    """)

    # 5️⃣ Final snapshot (your existing logic)
    await snapshot_dom_state(
        page,
        user_id,
        source,
        conversation_id,
        dia_count,
        prompt,
        redis,
    )

async def stream_via_selector_polling(
    page: Page,
    source: str,
    user_id: str,
    conversation_id: str,
    finish_locator: str,
    dia_count: int,
    prompt: str,
):
    last_text = ""
    redis = get_aioredis_client()
    finished_event = asyncio.Event()
    # Start watcher for finish_locator
    MAX_WAIT_SECONDS = 15 * 60
    asyncio.create_task(wait_for_finish_selector(page, finish_locator, MAX_WAIT_SECONDS, finished_event))
    stable_rounds = 0
    sleep = 0.5
    while not finished_event.is_set():
        text = await page.eval_on_selector_all( 
            "div.flex.flex-col.agent-share_wrapper_inner", 
            "els => els.map(el => el.textContent).join('\\n\\n')" )

        if text and len(text) - len(last_text) > 20:
            last_text = text
            stable_rounds = 0
            await redis.xadd(
                f"sse:{user_id}_{conversation_id}_{source}",
                {
                    "data": json.dumps({
                        "content": text,
                        "dia_count": dia_count,
                    }),
                },
                maxlen=1000,
            )
            logger.info("PUBLISH %s chars (%s)", len(text), source)
        elif text:
            stable_rounds += 1

        if stable_rounds > 4:
            sleep = min(0.8, sleep * 1.25)
        else:
            sleep = 0.5

        await asyncio.sleep(sleep)

    await snapshot_dom_state(
        page,
        user_id,
        source,
        conversation_id,
        dia_count,
        prompt,
        redis,
    )

async def snapshot_dom_state(
    page: Page,
    user_id: str,
    source: str,
    conversation_id: str,
    dia_count: int,
    prompt: str,
    redis,
):
    """
    Extracts answer elements depending on source and saves snapshot HTML.
    """
    snapshot_dir = Path("snapshoots") / user_id / conversation_id / source
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{prompt[:15]}_{dia_count}.html"

    if source == "alphapai":
        selector = ".cp-document-block.entity-section"
    elif source == "gangtise":
        selector = ".answer-agent"
    else:
        selector = None

    answers = []
    if selector:
        answers = await page.eval_on_selector_all(
            selector,
            """els => els.map(el => ({
                tag: el.tagName,
                html: el.outerHTML,
                text: el.innerText
            }))"""
        )

    parts = []
    if answers:
        for a in answers:
            parts.append(f"<div class='answer'>{a['html']}</div>")
    else:
        parts.append(f"""
        <div class="stage">
            <strong>Status:</strong> {html.escape("No content scraped")}<br/>
        </div>
        """)

    html_doc = f"""<!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8"/>
        <style>
            body {{
                font-family: system-ui, -apple-system, BlinkMacSystemFont;
                padding: 16px;
                line-height: 1.6;
            }}
            .stage {{
                color: #555;
                font-style: italic;
            }}
        </style>
    </head>
    <body>
        {''.join(parts)}
    </body>
    </html>
    """

    snapshot_path.write_text(html_doc, encoding="utf-8")

    await redis.xadd(
        f"sse:{user_id}_{conversation_id}_{source}",
        {
            "data": json.dumps({
                "file": f"{source}_{dia_count}.html",
                "dia_count": dia_count,
            }),
        },
        maxlen=1000,
    )
    logger.info("html saved")