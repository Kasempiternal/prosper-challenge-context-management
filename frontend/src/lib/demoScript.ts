export type DemoAgent = 'clinic-scheduler' | 'national-scheduler'

export interface DemoStep {
  /** The words to say to the agent, as written; the demo works with these. */
  say: string
  /** What the agent should answer. */
  expect: string
  /** Only say this when the agent did this ("it asks which city"): the agent can take more than one good path. */
  when?: string
}

export type DemoGroup = 'demo' | 'real'

export interface DemoBeat {
  id: number
  group: DemoGroup
  title: string
  agent: DemoAgent
  /** Anything to do before the first line: a new call, a switch to flip. */
  setup?: string
  steps: DemoStep[]
  /** What would make this beat a failure. */
  failIf?: string
  /** What this shows, for the walk-through. */
  why: string
}

export const AGENT_NAMES: Record<DemoAgent, string> = {
  'clinic-scheduler': 'Clinic Scheduler',
  'national-scheduler': 'National Scheduler',
}

export const DEMO_GROUPS: { key: DemoGroup; label: string; hint: string }[] = [
  {
    key: 'demo',
    label: 'Demo beats',
    hint: 'The nine rehearsed beats, each a full call checked against the real agent. Names and times can differ if an earlier call booked that slot.',
  },
  {
    key: 'real',
    label: 'Real callers',
    hint: 'Messy, natural calls. The agent can take more than one good path: follow the "If" lines and judge by "Fail if", not by exact words.',
  },
]

