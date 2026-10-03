"""Screenshots of Dev view (live pipeline telemetry) during a simulated call, in both themes.

Never starts a test call: the call store is driven with flow events and the telemetry store is
fed the recorded RTVI sequence from src/lib/telemetry.fixture.ts through the real adapter.

    python frontend/scripts/screenshots_devview.py [base_url]
"""

from playwright.sync_api import Browser

from shots import app_script, base_url, new_page, run, shot

BASE = base_url("http://localhost:5174")

SIMULATE = app_script("""
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
         offers: ['1 Tue 09:00 Downtown Dr. Emily Chen'], model: { used: true, provider: 'jev', p: 0.92, ms: 540 },
         tokens: { result: 77 } })
  const stop = until ?? 13950
  useTelemetry.getState().dispatch(replay(Date.now() - Math.min(stop, 13950) - 50, stop))""", params="until")


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    page = new_page(browser, theme, errors, dev_view=False)

    def snap(name: str) -> None:
        shot(page, f"30-devview-{name}-{theme}", 900)

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.get_by_role("navigation", name="Agents").get_by_text("National Scheduler").click()
    page.wait_for_selector(".react-flow__node:has-text('handoff')", timeout=10000)
    page.wait_for_timeout(500)

    page.keyboard.press("d")
    page.wait_for_selector("role=group[name='Voice pipeline']")
    assert page.get_by_role("button", name="Dev view").get_attribute("aria-pressed") == "true"
    snap("idle")

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")

    page.evaluate(SIMULATE, 12790)
    page.get_by_role("tab", name="Transcript").click()
    snap("streaming-transcript")

    page.evaluate(SIMULATE, None)
    page.get_by_role("tab", name="Latency").click()
    snap("latency")

    page.evaluate(SIMULATE, None)
    page.get_by_role("tab", name="Cost").click()
    snap("cost")

    page.get_by_role("button", name="Prices").click()
    page.wait_for_selector("text=Unit prices")
    snap("prices")
    page.keyboard.press("Escape")

    page.context.close()


if __name__ == "__main__":
    run(capture)
