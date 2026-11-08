# deni-50-agent — what fifty residents cost the machine

**The first per-agent resource record this project has ever produced**, and
the first real memory measurement ever taken on this machine.

`docs/runs/series/fifty/resources.jsonl` — one JSON object per engine per
tick: `agent_id`, `rss_mb`, `peak_rss_mb`, `cpu_percent`, `alive`,
`processes`, `tick`, `at`.

| | |
|---|---|
| Engines | **50** |
| Samples | **1,800** (36 per agent, ticks 37–72) |
| Dead samples | **0** — no engine died or was restarted |
| Processes per agent | **2** (launcher + engine) |

---

## §1 The headline: the capacity model was wrong by 6×

Preflight has always budgeted **550 MB per engine**. Measured here:

| | |
|---|---|
| Mean peak RSS per engine | **88.0 MB** |
| Whole town at peak | **4,402 MB** |
| Preflight's assumption for 50 | 27,500 MB |
| **Error** | **6.2× too high** |

The reason, and it is not a rounding issue.
`memory_budget.measured_mb_per_engine()` shells out to `ps`. **Windows has
no `ps`**, so it returned `None` on every call ever made on this machine
and preflight silently fell back to a constant measured on a Linux box
under load. The constant's own comment already said what that costs:

> "The constant is not wrong; it is wrong HERE, and the machine already
> knows better."

It was written about a Mac. The identical hole was open on Windows and
nobody could see it from the output, because a fallback and a measurement
print the same way.

**Consequence:** this run was refused before the fix — *"50 engines need
about 27500 MB, but only 13051 MB is usable"* — and permitted after it:
*"50 engines need ~2100 MB, 13931 MB free - fits"*.

**At the measured size this machine fits about 177 engines**, not the ~30
previously believed.

---

## §2 Memory per agent

Remarkably even. Every engine sat between 84 and 91 MB.

| Agent | Peak RSS | Peak CPU | Mean CPU |
|---|---:|---:|---:|
| **n01** | **90.2 MB** | 23.5% | 1.5% |
| n24 | 89.9 MB | 3.3% | 0.6% |
| n44 | 89.8 MB | 14.1% | 1.1% |
| n04 | 89.7 MB | 4.5% | 0.6% |
| n27 | 89.7 MB | 18.6% | 1.1% |
| n09 | 89.6 MB | 29.8% | 1.3% |
| n31 | 89.6 MB | 19.6% | 0.9% |
| n42 | 89.6 MB | 14.5% | 1.2% |
| … | | | |
| n32 | 85.3 MB | 3.9% | 0.3% |
| n10 | 85.0 MB | 1.4% | 0.3% |
| n33 | 84.9 MB | 2.7% | 0.3% |
| **n46** | **84.4 MB** | 2.4% | 0.3% |

**Total spread: 5.8 MB across fifty engines** — 84.4 to 90.2. Memory is
essentially a fixed cost per resident. What a resident *does* barely
shows in what it *occupies*.

That is worth saying plainly because it refutes something I expected. I
built this looking for the expensive resident — n09 was once measured
writing 18,547 characters of prose a turn. **There is no expensive
resident in memory.** n09 is 89.6 MB against a mean of 88.0.

---

## §3 CPU per agent

Where the variation actually lives. **A 21× spread**, 1.4% to 29.8%.

| | Peak CPU |
|---|---:|
| n09 | **29.8%** |
| n34 | 28.5% |
| n11 | 27.4% |
| n47 | 26.8% |
| n01 | 23.5% |
| median | 8.4% |
| n10 | **1.4%** |

**n09 is top by CPU and unremarkable by memory** — which is exactly the
shape you would expect from a resident that writes long turns. Thinking
costs cycles, not pages. A mean-only measurement would have shown none of
this.

Mean CPU across a whole run is 0.3–1.5% per engine: an engine spends
almost all its time waiting on the broker, and the peaks are the moments
it is actually generating.

---

## §4 The town over time

| Tick | Town RSS | Town CPU |
|---|---:|---:|
| 37 | 3,961 MB | 45% |
| 43 | 4,145 MB | 54% |
| 49 | 4,219 MB | 62% |
| 55 | 4,214 MB | 25% |
| 61 | 4,224 MB | 12% |
| 67 | 4,308 MB | 11% |

**Memory grows, slowly and then flat.** +184 MB over the first six ticks
as the engines warm, then +163 MB across the remaining twenty-four — about
**3.3 MB per agent over the whole run**, and flattening. Whatever
accumulates in the working-memory flush (99,591 tokens in one observed
call) is **not** accumulating in RSS at a rate that threatens a long run.

**CPU peaks early and falls away.** 62% of one core at tick 49, down to
11% by tick 67 — the town spending down its budget and going quiet as the
spend cap approached. Peak town-wide CPU was **220% of one core**, so
roughly 2.2 cores for fifty residents.

---

## §5 A defect this found in itself

The first version of this sampler wrote:

```
{"agent_id": "n01", "tick": 37, "rss_mb": 4.4, ...}
```

`python -m src.sim.sim_main` spawns a **child** that does the work — 100
processes for 50 agents, the one the supervisor holds weighing ~4.4 MB and
its child ~79 MB. The sampler measured the launcher. **Every number was
18× too small**, and the total would have claimed fifty engines cost 220 MB
when they cost 4.4 GB.

That run was discarded and re-run rather than reported. Each row now
carries `processes`, so a row that found only the launcher announces
itself as an undercount instead of passing as a small engine.

---

## §6 What this means for scale

| Population | Memory at 88 MB/engine | Fits in 13 GB free? |
|---|---:|---|
| 50 | 4.4 GB | ✓ measured |
| 100 | 8.8 GB | ✓ likely |
| 150 | 13.2 GB | marginal |
| **177** | 15.6 GB | preflight's own limit |
| 250 (DESIGN.md's town) | 22.0 GB | ✗ on this machine |

**CPU, not memory, is the likelier ceiling.** 2.2 cores at fifty means
~4.4 at a hundred; and the broker serves 200 RPM on this account (four of
six lanes have no inference profile), which fifty residents at a 20-second
tick already approach.

Two caveats I have not tested: growth was measured over 35 ticks, not a
game-day; and every engine here was healthy, so nothing says what a
restart storm costs.

---

*Generated from `docs/runs/series/fifty/resources.jsonl` by
`app/world/resource_log.py`. Preflight figures from
`app/world/memory_budget.py`.*
