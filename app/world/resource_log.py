"""What each resident's engine costs the machine, per agent, sample by sample.

    docs/runs/series/<run>/resources.jsonl   one JSON object per line

WHY PER AGENT. Every memory figure this project has ever acted on is a
single global average - `MB_PER_ENGINE_PROCESS = 550`, and
`memory_budget.measured_mb_per_engine()` reporting the mean across
whatever is running. That answers "will thirty engines fit" and nothing
else. It cannot answer:

  * WHICH resident is expensive. n09 was measured writing 18,547
    characters of prose per turn against a few hundred for others, and
    nothing in any log said what that cost.
  * WHETHER an engine grows. The working-memory flush reached 99,591
    tokens in a single call, so something accumulates - and whether the
    process grows with it has never been recorded.
  * WHY a run died. A run ending in ReadError looks the same whether the
    engine crashed, was swapped out, or hung.

AND ON THIS MACHINE THE MEAN IS NOT EVEN A MEASUREMENT.
`measured_mb_per_engine` shells out to `ps`, which Windows does not have,
so it returns None and preflight falls back to the 550 MB constant -
measured on Linux, under load, on a different machine. Its own comment
says so: "The constant is not wrong; it is wrong HERE." Every capacity
decision made on this box has been made against a number from another
one.

NO NEW DEPENDENCY. `memory_budget` has a standing no-psutil rule and this
module keeps it: Win32 through ctypes, `ps` everywhere else. A measurement
tool that needs a package installed before it can measure is a tool that
does not run on the machine you most need it on.

IT NEVER INTERVENES. It does not kill, throttle or refuse. `preflight`
owns refuse-before-boot and `memory_budget` owns the arithmetic; this
records what happened. A sampler that also intervened would be changing
what it measures.

IT NEVER RAISES. The numbers are the least important thing in the
process, and a sampler that takes down the run it was measuring has cost
more than it could ever be worth.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# CPU percent needs two readings to be meaningful - it is a rate, and a
# single sample of cumulative process time is not one. Kept per pid.
_LAST_CPU: dict[int, tuple[float, float]] = {}


def _windows_reading(pid: int) -> dict[str, float] | None:
    """RSS and cumulative CPU seconds for one process, via Win32.

    `GetProcessMemoryInfo().WorkingSetSize` is the Windows name for RSS:
    physical memory the process actually occupies. `GetProcessTimes`
    returns kernel and user time in 100-nanosecond units.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        # Gone, or not ours to look at. Both are "no reading", and the
        # caller records the agent as not alive rather than skipping it.
        return None

    try:
        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if not psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return None

        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        cpu_seconds = 0.0
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            def _seconds(ft: wintypes.FILETIME) -> float:
                # 100-nanosecond units, split across two 32-bit halves.
                return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7

            cpu_seconds = _seconds(kernel) + _seconds(user)

        return {
            "rss_mb": round(counters.WorkingSetSize / (1024 * 1024), 1),
            "peak_rss_mb": round(counters.PeakWorkingSetSize / (1024 * 1024), 1),
            "cpu_seconds": cpu_seconds,
        }
    finally:
        kernel32.CloseHandle(handle)


def _posix_reading(pid: int) -> dict[str, float] | None:
    """The same two numbers from `ps`, which every platform here but
    Windows has - the approach memory_budget already uses, for the same
    no-psutil reason."""
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=,time=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    parts = out.split()
    if len(parts) < 2 or not parts[0].isdigit():
        return None

    # ps prints elapsed CPU as [[dd-]hh:]mm:ss.
    clock = parts[1].replace("-", ":").split(":")
    try:
        seconds = 0.0
        for piece in clock:
            seconds = seconds * 60 + float(piece)
    except ValueError:
        seconds = 0.0
    rss_mb = round(int(parts[0]) / 1024, 1)
    return {"rss_mb": rss_mb, "peak_rss_mb": rss_mb, "cpu_seconds": seconds}


