"""Optional Selenium fallback for deep, DOM-level browser automation.

This is NOT the primary browser mechanism — :mod:`seedcode.computer.browser`
drives the user's real default browser through the OS and the UI resolver, with
no driver downloads and full offline support. Selenium is only used when a task
genuinely needs DOM-level control (CSS/XPath selectors, ``execute_script``) and
the user has installed the optional extra.

Kept isolated here so importing the Computer Engine never pulls in Selenium, and
so the default-browser path has zero dependency on WebDriver being present.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selenium import webdriver

_driver: "webdriver.Chrome | webdriver.Edge | None" = None
_browser_type: str | None = None


class BrowserError(Exception):
    """Selenium-backed browser automation error."""


def is_available() -> tuple[bool, str]:
    """Whether the optional Selenium fallback can run."""
    try:
        import selenium  # noqa: F401

        return True, ""
    except ImportError:
        return False, "selenium not installed (pip install seedcode-cli[browser])"


def _get_driver() -> "webdriver.Chrome | webdriver.Edge":
    """Get or create the WebDriver session."""
    global _driver, _browser_type

    if _driver is not None:
        try:
            _driver.current_url  # liveness probe
            return _driver
        except Exception:
            _driver = None

    if _driver is None:
        _driver, _browser_type = _launch_browser()
    return _driver


def _launch_browser() -> tuple["webdriver.Chrome | webdriver.Edge", str]:
    """Launch Chrome or Edge under WebDriver."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        from webdriver_manager.microsoft import EdgeChromiumDriverManager

        try:
            options = ChromeOptions()
            options.add_argument("--start-maximized")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])
            service = ChromeService(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=options), "chrome"
        except Exception:
            pass

        try:
            options = EdgeOptions()
            options.add_argument("--start-maximized")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])
            service = EdgeService(EdgeChromiumDriverManager().install())
            return webdriver.Edge(service=service, options=options), "edge"
        except Exception:
            pass
    except ImportError:
        try:
            options = ChromeOptions()
            options.add_argument("--start-maximized")
            return webdriver.Chrome(options=options), "chrome"
        except Exception:
            pass
        try:
            options = EdgeOptions()
            options.add_argument("--start-maximized")
            return webdriver.Edge(options=options), "edge"
        except Exception:
            pass

    raise BrowserError(
        "Could not launch a WebDriver browser. Install the optional extra: "
        "pip install seedcode-cli[browser]"
    )


def navigate(url: str) -> str:
    driver = _get_driver()
    if not url.startswith(("http://", "https://", "file://", "about:")):
        url = "https://" + url
    driver.get(url)
    time.sleep(1)
    return f"Navigated to: {driver.current_url}\nTitle: {driver.title}"


def click_element(selector: str, selector_type: str = "css") -> str:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    driver = _get_driver()
    by_map = {
        "css": By.CSS_SELECTOR, "xpath": By.XPATH, "id": By.ID, "name": By.NAME,
        "class": By.CLASS_NAME, "tag": By.TAG_NAME, "link_text": By.LINK_TEXT,
        "partial_link": By.PARTIAL_LINK_TEXT,
    }
    by = by_map.get(selector_type)
    if by is None:
        raise BrowserError(f"Unknown selector type: {selector_type}")
    try:
        element = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((by, selector)))
        element.click()
        return f"Clicked element: {selector}"
    except Exception as exc:
        raise BrowserError(f"Could not click element '{selector}': {exc}")


def type_in_element(selector: str, text: str, selector_type: str = "css", clear: bool = True) -> str:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    driver = _get_driver()
    by_map = {"css": By.CSS_SELECTOR, "xpath": By.XPATH, "id": By.ID, "name": By.NAME}
    by = by_map.get(selector_type)
    if by is None:
        raise BrowserError(f"Unknown selector type: {selector_type}")
    try:
        element = WebDriverWait(driver, 10).until(EC.presence_of_element_located((by, selector)))
        if clear:
            element.clear()
        element.send_keys(text)
        return f"Typed into {selector}: {text}"
    except Exception as exc:
        raise BrowserError(f"Could not type into '{selector}': {exc}")


def get_page_info() -> str:
    driver = _get_driver()
    return f"URL: {driver.current_url}\nTitle: {driver.title}"


def find_elements(selector: str, selector_type: str = "css") -> str:
    from selenium.webdriver.common.by import By

    driver = _get_driver()
    by_map = {
        "css": By.CSS_SELECTOR, "xpath": By.XPATH, "id": By.ID, "name": By.NAME,
        "class": By.CLASS_NAME, "tag": By.TAG_NAME,
    }
    by = by_map.get(selector_type)
    if by is None:
        raise BrowserError(f"Unknown selector type: {selector_type}")
    try:
        elements = driver.find_elements(by, selector)
        if not elements:
            return f"No elements found matching: {selector}"
        results = []
        for i, elem in enumerate(elements[:20], 1):
            text = elem.text.strip()[:100] or elem.get_attribute("value") or ""
            results.append(f"{i}. {text}")
        return f"Found {len(elements)} elements:\n" + "\n".join(results)
    except Exception as exc:
        raise BrowserError(f"Could not find elements '{selector}': {exc}")


def execute_script(script: str) -> str:
    driver = _get_driver()
    try:
        result = driver.execute_script(script)
        return f"Script executed. Result: {result}"
    except Exception as exc:
        raise BrowserError(f"Script execution failed: {exc}")


def close_browser() -> str:
    global _driver, _browser_type
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None
        _browser_type = None
        return "Browser closed"
    return "No browser open"


def get_current_browser() -> str:
    if _driver is None:
        return "No browser open"
    return _browser_type or "unknown"
