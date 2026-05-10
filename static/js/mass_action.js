// v0.2.5 — mass-action client-side confirmation prompts.
// Server is the source of truth; this just sets the hidden form fields the
// server expects. If JS is disabled, the server still rejects with a clear
// flash message so admins always know they need to retry with confirmation.

// Thresholds must mirror app/services/mass_action.py.
const MA_SIMPLE_THRESHOLD = 5;
const MA_TYPED_THRESHOLD = 20;

function _setHidden(form, name, value) {
    let f = form.querySelector('input[name="' + name + '"]');
    if (!f) {
        f = document.createElement("input");
        f.type = "hidden";
        f.name = name;
        form.appendChild(f);
    }
    f.value = value;
}

function confirmMassAction(form, targetCount, verb) {
    if (targetCount > MA_TYPED_THRESHOLD) {
        const prompt_msg =
            "DANGER: this will affect " + targetCount + " devices.\n\n" +
            "Type the command verb exactly to confirm:\n  " + verb;
        const typed = window.prompt(prompt_msg, "");
        if (typed !== verb) {
            alert("Confirmation did not match. Action cancelled.");
            return false;
        }
        _setHidden(form, "confirmation_level", "typed");
        _setHidden(form, "confirmation_typed_value", typed);
        return true;
    }
    if (targetCount > MA_SIMPLE_THRESHOLD) {
        if (!confirm(
            "This will affect " + targetCount + " devices (" + verb + "). Continue?"
        )) return false;
        _setHidden(form, "confirmation_level", "simple");
        _setHidden(form, "confirmation_typed_value", "");
        return true;
    }
    // Below threshold — no confirmation required server-side.
    _setHidden(form, "confirmation_level", "");
    _setHidden(form, "confirmation_typed_value", "");
    return true;
}

function confirmFirmwareDeploy(form) {
    const targetTypeEl = form.querySelector('select[name="target_type"]');
    const targetIdEl = form.querySelector('select[name="target_id"]');
    const targetType = targetTypeEl ? targetTypeEl.value : "";
    let count = 1;
    if (targetType === "all_devices") {
        const opt = targetTypeEl.options[targetTypeEl.selectedIndex];
        count = parseInt(opt.dataset.count || "0", 10) || 0;
    } else if (targetType === "group" && targetIdEl) {
        const opt = targetIdEl.options[targetIdEl.selectedIndex];
        count = parseInt(opt.dataset.count || "0", 10) || 0;
    } else {
        count = 1;
    }
    return confirmMassAction(form, count, "deploy_firmware");
}

// v0.4.22 (BUG-049): CSP-safe attach via data attributes. Templates
// can now write:
//   <form data-mass-action-verb="relay_cycle" data-mass-action-count="42">
//   <form data-firmware-deploy-confirm>
// instead of inline onsubmit="return confirmMassAction(...)" handlers.
(function () {
    function attach() {
        document.querySelectorAll('form[data-mass-action-verb]').forEach(function (form) {
            if (form.__rdMassActionAttached) return;
            form.__rdMassActionAttached = true;
            form.addEventListener('submit', function (evt) {
                const verb = form.getAttribute('data-mass-action-verb');
                const count = parseInt(form.getAttribute('data-mass-action-count') || '1', 10);
                if (!confirmMassAction(form, count, verb)) {
                    evt.preventDefault();
                }
            });
        });
        document.querySelectorAll('form[data-firmware-deploy-confirm]').forEach(function (form) {
            if (form.__rdFirmwareDeployAttached) return;
            form.__rdFirmwareDeployAttached = true;
            form.addEventListener('submit', function (evt) {
                if (!confirmFirmwareDeploy(form)) {
                    evt.preventDefault();
                }
            });
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attach);
    } else {
        attach();
    }
})();
