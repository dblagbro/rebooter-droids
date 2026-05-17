/* Auto-refresh the Pending Adoption page every 3s so new device
   announcements appear without operator action. Paused when the tab is
   backgrounded, an input/select/textarea is focused, or a <dialog> is
   open — so it never eats operator keystrokes.

   Extracted from pending_adoption.html (v0.5.73): the page CSP is
   `script-src 'self'`, which blocks inline <script>. */
(function () {
  var INTERVAL_MS = 3000;
  function shouldSkip() {
    if (document.hidden) return true;
    var ae = document.activeElement;
    if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.tagName === 'SELECT')) return true;
    // any open <dialog> or confirm prompt — avoid stomping
    if (document.querySelector('dialog[open]')) return true;
    return false;
  }
  setInterval(function () {
    if (!shouldSkip()) {
      window.location.reload();
    }
  }, INTERVAL_MS);
})();
