// Voting tab: live/browsable elections - candidates, votes as cast,
// running tally, result, and transfer of power. DESIGN.md section 8's
// "Voting viewer" bullet.

import { fetchElectionDetail, fetchElections } from "./api.js";
import { agentLabel, el, escapeHtml, fmtTick, setEmpty, setError, setLoading } from "./dom.js";

let electionsCache = [];
let openElectionId = null;

export async function loadVoting() {
  const container = el("voting-elections");
  setLoading(container);
  try {
    electionsCache = await fetchElections();
    if (!electionsCache.length) {
      setEmpty(container, "No election has been scheduled yet.");
      return;
    }
    if (openElectionId === null) openElectionId = electionsCache[0].election_id;
    await renderElections();
  } catch (err) {
    setError(container, err);
  }
}

const PHASE_LABEL = {
  scheduled: "Scheduled", candidacy_open: "Candidacy open", campaigning: "Campaigning",
  voting_open: "Voting open", tallied: "Tallied",
};

function phaseBadgeClass(phase) {
  if (phase === "tallied") return "badge-ok";
  if (phase === "voting_open") return "badge-warn";
  return "";
}

async function renderElections() {
  const container = el("voting-elections");
  const rows = electionsCache
    .map((e) => {
      const active = e.election_id === openElectionId;
      const resultLine = e.result
        ? `PM: ${agentLabel(e.result.prime_minister)}${e.regime_changed ? ' <span class="badge badge-warn">regime change</span>' : ""}`
        : `${e.candidate_count} candidate(s), ${e.ballots_cast} ballot(s) so far`;
      return `
      <div class="card ${active ? "expanded" : ""}" data-election="${escapeHtml(e.election_id)}">
        <div class="card-head">
          <span class="card-title">Election ${escapeHtml(e.election_id.slice(0, 14))}</span>
          <span class="badge ${phaseBadgeClass(e.phase)}">${PHASE_LABEL[e.phase] || e.phase}</span>
        </div>
        <div class="card-body">
          scheduled ${fmtTick(e.scheduled_tick)} · ${e.seats_at_scheduling} seat(s) · ${resultLine}
        </div>
        ${active ? `<div class="card-detail" id="election-detail-${escapeHtml(e.election_id)}"><div class="list-loading">Loading detail…</div></div>` : ""}
      </div>`;
    })
    .join("");
  container.innerHTML = `<div class="list">${rows}</div>`;
  container.querySelectorAll(".card").forEach((cardEl) => {
    cardEl.addEventListener("click", (evt) => {
      // Ignore clicks that landed inside the already-open detail panel.
      if (evt.target.closest(".card-detail")) return;
      openElectionId = cardEl.dataset.election;
      renderElections();
    });
  });
  const detail = document.getElementById(`election-detail-${openElectionId}`);
  if (detail) renderElectionDetail(openElectionId, detail);
}

async function renderElectionDetail(electionId, container) {
  try {
    const d = await fetchElectionDetail(electionId);
    const maxVotes = Math.max(1, ...Object.values(d.running_tally));
    const tallyRows = d.candidates
      .slice()
      .sort((a, b) => b.votes_so_far - a.votes_so_far)
      .map(
        (c) => `<div class="tally-row">
          <span class="tally-name">${agentLabel(c.candidate)}</span>
          <div class="tally-bar-track"><div class="tally-bar-fill" style="width:${(100 * c.votes_so_far) / maxVotes}%"></div></div>
          <span class="tally-count">${c.votes_so_far}</span>
        </div>`
      )
      .join("");
    const ballotRows = d.ballots.length
      ? d.ballots.map((b) => `<div class="card-body">${agentLabel(b.voter)} → ${agentLabel(b.candidate)}</div>`).join("")
      : `<div class="card-meta">No ballots cast yet.</div>`;
    container.innerHTML = `
      <h3>Running tally</h3>
      ${tallyRows}
      ${d.result
        ? `<h3>Result</h3><p>Prime Minister: ${agentLabel(d.result.prime_minister)}</p>
           <p>Ministers: ${d.result.ministers.length ? d.result.ministers.map(agentLabel).join(", ") : "none"}</p>
           <p class="card-meta">Tallied at ${fmtTick(d.result.tallied_tick)}${d.regime_changed !== null ? (d.regime_changed ? " · a regime change" : " · incumbent held power") : ""}</p>`
        : ""}
      <h3>Votes as cast (${d.ballots.length})</h3>
      <div style="max-height:220px;overflow-y:auto">${ballotRows}</div>`;
  } catch (err) {
    setError(container, err);
  }
}
