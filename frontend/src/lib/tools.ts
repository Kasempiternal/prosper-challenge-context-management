// One line per tool in backend/agent_tools/registry.py (plus hold_slot, which agents wire as a guarded edge).
export const TOOL_DESCRIPTIONS: Record<string, string> = {
  update_request: 'Merges what the caller said into the request; returns the next offer, question or read-back.',
  lookup: 'Answers questions about locations, doctors and services from the catalog.',
  book_offer: 'Books the appointment the caller confirmed. Takes no arguments.',
  hold_slot: 'Holds the confirmed slot so nobody else can take it while booking.',
}

export const CONTEXT_STRATEGIES = [
  { value: 'append', label: 'Keep history', hint: 'The conversation so far carries into this node.' },
  { value: 'reset', label: 'Reset on entry', hint: 'Starts from a clean context; only the instructions and state summary carry over.' },
] as const
