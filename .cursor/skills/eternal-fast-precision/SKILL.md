---
name: eternal-fast-precision
description: Gunakan untuk task coding Eternal Packaging agar Cursor bekerja presisi, cepat, dan hemat token. Batasi scope, hindari audit ulang, gunakan bukti yang sudah ada, tes terarah, fallback ke mode detail jika presisi menurun, dan aktifkan UI Precision Mode otomatis untuk task UI/UX.
---

# Eternal Fast Precision Mode

Always-on safety and execution policy is defined in:
`.cursor/rules/eternal-model-routing.mdc`
This skill does not override that rule.

Procedural add-on for fast-task execution on `prasnda21-tech/eternal-packaging`.

ROUTING — UI PRECISION MODE:
If the task involves UI, UX, layout, visual design, responsive behavior,
forms, tables, modals, drawers, dashboards, PWA, or screenshot-based work,
automatically activate `UI PRECISION MODE — ETERNAL`.
The owner does not need to say "use UI precision mode".

## 1. PRECISION / REUSE PRIOR PROOF

Before changing source:
- understand the exact task;
- reuse prior audit/proof when still relevant;
- do not guess fields, endpoints, files, permissions, status, or business rules;
- if one material fact is unproven, check only that gap.

If a prior audit already proved file, function, endpoint, data source, or guard — use it directly.

Re-audit only when:
- source changed materially;
- CI/test failed;
- new evidence appeared;
- prior result is ambiguous;
- owner requests re-audit.

## 2. DECISION CONTRACT

Prefer a short decision contract over narrating the whole ERP:

```
BUSINESS RULE
- [rule 1]
- [rule 2]

SCOPE
- [file/module]
- focused tests

JANGAN UBAH
- [high-risk adjacent modules]
```

## 3. AUDIT MODE

When source-of-truth is unclear: audit only what the task needs. Do not implement.

Minimum audit output:
- cause
- file/function
- data source
- guard
- side effect
- impacted files
- required tests
- uncertainty

Stop after the report.

## 4. IMPLEMENTATION MODE

After audit is enough: implement minimum scope.

Task should state:
- goal
- exact business rule
- expected files
- frozen areas
- focused tests
- stop gate

Do not paste the full audit report back into the implementation prompt.

## 5. BUILD REUSE

- Frontend build: run only if frontend/build inputs changed; reuse a prior valid build when inputs are unchanged.
- Do not rebuild an artifact that is already valid for the same inputs.
- If a build appears hung: check live process, artifact, and health first — do not blindly restart the build.
- Skip already-PASS focused tests when related source and build inputs are unchanged and no new failure/uncertainty exists.

## 6. OWNER FALLBACK → DETAILED MODE

If the owner says any of:
- kurang presisi / kurang detail / terlalu singkat
- kembalikan seperti sebelumnya / pakai versi lengkap
- hasilnya membingungkan / menyebabkan rework

switch immediately to DETAILED MODE for that task:
- fuller audit
- explicit source-of-truth
- edge cases and side effects
- file impact
- test matrix
- more detailed report

Stay in DETAILED MODE until the owner says fast mode may be used again.

## 7. PR / MERGE FINALIZATION

Keep finalization short:

1. fetch latest `main`
2. race-check
3. semantic overlap check
4. diff review
5. commit
6. push
7. PR
8. CI on exact HEAD
9. stop

Do not re-audit the whole feature during finalization.
Deploy remains manual; use the exact approved merge SHA only when deploy is explicitly authorized.

## 8. DEFAULT TASK TEMPLATE

```
TUGAS
[short goal]

BUSINESS RULE
- [rule 1]
- [rule 2]

SCOPE
- [file/module]

JANGAN UBAH
- [adjacent high-risk modules]

TES
- [focused case]
- [direct regression]
- build only if inputs changed
- git diff --check

STOP
[explicit stop gate, e.g. no commit/push/PR/merge/deploy]

LAPORAN
changed files, test result, risk, status
```

## UI PRECISION MODE — ETERNAL

