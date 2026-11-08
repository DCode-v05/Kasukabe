# Kasukabe — An LLM Agent Society

![Python](https://img.shields.io/badge/Python_3.12-3776AB?style=flat&logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white) ![Pydantic](https://img.shields.io/badge/Pydantic_v2-E92063?style=flat&logo=pydantic&logoColor=white) ![Amazon Bedrock](https://img.shields.io/badge/Amazon_Bedrock-232F3E?style=flat&logo=amazonaws&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white) ![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?style=flat&logo=duckdb&logoColor=black) ![Phaser](https://img.shields.io/badge/Phaser_3-8A2BE2?style=flat&logo=javascript&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)

## Overview

Kasukabe is a 314-resident town simulation built as a research instrument. Every resident is an LLM-backed agent with a wallet, a family, a job, a memory stream and a cause of death. The question it exists to answer is **which agents survive, and why**.

Nobody tells the residents what to do. They are given the prices and the rules, never the strategy. Their thinking is metered in real dollars, so an agent that cannot pay for its next thought goes dormant, and an agent nobody revives dies. Whether the town invents cooperation, trade, charity, cartels or theft is the research question.

The idea is inspired by Generative Agents (arXiv 2304.03442) as reference only. Kasukabe goes further with a larger population, real economic stakes, mutable politics and agents that can write their own tools.

## Key Features

- **Two currencies.** Model tokens (USD) are *energy* — what an agent spends to think and act. Sim-currency is *money* — what work earns and what buys energy at the government store.
- **A $1000 ledger with a tripwire.** Agent wallets + treasury + burned must always equal $1000, or the simulation halts.
- **World state changes only through tool calls.** Talk never moves matter. Every tool is location-gated: you mill at the mill and vote at town hall.
- **Self-extending agents.** A meta-tool lets agents compose new tools from existing primitives, register them at a patent office, and sell or license them.
- **Live institutions.** Businesses, jobs, taxes, pensions, elections, courts and a government whose confidential tools pass to whoever wins the election.
- **Cognitive poverty.** A resident at $0 goes dormant; another resident can revive them for 5,000 sim, or they die after 24 game-hours.
- **Browser township map (v2).** An isometric village that runs fully in the browser — families, jobs, wages, homes, illnesses and deaths for all 314 residents — with no server or credentials.
- **Evidence gate (`python -m proof`).** Runs the real system end to end with only the model replaced, and judges what the town actually did.

## How It Works

### The services

| Service | Port | Role |
|---|---|---|
| world | 8100 | The simulation: ticks, agents, ledger, map, tools |
| broker | 8200 | Bedrock gateway — the only package allowed to call AWS |
| console | 8300 | Operator console |
| game | 8400 | Viewer server, including the township map |

A tick is 10 game-minutes, 144 ticks make a game-day. On each tick a resident's turn goes through the broker to Amazon Bedrock, comes back as tool calls, and the world applies them under the location gates and the ledger invariant. Every call is traced, so the trace store is a complete causal record of the world. The research jobs export it per game-day to Parquet through DuckDB.

### The evidence gate

Unit tests can be green while a feature is never reached. `proof` closes that gap. It answers each claim with one of three verdicts — **proven**, **unproven** or **failed** — and an unproven claim is never scored as a pass. It works as a ratchet: nothing proven may stop being proven, and nothing may newly fail.

### The specifications

`DESIGN.md`, `ROADMAP.md` and the other root documents are cited by name and section number from inside the source code — `DESIGN.md` alone by 239 files. Do not move or rename them.

## Results / Highlights

- **314 residents** on the v2 map, **250** of them genesis residents with full biographies.
- Evidence gate: **236 claims judged — 177 proven, 59 unproven, 0 failed**, in 23.4 seconds and with no model calls. See [`docs/proof/FINDINGS.md`](docs/proof/FINDINGS.md).
- About **5,200 tests** in the app suite, plus the engine's own suite.
- Every resident turn is a metered Bedrock call, capped by `SPEND_CAP_USD` and `KASUKABE_BUDGET_CAP_CENTS`.

## Tech Stack

- **Language:** Python 3.12
- **Backend:** FastAPI, Uvicorn, Pydantic v2, WebSockets, httpx
- **Models:** Amazon Bedrock via boto3
- **Data:** SQLite trace store, DuckDB and Parquet for research exports
- **Agent engine:** vendored engine with aiosqlite and sqlite-vec memory
- **Frontend:** HTML, CSS, JavaScript, Phaser 3, Smallville Ville tileset (Apache-2.0)
- **Infrastructure:** Docker, Firecracker microVMs, Caddy, systemd
- **Tooling:** uv, pytest, Make

## Getting Started

### Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- For a live run only: AWS credentials and at least one Bedrock inference profile

### Installation

```bash
git clone https://github.com/DCode-v05/Kasukabe.git
cd Kasukabe
cd app    && uv sync      # simulation, broker, viewer, console
cd ../engine && uv sync   # the agent engine — its own project and venv
```

For a live run, put `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` and `BEDROCK_INFERENCE_PROFILES` in `app/.env`.

### Running

**See the village in 30 seconds** — open `app/game/static/township-v2.html` straight from disk. No server needed.

**Serve it** (needed for live engine positions):

```bash
cd app
uv run uvicorn game.server:app --port 8400
```

Then open <http://127.0.0.1:8400/township-v2.html> (no `/static/` prefix).

**A live run** (spends real money — check the spend caps first):

```bash
cd app
python -m world.run_genesis --world-mode embedded
```

Everything runs from `app/`, not the repo root.

## Usage

- Open the township map to watch the town: search residents, open place and profile cards, zoom, take roofs off, switch to night.
- Start individual services with `make run-world`, `make run-broker`, `make run-console` and `make run-game` from `app/`.
- Run the evidence gate — it costs nothing and makes no model calls:

```bash
cd app
python -m proof                    # the whole gate, about 2.5 minutes
python -m proof --show failed      # only what failed
python -m proof --skip stories     # fast loop
```

- Run the tests with `uv run pytest` in `app/` and in `engine/`.
- For the full picture — what is finished, what is not, and the path to a live run — read [`docs/HANDOVER.md`](docs/HANDOVER.md).

## Project Structure

```
Kasukabe/
├── app/
│   ├── world/            # The simulation — ticks, agents, ledger, map, tools
│   ├── broker/           # Bedrock gateway; the only package that imports boto3
│   ├── game/             # Viewer server and the township map (static/township-v2.html)
│   │   └── console/      # Operator console
│   ├── research/         # Offline measurement over the trace store
│   ├── proof/            # The evidence gate and its baseline
│   ├── infra/            # Firecracker microVMs, Caddy, systemd, runbook
│   └── tests/
├── engine/               # Vendored agent engine (partly frozen — see ROADMAP.md Law 1)
├── infra/                # Ops paperwork: quota request, launch workflows
├── docs/                 # Handover, proof findings, specs, decisions, run reports
├── DESIGN.md             # The world, economy and rules — cited from the source code
├── ROADMAP.md
└── README.md
```

---

## Contact

<table>
  <tr><td><b>Portfolio:</b> <a href="https://www.denistan.me">Denistan</a></td><td><b>LinkedIn:</b> <a href="https://www.linkedin.com/in/denistanb">denistanb</a></td></tr>
  <tr><td><b>GitHub:</b> <a href="https://github.com/DCode-v05">DCode-v05</a></td><td><b>LeetCode:</b> <a href="https://leetcode.com/u/Denistan_B">Denistan_B</a></td></tr>
  <tr><td colspan="2" align="center"><b>Email:</b> <a href="mailto:denistanb05@gmail.com">denistanb05@gmail.com</a></td></tr>
</table>

Made with ❤️ by **Denistan B**
