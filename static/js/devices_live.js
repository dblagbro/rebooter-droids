/* Devices live-state poller.

   Single script that handles BOTH the device-list page and the
   device-detail page. Polls /admin/devices/live every 3s, updates the
   relevant DOM in place. Activated by whichever DOM hooks exist:

     - On `<tr data-device-id="...">` rows (list page): swap online badge
       in `[data-live="online"]` and relay button label/colour/form-action
       in `[data-live="relay"]` for each device row.

     - On `<div data-device-live="...">` (detail page): update the compact
       overview strip (heartbeat-badge, last-seen-age, relay, uptime,
       free-heap, max-free-block, frag-pct) for the named device.

   After a relay-toggle form submit, polling speeds up to 1Hz for 30s so
   the eventual state change appears as soon as the device reports back.

   The endpoint URL is read from `<body data-devices-live-url="...">` so
   the script can stay static (CSP `script-src 'self'`, BUG-049).

   Extracted from inline blocks in devices_list.html (0.6.12) and
   device_detail.html (0.6.16) so it actually runs in production. */
(function () {
  var bodyEl = document.body;
  var url = bodyEl ? bodyEl.dataset.devicesLiveUrl : null;
  if (!url) return;

  var listRows = document.querySelectorAll('tr[data-device-id]');
  var detailPanel = document.querySelector('[data-device-live]');
  if (!listRows.length && !detailPanel) return;

  var fastUntil = 0;
  var POLL_FAST_MS = 1000;
  var POLL_SLOW_MS = 3000;
  var FAST_BURST_MS = 30000;

  function setText(root, name, val) {
    var el = root.querySelector('[data-live="' + name + '"]');
    if (el) el.textContent = val;
  }

  function applyListRow(tr, dev) {
    var onlineCell = tr.querySelector('[data-live="online"]');
    if (onlineCell) {
      var html;
      if (dev.heartbeat_state === 'online') {
        html = '<span class="badge green" title="last heartbeat within 3 min">online</span>';
      } else if (dev.heartbeat_state === 'never') {
        html = '<span class="badge" title="device row exists but no heartbeat ever received">never heartbeated</span>';
      } else {
        html = '<span class="badge red" title="last heartbeat older than 3 min">offline</span>';
      }
      if (onlineCell.innerHTML.trim() !== html) onlineCell.innerHTML = html;
    }
    if (dev.latest_relay_on !== null && dev.latest_relay_on !== undefined) {
      var btn = tr.querySelector('button[data-relay-toggle]');
      var target = tr.querySelector('input[data-relay-target]');
      var label = tr.querySelector('[data-relay-label]');
      var badge = tr.querySelector('[data-relay-badge]');
      var on = !!dev.latest_relay_on;
      if (btn && target) {
        var curOn = btn.dataset.current === 'on';
        if (curOn !== on) {
          btn.dataset.current = on ? 'on' : 'off';
          btn.className = on ? 'btn' : 'btn-secondary';
          btn.style.background = on ? 'var(--green-fg,#15803d)' : '';
          btn.style.color = on ? 'white' : '';
          btn.style.fontWeight = '600';
          btn.style.minWidth = '3.2rem';
          target.value = on ? 'relay_off' : 'relay_on';
          if (label) label.textContent = on ? 'ON' : 'OFF';
        }
      }
      if (badge) {
        var wantClass = 'badge' + (on ? ' green' : '');
        if (badge.className !== wantClass) badge.className = wantClass;
        var wantText = on ? 'ON' : 'OFF';
        if (badge.textContent !== wantText) badge.textContent = wantText;
      }
    }
  }

  function fmtAge(iso) {
    if (!iso) return '—';
    var dt = new Date(iso);
    var sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (sec < 90) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    return Math.floor(sec / 3600) + 'h ago';
  }

  function applyDetailPanel(dev) {
    var badgeEl = detailPanel.querySelector('[data-live="heartbeat-badge"]');
    if (badgeEl) {
      var stateMap = { online: 'online', offline: 'offline', never: 'never heartbeated' };
      badgeEl.textContent = stateMap[dev.heartbeat_state] || 'unknown';
      badgeEl.className = 'badge ' + (
        dev.heartbeat_state === 'online' ? 'green' :
        dev.heartbeat_state === 'offline' ? 'red' : ''
      );
    }
    setText(detailPanel, 'last-seen-age', fmtAge(dev.last_seen_at));
    var relay = (dev.latest_relay_on === null || dev.latest_relay_on === undefined)
      ? '—' : (dev.latest_relay_on ? 'ON' : 'OFF');
    setText(detailPanel, 'relay', relay);
    setText(detailPanel, 'uptime', dev.uptime_seconds != null ? dev.uptime_seconds : '—');
    setText(detailPanel, 'free-heap', dev.free_heap != null ? dev.free_heap : '—');
    setText(detailPanel, 'max-free-block', dev.max_free_block != null ? dev.max_free_block : '—');
    var fp = dev.heap_fragmentation_pct;
    setText(detailPanel, 'frag-pct', fp != null ? fp : '—');
    var fpEl = detailPanel.querySelector('[data-live="frag-pct"]');
    if (fpEl) {
      fpEl.className = 'badge ' + (
        fp != null && fp >= 40 ? 'red' :
        fp != null && fp >= 25 ? 'amber' : ''
      );
    }
  }

  function tick() {
    fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.ok || !j.data || !Array.isArray(j.data.devices)) return;
        if (listRows.length) {
          var byId = {};
          for (var i = 0; i < j.data.devices.length; i++) byId[j.data.devices[i].id] = j.data.devices[i];
          for (var k = 0; k < listRows.length; k++) {
            var d = byId[listRows[k].dataset.deviceId];
            if (d) applyListRow(listRows[k], d);
          }
        }
        if (detailPanel) {
          var did = detailPanel.dataset.deviceLive;
          for (var m = 0; m < j.data.devices.length; m++) {
            if (j.data.devices[m].id === did) { applyDetailPanel(j.data.devices[m]); break; }
          }
        }
      })
      .catch(function () { /* keep polling */ });
  }

  document.addEventListener('submit', function (ev) {
    if (ev.target && ev.target.querySelector && ev.target.querySelector('button[data-relay-toggle]')) {
      fastUntil = Date.now() + FAST_BURST_MS;
    }
  }, true);

  (function loop() {
    tick();
    var delay = Date.now() < fastUntil ? POLL_FAST_MS : POLL_SLOW_MS;
    setTimeout(loop, delay);
  })();
})();
