/* PR-5: mobile swipe actions on device rows (0.6.30).
 *
 * Activated only when the viewport reports (hover: none) — i.e. touch
 * pointers. On desktops the existing click handlers stay primary.
 *
 *   swipe-right on a row → fire the row's relay-toggle form (optimistic;
 *                          the live SSE update or fast-poll snaps the
 *                          state back if the device doesn't confirm).
 *   swipe-left  on a row → reveal a "Reboot" affordance under the row;
 *                          tap it within 4s to confirm; otherwise the
 *                          affordance hides.
 *
 * CSP-compliant: vanilla DOM events, no eval, no innerHTML on user data.
 */

(function () {
  // Bail on hover-capable pointers (mouse). We only want touch.
  if (!window.matchMedia) return;
  if (!window.matchMedia('(hover: none)').matches) return;

  var SWIPE_MIN_PX = 60;
  var SWIPE_MAX_MS = 400;
  var REBOOT_AFFORDANCE_MS = 4000;

  function findRow(node) {
    while (node && node !== document.body) {
      if (node.tagName === 'TR' && node.dataset && node.dataset.deviceId) return node;
      node = node.parentNode;
    }
    return null;
  }

  // Track per-row gesture state.
  var active = null;  // { row, startX, startY, startT }

  document.addEventListener('pointerdown', function (e) {
    var row = findRow(e.target);
    if (!row) return;
    // 0.6.36 hotfix Finding #5: pre-fix the bail caught the device-name
    // anchor inside the row — the natural finger target on mobile —
    // so right-swipe-toggle and left-swipe-reboot silently did nothing.
    // Narrow the anchor exclusion to anchors OUTSIDE the row (header
    // nav, breadcrumbs); buttons + inputs still bail because those
    // need their tap.
    var t = e.target;
    if (t.closest && (t.closest('button') || t.closest('input'))) return;
    var a = t.closest && t.closest('a');
    if (a && !a.closest('tr[data-device-id]')) return;
    active = { row: row, startX: e.clientX, startY: e.clientY, startT: Date.now() };
  });

  document.addEventListener('pointercancel', function () { active = null; });

  document.addEventListener('pointerup', function (e) {
    if (!active) return;
    var dx = e.clientX - active.startX;
    var dy = e.clientY - active.startY;
    var dt = Date.now() - active.startT;
    var row = active.row;
    active = null;
    if (dt > SWIPE_MAX_MS) return;
    if (Math.abs(dy) > Math.abs(dx)) return;  // not a horizontal swipe
    if (Math.abs(dx) < SWIPE_MIN_PX) return;

    if (dx > 0) {
      // Right-swipe → fire the relay-toggle form, if present + enabled.
      var btn = row.querySelector('button[data-relay-toggle]');
      if (btn && !btn.disabled) {
        // Optimistic visual flip — actual confirmation arrives via SSE
        // (#178 phase 2). The form submit triggers the existing
        // confirm-handler chain so protected devices still warn.
        var form = btn.closest('form');
        if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
      }
    } else {
      // Left-swipe → reveal Reboot affordance under the row. Vanilla
      // div appended to the row; one tap on its button fires the
      // device_restart command via the existing post route.
      showRebootAffordance(row);
    }
  });

  function showRebootAffordance(row) {
    var existing = row.parentNode.querySelector('.v3-swipe-affordance[data-for="' + row.dataset.deviceId + '"]');
    if (existing) { existing.remove(); return; }
    var tr = document.createElement('tr');
    tr.className = 'v3-swipe-affordance';
    tr.dataset.for = row.dataset.deviceId;
    var td = document.createElement('td');
    td.colSpan = row.children.length;
    td.style.padding = '.4rem .9rem';
    td.style.background = 'rgba(180,83,9,.08)';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.style.background = 'var(--amber-fg,#b45309)';
    btn.style.color = 'white';
    btn.textContent = 'Reboot';
    btn.addEventListener('click', function () {
      // Build the command-post URL from a data-attribute on the row if
      // present; fall back to the row's link href + '/commands'.
      var link = row.querySelector('a[href*="/devices/"]');
      if (!link) return;
      var detailHref = link.getAttribute('href');
      var postUrl = detailHref.replace(/\/?(#.*)?$/, '') + '/commands';
      var form = document.createElement('form');
      form.method = 'POST';
      form.action = postUrl;
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'type';
      input.value = 'device_restart';
      form.appendChild(input);
      var next = document.createElement('input');
      next.type = 'hidden';
      next.name = 'next';
      next.value = 'list';
      form.appendChild(next);
      document.body.appendChild(form);
      form.submit();
    });
    td.appendChild(btn);
    tr.appendChild(td);
    row.parentNode.insertBefore(tr, row.nextSibling);
    setTimeout(function () { if (tr.parentNode) tr.remove(); }, REBOOT_AFFORDANCE_MS);
  }
})();