/** Every line checked end to end against the real agent (gpt-4o, JEV, the shipped tools) in text. */
export const DEMO_BEATS: DemoBeat[] = [
  {
    id: 1,
    group: 'demo',
    title: 'New patient with a referral',
    agent: 'clinic-scheduler',
    setup: 'New call.',
    why: 'Policy removes the Dr. Chen who takes no new patients, so no question is needed.',
    steps: [
      {
        say: "I'm a new patient and I have a referral. I need a cardiology consultation with Dr. Chen, soonest you have.",
        expect: "Offers Dr. Emily Chen's times with no question: tomorrow at 8 at Downtown, Friday at 8 at Richmond, or Monday at 8 at Downtown.",
      },
      { say: 'The first one.', expect: '"Okay, a cardiology consultation with Dr. Emily Chen, tomorrow at 8 at Downtown. Shall I book it?"' },
      { say: 'Yes, please.', expect: '"You\'re all booked. Your confirmation is H…" (four digits), then asks if there is anything else.' },
      { say: "No, that's all, thanks.", expect: 'Says goodbye. The call ends.' },
    ],
    failIf: 'it asks which Dr. Chen (David Chen takes no new patients), or books before your yes.',
  },
  {
    id: 2,
    group: 'demo',
    title: 'Same request, established patient',
    agent: 'clinic-scheduler',
    setup: 'New call.',
    why: 'Both Chens are valid now, so it asks. "The lady one" is confirmed by name, never booked on its own.',
    steps: [
      {
        say: 'I have been a patient there for years and I have a referral. Cardiology consultation with Dr. Chen, soonest.',
        expect: '"Do you mean Dr. David Chen or Dr. Emily Chen?"',
      },
      {
        say: 'The lady one.',
        expect: '"One moment." then "Do you mean Dr. Emily Chen?" With the Disambiguator Off, it asks between both again.',
      },
      { say: 'Yes.', expect: "Offers Dr. Emily Chen's times (tomorrow at 8:40 at Downtown, Friday at 8 at Richmond, Monday at 8)." },
      { say: 'The first one.', expect: 'Reads the booking back and asks "Shall I book it?".' },
      { say: 'Yes.', expect: 'Booked, with a confirmation reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it books a Chen without asking, or takes "the lady one" as Dr. Emily Chen without "Do you mean Dr. Emily Chen?".',
  },
  {
    id: 3,
    group: 'demo',
    title: 'Street level',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'A street and a house number decide the clinic. "Avenue" is not taken as The Avenues Family Clinic.',
    steps: [
      {
        say: 'I need a sick visit at the clinic on Market Street in San Jose.',
        expect: '"Is that Downtown at 1812 Market or Willow Glen at 3330 Market?"',
      },
      { say: 'Thirty-three thirty.', expect: 'Offers times at Willow Glen (Dr. Amalia Ren: today at 11, tomorrow at 8, Monday at 8).' },
      { say: 'The first one.', expect: 'Reads back a sick visit at Willow Glen.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: "No, that's all.", expect: 'Says goodbye.' },
      {
        when: 'you want the second street, start a new call',
        say: 'I need a sick visit at the one on Lincoln Avenue in Salt Lake.',
        expect: 'Offers times at Sugar House (4821 Lincoln Ave), for example Dr. Megan Vieira and Dr. Yasmin Siegel.',
      },
      { say: 'The first one.', expect: 'Reads back a sick visit at Sugar House.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
    ],
    failIf: 'it guesses one of the two Market Street clinics, or takes "Avenue" as The Avenues Family Clinic.',
  },
  {
    id: 4,
    group: 'demo',
    title: 'A place heard by sound',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'A place that sounds like another is confirmed first, and nothing far away is offered without saying so.',
    steps: [
      { say: "I need a flu shot, I'm in Trenton.", expect: '"Did you mean Renton, Washington?"' },
      {
        say: 'No, Trenton, New Jersey.',
        expect: '"Our nearest clinic in New Jersey for a flu shot is Cherry Hill, near Philadelphia. Want me to look there?"',
      },
      { say: 'Yes, please.', expect: 'Offers flu shot times at Cherry Hill (for example Dr. Chad Garvey).' },
      { say: 'The first one.', expect: 'Reads back a flu shot at Cherry Hill.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it offers Renton without asking, or repeats the Cherry Hill question after your yes.',
  },
  {
    id: 5,
    group: 'demo',
    title: 'The switch: JEV against Off',
    agent: 'clinic-scheduler',
    setup: 'Two calls. Set the Disambiguator (Test call panel) to JEV for the first and to Off for the second, before you press Call.',
    why: 'The same words, with and without a model. Off is the rules path: no model, so it asks.',
    steps: [
      {
        when: 'the Disambiguator is on JEV',
        say: 'Something for my back pain.',
        expect: '"One moment." then "Do you have a referral for an orthopedic consultation?"',
      },
      { say: 'Yes, I have one.', expect: 'Offers orthopedic consultation times (Dr. Grace Rodriguez at Richmond).' },
      { say: 'The first one.', expect: 'Reads the booking back.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
      {
        when: 'the Disambiguator is Off (new call)',
        say: 'Something for my back pain.',
        expect: '"What\'s the visit for?" The words alone name no visit.',
      },
      { say: 'An orthopedic consultation.', expect: '"Do you have a referral for an orthopedic consultation?"' },
      { say: 'Yes, I have one.', expect: 'Offers the same doctor\'s times.' },
      { say: "No, thanks, I'll call back.", expect: 'Says goodbye.' },
    ],
    failIf: 'Off guesses the visit, or JEV books without the referral question.',
  },
  {
    id: 6,
    group: 'demo',
    title: 'Consent guard',
    agent: 'clinic-scheduler',
    setup: 'New call.',
    why: 'Booking only happens after the caller picks a time and says yes to the read-back.',
    steps: [
      { say: "I'd like a flu shot.", expect: 'Offers flu shot times (Dr. Maria Garcia, Dr. Carlos Garcia).' },
      { say: 'Sure, book it.', expect: 'Does not book. Asks which time you want.' },
      { say: 'The first one.', expect: 'Reads the booking back.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it books at "Sure, book it" before you picked a time.',
  },
  {
    id: 7,
    group: 'demo',
    title: 'Nothing nearby',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'An honest refusal with the nearest valid option, asked before looking there.',
    steps: [
      {
        say: 'I need a dental cleaning, I live in Maine.',
        expect: '"We don\'t offer a dental cleaning in Maine. The nearest is Downtown in Boston. Want me to look there?"',
      },
      { say: 'Yes, Boston is fine.', expect: 'Offers dental cleaning times at Downtown, Boston.' },
      { say: 'The first one.', expect: 'Reads the booking back.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it offers Boston without asking first, or invents a clinic in Maine.',
  },
  {
    id: 8,
    group: 'demo',
    title: 'Questions first',
    agent: 'clinic-scheduler',
    setup: 'New call. Ask the three questions in one call.',
    why: 'Answers come from catalog facts (lookup), then the agent offers to book. A question is never a reason to transfer.',
    steps: [
      { say: 'What are the hours at the Mission Bay clinic?', expect: 'Monday to Friday, 8 to 5, and offers to book there.' },
      {
        say: 'Does anyone at Mission Bay speak Spanish?',
        expect: 'Two doctors: Dr. Olivia Sato (Psychiatry) and Dr. Tomas Hernandez (Internal Medicine).',
      },
      { say: 'Do I need a referral for an MRI?', expect: 'Yes, and only established patients can book it.' },
      { say: "Okay, thanks, that's all.", expect: 'Says goodbye.' },
    ],
    failIf: 'it transfers you for a question, or answers beyond the facts.',
  },
  {
    id: 9,
    group: 'demo',
    title: 'The model cannot answer for the caller',
    agent: 'national-scheduler',
    setup: 'New call. Keep the Decisions tab open.',
    why: "The conversation model tried to answer for the caller; code kept the caller's own words.",
    steps: [
      { say: 'A flu shot in Washington.', expect: '"Is that Seattle, Washington or Washington, DC?"' },
      {
        say: 'Washington.',
        expect:
          '"Sorry, I still need to know which one: Seattle, Washington or Washington, DC? Or tell me your ZIP code." Decisions tab: "kept the caller\'s words: model sent Washington, DC".',
      },
      { say: 'Seattle.', expect: 'Offers flu shot times in Seattle (Capitol Hill, Queen Anne).' },
      { say: 'The first one.', expect: 'Reads the booking back.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it offers anything in Washington, DC after you only said "Washington".',
  },
  {
    id: 10,
    group: 'real',
    title: 'Rigoberto, the picky one',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: '"Any other clinic", "another doctor" and "anything later" each search again; a yes that names another day books nothing.',
    steps: [
      { say: 'Hi, I need a dental cleaning.', expect: '"Which city are you in?"' },
      { say: 'New York.', expect: 'Offers times at Flushing (Dr. Helen Altman, Dr. Sylvia Smith).' },
      { say: 'Is there any other clinic I can book on?', expect: 'Offers times at Jamaica instead. Does not ask the city again.' },
      { say: 'Hmm, do you have another doctor?', expect: 'Offers other doctors at Jamaica (for example Dr. Lamar Powers, Dr. Rosemary Amoroso).' },
      { say: 'None of those times work for me. Anything later?', expect: 'Offers later times (for example 11:45, 8:45).' },
      { say: 'The second one.', expect: 'Reads back tomorrow at 8:45 and asks "Shall I book it?".' },
      { say: "Yes, Friday's perfect.", expect: 'Does not book. Offers Friday times instead.' },
      { say: 'The first one.', expect: 'Reads back a Friday appointment.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: "No, that's all. Thank you.", expect: 'Says goodbye.' },
    ],
    failIf: 'it asks the city again after "any other clinic", repeats the same clinic, doctors or times, or books a day you did not agree to.',
  },
  {
    id: 11,
    group: 'real',
    title: 'Dani, sprained ankle, no medical words',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: '"I don\'t know" gets a suggestion, not a transfer, and not knowing is never taken as a no.',
    steps: [
      {
        say: 'Hello, I sprained my ankle yesterday playing football with my son, and I want to see a doctor.',
        expect: 'Asks which city you are in, or which visit it is.',
      },
      { when: 'it asks which city', say: 'San Francisco.', expect: 'Offers sprain and strain evaluation times, or asks which visit it is.' },
      {
        when: 'it asks "a foot and ankle consultation or a sprain and strain evaluation?"',
        say: "I don't know.",
        expect: '"It sounds like a … , since you said sprained ankle. Shall I go with that?"',
      },
      { when: 'it suggests one', say: 'Yes.', expect: "Offers that visit's times, or asks about a referral." },
      { when: 'it asks whether you have a referral', say: 'Yes, I have one from my GP.', expect: 'Offers times.' },
      { say: 'How long does the visit take?', expect: 'Answers from the catalog (30 or 40 minutes) and keeps the times open.' },
      { say: 'Okay, the first one.', expect: 'Reads the booking back.' },
      { say: 'Yes, please.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks, bye.', expect: 'Says goodbye.' },
    ],
    failIf: 'it transfers you after "I don\'t know", takes "I don\'t know" as "no referral" and refuses, or asks the same two-option question a third time.',
  },
  {
    id: 12,
    group: 'real',
    title: 'Priya, a headache, then her mother',
    agent: 'clinic-scheduler',
    setup: 'New call.',
    why: 'A symptom never turns into a visit nobody chose; an honest "we don\'t offer that"; a second booking for someone else.',
    steps: [
      {
        say: "Hi, um, I've had a headache for three days and I'd like to see someone.",
        expect: 'Asks which visit (a sick visit, a headache follow-up, a neurology consultation), or offers sick visit times.',
      },
      { when: 'it asked which visit', say: 'A sick visit.', expect: 'Offers sick visit times.' },
      { say: 'The first one.', expect: 'Reads the booking back.' },
      { say: 'Yes.', expect: 'Booked, with a reference.' },
      {
        say: "Can you also book an eye exam for my mother? She's never been to your clinics.",
        expect: '"Sorry, we don\'t offer eye care at our clinics." (True: this catalog has no eye doctor.)',
      },
      { say: 'Oh. Then a flu shot for her.', expect: 'Offers flu shot times.' },
      { say: 'The first one.', expect: 'Reads the second booking back.' },
      { say: 'Yes.', expect: 'Booked, with a second reference. Yours still stands.' },
      { say: "No, that's all.", expect: 'Says goodbye.' },
    ],
    failIf: 'it offers a new patient visit for the headache without asking, offers an eye exam, or her booking replaces yours.',
  },
  {
    id: 13,
    group: 'real',
    title: 'Tom, needs a shot and is in a hurry',
    agent: 'clinic-scheduler',
    setup: 'New call.',
    why: 'A vague "shot" gets the likely options; "what did you say?"; no invented answers; a yes that names another doctor books nothing.',
    steps: [
      { say: 'I need a shot.', expect: '"What kind of visit is it: a vaccination, a flu shot, or a COVID-19 vaccine, or something else?"' },
      { say: 'Ugh, the flu one. This is taking forever.', expect: 'Offers flu shot times (Dr. Maria Garcia, Dr. Carlos Garcia on Friday at 8 at North Beach).' },
      { say: 'What did you say? Can you repeat that?', expect: 'Repeats the same times.' },
      { say: 'Is there parking at that clinic?', expect: '"I don\'t have that information", often with the phone number. Never a made-up yes.' },
      { say: 'Fine. The last one.', expect: 'Reads back a flu shot with Dr. Carlos Garcia, Friday at 8 at North Beach.' },
      {
        say: 'Yes, but with Dr. Maria Garcia.',
        expect: 'Does not book Carlos. Asks "Dr. Maria Garcia in pediatrics or Dr. Maria Garcia in family medicine?" (two doctors share the name), or reads her back.',
      },
      { when: 'it reads Dr. Maria Garcia back', say: 'Yes.', expect: 'Booked with Dr. Maria Garcia, with a reference. Skip to the last line.' },
      { when: 'it asks which Maria Garcia', say: 'The family medicine one.', expect: 'Offers her times at Richmond. Never the same question again.' },
      { when: 'it offered her times', say: 'The first one.', expect: 'Reads back a flu shot with Dr. Maria Garcia.' },
      { when: 'it read her back', say: 'Yes.', expect: 'Booked, with a reference.' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it books Carlos Garcia after you named Maria, asks "which Maria Garcia" again after you answered, or says the clinic has parking.',
  },
  {
    id: 14,
    group: 'real',
    title: 'Linda, stuck and losing patience',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'No loops: a question goes unanswered twice at most, then another way out; a request for a person is honoured at once.',
    steps: [
      { say: 'Hello? Is anybody there?', expect: 'Answers that it is there and asks what to book.' },
      {
        say: "Yes, hi. My husband needs to see someone about his back, he can't call himself.",
        expect: 'Asks which city you are in, or what the visit is for.',
      },
      { say: 'Your city.', expect: 'Asks again, for example "Sorry, which location was that?".' },
      { say: 'I already told you, your city!', expect: 'Asks for a nearby city or a ZIP code, or hands you over. Not the same question a third time.' },
      { say: 'Ugh. Can I just talk to a person?', expect: 'Transfers you to the clinic staff. The call ends.' },
    ],
    failIf: 'it asks the same question three times, refuses to transfer, or makes up a city.',
  },
  {
    id: 15,
    group: 'real',
    title: 'Sam, a returning patient with no referral',
    agent: 'national-scheduler',
    setup: 'New call.',
    why: 'Policy in code: a visit that needs a referral is never booked without one, however hard the caller pushes, and the agent still finds what can be booked.',
    steps: [
      {
        say: "Hi, I've been a patient with you for years. I need an MRI of my knee, I'm in Boston.",
        expect: '"Do you have a referral for a knee MRI?" It does not ask whether you have been seen before: you said so.',
      },
      { say: "No, I don't have one.", expect: '"A knee MRI needs a referral first. Once you have one, we can book it."' },
      { say: "Can't you just book it anyway? I'll bring the referral later.", expect: 'Refuses again: it cannot book a knee MRI without a referral.' },
      {
        say: 'Please, my knee really hurts. What can I book for my knee without a referral?',
        expect: 'Offers knee injury evaluation times in Boston (no referral needed), for example Dr. Sara Huddleston at South End.',
      },
      { say: 'The first one.', expect: 'Reads back a knee injury evaluation.' },
      { say: 'Yes.', expect: 'Booked, with a reference. (If that time was just taken, it says so and offers others.)' },
      { say: 'No, thanks.', expect: 'Says goodbye.' },
    ],
    failIf: 'it books the knee MRI at any point, asks again whether you are a returning patient, or offers an MRI elsewhere as if the referral did not matter.',
  },
]
