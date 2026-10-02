"""Screenshots of the Disambiguator switch (Test call panel and agent settings), in both themes.

Never starts a test call: a live call is simulated by driving the call and telemetry stores with
the backend's resolver_mode / model_call messages through the real RTVI adapter.

    python frontend/scripts/screenshots_chooser.py [base_url]
"""

import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5174"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
BRAVE = r"C:\Users\izotz\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"

SIMULATE_LIVE = """async () => {
  const loaded = (path) => performance.getEntriesByType('resource').map((e) => e.name).findLast((n) => n.includes(path)) ?? path
  const { useCall, parseFlowEvent } = await import(loaded('/src/store/call.ts'))
  const { useTelemetry } = await import(loaded('/src/store/telemetry.ts'))
  const { toSignals } = await import(loaded('/src/lib/telemetry.ts'))
  const call = useCall.getState()
  call.begin(); call.connected()
  await new Promise((r) => setTimeout(r, 300))
  const at = Date.now()
  const server = (data) => useTelemetry.getState().dispatch(toSignals('serverMessage', data).map((s) => ({ ...s, at })))
  server({ type: 'resolver_mode', requested: 'openai', active: 'openai' })
  server({ type: 'node_entered', node: 'schedule' })
  useTelemetry.getState().dispatch([{ kind: 'user_stopped', at: at - 900 }])
  server({ type: 'model_call', provider: 'openai', purpose: 'type', ms: 612, input_tokens: 1105, usd: 0.00016635, ok: true, p: 1 })
  const feed = (raw) => { const e = parseFlowEvent(raw); if (e) useCall.getState().ingest(e) }
  feed({ type: 'node_entered', node: 'schedule', state: {} })
  feed({ type: 'resolver_decision', status: 'offer', ms: 640, say: 'For a hearing test, Dr. Ana Ruiz has Tuesday at 9. Does that work?',
         offers: ['1 Tue 09:00 Midtown Dr. Ana Ruiz'], model: { used: true, provider: 'openai', p: 1, ms: 640 }, tokens: { result: 81 } })
}"""


END_CALL = """async () => {
  const loaded = (path) => performance.getEntriesByType('resource').map((e) => e.name).findLast((n) => n.includes(path)) ?? path
  const { useCall } = await import(loaded('/src/store/call.ts'))
  useCall.getState().end()
}"""


def shot(page: Page, name: str, theme: str) -> None:
    page.wait_for_timeout(700)
    path = OUT / f"40-chooser-{name}-{theme}.png"
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

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")
    group = page.get_by_role("radiogroup", name="Disambiguator")
    assert group.get_by_role("radio", name="JEV").get_attribute("aria-checked") == "true"
    shot(page, "idle-jev", theme)

    group.get_by_role("radio", name="OpenAI").click()
    assert group.get_by_role("radio", name="OpenAI").get_attribute("aria-checked") == "true"
    shot(page, "idle-openai", theme)

    page.keyboard.press("d")
    page.wait_for_selector("role=group[name='Voice pipeline']")
    page.evaluate(SIMULATE_LIVE)
    page.wait_for_selector("text=This call: OpenAI")
    assert group.get_by_role("radio", name="Embeddings").is_disabled()
    page.get_by_role("tab", name="Decisions").click()
    shot(page, "live-openai", theme)

    page.evaluate(END_CALL)
    page.get_by_role("button", name="Agent settings").click()
    settings = page.get_by_role("radiogroup", name="Disambiguator").last
    settings.get_by_role("radio", name="Embeddings").click()
    assert page.locator("#resolver-timeout").is_disabled()
    page.locator("#resolver-timeout").evaluate("(el) => el.scrollIntoView({ block: 'center' })")
    shot(page, "settings-embed", theme)

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
