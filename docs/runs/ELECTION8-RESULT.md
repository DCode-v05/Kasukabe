# The eight-resident election: what happened

*Run `election-at-dawn-8`, 2026-09-23, 14:48–15:18 IST. Scenario
`app/world/scenarios/election_at_dawn_8.json`. Full report:
`docs/runs/series/election8/report.md`. Video:
`docs/runs/series/election8/video/run.mp4` (27 s, 823 frames). Trace:
`app/data/election-at-dawn-8.db`.*

**The town elected a government, and turned out the one it had.** Three
residents stood, none of them the three already in office; five of eight voted;
Jovana Ravensworth, a day labourer, took four of the five votes and the seat.
It cost **$10.42** against a $19 cap and took **30 minutes**.

This is the first run in which residents stood for election and voted of their
own accord. Nothing was staged.

## What ran

| | |
|---|---|
| Cast | 8 residents, all adults (`world/genesis_8_funded.json`) |
| Ticks | 36 → 150, one game-day and a night (0.79 game-days) |
| Election | candidacy 36–60, campaign 60–84, voting 84–108, **counted at 108** |
| Already in office | Giselle Ingram (PM), Farid Ravensworth, Sebastian Petrov — appointed at genesis, marked `caretaker`, free to stand |
| Spend | **$10.42** of a $19 cap · 1,577 model calls · **100% succeeded** |
| Cost per resident-day | **$1.64**, against the $2.27–2.88 the budgets assumed |
| Wall-clock | 30 minutes, about 16 s a tick |

## What the town did

**Candidacy (36–60).** Three stood, all of them outside the government:
Hendrik Petrov, an artisan, at tick 44 — *"putting my own name forward, not
riding anyone's ticket"*; Delphine Quintero, a grocery clerk, at 50, who had
told a sitting minister at dawn that she wanted *"changes to the town charter —
stuff that's been sitting wrong with me"*; and Jovana Ravensworth, a day
labourer, at 55. **Not one of the three sitting members stood.** Farid
Ravensworth said why, on the record:

> Not running, Delphine. My name stays off that list — I've got a shop to keep
> and books that don't balance themselves.

**Campaign (60–84).** The same minister, who was not running, read the race
aloud better than any instrument in the repository:

> You've got three candidates, three different towns they're imagining. Hendrik
> sees Kasukabe as a workshop — gears turning, smithies glowing, apprentices
> learning trades their fathers never knew. Jovana sees it as a job site —
> hands busy, wages honest, nobody left idle. And you… you see it as a ledger,
> don't you? Debits, credits, flows in and out. Not wrong — just not the whole
> picture.

He also worked out the arithmetic of a split vote, and was right about who had
whose ear.

**Voting (84–108).** Nobody voted for the first seventeen ticks: residents were
felling timber, hiring labour and arguing about bread. The first two ballots
came at tick 101 — from Ulrike Greenhow, who never stood, and from **Giselle
Ingram, the sitting Prime Minister**, in an election she had not entered. Five
of eight voted in all. Delphine, a candidate herself, voted for a rival:

> Voted. Cast my ballot for Jovana Ravensworth. She spoke truth where others
> spoke poetry, and I've had my fill of pretty words that don't fill a belly.

**The count (108).** Jovana Ravensworth 4, Hendrik Petrov 1, Delphine Quintero
0. Jovana is Prime Minister; Hendrik and Delphine are her ministers.
`politics.regime.changed` is in the trace: every seat passed from the appointed
government to people the town chose.

**Afterwards (108–150).** The new government used none of its powers: no tax
change, no appointment, no order, no amendment. At the day boundary (tick 144)
the head tax was charged on all eight residents and the town's upkeep came due;
the treasury paid nothing out, because nobody had ever been hired to pay.

## The checklist: 16 of 42 items held

Scored by `world/run_checklist.py` from this run's own trace.

**Held.** G1 (86% of addressed remarks answered), G2 (9 long exchanges), G3 (a
conversation with 7 speakers), G4 (7 settlements at a price spoken first), G5
(nobody died), G6 (63% of turns changed the world), ES4 (16.9 settlements a
game-day), N10 (prompt growth 1.23×, inside the 1.25 bar), N6 (4 of 8
number-naming conversations became deals), N16 (9 distinct goals among 9), X5,
ECON-2 (goods moved for money), ECON-4 (1.09 meals per resident-day), ECON-14 (a
posted price changed), **GOV-1** (an election completed and seats filled) and
**GOV-2** (ordinary residents voted; 3 of the 5 were not candidates).

**Not held, and worth naming.**

- **GOV-3 to GOV-10** — no government tool was used by anyone, before or after
  the election. Holding a seat and using it are different things, and this run
  says plainly that the second did not happen.
- **ECON-5, ECON-11, X17** — the treasury took tax from eight residents and
  spent nothing. No hire, so no wage; nothing for a government to authorise.
- **ET1** — $0.20 per resident-day is the bar; this run was $1.64 (the report's
  own row says $2.28, dividing by a narrower window).
- **ET5** — p95 input 21,219 tokens against a 12,000 bar. Prompts are still the
  bill.
- **N15** — a day 2 cannot be reached in a run that stops at tick 150. That was
  the price of the budget.
- **N19** — 2 of 311 remarks were not in English.

## What broke, and what was fixed

- **The video did not encode.** All 823 frames were kept, and ffmpeg exited 254:
  `record_ui.encode()` runs ffmpeg with the frames directory as its working
  directory, and the launcher passed a relative `--out`, so the output path
  resolved into a directory that does not exist. The video was rebuilt by hand
  from the frames, and the paths are now resolved to absolute before ffmpeg is
  called (`world/tests/test_the_camera_writes_where_it_was_told.py`).
- **Two `ClientDisconnect` tracebacks** on the world's `/ledger/debit` endpoint:
  a caller hung up mid-request. No call failed — 1,577 of 1,577 succeeded — and
  the run was unaffected. Worth watching, not worth stopping for.
- **The AWS key was not rotated.** The operator was told and chose to run on the
  key that had been pasted into a chat on 2026-09-21. Recorded in
  `docs/runs/series/election8/KEY-NOT-ROTATED.txt`.

## What this run cannot tell you

It is one game-day with eight residents. It cannot speak to a second or third
day, to dormancy, to anything needing a larger market, or to whether a
government that *did* act would be obeyed. The incumbents held their seats by
appointment rather than by election, so "the town turned out its government" is
a weaker claim than it would be after two elections. And a cheaper rate per
resident-day at eight residents does not license the same rate at fifty.

What it does establish, for the first time and for $10.42: residents who are
told the rules will stand for office, campaign, weigh each other up, vote, and
hand power to somebody new.
