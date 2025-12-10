
import json
import time
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from config import get_playwright_browser, get_playwright_page

class BaseCookies:
    """Lightweight base class that provides:
    - universal browser + URL open
    - cookie/localstorage extraction
    - JSON saving (common or user-specific)
    - overridable login()
    """

    def __init__(self, phone, passwd, url, section_name, user_id=None):
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
        
        # Initialize Playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.7499.40 Safari/537.36"
            )
        )
        self.page = self.context.new_page()

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

    def wait(self, locator, timeout=8000):
        """Wait for element to be visible. locator is a string (CSS or XPath)."""
        try:
            self.page.wait_for_selector(locator, timeout=timeout, state="visible")
            return self.page.locator(locator).first
        except PlaywrightTimeoutError:
            raise TimeoutError(f"Element not found: {locator}")

    @staticmethod
    def normalize_domains(cookie_list):
        for c in cookie_list:
            domain = c.get("domain", "")
            if domain.startswith("www"):
                c["domain"] = domain[3:]
        return cookie_list

    @staticmethod
    def _decode_local_storage(ls_dict):
        """Try JSON decode; fall back to string."""
        result = {}
        for k, v in ls_dict.items():
            try:
                result[k] = json.loads(v)
            except Exception:
                result[k] = v
        return result

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
            if data:
                self.save_to_json(data)

        finally:
            try:
                self.context.close()
                self.browser.close()
                self.playwright.stop()
            except:
                pass


class GeneralCookies(BaseCookies):
    def __init__(self, config: dict, user_id: str | None):
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
            user_id=user_id
        )
        locs = config.get("locators", {})

        def parse(loc):
            """Convert JSON locator to Playwright locator string."""
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
        self.check_box_locator = parse(locs.get("check_box"))

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
                self.wait(self.login_dialog_locator, timeout=60000)
                self.logger.info("Login dialog already visible.")
            except:
                if self.optional_open_login_locator:
                    self._safe_click(self.optional_open_login_locator)
                    time.sleep(1)
                self.wait(self.login_dialog_locator, timeout=60000)

        # 2. Switch to phone/password login tab
        if self.tab_locator:
            try:
                self._safe_click(self.tab_locator)
                time.sleep(0.5)
            except Exception as e:
                self.logger.warning(f"Tab switch locator exists but could not be clicked: {e}")

        if self.check_box_locator:
            try:
                self._safe_click(self.check_box_locator)
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
        time.sleep(1)
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

        time.sleep(3)
        

def update_all_cookies():
    """
    Updates ALL cookies:
    - common section websites
    - all user-specific websites (iterate all user_id in cookies.json except 'common')
    """
    with open("json/cookies.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # ------------------------------
    # 1. COMMON WEBSITES
    # ------------------------------
    common_auth = data.get("common", {}).get("authen", [])
    for entry in common_auth:
        try:
            scraper = GeneralCookies(entry, user_id=None)
            scraper.run()
        except Exception as e:
            print(f"[ERROR] Common site '{entry.get('name')}' failed: {e}")

    # ------------------------------
    # 2. USER-SPECIFIC WEBSITES (iterate all user_ids except 'common')
    # ------------------------------
    for user_id in data:
        if user_id == "common":
            continue
        user_auth = data[user_id].get("authen", [])
        for entry in user_auth:
            try:
                scraper = GeneralCookies(entry, user_id=user_id)
                scraper.run()
            except Exception as e:
                print(f"[ERROR] User site '{entry.get('name')}' for user_id '{user_id}' failed: {e}")

if __name__ == "__main__":
    user_id = "564b3391-510f-4b50-a038-7df413bec15d"
    # Load cookies.json and find the gangtise entry under this user_id
    with open("json/cookies.json", "r", encoding="utf-8") as f:
        cookies_data = json.load(f)
    auth_list = cookies_data[user_id]["authen"]
    gangtise_entry = next((entry for entry in auth_list if entry["name"] == "gangtise"), None)
    if gangtise_entry is None:
        raise RuntimeError("gangtise entry not found for user_id '%s'" % user_id)
    scraper = GeneralCookies(gangtise_entry, user_id=user_id)
    scraper.run()