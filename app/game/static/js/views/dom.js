export function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function el(id) {
  return document.getElementById(id);
}

export function setLoading(container, message = "Loading…") {
  container.innerHTML = `<div class="list-loading">${escapeHtml(message)}</div>`;
}

export function setError(container, err) {
  container.innerHTML = `<div class="list-error">${escapeHtml(err.message || String(err))}</div>`;
}

export function setEmpty(container, message = "Nothing here yet.") {
  container.innerHTML = `<div class="list-empty">${escapeHtml(message)}</div>`;
}

export function agentLabel(ref) {
  if (!ref) return "—";
  if (ref.name) return `${escapeHtml(ref.name)} <span class="card-meta mono">(${escapeHtml(ref.agent_id)})</span>`;
  return `<span class="mono">${escapeHtml(ref.agent_id)}</span>`;
}

export function fmtTick(tick, gameDay) {
  if (tick === null || tick === undefined) return "tick —";
  const day = gameDay !== undefined && gameDay !== null ? gameDay : Math.floor(tick / 144);
  const minuteOfDay = (tick % 144) * 10;
  const hh = String(Math.floor(minuteOfDay / 60)).padStart(2, "0");
  const mm = String(minuteOfDay % 60).padStart(2, "0");
  return `day ${day} · ${hh}:${mm} (tick ${tick})`;
}

export function fmtBps(bps) {
  return `${(bps / 100).toFixed(2)}%`;
}

export function fmtCents(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

export function fmtSim(amount) {
  return `${amount.toLocaleString()} sim`;
}

const CATEGORY_LABELS = {
  crime: "Crime", trial: "Trial", birth: "Birth", death: "Death",
  marriage: "Marriage", disaster: "Disaster", miracle: "Miracle",
  cult: "Cult / Org", business: "Business", amendment: "Amendment",
  regime: "Regime", election: "Election",
};

export function categoryBadge(category) {
  if (!category) return `<span class="badge">other</span>`;
  const label = CATEGORY_LABELS[category] || category;
  return `<span class="badge badge-${escapeHtml(category)}">${escapeHtml(label)}</span>`;
}

export function statusBadge(status) {
  const cls = { enacted: "badge-ok", vetoed: "badge-danger", rejected: "badge-danger",
    passed_awaiting_president: "badge-warn", proposed: "" }[status] || "";
  return `<span class="badge ${cls}">${escapeHtml(status.replaceAll("_", " "))}</span>`;
}

export function renderDiffText(text) {
  if (!text) return "";
  return text
    .split("\n")
    .map((line) => {
      const escaped = escapeHtml(line);
      if (line.startsWith("+++") || line.startsWith("---")) return `<span class="diff-hunk">${escaped}</span>`;
      if (line.startsWith("@@")) return `<span class="diff-hunk">${escaped}</span>`;
      if (line.startsWith("+")) return `<span class="diff-add">${escaped}</span>`;
      if (line.startsWith("-")) return `<span class="diff-del">${escaped}</span>`;
      return `<span class="diff-ctx">${escaped}</span>`;
    })
    .join("");
}

export function debounce(fn, waitMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), waitMs);
  };
}
