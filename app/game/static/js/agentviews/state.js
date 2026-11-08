let selected = null;
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
