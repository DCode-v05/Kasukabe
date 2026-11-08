// Thin fetch wrappers for game/server.py's browser-facing REST surface.
// Same-origin (the FastAPI app serves both these routes and this file),
// so plain relative paths - no base URL configuration needed client side.

export async function fetchSnapshot() {
  const response = await fetch("/api/snapshot");
  if (!response.ok) {
    throw new Error(`GET /api/snapshot failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchAgentDetail(agentId) {
  const response = await fetch(`/api/agents/${encodeURIComponent(agentId)}`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`GET /api/agents/${agentId} failed: ${response.status}`);
  }
  return response.json();
}
