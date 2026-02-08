
import json
import os
import asyncio
import re
from datetime import datetime
import aiohttp
from urllib.parse import urljoin
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pwright.async_pm import AsyncPlaywrightManager
from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page
from utils.database import get_db_manager, StockRepository, SectionReasonRepository
from utils.interactive import async_safe_click
from utils.logging_config import get_stock_logger
from pathlib import Path

logger = get_stock_logger()


async def pw_text(el):
    if not el:
        return None
    return (await el.text_content()).strip()


async def run_named_extractors(*, scope, extractors, site):
    result = {}

    for name, extractor in extractors.items():
        fields = extractor["fields"]
        record = {}

        for field, cfg in fields.items():
            selector = cfg["selector"]
            # Handle multiple selectors as a comma-separated list
            selectors = (
                selector if isinstance(selector, list)
                else [s.strip() for s in selector.split(",")]
            )

            el = None
            raw = None
            for sel in selectors:
                el = await scope.query_selector(sel)
                if el:
                    raw = await el.text_content()
                    raw = raw.strip() if raw else None
                    if raw:  # Prefer the first one with content
                        break

            if cfg.get("required", False) and raw is None:
                return None

            record[field] = postprocess_value(
                raw, cfg.get("postprocess"), site
            )

        result[name] = record

    return result


def postprocess_value(value: str | None, rules: list[str] | None, site: str | None = None):
    if value is None or not rules:
        if rules and len(rules) == 1 and rules[0] == "prefix_by_site":
            value = apply_prefix("", site) if site else value
        return value

    for rule in rules:
        if rule == "strip_spaces":
            value = re.sub(r"\s+", "", value)

        elif rule == "extract_6_digits":
            m = re.search(r"\d{6}", value)
            value = m.group(0) if m else value

        elif rule == "extract_int":
            digits = re.sub(r"\D", "", value)
            value = int(digits) if digits.isdigit() else None

        elif rule == "keep_text":
            value = value.strip()

        elif rule == "prefix_by_site":
            value = apply_prefix(value, site) if site else value

        elif rule == "extract_float":
            digits = re.sub(r'[^\d\.]', '', value)
            try:
                value = float(digits)
            except ValueError:
                value = None

        elif rule == "time_format":
            # extract time in HH:mm:ss, fallback to HH:mm if no seconds, else None
            m = re.search(r"(\d{2}):(\d{2})(?::(\d{2}))?", value)
            if m:
                if m.group(3) is not None:
                    value = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
                else:
                    value = f"{m.group(1)}:{m.group(2)}:00"
            else:
                value = None

        elif rule == "text_formatter":
            value = re.sub(r"[\s\n\t]+", "", value)
        
        elif rule == "extract_percent_str":
            # Extract percent-format string, may start with + or -, e.g., "+8.00%", "-6.42%", "5.5%"
            m = re.search(r'[+-]?\d+\.?\d*%', value)
            value = m.group(0) if m else None
            
        else:
            raise ValueError(f"Unknown postprocess rule: {rule}")

    return value


async def extract_page_data(
        page,
        extractors: dict,
        site: str
) -> dict:
    result = {}
    
    for block_name, fields in extractors.items():
        block_data = {}

        for field, cfg in fields.items():
            try:
                await page.wait_for_selector(
                    cfg["selector"],
                    state="visible",
                    timeout=15_000
                )
                el = await page.query_selector(cfg["selector"])
                raw = await pw_text(el)
                value = postprocess_value(
                    raw,
                    cfg.get("postprocess"),
                    site
                )
            except Exception:
                value = None

            block_data[field] = value

        result[block_name] = block_data

    return result


def route_scraped_data(
        *,
        selectors: dict,
        data: dict,
        action_records: list,
        limit_records: list,
        market_number_ref: dict,
        section_reason_ref: list[dict],
):
    category = selectors["category"]

    if data.get("records"):
        if category == "action":
            action_records.extend(data["records"])
        elif category == "limit":
            limit_records.extend(data["records"])

    if data.get("market_number"):
        market_number_ref.update(data["market_number"])

    if data["sections"].get("section_reason"):
        section_reason_ref.extend(data["sections"].get("section_reason"))


def apply_prefix(value: str, site: str) -> str:
    if site == "jiuyan":
        return f"韭研:\n{value}\n"
    return f"选股通:\n{value}\n"


