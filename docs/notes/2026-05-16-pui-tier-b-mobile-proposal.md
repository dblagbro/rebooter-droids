# P-UI Tier B — Mobile: research findings + design proposal

2026-05-16 · v0.5.73. **Proposal for operator approval — no code until
signed off.** Addresses Tier B of the defect walkthrough
(`2026-05-16-pui-defect-walkthrough.md`, defects #3–#6).

## 1. What I researched

- **Layout shell** (`templates/layout.html`, 108 lines): one `<header
  class="topbar">` (brand + desktop `.topnav` + `.topbar-actions`) and a
  separate `<nav class="bottomnav">` (6-item mobile tab bar). Clean.
- **CSS** (`static/css/app.css`, one 847-line file): theme tokens, a
  spacing scale, breakpoints at **640 / 768 / 1024 px**. Header comment
  claims "mobile-first … phone ≤640px is the default."
- **Table handling**: three *different* strategies coexist —
  (a) `.table-wrap` → horizontal scroll (`min-width:600px`);
  (b) the Devices list → a **bespoke dual render** (`devices_list.html`
  renders the device loop twice — once as `<table>`, once as
  `.v3-device-card` articles — CSS shows one per breakpoint, ~150 lines
  duplicated); (c) bare `<table>` with no wrapper → clips the viewport.
- **Settings tabs**: `_components/settings_tabs.html` + `.v3-tabs`
  (`overflow-x:auto`). A second tab component, `.v3-tabbar`, exists for
  device-detail. Two components, same job.
- **Touch targets**: `button { min-height:44px }`, `.bottomnav a
  { min-height:56px }` — WCAG 2.5.5 already met.

**Verdict:** the foundation is sound (tokens, breakpoints, touch
targets, a real bottom-tab bar). The problem is **incomplete
execution**, not a bad base — the "mobile-first" claim is only
partly true.

## 2. This is not a new plan — it completes a documented one

`RFC-003-web-ui-redesign.md` §11.3 already specifies the mobile target,
and requirement **C4** makes it a hard rule: *"the portal MUST stay
usable on a 375px-wide phone screen."* RFC-003 §11.3 / phase "P5":

> Bottom-tab nav on mobile · **Tables on mobile collapse to
> row-per-card** · Touch targets ≥ 44×44.

Status of that spec today: bottom-tab nav ✅ · touch targets ✅ ·
**tables→cards ❌ (done for Devices only)**. Tier B = finishing P5.

## 3. The four defects — root cause + proposed fix

### #3 — Cramped mobile header

**Root cause:** `.topbar-actions` (version string · role badge · `@me`
· `Sign out`) has **zero** mobile-specific CSS. At 375px those four
items + the brand overflow the flex row; "Sign out" wraps.

**Proposal:** below 768px, slim the topbar to **brand (left) + a single
account control (right)**:
- Drop the `version` string on mobile — it's not operator-critical and
  is also shown in Settings.
- Collapse `@me` + `Sign out` + the role badge into one compact
  account affordance (a `@me` link to the profile page, which already
  carries Sign-out and role). Net mobile topbar: `Rebooter` · `@me`.
- Primary nav already lives in the bottom bar — the topbar carries no
  nav on mobile, so it can be this lean.

### #4 — Tables overflow / don't reflow  *(the core defect)*

**Root cause:** the three-strategy mess in §1. Only Devices reflows;
Sites / Groups / Pending-adoption / Firmware-deployments / Rules-list /
History either clip or horizontal-scroll.

**Two ways to get every list to "row-per-card" on mobile:**

| Approach | Cost | Notes |
|---|---|---|
| **A. Generalise the Devices bespoke dual-render** | High | Every list template renders its loop twice. ~150 lines duplicated per page; two renderings per page to keep in sync forever. |
| **B. CSS-only responsive-table reflow** *(recommended)* | Low | Keep one `<table>`. Add `data-label="Name"` etc. to each `<td>`. One CSS block at ≤640px makes `tr` a card and each `td` a labelled row. One source of truth per table; ~1 CSS block + a `data-label` per column. |

**Recommendation: B.** It's the standard pattern, far less markup
churn, and no duplicated render to drift. As a *follow-up* (not Tier B)
the Devices page could drop its bespoke dual-render and adopt B too,
deleting ~150 lines — optional cleanup.

### #5 — Fixed bottom-nav overlapping content

**Research correction:** the walkthrough over-flagged this. `main`
already has `padding-bottom: calc(bottomnav-h + space-4)`, and the bulk
bar already sits at `bottom: bottomnav-h`. The "overlap" in the
walkthrough screenshots is a *full-page-screenshot artifact* (fixed
elements composite mid-image). **Downgraded to a spot-check** — verify
no page puts interactive content outside `<main>`; likely no real fix
needed. Minutes, not a work item.

### #6 — Settings 12-tab strip unusable on mobile

**Root cause:** 12 tabs in a horizontal `overflow-x:auto` scroller —
scrollable but with no affordance, and 12 is too many to scroll
comfortably at 375px.

**Proposal:** at ≤640px, render the tab set as a native **`<select>`
jump menu** (the section names as options; changing it navigates).
One change in `_components/settings_tabs.html`. Native select = a
proper mobile control, zero horizontal scroll. Desktop keeps the tab
strip. *(Also fold `.v3-tabbar` and `.v3-tabs` into one component while
here — same job, two implementations.)*

## 4. Decisions needed from you

1. **#4 table strategy** — confirm **B (CSS reflow)** over A (bespoke
   dual-render). My strong recommendation is B.
2. **#3 mobile topbar** — OK to **drop the version string** and
   collapse role-badge/sign-out into the `@me` profile link on mobile?
3. **#6 settings tabs** — OK with a **`<select>` jump menu** on mobile
   (vs. a wrap-to-two-rows alternative)?
4. **Settings IA** — 12 settings sections is itself a lot. Reworking
   that information architecture is **out of Tier B scope** (it's
   Tier C/E territory) — flagging so it's a conscious deferral, not an
   oversight.

## 5. Proposed scope + sequencing

All Tier B work is **CSS + small template edits — no behavior/route
changes**, shippable as one or two versions:

1. **#4 table reflow** — the CSS block + `data-label`s on the list
   tables (Sites, Groups, Pending-adoption, Firmware-deployments,
   Rules-list, History). The largest item; highest user impact.
2. **#3 mobile topbar** — CSS + a small `layout.html` edit.
3. **#6 settings tabs** — `_components/settings_tabs.html` + CSS.
4. **#5** — spot-check only.

Verification: re-run the walkthrough's Playwright capture at 375px
against the changed pages and confirm no overflow / no clipped tables /
lean header. Estimate: ~1 focused session once approved.

**Awaiting sign-off on §4 before writing any code.**
