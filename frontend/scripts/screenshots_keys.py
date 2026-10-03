"""Screenshots of the API keys sheet, the top bar's key dot, the call gate and the inline key rows,
in both themes.

/api/keys/status and /api/keys/test are answered here, so every state is reachable without a key
and no request reaches a provider; the rest of /api goes to the backend behind base_url. Never
starts a test call: a live call is simulated through the call store.

    python frontend/scripts/screenshots_keys.py [base_url]
"""

import re

from playwright.sync_api import Browser, Locator, Page, Route

from shots import OUT, app_script, base_url, new_page, run

BASE = base_url("http://localhost:5174")
PROVIDERS = ("openai", "elevenlabs", "jev")
FAKE = {"openai": "sk-proj-demo7d1f0b52e9a4c3k8", "elevenlabs": "sk_demo55aa01bc3e7f9q2z", "jev": "cc-demo-7d1f0b52e9a4m4n6"}
ALL_SERVER = dict.fromkeys(PROVIDERS, True)
NO_SERVER = dict.fromkeys(PROVIDERS, False)
NARROW = {"width": 1280, "height": 800}

SIMULATE_LIVE = app_script("""
  const { useCall } = await import(loaded('/src/store/call.ts'))
  const call = useCall.getState()
  call.begin(); call.connected()""")


class KeysApi:
    """The two key endpoints. A key test is held until answer() so the spinner can be captured."""

    def __init__(self, page: Page, server: dict[str, bool]):
        self.held: list[Route] = []
        page.route("**/api/keys/status", lambda r: r.fulfill(json={p: {"server_key": v} for p, v in server.items()}))
        page.route(re.compile(r"/api/keys/test"), lambda r: self.held.append(r))

    def answer(self, body: dict) -> str:
        """Answers the oldest held test; returns the provider it was for."""
        route = self.held.pop(0)
        route.fulfill(json=body)
        return route.request.url.split("provider=")[-1]


def page_with(browser: Browser, theme: str, errors: list[str], server: dict[str, bool], saved: dict[str, str] | None = None) -> tuple[Page, KeysApi]:
    page = new_page(browser, theme, errors, dev_view=False, scale=2)
    for p, key in (saved or {}).items():
        page.add_init_script(f"localStorage.setItem('agent-studio:{p}-key', '{key}')")
    return page, KeysApi(page, server)


def open_agent(page: Page) -> None:
    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.get_by_role("navigation", name="Agents").get_by_text("Clinic Scheduler").click()
    page.wait_for_selector(".react-flow__node:has-text('greeting')", timeout=10000)


def open_call_panel(page: Page) -> None:
    page.get_by_role("button", name="Test call", exact=True).click()
    page.wait_for_selector("text=Ready to test")


def keys_button(page: Page) -> Locator:
    return page.locator("header button", has_text="Keys")


def sheet(page: Page) -> Locator:
    return page.get_by_role("dialog", name="API keys")


def row(page: Page, provider: str) -> Locator:
    return page.locator(f"section[aria-labelledby='keys-{provider}-title']")


def save_shot(locator: Locator, name: str, wait_ms: int = 450) -> None:
    locator.page.wait_for_timeout(wait_ms)
    path = OUT / f"{name}.png"
    locator.screenshot(path=str(path))
    print(path)


