import { fetchConversationDetail, fetchConversations } from "./api.js";
import { debounce, el, escapeHtml, fmtTick, setEmpty, setError, setLoading } from "./dom.js";

export async function loadConversations() {
  wireFilters();
  await refreshConversations();
}

function wireFilters() {
  const kindSelect = el("conv-kind");
  if (kindSelect.dataset.wired) return;
  kindSelect.dataset.wired = "1";
  el("conv-place").addEventListener("input", debounce(refreshConversations, 350));
  el("conv-participant").addEventListener("input", debounce(refreshConversations, 350));
  kindSelect.addEventListener("change", refreshConversations);
  el("conv-refresh").addEventListener("click", refreshConversations);
}

async function refreshConversations() {
  const listContainer = el("conv-list");
  const detailContainer = el("conv-detail");
  setLoading(listContainer);
  detailContainer.innerHTML = `<div class="panel-hint">Select a conversation to read its transcript.</div>`;
  try {
    const summaries = await fetchConversations({
      place: el("conv-place").value || undefined,
      participant: el("conv-participant").value || undefined,
      kind: el("conv-kind").value || undefined,
      limit: 200,
    });
    if (!summaries.length) {
      setEmpty(
        listContainer,
        "No conversations recorded yet (the trace store's utterances table has no writer wired in this deployment - see this track's report)."
      );
      return;
    }
    render(summaries);
  } catch (err) {
    setError(listContainer, err);
  }
}

function render(summaries) {
  const container = el("conv-list");
  container.innerHTML = `<div class="list">${summaries.map(renderSummaryCard).join("")}</div>`;
  container.querySelectorAll(".card").forEach((cardEl) => {
    cardEl.addEventListener("click", () => openConversation(cardEl.dataset.clusterId));
  });
}

function renderSummaryCard(s) {
  return `
    <div class="card" data-cluster-id="${escapeHtml(s.conversation_cluster_id)}">
      <div class="card-head">
        <span class="card-title">${escapeHtml(s.participant_names.join(", "))}</span>
        <span class="badge badge-${s.kind}">${s.kind}</span>
      </div>
      <div class="card-body">
        ${escapeHtml(s.location || "unknown place")} · ${fmtTick(s.tick_start)} → tick ${s.tick_end} · ${s.utterance_count} utterance(s)
        <br/><em>${escapeHtml(s.preview)}${s.preview.length >= 140 ? "…" : ""}</em>
      </div>
    </div>`;
}

async function openConversation(clusterId) {
  const container = el("conv-detail");
  setLoading(container, "Loading transcript…");
  try {
    const detail = await fetchConversationDetail(clusterId);
    container.innerHTML = `
      <h3>${escapeHtml(detail.participant_names.join(", "))} <span class="badge badge-${detail.kind}">${detail.kind}</span></h3>
      <p class="panel-hint">${escapeHtml(detail.location || "unknown place")} · tick ${detail.tick_start} → ${detail.tick_end}</p>
      <div class="transcript">
        ${detail.utterances
          .map(
            (u) => `<div class="utterance">
              <div class="utterance-meta">${escapeHtml(u.speaker_name || u.speaker)}${u.listener ? " → " + escapeHtml(u.listener_name || u.listener) : " (to the room)"} · tick ${u.tick}${u.location ? " · " + escapeHtml(u.location) : ""}</div>
              ${escapeHtml(u.content)}
            </div>`
          )
          .join("")}
      </div>`;
  } catch (err) {
    setError(container, err);
  }
}
