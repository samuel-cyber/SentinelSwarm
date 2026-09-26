# Sentinel Swarm
### *(codename: ProActive)*

**Your codebase has bugs nobody's found yet. We send a swarm to go find them — and prove it.**

Built for the IBM Bob 2.0 Hackathon (lablab.ai, Sep 25–27 2026).

---

## 1. The one-line pitch

Most AI coding tools wait for a human to write a bug report before they help.
Sentinel Swarm doesn't wait. It sends four specialized Bob 2.0 subagents into
a codebase with no ticket, no instructions, no hints — and they hunt down a
real, provable bug, fix it, and hand you the receipts, live, in under two
minutes, on a page anyone can click.

## 2. The problem, plainly

Right now: a bug only gets fixed after someone notices it, writes it up, and
an AI (or a human) fixes what was described. The AI never goes looking on
its own. Research on this (the "Active-SWE" benchmark, 2026) shows current
AI agents are specifically bad at *proactive* bug-finding — they're good
students, bad detectives.

That gap is the whole idea: turn Bob into the detective, not just the
student.

## 3. Why this isn't "just another Qodo/Copilot clone" — the differentiator

Existing AI code-review tools (Qodo, GitHub Copilot, etc.) mostly **suggest**
tests or **flag** possible issues based on static analysis or pattern-
matching. That's a guess dressed up as a finding.

Sentinel Swarm's whole design is built around one rule: **a bug is only
reported if we can prove it, live, by actually running a test that fails
against the real code.** No claim without a receipt. That's the wedge —
we're not smarter at spotting bugs, we're stricter about only shouting
"bug!" when we've watched it break with our own eyes (well, Bob's).

This is also the anti-hallucination safeguard: if the generated test doesn't
compile or doesn't actually fail, the "bug" is silently discarded — the
swarm never gets to lie to the judge.

## 4. The creative twist: make it feel like a bounty hunt, not a scan

Instead of a boring "Run Analysis" spinner, the four subagents are framed
and visualized as a **swarm of hunters converging on a target**:

| Codename | Real job | On-screen persona |
|---|---|---|
| **Scout** | Reads the repo, builds a map of what does what | A radar/map panel lighting up as it "explores" each file |
| **Saboteur** | Thinks up edge cases, writes real test code trying to break things | A crosshair/target icon over the function it's attacking |
| **Medic** | Writes the fix once a test proves something's broken, re-runs the test | A pulse/heartbeat animation while patching |
| **Scribe** | Writes the human-readable bug report + diff | A "case file" card that visually assembles itself |

Each bug found gets a **Fragility Score** (a simple 1–10 severity number
Bob assigns based on how easily the edge case is hit in real usage) and a
**Proof Badge** — a little green checkmark that only appears once the fix
is confirmed by re-running the exact same test that found the bug. The
badge *is* the differentiator, visually: "Proven, not guessed."

Optional extra flourish if time allows: a running **"Bugs Found While You
Read This"** counter/ticker on the landing page before the judge even
clicks the button, seeded with your own pre-run results, just for flavor.

## 5. End-to-end user flow (zero assumed knowledge)

1. Judge (or you, in the demo) opens your hosted link in a browser. No
   install, no login.
2. Page shows a small sample project already loaded (you built and seeded
   this beforehand — a small app with 2–3 bugs planted on purpose) and one
   big button: **"Release the Swarm."**
3. Click it.
4. Four panels animate in real time as each subagent does its job (Scout →
   Saboteur → Medic → Scribe), calling Bob 2.0 behind the scenes for each
   step.
