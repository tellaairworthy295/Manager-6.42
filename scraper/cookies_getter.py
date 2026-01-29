
import json
import os
import asyncio
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pwright.async_cm import PlaywrightContext
from pwright.async_pf import new_stealth_page           # async version
from pwright.async_pm import AsyncPlaywrightManager               # async version
from utils.logging_config import get_agent_task_logger
from exception.exception_handler import ValidationError
from utils.interactive import async_safe_click, async_safe_fill

class AsyncCookiesHandler:
    """
    Handles browser automation for login, cookie/localStorage extraction, and saving,
    using locator information from config.
    Uses async helper functions from interactive.py for robust UI actions.
    Now supports validation before update, if specified.
    """

    def __init__(self, config: dict, user_id: str | None, validate_before_update: bool = False):
        if not user_id:
            raise ValidationError("No user_id provided")
        # Extract core info
        self.phone = config.get("phone")
        self.passwd = config.get("passwd")
        self.url = config.get("url")
        self.section_name = config.get("name")
        self.user_id = user_id
        self.logger = get_agent_task_logger()
        self.page = None  # Will be set at runtime
        self.validate_before_update = validate_before_update

        # Extract and parse locators
        locs = config.get("locators", {})

        def parse(loc):
            if loc is None or not loc:
                return None
            by = loc["by"].lower()
            value = loc["value"]
            if by == "css":
                return value
            if by == "xpath":
                return f"xpath={value}"
            raise ValueError(f"Unknown locator type: {by}")

        self.login_dialog_locator = parse(locs.get("login_dialog"))
        self.tab_locator = parse(locs.get("tab_switch"))
        self.phone_locator = parse(locs.get("phone_input"))
        self.password_locator = parse(locs.get("pwd_input"))
        self.submit_locator = parse(locs.get("login_btn"))
        self.optional_open_login_locator = parse(locs.get("open_login_btn"))
        self.wrong_phone_passwd = parse(locs.get("wrong_phone_passed"))
        self.check_box_locators = []
        check_box = locs.get("check_box", [])
        if check_box:
            for check_box_locator in check_box:
                self.check_box_locators.append(parse(check_box_locator))

        # For validation, extract url for checking expired
        self.validation_url = config.get("url", self.url)

    @staticmethod
    def normalize_cookies(cookie_list):
        for c in cookie_list:
            domain = c.get("domain", "")
            if domain.startswith("www"):
                c["domain"] = domain[3:]
        return cookie_list

    @staticmethod
    def normalize_local_storage(origins):
        if not origins:
            return
        if origins[0].get("origin", "") == "https://alphapai-web.rabyte.cn":
            manual_pairs = [
                {"name": "search-to-paipai-guide", "value": "true"},
                {"name": "paipai-agent-fastsheet-us-guide", "value": "true"},
                {"name": "search-to-paipai-guide-date", "value": "1768962236148"},
                {"name": "version-market-tip", "value": "1"},
                {"name": "MODE", "value": "undefined"},
                {"name": "hasShowpaipaiAnswerRangeGuide", "value": "true"},
                {"name": "version-social-sub-tip", "value": "1"},
                {"name": "hasShowSocialMediaGuide", "value": "1"},
                {"name": "paipai-agent-fastsheet-us-guide", "value": "true"},
                {"name": "extension-download-guide", "value": "true"},
                {"name": "paipai_mode-select_task-guide", "value": "1"},
                {"name": "hasShowPaipaiRecoderGuide", "value": "1"}
            ]
            if "localStorage" not in origins[0] or not isinstance(origins[0]["localStorage"], list):
                origins[0]["localStorage"] = []
            # Only add if not already present
            existing_names = {entry['name'] for entry in origins[0]["localStorage"]}
            for pair in manual_pairs:
                if pair['name'] not in existing_names:
                    origins[0]["localStorage"].append(pair)
        elif origins[0].get("origin", "") == "https://www.jiuyangongshe.com":
            origins[0]["localStorage"] = []

    async def save_to_json(self, new_data):
        target_dir = f"json/{self.user_id}"
        os.makedirs(target_dir, exist_ok=True)
        try:
            with open(f"{target_dir}/cookies.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[self.section_name] = new_data
        with open(f"{target_dir}/cookies.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.logger.info(f"Saved cookies + localStorage for {self.user_id} : {self.section_name}")

    async def login(self):
        self.logger.info("Starting generic login flow...")

        # 1. Wait for login dialog, or optionally open it
        if self.login_dialog_locator:
            try:
                await self.page.wait_for_selector(self.login_dialog_locator, timeout=2000, state="visible")
                self.logger.info("Login dialog already visible.")
            except Exception:
                if self.optional_open_login_locator:
                    await async_safe_click(self.page, self.optional_open_login_locator)
                await self.page.wait_for_selector(self.login_dialog_locator, timeout=2000, state="visible")

        # 2. Optional tab switch
        if self.tab_locator:
            await async_safe_click(self.page, self.tab_locator)
            await asyncio.sleep(0.5)

        # 3. Optional checkboxes
        for check_box_locator in self.check_box_locators:
            try:
                await async_safe_click(self.page, check_box_locator)
                await asyncio.sleep(0.3)
                self.logger.info("Checked the checkbox as required.")
            except Exception as e:
                self.logger.warning(f"Checkbox locator exists but could not be clicked: {e}")

        # 4. Credentials entering
        if self.phone_locator:
            await async_safe_fill(self.page, self.phone_locator, self.phone, clear_first=True)
            await asyncio.sleep(0.3)
        if self.password_locator:
            await async_safe_fill(self.page, self.password_locator, self.passwd, clear_first=True)
            await asyncio.sleep(0.3)

        # 5. Submit login
        if self.submit_locator:
            await async_safe_click(self.page, self.submit_locator)
        self.logger.info("Login button clicked, waiting for login success...")

        # 6. Detect wrong phone/passwd (if locator exists)
        if self.wrong_phone_passwd:
            try:
                await self.page.wait_for_selector(self.wrong_phone_passwd, timeout=3000)
                raise ValidationError(f"{self.section_name}用户名或密码错误，请检查后重试")
            except Exception:
                pass  # Timeout means not found (normal)

        import time
        time.sleep(30)
        
        # 7. Wait login dialog to disappear (if locator defined)
        if self.login_dialog_locator:
            try:
                await self.page.wait_for_selector(
                    self.login_dialog_locator,
                    state="hidden",
                    timeout=30000
                )
                self.logger.info("Login dialog disappeared — login successful.")
            except PlaywrightTimeoutError:
                raise RuntimeError("Login dialog did not disappear — login likely failed.")

    async def update(self):
        """
        Integrated validation and update process.
        """
        cookies_file_path = f"json/{self.user_id}/cookies.json"
        prev_storage_state = None

        if os.path.exists(cookies_file_path) and self.validate_before_update:
            try:
                with open(cookies_file_path, "r", encoding="utf-8") as f:
                    cookies_json = json.load(f)
                prev_storage_state = cookies_json.get(self.section_name)
            except Exception as e:
                self.logger.warning(f"Could not load previous cookies, will perform fresh login: {e}")

        use_old_state = self.validate_before_update and prev_storage_state is not None

        pw_manager = AsyncPlaywrightManager()
        await pw_manager.start()
        result_storage_state = None

        try:
            async with PlaywrightContext(
                pw_manager,
                storage_state=prev_storage_state if use_old_state else None,
                accept_downloads=True,
                ignore_https_errors=True,
            ) as context:
                page = await new_stealth_page(context)
                self.page = page

                cookies_valid = False
                # 1️⃣ Navigate explicitly
                await page.goto(self.validation_url, wait_until="domcontentloaded", timeout=30000)

                if use_old_state:
                    try:
                        # 2️⃣ Bounded SPA settle window (critical)
                        await page.wait_for_timeout(5000)

                        # 3️⃣ Decide auth state AFTER settle
                        if self.login_dialog_locator and await page.is_visible(self.login_dialog_locator):
                            self.logger.warning("Login dialog still visible after refresh settle window.")
                            cookies_valid = False
                        else:
                            cookies_valid = True

                    except Exception as e:
                        self.logger.warning(f"Cookie validation failed, will login: {e}")
                        cookies_valid = False

                    if cookies_valid:
                        self.logger.info("Cookies valid, no update necessary.")
                        data = await page.context.storage_state()
                        self.normalize_cookies(data["cookies"])
                        self.normalize_local_storage(data["origins"])
                        if data:
                            await self.save_to_json(data)
                        return data
                    # else → fall through to login

                # 4️⃣ Force real login
                await self.login()

                # Optional: short settle after login
                await page.wait_for_timeout(1000)

                # 5️⃣ Save ONLY after confirmed login
                if self.login_dialog_locator and await page.is_visible(self.login_dialog_locator):
                    raise RuntimeError("Login failed — refusing to save storage_state")

                data = await page.context.storage_state()
                self.normalize_cookies(data["cookies"])
                self.normalize_local_storage(data["origins"])
                if data:
                    await self.save_to_json(data)
                result_storage_state = data

        finally:
            await pw_manager.shutdown()

        return result_storage_state


from utils.database import get_db_manager, UsersRepository

async def update_common_cookies(sources: list = None, validate_before_update: bool = False):
    """
    Updates cookies for a given user and specified sources.
    - If sources is given: updates only those sites, otherwise updates all sites for the user.
    - If validate_before_update is True, validates cookies before updating.
    """
    db = get_db_manager()
    users_repo = UsersRepository(db)

    with open("json/locators.json", "r", encoding="utf-8") as f:
        locators_data = json.load(f)

    common_auths = [r for r in users_repo.get_all_users() if r["user_id"] == "common" and (sources is None or r["source"] in sources)]

    for entry in common_auths:
        source = entry.get("source")
        phone = entry.get("phone")
        password = entry.get("password")
        locator_cfg = locators_data.get(source)
        if not locator_cfg:
            continue

        auth_entry = {
            "name": source,
            "url": locator_cfg.get("url"),
            "phone": phone,
            "passwd": password,
            "wait_for_dynamical": locator_cfg.get("wait_for_dynamical"),
            "locators": locator_cfg.get("locators"),
        }

        scraper = AsyncCookiesHandler(auth_entry, user_id="common", validate_before_update=validate_before_update)
        await scraper.update()


from utils.redis_utils import get_aioredis_client
from utils.redis_lock import RedisLock

async def update_agent_cookies(
    user_credentials: list[dict],
    user_id: str,
    validate_before_update: bool = False,
):
    """
    Accepts a list of user credentials [{"source": ..., "phone": ..., "password": ...}, ...]
    Runs cookie updating logic for each entry.
    """
    with open("json/locators.json", "r", encoding="utf-8") as f:
        locators_data = json.load(f)

    redis = await get_aioredis_client()
    cks = {}

    for cred in user_credentials:
        source = cred.get("source")
        phone = cred.get("phone")
        password = cred.get("password")
        email = cred.get("email", None)

        locator_cfg = locators_data.get(source)
        if not locator_cfg:
            continue

        auth_entry = {
            "name": source,
            "url": locator_cfg.get("url"),
            "phone": phone,
            "passwd": password,
            "email": email,
            "wait_for_dynamical": locator_cfg.get("wait_for_dynamical"),
            "locators": locator_cfg.get("locators"),
        }

        uid = "common_agents" if source == "gangtise" else user_id
        scraper = AsyncCookiesHandler(
            auth_entry,
            user_id=uid,
            validate_before_update=validate_before_update,
        )

        # 🔒 Serialize gangtise only
        if source == "gangtise":
            lock = RedisLock(
                redis,
                key="lock:auth:gangtise",
                ttl=120,          # long enough for login
                retry_delay=0.5,
            )
            async with lock:
                ck = await scraper.update()
        else:
            ck = await scraper.update()

        cks[source] = ck

    return cks


