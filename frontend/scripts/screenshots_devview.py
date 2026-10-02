"""Screenshots of Dev view (live pipeline telemetry) during a simulated call, in both themes.

Never starts a test call: the call store is driven with flow events and the telemetry store is
fed the recorded RTVI sequence from src/lib/telemetry.fixture.ts through the real adapter.

    python frontend/scripts/screenshots_devview.py [base_url]
"""

import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5174"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
BRAVE = r"C:\Users\izotz\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"

# Stores are imported by the exact URL the app loaded (HMR adds ?t=), so they are the app's instances.
SIMULATE = """async (until) => {
  const loaded = (path) => performance.getEntriesByType('resource').map((e) => e.name).findLast((n) => n.includes(path)) ?? path
  const { useCall, parseFlowEvent } = await import(loaded('/src/store/call.ts'))
  const { useTelemetry } = await import(loaded('/src/store/telemetry.ts'))
  const { replay } = await import(loaded('/src/lib/telemetry.fixture.ts'))
  const call = useCall.getState()
  call.begin(); call.connected()
  await new Promise((r) => setTimeout(r, 300))
  const feed = (raw) => { const e = parseFlowEvent(raw); if (e) useCall.getState().ingest(e) }
  feed({ type: 'node_entered', node: 'greeting', state: {} })
  feed({ type: 'edge_taken', function: 'start', from: 'greeting', to: 'schedule', args: {} })
  feed({ type: 'node_entered', node: 'schedule', state: {} })
  feed({ type: 'resolver_decision', status: 'offer', ms: 552, say: 'Dr. Emily Chen has Tuesday at 9. Does that work?',
         offers: ['1 Tue 09:00 Downtown Dr. Emily Chen'], jev: { used: true, p: 0.92, ms: 540 }, tokens: { result: 77 } })
  const stop = until ?? 13950
  useTelemetry.getState().dispatch(replay(Date.now() - Math.min(stop, 13950) - 50, stop))
}"""


def shot(page: Page, name: str, theme: str) -> None:
    page.wait_for_timeout(900)
    path = OUT / f"30-devview-{name}-{theme}.png"
    page.screenshot(path=str(path))
    print(path)


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    context = browser.new_context(viewport={"width": 1600, "height": 960}, device_scale_factor=1)
    context.add_init_script(
        f"localStorage.setItem('agent-studio:theme', '{theme}'); localStorage.setItem('agent-studio:dev-view', '0')"
    )
    page = context.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(f"[{theme}] {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"[{theme}] {e}"))

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.get_by_role("navigation", name="Agents").get_by_text("National Scheduler").click()
    page.wait_for_selector(".react-flow__node:has-text('handoff')", timeout=10000)
    page.wait_for_timeout(500)

    page.keyboard.press("d")
    page.wait_for_selector("role=group[name='Voice pipeline']")
    assert page.get_by_role("button", name="Dev view").get_attribute("aria-pressed") == "true"
    shot(page, "idle", theme)

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")

    page.evaluate(SIMULATE, 12790)
    page.get_by_role("tab", name="Transcript").click()
    shot(page, "streaming-transcript", theme)

    page.evaluate(SIMULATE, None)
    page.get_by_role("tab", name="Latency").click()
    shot(page, "latency", theme)

    page.evaluate(SIMULATE, None)
    page.get_by_role("tab", name="Cost").click()
    shot(page, "cost", theme)

    page.get_by_role("button", name="Prices").click()
    page.wait_for_selector("text=Unit prices")
    shot(page, "prices", theme)
    page.keyboard.press("Escape")

    context.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=BRAVE, headless=True)
        for theme in ("light", "dark"):
            capture(browser, theme, errors)
        browser.close()
    if errors:
        print("Console errors:")
        for e in errors:
            print(" ", e)


if __name__ == "__main__":
    main()
