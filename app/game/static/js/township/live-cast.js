(function () {
  'use strict';

  var state = { applied: false, live: 0, full: 0, ids: null };

  function log(msg) {
    try { console.info('[live-cast] ' + msg); } catch (e) {}
  }

  function prune(liveIds) {
    if (typeof AGENTS === 'undefined' || !AGENTS || !AGENTS.length) return false;
    state.full = state.full || AGENTS.length;
    state.ids = state.ids || {};
    for (var k = 0; k < liveIds.length; k++) state.ids[liveIds[k]] = true;

    var keep = {};
    for (var i = 0; i < liveIds.length; i++) keep[liveIds[i]] = true;

    var extra = AGENTS.filter(function (a) { return !keep[a.id]; });
    if (!extra.length) return false;

    for (var j = AGENTS.length - 1; j >= 0; j--) {
      if (!keep[AGENTS[j].id]) AGENTS.splice(j, 1);
    }
    state.applied = true;
    state.live = AGENTS.length;
    state.ids = keep;
    log('pruned ' + state.full + ' → ' + AGENTS.length + ' (the bound run)');
    return true;
  }

  function rerender() {
    try {
      if (typeof Village !== 'undefined' && Village) {
        if (typeof Village.seedAgents === 'function') Village.seedAgents(AGENTS);
        else if (typeof Village.setAgents === 'function') Village.setAgents(AGENTS);
      }
    } catch (e) { log('map reseed failed: ' + e.message); }

    ['renderScoreboard', 'renderCensus', 'renderRoster', 'refreshBooks',
     'refreshOpenSkillsTab'].forEach(function (fn) {
      try { if (typeof window[fn] === 'function') window[fn](); } catch (e) {}
    });

    try {
      var alive = AGENTS.filter(function (a) {
        return !(a.dead || a.deceased || a.alive === false);
      }).length;
      var hudAlive = document.getElementById('hudAlive');
      var hudDead = document.getElementById('hudDead');
      if (hudAlive) hudAlive.textContent = alive;
      if (hudDead) hudDead.textContent = AGENTS.length - alive;
    } catch (e) {}

    try {
      var cards = document.querySelectorAll('.char-card[data-id]');
      var removed = 0;
      for (var i = 0; i < cards.length; i++) {
        var id = cards[i].getAttribute('data-id');
        if (state.ids && !state.ids[id]) { cards[i].remove(); removed++; }
      }
      if (removed) log('removed ' + removed + ' profile cards outside the run');

      var shown = document.querySelectorAll('.char-card[data-id]').length;
      var count = document.getElementById('rosterCount');
      if (count && shown) count.textContent = shown + ' shown';
    } catch (e) { log('roster prune failed: ' + e.message); }

    try {
      var kicker = document.querySelector('.titles .kicker');
      if (kicker) {
        kicker.textContent = AGENTS.length + ' residents · ' +
          (state.applied ? 'live run' : 'live village');
      }
    } catch (e) {}

    try {
      var scope = document.getElementById('rosterScope');
      if (scope) {
        scope.textContent = state.applied
          ? ' — the ' + AGENTS.length + ' in this run'
          : ' — all ' + AGENTS.length + ' residents';
      }
    } catch (e) {}

    try {
      var panel = document.getElementById('profilePanel');
      var open = panel && !panel.hidden;
      var shownId = panel && panel.dataset ? panel.dataset.agentId : null;
      if (open && shownId && state.ids && !state.ids[shownId]) {
        var close = panel.querySelector('.ctl.tiny, .dock-close, [aria-label="Close"]');
        if (close) close.click(); else panel.hidden = true;
      }
    } catch (e) {}
  }

  function adoptWorldNames(names) {
    if (!names) return 0;
    var changed = 0;
    for (var i = 0; i < AGENTS.length; i++) {
      var real = names[AGENTS[i].id];
      if (real && AGENTS[i].n !== real) {
        var was = AGENTS[i].n;
        AGENTS[i].castName = was;
        AGENTS[i].n = real;

        var firstWas = was.split(' ')[0], firstNow = real.split(' ')[0];
        ['bio', 'tension', 'fear', 'ambition', 'values', 'limit', 'scarcity',
         'habit', 'trait'].forEach(function (field) {
          var v = AGENTS[i][field];
          if (typeof v !== 'string' || !v) return;
          v = v.split(was).join(real);
          if (firstWas && firstWas !== firstNow) v = v.split(firstWas).join(firstNow);
          AGENTS[i][field] = v;
        });
        changed++;
      }
    }
    if (changed) log('renamed ' + changed + ' residents to the world\'s names');
    return changed;
  }

  async function sync() {
    var d, authored;
    try {
      d = await fetch('/api/township/live', { cache: 'no-store' }).then(function (r) { return r.json(); });
    } catch (e) { return; }

    if (!d || !d.connected || !Array.isArray(d.agents) || !d.agents.length) return;

    var ids = d.agents.map(function (a) { return a.id; });
    var pruned = prune(ids);

    try {
      authored = await fetch('/api/town/authored', { cache: 'no-store' })
        .then(function (r) { return r.json(); });
    } catch (e) { authored = null; }
    var renamed = adoptWorldNames(authored && authored.names);

    state.applied = state.applied || AGENTS.length !== state.full;
    rerender();
  }

  function start() {
    sync();
    setInterval(sync, 10000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.KasukabeLiveCast = { state: state, sync: sync };
})();
