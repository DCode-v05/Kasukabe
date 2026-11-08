# Start here: 50 agents, $60, day 2, all six views filmed

Do these in order. Every path is real and every command can be pasted as it is.
Longer explanations live in [TEAM-BOX-50-AGENTS.md](TEAM-BOX-50-AGENTS.md); this
file is only the steps.

Two words used throughout, because getting them the wrong way round is the most
common mistake here:

- **your Mac** — the laptop you are reading this on.
- **the box** — the shared cloud computer, after you have SSH'd into it.

---

## Step 1 · The SSH key — a web form, not a file

**Where:** the **start page** (the private start link the project owner sent
you), in the field marked **Add your SSH key**.

The key is already made. Print it on **your Mac**:

```bash
cat ~/.ssh/id_ed25519.pub
```

It prints one line beginning `ssh-ed25519 AAAA…`. Copy that whole line, paste it
into **Add your SSH key**, and press **Add my key**.

| | |
|---|---|
| paste **this** file | `~/.ssh/id_ed25519.pub` — the public half |
| never share **this** one | `~/.ssh/id_ed25519` — no `.pub`. This is the secret |

Wait about a minute after adding it. `Permission denied (publickey)` straight
away usually just means it has not propagated yet — wait and retry before
debugging anything.

---

## Step 2 · Get onto the box

On the start page press **Start the box**. The page refreshes itself and after
about a minute shows a command like `ssh team@13.233.40.12`. Paste that into a
terminal on **your Mac**. At a new address SSH asks whether to continue: type
`yes`.

The address changes every single start, so copy it from the page each time.

---

## Step 3 · Set the box up (first time only, about 10 minutes)

All of this runs **on the box**.

```bash
# the code. NOT into ~/work - that is the team's own research code.
cd ~ && git clone https://github.com/septemberai/kasukabe.git
cd ~/kasukabe/app

# our own environment, kept out of the shared /opt/venv
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e .

# the two things the cameras need. Neither needs sudo, which is just as well
# because there is none.
uv pip install --python .venv/bin/python playwright imageio-ffmpeg
.venv/bin/python -m playwright install chromium
```

---

## Step 4 · The GPT API key — `~/kasukabe/app/.env` on the box

**Where:** a file called `.env` inside `~/kasukabe/app/` **on the box**. Not on
your Mac. It does not exist yet; this creates it.

```bash
cd ~/kasukabe/app
nano .env
```

Type or paste this in, putting your real key on the first line:

```bash
OPENAI_API_KEY=sk-...your key here...
ONLY_MODEL=gpt-6-luna
OPENAI_RPM=500

WORLD_BASE_URL=http://127.0.0.1:8100
```

That is the whole file. **No AWS keys** — this run is not allowed to touch
Bedrock, so it does not need credentials for it.

Save with `Ctrl-o`, `Enter`, then `Ctrl-x`. Then lock the file down:

```bash
chmod 600 .env
```

Three notes on that file:

- **`ONLY_MODEL=gpt-6-luna` means one model and no other.** Not a preference —
  a rule. Kimi and deepseek are removed from the order entirely, for *every*
  kind of call, so there is no overflow onto Bedrock at any load.

  This is stronger than `COGNITION_FIRST_MODEL`, which only *prefers* a model
  and only for thinking calls. Under that setting the background work — the
  learning centre, dream probes, importance scoring, the summariser — still
  went to Bedrock every time. On the last run that was **$1.43 of $20.16 spent
  on deepseek while Kimi was supposedly the chosen model.**

  If the key is missing or wrong, the broker **refuses to start** rather than
  falling back to Bedrock. Falling back quietly is the entire thing this
  prevents, and it would otherwise be discovered as an AWS invoice.
- **Do not put the budget in here.** No `SPEND_CAP_USD`. The launcher works it
  out from the scenario file (Step 5) and a second copy here would be a second,
  disagreeing answer to what the run may spend.
- `.env` is gitignored, so it never reaches GitHub. It is also the only place
  the key should ever be typed.

---

## Step 5 · Check the 50 agents and the $60 before spending anything

**Where the two numbers live:**

| what | file | the line |
|---|---|---|
| **$60 budget** | `app/world/scenarios/fifty_from_day_two.json` | `"budget_cap_usd": 60.0` |
| **50 agents** | `app/world/genesis_50_town.json` | 50 residents, 37 of voting age |

