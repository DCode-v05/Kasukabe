// Agent Reasoning tab - "the research centrepiece" (DESIGN.md section 8):
// for any decision, retrieved memories -> prompt -> reasoning text ->
// chosen tool call -> result, with model, lane, tokens and cost. The
// causal chain is rendered top to bottom in that exact order so "the
// agent bought flour" and "here is why, and what it cost to think that"
// are the same screen.

import { fetchReasoningDetail, fetchReasoningList } from "./api.js";
import { el, escapeHtml, fmtCents, fmtTick, fmtUsd, setEmpty, setError, setLoading } from "./dom.js";
import { getSelectedAgent } from "./state.js";

let callsCache = [];
let selectedCallId = null;

export async function loadReasoning() {
  const selected = getSelectedAgent();
  const listContainer = el("reasoning-list");
  const detailContainer = el("reasoning-detail");
  if (!selected) {
    listContainer.innerHTML = `<div class="panel-hint">Pick an agent in the Finder tab first.</div>`;
    detailContainer.innerHTML = "";
    return;
  }
  setLoading(listContainer);
  detailContainer.innerHTML = `<div class="panel-hint">Select a call on the left to see its full reasoning chain.</div>`;
  try {
    callsCache = await fetchReasoningList(selected.agentId, 200);
    if (!callsCache.length) {
      setEmpty(
        listContainer,
        "No LLM calls recorded for this agent (the trace store's llm_calls table is empty for this deployment - see this track's report)."
      );
      return;
    }
    selectedCallId = callsCache[0].id;
    renderList();
    openDetail(selectedCallId);
  } catch (err) {
    setError(listContainer, err);
  }
}

function renderList() {
  const container = el("reasoning-list");
  container.innerHTML = `<div class="call-list">${callsCache
    .map(
      (c) => `
      <div class="call-row ${c.id === selectedCallId ? "selected" : ""}" data-call-id="${escapeHtml(c.id)}">
        <span class="tick">${c.tick !== null ? "tick " + c.tick : "—"}</span>
        <span>
          <div>${escapeHtml(c.purpose)} ${c.success ? "" : '<span class="badge badge-danger">failed</span>'}</div>
          <div class="lane">${escapeHtml(c.lane)} · ${escapeHtml(c.call_class)}</div>
        </span>
        <span class="lane">${c.input_tokens}→${c.output_tokens} tok</span>
        <span class="cost">${fmtUsd(c.cost_usd)}</span>
      </div>`
    )
    .join("")}</div>`;
  container.querySelectorAll(".call-row").forEach((row) => {
    row.addEventListener("click", () => {
      selectedCallId = row.dataset.callId;
      renderList();
      openDetail(selectedCallId);
    });
  });
}

async function openDetail(callId) {
  const selected = getSelectedAgent();
  const container = el("reasoning-detail");
  setLoading(container, "Loading reasoning chain…");
  try {
    const detail = await fetchReasoningDetail(selected.agentId, callId);
    container.innerHTML = render(detail);
  } catch (err) {
    setError(container, err);
  }
}

function renderMessages(requestBody) {
  if (!requestBody) return `<p class="panel-hint">No prompt recorded.</p>`;
  const system = requestBody.system;
  const messages = Array.isArray(requestBody.messages) ? requestBody.messages : [];
  const systemHtml = system ? `<div class="msg"><div class="msg-role">system</div>${escapeHtml(typeof system === "string" ? system : JSON.stringify(system))}</div>` : "";
  const messageHtml = messages
    .map((m) => {
      const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2);
      return `<div class="msg"><div class="msg-role">${escapeHtml(m.role || "?")}</div>${escapeHtml(content)}</div>`;
    })
    .join("");
  return systemHtml + messageHtml || `<p class="panel-hint">Prompt body did not contain a recognizable messages array.</p>`;
}

