// Thin fetch wrappers for world.governance_api / world.social_api's HTTP
// surface (world/governance_api.py, world/social_api.py).
//
// Unlike game/static/js/api.js (the live town view, same-origin against
// game/server.py), this page's data lives on a DIFFERENT process/origin:
// DESIGN.md section 9 runs world/ and game/ as separate deployables (dev
// ports 8100 and 8400, app/CONVENTIONS.md), and infra/Caddyfile's
// production routing only exposes game/'s port on the public domain, not
// world's - see world/governance_api.py's own module docstring
// "Cross-origin" note for the CORS half of this. So the "world API base
// URL" is a small piece of operator-set configuration (mirroring
// game/console/static/console.js's operator-token box), persisted in
// localStorage, not a hardcoded relative path.

const STORAGE_KEY = "kasukabe_world_base_url";

export function getWorldBaseUrl() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored !== null) return stored;
  // Zero-config convenience for local dev: world/main.py's documented
  // default dev port is 8100 (app/CONVENTIONS.md), this page's own
  // default is 8400 (game/config.py) - if we're on localhost, guess
  // world lives on the same host at 8100 rather than showing an empty
  // page until an operator types a URL in.
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    return `${location.protocol}//${location.hostname}:8100`;
  }
  return "";
}

export function setWorldBaseUrl(url) {
  localStorage.setItem(STORAGE_KEY, url.trim());
}

async function apiFetch(path, params) {
  const base = getWorldBaseUrl();
  const url = new URL(path, base ? base.replace(/\/$/, "") + "/" : location.origin + "/");
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
    }
  }
  let response;
  try {
    response = await fetch(url.toString());
  } catch (err) {
    throw new Error(`could not reach world API at ${base || "(same origin)"}: ${err.message}`);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      // response body wasn't JSON - fall back to statusText, set above.
    }
    throw new Error(`${response.status} ${path}: ${detail}`);
  }
  return response.json();
}

/** GET /health on world/main.py - used only to light the connection dot. */
export function checkWorldHealth() {
  return apiFetch("/health");
}

// ── governance ────────────────────────────────────────────────────────

export function fetchCharter() {
  return apiFetch("/governance/charter");
}

export function fetchCharterHistory() {
  return apiFetch("/governance/charter/history");
}

export function fetchActivePolicies() {
  return apiFetch("/governance/active-policies");
}

export function fetchGovernment() {
  return apiFetch("/governance/government");
}

export function fetchAmendments() {
  return apiFetch("/governance/amendments");
}

export function fetchComplaints() {
  return apiFetch("/governance/complaints");
}

// ── voting ───────────────────────────────────────────────────────────

export function fetchElections() {
  return apiFetch("/voting/elections");
}

export function fetchElectionDetail(electionId) {
  return apiFetch(`/voting/elections/${encodeURIComponent(electionId)}`);
}

// ── scenarios ────────────────────────────────────────────────────────

export function fetchScenarios(filters) {
  return apiFetch("/scenarios", filters);
}

export function fetchScenarioTrace(eventId) {
  return apiFetch(`/scenarios/${encodeURIComponent(eventId)}/trace`);
}

// ── conversations ────────────────────────────────────────────────────

export function fetchConversations(filters) {
  return apiFetch("/conversations", filters);
}

export function fetchConversationDetail(clusterId) {
  return apiFetch(`/conversations/${encodeURIComponent(clusterId)}`);
}
