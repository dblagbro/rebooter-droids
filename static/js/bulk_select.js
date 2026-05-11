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
    // v0.5.3: form-associated elements may live OUTSIDE the form's
    // DOM subtree if they reference it by `form="<id>"` attribute.
    // form.querySelectorAll() only walks descendants; switch to a
    // document-wide query filtered by ownership so we pick up
    // checkboxes/buttons associated via the attribute too. This is
    // what enabled v0.5.3's nested-form-bug fix on /app/devices —
    // the row checkboxes there now sit OUTSIDE the bulk-delete form
    // so per-row upgrade forms above them don't nest.
    function ownedBy(selector) {
      var all = document.querySelectorAll(selector);
      var owned = [];
      for (var i = 0; i < all.length; i++) {
        if (all[i].form === form) owned.push(all[i]);
      }
      return owned;
    }
    function ownedFirst(selector) {
      var owned = ownedBy(selector);
      return owned.length ? owned[0] : null;
    }
    var rows = ownedBy('input[type="checkbox"][data-bulk-row]');
    var master = ownedFirst('input[type="checkbox"][data-bulk-master]');
    // Bulk-bar + counter live inside the form DOM-wise (they're not
    // form-control elements, so the form= attribute doesn't apply to
    // them). Keep the descendant query for those.
    var bar = form.querySelector('[data-bulk-bar]');
    var counter = form.querySelector('[data-bulk-bar-count]');
    var buttons = ownedBy('button[data-bulk-submit]');

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

    // v0.3.5 fix: many list pages render the SAME row in two
    // layouts (desktop table + mobile card) with paired checkboxes
    // sharing the same name+value. Without sync, master-toggle checks
    // both copies, but the user only sees and unchecks one — the
    // hidden pair stays checked and gets submitted. Pair-sync makes
    // toggling one toggle its pair too.
    function syncPairs(cb) {
      if (!cb.value || !cb.name) return;
      // v0.5.3: rows can be outside the form DOM-wise (form=
      // attribute association); filter document-wide siblings by
      // ownership instead of descendant query.
      var allSiblings = document.querySelectorAll(
        'input[type="checkbox"][data-bulk-row][name="'
        + cb.name + '"][value="' + cb.value + '"]'
      );
      each(allSiblings, function (s) {
        if (s !== cb && s.form === form) s.checked = cb.checked;
      });
    }

    each(rows, function (cb) {
      cb.addEventListener('change', function () {
        syncPairs(cb);
        refresh();
      });
    });

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
