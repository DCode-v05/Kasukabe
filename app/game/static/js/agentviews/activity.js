// Agent Activity tab: what one agent is doing right now - action and
// why, location, who is with it, schedule status, wallets, health,
// skills, relationships, live utterances. DESIGN.md section 8's "Agent
// activity viewer" bullet.

import { fetchAgentActivity } from "./api.js";
import { el, escapeHtml, fmtCents, fmtTick, setError, setLoading, statusBadge } from "./dom.js";
import { getSelectedAgent } from "./state.js";

let goToReasoningTab = () => {};

export function initActivity(onGoToReasoning) {
  goToReasoningTab = onGoToReasoning;
}

export async function loadActivity() {
  const selected = getSelectedAgent();
  const container = el("activity-body");
  if (!selected) {
    container.innerHTML = `<div class="panel-hint">Pick an agent in the Finder tab first.</div>`;
    return;
  }
  setLoading(container);
  try {
    const a = await fetchAgentActivity(selected.agentId);
    container.innerHTML = render(a);
  } catch (err) {
    setError(container, err);
  }
}

function render(a) {
  const ailment = a.health.current_ailment;
  return `
    <div class="grid-2">
      <div class="panel">
        <h3>Right now</h3>
        <p><strong>${escapeHtml(a.place || "location unknown")}</strong> ${statusBadge(a.status)}</p>
        <p class="panel-hint">Co-located: ${a.co_located_agents.length ? a.co_located_agents.map((c) => escapeHtml(c.name)).join(", ") : "nobody else here"}</p>
        ${a.last_known_action
          ? `<p><em>Last known action</em>: <code>${escapeHtml(a.last_known_action.event_type)}</code> at ${fmtTick(a.last_known_action.tick)}</p>
             <details class="raw-json"><summary>why (derived from the event log)</summary><pre>${escapeHtml(JSON.stringify(a.last_known_action.summary, null, 2))}</pre></details>`
          : `<p class="panel-hint">No recorded action for this agent yet.</p>`}
        ${a.schedule
          ? `<p class="panel-hint">Next scheduled decision: tick ${a.schedule.next_decision_tick} (cadence ${a.schedule.cadence_ticks} ticks)${a.schedule.interrupted ? " · interrupted early" : ""}</p>`
          : ""}
      </div>

      <div class="panel">
        <h3>Wallet &amp; health</h3>
        <div class="meta-grid">
          <div class="meta-item"><div class="k">USD wallet</div><div class="v">${fmtCents(a.usd_wallet_cents)}</div></div>
          <div class="meta-item"><div class="k">Sim balance</div><div class="v">${a.sim_balance.toLocaleString()}</div></div>
          <div class="meta-item"><div class="k">Age</div><div class="v">${a.age_years.toFixed(1)}${a.is_minor ? " (minor)" : ""}</div></div>
        </div>
        ${ailment
          ? `<p style="margin-top:0.6rem">Sick: <strong>${escapeHtml(ailment.kind)}</strong>, onset day ${ailment.onset_day}${ailment.duration_days ? `, ${ailment.duration_days}d` : ""}${ailment.treated ? " · treated" : " · untreated"}</p>`
          : `<p style="margin-top:0.6rem">Healthy.</p>`}
        <button id="btn-open-reasoning" class="btn btn-accent" style="margin-top:0.6rem">View reasoning for this agent →</button>
      </div>

      <div class="panel">
        <h3>Skills</h3>
        ${a.live_skills.length
          ? `<div class="table-wrap"><table><thead><tr><th>Task</th><th>Level</th></tr></thead><tbody>
              ${a.live_skills.map((s) => `<tr><td>${escapeHtml(s.task_type)}</td><td>${s.level}</td></tr>`).join("")}
            </tbody></table></div>`
          : `<p class="panel-hint">No live skill levels recorded.</p>`}
        ${a.trace_skill_records.length
          ? `<h3 style="margin-top:0.6rem">Trace-store proficiency history</h3>
             <div class="table-wrap"><table><thead><tr><th>Task</th><th>Proficiency</th><th>Reps</th><th>Tick</th></tr></thead><tbody>
               ${a.trace_skill_records.map((s) => `<tr><td>${escapeHtml(s.task_type)}</td><td>${s.proficiency_level.toFixed(2)}</td><td>${s.repetitions_observed}</td><td>${s.tick}</td></tr>`).join("")}
             </tbody></table></div>`
          : ""}
      </div>

      <div class="panel">
        <h3>Relationships</h3>
        ${a.relationships.length
          ? `<div class="list">${a.relationships
              .map((r) => `<div class="list-item">${escapeHtml(r.other_agent_name || r.other_agent_id || r.organization_id || "?")} — ${escapeHtml(r.relationship_type)}${r.sentiment !== null ? `, sentiment ${r.sentiment.toFixed(2)}` : ""}${r.trust !== null ? `, trust ${r.trust.toFixed(2)}` : ""} (${r.interaction_count}×)</div>`)
              .join("")}</div>`
          : `<p class="panel-hint">No relationships recorded in the trace store yet.</p>`}
      </div>

      <div class="panel panel-wide">
        <h3>Recent utterances</h3>
        ${a.recent_utterances.length
          ? `<div class="list">${a.recent_utterances
              .map((u) => `<div class="list-item">tick ${u.tick}${u.location ? " · " + escapeHtml(u.location) : ""}${u.listener ? " → " + escapeHtml(u.listener) : ""}: ${escapeHtml(u.content)}</div>`)
              .join("")}</div>`
          : `<p class="panel-hint">No utterances recorded for this agent yet.</p>`}
      </div>
    </div>`;
}

// Delegate the in-body "view reasoning" button since it's re-rendered
// every load(); wire it once per render via event delegation on the
// stable container instead of re-querying after every innerHTML swap.
document.addEventListener("click", (evt) => {
  if (evt.target && evt.target.id === "btn-open-reasoning") goToReasoningTab();
});
