(function () {
  'use strict';

  var TALK_MS = 5000;
  var state = { talk: null, authored: null };

  function nameOf(agentId) {
    if (!agentId) return null;
    try {
      var n = state.authored && state.authored.names;
      if (n && n[agentId]) return n[agentId];
    } catch (e) {}
    try {
      if (typeof AGENTS !== 'undefined' && AGENTS) {
        for (var i = 0; i < AGENTS.length; i++) {
          if (AGENTS[i].id === agentId) return AGENTS[i].n || agentId;
        }
      }
    } catch (e) {  }
    return agentId;
  }

  function istTime(secs) {
    if (!secs) return '';
    var d = new Date(secs * 1000);
    var hh = String(d.getUTCHours()).padStart(2, '0');
    var mm = String(d.getUTCMinutes()).padStart(2, '0');
    var ss = String(d.getUTCSeconds()).padStart(2, '0');
    return hh + ':' + mm + ':' + ss + ' UTC';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function unavailable(d, what) {
    var why = (d && d.why) || 'no world bound';
    return '<div class="book-empty">' + esc(what) + ' is not available.<br>' +
      '<span class="dim">' + esc(why) + '</span></div>';
  }

  function lineHtml(u) {
    var speaker = u.speaker_name || nameOf(u.speaker_agent_id);
    var listener = u.listener_agent_id ? nameOf(u.listener_agent_id) : null;
    var selfTalk = u.listener_agent_id && u.listener_agent_id === u.speaker_agent_id;
    var to = (listener && !selfTalk)
      ? '<span class="talk-to">to ' + esc(listener) + '</span>'
      : '<span class="talk-open">aloud</span>';
    return '<div class="talk-line">' +
      '<div class="talk-meta">' +
        '<span class="talk-who">' + esc(speaker) + '</span>' +
        to +
        (u.location_id ? '<span class="talk-at">' + esc(u.location_id) + '</span>' : '') +
        '<span class="talk-tick">t' + esc(u.tick) + '</span>' +
        (u.said_at ? '<span class="talk-clock">' + esc(istTime(u.said_at)) + ' IST</span>' : '') +
      '</div>' +
      '<div class="talk-said">' + esc(u.content) + '</div>' +
    '</div>';
  }

  var THREAD_GAP_TICKS = 6;

  function threads(rows) {
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      var u = rows[i];
      var who = [u.speaker_agent_id, u.listener_agent_id].filter(Boolean).sort().join('|');
      var last = out[out.length - 1];
      var sameRoom = last && last.place === u.location_id;
      var overlaps = last && (last.who === who ||
        last.people.indexOf(u.speaker_agent_id) > -1 ||
        (u.listener_agent_id && last.people.indexOf(u.listener_agent_id) > -1));
      var close = last && (u.tick - last.lastTick) <= THREAD_GAP_TICKS;
      var sameThread = last && u.conversation_id && last.cid === u.conversation_id;

      if (sameThread || (sameRoom && overlaps && close)) {
        last.lines.push(u);
        last.lastTick = u.tick;
        if (last.people.indexOf(u.speaker_agent_id) < 0) last.people.push(u.speaker_agent_id);
        if (u.listener_agent_id && last.people.indexOf(u.listener_agent_id) < 0) {
          last.people.push(u.listener_agent_id);
        }
      } else {
        out.push({
          cid: u.conversation_id,
          place: u.location_id, who: who, people: [u.speaker_agent_id].concat(
            u.listener_agent_id && u.listener_agent_id !== u.speaker_agent_id
              ? [u.listener_agent_id] : []),
          lines: [u], firstTick: u.tick, lastTick: u.tick
        });
      }
    }
    return out;
  }

  function threadHtml(t) {
    var names = t.people.map(nameOf);
    var head = names.length > 1
      ? names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1]
      : names[0];
    var reply = t.lines.length > 1
      ? '<span class="th-count">' + t.lines.length + ' turns</span>'
      : '<span class="th-count one">no reply yet</span>';
    return '<div class="talk-thread">' +
      '<div class="th-head">' +
        '<span class="th-who">' + esc(head) + '</span>' +
        (t.place ? '<span class="th-at">' + esc(t.place) + '</span>' : '') +
        reply +
        '<span class="th-tick">t' + t.firstTick + (t.lastTick !== t.firstTick ? '–' + t.lastTick : '') + '</span>' +
        (t.lines.length && t.lines[0].said_at
          ? '<span class="th-clock">' + esc(istTime(t.lines[0].said_at)) + ' IST</span>' : '') +
      '</div>' +
      t.lines.map(function (u, i) {
        var prev = i > 0 ? t.lines[i - 1] : null;
        var isReply = !!(prev && prev.listener_agent_id
          && prev.listener_agent_id === u.speaker_agent_id);
        var toId = u.listener_agent_id;
        var to = (toId && toId !== u.speaker_agent_id)
          ? '<span class="th-to">→ ' + esc(nameOf(toId)) + '</span>' : '';
        return '<div class="th-line' + (isReply ? ' reply' : '') + '">' +
          (u.said_at ? '<span class="th-at-time">' + esc(istTime(u.said_at)) + '</span>' : '') +
          '<span class="th-speaker">' + esc(nameOf(u.speaker_agent_id)) + '</span>' +
          to +
          (isReply ? '<span class="th-badge">replies</span>' : '') +
          '<span class="th-said">' + esc(u.content) + '</span>' +
        '</div>';
      }).join('') +
    '</div>';
  }

  function talkHtml(d) {
    if (!d || !d.available) return unavailable(d, 'Talk');
    var rows = (d.utterances || []).slice();
    if (!rows.length) {
      return '<div class="book-empty">Nobody has spoken yet.<br>' +
        '<span class="dim">Not an error: residents talk when they have a reason to, ' +
        'and a town that has just woken up has not found one.</span></div>';
    }
    var t = threads(rows);
    t.reverse();
    return '<div class="talk-feed">' + t.map(threadHtml).join('') + '</div>';
  }

  function authoredHtml(d) {
    if (!d || !d.available) return unavailable(d, 'Authored');
    var skills = d.skills || [], tools = d.tools || [];
    if (!skills.length && !tools.length) {
      return '<div class="book-empty">Nobody has authored anything yet.<br>' +
        '<span class="dim">Agents are prompted to write a procedure down only after ' +
        'repeating one job several times, so an early run shows nothing here and ' +
        'that is the honest state, not a broken panel.</span></div>';
    }

    var by = {};
    function slot(id) {
      if (!by[id]) by[id] = { id: id, skills: [], tools: [] };
      return by[id];
    }
    tools.forEach(function (t) { slot(t.creator_agent_id).tools.push(t); });
    skills.forEach(function (s) { slot(s.author_agent_id).skills.push(s); });

    var authors = Object.keys(by).map(function (k) { return by[k]; });
    authors.sort(function (a, b) {
      return (b.skills.length + b.tools.length) - (a.skills.length + a.tools.length);
    });

    var prof = d.proficiency || {}, reps = d.repetitions || {};

    return '<div class="authored-book">' +
      '<div class="ab-summary">' +
        '<span><b>' + authors.length + '</b> author' + (authors.length === 1 ? '' : 's') + '</span>' +
        '<span><b>' + skills.length + '</b> skill' + (skills.length === 1 ? '' : 's') + ' written</span>' +
        '<span><b>' + tools.length + '</b> tool' + (tools.length === 1 ? '' : 's') + ' invented</span>' +
      '</div>' +
      authors.map(function (a) {
        var held = prof[a.id] || {}, r = reps[a.id] || {};
        return '<div class="ab-author">' +
          '<div class="ab-name">' + esc(nameOf(a.id)) +
            '<span class="ab-id">' + esc(a.id) + '</span></div>' +
          a.tools.map(function (t) {
            return '<div class="ab-item tool">' +
              '<span class="ab-verb">toolify</span>' +
              '<span class="ab-what">' + esc(t.tool_name) + '</span>' +
              '<span class="ab-note">' + (t.steps || []).length + ' steps' +
                (t.version > 1 ? ' · v' + t.version : '') + '</span></div>';
          }).join('') +
          a.skills.map(function (sk) {
            var p = held[sk.name] || 0, n = r[sk.name] || 0;
            return '<div class="ab-item skill">' +
              '<span class="ab-verb">skillify</span>' +
              '<span class="ab-what">' + esc(sk.name) + '</span>' +
              '<span class="ab-note">' + sk.steps + ' steps · prof <b>' + p + '</b>' +
                ' · ' + n + ' completion' + (n === 1 ? '' : 's') +
                (sk.governmental ? ' · <i>standing order</i>' : '') + '</span></div>';
          }).join('') +
        '</div>';
      }).join('') +
    '</div>';
  }

  function render() {
    var body = document.getElementById('talkBody');
    if (body) body.innerHTML = talkHtml(state.talk);
    var ab = document.getElementById('authoredBody');
    if (ab) ab.innerHTML = authoredHtml(state.authored);
    stampResidentFile();
  }

  function stampResidentFile() {
    var panel = document.getElementById('profilePanel');
    if (!panel || panel.hidden) return;
    var a = state.authored;
    if (!a || !a.available) return;

    var id = panel.dataset.agentId || (window.SELECTED_AGENT_ID || '');
    if (!id) return;

    var mine = (a.skills || []).filter(function (s) { return s.author_agent_id === id; });
    var tools = (a.tools || []).filter(function (t) { return t.creator_agent_id === id; });
    var old = panel.querySelector('.authored-strip');
    if (old) old.remove();
    if (!mine.length && !tools.length) return;

    var html = '<div class="authored-strip"><div class="lbl">Authored &amp; learned</div>';
    if (tools.length) {
      html += '<div class="auth-row"><span class="auth-k">Tools invented</span><span class="auth-v">' +
        tools.map(function (t) {
          return '<span class="auth-chip tool">' + esc(t.tool_name) +
            (t.version > 1 ? ' <span class="dim">v' + t.version + '</span>' : '') + '</span>';
        }).join('') + '</span></div>';
    }
    if (mine.length) {
      html += '<div class="auth-row"><span class="auth-k">Workflows written</span><span class="auth-v">' +
        mine.map(function (s) {
          return '<span class="auth-chip skill">' + esc(s.name) +
            ' <span class="dim">' + s.steps + ' steps</span></span>';
        }).join('') + '</span></div>';
    }
    html += '</div>';

    var body = panel.querySelector('.inspector-body') || panel;
    body.insertAdjacentHTML('beforeend', html);
  }

  async function poll() {
    try {
      var r = await Promise.all([
        fetch('/api/town/talk', { cache: 'no-store' }).then(function (x) { return x.json(); }),
        fetch('/api/town/authored', { cache: 'no-store' }).then(function (x) { return x.json(); })
      ]);
      state.talk = r[0];
      state.authored = r[1];
    } catch (e) {
      state.talk = { available: false, why: 'the viewer could not reach its own API' };
    }
    render();
  }

  function start() {
    poll();
    setInterval(poll, TALK_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.KasukabeTalk = { poll: poll, state: state };
})();
