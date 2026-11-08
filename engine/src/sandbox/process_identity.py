"""Process identity resolution: map a PID, or a live TCP connection, to a binary.

Used by the proxy to determine which binary initiated an outbound connection
so per-binary network policy rules can be evaluated. The proxy only sees an
accepted socket, so the full chain is: (peer address, local address) →
socket inode (/proc/net/tcp) → owning PID (/proc/<pid>/fd) → binary name
(/proc/<pid>/exe).

Every step is Linux-only and reads /proc. On other platforms resolution
returns None and per-binary rules therefore match nothing but "*", which is
the same failure mode as a connection from outside the sandbox.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

# IPv4-mapped IPv6 prefix (::ffff:a.b.c.d). A loopback client connecting to a
# dual-stack listener shows up as an IPv4-mapped address in /proc/net/tcp6 but
# as a plain IPv4 address in peername (or vice versa), so both forms are
# normalised to the bare 4-byte address before comparison.
V4_MAPPED_PREFIX = b"\x00" * 10 + b"\xff\xff"


def resolve_binary_from_pid(pid: int) -> str | None:
    """Resolve the executable path for a given PID.

    Returns absolute path string, or None if resolution fails.
    """
    if platform.system() == "Linux":
        return resolve_linux(pid)
    return None


def resolve_binary_name_from_pid(pid: int) -> str | None:
    """Resolve just the binary name (basename) for a PID."""
    path = resolve_binary_from_pid(pid)
    if path is None:
        return None
    return Path(path).name


def resolve_linux(pid: int) -> str | None:
    """Read /proc/<pid>/exe symlink."""
    try:
        exe_path = os.readlink(f"/proc/{pid}/exe")
        if exe_path.endswith(" (deleted)"):
            exe_path = exe_path[: -len(" (deleted)")]
        return exe_path
    except (OSError, PermissionError):
        return None


# ── Socket → PID → binary ─────────────────────────────────────


def resolve_pid_from_tcp_peer(
    peer: tuple[str, int],
    local: tuple[str, int],
) -> int | None:
    """Resolve the PID owning the client end of an accepted TCP connection.

    `peer` and `local` are the server's view of the connection: asyncio's
    "peername" and "sockname" respectively. Returns None when the connection
    cannot be attributed (non-Linux, the peer already exited, or the peer
    belongs to another user whose /proc/<pid>/fd is unreadable).

    The kernel indexes sockets by inode, and /proc/net/tcp lists each socket
    from its *owner's* point of view, so the client's row has our `peer` as
    its local address and our `local` as its remote address. Once the inode
    is known the owner is found by scanning /proc/<pid>/fd for a symlink to
    `socket:[<inode>]`.
    """
    if platform.system() != "Linux":
        return None

    inode = find_socket_inode(peer, local)
    if inode is None:
        return None
    return find_pid_owning_inode(inode)


def find_socket_inode(local: tuple[str, int], remote: tuple[str, int]) -> str | None:
    """Find the /proc/net/tcp{,6} inode for the socket with this address pair.

    `local`/`remote` are from the owning process's point of view, the same
    orientation /proc/net/tcp uses.
    """
    want_local = pack_address(local[0])
    want_remote = pack_address(remote[0])
    if want_local is None or want_remote is None:
        return None
    want = (want_local, local[1], want_remote, remote[1])

    for proc_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_file, "r", encoding="ascii", errors="replace") as f:
                next(f, None)  # header row
                for line in f:
                    fields = line.split()
                    if len(fields) < 10:
                        continue
                    parsed_local = parse_proc_address(fields[1])
                    parsed_remote = parse_proc_address(fields[2])
                    if parsed_local is None or parsed_remote is None:
                        continue
                    if (*parsed_local, *parsed_remote) == want:
                        return fields[9]
        except OSError:
            continue
    return None


def find_pid_owning_inode(inode: str) -> int | None:
    """Find which PID holds an open file descriptor for this socket inode.

    This is a linear scan of /proc; there is no reverse index from inode to
    PID in the kernel. Sandbox processes are few and the scan stops at the
    first match, so the cost is paid once per proxied connection.
    """
    target = f"socket:[{inode}]"
    try:
        pids = os.listdir("/proc")
    except OSError:
        return None

    for entry in pids:
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            # The process exited mid-scan, or belongs to another user.
            continue
        for fd in fds:
            try:
                if os.readlink(f"{fd_dir}/{fd}") == target:
                    return int(entry)
            except OSError:
                continue
    return None


def parse_proc_address(field: str) -> tuple[bytes, int] | None:
    """Parse a "HEXADDR:HEXPORT" field from /proc/net/tcp{,6}.

    The address is a hex dump of the in-kernel struct in host byte order:
    one 32-bit word for IPv4, four for IPv6. The port is big-endian hex.
    Returns (packed address, port) with IPv4-mapped IPv6 folded to IPv4.
    """
    addr_hex, sep, port_hex = field.partition(":")
    if not sep:
        return None
    try:
        port = int(port_hex, 16)
        if len(addr_hex) == 8:
            packed = struct.pack("<I", int(addr_hex, 16))
        elif len(addr_hex) == 32:
            packed = b"".join(
                struct.pack("<I", int(addr_hex[i : i + 8], 16)) for i in range(0, 32, 8)
            )
        else:
            return None
    except ValueError:
        return None
    return normalize_packed(packed), port


def pack_address(addr: str) -> bytes | None:
    """Pack a textual IP address for comparison with a /proc/net/tcp row."""
    addr = addr.partition("%")[0]  # drop any IPv6 zone id
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            return normalize_packed(socket.inet_pton(family, addr))
        except OSError:
            continue
    return None


def normalize_packed(packed: bytes) -> bytes:
    """Fold an IPv4-mapped IPv6 address down to its 4-byte IPv4 form."""
    if len(packed) == 16 and packed.startswith(V4_MAPPED_PREFIX):
        return packed[12:]
    return packed
