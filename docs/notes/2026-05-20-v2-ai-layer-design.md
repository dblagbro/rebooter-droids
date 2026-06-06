# Rebooter v2 — AI Conversational Configuration Layer

> **Status:** DESIGN PASS — 2026-05-20. Author: D. Blagbrough.
> Scope: the v2 AI "converse-to-configure" layer for the Rebooter Hub
> (`rebooter-droids`, Python/Flask/Postgres/SQLAlchemy). This is a
> **design-only** document — no source modified, nothing committed, no
> release number promised.
> Grounded in branch `consolidate-2026-05-20` (`97eb35f`).
> Companion docs: `docs/notes/2026-05-20-hub-tier2-design.md`,
> `docs/notes/2026-05-20-organization-boundary-design.md`.

---

## 0. What this is, in one paragraph

Rebooter is an ESP8266 smart-plug (Sonoff S31) plus a multi-tenant cloud
hub. The hub already translates plain-language intent into device
configuration: the Tier-2 **setup wizard** (`app/services/setup_wizard.py`)
turns answers like "keep my internet alive" into a `desired_config` blob
plus a watchdog `create_rule()` call. That wizard is a *fixed-questionnaire*
translator. The v2 AI layer is its **conversational evolution**: instead of
a rigid step machine, the user describes what they want in free text, an
LLM proposes a structured change, and the **existing safety machinery**
(validation, preview, human approval, backup, apply, rollback) gates every
write. The AI never touches a relay. It only ever *proposes*.

This document designs only that. It assumes **v1 is already deployed** —
the organization boundary, the Tier-2 setup wizard, hub-side notifications,
and backup/restore are all live.

---

## 1. Locked product decisions this design obeys

These are inputs, not open questions. The design conforms to all of them.

1. **CHAT-FIRST.** A text chat interface, rendered as a hub UI page. Voice
   is **out of scope** — mentioned in §10 only as a v3 possibility, with
   nothing designed for it.
2. **FOCUSED "converse-to-configure".** The AI helps a user configure
   *their own* devices and rules through conversation, with AI review and
   conflict detection. It is **not** an autonomous fleet administrator. It
   never acts on its own and never runs continuously. Autonomous/continuous
   administration is **out of scope** (§10).
3. **v2 ships after v1.** The org boundary, setup wizard, notifications and
   backup/restore are assumed deployed and are *reused*, not rebuilt.
4. **MANDATORY SAFETY MODEL — non-negotiable.** The device cuts **mains
   power** to customer equipment. Every change the AI proposes goes through:
   `propose → diff/preview → explicit human approval → backup → apply →
   rollback-on-failure`. The AI **never** auto-applies. There is no "just
   do it" path. This is enforced server-side (§2.4), not by prompt wording.

---

## 2. Architecture

### 2.1 Where the chat lives

A new hub UI page — **`/app/assistant`** — added as a first-class entry in
the existing `admin_ui_bp` blueprint family, alongside Devices, Rules,
Schedules and Settings. It is **not** a floating widget and **not** a
separate service; it is one server-rendered page plus a small amount of
extracted JavaScript (CSP-clean, no inline JS — same posture as
`theme_flash.js` / `settings_tab_select.js`).

The page has two panes:

- **Conversation pane** (left / top on mobile) — the message thread:
  user turns, assistant turns, and inline **proposal cards**.
- **Proposal/preview pane** (right / expandable on mobile) — when the
  assistant emits a structured proposal, its diff/preview renders here as
  a reviewable card with an explicit **Approve & Apply** button and a
  **Discard** button.

The page reuses the existing 5+1 top-nav / bottom-tab responsive layout
(`layout.html`, already responsive at 640/768px). The mobile-first
single-column pattern from the Tier-2 dashboard pass applies directly.

New blueprint file: `app/blueprints/admin/assistant.py`. New service file:
`app/services/ai_assistant.py` (the orchestrator). New templates under
`templates/assistant/`.

### 2.2 Why a page, not a background service

The locked decision (§1.2) forbids continuous operation. A page that only
runs while a logged-in user is actively typing is the *structural*
guarantee of that: there is no scheduler job, no APScheduler entry, no
queue worker for the AI layer. The model is invoked **only** in the
request thread of an authenticated `/app/assistant` POST. If nobody is
chatting, the AI layer consumes nothing and does nothing. This is
deliberate and load-bearing — it makes "never runs continuously"
impossible to violate by construction, not just by policy.

### 2.3 Conversation flow

