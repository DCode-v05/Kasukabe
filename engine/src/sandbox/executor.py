"""Sandbox executor — isolated code execution with background processes, PTY,
process monitoring, filesystem bridge, and safe binary allowlist.

Two-layer security model:
  Layer 1 (outer): Docker container with --cap-drop=ALL, --no-new-privileges
  Layer 2 (inner): bubblewrap namespace isolation per code execution

Features:
  - Background process management: run commands in background, check later
  - PTY mode: pseudo-terminal for interactive programs
  - Process monitoring: list, poll, log, kill, write stdin, send keys
  - Filesystem bridge: sandboxed read/write with path safety
  - Safe binary allowlist: restricted set of binaries that bypass approval
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.utility_directory.subprocess_env import subprocess_env
from src.sandbox.cleanup_registry import register_cleanup

logger = logging.getLogger(__name__)

WORKSPACE_PREFIX = "workspace-"


def workspace_root() -> Path:
    """Directory holding per-session sandbox workspaces.

    Under the engine root rather than the system temp dir: a user's files live
    here, so they belong in the tree a runtime captures. The mount must allow
    exec — bubblewrap writes a shell script into a workspace and execve()s it,
    and a noexec mount turns every bash call into a bare "Permission denied".

    NOTE: src/sandbox/orphan_detector.py matches process cwds against this
    root. Both must agree or reparented sandbox processes stop being reaped.
    """
    from src.infra.september_home import resolve_september_home, september_paths

    root = september_paths(resolve_september_home())["workspaces"]
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


DEFAULT_TIMEOUT = 30
DEFAULT_YIELD_MS = 10_000
DEFAULT_MAX_OUTPUT = 200_000
DEFAULT_TAIL_CHARS = 2000
SESSION_TTL_SEC = 1800  # 30 min
MAX_REGEX_LEN = 200  # ReDoS protection: reject user-supplied regex longer than this
MAX_BG_OUTPUT_BYTES = 100 * 1024 * 1024  # 100 MB — background process output watchdog limit
BG_WATCHDOG_INTERVAL_SEC = 5.0

# ── Feature flags ────────────────────────────────────────────────

TREE_KILL_FLAG = "FEATURE_TREE_KILL"
TREE_KILL_DEFAULT = "true"

BG_WATCHDOG_FLAG = "FEATURE_BACKGROUND_WATCHDOG"
BG_WATCHDOG_DEFAULT = "true"


def tree_kill_enabled() -> bool:
    """Check whether recursive tree-kill is active (feature flag)."""
    return os.environ.get(TREE_KILL_FLAG, TREE_KILL_DEFAULT).strip().lower() != "false"


def bg_watchdog_enabled() -> bool:
    """Check whether the background process output watchdog is active."""
    return os.environ.get(BG_WATCHDOG_FLAG, BG_WATCHDOG_DEFAULT).strip().lower() != "false"


def kill_process_tree(proc: Any) -> None:
    """Kill a process tree if FEATURE_TREE_KILL is enabled, else direct kill."""
    if tree_kill_enabled() and proc.pid:
        try:
            from src.sandbox.process_tree import tree_kill

            tree_kill(proc.pid)
        except Exception:
            proc.kill()
    else:
        proc.kill()


# ── Safe binary allowlist ────────────────────────────────────────


@dataclass
class SafeBinProfile:
    """Constraints for a safe binary."""

    min_positional: int = 0
    max_positional: int = 10
    allowed_value_flags: set[str] = field(default_factory=set)
    denied_flags: set[str] = field(default_factory=set)
    stdin_only: bool = False


DEFAULT_SAFE_BINS: dict[str, SafeBinProfile] = {
    "jq": SafeBinProfile(
        max_positional=1,
        allowed_value_flags={"--arg", "--argjson", "--argstr"},
        denied_flags={"-f", "--fromfile", "--slurpfile"},
    ),
    "grep": SafeBinProfile(
        stdin_only=True,
        max_positional=0,
        allowed_value_flags={"-e", "-m", "-A", "-B", "-C", "--regexp", "--max-count"},
    ),
    "cut": SafeBinProfile(
        stdin_only=True, max_positional=0, allowed_value_flags={"-d", "-f", "-c"}
    ),
    "sort": SafeBinProfile(stdin_only=True, max_positional=0, allowed_value_flags={"-k", "-t"}),
    "uniq": SafeBinProfile(stdin_only=True, max_positional=0),
    "head": SafeBinProfile(stdin_only=True, max_positional=0, allowed_value_flags={"-n"}),
    "tail": SafeBinProfile(stdin_only=True, max_positional=0, allowed_value_flags={"-n", "-f"}),
    "wc": SafeBinProfile(stdin_only=True, max_positional=0),
    "tr": SafeBinProfile(stdin_only=True, max_positional=2),
    "cat": SafeBinProfile(max_positional=5),
    "echo": SafeBinProfile(max_positional=20),
    "ls": SafeBinProfile(max_positional=5),
    "find": SafeBinProfile(max_positional=5, denied_flags={"-exec", "-execdir", "-delete"}),
    # `sed` is gated at permission-time by src/sandbox/sed_validation.py
    # (step 4.5 of check_permission), not here.
    "awk": SafeBinProfile(stdin_only=True, max_positional=1, denied_flags={"-f"}),
    # pip and node removed from safe bins — must go through approval flow
}

DEFAULT_TRUSTED_DIRS = ["/bin", "/usr/bin", "/usr/local/bin"]


def validate_safe_bin(
    argv: list[str],
    safe_bins: dict[str, SafeBinProfile],
    trusted_dirs: list[str],
) -> bool:
    """Check if a command matches the safe binary allowlist."""
    if not argv:
        return False

    cmd_name = Path(argv[0]).name
    if cmd_name not in safe_bins:
        return False

    profile = safe_bins[cmd_name]

    # Check binary location if full path given
    if "/" in argv[0]:
        cmd_dir = str(Path(argv[0]).parent)
        if cmd_dir not in trusted_dirs:
            return False

    # Parse args
    positional = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("-"):
            if arg in profile.denied_flags:
                return False
            if arg in profile.allowed_value_flags and i + 1 < len(argv):
                i += 2  # skip flag + value
                continue
        else:
            positional.append(arg)
        i += 1

    if len(positional) < profile.min_positional:
        return False
    if len(positional) > profile.max_positional:
        return False

    return True


# ── Process session tracking ─────────────────────────────────────


@dataclass
class ProcessSession:
    """State for a running or finished process."""

    id: str
    command: str
    pid: int | None = None
    cwd: str = ""
    started_at: float = field(default_factory=time.monotonic)
    # Stamped when the session transitions into the finished registry.
    # Retention (reap_finished) keys on THIS, not started_at: a job whose
    # runtime exceeded the TTL must still get a full post-finish retention
    # window. None while running; never refreshed on re-insertion.
    finished_at: float | None = None
    aggregated: str = ""
    tail: str = ""
    pending_stdout: list[str] = field(default_factory=list)
    pending_stderr: list[str] = field(default_factory=list)
    exit_code: int | None = None
    exit_signal: str | None = None
    exited: bool = False
    backgrounded: bool = False
    truncated: bool = False
    max_output_chars: int = DEFAULT_MAX_OUTPUT
    process: Any = field(default=None, repr=False)
    stdin_queue: asyncio.Queue | None = field(default=None, repr=False)
    reader_task: asyncio.Task | None = field(default=None, repr=False)
    unregister_cleanup: Any = field(default=None, repr=False)
    output_bytes: int = 0

    @property
    def is_running(self) -> bool:
        return not self.exited

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def append_output(self, text: str, raw_byte_len: int = 0) -> None:
        self.output_bytes += raw_byte_len or len(text)
        if len(self.aggregated) >= self.max_output_chars:
            self.truncated = True
            return
        self.aggregated += text
        self.pending_stdout.append(text)
        self.tail = self.aggregated[-DEFAULT_TAIL_CHARS:]

    def append_error(self, text: str, raw_byte_len: int = 0) -> None:
        self.output_bytes += raw_byte_len or len(text)
        if len(self.aggregated) >= self.max_output_chars:
            self.truncated = True
            return
        self.aggregated += text
        self.pending_stderr.append(text)
        self.tail = self.aggregated[-DEFAULT_TAIL_CHARS:]

    def drain_pending(self) -> tuple[str, str]:
        stdout = "".join(self.pending_stdout)
        stderr = "".join(self.pending_stderr)
        self.pending_stdout.clear()
        self.pending_stderr.clear()
        return stdout, stderr

    def teardown_resources(self) -> None:
        """Release process handles, cancel reader task, unregister cleanup.

        Called from every session exit/kill path to ensure consistent
        resource cleanup.  Idempotent — safe to call multiple times.
        """
        if self.reader_task and not self.reader_task.done():
            self.reader_task.cancel()
        self.reader_task = None
        self.process = None
        self.stdin_queue = None
        if self.unregister_cleanup:
            self.unregister_cleanup()
            self.unregister_cleanup = None

    def to_summary(self) -> dict[str, Any]:
        status = "exited" if self.exited else ("backgrounded" if self.backgrounded else "running")
        return {
            "id": self.id,
            "command": self.command[:100],
            "pid": self.pid,
            "status": status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "output_chars": len(self.aggregated),
            "truncated": self.truncated,
        }


# ── Sandbox result ───────────────────────────────────────────────


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    session_id: str = ""
    command: str = ""

    @property
    def ok(self) -> bool:
        if self.timed_out:
            return False
        if self.command:
            from src.sandbox.command_semantics import interpret_exit_code

            is_error, _ = interpret_exit_code(self.command, self.exit_code)
            return not is_error
        return self.exit_code == 0

    def to_str(self, max_chars: int = DEFAULT_MAX_OUTPUT) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout[:max_chars])
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr[:max_chars]}")
        if self.timed_out:
            parts.append("[timed out]")
        if self.session_id:
            parts.append(f"[session: {self.session_id}]")

        # Semantic annotation for known commands with non-zero exit
        if self.command and self.exit_code != 0:
            from src.sandbox.command_semantics import interpret_exit_code

            is_error, message = interpret_exit_code(
                self.command,
                self.exit_code,
                self.stdout,
                self.stderr,
            )
            if message:
                parts.append(f"[{message}]")
            elif is_error and not parts:
                return f"[exit code {self.exit_code}]"

        if not parts:
            return f"[exit code {self.exit_code}]"
        return "\n".join(parts)


# ── Main executor ────────────────────────────────────────────────


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_windows() -> bool:
    return platform.system() == "Windows"


def resolve_python() -> str:
    """Find the Python binary — python3 on Linux, python on Windows."""
    if is_windows():
        return "python"
    return "python3"


def resolve_shell() -> tuple[str, list[str]]:
    """Find the shell binary and args — PowerShell on Windows, bash/sh on Linux/macOS."""
    if is_windows():
        # PowerShell 7 preferred, fall back to 5.1
        for candidate in ["pwsh", "powershell"]:
            if shutil.which(candidate):
                return candidate, ["-NoProfile", "-NonInteractive", "-Command"]
        return "powershell", ["-NoProfile", "-NonInteractive", "-Command"]

    env_shell = os.environ.get("SHELL", "").strip()
    if env_shell:
        name = Path(env_shell).name
        # Fish rejects common bashisms — prefer bash
        if name == "fish":
            bash = shutil.which("bash")
            if bash:
                return bash, ["-c"]
            sh = shutil.which("sh")
            if sh:
                return sh, ["-c"]
        return env_shell, ["-c"]

    for candidate in ["bash", "sh"]:
        found = shutil.which(candidate)
        if found:
            return found, ["-c"]

    return "sh", ["-c"]


bwrap_cached: bool | None = None


def has_bwrap() -> bool:
    """Check if bwrap is installed AND fully functional (namespaces + fork).

    The simple ``bwrap -- /bin/true`` test only validates namespace creation.
    Pipe commands (``echo x | grep x``) need fork() inside the namespace,
    which fails under Docker's default seccomp or on emulated architectures.
    We test with ``sh -c 'echo x | cat'`` to catch that.
    """
    global bwrap_cached
    if bwrap_cached is None:
        if shutil.which("bwrap") is None:
            bwrap_cached = False
        else:
            import subprocess

            try:
                # Phase 1: basic namespace creation
                subprocess.run(
                    ["bwrap", "--ro-bind", "/", "/", "--", "/bin/true"],
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
                # Phase 2: fork inside namespace (pipes need fork)
                result = subprocess.run(
                    ["bwrap", "--ro-bind", "/", "/", "--", "/bin/sh", "-c", "echo bwrap_ok | cat"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode != 0 or b"bwrap_ok" not in result.stdout:
                    logger.warning(
                        "bwrap namespaces work but fork blocked inside sandbox "
                        "(Docker seccomp or emulated arch) — falling back to plain subprocess"
                    )
                    bwrap_cached = False
                else:
                    bwrap_cached = True
            except Exception:
                logger.warning(
                    "bwrap installed but cannot create namespaces — falling back to plain subprocess"
                )
                bwrap_cached = False
    return bwrap_cached


EXIT_CODE_TIMEOUT = 124


def build_bwrap_args(
    workspace: str,
    allow_network: bool = False,
    seccomp_fd: int | None = None,
    runtimes_path: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> list[str]:
    """Build bubblewrap isolation args. Shared by SandboxExecutor and run_isolated.

    Args:
        workspace: host path to writable workspace directory
        allow_network: if True, skip --unshare-net
        seccomp_fd: if set, add --seccomp <fd> for syscall filtering
        runtimes_path: if set, ro-bind bundled runtimes at /runtimes
        extra_env: additional --setenv KEY VALUE pairs for the sandbox
    """
    bwrap = [
        "bwrap",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/bin",
        "/bin",
    ]
    if Path("/lib64").exists():
        bwrap += ["--symlink", "/usr/lib64", "/lib64"]
    # Bundled runtimes (read-only)
    if runtimes_path and Path(runtimes_path).exists():
        bwrap += ["--ro-bind", runtimes_path, "/runtimes"]
    bwrap += ["--bind", workspace, "/workspace"]
    if not allow_network:
        bwrap += ["--unshare-net"]
    bwrap += [
        "--unshare-pid",
        "--unshare-ipc",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--chdir",
        "/workspace",
        "--die-with-parent",
    ]
    # Seccomp BPF filter
    if seccomp_fd is not None:
        bwrap += ["--seccomp", str(seccomp_fd)]
    # Extra environment variables
    if extra_env:
        for k, v in extra_env.items():
            bwrap += ["--setenv", k, v]
    bwrap += ["--"]
    return bwrap


# Binaries that require network access — triggers permission flow
NETWORK_BINARIES = frozenset(
    {
        "curl",
        "wget",
        "git",
        "pip",
        "pip3",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "ssh",
        "scp",
        "rsync",
        "apt",
        "apt-get",
        "brew",
        "docker",
        "go",
        "cargo",
        "gem",
        "composer",
        "nuget",
        "dotnet",
        "httpie",
        "http",
        "fetch",
    }
)


def needs_network(command: str) -> bool:
    """Check if a command uses a network-requiring binary."""
    import shlex

    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False
    cmd_name = Path(argv[0]).name
    return cmd_name in NETWORK_BINARIES


# Async callback type for permission requests
# Signature: async (commands: list[str]) -> bool
PermissionCallback = Any  # Callable[[list[str]], Awaitable[bool]]


class SandboxExecutor:
    """Manages per-session workspaces, background processes, and isolated execution.

    Network permission model:
    - Default: no network (--unshare-net)
    - When a command needs network (detected by binary name or LLM flag),
      the executor calls the permission callback
    - Permission is per-task: once approved, all commands in the task can use network
    - When the task ends (cleanup), network permission reverts
    """

    def __init__(
        self,
        session_id: str | None = None,
        safe_bins: dict[str, SafeBinProfile] | None = None,
        trusted_dirs: list[str] | None = None,
        permission_callback: PermissionCallback | None = None,
        policy: Any | None = None,  # SandboxPolicy — imported lazily to avoid circular
        proxy: Any | None = None,  # TrustedZoneProxy — imported lazily to avoid circular
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.workspace_dir: Path | None = None
        self.use_bwrap = is_linux() and has_bwrap()
        self.sessions: dict[str, ProcessSession] = {}
        self.finished: dict[str, ProcessSession] = {}
        self.policy = policy
        self.proxy = proxy
        # Raw FD of the seccomp BPF file. Held open across bwrap subprocess
        # launches and closed in cleanup(). Stored as int (not a file object)
        # because the FD must be inheritable across exec — Python's open()
        # sets O_CLOEXEC by default since 3.4. We use os.open + set_inheritable
        # and pass it via pass_fds= on create_subprocess_exec.
        self.seccomp_fd: int | None = None

        # Derive safe bins from policy if provided, else from explicit param or defaults
        if policy is not None:
            effective_bins = dict(policy.to_safe_bins())
        else:
            effective_bins = dict(safe_bins or DEFAULT_SAFE_BINS)
        # Python only safe when bubblewrap provides isolation
        if self.use_bwrap:
            effective_bins.setdefault("python3", SafeBinProfile(max_positional=5))
            effective_bins.setdefault("python", SafeBinProfile(max_positional=5))
        self.safe_bins = effective_bins
        self.trusted_dirs = trusted_dirs or DEFAULT_TRUSTED_DIRS
        self.permission_callback = permission_callback
        self.task_network_approved = False
        self.pending_network_commands: list[str] = []
        self.watchdog_task: asyncio.Task | None = None

        if policy:
            logger.info(
                "Sandbox: policy loaded (%d binaries, seccomp=%s, landlock=%s, proxy=%s)",
                len(self.safe_bins),
                policy.seccomp_enabled,
                policy.landlock_enabled,
                policy.proxy.enabled if policy.proxy else False,
            )
        if proxy:
            logger.info("Sandbox: proxy attached on port %s", getattr(proxy, "port", "?"))

    @property
    def workspace(self) -> Path:
        if self.workspace_dir is None:
            self.workspace_dir = workspace_root() / f"workspace-{self.session_id}"
            self.workspace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.workspace_dir

    @property
    def uses_bubblewrap(self) -> bool:
        return self.use_bwrap

    @property
    def is_busy(self) -> bool:
        """True while any background process session is still live (don't reap)."""
        return bool(self.sessions)

    def is_safe_command(self, command: str) -> bool:
        """Check if a command matches the safe binary allowlist."""
        import shlex

        try:
            argv = shlex.split(command)
        except ValueError:
            return False
        return validate_safe_bin(argv, self.safe_bins, self.trusted_dirs)

    def command_needs_network(self, command: str, explicit_network: bool = False) -> bool:
        """Check if a command requires network access.

        Detection: binary name list (curl, git, pip, etc.) OR explicit flag.
        """
        if explicit_network:
            return True
        return needs_network(command)

    async def request_network_permission(self, commands: list[str]) -> bool:
        """Request per-task network permission via the permission callback.

        Bundles all planned network commands into one prompt. If approved,
        all commands in this task can use network until cleanup().
        """
        if self.task_network_approved:
            return True

        if self.permission_callback is None:
            logger.warning("Network permission requested but no callback configured — denying")
            return False

        try:
            approved = await self.permission_callback(commands)
            if approved:
                self.task_network_approved = True
                logger.info("Network permission granted for task (commands: %s)", commands)
            else:
                logger.info("Network permission denied for task")
            return approved
        except Exception as e:
            logger.warning("Network permission callback failed: %s — denying", e)
            return False

    def revoke_network_permission(self) -> None:
        """Revoke network permission. Called at task end / cleanup."""
        self.task_network_approved = False
        self.pending_network_commands.clear()

    # ── Foreground execution ──────────────────────────────────

    async def run_python(self, code: str, timeout: int = DEFAULT_TIMEOUT) -> SandboxResult:
        if not self.use_bwrap:
            return SandboxResult(
                "",
                "Python execution requires bubblewrap sandbox (Linux only)",
                1,
            )
        script = self.workspace / "script.py"
        script.write_text(code, encoding="utf-8")
        return await self.exec([resolve_python(), str(script)], timeout)

    async def run_bash(
        self,
        command: str,
        timeout: int = DEFAULT_TIMEOUT,
        background: bool = False,
        yield_ms: int = DEFAULT_YIELD_MS,
        pty: bool = False,
        network: bool = False,
    ) -> SandboxResult:
        """Execute a shell command. Uses PowerShell on Windows, bash/sh on Linux/macOS.
        If background=True, yields after yield_ms.
        If network=True or command needs network (detected by binary name),
        requests per-task permission before running."""
        if not self.use_bwrap:
            # Fail-closed: never run a shell unsandboxed. bwrap is the inner ring;
            # if it isn't active we refuse rather than execute on the host kernel.
            return SandboxResult(
                "",
                "Shell execution requires the bubblewrap sandbox (Linux container). "
                "Refusing to run unsandboxed — use `docker compose run` for local dev.",
                1,
                command=command,
            )
        needs_net = self.command_needs_network(command, explicit_network=network)

        if needs_net and not self.task_network_approved:
            approved = await self.request_network_permission([command])
            if not approved:
                return SandboxResult(
                    "",
                    "Network access denied. This command requires network "
                    "permission which was not granted.",
                    1,
                    command=command,
                )

        shell, shell_args = resolve_shell()

        if is_windows():
            # PowerShell: pass command as arg, no script file
            cmd = [shell] + shell_args + [command]
        else:
            script = self.workspace / f"script_{uuid.uuid4().hex[:6]}.sh"
            script.write_text(command, encoding="utf-8")
            os.chmod(script, 0o755)
            cmd = [shell] + shell_args + [str(script)]

        if background:
            result = await self.exec_background(cmd, yield_ms)
            result.command = command
            return result

        if pty:
            result = await self.exec_pty(cmd, timeout)
            result.command = command
            return result

        result = await self.exec(cmd, timeout, allow_network=needs_net)
        result.command = command
        return result

    # ── Background execution ──────────────────────────────────

    async def exec_background(self, cmd: list[str], yield_ms: int) -> SandboxResult:
        """Start a process, wait yield_ms, then return with session ID for monitoring."""
        sess_id = f"bg_{uuid.uuid4().hex[:8]}"
        session = ProcessSession(id=sess_id, command=" ".join(cmd), cwd=str(self.workspace))

        actual_cmd = self.build_cmd(cmd)
        # Strict tier at every sandbox spawn site regardless of session default:
        # sandbox Bash is the highest-threat context (can execute LLM-generated
        # commands) and must never see AWS/GCP/Azure/OIDC creds via env.
        spawn_env = subprocess_env(mode="strict", spawn_site="sandbox_background").env
        try:
            proc = await asyncio.create_subprocess_exec(
                *actual_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace) if not self.use_bwrap else None,
                pass_fds=self.bwrap_pass_fds(),
                env=spawn_env,
            )
        except Exception as e:
            return SandboxResult("", f"Failed to start: {e}", 1)

        session.pid = proc.pid
        session.process = proc
        self.sessions[sess_id] = session

        # Register cleanup callback so this session is killed on shutdown
        async def cleanup_session() -> None:
            if not session.exited and session.process and session.process.returncode is None:
                kill_process_tree(session.process)

        session.unregister_cleanup = register_cleanup(f"session-{sess_id}", cleanup_session)

        # Start output readers (tracked for cancellation)
        session.reader_task = asyncio.create_task(self.read_output(session, proc))

        # Wait for yield_ms or process exit
        try:
            await asyncio.wait_for(self.wait_exit(session, proc), timeout=yield_ms / 1000)
            # Process exited within yield window
            stdout, stderr = session.drain_pending()
            return SandboxResult(
                stdout=session.aggregated[:DEFAULT_MAX_OUTPUT],
                stderr="",
                exit_code=session.exit_code or 0,
                session_id=sess_id,
            )
        except asyncio.TimeoutError:
            # Race guard: wait_exit may have completed between the timeout
            # firing and us reaching this branch.  If the session already
            # exited, return the real exit result instead of a fake
            # "backgrounded" result that points to a finished session.
            if session.exited:
                stdout, stderr = session.drain_pending()
                return SandboxResult(
                    stdout=session.aggregated[:DEFAULT_MAX_OUTPUT],
                    stderr="",
                    exit_code=session.exit_code or 0,
                    session_id=sess_id,
                )
            # Process still running — background it
            session.backgrounded = True
            # Unregister from cleanup so backgrounded sessions survive agent exit
            if session.unregister_cleanup:
                session.unregister_cleanup()
                session.unregister_cleanup = None
            # Start the per-executor watchdog on first backgrounded session
            if bg_watchdog_enabled() and (self.watchdog_task is None or self.watchdog_task.done()):
                self.watchdog_task = asyncio.create_task(self.background_watchdog_loop())
            return SandboxResult(
                stdout=f"Process started in background (session: {sess_id}, pid: {session.pid})\n"
                f"Use process tool with action:poll to check progress.\n"
                f"Output so far:\n{session.tail}",
                stderr="",
                exit_code=0,
                session_id=sess_id,
            )

    async def read_output(self, session: ProcessSession, proc: asyncio.subprocess.Process) -> None:
        """Read stdout and stderr into the session."""

        async def read_stream(stream, is_stderr=False):
            while True:
                try:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    byte_len = len(chunk)
                    text = chunk.decode("utf-8", errors="replace")
                    if is_stderr:
                        session.append_error(text, raw_byte_len=byte_len)
                    else:
                        session.append_output(text, raw_byte_len=byte_len)
                except Exception:
                    break

        tasks = []
        if proc.stdout:
            tasks.append(asyncio.create_task(read_stream(proc.stdout)))
        if proc.stderr:
            tasks.append(asyncio.create_task(read_stream(proc.stderr, True)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_exit(self, session: ProcessSession, proc: asyncio.subprocess.Process) -> None:
        """Wait for process to exit and update session."""
        code = await proc.wait()
        session.exit_code = code
        session.exited = True
        session.teardown_resources()
        # Move to finished
        self.sessions.pop(session.id, None)
        if session.finished_at is None:
            session.finished_at = time.monotonic()
        self.finished[session.id] = session

    # ── PTY execution ─────────────────────────────────────────

    async def exec_pty(self, cmd: list[str], timeout: int) -> SandboxResult:
        """Execute with a pseudo-terminal (Linux only, falls back to regular subprocess)."""
        if not is_linux():
            return await self.exec(cmd, timeout)

        try:
            import pty as pty_mod
            import select

            master_fd, slave_fd = pty_mod.openpty()
            actual_cmd = self.build_cmd(cmd)
            spawn_env = subprocess_env(mode="strict", spawn_site="sandbox_pty").env

            proc = await asyncio.create_subprocess_exec(
                *actual_cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(self.workspace) if not self.use_bwrap else None,
                pass_fds=self.bwrap_pass_fds(),
                env=spawn_env,
            )
            os.close(slave_fd)

            output = []

            def read_pty():
                while True:
                    r, _, _ = select.select([master_fd], [], [], 0.1)
                    if r:
                        try:
                            data = os.read(master_fd, 4096)
                            if not data:
                                break
                            output.append(data.decode("utf-8", errors="replace"))
                        except OSError:
                            break
                    # Check if process exited
                    if proc.returncode is not None:
                        # Drain remaining
                        while True:
                            r, _, _ = select.select([master_fd], [], [], 0.05)
                            if not r:
                                break
                            try:
                                data = os.read(master_fd, 4096)
                                if not data:
                                    break
                                output.append(data.decode("utf-8", errors="replace"))
                            except OSError:
                                break
                        break

            reader = asyncio.get_event_loop().run_in_executor(None, read_pty)

            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                kill_process_tree(proc)
                await proc.wait()
                os.close(master_fd)
                return SandboxResult("".join(output), "", 124, timed_out=True)

            await asyncio.wait_for(reader, timeout=2)
            os.close(master_fd)

            return SandboxResult(
                stdout="".join(output)[:DEFAULT_MAX_OUTPUT],
                stderr="",
                exit_code=proc.returncode or 0,
            )

        except ImportError:
            return await self.exec(cmd, timeout)

    # ── Process monitoring ────────────────────────────────────

    def process_list(self) -> list[dict[str, Any]]:
        """List all running and recently finished processes."""
        self.reap_finished()
        result = []
        for s in self.sessions.values():
            result.append(s.to_summary())
        for s in self.finished.values():
            result.append(s.to_summary())
        return result

    async def process_poll(self, session_id: str, timeout_ms: int = 2000) -> dict[str, Any]:
        """Wait for new output or exit from a session."""
        session = self.sessions.get(session_id) or self.finished.get(session_id)
        if session is None:
            return {"error": f"Session {session_id} not found"}

        # Check if process actually exited but session hasn't been updated yet
        proc = session.process
        if proc and proc.returncode is not None and not session.exited:
            session.exit_code = proc.returncode
            session.exited = True
            session.teardown_resources()
            self.sessions.pop(session_id, None)
            if session.finished_at is None:
                session.finished_at = time.monotonic()
            self.finished[session_id] = session

        if session.exited:
            stdout, stderr = session.drain_pending()
            return {
                "status": "exited",
                "exit_code": session.exit_code,
                "stdout": stdout or session.tail,
                "stderr": stderr,
            }

        # Wait for output or exit
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            # Re-check process exit
            if proc and proc.returncode is not None:
                session.exit_code = proc.returncode
                session.exited = True
                session.teardown_resources()
                self.sessions.pop(session_id, None)
                if session.finished_at is None:
                    session.finished_at = time.monotonic()
                self.finished[session_id] = session
                break
            if session.exited or session.pending_stdout or session.pending_stderr:
                break
            await asyncio.sleep(0.1)

        stdout, stderr = session.drain_pending()
        return {
            "status": "exited" if session.exited else "running",
            "exit_code": session.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "output_chars": len(session.aggregated),
        }

    def process_log(self, session_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        """Get paginated log output from a session."""
        session = self.sessions.get(session_id) or self.finished.get(session_id)
        if session is None:
            return {"error": f"Session {session_id} not found"}

        lines = session.aggregated.split("\n")
        page = lines[offset : offset + limit]
        return {
            "lines": page,
            "total_lines": len(lines),
            "offset": offset,
            "truncated": session.truncated,
            "status": "exited" if session.exited else "running",
        }

    async def process_kill(self, session_id: str) -> dict[str, Any]:
        """Kill a running process."""
        session = self.sessions.get(session_id)
        if session is None:
            if session_id in self.finished:
                return {
                    "status": "already_exited",
                    "exit_code": self.finished[session_id].exit_code,
                }
            return {"error": f"Session {session_id} not found"}

        proc = session.process
        if proc and proc.returncode is None:
            try:
                kill_process_tree(proc)
                await asyncio.wait_for(proc.wait(), timeout=4)
            except Exception:
                pass
        session.exited = True
        session.exit_code = -9
        session.exit_signal = "SIGKILL"
        session.teardown_resources()
        self.sessions.pop(session_id, None)
        if session.finished_at is None:
            session.finished_at = time.monotonic()
        self.finished[session_id] = session
        return {"status": "killed", "session_id": session_id}

    async def process_write(self, session_id: str, data: str) -> dict[str, Any]:
        """Write data to a running process's stdin."""
        session = self.sessions.get(session_id)
        if session is None:
            return {"error": f"Session {session_id} not found or not running"}

        proc = session.process
        if proc and proc.stdin:
            try:
                proc.stdin.write(data.encode("utf-8"))
                await proc.stdin.drain()
                return {"status": "written", "bytes": len(data)}
            except Exception as e:
                return {"error": f"Write failed: {e}"}

        return {"error": "Process has no stdin"}

    async def process_send_keys(self, session_id: str, keys: str) -> dict[str, Any]:
        """Send special keys to a process (Ctrl+C, Ctrl+D, etc.)."""
        key_map = {
            "ctrl-c": "\x03",
            "ctrl-d": "\x04",
            "ctrl-z": "\x1a",
            "ctrl-\\": "\x1c",
            "enter": "\n",
            "tab": "\t",
            "escape": "\x1b",
            "up": "\x1b[A",
            "down": "\x1b[B",
            "left": "\x1b[D",
            "right": "\x1b[C",
        }
        resolved = key_map.get(keys.lower(), keys)
        return await self.process_write(session_id, resolved)

    def process_remove(self, session_id: str) -> dict[str, Any]:
        """Remove a finished session record."""
        if session_id in self.finished:
            del self.finished[session_id]
            return {"status": "removed"}
        if session_id in self.sessions:
            return {"error": "Cannot remove running session — kill it first"}
        return {"error": f"Session {session_id} not found"}

    def reap_finished(self) -> None:
        """Remove finished sessions older than TTL.

        Keyed on finished_at (fall back to started_at only for legacy
        records that predate the stamp): retention is measured from when
        the job FINISHED, so a job that ran longer than the TTL still keeps
        its exit code and output readable for a full window afterwards.
        """
        now = time.monotonic()
        stale = [
            sid
            for sid, s in self.finished.items()
            if now - (s.finished_at or s.started_at) > SESSION_TTL_SEC
        ]
        for sid in stale:
            del self.finished[sid]

    async def kill_orphaned_children(self) -> int:
        """Kill all running non-backgrounded sessions (post-tool cleanup).

        Called after foreground tool execution to catch child processes that
        outlived their parent command.  Skips backgrounded sessions — those
        are intentionally independent.
        """
        killed = 0
        for session in list(self.sessions.values()):
            if session.backgrounded or session.exited:
                continue
            proc = session.process
            if proc and proc.returncode is None:
                try:
                    kill_process_tree(proc)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2)
                    except (asyncio.TimeoutError, Exception):
                        pass
                except Exception:
                    pass
                killed += 1
            session.exited = True
            session.exit_code = -9
            session.exit_signal = "SIGKILL"
            session.teardown_resources()
            self.sessions.pop(session.id, None)
            if session.finished_at is None:
                session.finished_at = time.monotonic()
            self.finished[session.id] = session
        return killed

    def get_backgrounded_sessions(self) -> list[ProcessSession]:
        """Return all active backgrounded sessions (for watchdog inspection)."""
        return [s for s in self.sessions.values() if s.backgrounded and not s.exited]

    async def background_watchdog_loop(self) -> None:
        """Poll backgrounded sessions every 5s, kill any exceeding output limit."""
        while True:
            await asyncio.sleep(BG_WATCHDOG_INTERVAL_SEC)
            if not self.get_backgrounded_sessions():
                break
            for session in list(self.sessions.values()):
                if not session.backgrounded or session.exited:
                    continue
                output_bytes = session.output_bytes
                if output_bytes > MAX_BG_OUTPUT_BYTES:
                    logger.warning(
                        "watchdog: session %s exceeded %d bytes (%d) — killing",
                        session.id,
                        MAX_BG_OUTPUT_BYTES,
                        output_bytes,
                    )
                    proc = session.process
                    if proc and proc.returncode is None:
                        try:
                            kill_process_tree(proc)
                        except Exception:
                            pass
                    session.exited = True
                    session.exit_code = -9
                    session.exit_signal = "SIGKILL_WATCHDOG"
                    session.teardown_resources()
                    self.sessions.pop(session.id, None)
                    if session.finished_at is None:
                        session.finished_at = time.monotonic()
                    self.finished[session.id] = session

    # ── Filesystem bridge ─────────────────────────────────────

    async def read_file(self, path: str) -> SandboxResult:
        full = self.resolve_path(path)
        if full is None:
            return SandboxResult("", "Path escapes workspace", 1)
        if not full.exists():
            return SandboxResult("", f"File not found: {path}", 1)
        try:
            content = full.read_text(encoding="utf-8")
            return SandboxResult(content, "", 0)
        except UnicodeDecodeError:
            size = full.stat().st_size
            return SandboxResult(f"[binary file, {size} bytes]", "", 0)
        except Exception as e:
            return SandboxResult("", str(e), 1)

    async def write_file(self, path: str, content: str) -> SandboxResult:
        full = self.resolve_path(path)
        if full is None:
            return SandboxResult("", "Path escapes workspace", 1)
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            return SandboxResult(f"Written: {path} ({len(content)} chars)", "", 0)
        except Exception as e:
            return SandboxResult("", str(e), 1)

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> SandboxResult:
        """Replace old_string with new_string in a sandbox workspace file.

        When replace_all is False (default), old_string must match exactly
        once — if it appears multiple times, returns an error asking the
        caller to add context, preventing silent mutation of the wrong
        site. Pass replace_all=True to substitute every occurrence. Returns
        an error if the file is missing or old_string is not found —
        callers should not create files this way.
        """
        full = self.resolve_path(path)
        if full is None:
            return SandboxResult("", "Path escapes workspace", 1)
        if not full.exists():
            return SandboxResult("", f"File not found: {path}", 1)
        try:
            content = full.read_text(encoding="utf-8")
        except Exception as e:
            return SandboxResult("", str(e), 1)
        match_count = content.count(old_string)
        if match_count == 0:
            return SandboxResult("", f"old_string not found in {path}", 1)
        if not replace_all and match_count > 1:
            return SandboxResult(
                "",
                f"old_string is not unique in {path} ({match_count} matches) — add surrounding context to make it unique, or pass replace_all=true to replace every occurrence",
                1,
            )
        count = match_count if replace_all else 1
        new_content = content.replace(old_string, new_string, -1 if replace_all else 1)
        try:
            full.write_text(new_content, encoding="utf-8")
            return SandboxResult(f"Edited: {path} ({count} replacement(s))", "", 0)
        except Exception as e:
            return SandboxResult("", str(e), 1)

    async def list_directory(self, path: str = ".") -> SandboxResult:
        full = self.resolve_path(path)
        if full is None:
            return SandboxResult("", "Path escapes workspace", 1)
        if not full.exists():
            return SandboxResult("", f"Directory not found: {path}", 1)
        try:
            entries = sorted(full.iterdir())
            lines = []
            for e in entries:
                if e.is_dir():
                    lines.append(f"d {e.name}/")
                else:
                    size = e.stat().st_size
                    lines.append(f"f {e.name} ({size}b)")
            return SandboxResult("\n".join(lines) if lines else "(empty)", "", 0)
        except Exception as e:
            return SandboxResult("", str(e), 1)

    async def search_content(self, pattern: str, path: str = ".") -> SandboxResult:
        full = self.resolve_path(path)
        if full is None:
            return SandboxResult("", "Path escapes workspace", 1)

        if len(pattern) > MAX_REGEX_LEN:
            return SandboxResult(
                "", f"Pattern too long ({len(pattern)} chars, max {MAX_REGEX_LEN})", 1
            )

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return SandboxResult("", f"Invalid pattern: {e}", 1)

        matches = []
        target = full if full.is_file() else self.workspace
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        deadline = time.monotonic() + 5.0  # 5-second wall-clock limit

        for f in files:
            if time.monotonic() > deadline:
                matches.append("[search timed out after 5s]")
                break
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if time.monotonic() > deadline:
                        matches.append("[search timed out after 5s]")
                        break
                    if regex.search(line):
                        rel = f.relative_to(self.workspace)
                        matches.append(f"{rel}:{i}: {line}")
                        if len(matches) >= 100:
                            break
            except (UnicodeDecodeError, PermissionError):
                continue
            if len(matches) >= 100:
                break

        return SandboxResult("\n".join(matches) if matches else "No matches", "", 0)

    # ── Workspace management ──────────────────────────────────

    def cleanup(self) -> None:
        """Kill all processes, close fds, remove workspace, revoke permissions."""
        if self.watchdog_task and not self.watchdog_task.done():
            self.watchdog_task.cancel()
            self.watchdog_task = None
        for session in list(self.sessions.values()):
            proc = session.process
            if proc and proc.returncode is None:
                try:
                    kill_process_tree(proc)
                except Exception:
                    pass
            session.teardown_resources()
        self.sessions.clear()
        self.finished.clear()
        self.revoke_network_permission()
        if self.seccomp_fd is not None:
            try:
                os.close(self.seccomp_fd)
            except Exception:
                pass
            self.seccomp_fd = None
        if self.workspace_dir and self.workspace_dir.exists():
            shutil.rmtree(self.workspace_dir, ignore_errors=True)
            self.workspace_dir = None

    # ── Internal helpers ──────────────────────────────────────

    def resolve_path(self, path: str) -> Path | None:
        ws = self.workspace
        # Handle absolute paths within workspace
        if path.startswith("/workspace/"):
            path = path[len("/workspace/") :]
        full = (ws / path).resolve()
        if not str(full).startswith(str(ws.resolve())):
            return None
        return full

    def build_cmd(self, cmd: list[str], allow_network: bool = False) -> list[str]:
        """Wrap command with bubblewrap + seccomp + Landlock + proxy if available."""
        if not self.use_bwrap:
            # Fail-closed defense-in-depth: every code-execution entry point
            # (run_bash/run_python) already refuses without bwrap, so reaching
            # here unsandboxed is a bug. Never return an unwrapped command.
            raise RuntimeError(
                "sandbox unavailable: refusing to build an unsandboxed command "
                "(bubblewrap required)"
            )

        net = allow_network and self.task_network_approved
        extra_env: dict[str, str] = {}

        # When proxy is active, sandbox needs localhost access (no --unshare-net).
        # The proxy is the network gatekeeper instead of namespace isolation.
        if self.proxy is not None:
            net = True  # allow network so sandbox can reach proxy on localhost
            proxy_url = f"http://127.0.0.1:{self.proxy.port}"
            extra_env.update(
                {
                    "HTTP_PROXY": proxy_url,
                    "HTTPS_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "https_proxy": proxy_url,
                    "NO_PROXY": "localhost,127.0.0.1",
                }
            )

        # Try seccomp: write BPF to workspace, open inheritable fd for bwrap.
        # The FD must survive subprocess exec — see seccomp_fd docstring.
        seccomp_fd = None
        seccomp_disabled = self.policy and not self.policy.seccomp_enabled
        if not seccomp_disabled:
            try:
                from src.sandbox.seccomp import build_bpf_for_sandbox, write_bpf_file

                bpf = build_bpf_for_sandbox(allow_network=net)
                if bpf:
                    bpf_path = write_bpf_file(bpf, str(self.workspace))
                    if bpf_path:
                        # Close any stale fd from a previous build_cmd call.
                        if self.seccomp_fd is not None:
                            try:
                                os.close(self.seccomp_fd)
                            except Exception:
                                pass
                        fd = os.open(bpf_path, os.O_RDONLY)
                        os.set_inheritable(fd, True)
                        self.seccomp_fd = fd
                        seccomp_fd = fd
            except Exception as e:
                logger.debug("seccomp unavailable: %s", e)

        bwrap = build_bwrap_args(
            str(self.workspace),
            allow_network=net,
            seccomp_fd=seccomp_fd,
            extra_env=extra_env if extra_env else None,
        )

        # Remap paths from host workspace to /workspace
        sandbox_cmd = []
        for part in cmd:
            if str(self.workspace) in part:
                sandbox_cmd.append(part.replace(str(self.workspace), "/workspace"))
            else:
                sandbox_cmd.append(part)

        # Try Landlock: wrap command with Landlock enforcement script.
        # The profile's workspace path must be the path the wrapper sees
        # *inside* the bwrap mount namespace ("/workspace"), not the host
        # tempdir — otherwise os.open() in the wrapper hits ENOENT and the
        # workspace rule is silently dropped, causing Landlock to deny
        # script execution. landlock_bwrap_integration still needs the host
        # path so it can write the wrapper file there before bwrap binds it.
        landlock_disabled = self.policy and not self.policy.landlock_enabled
        if not landlock_disabled:
            try:
                from src.sandbox.landlock import (
                    has_landlock_support,
                    default_sandbox_profile,
                    landlock_bwrap_integration,
                )

                if has_landlock_support():
                    profile = default_sandbox_profile("/workspace")
                    wrapped = landlock_bwrap_integration(
                        profile,
                        str(self.workspace),
                        sandbox_cmd,
                    )
                    if wrapped:
                        sandbox_cmd = wrapped
                        logger.debug("Landlock wrapper applied")
            except Exception as e:
                logger.debug("Landlock unavailable: %s", e)

        return bwrap + sandbox_cmd

    def bwrap_pass_fds(self) -> tuple[int, ...]:
        """FDs that must be inherited by bwrap subprocesses (seccomp BPF)."""
        return (self.seccomp_fd,) if self.seccomp_fd is not None else ()

    async def exec(
        self, cmd: list[str], timeout: int, allow_network: bool = False
    ) -> SandboxResult:
        actual_cmd = self.build_cmd(cmd, allow_network=allow_network)
        spawn_env = subprocess_env(mode="strict", spawn_site="sandbox_exec").env
        try:
            proc = await asyncio.create_subprocess_exec(
                *actual_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace) if not self.use_bwrap else None,
                pass_fds=self.bwrap_pass_fds(),
                env=spawn_env,
            )
        except FileNotFoundError as e:
            return SandboxResult("", f"Command not found: {e}", 127)
        except Exception as e:
            return SandboxResult("", f"Failed to start: {e}", 1)

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return SandboxResult(
                stdout=stdout.decode("utf-8", errors="replace")[:DEFAULT_MAX_OUTPUT],
                stderr=stderr.decode("utf-8", errors="replace")[:DEFAULT_MAX_OUTPUT],
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            kill_process_tree(proc)
            try:
                await proc.communicate()
            except Exception:
                pass
            return SandboxResult(
                "", f"Timed out after {timeout}s", EXIT_CODE_TIMEOUT, timed_out=True
            )


# ── Isolated subprocess runner ──


async def run_isolated(
    source_code: str,
    wrapper_code: str,
    input_data: Any,
    timeout: float,
    allow_network: bool = False,
    prefix: str = "sandbox_",
) -> tuple[int, str, str]:
    """Run source_code + wrapper_code in an isolated subprocess.

    Writes code to a temp
    file, runs it with bubblewrap (Linux) or plain subprocess (elsewhere).
    The subprocess never has access to the server's Python objects.

    Args:
        source_code: User/catalog Python source
        wrapper_code: Injected wrapper that reads stdin and writes stdout
        input_data: Serializable data passed via stdin as JSON
        timeout: Max execution time in seconds
        allow_network: If True, bubblewrap won't block network (for tools)
        prefix: Temp directory prefix

    Returns:
        (exit_code, stdout, stderr)
    """
    workspace = tempfile.mkdtemp(prefix=prefix)
    script_path = os.path.join(workspace, "exec.py")

    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(source_code + "\n\n" + wrapper_code)

        use_bwrap = is_linux() and has_bwrap()
        seccomp_fd: int | None = None
        if use_bwrap:
            # Try seccomp: open inheritable fd for bwrap (must survive exec).
            try:
                from src.sandbox.seccomp import build_bpf_for_sandbox, write_bpf_file

                bpf = build_bpf_for_sandbox(allow_network=allow_network)
                if bpf:
                    bpf_path = write_bpf_file(bpf, workspace)
                    if bpf_path:
                        seccomp_fd = os.open(bpf_path, os.O_RDONLY)
                        os.set_inheritable(seccomp_fd, True)
            except Exception:
                pass

            cmd = build_bwrap_args(
                workspace,
                allow_network=allow_network,
                seccomp_fd=seccomp_fd,
            ) + [
                resolve_python(),
                script_path.replace(workspace, "/workspace"),
            ]
        else:
            cmd = [resolve_python(), script_path]

        spawn_env = subprocess_env(mode="strict", spawn_site="sandbox_isolated").env
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace if not use_bwrap else None,
            pass_fds=(seccomp_fd,) if seccomp_fd is not None else (),
            env=spawn_env,
        )

        input_bytes = json.dumps(input_data).encode("utf-8")

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=input_bytes),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            kill_process_tree(proc)
            try:
                await proc.communicate()
            except Exception:
                pass
            return (EXIT_CODE_TIMEOUT, "", f"Timed out after {timeout}s")

        return (
            proc.returncode or 0,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )
    finally:
        if seccomp_fd is not None:
            try:
                os.close(seccomp_fd)
            except Exception:
                pass
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception:
            pass
