(function () {
  'use strict';

  var POLL_MS = 30000;
  var body = null, headBtn = null;
  var expanded = false;

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