def assert_fits(page: Page) -> None:
    """Nothing in the sheet or the side panel is wider than its box."""
    overflow = page.evaluate("""() => [...document.querySelectorAll('[role=dialog] *, .w-\\\\[384px\\\\] *')]
      .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible'
        && !el.classList.contains('truncate') && !el.classList.contains('sr-only'))
      .map((el) => el.className.toString().slice(0, 80))""")
    assert not overflow, overflow
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= page.viewport_size["width"], width


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    def name(n: str) -> str:
        return f"60-keys-{n}-{theme}"

    # No key anywhere: red dot, the call gate, then the sheet through every test outcome.
    page, api = page_with(browser, theme, errors, NO_SERVER)
    page.set_viewport_size(NARROW)
    open_agent(page)
    assert "add OpenAI and ElevenLabs" in keys_button(page).get_attribute("aria-label")
    save_shot(page.locator("header"), name("topbar-red"))
    open_call_panel(page)
    page.wait_for_selector("text=Add your OpenAI and ElevenLabs keys to start a call")
    save_shot(page.locator(".w-\\[384px\\]"), name("call-gate"))
    page.screenshot(path=str(OUT / f"{name('call-gate-page')}.png"))

    page.get_by_role("button", name="Start test call").click()
    sheet(page).wait_for()
    assert page.evaluate("document.activeElement.id") == "keys-openai-key"
    assert_fits(page)
    save_shot(sheet(page), name("sheet-missing"))
    page.screenshot(path=str(OUT / f"{name('sheet-missing-page')}.png"))

    page.keyboard.press("Escape")
    sheet(page).wait_for(state="detached")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Start test call"
    page.get_by_role("button", name="Open keys").click()
    sheet(page).wait_for()

    openai = row(page, "openai")
    openai.locator("input").fill(FAKE["openai"])
    openai.get_by_role("button", name="Test", exact=True).click()
    openai.get_by_text("Testing…").wait_for()
    save_shot(sheet(page), name("sheet-testing"))
    assert api.answer({"ok": False, "ms": 380, "error": "invalid key"}) == "openai"
    openai.get_by_text("Key rejected").wait_for()
    save_shot(sheet(page), name("sheet-rejected"))

    openai.get_by_role("button", name="Test", exact=True).click()
    api.answer({"ok": True, "ms": 412})
    openai.get_by_text("Key verified").wait_for()
    openai.get_by_role("button", name="Show key").click()
    save_shot(sheet(page), name("sheet-verified"))
    openai.locator("input").press("Enter")
    openai.get_by_text("••••c3k8").wait_for()

    eleven = row(page, "elevenlabs")
    eleven.locator("input").fill(FAKE["elevenlabs"])
    eleven.get_by_role("button", name="Test", exact=True).click()
    assert api.answer({"ok": True, "ms": 230, "limited": True}) == "elevenlabs"
    eleven.get_by_text("Key valid (limited permissions)").wait_for()
    eleven.get_by_role("button", name="Save").click()
    eleven.get_by_text("••••9q2z").wait_for()
    assert_fits(page)
    save_shot(sheet(page), name("sheet-saved-no-jev"))
    assert page.evaluate("localStorage.getItem('agent-studio:openai-key')") == FAKE["openai"]
    assert FAKE["openai"] not in page.content()

    page.keyboard.press("Escape")
    sheet(page).wait_for(state="detached")
    assert "no JEV key" in keys_button(page).get_attribute("aria-label")
    save_shot(page.locator("header"), name("topbar-amber"))
    page.get_by_role("button", name="Start test call").click()
    page.wait_for_selector("text=JEV has no key")
    save_shot(page.locator(".w-\\[384px\\]"), name("call-jev-confirm"))
    page.get_by_role("button", name="Add key").click()
    assert page.evaluate("document.activeElement.id") == "call-chooser-key"
    page.locator("#call-chooser-key").fill(FAKE["jev"])
    page.locator("#call-chooser-key").press("Enter")
    page.wait_for_selector("text=••••m4n6")
    assert "all set" in keys_button(page).get_attribute("aria-label")
    save_shot(page.locator("header"), name("topbar-green"))
    page.screenshot(path=str(OUT / f"{name('ready-page')}.png"))
    page.context.close()

    # The server has every key: quiet rows, green dot.
    page, _ = page_with(browser, theme, errors, ALL_SERVER)
    open_agent(page)
    keys_button(page).click()
    sheet(page).wait_for()
    assert page.evaluate("document.activeElement.getAttribute('role')") == "dialog"
    save_shot(sheet(page), name("sheet-all-server"))
    page.context.close()

    # Mixed: the server has OpenAI and JEV, this browser has ElevenLabs and its own OpenAI key.
    page, _ = page_with(browser, theme, errors, {"openai": True, "elevenlabs": False, "jev": True},
                        {"elevenlabs": FAKE["elevenlabs"], "openai": FAKE["openai"]})
    page.set_viewport_size(NARROW)
    open_agent(page)
    keys_button(page).click()
    sheet(page).wait_for()
    row(page, "jev").get_by_role("button", name="Use my own key").click()
    page.wait_for_selector("text=Use your own Command Code API key")
    assert_fits(page)
    save_shot(sheet(page), name("sheet-mixed"))
    page.screenshot(path=str(OUT / f"{name('sheet-mixed-page')}.png"))
    page.keyboard.press("Escape")

    # The inline rows under the Disambiguator: JEV on the server key, OpenAI in this browser, locked in a call.
    open_call_panel(page)
    page.wait_for_selector("text=Using the server’s JEV key")
    save_shot(page.locator(".w-\\[384px\\]"), name("inline-jev-server"))
    page.get_by_role("radiogroup", name="Disambiguator").get_by_role("radio", name="OpenAI").click()
    page.wait_for_selector("text=Your OpenAI key, in this browser")
    page.evaluate(SIMULATE_LIVE)
    page.wait_for_selector("text=Locked during the call")
    assert page.get_by_role("button", name="Remove").is_disabled()
    save_shot(page.locator(".w-\\[384px\\]"), name("inline-openai-locked"))
    page.context.close()

    # Agent settings: the same row, and the keys button in the header.
    page, _ = page_with(browser, theme, errors, NO_SERVER, {"openai": FAKE["openai"], "elevenlabs": FAKE["elevenlabs"]})
    open_agent(page)
    page.get_by_role("button", name="Agent settings", exact=True).click()
    page.locator("#resolver-chooser").wait_for()
    page.locator("#resolver-chooser").evaluate("(el) => el.scrollIntoView({ block: 'start' })")
    page.wait_for_selector("#resolver-chooser-key")
    assert_fits(page)
    save_shot(page.locator(".w-\\[384px\\]"), name("settings-inline-entry"))
    page.locator(".w-\\[384px\\]").get_by_role("button", name=re.compile("^API keys")).click()
    sheet(page).wait_for()
    assert page.evaluate("document.activeElement.id") == "keys-jev-key"
    page.context.close()


if __name__ == "__main__":
    run(capture)
