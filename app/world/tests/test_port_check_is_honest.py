"""The engine-port check has to agree with what an engine can actually bind.

It got this wrong in both directions, and the two mistakes have opposite
consequences:

  - Binding WITHOUT SO_REUSEADDR reported a port left in TIME_WAIT as
    busy. uvicorn sets that flag, so the engine would have taken the port
    without complaint. Measured: four consecutive relaunches refused with
    "a previous run is still shutting down" while lsof showed no listener
    at all - a clean machine, blocked for the ~60s of 2*MSL.

  - Binding to 127.0.0.1 specifically reports a port free while another
    process holds it on the wildcard address, because BSD allows the
    narrower bind. That is the dangerous inverse: it lets a run start on
    top of a run that is already there.
"""

from __future__ import annotations

import socket

from world.preflight import _ports_in_use

BASE = 9180  # away from the real engine range, so a live run cannot skew this


def test_a_free_port_is_reported_free() -> None:
    assert _ports_in_use(BASE, 4) == []


def test_a_real_listener_is_caught() -> None:
    """The check must still do its job: an engine already on the port."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", BASE + 2))
    srv.listen(5)
    try:
        assert BASE + 2 in _ports_in_use(BASE, 4)
    finally:
        srv.close()


def test_a_listener_on_the_wildcard_is_caught_not_just_loopback() -> None:
    """Engines bind "*:9100". A check that probes 127.0.0.1 specifically
    binds successfully alongside a wildcard socket on BSD and calls an
    occupied port free."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", BASE + 1))
    srv.listen(5)
    try:
        loopback_says_free = True
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", BASE + 1))
        except OSError:
            loopback_says_free = False
        finally:
            probe.close()

        # Whatever the platform does with the narrower bind, the check
        # itself must report the port busy.
        assert BASE + 1 in _ports_in_use(BASE, 4), (
            f"wildcard listener missed (loopback bind succeeded: "
            f"{loopback_says_free})"
        )
    finally:
        srv.close()


def test_a_port_in_time_wait_does_not_block_a_relaunch() -> None:
    """The exact false blocker: a run stopped seconds ago, its sockets in
    TIME_WAIT, and the next run refused for a minute over ports its own
    engines would have bound immediately."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", BASE + 3))
    srv.listen(1)
    client = socket.create_connection(("127.0.0.1", BASE + 3))
    accepted, _ = srv.accept()
    client.close()
    accepted.close()
    srv.close()

    assert BASE + 3 not in _ports_in_use(BASE, 4)


def test_it_sets_the_same_flag_the_engine_does() -> None:
    """Stated in code, not just in behaviour - the behaviour above is
    platform-dependent enough to be worth pinning at the source too."""
    import inspect

    source = inspect.getsource(_ports_in_use)
    assert "SO_REUSEADDR" in source, (
        "without it a port in TIME_WAIT blocks a clean relaunch"
    )
    # Both addresses: probing only the loopback misses a wildcard listener,
    # and probing only the wildcard misses a loopback-only one.
    assert '"127.0.0.1"' in source
    assert '""' in source or "('', port)" in source or '("", port)' in source


def test_a_loopback_only_listener_is_caught_too() -> None:
    """The inverse blind spot. Probing only the wildcard address binds
    successfully alongside a socket held on 127.0.0.1, and reports an
    occupied port free - which lets a run start on top of one already
    there, the worse of the two failures this check can have."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", BASE + 3))
    srv.listen(5)
    try:
        assert BASE + 3 in _ports_in_use(BASE, 4)
    finally:
        srv.close()


# ── The third mistake, and the one that made this check dead on Windows ──
#
# SO_REUSEADDR does not mean the same thing on the two platforms. On POSIX
# it waives the TIME_WAIT reservation and a LISTENing socket still refuses
# the second bind, which is what makes bind() a usable probe there. On
# Windows it waives the conflict outright - it is closer to POSIX's
# SO_REUSEPORT - so the bind SUCCEEDS against a live listener.
#
# Measured on this machine, with a listener on *:9187:
#
#     bind ''          reuse=True   -> SUCCEEDED   (probe says FREE)
#     bind ''          reuse=False  -> refused 10048
#     bind '127.0.0.1' reuse=True   -> SUCCEEDED   (probe says FREE)
#     bind '127.0.0.1' reuse=False  -> SUCCEEDED   (probe says FREE)
#     connect '127.0.0.1'           -> SUCCEEDED   (listener detected)
#     connect after close           -> refused     (correctly free)
#
# So on the machine every run of this project has been launched from, the
# check that exists to stop a run starting on top of another run returned
# [] no matter what was running. A green board over a dead check, which is
# gate 3 of Build Spec v2 section 1 inverted.
#
# connect() is the honest question. The docstring rejected it for a reason
# that does not hold: a port in TIME_WAIT has no listener, so connect()
# refuses, as the last line of the table above shows.


def test_a_listener_is_caught_even_where_bind_would_allow_it() -> None:
    """The regression that matters. If somebody reintroduces a bind-only
    probe, this goes red on Windows and stays green on POSIX - which is
    the correct asymmetry, because the defect only ever existed here."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", BASE + 3))
    srv.listen(5)
    try:
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("", BASE + 3))
            bind_alone_would_say_free = True
        except OSError:
            bind_alone_would_say_free = False
        finally:
            probe.close()

        assert BASE + 3 in _ports_in_use(BASE, 4), (
            "a live listener was reported as a free port"
            + (
                " - and a bind probe agrees it is free on this platform, which "
                "is exactly the case bind alone cannot answer"
                if bind_alone_would_say_free
                else ""
            )
        )
    finally:
        srv.close()


def test_a_port_freed_a_moment_ago_is_not_reported_busy() -> None:
    """The other direction, and the reason the original check reached for
    SO_REUSEADDR in the first place: four consecutive relaunches were once
    refused with "a previous run is still shutting down" on a machine with
    nothing running at all. A closed listener leaves TIME_WAIT behind but
    no listener, so connect() refuses and the port reads free."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", BASE + 3))
    srv.listen(5)
    client = socket.socket()
    client.connect(("127.0.0.1", BASE + 3))
    accepted, _ = srv.accept()
    client.close()
    accepted.close()
    srv.close()

    assert BASE + 3 not in _ports_in_use(BASE, 4)


def test_checking_the_whole_engine_range_is_not_a_minute_of_waiting() -> None:
    """A run of 250 residents checks 250 ports.

    Measured when the probe was serial: 64.6 seconds, because Windows does
    not answer a connect to an unused loopback port - it drops the SYN, so
    every free port costs the full timeout, and a healthy machine is the
    worst case. A preflight check that adds a minute to every launch is a
    preflight check somebody passes --skip-preflight to get around, and
    then it protects nothing at all.
    """
    import time

    started = time.perf_counter()
    assert _ports_in_use(BASE + 100, 250) == []
    assert time.perf_counter() - started < 8.0
