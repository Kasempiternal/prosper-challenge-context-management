"""Screenshots of the Phase 2 scheduling UI (clinic-scheduler agent) in both themes.

Never starts a test call: the Decisions shot feeds resolver_decision payloads (shaped like
backend/agent_tools/scheduling_tools.py `_decision_event`) through parseFlowEvent into the call store.

    python frontend/scripts/screenshots_scheduling.py [base_url]
"""

import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5174"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
BRAVE = r"C:\Users\izotz\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"

SIMULATED_DECISIONS = """async () => {
  const url = performance.getEntriesByType('resource').map((e) => e.name).findLast((n) => n.includes('/src/store/call.ts'))
  const { useCall, parseFlowEvent } = await import(url ?? '/src/store/call.ts')
  const call = useCall.getState()
  call.begin(); call.connected()
  const feed = (raw) => { const e = parseFlowEvent(raw); if (e) useCall.getState().ingest(e) }
  feed({ type: 'node_entered', node: 'greeting', state: {} })
  feed({ type: 'edge_taken', function: 'start', from: 'greeting', to: 'schedule', args: { summary: 'heart checkup with Dr. Chen' } })
  feed({ type: 'node_entered', node: 'schedule', state: { summary: 'heart checkup with Dr. Chen' } })
  feed({ type: 'resolver_decision', status: 'ask', say: 'Do you mean Dr. David Chen or Dr. Emily Chen?',
         candidates: { field: 'provider', options: ['Dr. David Chen', 'Dr. Emily Chen'] }, jev: { used: true, p: 0.54, ms: 610 }, tokens: { result: 61 } })
  feed({ type: 'resolver_decision', status: 'refuse', say: 'A knee MRI is only for established patients and needs a referral, so I cannot book it for a new patient.',
         reason: 'needs_referral', jev: { used: false }, tokens: { result: 58 } })
  feed({ type: 'resolver_decision', status: 'offer', say: 'Dr. Emily Chen has Tuesday at nine at Downtown, Wednesday at two at Northside, or Friday at ten at Downtown. Which works best?',
         offers: ['1 Tue 09:00 Downtown Dr. Emily Chen', '2 Wed 14:00 Northside Dr. Emily Chen', '3 Fri 10:00 Downtown Dr. Emily Chen'],
         jev: { used: true, p: 0.92, ms: 540 }, tokens: { result: 94 } })
  feed({ type: 'resolver_decision', status: 'confirm', say: 'That is a new patient visit with Dr. Emily Chen, Tuesday at nine at Downtown. Shall I book it?',
         jev: { used: false }, tokens: { result: 73 } })
  const t0 = Date.now() - 75000
  useCall.setState((s) => ({ startedAt: t0, decisions: s.decisions.map((d, i) => ({ ...d, at: t0 + 9000 + i * 16000 })) }))
}"""


def shot(page: Page, name: str, theme: str) -> None:
    page.wait_for_timeout(800)
    path = OUT / f"20-{name}-{theme}.png"
    page.screenshot(path=str(path))
    print(path)


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    context = browser.new_context(viewport={"width": 1600, "height": 960}, device_scale_factor=1)
    context.add_init_script(f"localStorage.setItem('agent-studio:theme', '{theme}')")
    page = context.new_page()
    page.on("console", lambda m: m.type == "error" and errors.append(f"[{theme}] {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"[{theme}] {e}"))

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.get_by_role("navigation", name="Agents").get_by_text("Clinic Scheduler").click()
    page.wait_for_selector(".react-flow__node:has-text('handoff')", timeout=10000)
    page.wait_for_timeout(600)
    shot(page, "scheduler-canvas", theme)

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
    shot(page, "node-tools", theme)

    page.get_by_role("button", name="Agent settings").click()
    page.wait_for_selector("text=Scheduling")
    page.locator("#resolver-jev-timeout").scroll_into_view_if_needed()
    shot(page, "agent-scheduling", theme)

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")
    page.evaluate(SIMULATED_DECISIONS)
    page.get_by_role("tab", name="Decisions").click()
    page.wait_for_timeout(600)
    shot(page, "call-decisions", theme)
    page.evaluate("() => document.querySelector('ol').parentElement.scrollTo({ top: 0 })")
    shot(page, "call-decisions-top", theme)

    context.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = p.chromium.launch(executable_path=BRAVE)
        for theme in ("light", "dark"):
            capture(browser, theme, errors)
        browser.close()
    if errors:
        print("Console errors:")
        for e in errors:
            print(" ", e)


if __name__ == "__main__":
    main()
