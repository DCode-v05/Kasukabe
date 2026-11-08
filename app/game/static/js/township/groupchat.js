/* Kasukabe — the group chat panel.
 *
 * Talk is a tail of every remark the town made. This is the other thing a
 * reader wants: the conversations themselves, each with the people who are
 * IN it — round the table, addressed by name, listening — not only the ones
 * who have produced text.
 *
 * That distinction is the whole reason /world/conversations exists. Until
 * the world kept a roster, a conversation was inferred after the fact from
 * "who spoke in this room recently", so a resident standing there being
 * talked about was in no conversation at all, and two pairs talking in one
 * market were a single thread with each answer under the other's question.
 *
 * Reads /api/town/conversations on the same beat as talk.js. Renders into
 * #groupChatBody, which shell.js lifts into the left drawer as its own tab.
 */
(function () {
  'use strict';

  var POLL_MS = 5000;
  var state = { data: null };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* UI-24: UTC, not 'Asia/Kolkata'. See talk.js's copy of this function
   * for why - one transcript must read the same for every reader. */
  function istTime(secs) {
    if (!secs) return '';
    try {
      var d = new Date(secs * 1000);
      return String(d.getUTCHours()).padStart(2, '0') + ':'
           + String(d.getUTCMinutes()).padStart(2, '0') + ':'
           + String(d.getUTCSeconds()).padStart(2, '0') + ' UTC';
    } catch (e) { return ''; }
  }

  function shortPlace(place) {
    if (!place) return 'somewhere';
    return String(place).replace(/^home:/, '').replace(/^business:/, '').replace(/_/g, ' ');
  }

  /* Every participant, named. Never "and 3 others": the count of people in
   * a conversation is the fact the panel exists to show, and a group of
   * five collapsing into "Rosalind and 4 others" hides exactly the thing
   * that makes it a group chat. */
  function roster(people) {
    if (!people || !people.length) return '';
    return '<div class="gc-roster">' + people.map(function (p) {
      return '<span class="gc-who">' + esc(p.name || p.agent_id) + '</span>';
    }).join('') + '</div>';
  }

  function lineHtml(line) {
    var to = line.listener_name
      ? ' <span class="gc-to">to ' + esc(line.listener_name) + '</span>' : '';
    return '<div class="gc-line">' +
      '<div class="gc-meta"><b>' + esc(line.speaker_name || line.speaker_agent_id) + '</b>' + to +
      '<span class="gc-when">t' + esc(line.tick) + ' · ' + esc(istTime(line.said_at)) + '</span></div>' +
      '<div class="gc-said">' + esc(line.content) + '</div>' +
      '</div>';
  }

  function chatHtml(c) {
    var people = c.participants || [];
    var isGroup = people.length > 2;
    var head =
      '<div class="gc-head">' +
        '<span class="gc-badge' + (isGroup ? ' group' : '') + '">' +
          (isGroup ? people.length + ' people' : people.length + ' people') + '</span>' +
        '<span class="gc-place">' + esc(shortPlace(c.place)) + '</span>' +
        '<span class="gc-state' + (c.open ? ' live' : '') + '">' +
          (c.open ? 'open' : 'closed') + '</span>' +
        '<span class="gc-turns">' + esc(c.turns) + ' turn' + (c.turns === 1 ? '' : 's') + '</span>' +
      '</div>';
    var topic = c.topic
      ? '<div class="gc-topic">' + esc(c.topic) + '</div>'
      : '';
    var lines = (c.lines || []).map(lineHtml).join('');
    if (!lines) {
      /* A convened conversation with nobody speaking yet is a real state,
       * and saying so beats an empty card that looks like a bug. */
      lines = '<div class="gc-quiet">Called, and nobody has spoken yet.</div>';
    }
    return '<div class="gc-card' + (isGroup ? ' is-group' : '') + '">' +
      head + topic + roster(people) + '<div class="gc-lines">' + lines + '</div></div>';
  }

  function render() {
    var body = document.getElementById('groupChatBody');
    if (!body) return;
    var d = state.data;

    if (!d || d.available === false) {
      body.innerHTML = '<div class="book-empty">' +
        esc((d && d.why) || 'the viewer could not reach its own API') + '</div>';
      return;
    }
    var chats = d.conversations || [];
    if (!chats.length) {
      body.innerHTML = '<div class="book-empty">Nobody has started a conversation yet.</div>';
      return;
    }
    var groups = chats.filter(function (c) { return (c.participants || []).length > 2; });
    var summary = '<div class="gc-summary">' +
      '<b>' + chats.length + '</b> conversation' + (chats.length === 1 ? '' : 's') +
      ' · <b>' + groups.length + '</b> with three or more people' +
      ' · <b>' + (d.open_count || 0) + '</b> still open</div>';
    body.innerHTML = summary + chats.map(chatHtml).join('');
  }

  async function poll() {
    try {
      var r = await fetch('/api/town/conversations', { cache: 'no-store' });
      state.data = await r.json();
    } catch (e) {
      state.data = { available: false, why: 'the viewer could not reach its own API' };
    }
    render();
  }

  function start() { poll(); setInterval(poll, POLL_MS); }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }

  window.KasukabeGroupChat = { poll: poll, state: state, render: render };
})();
