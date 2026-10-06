---
name: eternal-fast-precision
description: Gunakan untuk task coding Eternal Packaging agar Cursor bekerja presisi, cepat, dan hemat token. Batasi scope, hindari audit ulang, gunakan bukti yang sudah ada, tes terarah, dan fallback ke mode detail jika presisi menurun.
---

# Eternal Fast Precision Mode

Always-on safety and execution policy is defined in:
`.cursor/rules/eternal-model-routing.mdc`
This skill does not override that rule.

Procedural add-on for fast-task execution on `prasnda21-tech/eternal-packaging`.

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