```
┌─────────────────────────────────────────────────────────────────┐
│  USER types a request in /app/assistant                          │
│  e.g. "make the office router restart if the net drops for 2 min" │
└───────────────────────────────┬─────────────────────────────────┘
                                │ POST /app/assistant/message
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  ai_assistant.py orchestrator (runs in the request thread)        │
│   1. Load org-scoped context: this org's devices, desired_config, │
│      desired_mode, watchdog rules, schedules, scenes.             │
│   2. Build the Anthropic Messages API call:                       │
│      - system prompt (CACHED): config schema + tool defs +        │
│        safety rules + the user's current fleet/rules snapshot     │
│      - messages: the running conversation transcript              │
│      - tools: the structured proposal tool-use schema (§3)        │
│   3. Call Claude (Anthropic Python SDK).                          │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
              ┌──────────────────────────────────┐
              │  Claude responds. Two shapes:     │
              └───────┬───────────────────┬───────┘
                      │                   │
         plain text   │                   │  tool_use block
         (a question, │                   │  (a structured
          a clarif.,  │                   │   proposal)
          a summary)  │                   │
                      ▼                   ▼
        ┌───────────────────┐   ┌────────────────────────────────┐
        │ Render as an       │   │ 4. Server-side VALIDATION of    │
        │ assistant message. │   │    the tool input against the   │
        │ No write. Loop.    │   │    real schema + create_rule()  │
        └───────────────────┘   │    validators + conflict checks  │
                                │ 5. Build a DIFF (current → new). │
                                │ 6. Render a PROPOSAL CARD.       │
                                │    NOTHING IS WRITTEN YET.       │
                                └───────────────┬────────────────┘
                                                ▼
                          ┌──────────────────────────────────────┐
                          │  HUMAN reviews the diff/preview.       │
                          │  Clicks "Approve & Apply" or "Discard".│
                          └───────────────┬──────────────────────┘
                                          │ Approve
                                          ▼
                          ┌──────────────────────────────────────┐
                          │  APPLY PIPELINE (§2.4):                │
                          │  backup → apply → verify → rollback?   │
                          └──────────────────────────────────────┘
```

Key properties:

- The model emits **either** conversational text **or** a structured
  proposal (tool use), never config that gets executed as free text.
- A proposal is **inert** until a human clicks Approve. The tool-use
  result is *data*, parsed and re-validated by the hub — the model's
  output is never trusted as authoritative.
- Clarifying questions are normal and expected: if the request is
  ambiguous ("restart the router" — which device?), the model asks rather
  than guesses, because the system prompt instructs it to and because it
  only has the org's real device list to choose from.

### 2.4 How an NL request becomes a structured proposed change

The orchestrator (`ai_assistant.py`) is the only component that talks to
Claude. The unit it produces is a **`ProposedChange`** — a structured,
server-validated object, *not* model text:

```
ProposedChange:
  id                  (ulid, stashed server-side)
  org_id              (the requesting user's active org)
  kind                "device_config" | "watchdog_rule" | "schedule"
  operation           "create" | "update" | "delete"
  target_ref          device_id / rule_id / schedule_id (must resolve
                      inside the org)
  payload             the new structured value (a desired_config dict,
                      a create_rule kwargs dict, a schedule dict)
  rationale           model's plain-language explanation (display only)
  conflicts           list of ConflictFinding (§4) — may be empty
  diff                computed current→new field diff (display only)
  status              "proposed" | "approved" | "applied" | "failed"
                      | "discarded" | "rolled_back"
```

The pipeline that turns a model response into a `ProposedChange`:

1. **Tool-use extraction.** The model emits a `tool_use` block (§3). The
   orchestrator reads `tool_use.input` — a JSON object — and nothing else.
   Free-text in the same turn is shown as the rationale only.
2. **Schema validation.** The `payload` is validated *exactly* as a
   non-AI write would be:
   - `device_config` → `device_config.set_desired_config()`'s validation
     path: top-level keys checked against `ALLOWED_DESIRED_CONFIG_KEYS`
     (`device_name`, `relay_restore_behavior`, `monitor_interval_seconds`,
     `boot_warmup_seconds`, `manual_button_enabled`, `internet`, `device`,
     `notifications`, `power`); `device_name` length cap; `desired_mode`
     enum.
   - `watchdog_rule` → `watchdog.create_rule()` / `update_rule()`
     validation: `KNOWN_PROBE_KINDS` membership, `_validate_probe()`
     per-kind required-field checks, `_validate_action()` /
     `_validate_leaf()` action checks, target-kind + identifier checks,
     and the numeric bounds on `failure_threshold` (1–100),
     `recovery_threshold` (1–100), `window_seconds` (5–86400),
     `cooldown_seconds` (0–86400).
   - `schedule` → `schedules.py` validation.
   If validation fails, the proposal is rejected **server-side** and the
   failure is fed back into the conversation as a tool-use error result so
   the model can correct itself. A malformed proposal can never reach the
   approval UI.
3. **Conflict detection** (§4) runs across the org's other rules/schedules.
4. **Diff computation.** For an update, the orchestrator computes a
   field-level diff between the current stored value and the proposed
   `payload`. For `device_config` this reuses the shape of
   `device_config.compute_drift()` (`missing` / `mismatch` / `extra`). For
   rules it renders both the current and proposed
   `watchdog.render_rule_sentence()` plain-English sentences side by side.