Don't take that on trust — print it back, **on the box**:

```bash
cd ~/kasukabe/app
.venv/bin/python -c "
import json
s = json.load(open('world/scenarios/fifty_from_day_two.json'))
g = json.load(open('world/genesis_50_town.json'))
print('budget   \$', s['world']['budget_cap_usd'])
print('agents    ', len(g['agents']))
print('must vote ', sum(1 for a in g['agents'] if a['age_years'] >= 18), 'adults')
print('starts    game-day', s['world']['start_tick'] // 144, 'at dawn')
print('views     ', [v['name'] for v in s['recording']['views']])"
```

It must print exactly this:

```
budget   $ 60.0
agents     50
must vote  37 adults
starts    game-day 2 at dawn
views      ['town-with-buildings', 'town-without-buildings', 'live-chats', 'election', 'money', 'government']
```

If any line differs, stop — something is out of date. Then two more checks:

```bash
# is GPT really the model that will serve the run?
.venv/bin/python -c "
from broker.config import BrokerSettings
from broker.lanes import build_lane_pool, CallClass
p = build_lane_pool(BrokerSettings.from_env(), lambda m, r: None)
print('order:', p.preference_for(CallClass.COGNITION))"

# can this machine actually film?
.venv/bin/python -c "
import sys; sys.path.insert(0, 'scripts')
from record_ui import find_chrome, find_ffmpeg
from pathlib import Path
c, f = find_chrome(), find_ffmpeg()
ok = Path(c).is_absolute() and Path(c).exists()
print('chrome:', c if ok else 'MISSING'); print('ffmpeg:', f or 'MISSING')
raise SystemExit(0 if (ok and f) else 'cannot record')"

# is the shared box free enough to take us? 32 CPUs; over 16 means wait.
uptime
```

`order:` **must be exactly `('gpt-6-luna',)` — one model, nothing behind it.**
If anything else is in that tuple, `ONLY_MODEL` did not load and the run can
still overflow onto Bedrock.

If the key is missing entirely the check will not print an order at all; it
raises, because the broker refuses to build a pool that cannot serve the only
model it is allowed to use. That refusal is the feature.

---

## Step 6 · Start the run

One run, straight through: 50 residents, from dawn on game-day 2 with the
election that day, six cameras filming, the report rewriting itself every two
minutes, until the $60 is spent.

```bash
tmux new -s fifty
cd ~/kasukabe/app
./run-scenario.sh world/scenarios/fifty_from_day_two.json fifty-day2-$(date +%Y%m%d) 4
```

Detach with `Ctrl-b` then `d`. Reattach with `tmux attach -t fifty`.

**Write down the name it prints.** Everything for this run lands in one dated
folder, and you need that exact name to resume:

```
docs/runs/series/fifty-day2-20260929/     report.md, video/, sessions/, logs
app/data/fifty-day2-20260929.db           the trace
```

The date stops two runs of this scenario from overwriting each other's
evidence, and makes the folder say when it was made. Leaving the name off
entirely also works — the launcher dates it for you — but then you must still
pass it by name to resume.

The three arguments are the scenario, the run name, and a ceiling in **hours**.
The `$60` is not one of them — it comes from the scenario file you checked in
Step 5, and the broker's own ceiling is set to $65 automatically so the
end-of-run learning drain has room.

### Ten minutes in, check it — this is the one moment that matters

The first ten minutes cost about a dollar. Spend them proving the run is the
one you meant to start, because every fault below is silent and all of them get
more expensive by the hour:

```bash
.venv/bin/python scripts/check_run_health.py \
  --run fifty-day2-20260929 --residents 50 --views 6 \
  --model gpt-6-luna --only --cap 60
```

`--only` changes the model question from "is Luna leading" to "**did anything
else serve a single call**", answered from the trace rather than from the
config. Config says what should happen; only the trace says what did.

| check | what a failure means, and why it is worth catching in minute ten |
|---|---|
| **residents acting** | all 50 must have taken a turn. On the 20-resident run every resident had acted within **4 ticks**, so one still silent here is stuck, not slow |
| **model serving** | `gpt-6-luna` must be the **only** model with any calls. One deepseek call means Bedrock is being billed after all — silent, and caught at hour four it has already spent the run |
| **cameras** | six views, each with **recent** frames. Six directories containing frames is not six cameras filming, which is the failure an `ls` calls healthy |
| **live report** | `report.md` younger than two refreshes. A dead loop is invisible: the file still exists and reads correctly, it just stops being true |
| **call errors** | under 5%, or the run is paying for failures |