5. A test visibly goes **red** (proof a bug is real) then **green** (proof
   it's fixed).
6. A "case file" card appears: what broke, why, the fix, the Fragility
   Score, the Proof Badge, and a link to the actual code diff.
7. Done — the whole thing takes under two minutes and needs zero further
   interaction from the judge.

## 6. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend / orchestration | Python (Flask) | Plays to strong Python skills; simple to reason about |
| Realtime updates to the page | Flask-SocketIO (WebSocket) | Lets the four panels update live instead of one big "loading" spinner |
| Test execution | `pytest` | Actually runs the AI-written test against real code — this is what makes a "bug" provable, not claimed |
| The "victim" sample app | Small Flask/Python app, written by you | Deliberately simple, seeded with known planted bugs for a reliable live demo |
| Frontend | Plain HTML/CSS/JS | No framework overhead; Claude Code can generate this directly |
| AI reasoning (all 4 subagent steps) | IBM Bob 2.0 (Agent mode + subagents) | This is the mandatory, judged part — every "smart" decision is a real Bob call, not hardcoded logic |
| Hosting | Render or Vercel (free tier) | Gives you the required public Application URL |

## 7. Architecture, in one paragraph

Your Flask backend is the ringmaster: it holds the sample repo, calls Bob
2.0 four times in sequence (once per subagent role, each with a tightly
scoped prompt), actually executes whatever test code Bob writes using
`pytest`, and pushes each step's result to the browser over a WebSocket so
the four panels update live instead of everything appearing at once at the
end. The "proof" step (running pytest for real) is the one piece of pure,
deterministic code in the whole system — everything else is Bob doing the
thinking.

## 8. What to build, in order

### Must-have (this IS the whole demo)
1. **Sample repo** — small Flask app with 2–3 bugs planted on purpose
   (e.g. an unhandled empty-string input, an off-by-one in pagination).
2. **Scout call** — prompt Bob to read the sample repo and summarize what
   each function does. Store the result.
3. **Saboteur call** — prompt Bob, given that summary, to write a real
   `pytest` test targeting a plausible edge case.
4. **Run the test for real** — execute it with `pytest`. If it fails,
   you've proven a bug. If it passes, discard silently and try another
   edge case (or move on — for demo purposes, pre-verify this works
   against your seeded bugs).
5. **Medic call** — prompt Bob with the failing test + the broken function
   to produce a fix. Apply it. Re-run the same test to confirm it now
   passes.
6. **Scribe call** — prompt Bob to summarize the finding in plain English
   for the case-file card.
7. **Frontend** — the button, the four live panels, the case-file card,
   the Fragility Score + Proof Badge.
8. **WebSocket wiring** — push status updates from backend to frontend as
   each step completes.
9. **Deploy** — get it on Render/Vercel with a public URL.

### Nice-to-have (only if MVP is solid with time to spare)
- The "Bugs Found While You Read This" ticker flourish.
- Handling more than one bug per run, shown as multiple case-file cards.
- A little animated "swarm converging" visual instead of static panels.

### Explicitly not building
- Running this against arbitrary/user-submitted repos (too much surface
  area for 48 hours — one polished, pre-seeded sample repo is the demo).
- Auth, multi-user support, saving history across sessions.
- Auto-committing fixes back to a real GitHub repo live (show the diff,
  don't actually push it — keep the blast radius zero).

## 9. Demo script (≤3 min total, ≥90 sec must be the product in action)

- **0:00–0:20** — Cold open on the problem: one line of narration + the
  Active-SWE stat on screen ("AI agents are bad at finding bugs nobody
  reported"). No fluff.
- **0:20–0:35** — Cut to the live site. Click "Release the Swarm."
- **0:35–2:00** — Screen-record the real thing happening: Scout mapping,
  Saboteur writing a test, the test going red, Medic patching, test going
  green, Scribe's case-file card assembling with the Fragility Score and
  Proof Badge. Narrate briefly over it, but let the visuals carry it.
- **2:00–2:30** — One line on how Bob 2.0 specifically made this possible
  (agent mode + subagents, real reasoning at every step, not hardcoded).
- **2:30–3:00** — Close: state the differentiator plainly ("we don't
  suggest bugs, we prove them") and end on the tagline.

## 10. What to get ready on your end before building

- [ ] IBM Bob 2.0 access activated (check email for the invite, follow the
      official hackathon setup guide)
- [ ] Python 3.x + `pip` installed locally
- [ ] A free Render or Vercel account for hosting
- [ ] A public GitHub repo created (with this README dropped in)
- [ ] Screen recording software ready
- [ ] Decide and write out your 2–3 planted bugs *in advance* — don't
      leave finding a bug to chance during the actual recording
- [ ] Keep taking screenshots of every real Bob session as you build —
      you need these as required submission evidence

## 11. Submission checklist (per official hackathon rules)

- [ ] Project Title, Short Description, Long Description (≤500 words)
- [ ] IBM Bob Usage Statement (≤500 words) — explicitly name Agent mode,
      subagents, and how each of the 4 roles maps to a real Bob call
- [ ] Technology & Category tags
- [ ] Public GitHub repo, including IBM Bob task session summary
      screenshots from every team member
- [ ] Demo Application Platform + live Application URL
- [ ] Cover image (16:9, PNG/JPG)
- [ ] Video demonstration (MP4, ≤3 minutes, ≥90 seconds showing the
      solution actually working on screen)
- [ ] Slide presentation (PDF)

## 12. One-paragraph pitch for your written statement (starter draft)

> Sentinel Swarm sends four IBM Bob 2.0 subagents to proactively hunt for
> bugs no one has reported yet — a class of problem current AI coding
> agents are known to struggle with. Unlike tools that merely suggest
> possible issues, every bug Sentinel Swarm reports is proven: a subagent
> writes a real test targeting a plausible edge case, the test is
> actually executed against the live code, and only a genuine failure
> counts as a finding. A second subagent then fixes it and confirms the
> fix by re-running the same test. The result is a fully autonomous,
> self-verifying bug hunt — visualized live as a swarm converging on a
> target — with zero human-authored bug report required.

Feel free to hand this straight to Bob/Claude Code as a starting brief.
