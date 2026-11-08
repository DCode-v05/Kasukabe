# Kasukabe

A 314-resident town simulation built as a research instrument. Every
resident is an LLM-backed agent with a wallet, a family, a job, a memory
stream and a cause of death. The question it exists to answer is **which
agents survive, and why**.

## Start here

**→ [`docs/HANDOVER.md`](docs/HANDOVER.md)** — the engineering handover:
what the system is, what is finished, what is not, how to run it, and the
sequence to a live run.

## See the village in 30 seconds

The v2 township map runs entirely in the browser. No credentials, no
database, no world server.

**Fastest — open it straight off disk:**

```bash
open app/game/static/township-v2.html          # macOS
xdg-open app/game/static/township-v2.html      # Linux
```

**Or serve it** (needed for live engine positions):

```bash
cd app                                          # pyproject.toml lives here
uv sync
uv run uvicorn game.server:app --port 8400      # run from app/, not the root
```

Then open **<http://127.0.0.1:8400/township-v2.html>** — note there is no
`/static/` prefix; the static files are mounted at `/`.

Full instructions, troubleshooting and URL parameters: [`docs/HANDOVER.md`
§1](docs/HANDOVER.md).

## Running it

### Prerequisites

```bash
cd app    && uv sync      # the simulation, broker, viewer, console
cd engine && uv sync      # the agent engine - its own project, its own venv
```

Everything below runs **from `app/`**, not the repo root: `pyproject.toml`
lives there and sets `pythonpath = ["."]`.

A live run also needs AWS credentials in `app/.env` (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) and at least one Bedrock inference
profile in `BEDROCK_INFERENCE_PROFILES`. **Nothing in this section except a
live run costs money.**

### The services

| Service | Port | Run with |
|---|---|---|
| world | 8100 | `cd app && make run-world` |
| broker | 8200 | `cd app && make run-broker` |
| console | 8300 | `cd app && make run-console` |
| game (viewer) | 8400 | `cd app && make run-game` |

### A run

```bash
cd app
python -m world.run_genesis --world-mode embedded
```

The embedded mode is what every launcher uses: the runner hosts the world
itself and drives the tick loop. **A run spends real money** — every
resident turn is a metered Bedrock call — so check `SPEND_CAP_USD` and
`KASUKABE_BUDGET_CAP_CENTS` before starting one.

### The evidence gate

```bash
cd app
python -m proof                    # the whole gate, about 2.5 minutes
python -m proof --show failed      # only what failed
python -m proof --skip stories     # fast loop (stories boot a real town each)
python -m proof --update-baseline  # accept the current state as the lock
```

`proof` runs the real system end to end with **only the model replaced**,
and judges what the town actually did — never what the source says. It
answers `proven` / `unproven` / `failed`, and `unproven` is never scored
as a pass. Costs nothing; makes no model calls.

It is a **ratchet, not a target**: nothing that was proven may stop being
proven, and nothing may newly fail. Known failures are recorded in
`app/proof/baseline.json` and written up in
[`docs/proof/FINDINGS.md`](docs/proof/FINDINGS.md).

### Tests

```bash
cd app    && uv run pytest    # ~5,200 tests, about 54 minutes
cd engine && uv run pytest    # the engine and its seam
```

## Layout

| Path | What it is |
|---|---|
| `app/world/` | The simulation — ticks, agents, ledger, map, tools. |
| `app/broker/` | Bedrock gateway. The only package that may `import boto3`. |
| `app/game/` | The viewer and its server, including the township map. |
| `app/game/console/` | The operator console. |
| `app/research/` | Offline measurement over the trace store. |
| `app/infra/` | Deployment: Firecracker microVM supervision, Caddy, systemd, runbook. |
| `infra/` (root) | Ops paperwork — quota request, phase-2 launch workflows. Not imported by anything. |
| `engine/` | The vendored agent engine. Partly frozen — see `ROADMAP.md` Law 1. |
| `smallville/` | Vendored Ville map assets (Apache-2.0). |
| `docs/` | Handover doc and archived artifacts. |

## The specifications

These are cited **by name and section number from inside the source code** —
`DESIGN.md` alone is referenced by 239 source files. **Do not move or rename
them.**

`DESIGN.md` · `ROADMAP.md` · `WORK_SPLIT.md` · `COGNITION_DELIVERY.md` ·
`AGENT_LIFE_FRAMEWORK.md` · `IMPLEMENTATION_LOG.md` · `RESEARCH_NOTES.md` ·
`app/STATUS.md` · `app/CONVENTIONS.md`

## Further reading

Engineering handover: [`docs/HANDOVER.md`](docs/HANDOVER.md).
What the town has actually been proven to do:
[`docs/proof/FINDINGS.md`](docs/proof/FINDINGS.md).
Edits made inside the frozen engine:
[`docs/decisions/LAW-1-MANIFEST.md`](docs/decisions/LAW-1-MANIFEST.md).
