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

  // 0.6.23 #178 Phase 2: subscribe to the hub SSE stream for instant
  // state confirmations. Polling stays as a fallback (also reconciles
  // server-rendered state on initial page load) but SSE updates land
  // in ~100ms when a device heartbeats. EventSource handles auth via
  // the session cookie; no separate token needed on the same origin.
  var sse = null;
  // Derive from the polling URL so dev (/api/v1/...) and prod
  // (/rebooter/api/v1/...) both work without a separate data attribute.
  var sseUrl = url.replace(/\/devices\/live$/, '/events/commands');
  function connectSSE() {
    if (sse) try { sse.close(); } catch (e) {}
    try {
      sse = new EventSource(sseUrl);
    } catch (e) { return; }
    sse.addEventListener('device_state_changed', function (msg) {
      try {
        var ev = JSON.parse(msg.data);
        // List page: find the row and apply just the rapid-update fields
        if (listRows.length) {
          for (var i = 0; i < listRows.length; i++) {
            if (listRows[i].dataset.deviceId === ev.device_id) {
              applyListRow(listRows[i], {
                heartbeat_state: 'online',  // a fresh heartbeat means online
                latest_relay_on: ev.latest_relay_on,
              });
              break;
            }
          }
        }
        if (detailPanel && detailPanel.dataset.deviceLive === ev.device_id) {
          applyDetailPanel({
            heartbeat_state: 'online',
            latest_relay_on: ev.latest_relay_on,
            last_seen_at: ev.ts,
            uptime_seconds: ev.uptime_seconds,
            free_heap: ev.free_heap,
            max_free_block: ev.max_free_block,
            heap_fragmentation_pct: ev.heap_fragmentation_pct,
          });
        }
      } catch (e) {}
    });
    sse.addEventListener('command_queued', function (msg) {
      // A relay command was just queued — bump to fast-poll so we catch
      // the device-side state flip ASAP. The LAN agent (if running) is
      // about to POST to the device directly; the next heartbeat will
      // carry the confirmed new state and re-emit device_state_changed.
      try {
        var ev = JSON.parse(msg.data);
        if (ev.type && ev.type.indexOf('relay_') === 0) {
          fastUntil = Date.now() + FAST_BURST_MS;
        }
      } catch (e) {}
    });
    sse.onerror = function () {
      // EventSource auto-reconnects after ~3s; nothing to do here.
    };
  }
  connectSSE();

  // LOW #4 (code review): tear down on page hide so back-forward cache
  // navigation doesn't leak intervals + open SSE connections.
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (sse) try { sse.close(); } catch (e) {}
      sse = null;
    } else if (!sse) {
      connectSSE();
    }
  });
  window.addEventListener('pagehide', function () {
    if (sse) try { sse.close(); } catch (e) {}
  });

  (function loop() {
    if (!document.hidden) tick();
    var delay = Date.now() < fastUntil ? POLL_FAST_MS : POLL_SLOW_MS;
    setTimeout(loop, delay);
  })();
})();
