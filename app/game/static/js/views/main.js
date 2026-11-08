// Entry point for views.html: tab wiring, the world-API connection
// indicator, and lazy per-tab loading (a tab's data is fetched the first
// time it's opened, then on every subsequent open, so switching tabs
// stays cheap and data never goes stale across a long-lived tab).

import { checkWorldHealth, getWorldBaseUrl, setWorldBaseUrl } from "./api.js";
import { el } from "./dom.js";
import { loadGovernance } from "./governance.js";
import { loadVoting } from "./voting.js";
import { loadScenarios } from "./scenarios.js";
import { loadConversations } from "./conversations.js";

const TAB_LOADERS = {
  governance: loadGovernance,
  voting: loadVoting,
  scenarios: loadScenarios,
  conversations: loadConversations,
};

function activateTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  const loader = TAB_LOADERS[name];
  if (loader) loader();
}

function wireTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

async function refreshConnectionDot() {
  const dot = el("api-dot");
  try {
    await checkWorldHealth();
    dot.className = "dot dot-ok";
    dot.title = `connected to ${getWorldBaseUrl() || "(same origin)"}`;
  } catch (err) {
    dot.className = "dot dot-bad";
    dot.title = err.message;
  }
}

function wireApiBox() {
  const input = el("api-base-url");
  input.value = getWorldBaseUrl();
  input.placeholder = "http://localhost:8100";
  el("api-save").addEventListener("click", () => {
    setWorldBaseUrl(input.value);
    refreshConnectionDot();
    activateTab(currentTab());
  });
}

function currentTab() {
  const active = document.querySelector(".tab-btn.active");
  return active ? active.dataset.tab : "governance";
}

wireTabs();
wireApiBox();
refreshConnectionDot();
activateTab(currentTab());
// Re-check the connection every 20s so the dot reflects the world
// process actually restarting or dropping mid-session, without an
// operator having to reload the page to find out.
setInterval(refreshConnectionDot, 20_000);
