"""September Engine — terminal client (TUI).

A **pure HTTP/SSE client** of the engine's v2 block wire. It connects to a
separately-running engine (``ENGINE_BASE_URL`` + ``ENGINE_API_KEY``) and does
NOT boot the engine in-process — it is the terminal counterpart of the browser
harness, and the first real (non-harness) consumer that proves the wire is
independently adoptable.

What it speaks:
- ``POST /execute`` (SSE block stream) for the conversation; renders the 27
  block types into a scrolling log. Runs at ``telemetry=full`` + ``super_thinking``
  + ``tool`` + ``craft`` so the stream shows everything.
- ``block_input`` (a follow-up ``/execute`` with ``kind=block_input``) to answer
  a paused craft / HITL prompt.
- ``/channel/*`` to subscribe a craft's live-data channels and inject updates.
- the ``control`` RPC (``list_threads`` / ``release_thread``) for the thread panel,
  and a handful of REST routes for ``/skills`` ``/soul`` ``/knowledge`` ``/episodes``
  ``/social`` ``/lc`` ``/assets`` ``/health``.

Run:
    ENGINE_API_KEY=sk-sept-... python tui.py            # interactive
    ENGINE_API_KEY=sk-sept-... python tui.py --base-url http://host:8000
    ENGINE_API_KEY=sk-sept-... python tui.py --input "hello"   # one-shot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Callable

import httpx

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from rich.text import Text

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# The TUI is a power-user surface: open every gate so the stream shows the full
# picture (reasoning, tools, crafts, and full telemetry incl. hook/memory).
FULL_GATES: dict[str, Any] = {
    "craft": True,
    "craft_wait": True,
    "tool": True,
    "telemetry": "full",
    "super_thinking": True,
}


# ── SSE parsing (mirrors harness/app.js) ──────────────────────────────────


def parse_frame(raw: str) -> dict[str, Any]:
    """Parse one SSE frame (block of lines). Multi-line ``data:`` is concatenated."""
    frame: dict[str, Any] = {"event": "message", "data": "", "seq": None}
    for line in raw.split("\n"):
        if line.startswith("event:"):
            frame["event"] = line[6:].strip()
        elif line.startswith("data:"):
            frame["data"] += line[5:].lstrip(" ")
        elif line.startswith("seq:"):
            try:
                frame["seq"] = int(line[4:].strip())
            except ValueError:
                pass
    return frame


async def iter_sse(resp: httpx.Response):
    """Yield parsed SSE frames from a streaming response (``\\n\\n``-delimited)."""
    buf = ""
    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            if raw.strip():
                yield parse_frame(raw)


# ── Wire transport ────────────────────────────────────────────────────────


class Wire:
    """Thin async client over the engine's HTTP/SSE wire."""

    def __init__(self, base_url: str, key: str, transport: Any = None) -> None:
        self.base = base_url.rstrip("/")
        self.key = key
        self.transport = transport  # inject an httpx transport (e.g. ASGITransport) for tests

    def client(self, timeout: Any) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    def headers(self) -> dict[str, str]:
        return {"x-engine-key": self.key, "content-type": "application/json"}

    async def execute(
        self,
        *,
        text: str = "",
        thread_id: str | None = None,
        block_id: str | None = None,
        value: dict | None = None,
        gates: dict | None = None,
        on_block: Callable[[dict], None],
    ) -> dict:
        """Stream ``POST /execute``; call ``on_block`` per block. Returns the done payload."""
        kind = "block_input" if block_id else "input"
        body: dict[str, Any] = {
            "input": {"kind": kind, "text": text, "block_id": block_id, "value": value},
            "thread_id": thread_id,
            **(gates or FULL_GATES),
        }
        done: dict = {}
        async with self.client(None) as client:
            async with client.stream(
                "POST",
                f"{self.base}/execute",
                headers=self.headers(),
                json=body,
            ) as resp:
                resp.raise_for_status()
                async for frame in iter_sse(resp):
                    if frame["event"] != "block":
                        continue
                    block = json.loads(frame["data"])
                    on_block(block)
                    if block.get("type") == "done":
                        done = dict(block.get("payload", {}))
                        done.setdefault("thread_id", block.get("thread_id"))
        return done

    async def rpc(self, subsystem: str, op: str, **params: Any) -> dict:
        async with self.client(30) as client:
            r = await client.post(
                f"{self.base}/rpc",
                headers=self.headers(),
                json={"subsystem": subsystem, "op": op, **params},
            )
            return r.json()

    async def get(self, path: str) -> Any:
        async with self.client(30) as client:
            r = await client.get(f"{self.base}{path}", headers=self.headers())
            return r.json()

    async def post(self, path: str, body: dict) -> Any:
        async with self.client(30) as client:
            r = await client.post(f"{self.base}{path}", headers=self.headers(), json=body)
            return r.json()

    async def health(self) -> dict:
        # /health needs no auth and reports subsystem booleans.
        async with self.client(10) as client:
            r = await client.get(f"{self.base}/health")
            return r.json()

    async def channel_subscribe(self, craft_id: str, channels: list[dict]) -> dict:
        return await self.post("/channel/subscribe", {"craft_id": craft_id, "channels": channels})

    async def channel_stream(self, session_id: str, on_data: Callable[[dict], None]) -> None:
        async with self.client(None) as client:
            async with client.stream(
                "GET",
                f"{self.base}/channel/{session_id}/stream",
                headers=self.headers(),
            ) as resp:
                resp.raise_for_status()
                async for frame in iter_sse(resp):
                    if frame["event"] == "data":
                        on_data(json.loads(frame["data"]))


