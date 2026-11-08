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

export function findAgents(filters) {
  return apiFetch("/agent-viewer/agents", filters);
}

export function fetchAgentActivity(agentId) {
  return apiFetch(`/agent-viewer/agents/${encodeURIComponent(agentId)}`);
}

export function fetchReasoningList(agentId, limit = 100) {
  return apiFetch(`/agent-viewer/agents/${encodeURIComponent(agentId)}/reasoning`, { limit });
}

export function fetchReasoningDetail(agentId, callId) {
  return apiFetch(`/agent-viewer/agents/${encodeURIComponent(agentId)}/reasoning/${encodeURIComponent(callId)}`);
}
