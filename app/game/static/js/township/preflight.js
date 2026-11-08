/* The start-of-run checklist, on screen.
 *
 * world/preflight.py runs fifteen checks and the runner REFUSES to start
 * when a critical one fails. That refusal used to be a traceback in a
 * terminal; a run that never began looked, from the browser, exactly like a
 * run that had begun and gone quiet. This panel is the same checks, answered
 * by the live world, so the reason is on the screen where the run is.
 *
 * The panel is deliberately loud on failure and quiet on success: fifteen
 * green lines nobody reads is worse than one red line nobody can miss, so a
 * ready run collapses to a single summary row and the failures stay open.
 *
 * Classic script, no IIFE-less globals: everything is inside the closure
 * below. township-v2.js's top-level bindings are NOT on window (it is a
 * classic script with no wrapper), so nothing here may reach for them.
 */
(function () {
  'use strict';

  var POLL_MS = 30000;          // the checks are cheap but not free
  var body = null, headBtn = null;
  var expanded = false;         // sticky once the reader opens it

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function row(c) {
    var mark = c.ok ? '✓' : (c.critical ? '✗' : '!');
    var cls = c.ok ? 'pf-ok' : (c.critical ? 'pf-fail' : 'pf-warn');
    return '<li class="pf-row ' + cls + '">' +
      '<span class="pf-mark">' + mark + '</span>' +
      '<span class="pf-name">' + esc(c.name) + '</span>' +
      (c.detail ? '<span class="pf-detail">' + esc(c.detail) + '</span>' : '') +
      // The fix line is the whole point of a failing check and is shown only
      // when it is failing - on a passing row it is noise.
      (!c.ok && c.fix ? '<span class="pf-fix">' + esc(c.fix) + '</span>' : '') +
      '</li>';
  }

  function render(d) {
    if (!body) return;
    if (!d || d.available === false) {
      body.innerHTML = '<div class="book-empty">No world bound, so there is ' +
        'nothing to check yet.' + (d && d.detail
          ? '<br><span class="hint">' + esc(d.detail) + '</span>' : '') + '</div>';
      setPanelState('idle');
      return;
    }
    var checks = d.checks || [];
    var failed = checks.filter(function (c) { return !c.ok && c.critical; });
    var warned = checks.filter(function (c) { return !c.ok && !c.critical; });
    var ready = !!d.ready;

    setPanelState(ready ? (warned.length ? 'warn' : 'ready') : 'blocked');

    var head = '<div class="pf-summary' + (ready ? '' : ' blocked') + '">' +
      '<span class="pf-count">' + (d.passed != null ? d.passed : checks.length - failed.length) +
        ' of ' + (d.total != null ? d.total : checks.length) + ' checks pass</span>' +
      '<span class="pf-verdict">' + (ready
        ? (warned.length ? 'ready, with ' + warned.length + ' warning' +
           (warned.length === 1 ? '' : 's') : 'ready to run')
        : failed.length + ' blocking failure' + (failed.length === 1 ? '' : 's') +
          ' — the run will not start') + '</span>' +
      '<button class="pf-toggle" type="button">' +
        (expanded ? 'hide all' : 'show all') + '</button></div>';

    // Failures and warnings are always visible. Passing rows only when asked.
    var shown = expanded ? checks
      : checks.filter(function (c) { return !c.ok; });
    var list = shown.length
      ? '<ul class="pf-list">' + shown.map(row).join('') + '</ul>'
      : '';

    body.innerHTML = head + list;
    var t = body.querySelector('.pf-toggle');
    if (t) t.addEventListener('click', function () {
      expanded = !expanded;
      render(d);
    });
  }

  /* The panel's own border and the map's banner both turn on this. A blocked
   * preflight is the one state the page must not let you scroll past. */
  function setPanelState(state) {
    var panel = document.getElementById('preflightPanel');
    if (panel) panel.dataset.state = state;
    document.body.dataset.preflight = state;
    document.dispatchEvent(new CustomEvent('kasukabe:preflight', {
      detail: { state: state }
    }));
  }

  function poll() {
    fetch('/api/town/preflight', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (e) {
        if (!body) return;
        body.innerHTML = '<div class="book-empty">Could not reach the ' +
          'checklist.<br><span class="hint">' + esc(e && e.message) + '</span></div>';
        setPanelState('idle');
      });
  }

  function start() {
    body = document.getElementById('preflightBody');
    headBtn = document.getElementById('pfRecheck');
    if (!body) return;
    if (headBtn) headBtn.addEventListener('click', function () {
      body.innerHTML = '<div class="book-empty">Checking…</div>';
      poll();
    });
    poll();
    setInterval(poll, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }
}());
