// Shared "which agent is selected" state for agents.html. One page, three
// tabs (Finder / Activity / Reasoning) where the latter two are both
// scoped to whatever agent Finder's table was clicked on - a plain
// module-level object with a tiny listener list is simpler and more
// honest than a state-management library for three consumers on one page.

let selected = null; // { agentId, name } | null
const listeners = [];

export function getSelectedAgent() {
  return selected;
}

export function selectAgent(agentId, name) {
  selected = { agentId, name: name || agentId };
  listeners.forEach((fn) => fn(selected));
}

export function onAgentSelected(fn) {
  listeners.push(fn);
}
