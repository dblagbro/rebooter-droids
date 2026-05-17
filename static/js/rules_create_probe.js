/* Rule-create form: probe-kind field visibility + add/remove internet
   target rows.

   Extracted from rules/index.html (v0.5.73): the page CSP is
   `script-src 'self'`, which blocks inline <script>. Loaded with
   `defer`, so it runs after the form's DOM exists. */
(function () {
  const sel = document.getElementById('probe_kind');
  const internetBlock = document.getElementById('probe_internet_block');
  const probeArgLabel = document.getElementById('probe_arg_label');
  const table = document.getElementById('internet_targets_table');
  const addBtn = document.getElementById('internet_add_target');
  if (!sel) return;
  // v0.5.28: per-kind integration probe blocks.
  const integrationBlocks = {
    'roku_app_active': document.getElementById('probe_roku_app_active_block'),
    'ha_state_is': document.getElementById('probe_ha_state_is_block'),
    'weather_alert_active': document.getElementById('probe_weather_alert_active_block'),
    'ical_event_active': document.getElementById('probe_ical_event_active_block'),
  };
  // v0.5.32: power probes share one block; the threshold-vs-near-zero
  // label swaps based on kind.
  const POWER_KINDS = new Set(['power_above', 'power_below', 'power_zero_while_on']);
  const powerBlock = document.getElementById('probe_power_block');
  const powerThresholdLabel = document.getElementById('power_threshold_w_label');
  const powerNearZeroLabel = document.getElementById('power_near_zero_label');
  const PROBE_ARG_KINDS = new Set(['ping', 'tcp', 'http', 'dns']);
  function syncVisibility() {
    const kind = sel.value;
    internetBlock.style.display = (kind === 'internet') ? '' : 'none';
    probeArgLabel.style.display = PROBE_ARG_KINDS.has(kind) ? '' : 'none';
    Object.entries(integrationBlocks).forEach(([k, el]) => {
      if (!el) return;
      el.style.display = (k === kind) ? '' : 'none';
    });
    if (powerBlock) {
      powerBlock.style.display = POWER_KINDS.has(kind) ? '' : 'none';
      if (powerThresholdLabel) powerThresholdLabel.style.display = (kind === 'power_above' || kind === 'power_below') ? '' : 'none';
      if (powerNearZeroLabel) powerNearZeroLabel.style.display = (kind === 'power_zero_while_on') ? '' : 'none';
    }
  }
  sel.addEventListener('change', syncVisibility);
  syncVisibility();
  if (addBtn && table) {
    addBtn.addEventListener('click', function () {
      const tbody = table.querySelector('tbody');
      if (tbody.children.length >= 8) { return; }
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td><input type="text" name="internet_target_host[]" placeholder="host or IP" style="width:100%"></td>' +
        '<td><input type="number" name="internet_target_port[]" min="1" max="65535" style="width:100%" value="53"></td>' +
        '<td><button type="button" class="btn-link" data-remove-row>remove</button></td>';
      tbody.appendChild(tr);
    });
    table.addEventListener('click', function (e) {
      if (e.target && e.target.matches('[data-remove-row]')) {
        const tbody = table.querySelector('tbody');
        if (tbody.children.length > 1) {
          e.target.closest('tr').remove();
        }
      }
    });
  }
})();
