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

export function fmtTick(tick) {
  if (tick === null || tick === undefined) return "—";
  const day = Math.floor(tick / 144);
  const minuteOfDay = (tick % 144) * 10;
  const hh = String(Math.floor(minuteOfDay / 60)).padStart(2, "0");
  const mm = String(minuteOfDay % 60).padStart(2, "0");
  return `day ${day} · ${hh}:${mm} (tick ${tick})`;
}

export function fmtCents(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

export function fmtUsd(usd) {
  return `$${usd.toFixed(4)}`;
}

export function statusBadge(status) {
  const cls = { alive: "badge-alive", dormant: "badge-dormant", dead: "badge-dead" }[status] || "";
  return `<span class="badge ${cls}">${escapeHtml(status)}</span>`;
}

export function debounce(fn, waitMs) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), waitMs);
  };
}
