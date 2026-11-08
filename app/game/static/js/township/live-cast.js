/* Show the town that is actually running, not the town that could be.
 *
 * `agents.js` bakes all 314 residents in at build time — bios, families,
 * balances — and the map, the roster, the scoreboard and every other book
 * read from it. A 30-agent run therefore drew 314 people, listed 314 in
 * People, and averaged money across 284 residents who were not in the
 * simulation at all. Every figure in the instrument was about a cast that
 * did not exist.
 *
 * The world already knows who is real: /api/township/live returns exactly the
 * agents a bound run reports. This intersects the two — the world says WHO,
 * the static cast says WHAT THEY ARE LIKE — and prunes `AGENTS` in place so
 * everything downstream is right without each panel having to learn about it.
 *
 * In place, because `AGENTS` is a top-level `const`: it can be spliced but not
 * reassigned, and every module holds the same array reference.
 *
 * No live world bound → nothing is pruned. The full 314 is the honest answer
 * when there is no run to be narrower than.
 */
(function () {
  'use strict';

  var state = { applied: false, live: 0, full: 0, ids: null };

  function log(msg) {
    try { console.info('[live-cast] ' + msg); } catch (e) {}
  }

  function prune(liveIds) {
    if (typeof AGENTS === 'undefined' || !AGENTS || !AGENTS.length) return false;
    // The cast may already be the run's - the server cuts it now - so `full`
    // is only meaningful when this module did the cutting.
    state.full = state.full || AGENTS.length;
    state.ids = state.ids || {};
    for (var k = 0; k < liveIds.length; k++) state.ids[liveIds[k]] = true;

    var keep = {};
    for (var i = 0; i < liveIds.length; i++) keep[liveIds[i]] = true;

    // Nothing to do if the cast is already exactly the live set.
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

  /* Redraw whatever is already on screen. The map keeps its own sprite list,
   * so splicing the array behind it is not enough — it has to be told. */
  function rerender() {
    try {
      if (typeof Village !== 'undefined' && Village) {
        if (typeof Village.seedAgents === 'function') Village.seedAgents(AGENTS);
        else if (typeof Village.setAgents === 'function') Village.setAgents(AGENTS);
      }
    } catch (e) { log('map reseed failed: ' + e.message); }

    // Panels rebuild from AGENTS on their own timers, so they self-correct
    // within a tick. Nudging the ones that ARE reachable just makes it
    // immediate. Only some of township-v2.js's top-level functions are on
    // `window` - it is a classic script with no IIFE, so its `const` bindings
    // are module-scope and not properties of anything. Guarded rather than
    // assumed, and never relied on: correctness comes from the poll.
    ['renderScoreboard', 'renderCensus', 'renderRoster', 'refreshBooks',
     'refreshOpenSkillsTab'].forEach(function (fn) {
      try { if (typeof window[fn] === 'function') window[fn](); } catch (e) {}
    });

    /* Alive / Deceased, set directly rather than waited for.
     *
     * These are written by the health panel's render, which runs on a slower
     * timer than the scoreboard's - so after a prune the top bar showed
     * "Town wealth 150,324" (30 agents, correct) beside "Alive 314" (the old
     * cast) for as long as that timer took. Two numbers about different towns,
     * side by side, and the wrong one is the one a reader trusts most.
     *
     * Computed here from the same array everything else uses, so they cannot
     * disagree even for a tick. */
    try {
      var alive = AGENTS.filter(function (a) {
        return !(a.dead || a.deceased || a.alive === false);
      }).length;
      var hudAlive = document.getElementById('hudAlive');
      var hudDead = document.getElementById('hudDead');
      if (hudAlive) hudAlive.textContent = alive;
      if (hudDead) hudDead.textContent = AGENTS.length - alive;
    } catch (e) {}

    /* The Characters grid: 314 profile cards, built once at page load and
     * never rebuilt.
     *
     * This is the one panel the prune could not reach by itself. Its render
     * lives in township-v2.js as a module-scope function - the file is a
     * classic script with no IIFE, so its bindings are not properties of
     * `window` and cannot be called from here. Splicing AGENTS left the
     * array right and the DOM wrong: 30 residents in every figure, 314 cards
     * underneath them.
     *
     * The cards carry `data-id`, so they can be pruned directly. Removing
     * DOM this module did not create is not a thing to do lightly, but the
     * alternative is a panel that contradicts every other panel on screen. */
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

    /* The masthead, for the same reason. It read "314 residents · live
     * village" as fixed text, directly above a live badge counting 30. */
    try {
      var kicker = document.querySelector('.titles .kicker');
      if (kicker) {
        kicker.textContent = AGENTS.length + ' residents · ' +
          (state.applied ? 'live run' : 'live village');
      }
    } catch (e) {}

    /* Say plainly which town the books describe. The roster header read
     * "Characters — all 250 residents" as fixed text: wrong before this
     * change (the cast was 314) and wrong after it (the run is 30). A header
     * that states a number nobody checked is worse than no header. */
    try {
      var scope = document.getElementById('rosterScope');
      if (scope) {
        scope.textContent = state.applied
          ? ' — the ' + AGENTS.length + ' in this run'
          : ' — all ' + AGENTS.length + ' residents';
      }
    } catch (e) {}

    // The one that cannot wait: a resident file left open on somebody who is
    // not in this run would keep showing a person the world has never heard
    // of. Close it rather than let it lie.
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

  /* Give every kept resident the name the RUNNING world knows them by.
   *
   * agents.js is a different draw of the town: it assigns a0039 the name
   * "Felix Ward" while this run calls the same agent "Ezra Lund". The
   * resident file showed both at once - "Ezra Lund" in the header, "Felix
   * Ward is a 41-year-old resident..." in the body - and there was no way for
   * a reader to tell which was real.
   *
   * Renaming the cast in place fixes every consumer at once: the file header,
   * the biography, the roster, the map label, Talk and Authored all read the
   * same array. The persona stays - it is this resident's character - only
   * the name is corrected to the one the world is actually using.
   */
  function adoptWorldNames(names) {
    if (!names) return 0;
    var changed = 0;
    for (var i = 0; i < AGENTS.length; i++) {
      var real = names[AGENTS[i].id];
      if (real && AGENTS[i].n !== real) {
        var was = AGENTS[i].n;
        // Keep what the cast called them, so a reader who noticed the old
        // name is not left wondering where it went.
        AGENTS[i].castName = was;
        AGENTS[i].n = real;

        /* The prose carries the name too. `bio` is a pre-rendered sentence -
         * "Felix Ward is a 41-year-old resident of Kasukabe who crafts goods"
         * - so renaming the field alone left the header saying Ezra Lund and
         * the paragraph underneath still saying Felix Ward. Every text field
         * gets the same substitution, first name included, because the
         * persona lines use it on its own. */
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

    // Names come from the world's genesis, not from the build-time cast.
    try {
      authored = await fetch('/api/town/authored', { cache: 'no-store' })
        .then(function (r) { return r.json(); });
    } catch (e) { authored = null; }
    var renamed = adoptWorldNames(authored && authored.names);

    // Always, not only on change. The server now cuts agents.js before it
    // reaches the browser, so there is usually nothing left to prune - and a
    // rerender gated on "did I change something" then never ran, leaving the
    // masthead still reading "314 residents · live village" over a cast of 30.
    state.applied = state.applied || AGENTS.length !== state.full;
    rerender();
  }

  function start() {
    sync();
    // Re-checked, not once: a run can be started after the page is open, and
    // an agent that dies mid-run leaves the live set. Cheap — it only acts
    // when the sets actually differ.
    setInterval(sync, 10000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.KasukabeLiveCast = { state: state, sync: sync };
})();
