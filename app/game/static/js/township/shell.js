/* Kasukabe township — the map-first shell.
 *
 * The page began as a document with a map near the top: you scrolled past the
 * map to reach the town's books, and clicking a resident scrolled you further
 * away from the thing you had just clicked. That is the wrong shape for what
 * this is. A map is not an illustration in a report; it is the application.
 *
 * This module rearranges the page into the shape a maps application has,
 * without touching the simulation:
 *
 *   - the map fills the window and is the only thing underneath;
 *   - the clock and controls float over it;
 *   - the resident file becomes a RIGHT dock — the whole file, not a stub, so
 *     you read a person without leaving the place they are standing;
 *   - the town's books (roster, census, treasury, scoreboard, government,
 *     health, build log) become a LEFT dock with tabs, open only when asked;
 *   - a chat dock answers questions about the town from its own live state.
 *
 * It works by REPARENTING the existing elements, never by rebuilding them.
 * Every id, every event handler and every render function in township-v2.js
 * keeps working because these are the same DOM nodes in a different place.
 * That is also why this is a separate file: the simulation does not need to
 * know how it is laid out, and a layout change should never be able to break
 * a tick.
 */
(function () {
  'use strict';

  if (!document.getElementById('village')) return;   // not the township page

  // ---------------------------------------------------------------- shell
  const shell = document.createElement('div');
  shell.className = 'shell';
  shell.innerHTML =
    '<div class="shell-map" id="shellMap"></div>' +
    // No top bar. The map IS the page: a strip of controls pinned across
    // the top covered the north edge of the town permanently, and every
    // one of those controls is something you touch occasionally and then
    // want out of the way. They live in a pane now, on a toggle.
    '<button class="dock-tab controls" id="controlsTab" type="button" aria-expanded="false"' +
      ' title="Controls, clock and census">' +
      '<span class="dt-icon">&#9881;</span><span class="dt-label">Controls</span></button>' +
    '<aside class="dock controls" id="controlsDock" hidden aria-label="Map controls">' +
      '<div class="dock-head"><b>Controls</b>' +
        '<button class="dock-close" id="controlsClose" type="button" aria-label="Close">&times;</button></div>' +
      '<div class="dock-body" id="controlsBody"></div>' +
    '</aside>' +
    '<button class="dock-tab left" id="booksTab" type="button" aria-expanded="false">' +
      '<span class="dt-icon">📚</span><span class="dt-label">Town books</span></button>' +
    '<aside class="dock left" id="leftDock" hidden aria-label="The town’s books">' +
      '<div class="dock-head"><b>Town books</b>' +
        '<button class="dock-close" id="leftClose" type="button" aria-label="Close">×</button></div>' +
      '<div class="dock-tabs" id="bookTabs"></div>' +
      '<div class="dock-body" id="leftBody"></div>' +
    '</aside>' +
    '<aside class="dock right" id="rightDock" hidden aria-label="Selection"></aside>' +
    '';
  document.body.appendChild(shell);

  const $ = (id) => document.getElementById(id);
  const shellMap = $('shellMap');
  const controlsDock = $('controlsDock');
  const controlsBody = $('controlsBody');
  const leftDock = $('leftDock');
  const rightDock = $('rightDock');
  const leftBody = $('leftBody');
  const bookTabs = $('bookTabs');

  // -------------------------------------------------- move, never rebuild
  const viewport = document.querySelector('.viewport.v2');
  if (viewport) shellMap.appendChild(viewport);

  const titles = document.querySelector('header.top .titles');
  if (titles) controlsBody.appendChild(titles);

  // Search goes in the pane with everything else. Floating over the map it
  // was the one element that could never be dismissed, sitting on top of
  // whatever you had just panned to.
  const search = document.querySelector('.map-search');
  if (search) { search.classList.add('in-pane'); controlsBody.appendChild(search); }

  const consoleBar = document.querySelector('.console');
  if (consoleBar) controlsBody.appendChild(consoleBar);

  // The resident file and the place card become the right dock. The floating
  // map card is retired: it showed a summary over the map while the full file
  // lived a scroll away, which is the split this shell exists to remove.
  const profilePanel = $('profilePanel');
  const placeCard = $('placeCard');
  if (profilePanel) rightDock.appendChild(profilePanel);
  if (placeCard) rightDock.appendChild(placeCard);

  // Everything else the page had becomes a book in the left dock.
  const BOOKS = [
    // Preflight leads. It is the one book whose answer decides whether any
    // of the others mean anything: a run that failed to start looks, from
    // every other panel here, exactly like a run that started and went quiet.
    { id: 'preflight', label: 'Preflight', icon: '✅', match: (h) => /^\s*preflight\b/i.test(h) },
    // Then Part E.3/E.4: what the town has authored is the thing this
    // instrument exists to observe, and shelving it behind People/Money
    // would make the town's own inventions the hardest book to reach.
    // The election, when the town is holding one: before the Workshop,
    // because a vote is the one thing in the town with a deadline.
    { id: 'election', label: 'Election',   icon: '🗳', match: (h) => /^\s*🗳?\s*election\b/i.test(h) },
    { id: 'workshop', label: 'Workshop',   icon: '🛠', match: (h) => /workshop/i.test(h) },
    { id: 'council',  label: 'Council',    icon: '⚖', match: (h) => /council/i.test(h) },
    // Talk sits with them: agent-to-agent speech is the other half of "what
    // is this town doing", and it had no surface at all until now.
    // Before 'talk', and before 'town' (whose matcher takes the word
    // "conversation"), so the group-chat panel gets its own tab rather
    // than falling into More.
    { id: 'groupchat', label: 'Group chats', icon: '👥', match: (h) => /group chat/i.test(h) },
    { id: 'talk',     label: 'Talk',       icon: '💬', match: (h) => /^\s*talk\b/i.test(h) },
    { id: 'authored', label: 'Authored',   icon: '✍', match: (h) => /^\s*authored\b/i.test(h) },
    { id: 'people',   label: 'People',     icon: '🧑‍🤝‍🧑', match: (h) => /characters|resident|roster/i.test(h) },
    { id: 'money',    label: 'Money',      icon: '💰', match: (h) => /scoreboard|treasury/i.test(h) },
    { id: 'govern',   label: 'Government', icon: '🏛️', match: (h) => /government|built/i.test(h) },
    { id: 'health',   label: 'Health',     icon: '🩺', match: (h) => /health|mortality/i.test(h) },
    { id: 'town',     label: 'Town',       icon: '🏘️', match: (h) => /census|conversation|doing|everyone/i.test(h) },
  ];
  const shelved = {};
  BOOKS.forEach((b) => { shelved[b.id] = []; });
  shelved.other = [];

  Array.prototype.slice.call(document.querySelectorAll('.wrap .panel, .wrap .stage, .wrap .census-grid'))
    .forEach((el) => {
      if (el.id === 'mapPanel' || el.id === 'profilePanel') return;
      if (rightDock.contains(el) || shellMap.contains(el)) return;
      const head = (el.querySelector('.panel-head') || el).textContent || '';
      const book = BOOKS.find((b) => b.match(head));
      (book ? shelved[book.id] : shelved.other).push(el);
    });

  const usedBooks = BOOKS.filter((b) => shelved[b.id].length);
  if (shelved.other.length) usedBooks.push({ id: 'other', label: 'More', icon: '📄' });

  // Talk opens by default; Preflight opens instead when it is red.
  //
  // The tab strip still LEADS with Preflight, for the reason its own entry
  // gives: a run that failed to start looks, from every other panel here,
  // exactly like a run that started and went quiet. But on a healthy run
  // the thing worth watching is the town talking, and the Talk panel was
  // being rendered full and left at zero height behind a tab nobody
  // clicked - 20,473 characters of conversation, with names, places and
  // timestamps, invisible. "Why didn't A2A happen" was asked repeatedly of
  // a run in which it had.
  const preflightRed = !!document.querySelector(
    '#preflightPanel .fail, #preflightPanel .bad, #preflightPanel [data-status="FAIL"]'
  );
  // Group chats first: it is the panel that answers "is the town actually
  // talking to each other", which is the question this viewer exists for.
  // Talk (a flat tail of every remark) is the fallback, then whatever
  // there is.
  const prefer = ['groupchat', 'talk'];
  // `?book=<id>` opens that book instead - what a recording of the run asks
  // for (`?book=election`), so the video shows the vote and not the chat.
  let asked = null;
  try { asked = new URLSearchParams(location.search).get('book'); } catch (_) { asked = null; }
  const openId = preflightRed
    ? 'preflight'
    : ((asked && usedBooks.some((b) => b.id === asked) && asked) ||
       prefer.find((id) => usedBooks.some((b) => b.id === id)) || (usedBooks[0] || {}).id);

  usedBooks.forEach((b, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'book-tab' + (b.id === openId ? ' on' : '');
    btn.dataset.book = b.id;
    btn.innerHTML = '<span>' + b.icon + '</span>' + b.label;
    bookTabs.appendChild(btn);

    const page = document.createElement('div');
    page.className = 'book-page';
    page.dataset.book = b.id;
    page.hidden = b.id !== openId;
    shelved[b.id].forEach((el) => page.appendChild(el));
    leftBody.appendChild(page);
  });

  bookTabs.addEventListener('click', (e) => {
    const btn = e.target.closest('.book-tab');
    if (!btn) return;
    bookTabs.querySelectorAll('.book-tab').forEach((b) => b.classList.toggle('on', b === btn));
    leftBody.querySelectorAll('.book-page').forEach((p) => { p.hidden = p.dataset.book !== btn.dataset.book; });
  });

  // What is left of the old document is now empty scaffolding.
  ['.wrap header.top', '.hero', '.scroll-cue', '.legend'].forEach((sel) => {
    const el = document.querySelector(sel);
    if (el && !el.querySelector('.viewport')) el.remove();
  });

  // ------------------------------------------------------------ dock open
  function openDock(dock, tab, on) {
    dock.hidden = !on;
    if (tab) tab.setAttribute('aria-expanded', String(on));
    document.body.classList.toggle('dock-' + (dock === leftDock ? 'left' : 'chat'), on);
    if (window.Village && Village.fit) requestAnimationFrame(resizeMap);
  }
  const booksTab = $('booksTab');
  booksTab.addEventListener('click', () => openDock(leftDock, booksTab, leftDock.hidden));
  $('leftClose').addEventListener('click', () => openDock(leftDock, booksTab, false));

  // Open the books on load, on Talk.
  //
  // The drawer started shut, so the panel a reader most wants sat behind two
  // closed doors: a collapsed drawer, and inside it a tab that was not the
  // one showing. The Talk panel was rendering 20,473 characters of
  // conversation - names, places, timestamps - at zero height, and "why
  // didn't A2A happen" was asked repeatedly of runs in which it had.
  //
  // The map is still the page; this is a drawer over its edge, and one click
  // on the tab closes it again.
  openDock(leftDock, booksTab, true);

  // ---------------------------------------------------------- controls
  // The controls pane and the selection pane share the right edge, and only
  // one may hold it. Two drawers on the same side either overlap or fight
  // over width; and in practice they answer different questions - "drive
  // the map" versus "read this person" - so you are never doing both.
  const controlsTab = $('controlsTab');
  function showControls(on) {
    controlsDock.hidden = !on;
    controlsTab.setAttribute('aria-expanded', String(on));
    document.body.classList.toggle('dock-controls', on);
    if (on) showRight(false);
    if (window.Village && Village.fit) requestAnimationFrame(resizeMap);
  }
  controlsTab.addEventListener('click', () => showControls(controlsDock.hidden));
  $('controlsClose').addEventListener('click', () => showControls(false));
  // The right dock opens itself whenever something is selected, and the page
  // never scrolls to do it - that was the original complaint.
  function showRight(on) {
    rightDock.hidden = !on;
    document.body.classList.toggle('dock-right', on);
    // Selecting a resident takes the right edge from the controls pane -
    // see showControls for why only one may hold it.
    if (on && !controlsDock.hidden) {
      controlsDock.hidden = true;
      controlsTab.setAttribute('aria-expanded', 'false');
      document.body.classList.remove('dock-controls');
    }
  }
  if (typeof window.openModal === 'function') {
    window.openModal = function () {
      const panel = $('profilePanel');
      if (panel) panel.hidden = false;
      showRight(true);
    };
  }
  if (typeof window.closeModal === 'function') {
    const inner = window.closeModal;
    window.closeModal = function () {
      inner.apply(this, arguments);
      if (!placeCard || placeCard.hidden) showRight(false);
    };
  }
  // selectPlace / hidePlaceCard drive the same dock, and so does the Real sim
  // panel - it renders into the same card, so it gets the same treatment
  // rather than a second way of opening the same drawer.
  ['selectPlace', 'hidePlaceCard', 'showEnginePanel', 'hideEnginePanel'].forEach((name) => {
    if (typeof window[name] !== 'function') return;
    const inner = window[name];
    window[name] = function () {
      const out = inner.apply(this, arguments);
      const openThing = (placeCard && !placeCard.hidden) ||
                        (profilePanel && !profilePanel.hidden);
      showRight(!!openThing);
      return out;
    };
  });
  if (typeof window.selectAgent === 'function') {
    const inner = window.selectAgent;
    window.selectAgent = function () {
      const out = inner.apply(this, arguments);
      const panel = $('profilePanel');
      if (panel) panel.hidden = false;
      showRight(true);
      return out;
    };
  }
  const profileClose = $('profileClose');
  if (profileClose) profileClose.addEventListener('click', () => showRight(false));

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!rightDock.hidden) { showRight(false); return; }
    if (!controlsDock.hidden) { showControls(false); return; }
    if (!leftDock.hidden) openDock(leftDock, booksTab, false);
  });

  // ------------------------------------------------------------- map size
  // The canvas is a fixed-size frame the renderer draws into; the shell only
  // decides how much of the window it covers. Refit on resize so the town
  // still fills a window that just changed shape.
  // The map is drawn AT the size it is shown, not scaled into place from a
  // fixed bitmap. Village.resize() sets the canvas to the viewport in real
  // device pixels and refits the town, so "full screen" means full screen.
  function sizeNow() {
    if (!window.Village || !Village.resize) return;
    try {
      const box = shellMap.getBoundingClientRect();
      Village.resize(box.width || window.innerWidth, box.height || window.innerHeight);
    } catch (err) { /* mid-bake; the next call catches it */ }
  }
  // Immediately on load: debouncing the FIRST size means the map is briefly
  // drawn letterboxed at its old fixed frame before snapping to the window.
  sizeNow();

  let fitTimer = null;
  function resizeMap() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(sizeNow, 60);   // dragging a window edge fires this a lot
  }
  window.addEventListener('resize', resizeMap);

  // Signboard widths are measured from the font. Measuring before the webfont
  // arrives sizes every pill to the fallback, so redraw once it is really in.
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => { try { Village.fit(); } catch (err) { /* ignore */ } });
  }

  // Search results should sit above the docks, and a search hit should open
  // the right dock rather than scroll.
  document.body.classList.add('shell-on');
  requestAnimationFrame(resizeMap);
})();
