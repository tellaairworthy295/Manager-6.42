
import json
import time
from loguru import logger
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from pwright.context_manager import playwright_context
from pwright.page_factory import new_stealth_page

class BaseCookies:
    """Lightweight base class that provides:
    - universal browser + URL open
    - cookie/localstorage extraction
    - JSON saving (common or user-specific)
    - overridable login()
    """

    def __init__(self, phone, passwd, url, section_name, page: Page, user_id=None):
        """
        phone/passwd/url come from cookies.json
        section_name: "jiuyan", "xuangutong", "alphapai", etc.
        user_id: None => common area, otherwise per-user block
        """
        self.phone = phone
        self.passwd = passwd
        self.url = url
        self.section_name = section_name
        self.user_id = user_id  # supports user-specific area
        self.logger = logger.bind(site=section_name)
        
        self.page = page

    # -------------------------------------------------------
    # Fundamental behavior
    # -------------------------------------------------------
    def login(self):
        """Must be implemented by subclass."""
        raise NotImplementedError("Subclasses must implement login()")

    def open_url(self):
        self.logger.info(f"Opening URL: {self.url}")
        self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

    @staticmethod
    def normalize_cookies(cookie_list):
        for c in cookie_list:
            domain = c.get("domain", "")
            if domain.startswith("www"):
                c["domain"] = domain[3:]
        return cookie_list

    @staticmethod
    def normalize_local_storage(origins):
        if origins[0].get("origin", "") == "https://alphapai-web.rabyte.cn":
            # Add specified pairs manually to localStorage for alphapai
            manual_pairs = [
                {"name": "search-to-paipai-guide", "value": "true"},
                {"name": "paipai-agent-fastsheet-us-guide", "value": "true"},
                {"name": "search-to-paipai-guide-date", "value": "1765348801926"},
                {"name": "version-market-tip", "value": "1"},
                {"name": "MODE", "value": "undefined"},
                {"name": "hasShowpaipaiAnswerRangeGuide", "value": "true"}
            ]
            if "localStorage" not in origins[0] or not isinstance(origins[0]["localStorage"], list):
                origins[0]["localStorage"] = []
            # Only add if not already present
            existing_names = {entry['name'] for entry in origins[0]["localStorage"]}
            for pair in manual_pairs:
                if pair['name'] not in existing_names:
                    origins[0]["localStorage"].append(pair)
        elif origins[0].get("origin", "") == "https://www.jiuyangongshe.com":
            # Remove all localStorage for this origin
            origins[0]["localStorage"] = []
            
    # -------------------------------------------------------
    # Saving logic
    # -------------------------------------------------------
    def save_to_json(self, new_data):
        try:
            with open("json/cookies.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {}

        # Write back
        if self.user_id:
            data[self.user_id][self.section_name] = new_data
        else:
            data["common"][self.section_name] = new_data

        with open("json/cookies.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"Saved cookies + localStorage for {self.section_name}")

    # -------------------------------------------------------
    # Main workflow
    # -------------------------------------------------------
    def run(self):
        try:
            self.open_url()
            self.login()  # implemented in subclass
            data = self.page.context.storage_state()
            self.normalize_cookies(data["cookies"])
            self.normalize_local_storage(data["origins"])
            if data:
                self.save_to_json(data)

        finally:
            try:
                self.context.close()
                self.browser.close()
                self.playwright.stop()
            except:
                pass
    
    def get_ck(self):
        try:
            self.open_url()
            self.login()  # implemented in subclass
            data = self.page.context.storage_state()
            self.normalize_cookies(data["cookies"])
            self.normalize_local_storage(data["origins"])
            return data
        finally:
            try:
                self.context.close()
                self.browser.close()
                self.playwright.stop()
            except:
                pass

class GeneralCookies(BaseCookies):
    def __init__(self, config: dict, page: Page, user_id: str | None):
        """
        config is one entry inside "authen": [...]
        {
          "name": "...",
          "url": "...",
          "phone": "...",
          "passwd": "...",
          "locators": {...}
        }
        """
        super().__init__(
            phone=config.get("phone"),
            passwd=config.get("passwd"),
            url=config.get("url"),
            section_name=config.get("name"),
            page = page,
            user_id=user_id
        )
        locs = config.get("locators", {})
        wfd = config.get("wait_for_dynamical", None)
        self.logger.info(wfd)
        def parse(loc):
            """Convert JSON locator to Playwright locator string."""
            if loc == None:
                return None
            if not loc:
                return None
            by = loc["by"].lower()
            value = loc["value"]
            if by == "css":
                return value  # Playwright accepts CSS selectors directly
            if by == "xpath":
                return f"xpath={value}"  # Playwright XPath format
            raise ValueError(f"Unknown locator type: {by}")

        # Generic locators (now strings instead of tuples)
        self.login_dialog_locator = parse(locs.get("login_dialog"))
        self.tab_locator = parse(locs.get("tab_switch"))
        self.phone_locator = parse(locs.get("phone_input"))
        self.password_locator = parse(locs.get("pwd_input"))
        self.submit_locator = parse(locs.get("login_btn"))
        self.optional_open_login_locator = parse(locs.get("open_login_btn"))
        self.wait_for_dynamic = parse(wfd)
        self.check_box_locators = []
        check_box = locs.get("check_box", [])
        if check_box:
            for check_box_locator in locs.get("check_box", []):
                self.check_box_locators.append(parse(check_box_locator))

    # ---------------------------------------------------------------------
    # Playwright helper methods
    # ---------------------------------------------------------------------
    def _safe_click(self, locator, max_attempts=5, timeout=10000):
        """Robust click with retries."""
        last_err = None
        for attempt in range(1, max_attempts + 1):
            try:
                element = self.page.locator(locator).first
                element.scroll_into_view_if_needed()
                element.click(timeout=timeout)
                self.logger.debug(f"Click succeeded on {locator}")
                return
            except Exception as e:
                last_err = e
                self.logger.warning(f"Click attempt {attempt}/{max_attempts} failed for {locator}: {e}")
                time.sleep(1 * attempt)
        raise RuntimeError(f"Failed to click {locator} after {max_attempts} attempts: {last_err}")

    def _safe_send_keys(self, locator, text, max_attempts=5, clear_first=True, timeout=10000):
        """Robust send keys with retries."""
        last_err = None
        for attempt in range(1, max_attempts + 1):
            try:
                element = self.page.locator(locator).first
                element.scroll_into_view_if_needed()
                if clear_first:
                    element.clear()
                element.fill(text)
                self.logger.debug(f"Send keys succeeded on {locator}")
                return
            except Exception as e:
                last_err = e
                self.logger.warning(f"Send keys attempt {attempt}/{max_attempts} failed for {locator}: {e}")
                time.sleep(1 * attempt)
        raise RuntimeError(f"Failed to send keys to {locator} after {max_attempts} attempts: {last_err}")

    # ---------------------------------------------------------------------
    # LOGIN LOGIC (only this function is site-specific through JSON)
    # ---------------------------------------------------------------------
    def login(self):
        self.logger.info("Starting generic login flow...")

        if self.login_dialog_locator:
            try:
                self.page.wait_for_selector(self.login_dialog_locator, timeout=8000, state="visible")
                self.logger.info("Login dialog already visible.")
            except:
                if self.optional_open_login_locator:
                    self._safe_click(self.optional_open_login_locator)
                self.page.wait_for_selector(self.login_dialog_locator, timeout=8000, state="visible")
        # 2. Switch to phone/password login tab
        if self.tab_locator:
            try:
                self._safe_click(self.tab_locator)
                time.sleep(0.5)
            except Exception as e:
                self.logger.warning(f"Tab switch locator exists but could not be clicked: {e}")

        if self.check_box_locators:
            for check_box_locator in self.check_box_locators:
                try:
                    self._safe_click(check_box_locator)
                    time.sleep(0.3)
                    self.logger.info("Checked the checkbox as required.")
                except Exception as e:
                    self.logger.warning(f"Checkbox locator exists but could not be clicked: {e}")
            
        # 3. Enter credentials
        if self.phone_locator:
            self._safe_send_keys(self.phone_locator, self.phone)
            time.sleep(0.3)

        if self.password_locator:
            self._safe_send_keys(self.password_locator, self.passwd)
            time.sleep(0.3)

        # 4. Submit login
        if self.submit_locator:
            self._safe_click(self.submit_locator)
        else:
            raise RuntimeError(f"No submit button locator for site: {self.section_name}")

        self.logger.info("Login button clicked, waiting for login success...")
        # 5. Wait login dialog to disappear (if locator defined)
        if self.login_dialog_locator:
            try:
                self.page.wait_for_selector(
                    self.login_dialog_locator,
                    state="hidden",
                    timeout=60000
                )
                self.logger.info("Login dialog disappeared — login successful.")
            except PlaywrightTimeoutError:
                raise RuntimeError("Login dialog did not disappear — login likely failed.")

        if self.wait_for_dynamic:
            self.page.wait_for_selector(self.wait_for_dynamic, timeout=8000, state="visible")
            
        

from utils.database import get_db_manager, UsersRepository

def update_all_cookies():
    """
    Updates cookies for all users (including 'common') and all supported sources.
    - For all users in database (including 'common'), grab their login info and refresh all their cookies.
    """
    db = get_db_manager()
    users_repo = UsersRepository(db)
    all_users = users_repo.get_all_users()  # List[dict], including "common"

    # index by user_id for loop logic
    from collections import defaultdict

    with open("json/locators.json", "r", encoding="utf-8") as f:
        locators_data = json.load(f)
        
    users_by_id = defaultdict(list)
    for row in all_users:
        users_by_id[row["user_id"]].append(row)

    for user_id, auth_entries in users_by_id.items():
        for entry in auth_entries:
            source = entry.get("source")
            phone = entry.get("phone")
            password = entry.get("password")
            email = entry.get("email", None)

            # get locator & site config
            locator_cfg = locators_data.get(source)
            if not locator_cfg:
                continue

            # Prepare auth info (phone, password, etc)
            auth_entry = {
                "name": source,
                "url": locator_cfg.get("url"),
                "phone": phone,
                "passwd": password,
                "email": email,
                "wait_for_dynamical": locator_cfg.get("wait_for_dynamical"),
                "locators": locator_cfg.get("locators"),
            }
    
            with playwright_context(storage_state=None, bypass_ext_path="") as context:
                page = new_stealth_page(context)
                scraper = GeneralCookies(auth_entry, page = page, user_id=user_id)
                scraper.run()


def update_common_cookies(sources: list = None):
    """
    Updates cookies for a given user and specified sources.
    - If user_id is None: updates ONLY 'common' user for all sources or limited by sources param.
    - If sources is given: updates only those sites, otherwise updates all sites for the user.
    """
    db = get_db_manager()
    users_repo = UsersRepository(db)

    with open("json/locators.json", "r", encoding="utf-8") as f:
        locators_data = json.load(f)
    # Who to update
    uid = "common"
    common_auths = [r for r in users_repo.get_all_users() if r["user_id"] == uid and r["source"] in sources]

    for entry in common_auths:
        source = entry.get("source")
        phone = entry.get("phone")
        password = entry.get("password")
        # get locator & site config
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

        with playwright_context(storage_state=None, bypass_ext_path="") as context:
            page = new_stealth_page(context)
            scraper = GeneralCookies(auth_entry, page = page, user_id=uid)
            scraper.run()


def update_agent_cookies(user_credentials: list[dict]):
    """
    Accepts a list of user credentials [{"source": ..., "phone": ..., "password": ...}, ...]
    Runs cookie updating logic for each entry.
    Each dict must have: "source", "phone", "password" (optionally: "email").
    """
    with open("json/locators.json", "r", encoding="utf-8") as f:
        locators_data = json.load(f)
    
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
        with playwright_context(storage_state=None, bypass_ext_path="") as context:
            page = new_stealth_page(context)
            scraper = GeneralCookies(auth_entry, page = page, user_id=None)
            ck = scraper.get_ck()
            cks[source] = ck

    return cks

    
if __name__ == "__main__":
    user_id = "564b3391-510f-4b50-a038-7df413bec15d"
    # Load cookies.json and find the gangtise entry under this user_id
    with open("json/cookies.json", "r", encoding="utf-8") as f:
        cookies_data = json.load(f)
    auth_list = cookies_data["common"]["authen"]
    gangtise_entry = next((entry for entry in auth_list if entry["name"] == "gangtise"), None)
    if gangtise_entry is None:
        raise RuntimeError("gangtise entry not found for user_id '%s'" % user_id)
    scraper = GeneralCookies(gangtise_entry, user_id=None)
    scraper.run()