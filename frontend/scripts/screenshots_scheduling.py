"""Screenshots of the Phase 2 scheduling UI (clinic-scheduler agent) in both themes.

Never starts a test call: the Decisions shot feeds resolver_decision payloads (shaped like
backend/agent_tools/scheduling_tools.py `_decision_event`) through parseFlowEvent into the call store.

    python frontend/scripts/screenshots_scheduling.py [base_url]
"""

from playwright.sync_api import Browser

from shots import OUT, app_script, base_url, new_page, run, shot

BASE = base_url("http://localhost:5174")

SIMULATED_DECISIONS = app_script("""
  const { useCall, parseFlowEvent } = await import(loaded('/src/store/call.ts'))
  const call = useCall.getState()
  call.begin(); call.connected()
  const feed = (raw) => { const e = parseFlowEvent(raw); if (e) useCall.getState().ingest(e) }
  feed({ type: 'node_entered', node: 'greeting', state: {} })
  feed({ type: 'edge_taken', function: 'start', from: 'greeting', to: 'schedule', args: { summary: 'heart checkup with Dr. Chen' } })
  feed({ type: 'node_entered', node: 'schedule', state: { summary: 'heart checkup with Dr. Chen' } })
  feed({ type: 'resolver_decision', status: 'ask', say: 'Do you mean Dr. David Chen or Dr. Emily Chen?',
         candidates: { field: 'provider', options: ['Dr. David Chen', 'Dr. Emily Chen'] },
         model: { used: true, provider: 'jev', p: 0.54, ms: 610 }, tokens: { result: 61 } })
  feed({ type: 'resolver_decision', status: 'refuse', say: 'A knee MRI is only for established patients and needs a referral, so I cannot book it for a new patient.',
         reason: 'needs_referral', model: { used: false }, tokens: { result: 58 } })
  feed({ type: 'resolver_decision', status: 'offer', say: 'Dr. Emily Chen has Tuesday at nine at Downtown, Wednesday at two at Northside, or Friday at ten at Downtown. Which works best?',
         offers: ['1 Tue 09:00 Downtown Dr. Emily Chen', '2 Wed 14:00 Northside Dr. Emily Chen', '3 Fri 10:00 Downtown Dr. Emily Chen'],
         model: { used: true, provider: 'jev', p: 0.92, ms: 540 }, tokens: { result: 94 } })
  feed({ type: 'resolver_decision', status: 'confirm', say: 'That is a new patient visit with Dr. Emily Chen, Tuesday at nine at Downtown. Shall I book it?',
         model: { used: false }, tokens: { result: 73 } })
  const t0 = Date.now() - 75000
  useCall.setState((s) => ({ startedAt: t0, decisions: s.decisions.map((d, i) => ({ ...d, at: t0 + 9000 + i * 16000 })) }))""")


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    page = new_page(browser, theme, errors)

    def snap(name: str) -> None:
        shot(page, f"20-{name}-{theme}")

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.get_by_role("navigation", name="Agents").get_by_text("Clinic Scheduler").click()
    page.wait_for_selector(".react-flow__node:has-text('handoff')", timeout=10000)
    page.wait_for_timeout(600)
    snap("scheduler-canvas")

    sched = page.locator(".react-flow__node").filter(has_text="update_request").first
    for _ in range(3):
        page.locator(".react-flow__controls-zoomin").click()
    page.wait_for_timeout(500)
    sched.evaluate("(el) => el.scrollIntoView()")
    box = sched.bounding_box()
    path = OUT / f"20-node-card-{theme}.png"
    page.screenshot(path=str(path), clip={"x": box["x"] - 24, "y": box["y"] - 24, "width": box["width"] + 48, "height": box["height"] + 48})
    print(path)

    page.locator(".react-flow__node").filter(has_text="update_request").first.click()
    page.wait_for_selector("text=Tools")
    page.locator("#node-context").scroll_into_view_if_needed()
    snap("node-tools")

    page.get_by_role("button", name="Agent settings").click()
    page.wait_for_selector("text=Scheduling")
    page.locator("#resolver-timeout").scroll_into_view_if_needed()
    snap("agent-scheduling")

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")
    page.evaluate(SIMULATED_DECISIONS)
    page.get_by_role("tab", name="Decisions").click()
    page.wait_for_timeout(600)
    snap("call-decisions")
    page.evaluate("() => document.querySelector('ol').parentElement.scrollTo({ top: 0 })")
    snap("call-decisions-top")

    page.context.close()


if __name__ == "__main__":
    run(capture)
