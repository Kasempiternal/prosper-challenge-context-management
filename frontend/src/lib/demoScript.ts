export type DemoAgent = 'clinic-scheduler' | 'national-scheduler'

export interface DemoStep {
  /** The words to say to the agent, as written; the demo works with these. */
  say: string
  /** What the agent should answer. */
  expect: string
}

export interface DemoBeat {
  id: number
  title: string
  agent: DemoAgent
  /** Anything to do before the first line: a new call, a switch to flip. */
  setup?: string
  steps: DemoStep[]
  /** What this shows, for the walk-through. */
  why: string
}

export const AGENT_NAMES: Record<DemoAgent, string> = {
  'clinic-scheduler': 'Clinic Scheduler',
  'national-scheduler': 'National Scheduler',
}

/** The nine beats of the README demo script, checked offline against the real resolver. */
export const DEMO_BEATS: DemoBeat[] = [
  {
    id: 1,
    title: 'New patient with a referral',
    agent: 'clinic-scheduler',
    why: 'Policy removes the Dr. Chen who takes no new patients, so no question is needed.',
    steps: [
      {
        say: "I'm a new patient and I have a referral. I need a cardiology consultation with Dr. Chen, soonest you have.",
        expect: "Offers Dr. Emily Chen's times with no question.",
      },
      { say: 'The first one.', expect: 'Reads the booking back and asks to book it.' },
      { say: 'Yes, please.', expect: 'Confirms with a reference number.' },
    ],
  },
  {
    id: 2,
    title: 'Same request, established patient',
    agent: 'clinic-scheduler',
    setup: 'New call.',
    why: 'Both Chens are valid now, so it asks. "The lady one" is confirmed by name, never booked on its own.',
    steps: [
      {
        say: 'I have been a patient there for years and I have a referral. Cardiology consultation with Dr. Chen, soonest.',
        expect: 'Asks: Dr. David Chen or Dr. Emily Chen?',
      },
      {
        say: 'The lady one.',
        expect: 'With JEV: asks "Do you mean Dr. Emily Chen?". With the Disambiguator Off: asks between both again.',
      },
      { say: 'Yes.', expect: "Offers Dr. Emily Chen's times." },
    ],
  },
  {
    id: 3,
    title: 'Street level',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'A street and a house number decide the clinic. "Avenue" is not taken as The Avenues Family Clinic.',
    steps: [
      {
        say: 'I need a sick visit at the clinic on Market Street in San Jose.',
        expect: 'Asks: Downtown at 1812 Market or Willow Glen at 3330 Market?',
      },
      { say: 'Thirty-three thirty.', expect: 'Offers times at Willow Glen.' },
      {
        say: 'I need a sick visit at the one on Lincoln Avenue in Salt Lake.',
        expect: 'Offers times at Sugar House (4821 Lincoln Ave). Start a new call for this line.',
      },
    ],
  },
  {
    id: 4,
    title: 'A place heard by sound',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'A place that sounds like another is confirmed first, and nothing is offered far away without saying so.',
    steps: [
      { say: "I need a flu shot, I'm in Trenton.", expect: 'Asks: Did you mean Renton, Washington?' },
      {
        say: 'No, Trenton, New Jersey.',
        expect: 'Says the nearest clinic is Cherry Hill, near Philadelphia, and asks before looking there.',
      },
    ],
  },
  {
    id: 5,
    title: 'The switch: JEV against Off',
    agent: 'clinic-scheduler',
    setup: 'New call with the Disambiguator on JEV (Test call panel). Then end the call, set it to Off, and call again.',
    why: 'The same words, with and without a model. Off is the rules path.',
    steps: [
      {
        say: 'Something for my back pain.',
        expect: 'JEV: "Do you have a referral for an orthopedic consultation?". Off: "What\'s the visit for?".',
      },
    ],
  },
  {
    id: 6,
    title: 'Consent guard',
    agent: 'clinic-scheduler',
    setup: 'New call. Get as far as an offer (beat 1), but do not pick a time.',
    why: 'Booking only happens after the caller picks a time and says yes. The tool refuses otherwise.',
    steps: [{ say: 'Sure, book it.', expect: 'Does not book. Asks which time first.' }],
  },
  {
    id: 7,
    title: 'Nothing nearby',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'An honest refusal with the nearest valid option, asked before looking there.',
    steps: [
      {
        say: 'I need a dental cleaning, I live in Maine.',
        expect: 'There is none in Maine; names the nearest Boston site and asks before looking there.',
      },
    ],
  },
  {
    id: 8,
    title: 'Questions first',
    agent: 'clinic-scheduler',
    setup: 'New call. Say each as the first words of a call.',
    why: 'Answers come from catalog facts (lookup), then the agent offers to book. It never guesses.',
    steps: [
      { say: 'What are the hours at the Mission Bay clinic?', expect: 'Monday to Friday, 8 to 5.' },
      { say: 'Does anyone at Mission Bay speak Spanish?', expect: 'Names the two doctors there who do.' },
      { say: 'Do I need a referral for an MRI?', expect: 'Yes, and only for established patients.' },
    ],
  },
  {
    id: 9,
    title: 'The model cannot answer for the caller',
    agent: 'national-scheduler',
    setup: 'New call. Keep the Decisions tab open.',
    why: "The conversation model tried to answer for the caller; code kept the caller's own words.",
    steps: [
      { say: 'A flu shot in Washington.', expect: 'Asks: Is that Seattle, Washington or Washington, DC?' },
      {
        say: 'Washington.',
        expect: "Asks again, with a ZIP code as a way out. Decisions tab: \"kept the caller's words: model sent Washington, DC\".",
      },
    ],
  },
]
