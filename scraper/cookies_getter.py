
import json
import time
from loguru import logger
from selenium.webdriver.support.wait import WebDriverWait
from undetected_chromedriver import By

from config import get_chrome_driver
from utils.interactive import EC, safe_click, safe_send_keys

class BaseCookies:
    """Lightweight base class that provides:
    - universal browser + URL open
    - cookie/localstorage extraction
    - JSON saving (common or user-specific)
    - overridable login()
    """

    def __init__(self, phone, passwd, url, section_name, cookies: bool, user_id=None):
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
        self.cookies = cookies
        self.logger = logger.bind(site=section_name)
        self.driver = get_chrome_driver("", "")

    # -------------------------------------------------------
    # Fundamental behavior
    # -------------------------------------------------------
    def login(self):
        """Must be implemented by subclass."""
        raise NotImplementedError("Subclasses must implement login()")

    def open_url(self):
        self.logger.info(f"Opening URL: {self.url}")
        self.driver.get(self.url)
        time.sleep(2)

    def wait(self, locator, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located(locator)
        )

    # -------------------------------------------------------
    # Universal storage extractors
    # -------------------------------------------------------
    def extract_cookies(self):
        cookies =  self.driver.get_cookies()
        return self.normalize_domains(cookies)

    def extract_local_storage(self):
        data = self.driver.execute_script("""
            var items = {}, ls = window.localStorage;
            for (var i = 0; i < ls.length; i++) {
                let key = ls.key(i);
                items[key] = ls.getItem(key);
            }
            return items;
        """)
        return self._decode_local_storage(data)

    @staticmethod
    def normalize_domains(cookie_list):
        for c in cookie_list:
            if c["domain"].startswith("www"):
                c["domain"] = c["domain"][3:]
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
    def save_to_json(self, cookies=None, local_storage=None):
        """
        Stores data in:
            common           → cookies.json["common"][section_name]
            per user        → cookies.json[user_id][section_name]
        """
        try:
            with open("json/cookies.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {}

        # Decide where to write
        if self.user_id:
            if self.user_id not in data:
                data[self.user_id] = {}
            section = data[self.user_id].get(self.section_name, {})
        else:
            section = data["common"].get(self.section_name, {})
        # Store
        if self.cookies and cookies:
            section["cookies"] = cookies
        section["local_storage"] = local_storage
        # Write back
        if self.user_id:
            data[self.user_id][self.section_name] = section
        else:
            data["common"][self.section_name] = section

        with open("json/cookies.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if self.cookies:
            self.logger.info(f"Saved cookies + localStorage for {self.section_name}")
        else:
            self.logger.info(f"Saved localStorage for {self.section_name}")

    # -------------------------------------------------------
    # Main workflow
    # -------------------------------------------------------
    def run(self):
        try:
            self.open_url()
            self.login()  # implemented in subclass
            cookies = None
            local_storage = None
            if self.cookies:
                cookies = self.extract_cookies()
            local_storage = self.extract_local_storage()

            if local_storage or cookies:
                self.save_to_json(cookies, local_storage)

        finally:
            try:
                self.driver.quit()
            except:
                pass


class GeneralCookies(BaseCookies):
    """
    A universal login handler:
    - Reads locators from cookies.json (if provided)
    - Performs login with phone/password
    - Saves cookies + LocalStorage through BaseCookies
    """

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
            cookies=config.get("cookies"),
            section_name=config.get("name"),
            user_id=user_id,
        )

        # Load JSON locators (optional)
        locs = config.get("locators", {})

        def parse(loc):
            if not loc:
                return None
            by = loc["by"].lower()
            value = loc["value"]
            if by == "css":
                return (By.CSS_SELECTOR, value)
            if by == "xpath":
                return (By.XPATH, value)
            raise ValueError(f"Unknown locator type: {by}")

        # Generic locators
        self.login_dialog_locator = parse(locs.get("login_dialog"))
        self.tab_locator = parse(locs.get("tab_switch"))
        self.phone_locator = parse(locs.get("phone_input"))
        self.password_locator = parse(locs.get("pwd_input"))
        self.submit_locator = parse(locs.get("login_btn"))
        self.optional_open_login_locator = parse(locs.get("open_login_btn"))

    # ---------------------------------------------------------------------
    # LOGIN LOGIC (only this function is site-specific through JSON)
    # ---------------------------------------------------------------------
    def login(self):
        self.logger.info("Starting generic login flow...")

        # 1. Ensure login dialog is visible
        if self.login_dialog_locator:
            try:
                self.wait(self.login_dialog_locator, timeout=8)
                self.logger.info("Login dialog already visible.")
            except:
                if self.optional_open_login_locator:
                    safe_click(self.driver, self.optional_open_login_locator)
                    time.sleep(1)
                self.wait(self.login_dialog_locator, timeout=8)

        # 2. Switch to phone/password login tab
        if self.tab_locator:
            try:
                safe_click(self.driver, self.tab_locator)
                time.sleep(0.5)
            except:
                self.logger.warning("Tab switch locator exists but could not be clicked.")

        # 3. Enter credentials
        if self.phone_locator:
            safe_send_keys(self.driver, self.phone_locator, self.phone)
            time.sleep(0.3)

        if self.password_locator:
            safe_send_keys(self.driver, self.password_locator, self.passwd)
            time.sleep(0.3)

        # 4. Submit login
        if self.submit_locator:
            safe_click(self.driver, self.submit_locator)
        else:
            raise RuntimeError(f"No submit button locator for site: {self.section_name}")

        self.logger.info("Login button clicked, waiting for login success...")

        # 5. Wait login dialog to disappear (if locator defined)
        if self.login_dialog_locator:
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.invisibility_of_element_located(self.login_dialog_locator)
                )
                self.logger.info("Login dialog disappeared — login successful.")
            except:
                raise RuntimeError("Login dialog did not disappear — login likely failed.")

        time.sleep(1)
        

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
    update_all_cookies()