5. The `ProposedChange` is stashed (see §2.6) and a proposal card is
   rendered. **No DB write to devices/rules/schedules has occurred.**

### 2.5 The diff/preview UI

The proposal card is the human-readable, decision-grade view of a
`ProposedChange`. It shows:

- **A one-line summary** — e.g. "Create a watchdog rule on *Office
  Router*".
- **The plain-English rule sentence** for rule proposals — reuse
  `watchdog.render_rule_sentence()` verbatim. This is the same sentence
  the Rules list page already shows, so what the user approves in chat is
  identically worded to what they would see in the Rules UI.
- **A field-level diff** for updates — current value struck through, new
  value highlighted, per field.
- **Conflict findings** (§4) — rendered as warnings *above* the Approve
  button; a `block`-severity conflict **disables** Approve entirely.
- **The model's rationale** — clearly labelled as the AI's explanation,
  visually distinct from the validated facts.
- **Two buttons:** `Approve & Apply` (primary; carries a CSRF-safe POST
  with the `ProposedChange.id`) and `Discard`.

The card never shows raw JSON as the primary view. Raw JSON is available
behind an "Advanced" `<details>` — mirroring how `rules/edit.html` keeps a
JSON escape hatch under the structured form.

### 2.6 The approve → backup → apply → rollback pipeline

When the user clicks **Approve & Apply**, `POST /app/assistant/apply`
runs this pipeline **server-side**, in order. The AI is not involved past
this point — it produced the proposal; the pipeline is deterministic hub
code.