async def main_scraper(date: str):
    PROJECT_ROOT = Path(__file__).parent.parent

    with open(PROJECT_ROOT / "json/selectors.json", "r", encoding="utf-8") as f:
        selectors_map = json.load(f)["stocks"]

    with open(PROJECT_ROOT / "json/common/cookies.json", "r", encoding="utf-8") as f:
        cookies_map = json.load(f)

    all_action_records: list[dict] = []
    all_limit_records: list[dict] = []
    market_number: dict = {}
    section_reason: list[dict] = []

    manager = AsyncPlaywrightManager()
    await manager.start()

    try:
        for url, selectors in selectors_map.items():
            storage_state = (
                cookies_map.get(selectors["name"])
                if selectors.get("cookies")
                else None
            )

            logger.info(f"Scraping stocks from {url}")

            try:
                async with PlaywrightContext(
                        manager,
                        storage_state=storage_state,
                        ignore_https_errors=True,
                ) as context:
                    page = await new_stealth_page(context)
                    await page.goto(url, wait_until="domcontentloaded", timeout=50_000)
                    data = await scrape_page(
                        page=page,
                        date=date,
                        selectors=selectors
                    )
                    if not data or not data.get("records"):
                        logger.warning(f"No records scraped from {url}")
                        return False, {}, []

                    route_scraped_data(
                        selectors=selectors,
                        data=data,
                        action_records=all_action_records,
                        limit_records=all_limit_records,
                        market_number_ref=market_number,
                        section_reason_ref=section_reason,
                    )

                    logger.info(
                        f"Scraped {len(data['records'])} records from {url}"
                    )

                    if selectors.get("image"):
                        await _prepare_image_dir("images")
                        await _scrape_images_async(page, url)
            except Exception as e:
                logger.exception(f"Error scraping {url}: {e}")

    finally:
        await manager.shutdown()

    # -------- persistence / downstream --------
    if all_action_records:
        db_manager = get_db_manager()
        StockRepository(db_manager).insert_or_update_stocks(datetime.strptime(date, "%Y-%m-%d"), all_action_records)
    if section_reason:
        db_manager = get_db_manager()
        repo = SectionReasonRepository(db_manager)
        repo.delete_by_date(datetime.strptime(date, "%Y-%m-%d"))
        repo.upsert_section_reason(
            datetime.strptime(date, "%Y-%m-%d"),
            section_reason
        )

    return True, market_number, all_limit_records


async def scrape_page(
        *,
        page,
        date: str,
        selectors: dict
) -> dict:
    site = selectors["name"]
    section_cfg = selectors["section"]
    columns = selectors["columns"]

    records = []
    section_datasets = {}
    page_data = {}

    if selectors.get("click_selector"): 
        await async_safe_click(
            page, 
            f"xpath={selectors['click_selector']}", 
            timeout=5_000, max_attempts=3
        )

    # ---- page-level extractors ----
    if "page_extractors" in selectors:
        page_data = await extract_page_data(
            page,
            selectors["page_extractors"],
            site
        )
    # ---- sections ----
    if "selector" in section_cfg:
        try:
            # Wait for selector and get the first element as confirmation
            await page.wait_for_selector(
                section_cfg["selector"],
                timeout=30_000,
                state="attached"  # 只需要元素存在于DOM中
            )

            if section_cfg.get("click"):
                await async_safe_click(
                    page=page, 
                    locator=f"xpath={section_cfg['click']}", 
                    multiple=True
                )
                try:
                    await page.wait_for_selector(
                    section_cfg["wait_for"],
                    timeout=3000,
                    state="attached"
                )
                except:
                    pass
            # Now get all matching elements
            sections = await page.query_selector_all(section_cfg["selector"])

        except Exception as e:
            raise RuntimeError(f"sections not loaded in time: {str(e)}")
    else:
        try:
            await page.wait_for_selector(
                section_cfg["rows"]["selector"],
                timeout=30_000,
                state="attached"
            )
        except Exception as e:
            raise RuntimeError(f"Page not loaded in time: {str(e)}")

        sections = [page]  # implicit single section

    for section in sections:
        extracted_blocks = {}

        # ---- section extractors ----
        if "extractors" in section_cfg:
            extracted_blocks = await run_named_extractors(
                scope=section,
                extractors=section_cfg["extractors"],
                site=site
            )
            if extracted_blocks is None:
                # If any required value is None (run_named_extractors returns None), skip this section.
                continue
            for name, data in extracted_blocks.items():
                section_datasets.setdefault(name, []).append(data)

        # ---- rows ----
        rows = await section.query_selector_all(
            section_cfg["rows"]["selector"]
        )

        for row in rows:
            record = {"date": date}
            # propagate section metadata
            for _, data in extracted_blocks.items():
                record.update(data)

            for field, cfg in columns.items():
                el = await row.query_selector(cfg["selector"])
                raw = await pw_text(el)

                record[field] = postprocess_value(
                    raw, cfg.get("postprocess"), site
                )
            
            if record.get("stock") and record.get("code"):
                records.append(record)

    return {
        "records": records,
        "sections": section_datasets,
        **page_data
    }


async def _scrape_images_async(page, url: str) -> str | None:
    logger.info(f"Scraping 涨停简图 from: {url}")

    button_locator = (
        "xpath=//div[contains(@class,'yd-tabs_item')]/div[text()='涨停简图']"
    )

    await async_safe_click(
        page,
        button_locator,
        timeout=5_000,
        max_attempts=3,
    )

    img_selector = "div#QR-code img"

    try:
        img_el = await page.wait_for_selector(
            img_selector,
            state="visible",
            timeout=30_000,
        )
        logger.info("Image area loaded.")
    except PlaywrightTimeoutError:
        return None

    # ---- extract src directly from DOM ----
    img_url = await img_el.get_attribute("src")
    if not img_url:
        logger.info("Image src attribute missing.")
        return None

    # resolve relative URL if needed
    img_url = urljoin(url, img_url)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(img_url) as resp:
                resp.raise_for_status()
                data = await resp.read()

        img_path = os.path.join("images", "Image.png")
        await asyncio.to_thread(
            lambda: open(img_path, "wb").write(data)
        )

        logger.info(f"Downloaded image: {img_url}")
        return img_url

    except Exception as e:
        logger.warning(f"Failed to download image: {e}")
        return None


async def _prepare_image_dir(path: str):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        return

    for fname in os.listdir(path):
        fp = os.path.join(path, fname)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
            except Exception as e:
                logger.warning(f"Could not delete {fp}: {e}")
