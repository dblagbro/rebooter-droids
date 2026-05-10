// CSP-safe confirm-on-submit interceptor.
// v0.4.22 (BUG-049 / CSP tighten): replaces inline onsubmit="return
// confirm(...)" attributes scattered across templates so we can drop
// 'unsafe-inline' from CSP script-src.
//
// Templates that previously had:
//     <form onsubmit="return confirm('Delete X?');">
// now write:
//     <form data-confirm-message="Delete X?">
//
// For the rare case where confirmation should require the operator to
// type the device's display_name (per the device-detail hold-off
// flow), templates can use BOTH:
//     <form data-confirm-message="Hold off device-foo — power drops..."
//           data-confirm-typed-name="device-foo">
// First confirm prompts; on OK, prompts for the typed name; only
// submits if it matches.
//
// Defensive defaults — never blocks submission unless the data
// attribute is present + user clicks Cancel.

(function () {
  function attach() {
    document.querySelectorAll('form[data-confirm-message]').forEach(function (form) {
      if (form.__rdConfirmAttached) return;
      form.__rdConfirmAttached = true;
      form.addEventListener('submit', function (evt) {
        var msg = form.getAttribute('data-confirm-message') || 'Are you sure?';
        if (!window.confirm(msg)) {
          evt.preventDefault();
          return false;
        }
        var typedExpected = form.getAttribute('data-confirm-typed-name');
        if (typedExpected) {
          var typed = window.prompt('Type the name to confirm: ' + typedExpected);
          if (typed !== typedExpected) {
            evt.preventDefault();
            return false;
          }
        }
        return true;
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }
})();
