(function () {
  'use strict';

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  const state = {
    skills: Object.create(null),
    following: Object.create(null),
    connected: false,
    tick: null,
    workshop: null,
    council: null,
    openSkill: Object.create(null),
  };

  document.addEventListener('kasukabe:live', function (e) {
    const d = e.detail || {};
    state.skills = d.skills || Object.create(null);
    state.following = d.following || Object.create(null);
    state.connected = !!d.connected;
    state.tick = d.tick;
    if (typeof window.refreshOpenSkillsTab === 'function') window.refreshOpenSkillsTab();
    paintFollowSlots();
    renderWorkshop();
  });

  function paintFollowSlots() {
    const slots = document.querySelectorAll('[data-follow]');
    for (let i = 0; i < slots.length; i++) {
      const id = slots[i].getAttribute('data-follow');
      slots[i].innerHTML = followingLabel(id);
    }
  }

  function profBar(level) {
    const filled = Math.max(0, Math.min(8, Math.round((Number(level) || 0) / 12.5)));
    let out = '<span class="prof-bar" aria-hidden="true">';
    for (let i = 0; i < 8; i++) out += '<i class="' + (i < filled ? 'on' : '') + '"></i>';
    return out + '</span><span class="prof-num">' + (Number(level) || 0) + '</span>';
  }

  function provenance(sk, agentId) {
    if (sk.authored && sk.author_agent_id === agentId) return 'invented by them';
    if (sk.taught_by_name) return 'taught by ' + esc(sk.taught_by_name);
    if (sk.author_name) return 'written by ' + esc(sk.author_name);
    return 'a trade the town always knew';
  }

  const STEP_MARK = { check: '◇', tool: '▪', skill: '▸' };

  function stepsHtml(sk) {
    if (!sk.steps || !sk.steps.length) {
      return '<div class="skill-nodoc">No written procedure — a built-in trade, ' +
             'measured by practice rather than by steps.</div>';
    }
    return '<ol class="skill-steps">' + sk.steps.map(function (st, i) {
      const here = sk.following && sk.next_step === i;
      const done = sk.following && sk.next_step != null && i < sk.next_step;
      const label = st.kind === 'check'
        ? '<span class="st-check">' + esc(st.note || 'check') + '</span>'
        : '<span class="st-kind">' + esc(st.kind) + '</span> <b>' + esc(st.ref || '') + '</b>' +
          (st.note ? ' <span class="st-note">' + esc(st.note) + '</span>' : '');
      return '<li class="' + (here ? 'here' : done ? 'done' : '') + '">' +
               '<span class="st-mark">' + (STEP_MARK[st.kind] || '·') + '</span>' +
               '<span class="st-n">' + (i + 1) + '</span>' + label +
             '</li>';
    }).join('') + '</ol>';
  }

  function skillCard(sk, agentId) {
    const key = agentId + '::' + sk.name;
    const open = state.openSkill[key] != null ? state.openSkill[key] : !!sk.following;
    const stepLine = sk.following && sk.steps.length
      ? '<span class="skill-step">step ' + (Math.min(sk.next_step + 1, sk.steps.length)) +
        '/' + sk.steps.length + '</span>'
      : '';
    return '<div class="skill-card' + (sk.following ? ' walking' : '') + '" data-skill="' + esc(key) + '">' +
      '<button class="skill-head" type="button" data-toggle="' + esc(key) + '" aria-expanded="' + open + '">' +
        '<span class="skill-name">' + esc(sk.name) +
          (sk.governmental ? '<span class="tag gov">governmental</span>' : '') +
          (sk.namespace && sk.namespace !== 'public'
            ? '<span class="tag secret">' + esc(sk.namespace) + ' only</span>' : '') +
        '</span>' +
        '<span class="skill-meter">' + profBar(sk.proficiency) + '</span>' +
      '</button>' +
      '<div class="skill-sub">' + provenance(sk, agentId) +
        ' · <b>' + (sk.completions || 0) + '</b> completion' + (sk.completions === 1 ? '' : 's') +
        (sk.version > 1 ? ' · revised to v' + sk.version : '') +
        ' ' + stepLine +
      '</div>' +
      '<div class="skill-body"' + (open ? '' : ' hidden') + '>' + stepsHtml(sk) + '</div>' +
    '</div>';
  }

  function skillsTabHtml(agentId) {
    if (!state.connected) {
      return '<div class="inspector-empty">No world server bound, so nothing can be ' +
             'said about what this resident knows. Skills are read live — this ' +
             'panel never guesses.</div>';
    }
    const held = state.skills[agentId] || [];
    if (!held.length) {
      return '<div class="inspector-empty">Holds no skill yet.<br>' +
             '<span class="dim">Not an error: most residents start this way, and ' +
             'watching that change is the point.</span></div>';
    }
    const authored = held.filter(function (s) { return s.authored; }).length;
    return '<div class="skills-tab">' +
      '<div class="skills-summary">' +
        '<b>' + held.length + '</b> skill' + (held.length === 1 ? '' : 's') + ' held · ' +
        '<b>' + authored + '</b> written down' +
        (state.following[agentId]
          ? ' · <span class="walking-now">walking ' + esc(state.following[agentId].skill) + '</span>'
          : ' · <span class="dim">not following any workflow</span>') +
      '</div>' +
      held.map(function (s) { return skillCard(s, agentId); }).join('') +
    '</div>';
  }

  function followingLabel(agentId) {
    const f = state.following[agentId];
    if (!state.connected) return '';
    if (!f) return '<span class="follow none">(no skill)</span>';
    return '<span class="follow on">' + esc(f.skill) +
           ' <span class="follow-step">step ' + f.step + '/' + f.total + '</span></span>';
  }

  function isFollowing(agentId) { return !!state.following[agentId]; }

  document.addEventListener('click', function (e) {
    const btn = e.target.closest && e.target.closest('[data-toggle]');
    if (!btn) return;
    const key = btn.getAttribute('data-toggle');
    const card = btn.closest('.skill-card');
    const body = card && card.querySelector('.skill-body');
    if (!body) return;
    const open = body.hidden;
    body.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    state.openSkill[key] = open;
  });

  function fetchJson(url) {
    return fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { available: false, detail: 'HTTP ' + r.status }; })
      .catch(function (err) { return { available: false, detail: String(err && err.message || err) }; });
  }

  function unavailable(d, what) {
    return '<div class="book-empty"><b>' + what + ' unavailable</b>' +
           '<span>' + esc((d && d.detail) || 'no world server bound') + '</span></div>';
  }

  function workshopHtml(d) {
    if (!d || !d.available) return unavailable(d, 'Workshop');
    const tools = d.tools || [], skills = d.skills || [];
    if (!tools.length && !skills.length) {
      return '<div class="book-empty"><b>Nothing filed yet</b>' +
        '<span>The town is connected and has invented nothing so far. ' +
        'This is the honest early state of a run, not a missing panel.</span></div>';
    }
    let fastest = null;
    skills.forEach(function (s) {
      if (s.holders > 1 && (!fastest || s.spread_per_day > fastest.spread_per_day)) fastest = s;
    });

    return '<div class="workshop">' +
      '<div class="ws-section"><h3>Tools invented <span class="ws-count">' + tools.length + '</span></h3>' +
        (tools.length ? tools.map(function (t) {
          return '<div class="ws-row' + (t.adopted ? '' : ' orphan') + '">' +
            '<div class="ws-main"><b>' + esc(t.name) + '</b>' +
              '<span class="ws-by">' + esc(t.creator_name || t.creator_agent_id) +
              ' · tick ' + t.created_tick + '</span></div>' +
            '<div class="ws-stat">used <b>' + t.uses + '×</b> by ' + t.distinct_users +
              ' agent' + (t.distinct_users === 1 ? '' : 's') +
              (t.adopted ? '' : '<span class="orphan-mark" title="Filed, but nobody other than ' +
                'the inventor ever called it">⚠</span>') +
              (t.pending_patent ? '<span class="tag pending">patent pending</span>' : '') +
            '</div>' +
            (t.version > 1 ? '<div class="ws-rev">revised to v' + t.version + '</div>' : '') +
          '</div>';
        }).join('') : '<div class="ws-none">none yet</div>') +
      '</div>' +
      '<div class="ws-section"><h3>Skills authored <span class="ws-count">' + skills.length + '</span></h3>' +
        (skills.length ? skills.map(function (s) {
          return '<div class="ws-row">' +
            '<div class="ws-main"><b>' + esc(s.name) + '</b>' +
              (s.governmental ? '<span class="tag gov">governmental</span>' : '') +
              '<span class="ws-by">' + esc(s.author_name || s.author_agent_id) +
              ' · tick ' + s.created_tick + '</span></div>' +
            '<div class="ws-stat">held by <b>' + s.holders + '</b>' +
              ' · avg prof ' + s.average_proficiency +
              ' · ' + s.completions + ' completion' + (s.completions === 1 ? '' : 's') +
              (s.followers_now ? '<span class="tag walking">' + s.followers_now +
                ' walking now</span>' : '') +
              (fastest && fastest.name === s.name
                ? '<span class="spreading">↑ spreading fastest</span>' : '') +
            '</div>' +
          '</div>';
        }).join('') : '<div class="ws-none">none yet</div>') +
      '</div>' +
    '</div>';
  }

  function councilHtml(d) {
    if (!d || !d.available) return unavailable(d, 'Council');
    const before = d.before_council || [], force = d.in_force || [], refused = d.refused || [];
    if (!before.length && !force.length && !refused.length) {
      return '<div class="book-empty"><b>Nothing before the council</b>' +
        '<span>No agent has filed a procedure of state. This tab being empty is ' +
        'the expected condition of a town, not a missing view.</span></div>';
    }
    function seats(i) {
      const req = i.votes_required || 0, yes = i.yes_votes || 0, total = i.seats || 0;
      let bar = '';
      for (let k = 0; k < total; k++) bar += '<i class="' + (k < yes ? 'on' : '') + '"></i>';
      return '<div class="seatbar"><span class="seats">' + bar + '</span>' +
        '<span class="seat-text"><b>' + yes + '</b> of ' + total + ' seats · needs ' + req + '</span></div>';
    }
    function item(i, kind) {
      return '<div class="cn-row ' + kind + '">' +
        '<div class="cn-title">“' + esc(i.title || i.skill) + '”</div>' +
        '<div class="cn-by">moved by ' + esc(i.moved_by_name || i.author_name || i.author_agent_id) +
          (i.moved_tick != null ? ' · tick ' + i.moved_tick : '') + '</div>' +
        (kind === 'before' ? seats(i) +
          '<div class="cn-then">then: the president signs or vetoes</div>' : '') +
        (kind === 'force' && i.enacted_tick != null
          ? '<div class="cn-when">enacted ' + i.enacted_tick + '</div>' : '') +
        (kind === 'refused'
          ? '<div class="cn-when">' + esc(i.status || 'refused') +
            (i.vetoed_tick != null ? ' at tick ' + i.vetoed_tick : '') +
            (i.veto_note ? ' — ' + esc(i.veto_note) : '') + '</div>' : '') +
      '</div>';
    }
    return '<div class="council">' +
      (before.length ? '<div class="cn-section"><h3>Before the council</h3>' +
        before.map(function (i) { return item(i, 'before'); }).join('') + '</div>' : '') +
      (force.length ? '<div class="cn-section"><h3>In force <span class="ws-count">' +
        force.length + '</span></h3>' +
        force.map(function (i) { return item(i, 'force'); }).join('') + '</div>' : '') +
      (refused.length ? '<div class="cn-section"><h3>Refused <span class="ws-count">' +
        refused.length + '</span></h3>' +
        '<p class="cn-note">Vetoed or voted down. There is no repeal transition in the ' +
        'charter, so a rule the town dropped would appear as a revision, not here.</p>' +
        refused.map(function (i) { return item(i, 'refused'); }).join('') + '</div>' : '') +
    '</div>';
  }

  function renderWorkshop() {
    const el = document.getElementById('workshopBody');
    if (el) el.innerHTML = workshopHtml(state.workshop);
  }
  function renderCouncil() {
    const el = document.getElementById('councilBody');
    if (el) el.innerHTML = councilHtml(state.council);
  }

  function pollTown() {
    fetchJson('api/town/workshop').then(function (d) { state.workshop = d; renderWorkshop(); });
    fetchJson('api/town/council').then(function (d) { state.council = d; renderCouncil(); });
  }

  window.PartE = {
    skillsTabHtml: skillsTabHtml,
    followingLabel: followingLabel,
    isFollowing: isFollowing,
    paintFollowSlots: paintFollowSlots,
    renderWorkshop: renderWorkshop,
    renderCouncil: renderCouncil,
    workshopHtml: workshopHtml,
    councilHtml: councilHtml,
    escape: esc,
  };

  if (location.protocol !== 'file:') {
    pollTown();
    setInterval(pollTown, 15000);
  }
})();
