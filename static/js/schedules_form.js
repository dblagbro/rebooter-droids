/* Schedules create-form — show only the fields relevant to the chosen
   Kind (power_cycle / maintenance) and Recurrence (daily / weekly /
   once), instead of showing every conditional field at once with
   "(X only)" parentheticals (P-UI Tier D #14).

   CSP-safe (no inline handler); loaded with `defer` so the form DOM
   exists. */
(function () {
  const recur = document.querySelector('select[name="recurrence"]');
  const kindRadios = document.querySelectorAll('input[name="kind"]');
  if (!recur || !kindRadios.length) return;

  function kindValue() {
    for (const r of kindRadios) { if (r.checked) return r.value; }
    return '';
  }
  function show(id, on) {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? '' : 'none';
  }
  function sync() {
    const r = recur.value;
    const k = kindValue();
    show('sched-at-time',  r === 'daily' || r === 'weekly');
    show('sched-weekdays', r === 'weekly');
    show('sched-start-at', r === 'once');
    show('sched-duration', k === 'maintenance');
    show('sched-target',   k === 'power_cycle');
  }
  recur.addEventListener('change', sync);
  kindRadios.forEach(function (r) { r.addEventListener('change', sync); });
  sync();
})();
