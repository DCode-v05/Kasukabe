// Small, dependency-free rendering helpers shared by every tab module in
// js/views/. No framework, no template engine (DESIGN.md section 8's
// viewers are "vanilla JS, dark theme, no build system") - just tagged
// string building with consistent HTML-escaping so every render function
// that interpolates a name, a headline, or free-text content (an
// utterance, a rationale, a description) is safe against an agent-chosen
// string that happens to contain "<" or "&".

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

/** agent_id + optional resolved name -> "Name (a0123)" / "a0123" if the
 * name never resolved (e.g. an agent born after genesis, or a bad id). */
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

/** Renders a unified diff's text with +/- lines colored, from
 * governance_api.py's CharterDiff.unified_text_diff. */
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

/** Debounces a filter-input handler so a search box doesn't refetch on
 * every keystroke. */
export function debounce(fn, waitMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), waitMs);
  };
}