def sample_process(pid: Any) -> dict[str, float] | None:
    """One process's cost right now, or None if it is not there.

    `cpu_percent` is derived from the change in cumulative CPU time since
    this pid was last sampled, so the FIRST sample of any process reports
    0.0 - a rate needs two readings and reporting a made-up one would be
    worse than reporting none.

    Never raises. A dead engine is an ordinary thing to sample, because
    the watchdog restarts them.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None

    try:
        reading = (
            _windows_reading(pid) if sys.platform == "win32" else _posix_reading(pid)
        )
    except Exception:  # noqa: BLE001 - see the module docstring: never raise
        return None
    if reading is None:
        return None

    now = time.monotonic()
    cpu_seconds = reading.pop("cpu_seconds", 0.0)
    previous = _LAST_CPU.get(pid)
    _LAST_CPU[pid] = (now, cpu_seconds)
    if previous is None or now <= previous[0]:
        reading["cpu_percent"] = 0.0
    else:
        elapsed = now - previous[0]
        reading["cpu_percent"] = round(
            max(0.0, (cpu_seconds - previous[1]) / elapsed) * 100, 1
        )
    return reading


def children_by_parent() -> dict[int, list[int]]:
    """pid -> its direct children, for every process on the machine.

    Built ONCE per sample and passed down, never once per agent: fifty
    agents would otherwise mean fifty process-table walks a tick, which
    would cost more than the town it is measuring.

    An empty map is the honest answer when the table cannot be read, and
    the caller falls back to charging an agent for its own process only -
    an undercount that is announced by `processes: 1` in the row rather
    than hidden.
    """
    tree: dict[int, list[int]] = {}
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["wmic", "process", "get", "ProcessId,ParentProcessId", "/format:csv"],
                capture_output=True, text=True, timeout=20, check=False,
            ).stdout
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 3 or not parts[-1].isdigit() or not parts[-2].isdigit():
                    continue
                # csv is Node,ParentProcessId,ProcessId
                tree.setdefault(int(parts[-2]), []).append(int(parts[-1]))
        else:
            out = subprocess.run(
                ["ps", "-axo", "ppid,pid"],
                capture_output=True, text=True, timeout=20, check=False,
            ).stdout
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    tree.setdefault(int(parts[0]), []).append(int(parts[1]))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return tree


def sample_tree(pid: Any, tree: "dict[int, list[int]] | None" = None) -> dict[str, Any] | None:
    """What a process and everything it spawned cost, together.

    WHY A TREE AND NOT A PROCESS. `python -m src.sim.sim_main` spawns a
    child that does the work. Measured on the fifty-agent run: a hundred
    matching processes for fifty agents, the one the supervisor holds
    weighing about 4.4 MB and its child about 79 MB. The first version of
    this module sampled the supervisor's pid and wrote

        {"agent_id": "n01", "rss_mb": 4.4}

    which is the launcher, not the resident - every number eighteen times
    too small, and a total that would have claimed fifty engines cost 220
    MB when they cost about 4 GB.

    CPU is summed the same way. `processes` says how many were counted,
    so a row that only found the launcher announces itself rather than
    passing as a small engine.
    """
    try:
        root = int(pid)
    except (TypeError, ValueError):
        return None
    own = sample_process(root)
    if own is None:
        return None

    if tree is None:
        tree = children_by_parent()

    total_rss = own["rss_mb"]
    total_peak = own["peak_rss_mb"]
    total_cpu = own["cpu_percent"]
    counted = 1

    seen = {root}
    queue = list(tree.get(root, ()))
    while queue:
        child = queue.pop()
        if child in seen:
            continue  # a cycle would hang the sampler; pids get reused
        seen.add(child)
        reading = sample_process(child)
        if reading is not None:
            total_rss += reading["rss_mb"]
            total_peak += reading["peak_rss_mb"]
            total_cpu += reading["cpu_percent"]
            counted += 1
        queue.extend(tree.get(child, ()))

    return {
        "rss_mb": round(total_rss, 1),
        "peak_rss_mb": round(total_peak, 1),
        "cpu_percent": round(total_cpu, 1),
        "processes": counted,
    }


class ResourceSampler:
    """Writes one line per agent per sample.

    `pids` is read fresh on every sample rather than cached, because the
    watchdog restarts silent engines and a restarted engine has a new pid.
    A cached roster would go on reporting a process that has gone, which
    is the same class of defect as a stale conversation pointer.
    """

    def __init__(self, path: str | Path, pids: dict[str, Any]) -> None:
        self.path = Path(path)
        self.pids = pids

    def sample(self, tick: int) -> None:
        """Record every engine's cost right now. Never raises."""
        at = time.time()
        lines = []
        # Once per sample, not once per agent. See children_by_parent.
        tree = children_by_parent()
        for agent_id, pid in list(self.pids.items()):
            if not agent_id:
                continue
            reading = sample_tree(pid, tree)
            row = {
                "agent_id": str(agent_id),
                "tick": tick,
                "at": at,
                # A dead engine is RECORDED as dead, not skipped. Silence
                # and death look identical in a log that omits what it
                # cannot read, and run 16 lost a resident's whole
                # existence to exactly that.
                "alive": reading is not None,
                "rss_mb": reading["rss_mb"] if reading else 0,
                "peak_rss_mb": reading["peak_rss_mb"] if reading else 0,
                "cpu_percent": reading["cpu_percent"] if reading else 0.0,
                # How many processes this agent's total is made of. A row
                # reading 1 found only the launcher and is an undercount
                # saying so, rather than a small engine.
                "processes": reading["processes"] if reading else 0,
            }
            lines.append(json.dumps(row))
        if not lines:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError:
            # See the module docstring. The numbers are the least
            # important thing in the process.
            pass


