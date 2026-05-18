/* Rule-create form: action-kind field visibility — v0.5.92 (Stage C).

   Shows the field block matching the selected action_kind:
   - cycle        → power_off / holdoff fields
   - apply_scene  → a saved-scene picker
   - binding      → two saved-scene pickers (active / cleared)
   hold_off / relay_on / relay_off / notify_only carry no fields.

   The page CSP is `script-src 'self'` (no inline <script>); loaded
   with `defer`, so it runs after the form DOM exists. */
(function () {
  const sel = document.getElementById('action_kind');
  if (!sel) return;
  const blocks = {
    'cycle': document.getElementById('action_cycle_block'),
    'apply_scene': document.getElementById('action_apply_scene_block'),
    'binding': document.getElementById('action_binding_block'),
  };
  function sync() {
    const kind = sel.value;
    Object.entries(blocks).forEach(function (entry) {
      const el = entry[1];
      if (el) el.style.display = (entry[0] === kind) ? '' : 'none';
    });
  }
  sel.addEventListener('change', sync);
  sync();
})();
