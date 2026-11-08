"""Fifteen checks that must pass before a long run starts.

A five-hour run that dies at minute forty because the broker had no
credentials, or that finishes having proved nothing because `skillify` was
never registered, is worse than a run that refuses to start. Every check below
corresponds to something that actually went wrong in this project at least
once - the comments say which.

Each check returns a `Check`: a name, a verdict, what was measured, and - when
it fails - what to do about it. A failing check is not advice. `run_genesis`
refuses to start, and the viewer shows the red line so it can be fixed before
any money is spent.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# A five-hour run at the observed rate needs this much room.
MIN_FREE_DISK_MB = 5_000
# A prompt is a few KB. Anything past this is a context being replayed in
# full rather than summarised, and it ends in Bedrock's "Failed to buffer
# the request body: length limit exceeded".
MAX_HEALTHY_KB_PER_CALL = 64
MIN_FREE_MEMORY_MB = 900
# Every authoring tool must be reachable or the experiment measures nothing.
REQUIRED_TOOLS = (
    "skillify", "toolify", "describe_primitives",
    "quote_action_cost", "revise_skill", "revise_tool", "say",
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    critical: bool = True  # a non-critical failure warns but does not block

    @property
    def status(self) -> str:
        return "PASS" if self.ok else ("FAIL" if self.critical else "WARN")


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)

    @property
    def blocking(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.critical]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.critical]

    @property
    def ready(self) -> bool:
        return not self.blocking

    def render(self) -> str:
        lines = []
        for c in self.checks:
            mark = {"PASS": "[✓]", "WARN": "[!]", "FAIL": "[✗]"}[c.status]
            lines.append(f"  {mark} {c.name}")
            if c.detail:
                lines.append(f"        {c.detail}")
            if not c.ok and c.fix:
                lines.append(f"        → {c.fix}")
        n_ok = sum(1 for c in self.checks if c.ok)
        lines.append("")
        lines.append(f"  {n_ok}/{len(self.checks)} passed"
                     + ("" if self.ready else
                        f" — {len(self.blocking)} blocking, run refused"))
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "passed": sum(1 for c in self.checks if c.ok),
            "total": len(self.checks),
            "checks": [
                {"name": c.name, "ok": c.ok, "status": c.status,
                 "detail": c.detail, "fix": c.fix, "critical": c.critical}
                for c in self.checks
            ],
        }


def _get(url: str, timeout: float = 8.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _broker_spend(broker_url: str) -> dict[str, Any] | None:
    """The governor's own cap and spend, or None if it will not say.

    Run 25's preflight reported the cap out of `app/.env` while the broker
    enforced a different one. Only the governor's number can stop a run,
    so it is the one worth printing - but a preflight that raises because
    an endpoint moved is worse than one that falls back and says which
    source it used. None means "ask the environment instead".
    """
    try:
        body = _get(f"{broker_url}/admin/spend", timeout=4.0)
    except Exception:  # noqa: BLE001 - any failure means "cannot say"
        return None
    return body if isinstance(body, dict) else None


# What a run costs per hour when its calls SUCCEED. Only used when the trace
# store has nothing to measure - see _measured_usd_per_hour.
FALLBACK_USD_PER_HOUR_PER_30_AGENTS = 32.0


def _has_spend_evidence(trace_db: str) -> bool:
    return _spend_sample(trace_db) is not None


def _spend_sample(trace_db: str) -> tuple[float, float] | None:
    """(dollars, seconds) over the most recent stretch of this trace store.

    None when there is nothing to measure. Deliberately reads the tail rather
    than the whole table: an earlier, cheaper, more broken run says nothing
    about what this build costs.
    """
    try:
        conn = sqlite3.connect(f"file:{trace_db}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT SUM(cost_nano_dollars), MIN(created_at), MAX(created_at), COUNT(*) "
                "FROM (SELECT cost_nano_dollars, created_at FROM llm_calls "
                "      ORDER BY rowid DESC LIMIT 500)"
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    if not row or not row[0] or row[3] < 50:
        return None
    nano, first, last, _ = row
    seconds = (last or 0) - (first or 0)
    if seconds < 60:  # too short a window to divide by
        return None
    return nano / 1e9, float(seconds)


def _measured_usd_per_hour(trace_db: str, agents: int) -> float:
    """Dollars per hour for THIS build, measured where possible.

    Scaled by agent count only in the fallback: a real sample already
    reflects however many agents produced it.
    """
    sample = _spend_sample(trace_db)
    if sample is None:
        return FALLBACK_USD_PER_HOUR_PER_30_AGENTS * (max(1, agents) / 30)
    dollars, seconds = sample
    return dollars * 3600.0 / seconds


# world/run_genesis.py's DEFAULT_SIM_AGENT_PORT_BASE. Copied rather than
# imported: preflight must be importable and runnable without dragging in the
# runner, which builds a world when it loads.
ENGINE_PORT_BASE = 9100


def _expected_agent_ids(genesis_path: str, fallback_count: int) -> list[str]:
    """The agent ids this run will use, from the cast file itself.

    Falls back to positional names only if the file cannot be read - the
    check that reads it is check 1, so by here it almost always can.
    """
    try:
        data = json.loads(Path(genesis_path).read_text(encoding="utf-8"))
        ids = [a["agent_id"] for a in data.get("agents", []) if a.get("agent_id")]
        if ids:
            return ids
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [f"a{i:04d}" for i in range(1, fallback_count + 1)]


# How long to wait for a connect probe to answer. Loopback, so the answer
# is immediate or it is not coming; long enough to survive a busy machine,
# short enough that 250 ports check in well under a second.
PORT_PROBE_TIMEOUT_S = 0.25

# Probes in flight at once. Each one is a socket waiting on a timeout and
# nothing else, so this is bounded by politeness rather than by cores.
PORT_PROBE_WORKERS = 64


def _ports_in_use(base: int, count: int) -> list[int]:
    """Which of the engine ports something is already listening on.

    Asked by CONNECTING, because "is somebody listening" is the question,
    and it is the only form of the question that means the same thing on
    both platforms this project runs on.

    THE BIND PROBE THIS REPLACES WAS DEAD ON WINDOWS. It bound with
    SO_REUSEADDR, which waives the TIME_WAIT reservation on POSIX while a
    LISTENing socket still refuses the second bind - so bind() worked as a
    probe there. Windows treats the flag as POSIX treats SO_REUSEPORT and
    lets the bind succeed against a live listener, so the check returned
    an empty list no matter what was running, on the machine every run of
    this project has been launched from. Measured, with a listener on
    *:9187: bind with the flag succeeded on both the wildcard and the
    loopback; connect refused only once the listener closed.

    The original docstring rejected connect() because "a connect()
    succeeds against a port in TIME_WAIT that a new engine could still
    take". It does not: TIME_WAIT is a closed connection with no listener
    behind it, so connect() is refused, which is the answer this check
    wants. `test_a_port_freed_a_moment_ago_is_not_reported_busy` pins it.
    """
    ports = list(range(base, base + max(0, count)))
    if not ports:
        return []

    def listening(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(PORT_PROBE_TIMEOUT_S)
        try:
            # Loopback, not the wildcard: an engine bound to "*:9100" is
            # reachable there, and so is one bound to 127.0.0.1 only, so
            # one probe answers for both of the cases the bind version
            # needed two addresses to cover.
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False  # refused, unreachable or timed out - nobody serving
        else:
            return True
        finally:
            sock.close()

    # Concurrently, because a HEALTHY machine is the worst case: Windows
    # does not answer a connect to an unused loopback port at all, so every
    # free port costs the whole timeout. Serially that was 64.6 seconds for
    # a 250-resident run, and a preflight check that adds a minute to every
    # launch gets skipped, which protects nothing.
    with ThreadPoolExecutor(max_workers=min(PORT_PROBE_WORKERS, len(ports))) as pool:
        answers = list(pool.map(listening, ports))
    return [port for port, busy in zip(ports, answers) if busy]


def _measured_kb_per_call(trace_db: str) -> float:
    """Average TRUE request-body size in this trace store, in KB.

    True, not stored. broker/trace.py caps what it writes at 64 KB, which is
    what stopped a 5.9 GB trace from filling the disk - and which would also
    have hidden the 630 KB average that made the cap necessary, turning this
    check into one that can never fire again. A capped row records the size
    it would have been in `_original_bytes`, and that is the number this
    reads.

    0.0 when there is nothing to measure (a fresh run, an unreadable file),
    which the callers treat as "no evidence" rather than "healthy".
    """
    try:
        conn = sqlite3.connect(f"file:{trace_db}?mode=ro", uri=True)
        try:
            # The tail, not the whole table: an old run's rows say nothing
            # about this build, and a full scan of a multi-GB table is not
            # something a preflight check may do.
            # SUCCESSFUL calls only.
            #
            # A prompt that was refused for being too large is already
            # counted as a failure; averaging it in here measures the
            # symptom instead of the trend. Observed on a fresh town:
            # successes averaged 30 KB - perfectly healthy - while failures
            # averaged 1,942 KB, and the combined average of 991 KB blocked
            # a run that was fine. What this check is for is a run whose
            # WORKING prompts are creeping toward the limit, because that
            # predicts the failures that have not happened yet.
            rows = conn.execute(
                "SELECT request_body FROM llm_calls WHERE success = 1 "
                "ORDER BY rowid DESC LIMIT 200"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - no evidence is a real answer here
        return 0.0

    sizes: list[int] = []
    for (body,) in rows:
        if body is None:
            continue
        size = len(body)
        if "_original_bytes" in body:
            try:
                size = int(json.loads(body)["_original_bytes"])
            except (ValueError, KeyError, TypeError):
                pass  # a truncated row we cannot parse still counts at its
                      # stored size, which understates rather than invents
        sizes.append(size)
    if not sizes:
        return 0.0
    return sum(sizes) / len(sizes) / 1024.0


def _free_mb(path: str = ".") -> int:
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return 10 ** 9


def run(
    *,
    broker_url: str,
    world_url: str | None = None,
    expected_agents: int,
    genesis_path: str,
    home_root: str,
    trace_db: str,
    engine_repo: str,
    ports_must_be_free: bool = True,
    engines_running: bool = False,
) -> Preflight:
    """Everything checkable BEFORE the engines start, plus the two that can
    only be checked once the world is up (passed `world_url` then)."""
    pf = Preflight()
    add: Callable[[Check], None] = pf.checks.append

    # 1 ── the cast file exists and parses
    try:
        genesis = json.loads(Path(genesis_path).read_text(encoding="utf-8"))
        pop = genesis.get("population") or len(genesis.get("agents") or [])
        add(Check("Genesis cast loads", True, f"{genesis_path} — {pop} agents"))
    except Exception as exc:  # noqa: BLE001
        add(Check("Genesis cast loads", False, f"{type(exc).__name__}: {exc}",
                  f"uv run python -m world.make_genesis_subset {expected_agents}"))
        genesis, pop = {}, 0

    # 2 ── the cast is the size the run asked for
    add(Check(
        "Cast size matches the run", pop == expected_agents,
        f"genesis has {pop}, run wants {expected_agents}",
        f"uv run python -m world.make_genesis_subset {expected_agents}",
    ))

    # 3 ── the $1000 USD invariant. A cast whose wallets do not sum correctly
    #      is rejected by the ledger at the first checkpoint, hours in.
    total = ((genesis.get("usd_ledger") or {}).get("total_cents"))
    add(Check(
        "USD ledger invariant ($1000)", total == 100_000,
        f"total_cents = {total}", "rebuild the genesis subset — the wallets do not sum",
    ))

    # 4 ── broker reachable
    try:
        health = _get(f"{broker_url}/health")
        live = health.get("status") == "ok"
        add(Check("Broker is up", live, f"{broker_url} — {health}",
                  f"uv run uvicorn broker.app:app --port {broker_url.rsplit(':', 1)[-1]}"))
    except Exception as exc:  # noqa: BLE001
        add(Check("Broker is up", False, f"{type(exc).__name__}: {exc}",
                  "start the broker before the run"))
        health = {}

    # 5 ── it is really Bedrock, not a stub
    add(Check(
        "Broker is on real Bedrock", health.get("inference") == "bedrock",
        f"inference = {health.get('inference')!r}",
        "check AWS credentials in app/.env",
    ))

    # 6 ── a spend cap exists. Without one a five-hour run is unbounded.
    #
    # ASKED OF THE BROKER, not read out of the operator's environment.
    #
    # Run 25: preflight printed "SPEND_CAP_USD = 10.00", read from
    # `app/.env`, while the broker had been started with $20.00 in its own
    # environment and was enforcing that. The checklist and the enforcer
    # disagreed by a factor of two and neither knew.
    #
    # The harmless direction is the one that happened. The dangerous one
    # is a checklist reporting the .env's $10 while the broker runs on its
    # 400.0 default - an operator who read the line and started a long run
    # would be forty times out. Only one number can stop the run, and it
    # is the governor's, so that is the number to show.
    #
    # `/admin/spend` is the governor itself: `cap_usd` is the field
    # `SpendGovernor` was constructed with. The env is kept as a fallback
    # for the case where the broker is up but this endpoint is not - a
    # preflight that cannot answer at all is worse than one answering from
    # a second-best source and saying so.
    cap: str | None = None
    cap_source = ""
    spend_status = _broker_spend(broker_url)
    if spend_status is not None and spend_status.get("cap_usd") is not None:
        cap = f"{float(spend_status['cap_usd']):.2f}"
        cap_source = " (from the broker, which is what enforces it)"
    else:
        cap = os.environ.get("SPEND_CAP_USD")
        if cap is None:
            env = Path(".env")
            if env.exists():
                for line in env.read_text(encoding="utf-8").splitlines():
                    if line.startswith("SPEND_CAP_USD"):
                        cap = line.split("=", 1)[1].strip()
        cap_source = " (from this shell — the BROKER did not answer, so this may not be the cap that stops the run)"
    ok_cap = bool(cap) and float(cap or 0) > 0
    add(Check("Spend cap is set", ok_cap, f"SPEND_CAP_USD = {cap}{cap_source}",
              "set SPEND_CAP_USD in app/.env — a long run without one is unbounded"))

    # 6b ── and the broker has not already spent most of it.
    #
    # The governor resumes from `SUM(cost_nano_dollars)` over the trace
    # store it opens, so pointing a new run at a used database starts it
    # part-spent. Run 25 was given a fresh store for exactly this reason,
    # and nothing in the checklist would have said so if it had not been.
    if spend_status is not None:
        already = float(spend_status.get("spent_usd") or 0.0)
        cap_usd = float(spend_status.get("cap_usd") or 0.0)
        headroom = cap_usd - already
        add(Check(
            "The cap has room left",
            cap_usd > 0 and headroom > 0.5 * cap_usd,
            f"${already:.2f} of ${cap_usd:.2f} already spent — ${headroom:.2f} left",
            "the governor resumes from the trace store it was given; point "
            "the broker at a fresh TRACE_SQLITE_PATH for a new run",
            critical=False,
        ))

    # 7 ── the cap is big enough to finish, at what this run actually costs.
    #
    # This was a hardcoded $6.60/hour at 30 agents, and it was wrong by five
    # times. The number came from a run where most calls FAILED - and a failed
    # call is free, so the more broken the run, the cheaper the estimate said
    # the next one would be. Once the prompts were fixed and calls started
    # succeeding, the real rate was $32/hour: a $30 cap bought 55 minutes
    # while this check called it 4.5 hours.
    #
    # Same lesson as the disk check above. Measure it.
    if ok_cap:
        rate = _measured_usd_per_hour(trace_db, expected_agents)
        hours = float(cap) / rate
        add(Check(
            "Cap covers the intended run", hours >= 5.0,
            f"${cap} ≈ {hours:.1f}h at ${rate:.2f}/hour"
            + ("" if _has_spend_evidence(trace_db) else " (estimated - no trace history yet)"),
            f"raise SPEND_CAP_USD to about ${rate * 5:.0f} for 5 hours, "
            f"or run fewer agents",
            critical=False,
        ))

    # 8 ── memory, from what engines on THIS machine actually cost.
    #
    # The constant cannot be right everywhere. The same code measures ~550 MB
    # per engine under load on Linux (/proc VmRSS, in a container) and 39 MB
    # on macOS (ps RSS, 30 engines at game-day 4) - not a disagreement about
    # the engine but about page sharing between thirty children of one
    # parent. Budget for Linux and this check refuses a run macOS handles in
    # 1.2 GB; budget for macOS and it exhausts a Linux box in hour three.
    #
    # So: measure the engines that are running, and fall back to the constant
    # only when there is nothing to measure - which is the ordinary pre-boot
    # case, where erring high is the safe direction.
    from world.memory_budget import RESERVED_SYSTEM_MB, available_mb
    from world.memory_budget import check as mem_check
    from world.memory_budget import measured_mb_per_engine

    # Three sources, best first: engines running right now, the last reading
    # taken on this machine, then the constant. The constant alone refused a
    # ten-engine run here at "5500 MB needed" while the same ten engines had
    # just been measured at 33 MB each.
    from world.memory_budget import last_measurement

    sample = measured_mb_per_engine()
    if sample:
        per_engine, basis = sample[1], (
            f"{sample[1]} MB/engine measured from {sample[0]} running engine(s)")
    elif (remembered := last_measurement()) is not None:
        per_engine, basis = remembered, (
            f"{remembered} MB/engine, last measured on this machine")
    else:
        per_engine, basis = None, "never measured here — using the conservative estimate"

    verdict = mem_check(expected_agents, backend="process", mb_per_engine=per_engine)
    add(Check(
        "Memory fits the engine count",
        # `ok` is the runner's own verdict and nothing else. The escape
        # hatch used to sit here, in `ok`, which meant `status` returned
        # PASS through it - so the intent recorded in the comment below
        # was never actually implemented, and a 20-engine run printed
        # "[✓] Memory fits the engine count" one second before the runner
        # refused it on the identical numbers. The hatch belongs in
        # `critical`, where it downgrades a blocking FAIL to a WARN
        # without ever claiming the run fits.
        verdict.fits,
        # The usable figure, not free: free is what the old refusal quoted
        # and it is 512 MB larger than the number being decided on.
        f"{verdict.needed_mb} MB needed, {verdict.usable_mb} MB usable "
        f"({verdict.available_mb} MB free less {RESERVED_SYSTEM_MB} reserved) · {basis}",
        f"close applications, or run about {verdict.affordable} agents",
        # A run that only clears the bar through the escape hatch is a WARN,
        # not a PASS. It read PASS at "1800 MB needed, 1280 MB free", which
        # is the one wording guaranteed to be misread as "it fits".
        critical=available_mb() <= MIN_FREE_MEMORY_MB,
    ))

    # 9 ── disk, sized from what THIS trace store actually writes.
    #
    # The estimate here used to be a constant, 61 KB per call, taken from a
    # short healthy run. A 30-agent, six-hour run then averaged 630 KB per
    # call - ten times the guess - and wrote 5.9 GB onto a laptop with 1.2 GB
    # free. The check passed the whole way down. A guessed denominator is
    # exactly as dangerous as no check at all, so this one now measures.
    free = _free_mb(".")
    kb_per_call = _measured_kb_per_call(trace_db)
    # ~1,600 calls/hour at 30 agents, from the same run: 9,845 calls / 6h.
    calls_5h = 1_600 * 5 * max(1, expected_agents) / 30
    projected_mb = int(kb_per_call * calls_5h / 1024)
    add(Check(
        "Disk has room for the trace store",
        free >= max(MIN_FREE_DISK_MB, projected_mb + 2_000),
        f"{free} MB free; a 5h run projects {projected_mb} MB "
        f"at the {kb_per_call:.0f} KB/call this store is averaging",
        "archive data/kasukabe.db, or set KASUKABE_TRACE_BODY_LIMIT",
    ))

    # 10 ── the prompts themselves. The disk check above is a symptom; this
    # is the disease. A body averaging hundreds of KB is a context that is
    # being replayed rather than summarised, and it fails outright once it
    # passes Bedrock's request limit - 506 of 890 failures in the six-hour
    # run were "request body too large" or the 400 that follows it.
    if kb_per_call:
        add(Check(
            "Prompt size is sane", kb_per_call <= MAX_HEALTHY_KB_PER_CALL,
            f"{kb_per_call:.0f} KB average request body on calls that SUCCEEDED "
            f"(healthy is under {MAX_HEALTHY_KB_PER_CALL} KB)",
            "the working-memory replay in engine/src/learning_centre/"
            "pipeline.py is unbounded — see docs/TEST_RUN.md",
        ))

    # 11 ── the trace db is writable and has the failed_at column
    try:
        Path(trace_db).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(trace_db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
        conn.close()
        has = "failed_at" in cols if cols else True  # fresh db is fine
        add(Check("Trace store writable, hop column present", has,
                  f"{trace_db} — llm_calls has {len(cols)} columns",
                  # INFRA-22. The remedy used to say "apply supabase/
                  # migrations/20260825120000_llm_calls_failed_at.sql" - a
                  # POSTGRES migration, for a SQLITE file, from a directory
                  # nothing applies (the trace store is SQLite by DESIGN
                  # section 9 and `migrations/` is dormant). An operator who
                  # followed it would have run psql against a database this
                  # project does not have and still had a missing column.
                  #
                  # `SqliteClient.ensure_schema` adds the column additively
                  # on open, so the real remedy is to let the writer open
                  # the file - which is what restarting it does.
                  "restart the broker; SqliteClient.ensure_schema adds the "
                  "column on open (INFRA-22)"))
    except Exception as exc:  # noqa: BLE001
        add(Check("Trace store writable, hop column present", False,
                  f"{type(exc).__name__}: {exc}", "check the path and permissions"))

    # 12 ── each engine's home root is writable
    try:
        root = Path(home_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add(Check("SEPTEMBER_HOME root is writable", True, str(root)))
    except Exception as exc:  # noqa: BLE001
        add(Check("SEPTEMBER_HOME root is writable", False,
                  f"{type(exc).__name__}: {exc}", f"check permissions on {home_root}"))

    # 13 ── the ports the engines will bind are free.
    #
    # A previous run's engines outlive `pkill` on the runner for as long as
    # its graceful shutdown takes, and they hold ports 9100+. Start the next
    # run inside that window and every engine dies with:
    #
    #   ERROR: [Errno 48] error while attempting to bind on ('0.0.0.0', 9100)
    #
    # which surfaces to the operator as the wholly uninformative "exited with
    # code 3", thirty times. Observed: 23 of 30 engines dead, world ticking a
    # town of statues, and nothing anywhere naming a port.
    # ...and only before boot. Served live from inside a running world, this
    # check finds that world's OWN thirty engines and calls them a conflict -
    # a red line on the panel of a perfectly healthy run.
    busy = _ports_in_use(ENGINE_PORT_BASE, expected_agents) if ports_must_be_free else []
    add(Check(
        "Engine ports are free", not busy,
        f"{ENGINE_PORT_BASE}–{ENGINE_PORT_BASE + expected_agents - 1}"
        + (f" — already listening: {busy[:6]}" if busy
           else (" — all free" if ports_must_be_free
                 else " — bound by this run's own engines, as expected")),
        "a previous run is still shutting down; wait for it, or "
        "`pkill -f run_genesis` and give it a few seconds",
    ))

    # 13 ── the engine is actually there and has its own venv
    repo = Path(engine_repo)
    # THIS platform's layout only. A venv keeps its interpreter at
    # .venv/bin/python on POSIX and .venv/Scripts/python.exe on Windows, and
    # a venv is not portable between the two - so probing "whichever exists"
    # would happily pass on a Windows host that is about to try executing a
    # Linux binary. run_genesis.default_engine_command_builder resolves the
    # interpreter by exactly this rule; this check has to agree with it or it
    # is checking for a file the run will never use.
    venv = (repo / ".venv" / "Scripts" / "python.exe") if os.name == "nt" else (
        repo / ".venv" / "bin" / "python")
    add(Check("Engine repo and venv present", venv.exists(),
              f"{venv}", f"cd {engine_repo} && uv sync"))

    # 13/14/15 ── only checkable once the world is up
    # ── Session logging will work, asked BEFORE the run starts ────────
    #
    # The live check further down proves the logs are being written. This one
    # proves they CAN be, and it is the one that matters: after the engines
    # are up the run has already started, and a checklist that only discovers
    # a problem once you are spending money is not a pre-flight check.
    #
    # It does not guess. It creates each engine's SEPTEMBER_HOME and writes a
    # byte to the exact path that engine will append its session log to, then
    # takes it back out. A permission, a full disk or a read-only mount all
    # fail here, before a single engine boots.
    home_root = Path(os.environ.get("KASUKABE_HOME_ROOT", home_root)).expanduser()
    unwritable: list[str] = []
    probed = 0
    for agent_id in _expected_agent_ids(genesis_path, expected_agents):
        log = home_root / agent_id / "session_log.jsonl"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            existed = log.exists()
            with log.open("a", encoding="utf-8"):
                pass
            if not existed:
                # Created by the probe, so remove it: an empty file here
                # would make the live check below read "logging" for an
                # engine that has never written a line.
                log.unlink(missing_ok=True)
            probed += 1
        except OSError as exc:
            unwritable.append(f"{agent_id} ({exc.strerror or exc})")

    add(Check(
        "Session logging is ready for every engine", not unwritable and probed > 0,
        f"{probed}/{expected_agents} SEPTEMBER_HOMEs accept a session log under "
        f"{home_root}" + (f" · cannot write: {unwritable[:4]}" if unwritable else ""),
        "each engine appends its reasoning to "
        "<home_root>/<agent_id>/session_log.jsonl — without it the run leaves "
        "no record of WHY any agent acted",
    ))

    # ── The broker is writing to the trace store this reads ───────────
    #
    # On a running world with a fresh trace file, an EMPTY llm_calls means
    # the broker still holds the previous file by inode - delete or rename
    # a trace store out from under a running broker and it keeps writing to
    # the unlinked file forever, perfectly happily. Cost three restarts to
    # notice, because every other surface looked healthy: engines alive,
    # world ticking, agents acting, and a report that said the town had
    # never made an API call.
    if engines_running:
        rows = 0
        try:
            conn = sqlite3.connect(f"file:{trace_db}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - a missing table counts as zero
            rows = 0
        add(Check(
            "Broker is writing to this trace store", rows > 0,
            f"{rows} calls recorded in {trace_db}",
            "the broker is holding a deleted or renamed trace file by inode — "
            "restart the broker after moving data/kasukabe.db",
        ))

    # ── Every engine is keeping a session log ─────────────────────────
    #
    # Runs once the engines are up, never before: a log that does not exist
    # yet, thirty seconds before the engines that write it have started, is
    # not a fault. The runner's pre-boot call therefore leaves
    # `engines_running` False and this check sits out; the live route on a
    # running world passes True and it is asked properly.
    if engines_running:
        #
        # The session log is the only place an agent's REASONING is written
        # down - input, what the model thought, the tool calls it made. The
        # trace store knows what each call cost and nothing about why it was
        # made. A run whose engines are not writing these produces numbers
        # nobody can interpret afterwards, and the gap is invisible while it
        # is happening: every other panel looks perfectly normal.
        home_root = Path(os.environ.get("KASUKABE_HOME_ROOT", "/tmp/kasukabe-agents")).expanduser()
        homes = sorted(d for d in home_root.glob("*") if d.is_dir()) if home_root.is_dir() else []
        logging_now = [d.name for d in homes if (d / "session_log.jsonl").is_file()]
        silent = [d.name for d in homes if not (d / "session_log.jsonl").is_file()]
        add(Check(
            "Every engine is writing a session log",
            bool(homes) and len(logging_now) >= expected_agents,
            f"{len(logging_now)}/{expected_agents} engines have a session_log.jsonl "
            f"under {home_root}"
            + (f" · silent: {silent[:6]}" if silent else ""),
            "an engine with no session log leaves no record of WHY it acted — "
            "check that it booted and that its SEPTEMBER_HOME is writable",
        ))


    if world_url:
        try:
            rows = _get(f"{world_url}/tools/registry")
            names = {r["name"] for r in rows}
            missing = [t for t in REQUIRED_TOOLS if t not in names]
            add(Check(
                "Authoring tools reachable by agents", not missing,
                f"{len(rows)} tools served; missing: {missing or 'none'}",
                "an agent that cannot call skillify/toolify makes the run pointless",
            ))
        except Exception as exc:  # noqa: BLE001
            add(Check("Authoring tools reachable by agents", False,
                      f"{type(exc).__name__}: {exc}", "is the world up?"))

        try:
            vit = _get(f"{world_url}/world/vitals")
            add(Check("World reports live vitals", "health" in vit,
                      f"tick {vit.get('tick')}, "
                      f"{(vit.get('health') or {}).get('alive')} alive",
                      "the viewer's panels read this"))
        except Exception as exc:  # noqa: BLE001
            add(Check("World reports live vitals", False,
                      f"{type(exc).__name__}: {exc}", "check /world/vitals"))

        try:
            eng = _get(f"{world_url}/world/engines")
            alive, expected = eng.get("alive", 0), eng.get("expected", 0)
            add(Check("Every engine booted", alive == expected and alive > 0,
                      f"{alive}/{expected} alive",
                      "check the run log for boot failures"))
        except Exception as exc:  # noqa: BLE001
            add(Check("Every engine booted", False,
                      f"{type(exc).__name__}: {exc}", "check /world/engines"))

    return pf
