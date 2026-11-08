# 50 residents on gpt-6-luna, one run in two legs

$8.16 all told, 50 residents, an election scheduled for game-day 2, and every
single call served by `gpt-6-luna` - not one by Bedrock, which is what
`ONLY_MODEL` was added to guarantee and what the trace confirms.

| | leg 1 - laptop | leg 2 - team box |
|---|---|---|
| spent | $2.48 | $5.68 |
| calls | 2,057 | 3,940 (cumulative) |
| ticks | 325-339 | 339-352 |
| duration | 8 min 41 s | ~14 min |
| video | **six views, short** | none - see below |

The second leg resumed the first from its checkpoint, so `report.md` and
`sessions/` cover **both** and are the only copies worth reading. The older,
shorter versions have been removed rather than left to be mistaken for these.

## What is here

- `report.md` - 12,459 lines, the whole run
- `sessions/n01..n50.jsonl` - one per resident, both legs
- `video/<view>/run.mp4` and `video/<view>/frames/` - six views, laptop leg
- `logs-laptop-leg/` - the first leg's recorder and runner logs
- everything else - the box leg's logs

## The video is short, and from the laptop only

**Laptop leg.** 8 CPUs and 8 GB carrying 51 engine processes and six
1920x1080 headless Chromes. Load average reached 37 with no free memory, the
cameras were starved, and their screenshots began failing after 11 to 18
frames. Five gave up and encoded what they had; the sixth was killed outright
by the OS and was rebuilt from its frames with `--encode-only`. Six of six
videos exist, all of them brief.

**Box leg: nothing.** Six recorders ran, six stayed alive, not one frame. Each
log repeats one unhelpful line, because Chrome's own complaint goes to
`/dev/null`:

    record_ui: frame 0 failed (no close frame received or sent)

Running Chrome by hand found three causes stacked behind it:

1. **16 missing shared libraries.** Amazon Linux 2023 ships none of the
   GTK/X11 stack Chromium links against.
2. **No `--no-sandbox`.** The sandbox needs user namespaces the box withholds,
   so Chrome dies as it renders.
3. **No fonts at all.** Chrome starts, answers on its DevTools port, and dies
   laying out text with nothing to lay it out in.

All three are fixed. (2) is in the repo with a test; (1) and (3) needed no
root - `dnf download` and `rpm2cpio` put 240 shared objects and 104 fonts
under `~/chromelibs`, with three exported paths in `~/.bashrc`. Verified
afterwards rather than assumed: zero font errors, six DevTools pages, and a
recorder that took frames and wrote an mp4.

By then the budget was spent, so this leg has no footage of its own.

## What the $8.16 bought

`gpt-6-luna` proven end to end, `ONLY_MODEL` holding under real traffic, all
50 residents acting within 4 ticks - and four defects that would each have
spoiled a $60 run: a 400 on every tool call, and the three above. None of the
three is reproducible on a Mac.

**Cost per tick: $0.165.** That number transfers between machines because it
is tokens. $60 buys about 364 ticks; the day-2 election at tick 378 is 53
ticks in, about $8.75.
