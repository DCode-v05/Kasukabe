const STORAGE_KEY = "kasukabe_world_base_url";

export function getWorldBaseUrl() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored !== null) return stored;
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
    }
    throw new Error(`${response.status} ${path}: ${detail}`);
  }
  return response.json();
}

export function checkWorldHealth() {
  return apiFetch("/health");
}

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

export function fetchElections() {
  return apiFetch("/voting/elections");
}

export function fetchElectionDetail(electionId) {
  return apiFetch(`/voting/elections/${encodeURIComponent(electionId)}`);
}

export function fetchScenarios(filters) {
  return apiFetch("/scenarios", filters);
}

export function fetchScenarioTrace(eventId) {
  return apiFetch(`/scenarios/${encodeURIComponent(eventId)}/trace`);
}

export function fetchConversations(filters) {
  return apiFetch("/conversations", filters);
}

export function fetchConversationDetail(clusterId) {
  return apiFetch(`/conversations/${encodeURIComponent(clusterId)}`);
}
