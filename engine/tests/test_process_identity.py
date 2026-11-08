"""Tests for process identity resolution."""

import os
import platform
import socket
from unittest.mock import patch

import pytest


from src.sandbox.process_identity import (
    normalize_packed,
    pack_address,
    parse_proc_address,
    resolve_binary_from_pid,
    resolve_binary_name_from_pid,
    resolve_pid_from_tcp_peer,
)


class TestResolveBinary:
    def test_resolve_current_process_returns_something(self):
        result = resolve_binary_from_pid(os.getpid())
        if platform.system() == "Linux":
            assert result is not None
            assert "python" in result.lower()
        else:
            assert result is None  # stubs return None on non-Linux

    def test_resolve_nonexistent_pid(self):
        result = resolve_binary_from_pid(99_999_999)
        assert result is None

    def test_resolve_negative_pid(self):
        result = resolve_binary_from_pid(-1)
        assert result is None

    def test_binary_name_from_current_process(self):
        result = resolve_binary_name_from_pid(os.getpid())
        if platform.system() == "Linux":
            assert result is not None
            assert "python" in result.lower()
        else:
            assert result is None

    def test_binary_name_from_nonexistent(self):
        assert resolve_binary_name_from_pid(99_999_999) is None

    @patch("src.sandbox.process_identity.platform.system", return_value="Darwin")
    def test_returns_none_on_macos(self, _):
        assert resolve_binary_from_pid(os.getpid()) is None

    @patch("src.sandbox.process_identity.platform.system", return_value="Windows")
    def test_returns_none_on_windows(self, _):
        assert resolve_binary_from_pid(os.getpid()) is None


class TestAddressParsing:
    """/proc/net/tcp writes addresses as a host-byte-order hex dump."""

    def test_ipv4_loopback(self):
        assert parse_proc_address("0100007F:1F90") == (socket.inet_aton("127.0.0.1"), 8080)

    def test_ipv6_loopback(self):
        packed, port = parse_proc_address("00000000000000000000000001000000:0050")
        assert packed == socket.inet_pton(socket.AF_INET6, "::1")
        assert port == 80

    def test_ipv4_mapped_ipv6_folds_to_ipv4(self):
        packed, _ = parse_proc_address("0000000000000000FFFF00000100007F:0050")
        assert packed == socket.inet_aton("127.0.0.1")

    def test_malformed_field_returns_none(self):
        assert parse_proc_address("not-an-address") is None
        assert parse_proc_address("ZZZZ:0050") is None
        assert parse_proc_address("0100007F") is None

    def test_pack_address_matches_proc_form(self):
        assert pack_address("127.0.0.1") == parse_proc_address("0100007F:0000")[0]

    def test_pack_address_strips_ipv6_zone(self):
        assert pack_address("fe80::1%eth0") == socket.inet_pton(socket.AF_INET6, "fe80::1")

    def test_pack_address_rejects_hostname(self):
        assert pack_address("localhost") is None

    def test_normalize_leaves_real_ipv6_alone(self):
        addr = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
        assert normalize_packed(addr) == addr


class TestResolvePidFromTcpPeer:
    def test_resolves_this_process_from_a_live_connection(self):
        """A loopback connection to ourselves must resolve back to our own PID."""
        if platform.system() != "Linux":
            pytest.skip("socket ownership is resolved through /proc")

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.create_connection(listener.getsockname())
        server, _ = listener.accept()
        try:
            # From the server's side the client is the peer, exactly what the
            # proxy passes in.
            pid = resolve_pid_from_tcp_peer(server.getpeername(), server.getsockname())
            assert pid == os.getpid()
        finally:
            client.close()
            server.close()
            listener.close()

    def test_unknown_connection_returns_none(self):
        if platform.system() != "Linux":
            pytest.skip("socket ownership is resolved through /proc")

        # A port pair with no corresponding socket must not resolve to anything.
        assert resolve_pid_from_tcp_peer(("127.0.0.1", 1), ("127.0.0.1", 2)) is None

    @patch("src.sandbox.process_identity.platform.system", return_value="Darwin")
    def test_returns_none_off_linux(self, _):
        assert resolve_pid_from_tcp_peer(("127.0.0.1", 1), ("127.0.0.1", 2)) is None
