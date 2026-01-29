
import os
import asyncio
from utils.redis_utils import close_loop_redis
import dramatiq
from pwright.async_pf import new_stealth_page
from pwright.async_cm import PlaywrightContext
from pwright.async_pm import AsyncPlaywrightManager
from scraper.agent_scraper import display_agent
from utils.logging_config import get_agent_task_logger

logger = get_agent_task_logger()

#===============================Task 4=========================================
@dramatiq.actor(queue_name="agents", max_retries=0)
def display_agent_task_main(
    *,
    user_id: str,
    prompt: list[str],
    all_cookies: dict,
    s_locators_map: dict,
    conversation_id: str,
    dia_count: int
):
    """
    One dramatiq task = one prompt, sources in asyncio.gather().
    """
    dir_path = os.path.join("snapshoots", user_id)
    os.makedirs(dir_path, exist_ok=True)

    # Run the actual async logic with asyncio.run to combine sources
    asyncio.run(_gather_display_agent_sources(
        user_id=user_id,
        prompt=prompt,
        all_cookies=all_cookies,
        s_locators_map=s_locators_map,
        conversation_id=conversation_id,
        dia_count=dia_count,
    ))


async def _process_one_source(
    *,
    source: str,
    storage_state: dict,
    site_locators: dict,
    user_id: str,
    prompt: list[str],
    conversation_id: str,
    dia_count: int,
):
    logger.info(f"Processing website={source} for displaying in iframe, user={user_id}")

    manager = AsyncPlaywrightManager()
    await manager.start()
    try:
        async with PlaywrightContext(
            manager,
            storage_state=storage_state,
            accept_downloads=True,
            ignore_https_errors=True,
        ) as context:
            page = await new_stealth_page(context)
            await display_agent(
                page=page,
                user_id=user_id,
                prompt=prompt,
                locators=site_locators,
                source=source,
                conversation_id=conversation_id,
                dia_count=dia_count
            )
    finally:
        await manager.shutdown()


async def _gather_display_agent_sources(
    *,
    user_id: str,
    prompt: list[str],
    all_cookies: dict,
    s_locators_map: dict,
    conversation_id: str,
    dia_count: int,
):
    """
    Run display_agent for all sources concurrently.
    """
    tasks = []
    for source, storage_state in all_cookies.items():
        site_locators = s_locators_map.get(source)
        if not site_locators:
            logger.warning(f"No site locators for source={source}")
            continue
        tasks.append(
            _process_one_source(
                source=source,
                storage_state=storage_state,
                site_locators=site_locators,
                user_id=user_id,
                prompt=prompt,
                conversation_id=conversation_id,
                dia_count=dia_count,
            )
        )

    if not tasks:
        logger.warning("No tasks created for display_agent_task_main")
        return

    logger.info(f"[display_agent_task_main] user={user_id}, tasks={len(tasks)} dispatched")
    await asyncio.gather(*tasks)
    await close_loop_redis()
