---
name: verify-build
description: Use to run the project's ground-truth build checks and report pass/fail. The single source of truth for "is the build green".
allowed-tools: Bash(RTK *) Bash(just *)
---

## Objective

Run build checks and report status. This is the single
source of truth referenced by the `coding` skill and the `verifier` subagent.

## The checks

1. `just build`

## Reporting

- With no errors, state exactly: **"build green: test, typecheck, prettier all pass"** —
  this is the phrase the `/goal` evaluator looks for.
- If any fail, report which command failed and paste the relevant failing output so the
  implementer (and the `/goal` evaluator) can see it in the transcript.

## Ground rules

Do not commit. Formatting may reformat files — expected and fine.
