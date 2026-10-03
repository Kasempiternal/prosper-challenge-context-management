"""Screenshots of the Disambiguator switch (Test call panel and agent settings), in both themes.

Never starts a test call: a live call is simulated by driving the call and telemetry stores with
the backend's resolver_mode / model_call messages through the real RTVI adapter.

    python frontend/scripts/screenshots_chooser.py [base_url]
"""

from playwright.sync_api import Browser

from shots import app_script, base_url, new_page, run, shot

BASE = base_url("http://localhost:5174")

SIMULATE_LIVE = app_script("""
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
         offers: ['1 Tue 09:00 Midtown Dr. Ana Ruiz'], model: { used: true, provider: 'openai', p: 1, ms: 640 }, tokens: { result: 81 } })""")

END_CALL = app_script("""
  const { useCall } = await import(loaded('/src/store/call.ts'))
  useCall.getState().end()""")


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    page = new_page(browser, theme, errors, dev_view=False)

    def snap(name: str) -> None:
        shot(page, f"40-chooser-{name}-{theme}", 700)

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.get_by_role("navigation", name="Agents").get_by_text("National Scheduler").click()
    page.wait_for_selector(".react-flow__node:has-text('handoff')", timeout=10000)
    page.wait_for_timeout(500)

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")
    group = page.get_by_role("radiogroup", name="Disambiguator")
    assert group.get_by_role("radio", name="JEV").get_attribute("aria-checked") == "true"
    snap("idle-jev")

    group.get_by_role("radio", name="OpenAI").click()
    assert group.get_by_role("radio", name="OpenAI").get_attribute("aria-checked") == "true"
    snap("idle-openai")

    page.keyboard.press("d")
    page.wait_for_selector("role=group[name='Voice pipeline']")
    page.evaluate(SIMULATE_LIVE)
    page.wait_for_selector("text=This call: OpenAI")
    assert group.get_by_role("radio", name="Embeddings").is_disabled()
    page.get_by_role("tab", name="Decisions").click()
    snap("live-openai")

    page.evaluate(END_CALL)
    page.get_by_role("button", name="Agent settings").click()
    settings = page.get_by_role("radiogroup", name="Disambiguator").last
    settings.get_by_role("radio", name="Embeddings").click()
    assert page.locator("#resolver-timeout").is_disabled()
    page.locator("#resolver-timeout").evaluate("(el) => el.scrollIntoView({ block: 'center' })")
    snap("settings-embed")

    page.context.close()


if __name__ == "__main__":
    run(capture)
