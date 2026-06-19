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
    // 0.6.36 hotfix Finding #2: while a pending optimistic flip is in
    // flight for this device, the next slow-poll tick (~3s) or SSE
    // event could clobber the optimistic state with the stale relay
    // value the device hasn't yet had time to flip. Skipping the row
    // refresh during the pending window preserves the optimistic UI
    // until clearPendingIfMatches confirms or the snap-back fires.
    // The row's data-device-id is the source of truth — the partial
    // dev object passed by the SSE path doesn't carry id.
    var rowDevId = tr.dataset.deviceId;
    if (rowDevId && pendingOptimistic[rowDevId]) return;
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

  // 0.6.52 Slice A/B: apply the relay-hero chip + state-aware toggle
  // button when SSE / poll confirms a new relay state. dev.source ===
  // "agent_ack" gets the lightning bolt + "Confirmed via LAN agent
  // (~Xms ago)" tooltip; "heartbeat" gets the heartbeat-pulse glyph.
  // The pre-fix code threw ev.source away entirely; the 0.6.50/0.6.51
  // sub-second push was invisible to the operator.
  function applyRelayHero(dev) {
    var hero = document.querySelector('[data-relay-hero-device="' + dev.id + '"]');
    if (!hero) return;
    if (dev.latest_relay_on !== null && dev.latest_relay_on !== undefined) {
      var newState = dev.latest_relay_on ? 'on' : 'off';
      hero.dataset.relayState = newState;
      hero.dataset.relaySource = dev.source || 'heartbeat';
      var stateEl = hero.querySelector('[data-live="relay-state"]');
      if (stateEl) stateEl.textContent = newState.toUpperCase();
      var sourceEl = hero.querySelector('[data-live="relay-source"]');
      if (sourceEl) {
        sourceEl.textContent = (dev.source === 'agent_ack') ? '⚡' : '♥';
        sourceEl.title = (dev.source === 'agent_ack')
          ? 'Confirmed via LAN agent — relay fired and acknowledged'
          : "Reported by device's last heartbeat";
      }
      var actionEl = hero.querySelector('[data-live="relay-action-label"]');
      if (actionEl) actionEl.textContent = (newState === 'on') ? 'Turn OFF' : 'Turn ON';
      var targetInput = hero.querySelector('[data-relay-hero-target]');
      if (targetInput) targetInput.value = (newState === 'on') ? 'relay_off' : 'relay_on';
      var btn = hero.querySelector('[data-relay-hero-btn]');
      if (btn) btn.dataset.current = newState;
    }
  }

  function applyDetailPanel(dev) {
    applyRelayHero(dev);
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
            if (d) {
              // PR-10: poll-confirmation path mirror — clear pending
              // optimistic state when the polled value matches.
              if (d.latest_relay_on !== null && d.latest_relay_on !== undefined) {
                clearPendingIfMatches(d.id, !!d.latest_relay_on);
                // 0.6.52 Slice A: hero too. Poll doesn't carry source,
                // so default to 'heartbeat' tag for the toast.
                clearHeroPendingIfMatches(d.id, !!d.latest_relay_on, 'heartbeat');
              }
              applyListRow(listRows[k], d);
            }
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

  // 0.6.31 PR-10: optimistic relay flip + 4s snap-back-on-timeout.
  // Pending optimistic states keyed by deviceId — when an SSE
  // device_state_changed event (or a tick from the slow-poll fallback)
  // confirms the matching latest_relay_on, we clear the entry. If the
  // deadline passes first, we revert the row's button + toast.
  var pendingOptimistic = {};  // {deviceId: {expectedOn: bool, deadline: number, originalLabel: string}}
  var OPTIMISTIC_TIMEOUT_MS = 4000;

  // 0.6.36 hotfix (Finding #1, CRITICAL): split visual flip from the
  // form-target mutation. Pre-fix, flipRowOptimistic ran during the
  // capture-phase submit handler AND wrote target.value BEFORE the
  // form serialized — so every relay click posted the OPPOSITE command
  // (click ON → device received OFF). Now:
  //   flipRowVisual()  — class, style, label, data-current. Safe to run
  //                      before the form serializes.
  //   updateRelayTarget() — mutate the hidden input's `value` AFTER the
  //                      current task (form serialization) completes,
  //                      so the right command goes out on this click
  //                      and the inverted command on the NEXT click.
  function flipRowVisual(tr, willBeOn) {
    var btn = tr.querySelector('button[data-relay-toggle]');
    var label = tr.querySelector('[data-relay-label]');
    if (!btn) return null;
    var original = btn.dataset.current;
    btn.dataset.current = willBeOn ? 'on' : 'off';
    btn.className = willBeOn ? 'btn' : 'btn-secondary';
    btn.style.background = willBeOn ? 'var(--green-fg,#15803d)' : '';
    btn.style.color = willBeOn ? 'white' : '';
    btn.style.fontWeight = '600';
    btn.style.minWidth = '3.2rem';
    if (label) label.textContent = willBeOn ? 'ON' : 'OFF';
    return original;
  }

  function updateRelayTarget(tr, willBeOn) {
    var target = tr.querySelector('input[data-relay-target]');
    if (target) target.value = willBeOn ? 'relay_off' : 'relay_on';
  }

  function revertRow(tr, originalCurrent) {
    var willBeOn = originalCurrent === 'on';
    flipRowVisual(tr, willBeOn);
    updateRelayTarget(tr, willBeOn);
  }

  function toast(msg, severity) {
    var existing = document.querySelector('.v3-toast');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.className = 'v3-toast' + (severity ? ' v3-toast-' + severity : '');
    el.textContent = msg;
    el.setAttribute('role', 'status');
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add('v3-toast-fade'); }, 3000);
    setTimeout(function () { el.remove(); }, 4000);
  }

  // 0.6.52 Slice A: optimistic flip + click→confirm latency timer for
  // the device-detail relay-hero. Pre-fix the device-detail page had
  // ZERO feedback in the 800ms between click and the SSE
  // device_state_changed event — the operator clicked, the page form-
  // POSTed, then waited for a Flask redirect with only the browser's
  // own load spinner. The optimistic flip + a timed success toast
  // makes the 0.6.50/0.6.51 sub-second push visible. Runs in the
  // capture phase so an outer confirm-handler that preventDefault's
  // is honored (the listener for the list-page rows below is wired
  // the same way).
  var heroPendingByDevice = {};
  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if (!form || !form.matches || !form.matches('[data-relay-hero-form]')) return;
    var hero = form.closest('[data-relay-hero-device]');
    if (!hero) return;
    var deviceId = hero.dataset.relayHeroDevice;
    var preClickOn = hero.dataset.relayState === 'on';
    var willBeOn = !preClickOn;
    fastUntil = Date.now() + FAST_BURST_MS;
    // Visual flip — synchronous on submit so the chip flips before
    // the page navigates. data-relay-state drives the CSS color.
    hero.dataset.relayState = willBeOn ? 'on' : 'off';
    hero.dataset.relayPending = '1';  // CSS pulses while pending
    var stateEl = hero.querySelector('[data-live="relay-state"]');
    if (stateEl) stateEl.textContent = willBeOn ? 'ON' : 'OFF';
    var actionEl = hero.querySelector('[data-live="relay-action-label"]');
    if (actionEl) actionEl.textContent = willBeOn ? 'Turn OFF' : 'Turn ON';
    var btn = hero.querySelector('[data-relay-hero-btn]');
    if (btn) btn.dataset.current = willBeOn ? 'on' : 'off';
    // Defer the hidden-input rewrite until AFTER the form serializes
    // so the click that just fired sends the right command and the
    // NEXT click sends the inverted one. Same trick as the list-page
    // 0.6.36 hotfix Finding #1.
    setTimeout(function () {
      var target = hero.querySelector('[data-relay-hero-target]');
      if (target) target.value = willBeOn ? 'relay_off' : 'relay_on';
    }, 0);
    queueMicrotask(function () {
      if (ev.defaultPrevented) {
        // Operator cancelled at the confirm modal — revert.
        hero.dataset.relayState = preClickOn ? 'on' : 'off';
        delete hero.dataset.relayPending;
        if (stateEl) stateEl.textContent = preClickOn ? 'ON' : 'OFF';
        if (actionEl) actionEl.textContent = preClickOn ? 'Turn OFF' : 'Turn ON';
        if (btn) btn.dataset.current = preClickOn ? 'on' : 'off';
        return;
      }
      heroPendingByDevice[deviceId] = {
        expectedOn: willBeOn,
        clickAtMs: performance.now(),
        deadline: Date.now() + OPTIMISTIC_TIMEOUT_MS,
        preClickOn: preClickOn,
      };
      setTimeout(function () {
        var entry = heroPendingByDevice[deviceId];
        if (!entry) return;
        if (Date.now() < entry.deadline) return;
        // Snap back to pre-click state — the device did not confirm.
        hero.dataset.relayState = entry.preClickOn ? 'on' : 'off';
        delete hero.dataset.relayPending;
        if (stateEl) stateEl.textContent = entry.preClickOn ? 'ON' : 'OFF';
        if (actionEl) actionEl.textContent = entry.preClickOn ? 'Turn OFF' : 'Turn ON';
        if (btn) btn.dataset.current = entry.preClickOn ? 'on' : 'off';
        toast('Device did not confirm the change', 'error');
        delete heroPendingByDevice[deviceId];
      }, OPTIMISTIC_TIMEOUT_MS + 100);
    });
  }, true);

  // When SSE / poll confirms the hero's expected state, clear the
  // pending entry, fire the success toast with measured latency, and
  // drop the pulsing-pending CSS class. The chip glyph (lightning vs
  // heartbeat) is set by applyRelayHero via dev.source.
  function clearHeroPendingIfMatches(deviceId, latestRelayOn, source) {
    var entry = heroPendingByDevice[deviceId];
    if (!entry) return;
    if (latestRelayOn === entry.expectedOn) {
      var dtMs = Math.round(performance.now() - entry.clickAtMs);
      var hero = document.querySelector('[data-relay-hero-device="' + deviceId + '"]');
      if (hero) delete hero.dataset.relayPending;
      var label = (latestRelayOn ? 'ON' : 'OFF');
      var via = (source === 'agent_ack') ? ' · confirmed via agent' : ' · confirmed via heartbeat';
      toast('Relay ' + label + ' (' + dtMs + 'ms)' + via, 'success');
      delete heroPendingByDevice[deviceId];
    }
  }

  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    var btn = form && form.querySelector ? form.querySelector('button[data-relay-toggle]') : null;
    if (!btn) return;
    fastUntil = Date.now() + FAST_BURST_MS;
    // Optimistic flip — the current button state IS the action the
    // operator just confirmed by clicking. data-current was the
    // pre-click value; we want to display its inverse.
    var tr = btn.closest('tr[data-device-id]');
    if (!tr) return;
    var preClickOn = btn.dataset.current === 'on';
    var willBeOn = !preClickOn;
    // 0.6.36 hotfix Finding #1: visual flip is synchronous, target.value
    // mutation is deferred so the form serializes the CURRENT click's
    // value before we rewrite it for the NEXT click.
    var originalCurrent = flipRowVisual(tr, willBeOn);
    setTimeout(function () { updateRelayTarget(tr, willBeOn); }, 0);
    var deviceId = tr.dataset.deviceId;
    // 0.6.36 hotfix Finding #3: defer the pendingOptimistic registration
    // until after the bubble-phase confirm-handler has had its say. If
    // confirm_handlers.js preventDefault'd this submit, evt.defaultPrevented
    // will be true — revert visually and skip the timeout entirely so no
    // spurious "Device did not confirm" toast fires 4s later.
    queueMicrotask(function () {
      if (ev.defaultPrevented) {
        flipRowVisual(tr, preClickOn);  // revert visual
        updateRelayTarget(tr, preClickOn);  // revert target
        return;
      }
      pendingOptimistic[deviceId] = {
        expectedOn: willBeOn,
        originalCurrent: originalCurrent,
        deadline: Date.now() + OPTIMISTIC_TIMEOUT_MS,
      };
      setTimeout(function () {
        var entry = pendingOptimistic[deviceId];
        if (!entry) return;  // already confirmed
        if (Date.now() < entry.deadline) return;
        var liveBtn = tr.querySelector('button[data-relay-toggle]');
        if (!liveBtn) { delete pendingOptimistic[deviceId]; return; }
        var liveOn = liveBtn.dataset.current === 'on';
        if (liveOn !== entry.expectedOn) {
          revertRow(tr, entry.originalCurrent);
          toast('Device did not confirm the change', 'error');
        }
        delete pendingOptimistic[deviceId];
      }, OPTIMISTIC_TIMEOUT_MS + 100);
    });
  }, true);

  // When SSE / poll confirms the expected state for a pending entry,
  // clear it so the timeout doesn't fire.
  function clearPendingIfMatches(deviceId, latestRelayOn) {
    var entry = pendingOptimistic[deviceId];
    if (!entry) return;
    if (latestRelayOn === entry.expectedOn) {
      delete pendingOptimistic[deviceId];
    }
  }

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
        // PR-10: confirm any pending optimistic flip whose expected
        // state matches the freshly-reported one — this clears the
        // pending entry so the 4s snap-back doesn't fire.
        clearPendingIfMatches(ev.device_id, !!ev.latest_relay_on);
        // 0.6.52 Slice A/B: same for the device-detail hero, and
        // emit the success toast carrying the measured click→confirm
        // latency + which path delivered (agent_ack vs heartbeat).
        if (ev.latest_relay_on !== null && ev.latest_relay_on !== undefined) {
          clearHeroPendingIfMatches(ev.device_id, !!ev.latest_relay_on, ev.source);
        }
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