# ── Widgets ───────────────────────────────────────────────────────────────


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar { dock: top; height: 1; background: $surface; color: $text-muted; padding: 0 1; }
    """

    def refresh_status(self, health: dict, thread_count: int) -> None:
        status = health.get("status", "?")
        subs = health.get("subsystems", {}) or {}
        up = sum(1 for v in subs.values() if v)
        self.update(f" engine: {status} | subsystems {up}/{len(subs)} | threads: {thread_count}")


class ThreadPanel(VerticalScroll):
    """Left sidebar: active threads from the control RPC (selectable)."""

    DEFAULT_CSS = """
    ThreadPanel { height: 1fr; min-height: 4; padding: 0; }
    ThreadPanel .slot-item { padding: 0 1; height: 3; }
    ThreadPanel .slot-item.selected { background: $accent; color: $text; }
    """

    def refresh_threads(self, threads: list[dict], selected_id: str = "") -> None:
        self.remove_children()
        if not threads:
            self.mount(Static("[dim]No active threads[/dim]"))
            return
        for t in threads:
            tid = t.get("thread_id", "")
            short = tid[:8]
            selected = tid == selected_id
            style = "bold cyan" if selected else "dim"
            marker = "▶ " if selected else "  "
            summary = (t.get("summary") or t.get("current_state") or "...")[:18]
            label = (
                f"{marker}[{style}]{short}[/{style}]\n  {t.get('turn_count', 0)} turns | {summary}"
            )
            self.mount(Static(label, classes="slot-item selected" if selected else "slot-item"))


class SubsystemHealth(Static):
    DEFAULT_CSS = """
    SubsystemHealth { height: auto; max-height: 16; padding: 0 1; }
    """

    def refresh_health(self, health: dict) -> None:
        subs = health.get("subsystems", {}) or {}
        if not subs:
            self.update("[dim]no health data[/dim]")
            return
        lines = []
        for name, ok in sorted(subs.items()):
            icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
            lines.append(f" {icon} {name}")
        self.update("\n".join(lines))


class ExecutionStream(RichLog):
    """Main panel: renders the /execute block stream for the active thread."""

    DEFAULT_CSS = """
    ExecutionStream { height: 2fr; min-height: 8; border-bottom: solid $surface-darken-2; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.text_buf: str = ""
        self.reasoning_buf: str = ""

    def flush(self) -> None:
        if self.text_buf:
            self.write(Text(self.text_buf))
            self.text_buf = ""
        if self.reasoning_buf:
            self.write(Text(self.reasoning_buf, style="dim italic"))
            self.reasoning_buf = ""

    def render_block(self, block: dict) -> None:
        """Render one wire Block (``{id,type,thread_id,ts,payload}``)."""
        btype = block.get("type", "")
        p = block.get("payload", {}) or {}

        # append-types accumulate; flush on newline
        if btype == "text":
            self.text_buf += p.get("text", "")
            while "\n" in self.text_buf:
                line, self.text_buf = self.text_buf.split("\n", 1)
                if line:
                    self.write(Text(line))
            return
        if btype in ("reasoning", "voice"):
            self.reasoning_buf += p.get("text", "")
            while "\n" in self.reasoning_buf:
                line, self.reasoning_buf = self.reasoning_buf.split("\n", 1)
                if line:
                    self.write(Text(line, style="dim italic"))
            return

        self.flush()

        if btype == "tool":
            phase = p.get("phase")
            if phase == "call":
                badges = {"asset": "[ASSET]", "skill": "[SKILL]", "engine": "[INTERNAL]"}
                badge = badges.get(p.get("source", ""), "[TOOL]")
                self.write(
                    Text(
                        f"  {badge} > {p.get('tool_name', '?')}({str(p.get('params', ''))[:120]})",
                        style="dim",
                    )
                )
            else:
                res = str(p.get("result", ""))
                display = res[:200] + "..." if len(res) > 200 else res
                st = p.get("status", "")
                color = "green" if st == "success" else "red" if st == "error" else "dim"
                dur = p.get("duration") or p.get("duration_ms")
                self.write(Text(f"  < {display}{f' ({dur}ms)' if dur else ''}", style=color))
        elif btype == "thread":
            st = p.get("status", "")
            tid = (block.get("thread_id") or "")[:8]
            if st == "started":
                self.write(
                    Text(
                        f"▶ Thread started (model={p.get('model_id', '')}, id={tid})",
                        style="bold cyan",
                    )
                )
            elif st == "failed":
                self.write(Text(f"▶ Thread failed (id={tid})", style="bold red"))
        elif btype == "progress":
            items = p.get("items")
            if items:
                self.write(Text(f"\n  Goals ({len(items)}):", style="bold cyan"))
                for it in items:
                    self.write(Text(f"    [ ] {it.get('label', '')}"))
            else:
                st, lbl, nid = p.get("status", ""), p.get("label", ""), p.get("node_id", "")
                mark = {
                    "completed": ("✓", "green"),
                    "in_progress": ("⟳", "yellow"),
                    "failed": ("✗", "red"),
                    "skipped": ("-", "dim"),
                }.get(st, ("•", "green"))
                self.write(Text(f"  {mark[0]} {lbl or nid}", style=mark[1]))
                if p.get("output_preview"):
                    self.write(Text(f"      {p['output_preview'][:100]}", style="dim"))
        elif btype == "craft":
            self.write(
                Text(
                    f"\n  ▢ Craft: {p.get('title', p.get('key', 'craft'))} "
                    f"[{p.get('format', '?')}/{p.get('surface', '?')}]",
                    style="bold magenta",
                )
            )
            if p.get("question"):
                self.write(Text(f"    {p['question']}", style="yellow"))
            for i, opt in enumerate(p.get("options", []) or [], 1):
                self.write(Text(f"      {i}) {opt.get('label', opt.get('value', '?'))}"))
            if p.get("subscribe"):
                aliases = ", ".join(s.get("as", "?") for s in p["subscribe"])
                self.write(Text(f"    live: {aliases}", style="cyan"))
            if p.get("options") or p.get("question"):
                self.write(Text("    answer with /pick <n> or just type a reply", style="dim"))
        elif btype == "sub_agent":
            name = p.get("agent_name", "")
            if p.get("result_summary"):
                s = p["result_summary"]
                self.write(Text(f"  ↳ Sub-Agent done: {s[:150]}", style="bold yellow"))
            else:
                self.write(Text(f"\n  ↳ Sub-Agent: {name}", style="bold yellow"))
                if p.get("task_description"):
                    self.write(Text(f"    Task: {p['task_description'][:100]}"))
        elif btype == "routing":
            self.write(
                Text(
                    f"  [routing: {p.get('intent', '')} → {(p.get('target_thread') or '?')[:8]}]",
                    style="dim",
                )
            )
        elif btype == "replan":
            self.write(Text("\n  ⚡ REPLAN", style="bold red"))
            self.write(Text(f"    Reason: {p.get('reason', '')}"))
        elif btype == "asset_required":
            self.write(
                Text(
                    f"\n  [ASSET REQUIRED] {p.get('server_name', '')} — /assets connect {p.get('server_name', '')}",
                    style="bold blue",
                )
            )
        elif btype == "summary":
            dur = (p.get("duration_ms", 0)) / 1000
            self.write(Text(f"\n  {'─' * 50}"))
            self.write(
                Text(
                    f"  Status: {p.get('status', '')} | {dur:.1f}s | "
                    f"{p.get('total_input_tokens', 0):,} in / {p.get('total_output_tokens', 0):,} out | "
                    f"${p.get('estimated_cost_usd', 0):.4f}"
                )
            )
        elif btype == "memory":
            self.write(
                Text(
                    f"  [WM] {p.get('memory_type', '')}: {p.get('key', '')} = {str(p.get('value_preview', ''))[:80]}",
                    style="dim",
                )
            )
        elif btype == "compaction":
            self.write(
                Text(
                    f"  [CTX] compaction ({p.get('method', '')}): "
                    f"{p.get('messages_before', 0)}→{p.get('messages_after', 0)} msgs, "
                    f"{p.get('tokens_before', 0):,}→{p.get('tokens_after', 0):,} tok",
                    style="dim cyan",
                )
            )
        elif btype == "hook":
            self.write(
                Text(
                    f"  [hook] {p.get('tool_name', p.get('trigger', ''))} "
                    f"({len(p.get('hook_outcomes', []))} hooks)",
                    style="dim",
                )
            )
        elif btype == "status":
            if p.get("reason"):
                self.write(Text(f"  ⚠ {p.get('reason')}", style="yellow"))
        elif btype in ("budget", "model", "file", "subprocess", "session"):
            self.write(Text(f"  [{btype}] {json.dumps(p)[:120]}", style="dim"))
        elif btype == "error":
            self.write(
                Text(
                    f"  ✗ ERROR: {p.get('message', p.get('error', json.dumps(p)))[:200]}",
                    style="bold red",
                )
            )
        elif btype == "done":
            st = p.get("status", "")
            if st == "awaiting":
                self.write(
                    Text(f"  … awaiting input (block {p.get('block_id', '')})", style="yellow")
                )
            else:
                self.write(Text(f"  ── done ({st}) ──", style="dim"))

    def channel_update(self, frame: dict) -> None:
        kind, alias = frame.get("kind", ""), frame.get("as", "")
        data = frame.get("data")
        preview = json.dumps(data)[:160] if data is not None else ""
        self.write(
            Text(f"  [live {alias} rev{frame.get('rev', '?')} {kind}] {preview}", style="cyan")
        )


# ── App ─────────────────────────────────────────────────────────────────


class EngineApp(App):
    TITLE = "September Engine — terminal client"
    CSS = """
    #sidebar { width: 24; border-right: solid $surface-darken-2; }
    #sidebar-label-threads, #sidebar-label-subs {
        height: 1; padding: 0 1; background: $surface; text-style: bold;
    }
    #main-area { width: 1fr; }
    #inspector-tabs { height: 1fr; min-height: 6; }
    #input-bar { dock: bottom; margin: 0 0; }
    """

    BINDINGS = [
        Binding("f1", "show_help", "Help"),
        Binding("f2", "refresh", "Refresh"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    selected_thread_id: reactive[str] = reactive("")
    awaiting_block_id: reactive[str] = reactive("")

    def __init__(self, wire: Wire) -> None:
        super().__init__()
        self.wire = wire

    def compose(self) -> ComposeResult:
        yield Header()
        yield StatusBar(id="status-bar")
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("THREADS", id="sidebar-label-threads")
                yield ThreadPanel(id="thread-panel")
                yield Label("SUBSYSTEMS", id="sidebar-label-subs")
                yield SubsystemHealth(id="subsystem-health")
            with Vertical(id="main-area"):
                yield ExecutionStream(id="stream", wrap=True, markup=False)
                with TabbedContent(id="inspector-tabs"):
                    with TabPane("Inspector", id="tab-info"):
                        yield RichLog(id="info-log", wrap=True, markup=True)
        yield Input(placeholder="Message the engine…  (/help for commands)", id="input-bar")
        yield Footer()

    # — lifecycle —

    async def on_mount(self) -> None:
        self.stream = self.query_one("#stream", ExecutionStream)
        self.info = self.query_one("#info-log", RichLog)
        self.stream.write(Text(f"Connected to {self.wire.base}", style="dim"))
        await self.refresh_panels()

    async def refresh_panels(self) -> None:
        try:
            health = await self.wire.health()
        except Exception as e:
            health = {"status": f"unreachable ({e})", "subsystems": {}}
        try:
            threads = (await self.wire.rpc("control", "list_threads")).get("threads", [])
        except Exception:
            threads = []
        self.query_one("#subsystem-health", SubsystemHealth).refresh_health(health)
        self.query_one("#thread-panel", ThreadPanel).refresh_threads(
            threads, self.selected_thread_id
        )
        self.query_one("#status-bar", StatusBar).refresh_status(health, len(threads))

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_panels(), exclusive=False)

    def action_show_help(self) -> None:
        self.info.write(Text(HELP, style="cyan"))

    # — input —

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#input-bar", Input).value = ""
        if not text:
            return
        if text.startswith("/"):
            await self.handle_command(text)
            return
        self.stream.write(Text(f"\n› {text}", style="bold white"))
        self.send_message(text)

    @work(exclusive=False)
    async def send_message(self, text: str) -> None:
        """Stream a turn over /execute. If a block is awaiting, answer it instead."""
        block_id = self.awaiting_block_id or None
        self.awaiting_block_id = ""
        try:
            done = await self.wire.execute(
                text=text,
                thread_id=self.selected_thread_id or None,
                block_id=block_id,
                value={"response": text} if block_id else None,
                on_block=self.on_block,
            )
        except Exception as e:
            self.stream.write(Text(f"  ✗ wire error: {e}", style="bold red"))
            return
        if done.get("status") == "awaiting":
            self.awaiting_block_id = done.get("block_id", "")
        else:
            # adopt the thread we just drove so follow-ups route to it
            tid = done.get("thread_id")
            if tid:
                self.selected_thread_id = tid
        await self.refresh_panels()

    def on_block(self, block: dict) -> None:
        self.stream.render_block(block)
        if block.get("type") == "craft":
            self.last_craft = block
            subs = (block.get("payload") or {}).get("subscribe") or []
            if subs:
                self.subscribe_channels(block["payload"].get("key", ""), subs)

    @work(exclusive=False)
    async def subscribe_channels(self, craft_id: str, channels: list[dict]) -> None:
        try:
            sub = await self.wire.channel_subscribe(craft_id, channels)
            session = sub.get("session_id")
            if session:
                await self.wire.channel_stream(session, self.stream.channel_update)
        except Exception as e:
            self.stream.write(Text(f"  ✗ channel error: {e}", style="red"))

    # — commands —

    async def handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit"):
            self.exit()
        elif cmd == "/help":
            self.info.write(Text(HELP, style="cyan"))
        elif cmd in ("/threads", "/slots"):
            await self.refresh_panels()
            self.info.write(Text("threads refreshed", style="dim"))
        elif cmd == "/select":
            self.selected_thread_id = arg
            self.info.write(Text(f"active thread → {arg[:8]}", style="cyan"))
            await self.refresh_panels()
        elif cmd == "/release":
            tid = arg or self.selected_thread_id
            self.rpc_to_info("control", "release_thread", thread_id=tid)
        elif cmd == "/pick":
            craft = getattr(self, "last_craft", None)
            opts = ((craft or {}).get("payload") or {}).get("options") or []
            try:
                opt = opts[int(arg) - 1]
            except (ValueError, IndexError):
                self.info.write(Text("no such option", style="red"))
                return
            if craft is None:
                self.info.write(Text("no such option", style="red"))
                return
            self.awaiting_block_id = craft["id"]
            self.send_message(str(opt.get("value", opt.get("label", ""))))
        elif cmd == "/skills":
            self.get_to_info("/skills")
        elif cmd == "/soul":
            self.get_to_info("/memory/soul")
        elif cmd == "/knowledge":
            self.post_to_info("/memory/knowledge/search", {"query": arg, "limit": 10})
        elif cmd == "/episodes":
            self.post_to_info("/memory/episodes/search", {"query": arg, "limit": 10})
        elif cmd == "/social":
            self.post_to_info("/memory/social/search", {"query": arg, "limit": 10})
        elif cmd == "/lc":
            self.post_to_info("/admin/lc/trigger", {})
        elif cmd == "/assets":
            self.get_to_info("/assets/connections")
        elif cmd in ("/status", "/health"):
            self.get_to_info("/health/deep")
        elif cmd in ("/wm", "/memory", "/trace", "/tools", "/agents", "/actions"):
            self.info.write(Text(f"{cmd}: not available over the wire (TUI v2)", style="yellow"))
        else:
            self.info.write(Text(f"unknown command: {cmd}", style="red"))

    @work(exclusive=False)
    async def get_to_info(self, path: str) -> None:
        try:
            self.info.write(Text(json.dumps(await self.wire.get(path), indent=2)[:2000]))
        except Exception as e:
            self.info.write(Text(f"✗ {e}", style="red"))

    @work(exclusive=False)
    async def post_to_info(self, path: str, body: dict) -> None:
        try:
            self.info.write(Text(json.dumps(await self.wire.post(path, body), indent=2)[:2000]))
        except Exception as e:
            self.info.write(Text(f"✗ {e}", style="red"))

    @work(exclusive=False)
    async def rpc_to_info(self, subsystem: str, op: str, **params: Any) -> None:
        try:
            self.info.write(
                Text(json.dumps(await self.wire.rpc(subsystem, op, **params), indent=2)[:2000])
            )
        except Exception as e:
            self.info.write(Text(f"✗ {e}", style="red"))
        await self.refresh_panels()


HELP = """Commands:
  <text>                 send a message to the engine (routes to the selected thread)
  /threads /slots        refresh the thread panel (control.list_threads)
  /select <thread_id>    route follow-ups to a thread
  /release [thread_id]   release a thread's slot (control.release_thread)
  /pick <n>              answer the last craft prompt with option n
  /skills /soul          list skills / show soul
  /knowledge <q>         search knowledge   /episodes <q>   /social <q>
  /assets                list connections   /lc   trigger learning batch
  /status                deep health
  /quit                  exit
  (/wm /memory /trace /tools /agents /actions — not on the wire in TUI v2)
"""


# ── one-shot mode ─────────────────────────────────────────────────────────


async def run_oneshot(wire: Wire, query: str) -> None:
    def show(block: dict) -> None:
        t = block.get("type")
        p = block.get("payload", {}) or {}
        if t == "text":
            sys.stdout.write(p.get("text", ""))
            sys.stdout.flush()
        elif t == "error":
            sys.stderr.write(f"\n[error] {p.get('message', p)}\n")
        elif t == "done":
            sys.stdout.write(f"\n[done: {p.get('status')}]\n")

    await wire.execute(text=query, gates={"tool": True, "telemetry": "off"}, on_block=show)


def main() -> None:
    parser = argparse.ArgumentParser(description="September Engine terminal client")
    parser.add_argument("--base-url", default=os.environ.get("ENGINE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--key", default=os.environ.get("ENGINE_API_KEY", ""))
    parser.add_argument("--input", default=None, help="one-shot query (non-interactive)")
    args = parser.parse_args()

    if not args.key:
        sys.stderr.write("ENGINE_API_KEY not set (use --key or the env var)\n")
        sys.exit(2)

    wire = Wire(args.base_url, args.key)
    if args.input:
        asyncio.run(run_oneshot(wire, args.input))
    else:
        EngineApp(wire).run()


if __name__ == "__main__":
    main()
