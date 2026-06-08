/* ⌘K command palette (0.6.28 PR-4).
 *
 * Brutal review prescribed a command palette as the primary nav so the
 * 6-item top + 6-item bottom nav can shrink to 4 without discoverability
 * cratering. This is the vanilla-JS, CSP-compliant implementation:
 *   - no eval, no inline handlers, no innerHTML on untrusted strings
 *   - feeds from /api/v1/admin/search?q=<text>
 *   - opened by ⌘K (Mac) or Ctrl+K (everyone else); also '/' when no
 *     input is focused
 *   - Enter on a device or page → navigate; on an action → POST
 *     (action verbs are permission-gated server-side)
 *
 * The DOM is built up once in JS, hidden by default, shown on demand. */

(function () {
  // Build the URL from a data attribute we'll add in layout.html.
  var meta = document.body && document.body.dataset;
  if (!meta) return;
  var searchUrl = meta.cmdkSearchUrl;
  if (!searchUrl) return;

  // ---- DOM scaffolding ---------------------------------------------------
  var overlay = document.createElement('div');
  overlay.className = 'v3-cmdk-overlay';
  overlay.hidden = true;
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Command palette');

  var panel = document.createElement('div');
  panel.className = 'v3-cmdk-panel';
  overlay.appendChild(panel);

  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'v3-cmdk-input';
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('autocorrect', 'off');
  input.setAttribute('spellcheck', 'false');
  input.setAttribute('placeholder', 'Search devices, pages, actions…  (Esc to close)');
  input.setAttribute('aria-label', 'Search');
  panel.appendChild(input);

  var list = document.createElement('ul');
  list.className = 'v3-cmdk-list';
  list.setAttribute('role', 'listbox');
  panel.appendChild(list);

  document.body.appendChild(overlay);

  // Reusable hidden form for action items (relay_on/off, device_restart).
  // Token comes from a CSRF meta tag if present; otherwise relies on
  // session cookie + same-origin protection like the existing forms.
  var actionForm = document.createElement('form');
  actionForm.method = 'POST';
  actionForm.style.display = 'none';
  document.body.appendChild(actionForm);

  // ---- state -------------------------------------------------------------
  var items = [];        // current candidate list from /search
  var filtered = [];     // after client-side prefix-substring rank
  var selectedIdx = 0;
  var open = false;
  var lastFetchAt = 0;
  var pendingFetch = null;
  var FETCH_DEBOUNCE_MS = 120;

  // ---- helpers -----------------------------------------------------------
  function show() {
    if (open) return;
    open = true;
    overlay.hidden = false;
    document.body.classList.add('v3-cmdk-open');
    input.value = '';
    fetchItems('');
    setTimeout(function () { input.focus(); }, 0);
  }
  function hide() {
    if (!open) return;
    open = false;
    overlay.hidden = true;
    document.body.classList.remove('v3-cmdk-open');
    items = [];
    filtered = [];
    renderList();
  }
  function isTypingTarget(el) {
    if (!el) return false;
    var tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
    if (el.isContentEditable) return true;
    return false;
  }

  // 0.6.36 hotfix Finding #4: out-of-order fetch staleness. Pre-fix
  // typing "abc" then backspacing to "a" could land the "abc" response
  // after the "a" response, overwriting items with the narrower set —
  // common-prefix matches silently vanished until the next keystroke.
  // Each fetch now uses an AbortController that the next fetch cancels.
  var inFlight = null;
  function fetchItems(q) {
    var url = searchUrl + (q ? ('?q=' + encodeURIComponent(q)) : '');
    if (pendingFetch) clearTimeout(pendingFetch);
    pendingFetch = setTimeout(function () {
      pendingFetch = null;
      if (inFlight) { try { inFlight.abort(); } catch (e) {} }
      var controller = ('AbortController' in window) ? new AbortController() : null;
      inFlight = controller;
      var opts = { credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
      if (controller) opts.signal = controller.signal;
      fetch(url, opts)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (inFlight !== controller) return;  // a newer fetch superseded us
          inFlight = null;
          if (!j || !j.ok || !j.data || !Array.isArray(j.data.items)) return;
          items = j.data.items;
          rank(q);
        })
        .catch(function () { /* aborted or network — ignore */ });
    }, FETCH_DEBOUNCE_MS);
  }

  function rank(q) {
    q = (q || '').toLowerCase().trim();
    if (!q) {
      filtered = items.slice(0, 50);
    } else {
      filtered = items
        .map(function (it) {
          var hay = (it.label + ' ' + (it.subtitle || '')).toLowerCase();
          var score = 0;
          if (hay.indexOf(q) === 0) score = 100;
          else if (hay.indexOf(' ' + q) >= 0) score = 80;
          else if (hay.indexOf(q) >= 0) score = 60;
          else return null;
          return { it: it, score: score };
        })
        .filter(Boolean)
        .sort(function (a, b) { return b.score - a.score; })
        .slice(0, 50)
        .map(function (w) { return w.it; });
    }
    selectedIdx = 0;
    renderList();
  }

  function renderList() {
    while (list.firstChild) list.removeChild(list.firstChild);
    for (var i = 0; i < filtered.length; i++) {
      var it = filtered[i];
      var li = document.createElement('li');
      li.className = 'v3-cmdk-item';
      li.setAttribute('role', 'option');
      if (i === selectedIdx) li.classList.add('v3-cmdk-item-selected');
      var kind = document.createElement('span');
      kind.className = 'v3-cmdk-kind';
      kind.textContent =
        it.kind === 'device' ? '⏻' :
        it.kind === 'action' ? '➤' :
        it.kind === 'page'   ? '☷' : '·';
      li.appendChild(kind);
      var label = document.createElement('span');
      label.className = 'v3-cmdk-label';
      label.textContent = it.label;
      li.appendChild(label);
      if (it.subtitle) {
        var sub = document.createElement('span');
        sub.className = 'v3-cmdk-sub';
        sub.textContent = it.subtitle;
        li.appendChild(sub);
      }
      li.addEventListener('click', function (idx) {
        return function () { activate(idx); };
      }(i));
      list.appendChild(li);
    }
  }

  function activate(idx) {
    var it = filtered[idx];
    if (!it) return;
    hide();
    if (it.kind === 'device' || it.kind === 'page') {
      window.location.href = it.url;
      return;
    }
    if (it.kind === 'action') {
      while (actionForm.firstChild) actionForm.removeChild(actionForm.firstChild);
      actionForm.action = it.post_url;
      var t = document.createElement('input');
      t.type = 'hidden';
      t.name = 'type';
      t.value = it.command_type;
      actionForm.appendChild(t);
      actionForm.submit();
    }
  }

  // ---- event wiring ------------------------------------------------------
  document.addEventListener('keydown', function (e) {
    // 0.6.32 hotfix: Escape ALWAYS closes the overlay if it's visible,
    // even if our state thinks it's not open. Pre-fix, a CSS bug let the
    // overlay paint with open=false and Escape returned early via the
    // !open branch — locking the operator out of the page until reload.
    var visuallyOpen = !overlay.hidden;
    if (e.key === 'Escape' && visuallyOpen) {
      e.preventDefault();
      hide();
      return;
    }
    // open palette
    if (!open) {
      var isCmdK = (e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey);
      var isSlash = e.key === '/' && !isTypingTarget(document.activeElement);
      if (isCmdK || isSlash) {
        e.preventDefault();
        show();
      }
      return;
    }
    // open: handle nav/submit
    if (e.key === 'Escape') { e.preventDefault(); hide(); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIdx = Math.min(filtered.length - 1, selectedIdx + 1);
      renderList();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIdx = Math.max(0, selectedIdx - 1);
      renderList();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      activate(selectedIdx);
      return;
    }
  });

  input.addEventListener('input', function () { rank(input.value); fetchItems(input.value); });
  overlay.addEventListener('click', function (e) { if (e.target === overlay) hide(); });
})();
