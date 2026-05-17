# P-UI Defect Walkthrough — 2026-05-16 (v0.5.72)

Heuristic walkthrough of the admin UI per the P-UI charter
(`docs/notes/2026-05-15-pause-state-and-resume-charter.md`). Every page
was loaded at desktop (1280px) and mobile (375px) widths via headless
Chromium; screenshots + console/HTTP errors captured. 14 pages, 28
screenshots.

This is the **defect list** the charter asked for — flat, numbered,
observed (not aspirational). It is **not** a 7th redesign plan. Fix
order is proposed at the end; final priority is the operator's call.

## Defects

### Broken — functional failures

1. **`/app/power` returns HTTP 500.** The entire Power top-nav page is
   down — it renders a raw JSON error blob (`{"error":{"code":
   "internal_error"...}}`) to the user. Root cause: `templates/power.html`
   line 112 — `TypeError: 'builtin_function_or_method' object is not
   iterable` (a `{% for %}` over a dict method reference instead of the
   called method, or the view passing the wrong context value).

2. **CSP blocks inline `<script>` on `/app/rules` (2) and
   `/app/pending-adoption` (1).** Console: *"Executing inline script
   violates Content Security Policy directive 'script-src 'self''"*.
   Those inline scripts do not run. On the Rules page the create-rule
   form's dynamic behaviour (add/remove target rows, probe-kind field
   switching, conditional show/hide) depends on script — at least some
   of it is dead. Fix: extract inline scripts to static files (the
   project's established pattern) or CSP-hash them.

### Mobile — layout breaks at 375px (the operator's chief complaint)

3. **Top header is cramped on mobile.** `Rebooter v0.5.72 [super
   admin] @me Sign out` does not fit 375px — "Sign out" wraps to two
   lines; version + role badge + `@me` are crowded.

4. **Tables overflow the mobile viewport** instead of reflowing:
   Groups, Sites (columns clipped, horizontal scroll, Delete buttons
   off-screen), Pending-adoption, Firmware → Deployments. The Devices
   list *does* reflow to cards — so the behaviour is inconsistent: one
   list reflows, the rest clip.

5. **Fixed bottom tab-bar overlaps content.** The mobile bottom nav is
   `position:fixed`; several pages lack the bottom padding to clear it,
   so it covers content in normal scroll (e.g. the rule-edit JSON
   textarea, Settings body, Schedule form fields).

6. **Settings 12-tab strip is unusable on mobile.** Overview / System /
   Network / … / Profile in one horizontal strip — only ~3 tabs fit
   375px; the rest need horizontal scroll with no affordance.

### Consistency — the app doesn't feel like one app

7. **Page content width is inconsistent.** Status, Devices,
   Device-detail use a narrow centred column (~640px); Pending-adoption,
   Rules, Rule-edit, Groups, Sites use a wide column (~1050–1150px). The
   layout visibly jumps width as you navigate.

8. **Desktop badly underuses horizontal space.** The narrow-column
   pages leave huge empty margins; the Devices data table is squeezed
   into ~640px on a 1280px screen.

9. **Create-forms float narrow fields in wide cards.** Rules,
   Schedules, Groups, Sites, Integrations put ~300px-wide inputs inside
   full-width cards with a large empty expanse to the right — looks
   unfinished.

10. **Breadcrumbs are inconsistent.** Groups and Sites have a
    "← Dashboard" link; Devices, Rules, Schedules, History have none;
    Device-detail has "← Devices". No consistent pattern.

11. **`Delete` buttons on Sites are styled primary (blue).** A
    destructive action looks identical to a primary action. The Rules
    page styles Delete red — so it's also inconsistent.

### Form UX

12. **Schedules "Kind" radio buttons are misaligned** — the radio dot
    floats above/right of its label instead of inline.

13. **Schedules "Weekdays" checkboxes are misaligned** with their
    Mon/Tue/…/Sun labels.

14. **Create-forms show every conditional field at once.** Schedules
    shows "Start at (one-shot only)", "Duration (maintenance only)",
    "Target (power_cycle only)" simultaneously with "(X only)"
    parentheticals instead of showing/hiding by the selected kind.

15. **Rule edit is JSON-textarea only.** Editing a rule means
    hand-editing raw JSON; the page itself says the structured form is
    "queued for a future ship" (the long-deferred Phase 2B).

### Content / polish

16. **Settings → Overview shows internal developer docs to the
    operator.** It references `docs/webui-redesign-plan.md`,
    `docs/redesign-continuation-plan.md`, "P5/P6 placeholder copy in
    v0.3.0", "shipped piecemeal across v0.4.x". Section descriptions are
    dev-speak: "env-var-driven", "TOTP/OIDC queued", "awaiting your
    architecture pick on RFC-004", "(v0.4.10)". Not operator-facing.

17. **Rules page has a "What's coming next" roadmap card** — dev-plan
    content surfaced in the product UI.

18. **Device-detail tab strip doesn't tab.** Overview / Power /
    Watchdog / Audit / Events / Settings looks like tabbed navigation,
    but every section renders stacked on one ~12-card page anyway.

19. **History has no pagination.** It renders the full audit log — many
    hundreds of rows in one scroll (punishing on mobile).

20. **Settings → Integrations is one page of ~10 stacked add-forms** —
    ~14,000px tall on mobile. Needs a pick-a-type flow or collapsible
    sections.

21. **Login page is broken-looking on desktop.** The "Sign in" card is
    sized near-full-width (~1050px) with the fields crammed top-left and
    a huge empty expanse. (Mobile login renders fine — desktop is the
    defect.)

22. **Pending-adoption state pill is malformed** — "awaiting pickup"
    wraps awkwardly inside an undersized rounded shape.

23. **Status "Needs attention" repeats 3 buttons per row.** Snooze 1h /
    Snooze 24h / Ack on every offline device (8 rows) — visually noisy;
    wants a compact or bulk control.

## Proposed fix order

| Tier | Defects | Rationale |
|---|---|---|
| **A — broken** | 1, 2 | The app is literally failing. A dead nav page and CSP-killed form JS outrank everything cosmetic. |
| **B — mobile** | 3, 4, 5, 6 | "Terrible UI" is most visceral on mobile; these are structural breaks, not polish. |
| **C — consistency** | 7, 8, 9, 10, 11 | Makes it feel like one coherent app; #11 also fixes a destructive-action footgun. |
| **D — form UX** | 12, 13, 14, 15 | Real usability wins; #15 is a known deferred feature (Phase 2B). |
| **E — content/polish** | 16–23 | Lower-effort wins; #16/#17 (dev docs in UI) are quick and worth doing early as freebies. |

Each tier is independently shippable. Recommend A first (it's
breakage), then B. Operator picks from here.