1. **Re-authorize.** Confirm the current user still has write authority on
   the target (RBAC `role_required_ui` + the target resolves inside the
   user's active org — §5). Authorization is checked *at apply time*, not
   only at propose time.
2. **Re-validate.** Re-run the §2.4 step-2 validation against the *current*
   DB state. The fleet may have changed since the proposal was generated;
   a stale proposal (target deleted, conflicting rule added) is rejected
   with a clear message and the user is asked to re-state the request.
3. **Backup.** Take a scoped config backup **before** the write, using the
   existing `app/services/config_backup.py` — `export_config()` produces
   the versioned, portable JSON document the Tier-2 backup feature already
   defines (watchdog rules, schedules, scenes, per-device `desired_config`
   keyed by MAC). For v2 the AI apply step exports at minimum the affected
   sections so a rollback has a known-good prior state. The backup blob is
   attached to the `ProposedChange` record.
4. **Apply.** Call the **existing** service — not a new write path:
   - `device_config` → `device_config.set_desired_config()`, then, only
     if the user also approved a push, `push_desired_config(source=
     "manual", issued_by_user_id=<user>)`. Note that auto-push is gated by
     the `desired_config.enabled` runtime flag; the AI layer respects that
     gate exactly as the rest of the hub does. The default is
     stage-the-config, and pushing is a *separate explicit* approval.
   - `watchdog_rule` → `watchdog.create_rule()` / `update_rule()` /
     `delete_rule()`.
   - `schedule` → the `schedules.py` CRUD functions.
5. **Verify.** For a config push, the pipeline does not block on device
   acknowledgement (the device may be offline). It records the enqueued
   `apply_config` command id and surfaces drift status via the existing
   `compute_drift()` on the next heartbeat. For a rule/schedule write,
   verification is the successful service return.
6. **Rollback on failure.** If the apply call raises, or post-apply
   re-validation detects the write produced an invalid state, the pipeline
   restores from the step-3 backup via `config_backup.parse_and_plan()` +
   `apply_plan()` (the existing dry-run-then-apply restore path) limited to
   the affected sections, sets `ProposedChange.status = "rolled_back"`, and
   reports the failure in the conversation. The user equipment is left in
   its pre-proposal configuration.
7. **Audit.** Every step emits `audit.record()` events —
   `ai.proposal_generated`, `ai.proposal_approved`, `ai.change_applied`,
   `ai.change_rolled_back` — with `target_type` / `target_id` and a
   `details` dict carrying the `ProposedChange.id`, the conversation id,
   and the actor. This matches every existing hub mutation handler.

**Blast-radius gate.** If a single AI proposal would fan out to multiple
devices (e.g. a rule targeting a group, or a multi-device scene), the apply
route runs the proposal through the existing `mass_action.validate()`
typed-confirmation gate before step 3: >5 devices needs `simple`
confirmation, >20 needs a `typed` confirmation echoing the verb. The AI is
*never* exempt from the blast-radius gate that a human using the normal UI
would hit. The v2 default posture is that the AI proposes **single-device,
single-rule** changes; multi-device proposals are allowed but always
inherit the typed-confirmation gate.

### 2.7 Conversation state & persistence

Two new lightweight tables (both `TenantScoped` — they carry
`organization_id` and are auto-filtered by `tenant_scope.py`):

- **`ai_conversations`** — `id`, `organization_id`, `created_by_user_id`,
  `title` (first user message, truncated), `created_at`, `updated_at`.
- **`ai_messages`** — `id`, `conversation_id` (FK), `role`
  (`user` / `assistant`), `content` (text), `tool_use` (JSON, nullable —
  the structured proposal if any), `proposed_change_id` (nullable),
  `created_at`.

`ProposedChange` records persist in a third table **`ai_proposed_changes`**
(also `TenantScoped`) so the approve POST can retrieve a proposal by id,
the backup blob can be attached, and the audit trail is complete. A
proposal expires (status auto-`discarded`) after a short TTL or when the
fleet state it was built against changes materially — preventing
stale-approval.

These follow the existing schema-evolution pattern: an Alembic revision
under `migrations/versions/` plus the `ensure_schema()` / `_ensure_columns()`
startup ADD COLUMN safety net.

---

## 3. Claude API integration

### 3.1 SDK and model

- **SDK:** the official **Anthropic Python SDK** (`anthropic`), added to
  `pyproject.toml` `dependencies`. It sits naturally next to the existing
  `requests` / `cryptography` dependencies. The SDK is called only from
  `app/services/ai_assistant.py`.
- **Model recommendation: `claude-sonnet-4-6` (Claude Sonnet 4.6).**

  Justification for Sonnet 4.6 over a larger or smaller model:
  - **The task is structured translate-and-review, not open-ended
    reasoning.** The model's job is: read a bounded config schema, read
    the user's current fleet state, and emit one well-formed tool call.
    Sonnet 4.6 is strong at exactly this — instruction-following, schema
    adherence, and tool use — without the latency and cost of an Opus-tier
    model.
  - **Latency matters.** The model runs synchronously in a request thread
    of an interactive chat page (§2.2). Sonnet-tier latency keeps the
    conversation responsive; an Opus-tier model would make every turn feel
    sluggish.
  - **The hub, not the model, is the safety authority.** Every proposal is
    re-validated server-side against the real validators (§2.4) and gated
    by human approval (§2.6). The model does not need to be the smartest
    possible model because it is structurally prevented from causing harm;
    it needs to be *reliable at structured output*, which Sonnet 4.6 is.
  - **Cost.** This is a per-seat SaaS feature that may run many short
    conversations. Sonnet pricing plus prompt caching (§3.4) keeps
    per-conversation cost predictable.

  The model id is stored in a `runtime_settings` key
  (`ai.model`, default `claude-sonnet-4-6`) so the operator can pin or
  upgrade the model without a redeploy — the same DB-backed-setting pattern
  the hub already uses everywhere. Haiku-tier is a fallback option for
  cost-sensitive self-hosters; Opus-tier is available for operators who
  want it, but Sonnet 4.6 is the recommended default.

- **API key** lives in a `runtime_settings` secret key (`ai.api_key`),
  stored write-only / masked exactly like the SMTP password and webhook
  channel secrets (the Tier-2 `********` = unchanged, blank = clear
  pattern). It is redacted from the Tier-2 backup export. If no key is
  configured the `/app/assistant` page renders a "not configured" state
  and the feature is inert.

### 3.2 Tool use / function calling — the structured-output contract

The model is **never** allowed to emit config as free text that the hub
then parses heuristically. It must call one of a small set of **tools**.
The tool input schema *is* the contract; the SDK enforces that the model's
`tool_use.input` conforms to the declared JSON schema before the
orchestrator ever sees it, and the hub then re-validates against the real
service validators (§2.4).

Proposed tools (defined in `ai_assistant.py`):

- **`propose_device_config`** — input: `device_id`, `desired_mode`
  (optional, the `smart_plug` / `internet_watchdog` / `device_watchdog`
  enum), `desired_config` (object whose top-level keys are constrained to
  `ALLOWED_DESIRED_CONFIG_KEYS`), `rationale`. The JSON schema for
  `desired_config` is generated directly from `ALLOWED_DESIRED_CONFIG_KEYS`
  and the firmware apply-config schema doc, so the model is told the exact
  allowed shape up front.
- **`propose_watchdog_rule`** — input mirrors `create_rule()` kwargs:
  `operation` (`create`/`update`/`delete`), `rule_id` (for update/delete),
  `name`, `probe` (with `kind` constrained to `KNOWN_PROBE_KINDS` and
  per-kind fields), `target`, `action` (`kind` constrained to
  `KNOWN_ACTION_KINDS`), `failure_threshold`, `recovery_threshold`,
  `window_seconds`, `cooldown_seconds`, `rationale`. The enums and numeric
  bounds in the tool schema are lifted straight from `watchdog.py` and
  `watchdog/__init__` constants.
- **`propose_schedule`** — input mirrors the `schedules.py` create/update
  shape (`kind`, `recurrence`, `at_time_utc`, `weekdays`, `target`,
  power-cycle timers).
- **`ask_clarifying_question`** — input: `question` text. Used when the
  request is ambiguous; produces no proposal, just an assistant turn.
- **`request_more_context`** — input: a narrow request like "show me the
  current rules for device X". The orchestrator answers from the
  already-loaded org context (no second model call needed); this exists so
  the model has an explicit channel rather than hallucinating.

The tool definitions are the single source of truth for what the AI can
propose. Adding a new device-config key or probe kind means regenerating
the tool schema from the same constants the validators use — there is one
schema, not a drifting copy.

Because every proposal is a tool call with a typed schema, and because the
hub re-validates with the production validators, **there is no code path
where model free-text becomes an executed configuration change.** This is
the structural answer to "never free-form text that gets executed."

### 3.3 Grounding the model with real schema and real fleet state

The model is given two kinds of ground truth so it proposes *valid,
relevant* changes rather than plausible-looking nonsense:

1. **The schema** — the device-config allowed keys and per-key support
   tier (verified vs. accepted), the watchdog probe/action kind registries
   and their per-kind required fields and numeric bounds, the schedule
   shape, and the three `desired_mode` values. This is essentially the
   `setup_wizard.py` translation knowledge, expressed declaratively for
   the model. Crucially the model is told which keys are *firmware-verified
   end-to-end* (`device_name`) versus *accepted but unverified* — so it can
   tell the user "older firmware may ignore this," exactly as the Tier-2
   setup wizard copy already does.
2. **The user's current fleet/rules state** — a snapshot of *this org's*
   devices (id, display name, `desired_mode`, online status), their current
   `desired_config`, all watchdog rules (rendered both as structured JSON
   and as the plain-English `render_rule_sentence()`), schedules and
   scenes. This snapshot is org-scoped (§5) and is what lets the model
   resolve "the office router" to a real `device_id` and detect that a
   proposed rule overlaps an existing one.

Both go into the **system prompt** (next section), so the model always
reasons against the real, current, tenant-specific state — never a generic
notion of "a Rebooter device."

### 3.4 Prompt caching

The system prompt carries the large, stable-within-a-conversation context:
the device-config schema, the tool definitions, the safety rules, and the
user's current fleet/rules snapshot. This is **marked for prompt caching**
via the Anthropic SDK's cache-control breakpoints.

- **Cache structure:** the system prompt is split so the *truly static*
  part (schema + tool docs + safety rules) and the *per-conversation* part
  (this org's fleet snapshot) are each cached. Across the many turns of a
  single conversation, the schema/tooling block is identical and the fleet
  snapshot is identical unless a change was applied — so every turn after
  the first is a cache hit on that large prefix.
- **Cache invalidation:** the fleet-snapshot cache segment is rebuilt (and
  thus re-cached) only when an AI proposal is applied, or when the
  conversation is older than the cache TTL. A turn that is just a
  clarifying exchange reuses the cached snapshot fully.
- **Why it matters here:** conversations are multi-turn (clarify →
  propose → revise → approve). Without caching, the full schema + fleet
  snapshot would be re-billed and re-processed every turn. With caching,
  the per-turn cost and latency drop to roughly the incremental
  conversation transcript plus the response. This is a material cost and
  responsiveness win for an interactive feature.

The orchestrator logs cache-hit metrics (read vs. write input tokens) so
the operator can see caching is effective.

### 3.5 What is NOT sent to the model

- No other org's data (§5).
- No device credentials, enrollment tokens, API tokens, user passwords or
  OAuth identities — these are not configuration and have no business in a
  configuration conversation. The fleet snapshot is built from the same
  in-scope set the Tier-2 backup export defines (config, not secrets, not
  operational data).
- No raw audit log or heartbeat history. If a conversation needs "has this
  device been flapping?", that is a future enhancement with an explicit,
  bounded summary — not a data dump.

---

## 4. Conflict detection

Conflict detection is the "AI review" half of "converse-to-configure." It
runs **server-side**, after schema validation and before the proposal card
is rendered (§2.4 step 3). It is deterministic hub code — not the model's
judgement — so its findings are trustworthy.

### 4.1 What it extends

The existing `watchdog.create_rule()` validation is **single-rule,
structural**: it checks that *one* rule is well-formed (probe kind known,
required fields present, numeric bounds). It has **no cross-rule semantic
awareness** — today you can create two rules that fight each other and
`create_rule()` accepts both.

The v2 conflict detector adds a **cross-rule, cross-schedule, semantic**
layer *on top of* `create_rule()`'s structural validation. It does not
replace it. The proposed home is a new function in `app/services/watchdog.py`
(or a sibling `app/services/rule_conflicts.py`):

```
detect_conflicts(proposed_change, *, org_id) -> list[ConflictFinding]
```

called by the orchestrator and — recommended — *also* wired into the
non-AI `create_rule()` / `update_rule()` path as a **warning surface** so
the structured Rules form benefits from the same checks. (Whether the
non-AI path *blocks* on a conflict or merely warns is open question Q4.)

`ConflictFinding`: `severity` (`block` / `warn` / `info`), `kind`, a
plain-language `message`, and the ids of the conflicting rules/schedules.

### 4.2 The semantic checks (v2 set)

For a given device (and its group memberships), across the org's rules and
schedules:

1. **Contradictory actions on the same target.** A device targeted by one
   rule with a `hold_off` action (power off until manually restored) and
   another with a `cycle` or `relay_on` action — the two fight. The
   detector resolves each rule's target to its device set (reusing
   `watchdog_runtime.resolve_target_devices()`, the same resolver
   `list_rules_for_device()` already uses) and flags any device that is the
   target of mutually-exclusive action kinds. **Severity: `block`** — this
   is the dangerous case; mains power would oscillate or stick.
2. **Watchdog vs. schedule fighting.** A `power_cycle` schedule on a device
   that also has a watchdog rule whose action turns it on/keeps it on, or
   a schedule that turns a device off while a watchdog rule is trying to
   keep it up. **Severity: `block` or `warn`** depending on overlap.
3. **Overlapping schedules.** Two schedules on the same device whose
   time windows overlap with conflicting effects (one `power_cycle`, one
   `maintenance`, or two power actions at the same time). Reuses the
   `schedules.py` next-run / recurrence computation to detect window
   overlap. **Severity: `warn`.**
4. **Duplicate / redundant rule.** A proposed rule whose probe + target +
   action is functionally identical to an existing one — likely the user
   forgot they already set it up, or the AI is re-proposing. **Severity:
   `info`.** Note the setup wizard already has a *narrow* version of this
   (`find_prior_wizard_rule()` matches by name + the `RULE_MARKER`
   description); the v2 detector generalizes it to semantic equivalence,
   not just the wizard's own marker.
5. **Self-defeating thresholds.** A rule whose `cooldown_seconds` is
   shorter than the device's `post_reboot_holdoff_seconds`, or a
   `failure_threshold` × `window_seconds` so short the device cannot
   complete a reboot before the next probe fails again — a power-cycle
   loop. **Severity: `warn`.** This catches the mains-oscillation footgun
   that structural validation misses.
6. **Mode/rule mismatch.** A device whose `desired_mode` is `smart_plug`
   (the wizard explicitly generates *no* watchdog rule for that mode) but
   which a proposal would attach a watchdog rule to — flag it so the user
   confirms they meant to change the device's role. **Severity: `warn`.**

A `block`-severity finding **disables the Approve button** on the proposal
card (§2.5). `warn` and `info` are shown but do not block — the human
decides. The findings are also fed back to the model as a tool-result so a
follow-up turn ("there's a conflict — here's a revised proposal") is
natural.

### 4.3 Why this is conflict *detection*, not resolution

The detector reports; it does not silently "fix." Auto-resolving a
conflict would mean the AI mutating the user's other rules without an
explicit proposal+approval for *that* change — which violates §1.4. If
resolving a conflict requires changing a second rule, that is a *second*
`ProposedChange` with its own diff and its own Approve button.

---

## 5. Multi-tenancy & security

### 5.1 The org boundary is the hard wall

v1 shipped the organization boundary (`Organization`,
`OrganizationMembership`, `tenant_scope.py`). The AI layer sits **entirely
inside** it and adds no bypass.

- The three new tables (`ai_conversations`, `ai_messages`,
  `ai_proposed_changes`) all mix in **`TenantScoped`** — they carry
  `organization_id`, are auto-filtered by the `do_orm_execute` read filter,
  and are auto-stamped by the `before_flush` write event. A user can only
  ever see and act on AI conversations and proposals in their **active
  org**.
- The orchestrator runs inside the request's tenant context — the
  `_current_org` ContextVar is already `set_org()`-bound by the existing
  auth middleware before `/app/assistant` handlers run. The fleet snapshot
  (§3.3) is built with **ordinary ORM queries**, which the tenant read
  filter already scopes to the active org. There is **no `system()`
  bypass** anywhere in the AI layer — the AI code never enters the
  enforcement-bypass context. It is structurally incapable of reading
  another org's devices or rules because it uses the same filtered session
  every other tenant-scoped feature uses.
- A user who belongs to multiple orgs (e.g. an MSP — `OrganizationMembership`
  is M:N by design) gets a *separate* conversation history and *separate*
  proposals per org; switching active org switches the whole AI context.

### 5.2 What fleet data enters the prompt — and the privacy implications

The fleet snapshot sent to Claude contains, **for the active org only**:
device ids and display names, `desired_mode`, online/heartbeat status,
each device's `desired_config`, all watchdog rules (structured + the
plain-English sentence), schedules, scenes, and group/site names.

