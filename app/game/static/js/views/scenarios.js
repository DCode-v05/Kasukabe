// Scenarios tab: curated feed of the moments that matter, each
// expandable into its full causal trace. DESIGN.md section 8's
// "Scenario / special-event viewer" bullet.

import { fetchScenarioTrace, fetchScenarios } from "./api.js";
import { categoryBadge, debounce, el, escapeHtml, fmtTick, setEmpty, setError, setLoading } from "./dom.js";

const CATEGORIES = [
  "", "crime", "trial", "birth", "death", "marriage", "disaster", "miracle",
  "cult", "business", "amendment", "regime", "election",
];

let traceCache = {}; // event_id -> trace entries, once fetched
let expandedId = null;

export async function loadScenarios() {
  wireFilters();
  await refreshScenarios();
}

function wireFilters() {
  const categorySelect = el("scn-category");
  if (categorySelect.dataset.wired) return;
  categorySelect.dataset.wired = "1";
  categorySelect.innerHTML = CATEGORIES.map((c) => `<option value="${c}">${c || "all categories"}</option>`).join("");
  categorySelect.addEventListener("change", refreshScenarios);
  el("scn-agent").addEventListener("input", debounce(refreshScenarios, 350));
  el("scn-refresh").addEventListener("click", refreshScenarios);
}

async function refreshScenarios() {
  const container = el("scn-feed");
  setLoading(container);
  try {
    const entries = await fetchScenarios({
      category: el("scn-category").value || undefined,
      agent_id: el("scn-agent").value || undefined,
      limit: 150,
    });
    if (!entries.length) {
      setEmpty(container, "No scenario events match this filter yet.");
      return;
    }
    render(entries);
  } catch (err) {
    setError(container, err);
  }
}

function render(entries) {
  const container = el("scn-feed");
  container.innerHTML = `<div class="list">${entries.map((e) => renderCard(e)).join("")}</div>`;
  container.querySelectorAll(".card").forEach((cardEl) => {
    cardEl.addEventListener("click", async () => {
      const id = cardEl.dataset.eventId;
      expandedId = expandedId === id ? null : id;
      render(entries);
      if (expandedId === id) await loadTrace(id);
    });
  });
}

function renderCard(entry) {
  const expanded = entry.event_id === expandedId;
  return `
    <div class="card ${expanded ? "expanded" : ""}" data-event-id="${escapeHtml(entry.event_id)}">
      <div class="card-head">
        <span class="card-title">${escapeHtml(entry.headline)}</span>
        <span class="card-meta">${categoryBadge(entry.category)} ${fmtTick(entry.tick, entry.game_day)}</span>
      </div>
      <div class="card-body">
        <code>${escapeHtml(entry.type)}</code>${entry.agent_name ? " · " + escapeHtml(entry.agent_name) : ""}
      </div>
      ${expanded ? `<div class="card-detail" id="scn-trace-${escapeHtml(entry.event_id)}"><div class="list-loading">Loading causal trace…</div></div>` : ""}
    </div>`;
}

async function loadTrace(eventId) {
  const container = document.getElementById(`scn-trace-${eventId}`);
  if (!container) return;
  try {
    if (!traceCache[eventId]) traceCache[eventId] = await fetchScenarioTrace(eventId);
    const steps = traceCache[eventId];
    if (steps.length <= 1) {
      container.innerHTML = `<p class="card-meta">No other correlated events found - this is the whole trace.</p>`;
      return;
    }
    container.innerHTML = `
      <h3>Causal trace (${steps.length} correlated event(s))</h3>
      <div class="trace-chain">
        ${steps
          .map(
            (s) => `<div class="trace-step">
              <div class="trace-rail"><div class="trace-dot"></div><div class="trace-line"></div></div>
              <div class="trace-content">
                <div class="card-head"><strong>${escapeHtml(s.headline)}</strong>${categoryBadge(s.category)}</div>
                <div class="card-meta">${fmtTick(s.tick, s.game_day)} · <code>${escapeHtml(s.type)}</code></div>
                <details class="raw-json"><summary>payload</summary><pre>${escapeHtml(JSON.stringify(s.payload, null, 2))}</pre></details>
              </div>
            </div>`
          )
          .join("")}
      </div>`;
  } catch (err) {
    setError(container, err);
  }
}
