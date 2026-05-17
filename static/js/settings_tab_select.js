/* Settings section jump-menu — the <select> is the phone-breakpoint
   alternative to the .v3-tabs strip (P-UI Tier B #6: 12 tabs are
   unusable scrolled at 375px). Navigates on change. CSP-safe — no
   inline onchange. */
(function () {
  document.querySelectorAll('select.v3-tabs-select').forEach(function (sel) {
    sel.addEventListener('change', function () {
      if (this.value) window.location.href = this.value;
    });
  });
})();
