import time
import random
import os
import pyperclip

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from browser.adspower import AdsPowerManager


class ThreadsBot:
    BASE_URL = "https://www.threads.net"

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
        selenium_address, _ = self.adspower.start_browser(self.adspower_id)
        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", selenium_address)
        self.driver = webdriver.Chrome(options=options)
        self.log.info(f"Browser opened for {self.username}")
        return self.driver

    def close_browser(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self.adspower.stop_browser(self.adspower_id)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def go(self, url):
        self.driver.get(url)
        time.sleep(random.uniform(2, 3.5))

    def go_home(self):
        self.go(self.BASE_URL)

    def is_logged_in(self):
        try:
            self.go_home()
            for xpath in [
                '//div[contains(text(),"What\'s new?")]',
                '//div[@aria-label="Empty text field. Type to compose a new post."]',
                '//button[@aria-label="Profile"]',
                '//a[contains(@href,"/home")]',
            ]:
                try:
                    WebDriverWait(self.driver, 4).until(
                        EC.presence_of_element_located((By.XPATH, xpath))
                    )
                    return True
                except Exception:
                    pass
            return "threads.net" in self.driver.current_url
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def wait(self, by, selector, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )

    def wait_click(self, by, selector, timeout=10):
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
        selectors = [
            (By.XPATH, '//div[@aria-label="Empty text field. Type to compose a new post."]'),
            (By.XPATH, '//div[contains(text(),"What\'s new?")]'),
            (By.XPATH, '//button[@aria-label="New post"]'),
            (By.XPATH, '//div[contains(@class,"xefz13k") and contains(@class,"x1gpr77m")]'),
        ]
        for by, sel in selectors:
            try:
                el = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((by, sel)))
                el.click()
                time.sleep(2.5)
                break
            except Exception:
                continue

        try:
            return WebDriverWait(self.driver, 8).until(
                EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
            )
        except Exception:
            self.log.warning("Could not open compose dialog")
            return None

    def click_post_button(self):
        """Click the Post submit button in the compose dialog."""
        attempts = [
            (By.XPATH, '//*[not(contains(text(),"Posting")) and text()="Post"]'),
            (By.XPATH, '//div[@role="button" and contains(text(),"Post")]'),
        ]
        for by, sel in attempts:
            try:
                el = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((by, sel)))
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