Privacy implications and the mitigations:

- **It leaves the hub.** This data is transmitted to the Anthropic API.
  That is a new data-egress path and must be disclosed: the
  `/app/assistant` page carries a clear notice ("Your device names and
  configuration for this organization are sent to Anthropic's API to power
  this assistant"), and it should be covered in the product privacy policy
  and, for Enterprise customers, the DPA. Anthropic's API
  data-retention/non-training terms for API traffic are the relevant
  contractual basis — the operator-facing docs should state this.
- **Per-org opt-in.** The AI assistant is **off by default per org**. An
  org `owner`/`admin` (the `ORG_ROLE_OWNER` / `ORG_ROLE_ADMIN` tier on the
  membership row) must explicitly enable it. Self-hosters and
  privacy-sensitive tenants can leave it off entirely; the rest of the hub
  is unaffected.
- **Minimization.** Only configuration data is sent — never device
  credentials, enrollment tokens, API tokens, user PII beyond display
  names, audit logs, heartbeat payloads or power telemetry (§3.5). Display
  names *can* be sensitive (a customer might name a device after a person
  or a location); the privacy notice should say so, and a future option
  could let an org redact display names to opaque ids in the prompt.
- **No cross-org leakage by construction.** Because the snapshot is built
  from tenant-filtered queries (§5.1), it is impossible for org A's
  conversation to ever include org B's data — there is no query in the AI
  layer that is not org-scoped.

### 5.3 Authorization on apply

- **Authentication:** `/app/assistant` and all its POST routes require an
  authenticated session — `role_required_ui` on the blueprint, same as
  every `/app/*` page.
- **Authorization to propose vs. to apply are separate.** Generating a
  proposal (a read + an API call) requires read access. **Applying** a
  proposal requires *write* authority on the specific target, checked with
  the existing RBAC `role_required_ui` roles and the per-target
  org-membership check — and it is re-checked **at apply time** (§2.6
  step 1), not only when the chat page loaded. A viewer-role user can have
  a conversation and see proposals but cannot click Approve & Apply to a
  successful write.
- **Blast-radius gate** (§2.6) — multi-device proposals inherit the
  `mass_action` typed-confirmation gate.
- **API-key handling** — the Anthropic API key is a `runtime_settings`
  secret, masked, never echoed, redacted from backups (§3.1).
- **Audit** — every propose/approve/apply/rollback is an `audit.record()`
  event (§2.6 step 7); the audit log is the tamper-evident record that a
  *human* approved each mains-power-affecting change.
- **Rate limiting** — `/app/assistant/message` is rate-limited via the
  existing `Flask-Limiter` integration, per user and per org, to bound
  both abuse and API cost.
- **Prompt-injection awareness** — a device `display_name` or a rule
  `description` is user-controlled text that enters the prompt. The
  mitigation is *not* prompt wording: it is that (a) the model can only
  ever emit a structured tool call, (b) every tool call is re-validated by
  the hub against the real validators, and (c) nothing applies without a
  human clicking Approve. Even a maliciously-named device cannot cause an
  unapproved or invalid change. This defense-in-depth is the whole point of
  the §2.4/§2.6 design.

---

## 6. Explicitly OUT OF SCOPE

These are stated so there is no ambiguity. Nothing in this design
addresses them and nothing should be built for them in v2.

1. **Voice.** The interface is text chat only. No speech-to-text, no
   text-to-speech, no telephony, no voice-assistant integration. Voice is
   noted as a *possible v3 direction* in §10 and is designed for **not at
   all** here.
2. **Autonomous / continuous administration.** The AI never acts on its
   own. It does not run on a schedule, has no background job, no
   APScheduler entry, no queue worker (§2.2). It does not monitor the
   fleet, does not "notice" problems, and does not propose changes
   unprompted. It runs only inside the request thread of an interactive
   user message and only ever *proposes* — a human approves every change.
   An "AI fleet administrator" or "self-healing fleet" is explicitly **not
   this product** and is out of scope.
3. **AI auto-apply / "just do it."** There is no path, flag, or setting
   that lets the AI apply a change without the explicit human-approval step
   (§1.4, §2.6). Not even for "trivial" changes. This is non-negotiable.
4. **AI editing other orgs' data, or any cross-tenant operation** (§5).
5. **AI managing non-configuration domains** — users, RBAC role bindings,
   billing, firmware deployment, enrollment. v2 is scoped to
   device-config, watchdog rules and schedules only.

---

## 7. Phased implementation plan

Sequenced so the safety machinery exists before the model is ever wired in.

**Phase A — Foundations (no model calls yet).**
- New tables `ai_conversations`, `ai_messages`, `ai_proposed_changes`
  (`TenantScoped`); Alembic revision + `ensure_schema()` parity.
- `ProposedChange` object, validation re-use layer (wrap
  `set_desired_config` / `create_rule` validators), diff computation.
- `app/services/config_backup.py` integration helpers for a
  scoped-section export/restore used by the apply pipeline.
- Unit tests: validation re-use produces identical errors to the direct
  service calls; diff computation; backup/rollback round-trip.

**Phase B — Conflict detection.**
- `detect_conflicts()` with the §4.2 check set; `ConflictFinding`.
- Wire it into the proposal pipeline; optionally surface it as a warning
  in the existing non-AI Rules form.
- Heavy unit tests — contradictory actions, watchdog-vs-schedule,
  overlapping schedules, cooldown-vs-holdoff loops.

**Phase C — Claude integration.**
- Add `anthropic` to dependencies; `ai.model` / `ai.api_key`
  `runtime_settings` keys (key masked, redacted from backup).
- `ai_assistant.py` orchestrator: system-prompt builder (schema + tools +
  safety rules + fleet snapshot), prompt-caching breakpoints, the tool
  definitions, the Messages API call, tool-use extraction.
- Tool-use → `ProposedChange` → validation → conflict detection wiring.

**Phase D — The UI.**
- `app/blueprints/admin/assistant.py` blueprint; `/app/assistant`,
  `/app/assistant/message`, `/app/assistant/apply` routes.
- `templates/assistant/` — conversation pane, proposal card, diff/preview,
  Advanced-JSON `<details>`. Mobile-first, CSP-clean extracted JS.
- Per-org enable toggle (org `owner`/`admin`); the privacy notice.
- Wire the apply pipeline: re-auth → re-validate → `mass_action` gate →
  backup → apply → verify → rollback → audit.

**Phase E — Hardening & rollout.**
- Rate limiting on `/app/assistant/message`.
- End-to-end tests: ambiguous request → clarifying question; valid
  request → proposal → approve → apply; malformed model output rejected;
  apply failure → rollback; stale-proposal rejection; conflict blocks
  Approve; cross-org isolation (a user in org A never sees org B).
- Ship behind a hub-wide feature flag *and* the per-org opt-in; enable for
  a pilot org first.

---

## 8. Open questions for the product owner

1. **Model tier default.** Recommendation is **`claude-sonnet-4-6`** for
   the cost/latency/reliability balance (§3.1). Confirm — or do you want
   Haiku-tier as the shipped default for cost, with Sonnet as an upgrade?
2. **Per-org opt-in vs. on-by-default.** The design defaults the assistant
   **off per org** (§5.2) given the new data-egress to Anthropic. Confirm
   that is the right posture, or should it be on-by-default for paid plans?
3. **Push vs. stage on apply.** The hub already separates *staging*
   `desired_config` from *pushing* it to the device (auto-push is gated by
   `desired_config.enabled`). Should an approved AI config change (a)
   always only stage, requiring a separate manual push, or (b) be allowed
   to push immediately as a second explicit approval in the same card?
   The design currently does (b) but defaults to staging.
4. **Conflict detection on the non-AI path.** Should `detect_conflicts()`
   also *block* (not just warn) the existing structured Rules form, or
   stay AI-only as a blocker and warn-only on the manual form? Making it a
   shared blocker improves safety hub-wide but changes existing UX.
5. **Multi-device proposals.** v2 defaults to single-device, single-rule
   proposals; multi-device proposals are allowed but inherit the
   `mass_action` typed-confirmation gate. Should v2 *forbid* multi-device
   AI proposals entirely and revisit in a later release?
6. **Conversation retention.** How long should `ai_conversations` /
   `ai_messages` be retained — indefinitely, or pruned (the hub already
   has an `audit_prune` service the pattern could follow)? Conversations
   contain device names and config; retention has a privacy dimension.
7. **Anthropic data terms / DPA.** Confirm the operator-facing privacy
   policy and Enterprise DPA language for the new egress path (§5.2), and
   whether an org-level "redact display names in prompts" option is wanted
   for v2 or deferred.
8. **Cost controls.** What per-org rate limit / monthly budget should bound
   `/app/assistant/message`? Should there be a visible per-org usage meter?

---

## 9. How this maps onto existing code (summary table)

| Need | Existing thing reused | New thing |
|---|---|---|
| NL → structured config | `setup_wizard.py` (fixed questionnaire) | `ai_assistant.py` (conversational) |
| Config schema / allowed keys | `ALLOWED_DESIRED_CONFIG_KEYS`, `set_desired_config()` validation | tool JSON schema generated from it |
| Rule validation | `create_rule()` / `_validate_probe()` / `_validate_action()` | re-used as the proposal validator |
| Plain-English rule view | `render_rule_sentence()` | shown verbatim in the proposal card |
| Drift / diff | `compute_drift()` shape | per-field diff in the preview |
| Blast-radius gate | `mass_action.validate()` | applied to multi-device AI proposals |
| Backup before write / rollback | `config_backup.export_config()` / `parse_and_plan()` / `apply_plan()` | scoped-section export in the apply pipeline |
| Tenant isolation | `TenantScoped` + `tenant_scope.py` filters | 3 new `TenantScoped` tables |
| Authz | `role_required_ui`, RBAC, org membership | re-auth at apply time |
| Audit | `audit.record()` | `ai.*` event names |

The AI layer is deliberately a **thin orchestrator over existing
services**. It introduces a conversation surface and a model call; it does
**not** introduce a new way to write to devices, rules or schedules. Every
mains-power-affecting write still goes through the exact same validated,
audited service functions a human using the normal UI would hit — now with
an added, mandatory propose/diff/approve/backup/rollback wrapper.
