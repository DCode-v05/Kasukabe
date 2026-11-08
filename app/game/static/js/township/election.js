(function () {
  'use strict';

  var POLL_MS = 8000;
  var TICKS_PER_DAY = 144, TICK_MINUTES = 10;
  var PHASES = {
    scheduled: 'Scheduled',
    candidacy_open: 'Open for candidates',
    campaigning: 'Campaigning',
    voting_open: 'Voting',
    tallied: 'Counted',
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function clock(tick) {
    if (tick == null) return '—';
    var day = Math.floor(tick / TICKS_PER_DAY);
    var mins = (tick % TICKS_PER_DAY) * TICK_MINUTES;
    var hh = String(Math.floor(mins / 60)).padStart(2, '0');
    var mm = String(mins % 60).padStart(2, '0');
    return 'game-day ' + day + ', ' + hh + ':' + mm;
  }
  function who(ref) {
    if (!ref) return 'nobody';
    return esc(ref.name || ref.agent_id);
  }

  function nextDeadline(e) {
    if (e.phase === 'scheduled' || e.phase === 'candidacy_open') return ['candidacy closes', e.candidacy_closes_tick];
    if (e.phase === 'campaigning') return ['voting opens', e.voting_opens_tick];
    if (e.phase === 'voting_open') return ['voting closes', e.voting_closes_tick];
    return null;
  }

  function timeline(e, now) {
    var start = e.candidacy_opens_tick, end = e.voting_closes_tick;
    var span = Math.max(1, end - start);
    function pct(t) { return Math.max(0, Math.min(100, ((t - start) / span) * 100)); }
    var segs = [
      ['cand', start, e.candidacy_closes_tick, 'Candidacy'],
      ['camp', e.candidacy_closes_tick, e.voting_opens_tick, 'Campaign'],
      ['vote', e.voting_opens_tick, e.voting_closes_tick, 'Voting'],
    ];
    var html = '<div class="el-line">';
    segs.forEach(function (s) {
      html += '<span class="el-seg ' + s[0] + '" style="left:' + pct(s[1]) + '%;width:' +
        (pct(s[2]) - pct(s[1])) + '%" title="' + s[3] + ': ' + clock(s[1]) + ' to ' + clock(s[2]) + '"></span>';
    });
    if (now != null) html += '<span class="el-now" style="left:' + pct(now) + '%" title="now: ' + clock(now) + '"></span>';
    return html + '</div>';
  }

  function tallyOf(e) {
    if (e.result && e.result.vote_counts) return e.result.vote_counts;
    return e.running_tally || {};
  }

  function candidatesHtml(e) {
    var list = (e.candidates || []).slice();
    if (!list.length) return '<div class="book-empty">Nobody has declared.</div>';
    var tally = tallyOf(e);
    var max = 1;
    list.forEach(function (c) { max = Math.max(max, tally[c.candidate.agent_id] || 0); });
    list.sort(function (a, b) {
      return (tally[b.candidate.agent_id] || 0) - (tally[a.candidate.agent_id] || 0) ||
        String(a.candidate.name).localeCompare(String(b.candidate.name));
    });
    var winners = {};
    if (e.result) {
      if (e.result.prime_minister) winners[e.result.prime_minister.agent_id] = 'Prime Minister';
      (e.result.ministers || []).forEach(function (m) { if (m) winners[m.agent_id] = 'Minister'; });
    }
    var showVotes = e.phase === 'voting_open' || e.phase === 'tallied';
    return '<ol class="el-cands">' + list.map(function (c) {
      var id = c.candidate.agent_id, v = tally[id] || 0;
      return '<li><span class="el-name">' + who(c.candidate) +
        (winners[id] ? ' <b class="el-seat">' + winners[id] + '</b>' : '') + '</span>' +
        (showVotes ? '<span class="el-bar"><i style="width:' + (100 * v / max) + '%"></i></span>' +
          '<span class="el-votes">' + v + '</span>' : '<span class="el-declared">declared ' + clock(c.declared_tick) + '</span>') +
        '</li>';
    }).join('') + '</ol>';
  }

  function resultHtml(r) {
    return '<div class="el-result"><b>Elected, ' + clock(r.result.tallied_tick) + '.</b> Prime Minister: ' +
      who(r.result.prime_minister) + '. Ministers: ' +
      ((r.result.ministers || []).map(who).join(', ') || 'none') + '.' +
      (r.regime_changed ? ' <span class="el-change">A new government.</span>' : '') + '</div>';
  }

  function bookHtml(d) {
    var r = d.latest_result;
    var e = d.current;
    var parts = [];
    if (r && r.result && (!e || e.election_id !== r.election_id)) {
      parts.push('<div class="election"><div class="el-head"><span class="el-phase tallied">Counted</span>' +
        '<span class="el-next">' + clock(r.result.tallied_tick) + '</span></div>' + resultHtml(r) +
        '<h4>Final totals <span class="el-count">' + (r.ballots_cast || 0) + ' ballots</span></h4>' +
        candidatesHtml(r) + '</div>');
      if (e) parts.push('<h4 class="el-next-head">Next election</h4>');
    }
    if (e) parts.push(currentHtml(d, e));
    if (!parts.length) {
      return '<div class="book-empty"><b>No election yet</b>The world has not scheduled one.</div>';
    }
    return parts.join('');
  }

  function currentHtml(d, e) {
    var deadline = nextDeadline(e);
    var html = '<div class="election">';
    html += '<div class="el-head"><span class="el-phase ' + esc(e.phase) + '">' + esc(PHASES[e.phase] || e.phase) + '</span>' +
      (deadline ? '<span class="el-next">' + esc(deadline[0]) + ' ' + clock(deadline[1]) + '</span>' : '') + '</div>';
    html += timeline(e, d.tick);
    html += '<dl class="el-when">' +
      '<dt>Candidacy</dt><dd>' + clock(e.candidacy_opens_tick) + ' to ' + clock(e.candidacy_closes_tick) + '</dd>' +
      '<dt>Campaign</dt><dd>to ' + clock(e.voting_opens_tick) + '</dd>' +
      '<dt>Voting</dt><dd>' + clock(e.voting_opens_tick) + ' to ' + clock(e.voting_closes_tick) + '</dd>' +
      '<dt>Where</dt><dd>in person, at the Kasukabe Government Complex</dd>' +
      '<dt>Who</dt><dd>residents aged 18 and over may stand and vote</dd>' +
      '</dl>';
    html += '<h4>Candidates <span class="el-count">' + (e.candidate_count || 0) + ' standing · ' +
      (e.ballots_cast || 0) + ' ballot' + ((e.ballots_cast || 0) === 1 ? '' : 's') + ' cast</span></h4>';
    html += candidatesHtml(e);
    if (e.result) html += resultHtml(e);
    var shownResult = d.latest_result && d.latest_result.election_id;
    var past = (d.elections || []).filter(function (x) {
      return x.election_id !== e.election_id && x.election_id !== shownResult;
    });
    if (past.length) {
      html += '<h4>Earlier</h4><ul class="el-past">' + past.map(function (x) {
        return '<li>' + esc(PHASES[x.phase] || x.phase) + ' · ' + clock(x.scheduled_tick) +
          (x.result ? ' · PM ' + who(x.result.prime_minister) : '') + '</li>';
      }).join('') + '</ul>';
    }
    return html + '</div>';
  }

  function stripHtml(d) {
    var r = d.latest_result;
    var e = d.current;
    if (r && r.result && d.tick != null && d.tick - r.result.tallied_tick < TICKS_PER_DAY) {
      return '<span class="es-icon">🗳</span><span class="es-body"><b>Elected</b> Prime Minister ' +
        who(r.result.prime_minister) +
        ((r.result.ministers || []).length ? ' · ' + r.result.ministers.length + ' ministers' : '') +
        '<span class="es-next">counted ' + clock(r.result.tallied_tick) + ' · ' + (r.ballots_cast || 0) + ' ballots</span></span>';
    }
    if (!e) return null;
    var deadline = nextDeadline(e);
    var body;
    if (e.phase === 'tallied' && e.result) {
      body = '<b>Elected</b> Prime Minister ' + who(e.result.prime_minister) +
        ((e.result.ministers || []).length ? ' · ' + e.result.ministers.length + ' ministers' : '');
    } else if (e.phase === 'voting_open') {
      body = '<b>Voting</b> ' + (e.ballots_cast || 0) + ' ballots · ' + (e.candidate_count || 0) + ' standing';
    } else {
      body = '<b>' + esc(PHASES[e.phase] || e.phase) + '</b> ' + (e.candidate_count || 0) + ' standing';
    }
    return '<span class="es-icon">🗳</span><span class="es-body">' + body +
      (deadline ? '<span class="es-next">' + esc(deadline[0]) + ' ' + clock(deadline[1]) + '</span>' : '') +
      '</span>' + timeline(e, d.tick);
  }

  var last = '';
  var good = null;
  function render(d) {
    var key = JSON.stringify(d);
    if (key === last) return;
    last = key;
    var stale = d.available === false;
    if (!stale) good = d;
    var shown = stale ? good : d;
    var body = document.getElementById('electionBody');
    if (body) {
      if (!shown) {
        body.innerHTML = '<div class="book-empty"><b>Election not available</b>' + esc(d.detail || '') + '</div>';
      } else {
        body.innerHTML = (stale ? '<div class="el-stale">The world has stopped answering; this is the last it said.</div>' : '') +
          bookHtml(shown);
      }
    }
    var strip = document.getElementById('electionStrip');
    if (strip) {
      var html = shown ? stripHtml(shown) : null;
      strip.hidden = !html;
      if (html) strip.innerHTML = html + (stale ? '<span class="stale">world stopped · last reading</span>' : '');
    }
  }

  function poll() {
    fetch('/api/town/election', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () {  })
      .then(function () { setTimeout(poll, POLL_MS); });
  }

  window.KasukabeElection = { bookHtml: bookHtml, stripHtml: stripHtml, clock: clock };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', poll);
  else poll();
})();
