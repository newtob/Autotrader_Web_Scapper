---
name: audit-spec
description: Use to independently audit an implementation against its design.md and report material gaps by severity.
---

## Objective

Act as an independent reviewer. Compare what was actually implemented against the phase's
`design.md` and produce a **severity-rated gap list**. You are the checker — you did not
write this code, and you should not fix it here (that is the implementer's job).

## What counts as a gap

Report only **material** divergences:

- Acceptance criteria or scope items from `design.md` that are missing or incomplete.
- Behaviour that contradicts the spec.
- Missing tests for specced scenarios.
- Correctness / security / data-integrity problems.

**Ignore** cosmetic differences, naming preferences, and anything already listed as Out of
Scope in the spec or deferred in `TECH_DEBT.md`.

## Procedure

1. Read `design.md` and `STATE.md` (which records known spec-vs-implementation divergences).
2. Read the implementation — the files named in `plan.md`'s checklist and their tests.
3. Run the tests if useful to confirm behaviour, but judge against the spec, not vibes.
4. Output a list of findings, each as: **[severity]** short title — what's missing — where.
   Severities: `blocker`, `major`, `minor`.
5. If there are no material gaps, say so explicitly ("no material gaps vs design.md") — this
   is the phrase the `/goal` evaluator looks for.

## Negative-control probe (core rows of `coverage.md`)

The implementation is uncommitted, so `git checkout`/`git restore`/`git stash` would destroy
it. Never use them. For each probe:

1. `cp <file> /tmp/probe-backup-<n>` (one file per probe, never more than one at a time).
2. Neutralise the line or branch the row names (smallest change: return the old value, skip
   the call).
3. `CI=true npx vitest run <the row's test file>` from the owning package. Record red/green.
4. `cp /tmp/probe-backup-<n> <file>` then `cmp <file> /tmp/probe-backup-<n>`. A probe is not
   finished until `cmp` is silent. If restore fails, stop the audit and report it first.

Report each probe as `AS-n: red ✓` or `AS-n: stayed green ✗ (false green)`.

## Ground rules

Read-mostly. Do not edit implementation files, except for the temporary probe above, which must
be restored byte-identical. Do not commit.
