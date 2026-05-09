// rebooter-droids — v0.3.4 bulk-select helper.
//
// Wires up:
//   form[data-bulk-form]                  ← the wrapping <form>
//   input[type=checkbox][data-bulk-master] ← master toggle in <th>
//   input[type=checkbox][data-bulk-row]    ← per-row checkboxes
//   [data-bulk-bar]                        ← sticky action bar
//   [data-bulk-bar-count]                  ← element whose textContent
//                                            is updated to the count
//   button[data-bulk-submit]               ← disabled when 0 selected,
//                                            danger-styled when >20
//
// Progressive enhancement: form posts work without JS — the JS
// adds the live count, master-toggle, and disable-when-empty.

(function () {
  'use strict';

  function each(list, fn) {
    Array.prototype.forEach.call(list, fn);
  }

  function init(form) {
    var rows = form.querySelectorAll('input[type="checkbox"][data-bulk-row]');
    var master = form.querySelector('input[type="checkbox"][data-bulk-master]');
    var bar = form.querySelector('[data-bulk-bar]');
    var counter = form.querySelector('[data-bulk-bar-count]');
    var buttons = form.querySelectorAll('button[data-bulk-submit]');

    function selectedCount() {
      var n = 0;
      each(rows, function (cb) { if (cb.checked) n++; });
      return n;
    }

    function refresh() {
      var n = selectedCount();
      if (counter) counter.textContent = String(n);
      if (bar) bar.classList.toggle('v3-bulk-bar-visible', n > 0);
      each(buttons, function (btn) {
        btn.disabled = n === 0;
        btn.classList.toggle('btn-danger', n > 20);
      });
      if (master) {
        if (n === 0) {
          master.checked = false;
          master.indeterminate = false;
        } else if (n === rows.length) {
          master.checked = true;
          master.indeterminate = false;
        } else {
          master.checked = false;
          master.indeterminate = true;
        }
      }
    }

    each(rows, function (cb) { cb.addEventListener('change', refresh); });

    if (master) {
      master.addEventListener('change', function () {
        each(rows, function (cb) { cb.checked = master.checked; });
        refresh();
      });
    }

    each(buttons, function (btn) {
      btn.addEventListener('click', function (ev) {
        var n = selectedCount();
        if (n === 0) {
          ev.preventDefault();
          return;
        }
        // Confirmation gate: scale prompt to count.
        var verb = btn.getAttribute('data-bulk-verb') || 'delete';
        var noun = btn.getAttribute('data-bulk-noun') || 'item';
        if (n > 20) {
          form.confirmation_level.value = 'typed';
          var typed = window.prompt(
            'Bulk ' + verb + ' ' + n + ' ' + noun + 's. ' +
            'Type "' + verb + '" to confirm:'
          );
          if (typed !== verb) {
            ev.preventDefault();
            return;
          }
          form.confirmation_typed_value.value = typed;
        } else if (n > 5) {
          form.confirmation_level.value = 'simple';
          if (!window.confirm('Bulk ' + verb + ' ' + n + ' ' + noun + 's?')) {
            ev.preventDefault();
            return;
          }
        } else {
          if (!window.confirm('Bulk ' + verb + ' ' + n + ' ' + noun + 's?')) {
            ev.preventDefault();
            return;
          }
        }
      });
    });

    refresh();
  }

  document.addEventListener('DOMContentLoaded', function () {
    each(document.querySelectorAll('form[data-bulk-form]'), init);
  });
})();
