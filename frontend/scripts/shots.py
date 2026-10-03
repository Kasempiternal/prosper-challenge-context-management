"""What the screenshot scripts share: the browser, a page per theme, saving a shot, and the import
trick that reaches the app's own stores."""

import sys
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "screenshots"
BRAVE = Path(r"C:\Users\izotz\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe")


def app_script(body: str, params: str = "") -> str:
    """An async page function whose `loaded(path)` is the module URL the app loaded: imported by that
    exact URL (HMR adds ?t=), a store is the app's own instance."""
    return (f"async ({params}) => {{\n  const loaded = (path) => performance.getEntriesByType('resource')"
            f".map((e) => e.name).findLast((n) => n.includes(path)) ?? path\n{body}\n}}")


def base_url(default: str) -> str:
    return sys.argv[1] if len(sys.argv) > 1 else default


def new_page(browser: Browser, theme: str, errors: list[str], dev_view: bool | None = None, scale: int = 1) -> Page:
    """A fresh 1600x960 context in `theme` (dev_view pins the Dev view toggle), collecting console errors."""
    context = browser.new_context(viewport={"width": 1600, "height": 960}, device_scale_factor=scale)
    init = f"localStorage.setItem('agent-studio:theme', '{theme}')"
    if dev_view is not None:
        init += f"; localStorage.setItem('agent-studio:dev-view', '{int(dev_view)}')"
    context.add_init_script(init)
    page = context.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(f"[{theme}] {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"[{theme}] {e}"))
    return page


def shot(page: Page, name: str, wait_ms: int = 800) -> Path:
    page.wait_for_timeout(wait_ms)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path))
    print(path)
    return path


def run(capture: Callable[[Browser, str, list[str]], None]) -> None:
    """capture(browser, theme, errors) in both themes, in Brave when installed; then the console errors."""
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=BRAVE, headless=True) if BRAVE.exists() else p.chromium.launch()
        for theme in ("light", "dark"):
            capture(browser, theme, errors)
        browser.close()
    if errors:
        print("Console errors:")
        for e in errors:
            print(" ", e)