Automatically activate this mode for any:

- UI
- UX
- layout
- responsive
- visual redesign
- form
- table
- modal
- drawer
- dashboard
- PWA
- screenshot-based task

The purpose is to prevent isolated previews from being approved
when the real Eternal application shell renders differently.

UI Precision Mode does not replace Fast / token-efficient mode.
Remain efficient: inspect only relevant shell/CSS paths, avoid broad repo scans,
reuse existing evidence, and do not repeatedly screenshot unchanged states.
Never skip real-route final validation.

### REAL APP PREVIEW IS MANDATORY

Never approve UI from an isolated component harness alone.

Component-only previews may be used during development,
but they are NOT accepted as final visual evidence.

Final visual validation MUST run inside the real Eternal application shell:

- real router
- actual page route
- current App.css
- actual page/component styles
- sidebar
- top navigation
- workspace tabs
- page wrapper
- real parent containers
- current global styles

The final screenshot must represent the same layout environment
that production will use.

### REAL ROUTE

For every UI task:

identify and report the actual route first.

Examples:

`/settings/design-cetakan`
`/po/{id}`
or the actual route relevant to the task.

Do not substitute a custom component harness as final proof.

### BROWSER VALIDATION

Use a real local browser/local application preview.

Do NOT use production data or perform production writes.

Required desktop validation:

- 1366×768
- 1440×900

When responsive/mobile behavior matters:

- 390×844

Additional viewport may be used only when genuinely useful.

Do not generate excessive screenshots.
Use 2 primary desktop screenshots by default;
add mobile/modal screenshots only when relevant.
Maximum 4 screenshots unless owner explicitly asks for more.

### CSS AUDIT BEFORE IMPLEMENTATION

Before changing UI, inspect the actual rendered layout dependencies.

At minimum audit:

- nearest parent overflow
- height
- max-height
- min-height
- position
- display
- grid/flex rules
- sticky/fixed ancestors
- z-index / stacking context
- App.css global input styles
- App.css global button styles
- page wrapper styles
- component stylesheet
- sidebar width
- top navigation height
- workspace tab height
- modal wrapper
- responsive breakpoints

Do not assume component-local CSS controls the final layout.

Explicitly identify global or parent CSS that can affect the result.

### VISUAL ACCEPTANCE GATES

Before reporting:

`VISUAL PASS`

verify ALL of these:

1. no element overlap
2. no sticky/fixed control covers inputs or content
3. no accidental nested/internal scrolling
4. no horizontal overflow
5. sidebar does not cover page
6. topbar does not cover page
7. workspace tabs do not hide page content
8. modal fits the viewport
9. buttons remain readable and reachable
10. inputs remain readable and reachable
11. 1366×768 is usable
12. 1440×900 is usable
13. mobile/narrow layout wraps correctly when relevant
14. no global CSS regression is visible
15. no clipped dropdown/popover
16. no clipped modal
17. sticky actions reserve safe layout space
18. final design follows the approved visual direction
19. functionality is not hidden by styling
20. page does not require an unnecessary nested scroll area

If ANY gate fails:

DO NOT report PASS.

Fix the issue and validate again.

### STICKY / FIXED CONTROLS

Never introduce a sticky or fixed control without testing it in the full app shell.

Sticky action bars MUST NOT cover:

- inputs
- form fields
- final row
- modal controls
- table content

Sticky bars must reserve enough layout/bottom space.

If safe sticky behavior cannot be guaranteed in the current app shell:

prefer a normal non-overlay action row.

Correctness is more important than keeping an action permanently visible.

### SCROLL POLICY

Avoid nested scrolling for normal Eternal forms.

Preferred:

normal page scroll.

Use an internal scroll only when the feature genuinely requires a bounded pane,
such as an intentionally scrollable table/list/modal.

A normal settings/input form should not gain an internal scrollbar
merely to force it into one viewport.

For the owner's compact preference:

make the layout compact first,
but do not achieve compactness by clipping or nested scrolling.

### SINGLE-VIEWPORT PRINCIPLE

