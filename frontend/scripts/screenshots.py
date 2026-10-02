"""Capture review screenshots of Agent Studio against the running dev server, in both themes.

Never starts a test call: the call button is never clicked while enabled, and the live-call
shot feeds contract events straight into the call store. Edits stay in the browser draft
(nothing is saved).

    python frontend/scripts/screenshots.py [base_url]
"""

import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
THEMES = ("light", "dark")

# Import the exact module URL the app loaded (after HMR it carries a ?t= query), so the
# store instance is the app's own.
SIMULATED_CALL = """async () => {
  const url = performance.getEntriesByType('resource').map((e) => e.name).findLast((n) => n.includes('/src/store/call.ts'))
  const { useCall } = await import(url ?? '/src/store/call.ts')
  const call = useCall.getState()
  call.begin(); call.connected()
  call.ingest({ type: 'node_entered', node: 'greeting', state: {} })
  call.ingest({ type: 'edge_taken', function: 'choose_intent', from: 'greeting', to: 'collect_details', args: { intent: 'book' } })
  call.ingest({ type: 'node_entered', node: 'collect_details', state: { intent: 'book' } })
}"""


def shot(page: Page, name: str, theme: str) -> None:
    page.wait_for_timeout(700)
    path = OUT / f"{name}-{theme}.png"
    page.screenshot(path=str(path))
    print(path)


def node(page: Page, name: str):
    return page.locator(".react-flow__node").filter(has_text=name).first


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    context = browser.new_context(viewport={"width": 1600, "height": 960}, device_scale_factor=1)
    context.add_init_script(f"localStorage.setItem('agent-studio:theme', '{theme}')")
    page = context.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(f"[{theme}] {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"[{theme}] {e}"))

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.wait_for_selector("text=Valid", timeout=10000)
    shot(page, "01-canvas", theme)

    node(page, "collect_details").click()
    page.wait_for_selector("text=Instructions")
    shot(page, "02-node-inspector", theme)

    page.locator(".react-flow__edgelabel-renderer button", has_text="choose_intent").click()
    page.wait_for_selector("text=Collected fields")
    shot(page, "03-edge-inspector", theme)

    page.get_by_role("button", name="Agent settings").click()
    page.wait_for_selector("text=Persona")
    shot(page, "04-agent-settings", theme)

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")
    shot(page, "05-test-call-valid", theme)

    # Break the draft: an invalid function name and an empty instruction.
    page.locator(".react-flow__edgelabel-renderer button", has_text="choose_intent").click()
    page.get_by_label("Function name", exact=True).fill("choose intent!")
    node(page, "offer_times").click()
    page.get_by_label("Instruction 1").fill("")
    page.wait_for_selector("text=/\\d+ issues?/", timeout=10000)
    page.get_by_role("button", name="validation issues").click()
    shot(page, "06-validation-issues", theme)

    page.keyboard.press("Escape")
    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=/Fix \\d+ issues? to start a call/")
    assert page.get_by_role("button", name="Start test call").is_disabled()
    shot(page, "07-test-call-blocked", theme)

    page.evaluate(SIMULATED_CALL)
    page.wait_for_timeout(900)
    shot(page, "08-live-call-simulated", theme)

    page.get_by_role("button", name="Theme:").click()
    page.wait_for_selector("role=menuitemradio")
    shot(page, "09-theme-menu", theme)

    context.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in THEMES:
            capture(browser, theme, errors)
        browser.close()
    if errors:
        print("Console errors:")
        for e in errors:
            print(" ", e)


if __name__ == "__main__":
    main()
