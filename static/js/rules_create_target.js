/* Rule-create form: target picker — kind switch (device/group/tag),
   tag mode swaps the <select> for a free-form text input, and a live
   text filter over device/group options.

   Extracted from rules/index.html (v0.5.73): the page CSP is
   `script-src 'self'`, which blocks inline <script>. Loaded with
   `defer`, so it runs after the form's DOM exists. */
(function () {
  var kindSel = document.getElementById('target_kind');
  var filter  = document.getElementById('target_filter');
  var sel     = document.getElementById('target_id');
  if (!kindSel || !filter || !sel) return;

  var originalSelHtml = sel.innerHTML;

  function rebuild() {
    var kind = kindSel.value;
    // tag mode: replace select with a free-form text input
    // (browsers can't make a <select> typeable; swap nodes).
    if (kind === 'tag') {
      if (sel.tagName !== 'INPUT') {
        var input = document.createElement('input');
        input.type = 'text';
        input.name = 'target_id';
        input.id = 'target_id';
        input.placeholder = 'tag name';
        // v0.5.77 (#15): the edit form pre-fills the existing tag via
        // data-tag-value on the <select>; the create form has none → ''.
        input.value = sel.getAttribute('data-tag-value') || '';
        sel.parentNode.replaceChild(input, sel);
        sel = input;
        filter.style.display = 'none';
      }
      return;
    }
    // restore select if we previously swapped to input
    if (sel.tagName === 'INPUT') {
      var newSel = document.createElement('select');
      newSel.name = 'target_id';
      newSel.id = 'target_id';
      newSel.innerHTML = originalSelHtml;
      sel.parentNode.replaceChild(newSel, sel);
      sel = newSel;
      filter.style.display = '';
    }
    // hide options whose data-kind doesn't match + apply filter text
    var q = (filter.value || '').toLowerCase().trim();
    var optgroups = sel.querySelectorAll('optgroup');
    optgroups.forEach(function (og) {
      var ogKind = og.getAttribute('data-kind');
      var keep = (ogKind === kind);
      og.style.display = keep ? '' : 'none';
      if (!keep) return;
      var anyVisible = false;
      og.querySelectorAll('option').forEach(function (opt) {
        if (!q) { opt.hidden = false; anyVisible = true; return; }
        var hay = (opt.getAttribute('data-name') || '') + ' ' +
                  (opt.getAttribute('data-id') || '') + ' ' +
                  (opt.getAttribute('data-mac') || '');
        var hit = hay.indexOf(q) !== -1;
        opt.hidden = !hit;
        if (hit) anyVisible = true;
      });
      if (!anyVisible) og.style.display = 'none';
    });
  }
  kindSel.addEventListener('change', rebuild);
  filter.addEventListener('input', rebuild);
  rebuild();
})();
