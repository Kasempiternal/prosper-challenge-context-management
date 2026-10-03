"""Capture review screenshots of Agent Studio against the running dev server, in both themes.

Never starts a test call: the call button is never clicked while enabled, and the live-call
shot feeds contract events straight into the call store. Edits stay in the browser draft
(nothing is saved).

    python frontend/scripts/screenshots.py [base_url]
"""

from playwright.sync_api import Browser, Page

from shots import app_script, base_url, new_page, run, shot

BASE = base_url("http://localhost:5173")

SIMULATED_CALL = app_script("""
  const { useCall } = await import(loaded('/src/store/call.ts'))
  const call = useCall.getState()
  call.begin(); call.connected()
  call.ingest({ type: 'node_entered', node: 'greeting', state: {} })
  call.ingest({ type: 'edge_taken', function: 'choose_intent', from: 'greeting', to: 'collect_details', args: { intent: 'book' } })
  call.ingest({ type: 'node_entered', node: 'collect_details', state: { intent: 'book' } })""")


def node(page: Page, name: str):
    return page.locator(".react-flow__node").filter(has_text=name).first


def capture(browser: Browser, theme: str, errors: list[str]) -> None:
    page = new_page(browser, theme, errors)

    def snap(name: str) -> None:
        shot(page, f"{name}-{theme}", 700)

    page.goto(BASE)
    page.wait_for_selector(".react-flow__node", timeout=15000)
    page.wait_for_selector("text=Valid", timeout=10000)
    snap("01-canvas")

    node(page, "collect_details").click()
    page.wait_for_selector("text=Instructions")
    snap("02-node-inspector")

    page.locator(".react-flow__edgelabel-renderer button", has_text="choose_intent").click()
    page.wait_for_selector("text=Collected fields")
    snap("03-edge-inspector")

    page.get_by_role("button", name="Agent settings").click()
    page.wait_for_selector("text=Persona")
    snap("04-agent-settings")

    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=Ready to test")
    snap("05-test-call-valid")

    # Break the draft: an invalid function name and an empty instruction.
    page.locator(".react-flow__edgelabel-renderer button", has_text="choose_intent").click()
    page.get_by_label("Function name", exact=True).fill("choose intent!")
    node(page, "offer_times").click()
    page.get_by_label("Instruction 1").fill("")
    page.wait_for_selector("text=/\\d+ issues?/", timeout=10000)
    page.get_by_role("button", name="validation issues").click()
    snap("06-validation-issues")

    page.keyboard.press("Escape")
    page.get_by_role("button", name="Test call").click()
    page.wait_for_selector("text=/Fix \\d+ issues? to start a call/")
    assert page.get_by_role("button", name="Start test call").is_disabled()
    snap("07-test-call-blocked")

    page.evaluate(SIMULATED_CALL)
    page.wait_for_timeout(900)
    snap("08-live-call-simulated")

    page.get_by_role("button", name="Theme:").click()
    page.wait_for_selector("role=menuitemradio")
    snap("09-theme-menu")

    page.context.close()


if __name__ == "__main__":
    run(capture)