function render(d) {
  const memoriesHtml = d.nearby_memories.length
    ? `<div class="list">${d.nearby_memories
        .map((m) => `<div class="list-item">[${m.memory_type}, importance ${m.importance}, tick ${m.tick}] ${escapeHtml(m.content)}</div>`)
        .join("")}</div>`
    : `<p class="panel-hint">No memories recorded for this agent at or before this tick.</p>`;

  const textBlocks = d.extracted.text_blocks.length
    ? d.extracted.text_blocks.map((t) => `<div class="msg">${escapeHtml(t)}</div>`).join("")
    : `<p class="panel-hint">No text reasoning block extracted (see raw response below).</p>`;

  const toolCalls = d.extracted.tool_calls.length
    ? d.extracted.tool_calls
        .map(
          (t) => `<div class="tool-call-box"><div class="tool-call-name">${escapeHtml(t.name || "?")}</div>
            <pre class="tool-call-input">${escapeHtml(JSON.stringify(t.input, null, 2))}</pre></div>`
        )
        .join("")
    : `<p class="panel-hint">No tool call in this response.</p>`;

  const matchedTools = d.matched_tool_calls.length
    ? d.matched_tool_calls
        .map((t) => `<div class="tool-call-box"><div class="tool-call-name">${escapeHtml(t.tool_name)}</div>
          <pre class="tool-call-input">${escapeHtml(JSON.stringify(t.params, null, 2))}</pre></div>`)
        .join("")
    : `<p class="panel-hint">No matching world.tool_invocations entry found for this tick.</p>`;

  return `
    <h3>${escapeHtml(d.purpose)} <span class="badge ${d.success ? "badge-ok" : "badge-danger"}">${d.success ? "success" : "failed"}</span></h3>
    <p class="panel-hint">${fmtTick(d.tick)} · ${escapeHtml(d.lane)} (requested ${escapeHtml(d.requested_model)})</p>

    <div class="chain">
      <div class="chain-step">
        <div class="chain-rail"><div class="chain-dot"></div><div class="chain-line"></div></div>
        <div class="chain-content">
          <div class="chain-label">1 — Retrieved memories</div>
          ${memoriesHtml}
          <div class="chain-note">${escapeHtml(d.nearby_memories_note)}</div>
        </div>
      </div>
      <div class="chain-step">
        <div class="chain-rail"><div class="chain-dot"></div><div class="chain-line"></div></div>
        <div class="chain-content">
          <div class="chain-label">2 — Prompt</div>
          ${renderMessages(d.request_body)}
          <details class="raw-json"><summary>raw request_body</summary><pre>${escapeHtml(JSON.stringify(d.request_body, null, 2))}</pre></details>
        </div>
      </div>
      <div class="chain-step">
        <div class="chain-rail"><div class="chain-dot"></div><div class="chain-line"></div></div>
        <div class="chain-content">
          <div class="chain-label">3 — Reasoning text</div>
          ${textBlocks}
        </div>
      </div>
      <div class="chain-step">
        <div class="chain-rail"><div class="chain-dot"></div><div class="chain-line"></div></div>
        <div class="chain-content">
          <div class="chain-label">4 — Chosen tool call</div>
          ${toolCalls}
          <details class="raw-json"><summary>raw response_body</summary><pre>${escapeHtml(JSON.stringify(d.response_body, null, 2))}</pre></details>
        </div>
      </div>
      <div class="chain-step">
        <div class="chain-rail"><div class="chain-dot"></div></div>
        <div class="chain-content">
          <div class="chain-label">5 — Result</div>
          ${matchedTools}
          <div class="chain-note">${escapeHtml(d.matched_tool_calls_note)}</div>
        </div>
      </div>
    </div>

    <h3 style="margin-top:1rem">Model, lane, cost</h3>
    <div class="meta-grid">
      <div class="meta-item"><div class="k">Model served</div><div class="v">${escapeHtml(d.model)}</div></div>
      <div class="meta-item"><div class="k">Region</div><div class="v">${escapeHtml(d.region || "—")}</div></div>
      <div class="meta-item"><div class="k">Call class</div><div class="v">${escapeHtml(d.call_class)}</div></div>
      <div class="meta-item"><div class="k">Tokens in/out</div><div class="v">${d.input_tokens} / ${d.output_tokens}</div></div>
      <div class="meta-item"><div class="k">Cost</div><div class="v">${fmtUsd(d.cost_usd)}</div></div>
      <div class="meta-item"><div class="k">Debited</div><div class="v">${fmtCents(d.cents_debited)}</div></div>
      <div class="meta-item"><div class="k">Latency</div><div class="v">${d.latency_ms}ms</div></div>
    </div>
    ${!d.success ? `<p style="color:var(--danger);margin-top:0.6rem">${escapeHtml(d.error_type || "")}: ${escapeHtml(d.error_message || "")}</p>` : ""}`;
}