Every line must read `ok`. If one says `FAIL`, stop now, fix it, and start
clean — about a dollar lost:

```bash
tmux kill-session -t fifty
rm -f data/fifty-day2-20260929.db && rm -rf ../docs/runs/series/fifty-day2-20260929
```

After that, re-run the same check every hour or so. It is read-only and cannot
disturb the run.

### The box stops it after 4 hours. That is expected.

The run needs roughly **3.5 to 7 hours** and the box switches off after 4 hours
per start, even mid-job, so it will be cut at least once. Press **Start the
box** again, SSH in at the **new** address, and continue:

```bash
tmux new -s fifty
cd ~/kasukabe/app
RESUME=1 ./run-scenario.sh world/scenarios/fifty_from_day_two.json fifty-day2-20260929 4
```

**Use the same dated name you started with.** `RESUME=1` now refuses rather
than starting a fresh run when the name matches nothing, and prints the names
that do exist — which matters most the morning after, when `$(date +%Y%m%d)`
would compute a *new* date, match nothing, and would otherwise have started the
$60 over from the beginning.

**The $60 carries across — it does not reset.** The amount already spent is
written into the checkpoint, so a resumed run continues towards $60 in total.
The video and the frame numbering carry across too, into the same dated
folder.

If the resume is refused for a stale lock: `rmdir /tmp/kasukabe-runseries.lock`.

### If you need to spend less

`CAP=40 ./run-scenario.sh ...` overrides the scenario for one run. The day-2
election is counted around **54 ticks in, near $7**, so the thing the run is
named for is reached early and cheaply; the rest of the money buys game-days 3
onwards.

## Step 7 · Every state is filmed, and how to prove it while it runs

**Six cameras run at once**, each its own headless Chrome, all filming the same
live world. This is the point: a run has one truth and many faces, and once the
run ends the world is gone and the other five can never be asked for.

| view | what you see |
|---|---|
| `town-with-buildings` | the map, **roofs on** |
| `town-without-buildings` | the same map, **roofs off** — people visible indoors |
| `live-chats` | the group chats as residents talk |
| `election` | standing, campaigning, and the count |
| `money` | the ledger, wages, price boards |
| `government` | the seats and what the ministry does |

One frame every 4 seconds at 1920×1080, encoded to `run.mp4` per view when the
run ends.

### The launcher refuses to start if it cannot film

This was a real risk worth closing. The recorder sends Chrome's error output to
`/dev/null` — right for a working camera, and it meant a **missing** Chrome was
silent: six empty folders and a report nobody can watch, discovered hours later
with the money already spent. The launcher now asks once, before starting
anything, and refuses with the exact fix if it cannot record.

### Watch the frames arrive

Open a second terminal on the box (or `Ctrl-b` then `c` inside tmux):

```bash
cd ~/kasukabe
watch -n 30 'ls docs/runs/series/fifty-day2-20260929/video/*/frames | wc -l; \
  du -sh docs/runs/series/fifty-day2-20260929/video/*'
```

**All six directories should grow together.** If one stays flat while the others
climb, that camera has a problem — read its own log:

```bash
cat ~/kasukabe/docs/runs/series/fifty-day2-20260929/record-live-chats.log
```

---

## Step 7b · The run report, rewritten every 2 minutes

You do not have to wait for the run to end to read it. The report is
regenerated **every 120 seconds** for as long as the run is alive, at:

```
~/kasukabe/docs/runs/series/fifty-day2-20260929/report.md
```

It is the same report the run finishes with — the checklist scored from the
trace, the spend, the election, who said what to whom — just as far as the run
has got. Watch it fill:

```bash
cd ~/kasukabe
watch -n 30 'ls -l docs/runs/series/fifty-day2-20260929/report.md; \
  head -20 docs/runs/series/fifty-day2-20260929/report.md'
```

Or read it properly at any moment:

```bash
less ~/kasukabe/docs/runs/series/fifty-day2-20260929/report.md
```

