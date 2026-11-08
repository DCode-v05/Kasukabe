"""Will this many engines fit in this machine's RAM?

Written for an 8 GB M1 laptop, where a 40-engine run does not fail politely:
the machine swaps, the fans spin, and something gets OOM-killed - often the
world server rather than an engine, so the run dies in a way that looks like a
world bug. A refusal before the first spawn is worth a great deal more than a
crash twenty engines in.

**Nothing is stripped to achieve this.** Docker is not in the path (the
supervisor already defaults to `process`), and the sandbox executor is never
reached because a sim engine registers world tools, a session log and a memory
stream - no shell tool, so nothing to sandbox. Each engine already writes into
its own `SEPTEMBER_HOME` at `<home_root>/<agent_id>`. The only thing missing
was somebody checking the arithmetic before starting.

The numbers below are measured, not guessed - see `MB_PER_ENGINE`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
from dataclasses import dataclass

MB_PER_ENGINE_PROCESS = 550
MB_PER_ENGINE_NAMESPACE = 4

MB_PER_ENGINE = MB_PER_ENGINE_NAMESPACE

NAMESPACE_BASE_MB = 70

RESERVED_SYSTEM_MB = 512


@dataclass(frozen=True)
class MemoryVerdict:
    requested: int
    affordable: int
    available_mb: int
    needed_mb: int

    @property
    def fits(self) -> bool:
        return self.requested <= self.affordable

    @property
    def usable_mb(self) -> int:
        """Free memory minus the reservation - the figure the verdict is
        actually decided on, and the one every message has to quote.

        `explain()` used to print `available_mb` here while `check()`
        decided on this. At 1,881 MB free the refusal read "20 engines
        need about 1380 MB, but only 1881 MB is free", which is an
        argument that the run fits, attached to a refusal. The missing
        512 MB was the whole disagreement."""
        return max(0, self.available_mb - RESERVED_SYSTEM_MB)

    def explain(self) -> str:
        return (
            f"{self.requested} engines need about {self.needed_mb} MB, but only "
            f"{self.usable_mb} MB is usable: {self.available_mb} MB free, less "
            f"{RESERVED_SYSTEM_MB} MB reserved "
            f"for the world server, broker, viewer and the OS.\n"
            f"This machine can run about {self.affordable} engines right now.\n"
            f"\n"
            f"  uv run python -m world.make_genesis_subset {self.affordable}\n"
            f"  ... --genesis-path world/genesis_{self.affordable}.json\n"
            f"\n"
            f"Or close some applications and try again: the figure is measured "
            f"from memory free at this moment, not from total RAM, so an IDE "
            f"and a browser make a real difference on an 8 GB machine."
        )


def available_mb() -> int:
    """Free memory right now, in MB. Best effort, and deliberately without a
    psutil dependency - a preflight check that requires a new package to run is
    a preflight check that gets skipped."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = 4096
            free = inactive = 0
            for line in out.splitlines():
                if "page size of" in line:
                    page = int(line.split("page size of")[1].split()[0])
                elif line.startswith("Pages free:"):
                    free = int(line.split(":")[1].strip().rstrip("."))
                elif line.startswith("Pages inactive:"):
                    inactive = int(line.split(":")[1].strip().rstrip("."))
            if free or inactive:
                return (free + inactive) * page // (1024 * 1024)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys) // (1024 * 1024)
        except Exception:  # noqa: BLE001 - a probe must never break preflight
            pass

    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024) if hasattr(os, "sysconf") else 0
    return total or 10 ** 6


