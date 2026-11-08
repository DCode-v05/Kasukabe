import { on } from "./bus.js";
import { fetchAgentDetail } from "./api.js";

const panel = document.getElementById("inspector-body");

function usd(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderLoading(agentId) {
  panel.innerHTML = `<p class="inspector-status">Loading ${escapeHtml(agentId)}...</p>`;
}

function renderError(agentId, message) {
  panel.innerHTML = `<p class="inspector-status inspector-error">Could not load ${escapeHtml(
    agentId
  )}: ${escapeHtml(message)}</p>`;
}

function renderNotFound(agentId) {
  panel.innerHTML = `<p class="inspector-status">No detail available yet for ${escapeHtml(
    agentId
  )}.</p>`;
}

function utteranceRow(u) {
  const to = u.listener_name ? ` &rarr; ${escapeHtml(u.listener_name)}` : "";
  return `<li><span class="tick">t${u.tick}</span> <strong>${escapeHtml(
    u.speaker_name
  )}</strong>${to}: ${escapeHtml(u.content)}</li>`;
}

function relationshipRow(r) {
  const target = r.other_agent_name ?? r.organization_name ?? "unknown";
  const sentiment = r.sentiment !== null && r.sentiment !== undefined
    ? ` &middot; sentiment ${r.sentiment.toFixed(2)}`
    : "";
  const trust = r.trust !== null && r.trust !== undefined ? ` &middot; trust ${r.trust.toFixed(2)}` : "";
  return `<li><strong>${escapeHtml(target)}</strong> - ${escapeHtml(
    r.relationship_type
  )}${sentiment}${trust}</li>`;
}

function render(detail) {
  const utterances = detail.recent_utterances.length
    ? `<ul class="inspector-list">${detail.recent_utterances.map(utteranceRow).join("")}</ul>`
    : `<p class="inspector-empty">Nothing said recently.</p>`;

  const relationships = detail.relationships.length
    ? `<ul class="inspector-list">${detail.relationships.map(relationshipRow).join("")}</ul>`
    : `<p class="inspector-empty">No known relationships yet.</p>`;

  const statusBadges = [
    !detail.is_alive ? '<span class="badge badge-dead">deceased</span>' : "",
    detail.is_dormant ? '<span class="badge badge-dormant">dormant</span>' : "",
  ]
    .filter(Boolean)
    .join(" ");

  panel.innerHTML = `
    <div class="inspector-header">
      <img class="inspector-portrait" src="assets/characters/profile/${encodeURIComponent(
        detail.sprite_key
      )}.png" alt="${escapeHtml(detail.display_name)}" />
      <div>
        <h2>${escapeHtml(detail.display_name)} ${statusBadges}</h2>
        <p class="inspector-subtitle">${escapeHtml(detail.occupation ?? "no occupation on file")}${
    detail.age_years ? ` &middot; ${Math.floor(detail.age_years)} yrs` : ""
  }</p>
      </div>
    </div>

    <p class="inspector-bio">${escapeHtml(detail.bio)}</p>

    <div class="inspector-stats">
      <div><span class="label">USD wallet</span><span class="value">${usd(
        detail.wallets.usd_wallet_cents
      )}</span></div>
      <div><span class="label">Sim balance</span><span class="value">${detail.wallets.sim_balance.toLocaleString()}</span></div>
      <div><span class="label">Location</span><span class="value">${escapeHtml(
        detail.position.location_id ?? "unknown"
      )}</span></div>
    </div>

    <h3>Current action</h3>
    <p>${escapeHtml(detail.current_action ?? "idle")}</p>

    <h3>Recent utterances</h3>
    ${utterances}

    <h3>Relationships</h3>
    ${relationships}
  `;
}

export function initInspector() {
  panel.innerHTML = `<p class="inspector-status">Click an agent to inspect them.</p>`;

  on("agent-selected", async (event) => {
    const { agentId } = event.detail;
    renderLoading(agentId);
    try {
      const detail = await fetchAgentDetail(agentId);
      if (detail === null) {
        renderNotFound(agentId);
        return;
      }
      render(detail);
    } catch (err) {
      renderError(agentId, err.message);
    }
  });
}