Three things worth knowing:

- **It cannot disturb the run.** Every database connection the generator opens
  is read-only, so it can never lock or corrupt the trace the run is writing.
- **You never catch it half-written.** Each pass writes to a temporary file and
  moves it into place, and a move within a directory is atomic — you see the
  previous report or the new one, never a torn one.
- **A failed pass costs one refresh, not the report.** Mid-run the trace holds a
  half-finished story and the generator may not like it; if a pass fails the
  last good report stays exactly where it was.

`REPORT_EVERY=60 ./run-scenario.sh ...` changes the interval. It costs about
2 seconds per pass on a 116 MB trace, so 120s is under 2% of one core.

This matters more on the box than on a laptop: the machine stops after four
hours whether the run has finished or not, so "at the end" may be a long way
off. With this, a run that has been going three hours can be read now.

---

### Restarts do not lose video

The recorder counts the frames already on disk and carries on numbering from
there, so the 4-hour stop does not restart the video or overwrite what was
filmed. The mp4 is built from the whole sequence at the end.

---

## Step 8 · Take the results home, then close the session

**On the box**, see what there is:

```bash
ls ~/kasukabe/docs/runs/series/fifty-day2-20260929
```

You want `report.md`, `sessions/` (50 files, one per resident), the logs, and
`video/<view>/run.mp4` for each of the six views.

**On your Mac**, using the address from the start page:

```bash
scp -r team@ADDRESS:kasukabe/docs/runs/series/fifty-day2-20260929 ./fifty-day2-20260929
```

Then, **back on the box, this part is not optional:**

```bash
tmux kill-session -t fifty && exit
```

An idle tmux session counts as work. It keeps the box switched on and spends the
whole team's remaining hours while nothing is happening.

---

## If something goes wrong

| you see | it means | do this |
|---|---|---|
| `Permission denied (publickey)` | the key is not on the box yet | re-add the `.pub` line on the start page, wait a minute, retry |
| `Connection timed out` / `refused` | the box is off, still starting, or you used an old address | reload the start page, use the address it shows **now** |
| `REFUSED: ... cannot record it` | no Chrome or no ffmpeg | run the two `uv pip install` lines from Step 3 |
| `REFUSED: <db> already exists` | this run already started once | add `RESUME=1` to continue it |
| `order:` has anything but `gpt-6-luna` | `ONLY_MODEL` did not load | check `.env` spelling, then re-run the check |
| `ONLY_MODEL=... has no lanes` | the OpenAI key is missing, empty or rejected | fix `OPENAI_API_KEY` in `.env`. This refusal is deliberate — booting anyway would bill Bedrock |
| it stopped after ~4 hours | the per-start limit | Step 6, the `RESUME=1` command |
| it stopped after 15 minutes | nothing was running and nobody was connected | nothing — it was saving budget |
| `Budget used up` | the team's hours are gone for good | ask the project owner |
| one camera's frames stay flat | that Chrome died | read `record-<view>.log`; the run itself is unaffected |
| a view has `frames/` but no `run.mp4` | its encode did not finish | rebuild it from the frames, below |

### If a view ends up with frames but no video

The frames **are** the recording; the mp4 is built from them. Nothing is lost,
and you can build it whenever you like:

```bash
cd ~/kasukabe/app
.venv/bin/python scripts/record_ui.py --encode-only \
  --out docs/runs/series/fifty-day2-20260929/video/live-chats --fps 30
```

A camera now survives a failed frame rather than dying on it, and encodes
whatever it filmed even when it is shutting down — but the frames are the
backstop if anything else goes wrong.

---

## Three honest warnings

1. **The team's box hours are the real limit, not the $60.** The run needs 4–8
   hours and the team has about **10.5 hours left, shared, for good**. Check the
   remaining budget on the start page before you begin. If it is under about 6
   hours, lower `budget_cap_usd` rather than hoping for the fast case — the run
   still reaches the day-2 election and crosses into day 3 on less.
2. **`gpt-6-luna` has never served a turn in this project.** The lane is tested
   against recorded OpenAI responses, not the live API. The first real call is
   the first proof, which is why the model check in Step 5 comes before the run.
3. **50 residents has never been run on this economy.** The largest so far is
   30, and that run predates every fix since. Expect surprises in the first
   hour and watch the frames and the spend.
