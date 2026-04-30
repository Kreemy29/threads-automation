import time
import random
import os
import pyperclip

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchWindowException, WebDriverException

from browser.adspower import AdsPowerManager


class ThreadsBot:
    BASE_URL = "https://www.threads.com"

    def __init__(self, username, adspower_id, logger):
        self.username = username
        self.adspower_id = adspower_id
        self.log = logger
        self.driver = None
        self.adspower = AdsPowerManager(log=logger)

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def open_browser(self):
        selenium_address, _, webdriver_path = self.adspower.start_browser(self.adspower_id)
        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", selenium_address)

        if webdriver_path and os.path.exists(webdriver_path):
            service = Service(executable_path=webdriver_path)
            self.driver = webdriver.Chrome(service=service, options=options)
        else:
            self.driver = webdriver.Chrome(options=options)

        self.log.info(f"Browser opened for {self.username}")

        # Always navigate to Threads on startup so we have a clean known page
        self._navigate_to_threads()
        return self.driver

    def _navigate_to_threads(self):
        """Navigate to Threads home and wait for the page to be interactive."""
        for attempt in range(3):
            try:
                self.driver.get(self.BASE_URL)
                # Wait for body to be present (page loaded at all)
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                time.sleep(random.uniform(2, 3))
                self.log.info(f"Navigated to Threads ({self.driver.current_url})")
                return
            except Exception as e:
                self.log.warning(f"Navigation attempt {attempt + 1} failed: {e}")
                time.sleep(3)
        self.log.warning("Could not navigate to Threads after 3 attempts — continuing anyway")

    def close_browser(self):
        if self.driver:
            try:
                # Do NOT call driver.quit() — it sends a shutdown command that kills
                # the AdsPower browser window. Just drop the Selenium reference and
                # let AdsPower's stop_browser API handle the actual shutdown.
                self.driver = None
            except Exception:
                pass
        self.adspower.stop_browser(self.adspower_id)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _ensure_window(self):
        """Switch to an open window if the current one was closed."""
        try:
            _ = self.driver.current_url
        except NoSuchWindowException:
            handles = self.driver.window_handles
            if handles:
                self.driver.switch_to.window(handles[-1])
                self.log.info("Switched to available window after previous one closed")
            else:
                raise NoSuchWindowException("All browser windows are closed")

    def go(self, url):
        self._ensure_window()
        self.driver.get(url)
        WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(random.uniform(2, 3.5))

    def go_home(self):
        self.go(self.BASE_URL)

    def is_logged_in(self):
        try:
            url = self.driver.current_url.lower()

            # Navigate to Threads if not already there
            if "threads.com" not in url and "threads.net" not in url:
                self._navigate_to_threads()
                time.sleep(2)
                url = self.driver.current_url.lower()

            # Explicit logged-out indicators in URL
            if any(k in url for k in ["login", "accounts/login", "/logout"]):
                return False

            # Check for visible login form or password field
            try:
                login_form = self.driver.find_elements(
                    By.XPATH,
                    '//input[@type="password"] | //button[contains(text(),"Log in")]'
                )
                if login_form:
                    return False
            except Exception:
                pass

            # Positive check: logged-in UI element present
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//div[@role="textbox"] | '
                        '//*[@aria-label="New post"] | '
                        '//*[contains(@aria-label,"compose")] | '
                        '//span[contains(text(),"What\'s new?")] | '
                        '//div[contains(text(),"What\'s new?")]'
                    ))
                )
                return True
            except Exception:
                pass

            return "threads.com" in url or "threads.net" in url
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def wait(self, by, selector, timeout=15):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )

    def wait_click(self, by, selector, timeout=15):
        el = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, selector))
        )
        el.click()
        return el

    def smart_click(self, element):
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", element
            )
            time.sleep(0.4)
            try:
                element.click()
                return True
            except Exception:
                try:
                    self.driver.execute_script("arguments[0].click();", element)
                    return True
                except Exception:
                    ActionChains(self.driver).move_to_element(element).click().perform()
                    return True
        except Exception as e:
            self.log.warning(f"smart_click failed: {e}")
            return False

    def paste_text(self, element, text):
        """Paste text into a focused element. Uses clipboard + ActionChains to handle emojis."""
        import platform
        try:
            original = pyperclip.paste()
            pyperclip.copy(text)
            time.sleep(0.5)

            element.click()
            time.sleep(0.5)

            if platform.system() == "Darwin":
                ActionChains(self.driver).key_down(Keys.COMMAND).send_keys("v").key_up(Keys.COMMAND).perform()
            else:
                ActionChains(self.driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()

            time.sleep(1)
            pyperclip.copy(original)
            return True
        except Exception as e:
            self.log.warning(f"paste_text clipboard failed: {e} — trying send_keys fallback")
            try:
                element.clear()
                for char in text:
                    element.send_keys(char)
                    time.sleep(0.03)
                return True
            except Exception:
                return False

    def scroll_down(self, amount=None):
        px = amount or random.randint(400, 900)
        self.driver.execute_script(f"window.scrollBy(0, {px});")
        time.sleep(random.uniform(0.3, 1.2))

    def scroll_to_top(self):
        self.driver.execute_script("window.scrollTo(0, 0);")

    # ------------------------------------------------------------------
    # Post creation entry point (opens the compose dialog)
    # ------------------------------------------------------------------

    def open_compose(self):
        """Open the post compose dialog. Returns the textbox element or None."""
        self.go_home()
        self.scroll_to_top()
        time.sleep(1)

        # Ordered by likelihood — aria-label is most stable
        selectors = [
            (By.XPATH, '//div[@aria-label="Empty text field. Type to compose a new post."]'),
            (By.XPATH, '//span[contains(text(),"What\'s new?")]'),
            (By.XPATH, '//div[contains(text(),"What\'s new?")]'),
            (By.XPATH, '//div[@aria-label="Create"]'),
            (By.XPATH, '//a[@aria-label="Create"]'),
            (By.XPATH, '//button[@aria-label="New post"]'),
            (By.XPATH, '//*[@aria-label="New post"]'),
            (By.CSS_SELECTOR, 'div[aria-label="Create"]'),
        ]

        clicked = False
        for by, sel in selectors:
            try:
                el = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((by, sel)))
                self.smart_click(el)
                self.log.info(f"Compose opened via: {sel[:60]}")
                time.sleep(4)  # give the modal time to fully render
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            # Log what aria-labels ARE present to help diagnose selector failures
            try:
                labels = self.driver.execute_script("""
                    return Array.from(document.querySelectorAll('[aria-label]'))
                        .map(e => e.getAttribute('aria-label'))
                        .filter(l => l && l.length < 80);
                """)
                self.log.info(f"aria-labels on page: {labels[:20]}")
            except Exception:
                pass

            # JS fallback: find any button/div that suggests "new post" or "create"
            self.log.warning("Primary selectors failed — trying JS compose fallback")
            try:
                result = self.driver.execute_script("""
                    const hints = ['new post', "what's new", 'create', 'compose'];
                    const els = document.querySelectorAll('[aria-label],[placeholder]');
                    for (const el of els) {
                        const label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').toLowerCase();
                        if (hints.some(h => label.includes(h))) {
                            el.click();
                            return el.getAttribute('aria-label') || el.getAttribute('placeholder');
                        }
                    }
                    return null;
                """)
                if result:
                    self.log.info(f"Compose opened via JS fallback: {result}")
                    time.sleep(3)
                    clicked = True
            except Exception as e:
                self.log.warning(f"JS compose fallback failed: {e}")

        # Wait for the textbox to appear
        for timeout in [10, 15]:
            try:
                textbox = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
                )
                return textbox
            except Exception:
                if timeout == 10 and clicked:
                    self.log.info("Textbox not found yet, waiting longer...")
                continue

        self.log.warning(f"[{self.username}] Could not open compose dialog")
        return None

    def click_post_button(self):
        """Click the Post submit button in the compose dialog."""
        attempts = [
            (By.XPATH, '//*[not(contains(text(),"Posting")) and text()="Post"]'),
            (By.XPATH, '//div[@role="button" and normalize-space(text())="Post"]'),
            (By.XPATH, '//span[normalize-space(text())="Post"]/ancestor::div[@role="button"]'),
        ]
        for by, sel in attempts:
            try:
                el = WebDriverWait(self.driver, 8).until(EC.element_to_be_clickable((by, sel)))
                el.click()
                time.sleep(5)
                return True
            except Exception:
                continue
        # JS fallback
        try:
            result = self.driver.execute_script("""
                const buttons = document.querySelectorAll('div[role="button"]');
                for (const b of buttons) {
                    if (b.textContent.trim() === 'Post' && b.getAttribute('aria-disabled') !== 'true') {
                        b.click(); return true;
                    }
                }
                return false;
            """)
            if result:
                time.sleep(5)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Profile page navigation
    # ------------------------------------------------------------------

    def go_to_profile(self, username=None):
        target = username or self.username
        self.go(f"{self.BASE_URL}/@{target}")

    def go_to_settings(self):
        self.go(f"{self.BASE_URL}/settings")

    def go_to_edit_profile(self):
        self.go(f"{self.BASE_URL}/settings/profile/edit")
