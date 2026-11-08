# Kasukabe

A town simulation with 314 residents, each one an LLM-backed agent with a wallet, a family, a job, a memory stream and a cause of death. It is built as a research instrument to see which agents survive and why.

Nobody tells the residents what to do. They get the prices and the rules, not a strategy. Thinking is metered in real dollars, so an agent that can't pay for its next thought goes dormant, and one nobody revives dies. Whether the town ends up with cooperation, trade, charity, cartels or theft is what the project is trying to find out. Generative Agents (arXiv 2304.03442) is a reference point only.

## How it works

- **Two currencies.** Model tokens (USD) are energy: what an agent spends to think and act. Sim-currency is money: what work earns and what buys energy at the government store.
- **Ledger invariant.** Agent wallets, treasury and burned must always sum to $1000, otherwise the simulation halts.
- **Tool calls only.** World state changes only through tool calls, and every tool is location-gated (you mill at the mill, you vote at town hall).
- **Self-extending agents.** A meta-tool lets agents compose new tools from existing primitives, register them at a patent office and sell or license them.
- **Institutions.** Businesses, jobs, taxes, pensions, elections, courts and a government whose confidential tools pass to whoever wins the election.
- **Dormancy.** A resident at $0 goes dormant. Another resident can revive them for 5,000 sim, or they die after 24 game-hours.

A tick is 10 game-minutes and 144 ticks make a game-day. On each tick a resident's turn goes through the broker to Amazon Bedrock, comes back as tool calls, and the world applies them under the location gates and the ledger invariant. Every call is traced, so the trace store is a full causal record. Research jobs export it per game-day to Parquet through DuckDB.

| Service | Port | Role |
|---|---|---|
| world | 8100 | Ticks, agents, ledger, map, tools |
| broker | 8200 | Bedrock gateway, the only package allowed to call AWS |
| console | 8300 | Operator console |
| game | 8400 | Viewer server, including the township map |

### Evidence gate

Green unit tests don't show that a feature is ever reached. `proof` runs the real system end to end with only the model replaced and gives each claim a verdict: proven, unproven or failed. An unproven claim never counts as a pass. It works as a ratchet: nothing proven may stop being proven, and nothing may newly fail.

The last recorded run judged 236 claims: 177 proven, 59 unproven, 0 failed, in 23.4 seconds with no model calls. Details are in [docs/proof/FINDINGS.md](docs/proof/FINDINGS.md).

## Tech stack

Python 3.12, FastAPI, Uvicorn, Pydantic v2, WebSockets, httpx. Amazon Bedrock through boto3. SQLite for the trace store, DuckDB and Parquet for research exports. A vendored agent engine (aiosqlite, sqlite-vec). Frontend in plain HTML, CSS and JavaScript with Phaser 3 and the Smallville Ville tileset (Apache-2.0). Docker, Firecracker microVMs, Caddy and systemd for deployment. uv, pytest and Make for tooling.

## Setup

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). A live run also needs AWS credentials and at least one Bedrock inference profile.

```bash
git clone https://github.com/DCode-v05/Kasukabe.git
cd Kasukabe
cd app    && uv sync      # simulation, broker, viewer, console
cd ../engine && uv sync   # the agent engine, its own project and venv
```

For a live run, put `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` and `BEDROCK_INFERENCE_PROFILES` in `app/.env`.

## Running

Everything runs from `app/`, not the repo root.

To see the village, open `app/game/static/township-v2.html` straight from disk. It runs fully in the browser with all 314 residents and needs no server or credentials. To get live engine positions, serve it:

```bash
cd app
uv run uvicorn game.server:app --port 8400
```

Then open <http://127.0.0.1:8400/township-v2.html> (no `/static/` prefix).

A live run spends real money, so check `SPEND_CAP_USD` and `KASUKABE_BUDGET_CAP_CENTS` first:

```bash
cd app
python -m world.run_genesis --world-mode embedded
```

Individual services start with `make run-world`, `make run-broker`, `make run-console` and `make run-game`. The scripts in [Run-commands/](Run-commands/README.md) cover multi-resident runs on a box.

The evidence gate costs nothing and makes no model calls:

```bash
cd app
python -m proof                    # the whole gate, about 2.5 minutes
python -m proof --show failed      # only what failed
python -m proof --skip stories     # fast loop
```

Tests run with `uv run pytest` in `app/` and in `engine/`. The app suite has about 5,200 tests.

## Project structure

```
Kasukabe/
├── app/
│   ├── world/            # simulation: ticks, agents, ledger, map, tools
│   ├── broker/           # Bedrock gateway, the only package that imports boto3
│   ├── game/             # viewer server and the township map (static/township-v2.html)
│   │   └── console/      # operator console
│   ├── research/         # offline measurement over the trace store
│   ├── proof/            # evidence gate and its baseline
│   ├── infra/            # Firecracker microVMs, Caddy, systemd, runbook
│   └── tests/
├── engine/               # vendored agent engine, partly frozen (see ROADMAP.md, Law 1)
├── infra/                # ops paperwork: quota request, launch workflows
├── Run-commands/         # copy-paste commands to start and watch a run
├── docs/                 # handover, proof findings, specs, decisions, run reports
├── DESIGN.md             # world, economy and rules
├── ROADMAP.md
└── README.md
```

`DESIGN.md` and `ROADMAP.md` stay at the root because source comments and tests cite them by name. Everything else is under `docs/`. [docs/HANDOVER.md](docs/HANDOVER.md) covers what is finished, what is not, and the path to a live run.

## Limitations

- Live runs depend on Bedrock access and cost real money.
- 59 of the 236 gate claims are still unproven (see [docs/proof/UNPROVEN.md](docs/proof/UNPROVEN.md)).
- The engine is vendored and partly frozen, so some fixes have to go through the Law 1 process in `ROADMAP.md`.
