---
name: ship-phase
description: Drive a phase from spec through the spec gate, then hand off to a /goal loop for autonomous implementation. Manual-only.
disable-model-invocation: true
argument-hint: <phase-number, e.g. 0011>
---

## What this does

`ship-phase` is the orchestrator for the loop-engineered development workflow. It runs the
pre-gate stages, **stops for human review of the spec**, and then prints the exact `/goal`
command that runs the autonomous stretch. The agent never commits.

Pipeline (models are baked into each subagent, so no manual `/model` switching):

```
spec + simplify   (spec-writer, opus)   -> design.md
   [SPEC GATE: human approves design.md]         <- this skill STOPS here
acceptance tests  (test-designer, opus)  -> deterministic tests, RED first
plan              (planner, opus)        -> plan.md
capture tech debt (implementer, haiku)   -> TECH_DEBT.md
implement         (implementer, haiku)   -> code (make acceptance tests GREEN)
audit vs spec     (spec-auditor, opus)   -> gap list + coverage gaps   \
remediate         (implementer, haiku)   -> code                        }  loop <= 3 rounds
# author UAT        (uat-author, sonnet)            -> verification.md
# PO scenario check (product-owner, opus)           -> realistic?
# validate + run UAT(verifier, haiku)               -> runs clean
verify build      (verifier, haiku)               -> test/typecheck/prettier green
document reality  (documentation-writer, sonnet)  -> README / QUICKSTART / ADRs updated
   [TERMINAL GATE: human UATs by hand, commits]
```

Acceptance-test-first: after the spec gate, `test-designer` writes deterministic tests for
every acceptance scenario in `design.md` (red), and implementation is "done" only when those
tests are green — not when the build compiles or a manual guide ran once.

## Procedure

The phase number is in `$ARGUMENTS` (e.g. `0011`). If empty, read `STATE.md` and pick the
next phase whose Implemented box is `[ ]`, then ask the user to execte that.

1. **Spec + simplify.** Delegate to the `spec-writer` subagent: write `design.md` for the
   phase (it uses `design-spec`), then run the `simplify` pass. Produce
   `docs/specs/<phase>-<slug>/design.md`.
      - [ ] spec-writer
         - [ ] spec-design
      - [ ] simplify
2. **STOP at the spec gate.** Do NOT write code. Summarise the design and the simplifications
   made, then tell the human to review/edit `design.md`.
3. **Print the goal command** for the human to run once they approve the spec — filled in
   with the phase number:

   ```
   /goal phase <PHASE> is complete: plan.md written next to design.md; the test-designer reports "every acceptance scenario in design.md has a passing deterministic automated test" (with a scenario→test coverage map); the spec-auditor reports "no material gaps vs design.md" AND no untested acceptance criteria; the product-owner reports "scenarios are product-realistic"; validate-verification reports "verification.md runs clean end-to-end"; verify-build reports "build green: test, typecheck, prettier all pass"; the documentation-writer reports "documentation updated to match what actually shipped and was verified" — or stop after 3 audit rounds. Do not commit.
   ```

## How the /goal loop behaves

After each turn a separate fast model (Haiku) evaluates the condition against the transcript
and re-runs the turn until it holds (or the 3-round bound is hit). Each turn, delegate the
current stage to the right subagent (planner → implementer → spec-auditor → implementer →
uat-author → product-owner → verifier → documentation-writer). The evaluator only sees what is in
the transcript, so each subagent must **surface its result phrase** (e.g. "build green: …",
"no material gaps vs design.md"). After verification is green, hand the final pass to
`documentation-writer` so README / QUICKSTART / ADR updates describe verified reality, not planned
scope. This is the maker≠checker separation: the implementer never grades itself.

## Ground rules

Only this skill's spec stage writes before the gate. Never commit. Update `STATE.md` when the
phase's terminal gate is reached (mark progress), but leave the commit to the human.
