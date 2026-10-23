---
name: coding
description: Use when coding new features, bugfixes or improvements. Use if you're asked to implement or code or execute.
---

## Ground Rules

When coding, continue until the build is green — the three ground-truth checks all pass:

1. `npm run test`
2. `npm run typecheck`
3. `npx prettier --write --check .`

These three checks are defined once in the `verify-build` skill (the single source of truth).
Use it to run and report them rather than re-listing the commands elsewhere.

## Fixing a user-facing bug

A green build is necessary, not sufficient — this project has shipped "complete" phases whose
primary interaction was broken across several layers because no test drove the real path.

1. **Trace the whole path the human actually uses before you fix anything.** The reported
   symptom ("can't say yes after Alice reconvenes") is usually the tail of a chain that spans
   layers — CLI → WebSocket → stream cursor → runner terminal frame → conversation history.
   Fix every broken link, not just the first one you find.
2. **Ship a regression test that fails without your fix.** Add it through the real seam (see
   `design-acceptance-tests`), then prove it: revert the fix, watch it go red, restore. An
   untested fix to an interactive path is not done.

Don't make any commits, your human will take care of it later.

## STATE.md & TECH_DEBT.md

You have access to `STATE.md`, which tracks project progress and `TECH_DEBT.md`, which tracks known technical debt. Use
these to understand the current state of the project and avoid duplicating work.

## Maintaining a log

- If a relevant fix or improvement is made, mark it as complete in those files
- If something was deferred to a later date, track it in TECH_DEBT.md with an appropriate description and rating.

## Verification Steps

If the phase has a `docs/specs/000x-xxx/verification.md` (`uat: guide`), your human will use it to do user testing of the changes. Ensure that it is kept up
to date.