For Eternal input screens:

aim to show as much useful content as possible in one viewport.

Use:

- compact spacing
- grouped controls
- drawers/modals
- concise labels
- sensible columns

Do NOT:

- shrink typography excessively
- hide important controls
- overlap actions
- force an unsafe sticky layer
- distort input heights

"Compact" means efficient hierarchy,
not tiny or cramped UI.

### SCREENSHOT EVIDENCE

Screenshots MUST come from:

the real application route in the real Eternal shell.

Do not use isolated component screenshots as final evidence.

For each final screenshot report:

VIEWPORT:
[...]

ROUTE:
[...]

APP SHELL PRESENT:
YES

SIDEBAR PRESENT:
YES / NOT APPLICABLE

TOPBAR PRESENT:
YES / NOT APPLICABLE

WORKSPACE TABS PRESENT:
YES / NOT APPLICABLE

HORIZONTAL OVERFLOW:
YES / NO

INTERNAL/NESTED SCROLL:
YES / NO

OVERLAY/CLIPPING:
YES / NO

STICKY CONTROL COVERS CONTENT:
YES / NO

VISUAL STATUS:
PASS / FAIL

If any answer represents a defect:
status must be FAIL.

### REFERENCE IMAGE RULE

When owner supplies a visual reference:

treat it as design direction,
unless owner explicitly requires pixel-perfect reproduction.

Compare the real-route implementation to:

- hierarchy
- density
- grouping
- visual priority
- spacing
- control placement
- overall direction

Do not blindly copy:
- sample data
- fake branch names
- fake customer names
- reference-only colors
- different application navigation

Use actual Eternal components and data model.

### PRODUCTION SCREENSHOT RULE

If owner supplies a production screenshot showing a UI problem:

treat the screenshot as evidence of real-shell behavior.

Do not dismiss a production mismatch because an isolated local harness looked correct.

Reproduce the issue in the real local app shell before fixing it.

### VISUAL DEBUGGING

When local isolated preview and production screenshot differ:

do NOT immediately patch arbitrary CSS.

First inspect:

1. actual route wrapper
2. parent container sizes
3. global CSS
4. inherited font/input/button styles
5. sticky/fixed ancestors
6. overflow rules
7. shell dimensions
8. responsive media rules
9. workspace tabs
10. sidebar/topbar offsets

Report the actual cause before widening the CSS change.

### BROWSER / COMPUTER USE

For final UI validation, use the available real-browser/browser-use capability
when available in the Cursor environment.

Use it for:

- viewport sizing
- actual route
- screenshots
- modal checks
- dropdown checks
- scroll checks
- responsive checks

Do not use video.

Do not use production writes.

### UI TEST POLICY

Code tests remain required.

Visual screenshots do NOT replace:

- focused frontend tests
- behavioral tests
- build
- diff check

For UI tasks, completion requires BOTH:

A. code/behavior evidence
B. real-route visual evidence

### UI STOP POINT

Default UI workflow:

AUDIT ACTUAL ROUTE
→ IMPLEMENT
→ RUN FOCUSED TESTS
→ BUILD
→ OPEN REAL LOCAL ROUTE
→ VALIDATE REQUIRED VIEWPORTS
→ CAPTURE MAX 4 SCREENSHOTS
→ REPORT
→ STOP

NO COMMIT
NO PUSH
NO PR
NO MERGE
NO DEPLOY

until the owner approves the screenshots,
unless the owner explicitly requested the finalization step already.

### VISUAL PASS LANGUAGE

Do not say:

"Preview aman"
"Visual PASS"
"Responsive PASS"

based only on code inspection or component harness.

Only say visual PASS after real-route browser validation.

If browser validation was not available:

state:

`VISUAL VALIDATION NOT COMPLETED`

and do not fabricate visual approval.

### NO PRODUCTION VALIDATION WRITES

Never validate UI by mutating production data.

Use:

- local server
- dummy data
- local fixtures
- test account/local mock context

Production screenshot supplied by owner is observational evidence only.
