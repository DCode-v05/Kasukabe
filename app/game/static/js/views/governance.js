import { fetchActivePolicies, fetchAmendments, fetchCharter, fetchComplaints, fetchGovernment } from "./api.js";
import { agentLabel, el, escapeHtml, fmtBps, fmtSim, fmtTick, renderDiffText, setError, setLoading, statusBadge } from "./dom.js";

let amendmentsCache = [];

export async function loadGovernance() {
  await Promise.all([loadSummary(), loadCharter(), loadActivePolicies(), loadAmendments(), loadComplaints()]);
}

async function loadSummary() {
  const container = el("gov-summary");
  setLoading(container);
  try {
    const [charter, government, policies] = await Promise.all([fetchCharter(), fetchGovernment(), fetchActivePolicies()]);
    container.innerHTML = `
      <div class="grid-3">
        <div class="stat-card">
          <div class="stat-label">Charter version</div>
          <div class="stat-value">v${charter.version}</div>
          <div class="stat-sub">${charter.total_seats} seats · 3/5-style threshold needs ${charter.amendment_votes_required}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Prime Minister</div>
          <div class="stat-value" style="font-size:1rem">${agentLabel(government.prime_minister) || "vacant"}</div>
          <div class="stat-sub">${government.ministers.length} minister(s) sitting</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Tax rate</div>
          <div class="stat-value">${fmtBps(policies.policy.tax_rate_bps)}</div>
          <div class="stat-sub">credit-store margin ${fmtBps(policies.policy.credit_store_margin_bps)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Police force</div>
          <div class="stat-value">${policies.police_force_size}</div>
          <div class="stat-sub">${policies.jobs_open} open · ${policies.jobs_filled} filled · ${policies.jobs_cut} cut jobs</div>
        </div>
      </div>`;
  } catch (err) {
    setError(container, err);
  }
}

async function loadCharter() {
  const container = el("gov-charter");
  setLoading(container);
  try {
    const c = await fetchCharter();
    const rows = [
      ["Election interval", `${c.election_interval_days} game-days`],
      ["Seats", `${c.pm_seats} PM + ${c.minister_seats} minister(s)`],
      ["Amendment threshold", `${c.amendment_threshold_numerator}/${c.amendment_threshold_denominator} (${c.amendment_votes_required} votes today)`],
      ["Suffrage", `age ${c.suffrage_min_age}+${c.suffrage_requires_residency ? ", residents only" : ""}`],
      ["Term limit", c.term_limit_terms === null ? "unlimited" : `${c.term_limit_terms} terms`],
      ["Candidacy / campaign / voting window", `${c.candidacy_window_days} / ${c.campaign_period_days} / ${c.voting_window_days} days`],
      ["Patent filing fee", fmtSim(c.patent_filing_fee_sim)],
      ["Permit fee", fmtSim(c.permit_fee_sim)],
      ["Builder license fee", fmtSim(c.builder_license_fee_sim)],
    ];
    container.innerHTML = `
      <div class="table-wrap"><table><tbody>
        ${rows.map(([k, v]) => `<tr><td class="card-meta">${escapeHtml(k)}</td><td>${escapeHtml(String(v))}</td></tr>`).join("")}
      </tbody></table></div>`;
  } catch (err) {
    setError(container, err);
  }
}

async function loadActivePolicies() {
  const container = el("gov-active-policies");
  setLoading(container);
  try {
    const p = await fetchActivePolicies();
    const jobsRows = p.jobs.length
      ? p.jobs.map((j) => `<tr>
          <td>${escapeHtml(j.title)}</td><td>${escapeHtml(j.job_type)}</td>
          <td>${fmtSim(j.salary_sim)}</td><td><span class="badge">${escapeHtml(j.status)}</span></td>
          <td>${j.assigned_agent ? agentLabel(j.assigned_agent) : "—"}</td>
        </tr>`).join("")
      : `<tr><td colspan="5" class="list-empty">No jobs created yet.</td></tr>`;
    const licenseRows = p.permits_and_licenses.length
      ? p.permits_and_licenses.map((l) => `<tr>
          <td>${escapeHtml(l.license_type)}</td><td>${agentLabel(l.holder)}</td>
          <td>${fmtSim(l.fee_paid_sim)}</td><td><span class="badge ${l.status === "active" ? "badge-ok" : "badge-danger"}">${escapeHtml(l.status)}</span></td>
        </tr>`).join("")
      : `<tr><td colspan="4" class="list-empty">No permits or licenses issued yet.</td></tr>`;
    container.innerHTML = `
      <div class="grid-2">
        <div>
          <h3>Pensions</h3>
          <p class="panel-hint">private ${p.policy.pension_private_pct}% · government ${p.policy.pension_government_pct}% · eligible at ${p.policy.pension_eligibility_age}</p>
          <h3>Jobs (${p.jobs.length})</h3>
          <div class="table-wrap"><table><thead><tr><th>Title</th><th>Type</th><th>Salary</th><th>Status</th><th>Assigned</th></tr></thead>
          <tbody>${jobsRows}</tbody></table></div>
        </div>
        <div>
          <h3>Permits &amp; licenses (${p.permits_and_licenses.length})</h3>
          <div class="table-wrap"><table><thead><tr><th>Type</th><th>Holder</th><th>Fee</th><th>Status</th></tr></thead>
          <tbody>${licenseRows}</tbody></table></div>
        </div>
      </div>`;
  } catch (err) {
    setError(container, err);
  }
}

