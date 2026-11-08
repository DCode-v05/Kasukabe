/* The Talk book — agent-to-agent speech, live.
 *
 * The one thing a running town produced that no surface showed. `say` emits
 * `agent.utterance.spoken`; only the trace store consumed it, so two residents
 * could hold a conversation and the viewer had no way to display it. A reader
 * watching a live run could see people walk to the same tile and nothing else.
 *
 * Also stamps the resident file with what that agent has AUTHORED — skills
 * written with `skillify`, tools invented with `toolify` — because "is this
 * town inventing anything" is answerable from the Workshop book, while "did
 * THIS person invent it" was not answerable anywhere.
 *
 * Polls /api/town/talk and /api/town/authored on the same 5s beat. Both
 * degrade with a reason rather than an empty list, for the reason the Workshop
 * already does: a silent town and a broken pipe must not look identical.
 */
(function () {
  'use strict';

  var TALK_MS = 5000;
  var state = { talk: null, authored: null };

  /* The world server cannot name a speaker: AgentLifeRecord has no name
   * field - names live in genesis, not in world state - so /world/utterances
   * honestly returns the agent id. The viewer already holds the roster, so
   * resolution happens here rather than by teaching the world a fact it does
   * not have. */
  function nameOf(agentId) {
    if (!agentId) return null;
    /* The RUNNING genesis wins. agents.js is a different draw of the town and
     * gives the same id a different name - a0064 was "Lior Jarrah" to the
     * world and "Hector Stone" to the cast. Preferring the world means the
     * Talk feed, the Authored book and the Workshop all say the same thing. */
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
    } catch (e) { /* roster not loaded yet */ }
    return agentId;
  }

  /* IST, 12-hour, with seconds - matching the run report, so a line in the
   * browser and the same line in docs/runs/ read identically.
   *
   * The panel showed only a tick number, which locates a remark inside the
   * simulation and nowhere inside the run: a reader could not line a
   * conversation up against anything that happened in the world outside it.
   */
  /* UI-24. UTC, not the operator's timezone.
   *
   * This rendered every timestamp in IST - a hard-coded +330 here and an
   * 'Asia/Kolkata' locale in groupchat.js. Correct for this operator and
   * wrong for anybody else reading the same transcript, including the
   * paper's readers: two people looking at one conversation saw two
   * different clocks and neither was told which.
   *
   * UTC is the one answer that is the same everywhere, and it is what the
   * trace store's own `created_at` is written in, so a reader can line a
   * remark up against the row that recorded it.
   */
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

  /* One utterance. Public speech and a directed remark read differently, so
   * they look different: "to X" only appears when there really is a listener,
   * rather than inventing an audience for a line said to the room. */
  function lineHtml(u) {
    var speaker = u.speaker_name || nameOf(u.speaker_agent_id);
    var listener = u.listener_agent_id ? nameOf(u.listener_agent_id) : null;
    /* An agent that addressed its own id is thinking out loud, not holding a
     * conversation. Shown as "aloud" rather than "to themselves", which would
     * read as a rendering bug rather than what it is - a model naming the
     * wrong listener. */
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

  /* Group speech into exchanges rather than listing lines.
   *
   * A flat feed cannot show a conversation: "who spoke to who, and what did
   * the other person say back" is the question, and consecutive lines by the
   * same two people at the same place ARE the answer — they just have to be
   * put next to each other.
   *
   * Grouped by `conversation_id` now that one means something.
   *
   * This used to group by (place, overlapping people, tick gap) because
   * `say` minted a fresh id per utterance, so every reply was otherwise a
   * thread of one. A conversation id is now the ROOM's conversation:
   * everyone speaking in one place inside the window shares it.
   *
   * The old overlap rule cannot show a group. Four residents talked on one
   * street inside two ticks - Liora asking Perrin why he had not answered,
   * Marta telling Tobias to sit down, Tobias apologising to Liora, Perrin
   * answering her - and because Marta and Tobias overlapped with neither of
   * the first two, it drew three conversations out of one.
   *
   * The heuristic stays as a fallback for rows with no id: older runs, and
   * anything replayed from a trace written before this changed. */
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
      /* Who said it, to whom, and whether it was an answer.
       *
       * Every line rendered identically, so an exchange read as two people
       * talking past each other - "who spoke to who and what did they say
       * back" was exactly what the panel could not show. A line whose
       * speaker is the previous line's listener IS the reply, and it is
       * marked and indented as one. */
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
    var t = threads(rows);        // built in spoken order so replies follow
    t.reverse();                  // newest exchange first
    return '<div class="talk-feed">' + t.map(threadHtml).join('') + '</div>';
  }

  /* The Authored book: WHO reached for skillify and toolify.
   *
   * The Workshop book answers "what does the town have". This answers "which
   * residents are authoring", which is the different and more interesting
   * question in a run where most agents never invent anything: the split
   * between authors and everyone else is the finding, not the totals. */
  function authoredHtml(d) {
    if (!d || !d.available) return unavailable(d, 'Authored');
    var skills = d.skills || [], tools = d.tools || [];
    if (!skills.length && !tools.length) {
      return '<div class="book-empty">Nobody has authored anything yet.<br>' +
        '<span class="dim">Agents are prompted to write a procedure down only after ' +
        'repeating one job several times, so an early run shows nothing here and ' +
        'that is the honest state, not a broken panel.</span></div>';
    }

    // By author, because the question is who - not what.
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

  /* What this resident has AUTHORED, stamped into the open resident file.
   *
   * Appended rather than woven into part-e.js's Skills tab so the two
   * cannot fight over the same node: that tab owns proficiency, this owns
   * provenance.
   *
   * UI-25: that sentence was not true. This strip rendered a Proficiency
   * row as well, off its own poll, beside part-e.js's proficiency bars off
   * theirs - two owners of one fact on two independent timers, so the same
   * resident's proficiency could read 3 in one place and 4 an inch below
   * it, and neither number was wrong when it was fetched.
   *
   * The split is now real. Provenance here: what this resident invented
   * and wrote. Proficiency there, in the Skills tab, where the bar and the
   * repetition count already live. */
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
