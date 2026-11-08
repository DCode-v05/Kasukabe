// Agent Finder tab: search/sort/filter all 250 by name, age, sex,
// occupation, business, household, place, wealth, health, org, ablation
// arm, alive/dormant/dead. DESIGN.md section 8's "Agent finder" bullet.

import { findAgents } from "./api.js";
import { debounce, el, escapeHtml, fmtCents, setError, setLoading, statusBadge } from "./dom.js";
import { selectAgent } from "./state.js";

// `sortKey: null` columns (Org) are not offered as a sort key - sorting a
// per-agent LIST of organization memberships has no single well-defined
// order, and world/agent_viewer_api.py's FINDER_SORT_FIELDS excludes it
// (400s if asked for) for the same reason - see that module's own
// finder_sort_key() docstring.
const COLUMNS = [
  { sortKey: "name", label: "Name" },
  { sortKey: "age_years", label: "Age" },
  { sortKey: "sex", label: "Sex" },
  { sortKey: "occupation", label: "Occupation" },
  { sortKey: "household_id", label: "Household" },
  { sortKey: "place", label: "Place" },
  { sortKey: "usd_wallet_cents", label: "Wallet" },
  { sortKey: "sim_balance", label: "Sim" },
  { sortKey: "health_status", label: "Health" },
  { sortKey: "status", label: "Status" },
  { sortKey: null, label: "Org" },
  { sortKey: "ablation_arm", label: "Arm" },
];

let sortBy = "name";
let sortDesc = false;
let goToActivityTab = () => {};

export function initFinder(onGoToActivity) {
  goToActivityTab = onGoToActivity;
  wireFilters();
}

export async function loadFinder() {
  await refresh();
}

function wireFilters() {
  const form = el("finder-filters");
  if (form.dataset.wired) return;
  form.dataset.wired = "1";
  form.querySelectorAll("input").forEach((input) => input.addEventListener("input", debounce(refresh, 300)));
  form.querySelectorAll("select").forEach((select) => select.addEventListener("change", refresh));
}

async function refresh() {
  const container = el("finder-results");
  setLoading(container);
  try {
    const page = await findAgents({
      q: el("finder-q").value || undefined,
      sex: el("finder-sex").value || undefined,
      status: el("finder-status").value || undefined,
      occupation: el("finder-occupation").value || undefined,
      household_id: el("finder-household").value || undefined,
      place: el("finder-place").value || undefined,
      organization: el("finder-org").value || undefined,
      ablation_arm: el("finder-arm").value || undefined,
      sort_by: sortBy,
      sort_desc: sortDesc,
      limit: 250,
    });
    el("finder-count").textContent = `${page.total} agent(s)`;
    renderTable(page.agents);
  } catch (err) {
    setError(container, err);
  }
}

function headHtml() {
  return COLUMNS.map((c) => {
    if (!c.sortKey) return `<th>${escapeHtml(c.label)}</th>`;
    const sorted = c.sortKey === sortBy;
    return `<th data-sort="${c.sortKey}" class="${sorted ? "sorted" : ""}" data-dir="${sortDesc ? "↓" : "↑"}">${escapeHtml(c.label)}</th>`;
  }).join("");
}

function renderTable(agents) {
  const container = el("finder-results");
  if (!agents.length) {
    container.innerHTML = `<div class="list-empty">No agent matches this filter.</div>`;
    return;
  }
  const rows = agents
    .map(
      (a) => `
      <tr data-agent-id="${escapeHtml(a.agent_id)}" data-agent-name="${escapeHtml(a.name)}">
        <td>${escapeHtml(a.name)}${a.is_minor ? ' <span class="badge">minor</span>' : ""}</td>
        <td>${a.age_years.toFixed(1)}</td>
        <td>${escapeHtml(a.sex)}</td>
        <td>${escapeHtml(a.occupation)}</td>
        <td class="mono">${escapeHtml(a.household_id || "—")}</td>
        <td>${escapeHtml(a.place || "—")}</td>
        <td>${fmtCents(a.usd_wallet_cents)}</td>
        <td>${a.sim_balance.toLocaleString()}</td>
        <td>${escapeHtml(a.health_status)}</td>
        <td>${statusBadge(a.status)}</td>
        <td>${a.organizations.length ? a.organizations.map((o) => `<span class="chip">${escapeHtml(o)}</span>`).join("") : "—"}</td>
        <td>${escapeHtml(a.ablation_arm)}</td>
      </tr>`
    )
    .join("");
  container.innerHTML = `<div class="table-wrap"><table>
    <thead><tr>${headHtml()}</tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
  container.querySelectorAll("thead th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (sortBy === key) sortDesc = !sortDesc;
      else {
        sortBy = key;
        sortDesc = false;
      }
      refresh();
    });
  });
  container.querySelectorAll("tbody tr").forEach((row) => {
    row.addEventListener("click", () => {
      selectAgent(row.dataset.agentId, row.dataset.agentName);
      goToActivityTab();
    });
  });
}