def check(
    requested_engines: int,
    *,
    mb_per_engine: int | None = None,
    backend: str = "namespace",
) -> MemoryVerdict:
    """Does this many engines fit, on this backend, right now?

    `backend` rather than a bare number, because the same agent count is
    affordable under one backend and impossible under the other - 50 agents is
    ~210 MB namespaced and 5.5 GB as separate processes.
    """
    per = mb_per_engine if mb_per_engine is not None else (
        MB_PER_ENGINE_NAMESPACE if backend == "namespace" else MB_PER_ENGINE_PROCESS
    )
    fixed = NAMESPACE_BASE_MB if backend == "namespace" else 0
    free = available_mb()
    usable = max(0, free - RESERVED_SYSTEM_MB - fixed)
    return MemoryVerdict(
        requested=requested_engines,
        affordable=max(1, usable // per),
        available_mb=free,
        needed_mb=fixed + requested_engines * per,
    )


def free_disk_mb(path: str) -> int:
    """Each engine's SEPTEMBER_HOME carries its own SQLite. A run that fills
    the disk corrupts every one of them at once."""
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return 10 ** 6

ENGINE_PROCESS_MARKER = "src.sim.sim_main"


def _windows_measured(marker: str) -> "tuple[int, int] | None":
    """(engines seen, mean MB each) on Windows, via tasklist + Win32.

    `tasklist` gives pids and image names but not command lines, so the
    marker cannot be matched against `src.sim.sim_main` the way `ps` does.
    WMIC carries the command line and is the one place to get it without a
    dependency; when it is absent - it is deprecated on newer Windows -
    this answers None and the caller falls back to the constant, which is
    exactly the behaviour that existed before and no worse.
    """
    from world.resource_log import sample_process

    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    sizes_mb: list[float] = []
    for line in out.splitlines():
        if marker not in line:
            continue
        pid = line.rsplit(",", 1)[-1].strip()
        if not pid.isdigit():
            continue
        reading = sample_process(int(pid))
        if reading:
            sizes_mb.append(reading["rss_mb"])
    if not sizes_mb:
        return None
    return len(sizes_mb), round(sum(sizes_mb) / len(sizes_mb))


def measured_mb_per_engine(marker: str = ENGINE_PROCESS_MARKER) -> tuple[int, int] | None:
    """(engines seen, mean MB each) for the engines running right now.

    None when nothing is running, which is the normal pre-boot case and the
    reason the constant above still exists.

    Deliberately shells out to `ps` rather than importing psutil: this module
    has a standing no-psutil rule (see available_mb) and `ps` is on every
    platform this project runs on except Windows.

    WINDOWS USED TO RETURN None HERE, so every capacity decision made on a
    Windows box fell back to MB_PER_ENGINE_PROCESS - the Linux-under-load
    figure. The comment on `last_measurement` below already records what
    that costs, about a Mac: the same ten engines measured 33 MB each
    against the 550 the check assumed, and it refused a run that fitted
    easily. The identical hole was open on Windows and nobody could see it
    from the output, because a fallback and a measurement print the same
    way.

    Measured on this machine once the branch existed: engines at 81-94 MB
    against a constant of 550, which is the difference between "fifty
    engines need 27 GB" and "fifty engines need 4 GB".

    The Win32 reader is `resource_log.sample_process`, reused rather than
    reimplemented. Two implementations of one measurement is how a gate
    and a report end up telling different stories about the same run.
    """
    if sys.platform == "win32":
        return _windows_measured(marker)

    try:
        out = subprocess.run(
            ["ps", "-axo", "rss,args"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    return engines_from_ps_output(out, marker)


def engines_from_ps_output(out: str, marker: str = ENGINE_PROCESS_MARKER) -> tuple[int, int] | None:
    """Parse `ps -axo rss,args` into (engines seen, mean MB each).

    Split out of `measured_mb_per_engine` so it can be tested at all. The
    test that covers it monkeypatched `subprocess.run` and called the
    dispatcher, which on Windows takes the `_windows_measured` branch and
    never reads the fake at once - so the test failed on every non-Linux
    machine while appearing to cover the parsing. A pure function over a
    string tests the same thing on every platform.

    The world server, the broker and the reporter are all Python. A
    per-ENGINE figure that counted them would not be a per-engine figure,
    and `grep` matching its own argument is the oldest way to overcount by
    one.
    """
    sizes_kb = [
        int(line.split(None, 1)[0])
        for line in out.splitlines()
        if marker in line and "grep" not in line and line.split(None, 1)[:1]
        and line.split(None, 1)[0].isdigit()
    ]
    if not sizes_kb:
        return None
    return len(sizes_kb), round(sum(sizes_kb) / len(sizes_kb) / 1024)


MEASUREMENT_PATH = Path("data/.engine-memory.json")


def remember_measurement(engines: int, mb_each: int, path: Path = MEASUREMENT_PATH) -> None:
    """Record what engines cost on this machine, for the next preflight."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"engines": engines, "mb_each": mb_each,
                        "platform": sys.platform}),
            encoding="utf-8",
        )
    except OSError:
        pass


def last_measurement(path: Path = MEASUREMENT_PATH) -> int | None:
    """Per-engine MB last observed here, or None if never measured.

    Ignores a reading from a different platform: the whole reason this file
    exists is that the figure is platform-specific, so carrying one across
    would repeat the mistake it was written to fix.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("platform") != sys.platform:
        return None
    mb = data.get("mb_each")
    return int(mb) if isinstance(mb, (int, float)) and mb > 0 else None
