(function () {
  'use strict';

  if (!document.getElementById('village')) return;

  const shell = document.createElement('div');
  shell.className = 'shell';
  shell.innerHTML =
    '<div class="shell-map" id="shellMap"></div>' +
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

  const viewport = document.querySelector('.viewport.v2');
  if (viewport) shellMap.appendChild(viewport);

  const titles = document.querySelector('header.top .titles');
  if (titles) controlsBody.appendChild(titles);

  const search = document.querySelector('.map-search');
  if (search) { search.classList.add('in-pane'); controlsBody.appendChild(search); }

  const consoleBar = document.querySelector('.console');
  if (consoleBar) controlsBody.appendChild(consoleBar);

  const profilePanel = $('profilePanel');
  const placeCard = $('placeCard');
  if (profilePanel) rightDock.appendChild(profilePanel);
  if (placeCard) rightDock.appendChild(placeCard);

  const BOOKS = [
    { id: 'preflight', label: 'Preflight', icon: '✅', match: (h) => /^\s*preflight\b/i.test(h) },
    { id: 'election', label: 'Election',   icon: '🗳', match: (h) => /^\s*🗳?\s*election\b/i.test(h) },
    { id: 'workshop', label: 'Workshop',   icon: '🛠', match: (h) => /workshop/i.test(h) },
    { id: 'council',  label: 'Council',    icon: '⚖', match: (h) => /council/i.test(h) },
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

  const preflightRed = !!document.querySelector(
    '#preflightPanel .fail, #preflightPanel .bad, #preflightPanel [data-status="FAIL"]'
  );
  const prefer = ['groupchat', 'talk'];
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

  ['.wrap header.top', '.hero', '.scroll-cue', '.legend'].forEach((sel) => {
    const el = document.querySelector(sel);
    if (el && !el.querySelector('.viewport')) el.remove();
  });

  function openDock(dock, tab, on) {
    dock.hidden = !on;
    if (tab) tab.setAttribute('aria-expanded', String(on));
    document.body.classList.toggle('dock-' + (dock === leftDock ? 'left' : 'chat'), on);
    if (window.Village && Village.fit) requestAnimationFrame(resizeMap);
  }
  const booksTab = $('booksTab');
  booksTab.addEventListener('click', () => openDock(leftDock, booksTab, leftDock.hidden));
  $('leftClose').addEventListener('click', () => openDock(leftDock, booksTab, false));

  openDock(leftDock, booksTab, true);

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
  function showRight(on) {
    rightDock.hidden = !on;
    document.body.classList.toggle('dock-right', on);
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

  function sizeNow() {
    if (!window.Village || !Village.resize) return;
    try {
      const box = shellMap.getBoundingClientRect();
      Village.resize(box.width || window.innerWidth, box.height || window.innerHeight);
    } catch (err) {  }
  }
  sizeNow();

  let fitTimer = null;
  function resizeMap() {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(sizeNow, 60);
  }
  window.addEventListener('resize', resizeMap);

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => { try { Village.fit(); } catch (err) {  } });
  }

  document.body.classList.add('shell-on');
  requestAnimationFrame(resizeMap);
})();