def summarise(path: str | Path) -> dict[str, Any]:
    """What the log says, for a human and for the run report.

    Answers the question preflight cannot: how far does THIS machine
    actually go, from what the engines really cost on it rather than from
    a constant measured on another one.
    """
    peaks: dict[str, float] = {}
    cpu: dict[str, float] = {}
    samples = 0
    deaths: dict[str, int] = {}

    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    # The file is written by a live run that may be
                    # killed mid-line.
                    continue
                agent = str(row.get("agent_id") or "")
                if not agent:
                    continue
                samples += 1
                peaks[agent] = max(peaks.get(agent, 0), row.get("rss_mb") or 0)
                cpu[agent] = max(cpu.get(agent, 0.0), row.get("cpu_percent") or 0.0)
                if row.get("alive") is False:
                    deaths[agent] = deaths.get(agent, 0) + 1
    except OSError:
        pass

    if not peaks:
        return {"engines": 0, "samples": 0, "peak_rss_mb": {}, "heaviest": None,
                "total_peak_rss_mb": 0, "mean_peak_rss_mb": 0, "peak_cpu_percent": {},
                "samples_with_a_dead_engine": {}}

    heaviest = max(peaks, key=lambda a: peaks[a])
    return {
        "engines": len(peaks),
        "samples": samples,
        "peak_rss_mb": peaks,
        "peak_cpu_percent": cpu,
        "heaviest": heaviest,
        "total_peak_rss_mb": round(sum(peaks.values()), 1),
        "mean_peak_rss_mb": round(sum(peaks.values()) / len(peaks), 1),
        "samples_with_a_dead_engine": deaths,
    }


def main(path: str) -> int:
    report = summarise(path)
    if not report["engines"]:
        print(f"{path}: no readings.")
        return 1

    print(f"\n{'=' * 66}\nWHAT THE TOWN COST THE MACHINE  {path}\n{'=' * 66}")
    print(f"{report['engines']} engines, {report['samples']} samples\n")
    print(f"{'AGENT':<10}{'PEAK RSS MB':>14}{'PEAK CPU %':>13}{'DEAD SAMPLES':>15}")
    for agent in sorted(report["peak_rss_mb"], key=lambda a: -report["peak_rss_mb"][a]):
        print(
            f"{agent:<10}{report['peak_rss_mb'][agent]:>14}"
            f"{report['peak_cpu_percent'].get(agent, 0):>13}"
            f"{report['samples_with_a_dead_engine'].get(agent, 0):>15}"
        )

    mean = report["mean_peak_rss_mb"]
    print(f"\nheaviest resident   {report['heaviest']} at {report['peak_rss_mb'][report['heaviest']]} MB")
    print(f"mean peak per engine {mean} MB")
    print(f"whole town at peak   {report['total_peak_rss_mb']} MB")

    # The question the operator actually has, answered from measurement.
    from world.memory_budget import MB_PER_ENGINE_PROCESS, available_mb

    free = available_mb()
    if mean > 0 and free:
        print(f"\nmeasured here, not assumed. preflight's constant is "
              f"{MB_PER_ENGINE_PROCESS} MB/engine;")
        print(f"these engines measured {mean} MB. With {free} MB free, this machine")
        print(f"fits about {int(free // mean)} engines at the measured size.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "resources.jsonl"))
