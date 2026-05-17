/* User detail — role-binding form: show/hide the site/group/device
   resource picker based on the selected scope_type, and enable/require
   only the visible select.

   Extracted from user_detail.html (v0.5.73): the page CSP is
   `script-src 'self'`, which blocks inline <script>. Loaded with
   `defer`, so it runs after the form's DOM exists. */
(function () {
  const scopeType = document.getElementById('scope_type');
  const sitePicker = document.getElementById('site_picker');
  const groupPicker = document.getElementById('group_picker');
  const devicePicker = document.getElementById('device_picker');
  if (!scopeType || !sitePicker || !groupPicker || !devicePicker) return;

  function updatePickers() {
    const selected = scopeType.value;

    // Hide all and disable their selects
    [sitePicker, groupPicker, devicePicker].forEach(picker => {
      picker.style.display = 'none';
      const select = picker.querySelector('select');
      if (select) {
        select.disabled = true;
        select.removeAttribute('required');
      }
    });

    // Show and enable the selected one
    if (selected === 'site') {
      sitePicker.style.display = 'block';
      const select = sitePicker.querySelector('select');
      select.disabled = false;
      select.required = true;
    } else if (selected === 'group') {
      groupPicker.style.display = 'block';
      const select = groupPicker.querySelector('select');
      select.disabled = false;
      select.required = true;
    } else if (selected === 'device') {
      devicePicker.style.display = 'block';
      const select = devicePicker.querySelector('select');
      select.disabled = false;
      select.required = true;
    }
  }

  scopeType.addEventListener('change', updatePickers);
  updatePickers();
})();
