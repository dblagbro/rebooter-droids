// Theme-flash mitigation. Extracted from layout.html inline <script>
// in v0.4.22 (BUG-049 / CSP tighten). Runs synchronously in <head>
// before <body> renders so that a returning user with a non-system
// theme cookie doesn't see a flash of the system theme.
//
// Reads `rebooter_theme` cookie; falls back to legacy `theme` for
// users upgrading from v0.3.0–0.3.2.
(function () {
  try {
    var cookies = document.cookie.split('; ');
    var lookup = function (name) {
      var hit = cookies.find(function (r) { return r.startsWith(name + '='); });
      return hit ? hit.split('=')[1] : null;
    };
    var t = lookup('rebooter_theme') || lookup('theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
})();