function renderVotes(votes) {
  if (!votes.length) return `<span class="card-meta">no votes cast</span>`;
  return votes
    .map((v) => `<span class="vote-pill ${v.support ? "vote-yes" : "vote-no"}">${v.support ? "✓" : "✗"} ${agentLabel(v.voter)}</span>`)
    .join("");
}

function renderAmendmentCard(entry, expanded) {
  return `
    <div class="card ${expanded ? "expanded" : ""}" data-proposal="${escapeHtml(entry.proposal_id)}">
      <div class="card-head">
        <span class="card-title">${escapeHtml(entry.title)}</span>
        ${statusBadge(entry.status)}
      </div>
      <div class="card-body">
        field <code>${escapeHtml(entry.field_name)}</code> → <code>${escapeHtml(JSON.stringify(entry.new_value))}</code>
        · proposed by ${agentLabel(entry.proposed_by)} at ${fmtTick(entry.proposed_tick)}
      </div>
      ${expanded ? renderAmendmentDetail(entry) : ""}
    </div>`;
}

function renderAmendmentDetail(entry) {
  return `
    <div class="card-detail">
      <p>${escapeHtml(entry.rationale)}</p>
      <p class="card-meta">Enacting action: <code>${escapeHtml(entry.enacting_action)}</code></p>
      <h3>Votes (${entry.yes_votes} yes / ${entry.no_votes} no, needs ${entry.votes_required})</h3>
      <div>${renderVotes(entry.votes)}</div>
      ${entry.vetoed_tick !== null ? `<p class="card-meta">Vetoed at ${fmtTick(entry.vetoed_tick)}${entry.veto_note ? ": " + escapeHtml(entry.veto_note) : ""}</p>` : ""}
      ${entry.diff
        ? `<h3>Charter diff (v${entry.diff.before_version} → v${entry.diff.after_version})</h3>
           <div class="diff-summary">${entry.diff.changed_fields
             .map((c) => `<span class="diff-field"><code>${escapeHtml(c.field_name)}</code>: <span class="mono">${escapeHtml(JSON.stringify(c.before))}</span><span class="arrow">→</span><span class="to mono">${escapeHtml(JSON.stringify(c.after))}</span></span>`)
             .join("")}</div>
           <div class="diff-block">${renderDiffText(entry.diff.unified_text_diff)}</div>`
        : `<p class="card-meta">Not enacted yet - no charter diff to show.</p>`}
    </div>`;
}

async function loadAmendments() {
  const container = el("gov-amendments");
  setLoading(container);
  try {
    amendmentsCache = await fetchAmendments();
    renderAmendments();
  } catch (err) {
    setError(container, err);
  }
}

function renderAmendments() {
  const container = el("gov-amendments");
  if (!amendmentsCache.length) {
    container.innerHTML = `<div class="list-empty">No amendments have been proposed yet.</div>`;
    return;
  }
  container.innerHTML = `<div class="list">${amendmentsCache.map((e) => renderAmendmentCard(e, e.expanded)).join("")}</div>`;
  container.querySelectorAll(".card").forEach((cardEl) => {
    cardEl.addEventListener("click", () => {
      const id = cardEl.dataset.proposal;
      amendmentsCache = amendmentsCache.map((e) => (e.proposal_id === id ? { ...e, expanded: !e.expanded } : e));
      renderAmendments();
    });
  });
}

async function loadComplaints() {
  const container = el("gov-complaints");
  setLoading(container);
  try {
    const complaints = await fetchComplaints();
    if (!complaints.length) {
      container.innerHTML = `<div class="list-empty">The complaint ledger is empty.</div>`;
      return;
    }
    container.innerHTML = `<div class="list">${complaints
      .map(
        (c) => `<div class="card" style="cursor:default">
          <div class="card-head"><span class="card-title">#${c.ledger_index} — against ${escapeHtml(c.against_kind)} ${escapeHtml(c.against_id)}</span>
          <span class="card-meta">${fmtTick(c.filed_tick)}</span></div>
          <div class="card-body">${escapeHtml(c.description)} — filed by ${agentLabel(c.complainant)}</div>
        </div>`
      )
      .join("")}</div>`;
  } catch (err) {
    setError(container, err);
  }
}

