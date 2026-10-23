---
name: validate-verification
description: Use to execute a verification.md end-to-end, confirm every step works, and fix formatting or code issues it surfaces — before the human runs it.
---

## Objective

Run the phase's `verification.md` exactly as a human would, so it works first time when they
run it. This is the agent self-validating the UAT guide before the terminal human gate.

## This is a sanity layer, NOT the proof

`verification.md` is a human-facing sanity check on top of the deterministic acceptance tests
(`design-acceptance-tests` / `test-designer`). It is **not** the evidence that the feature
works. A single manual run — especially of an LLM-driven flow — is a coin flip: it can pass by
luck and hide a real break (a delegated reply that only sometimes formats correctly, say). So:

- A passing `verification.md` run does **not** license "verified." The proof is a green
  **deterministic automated acceptance test** for the same scenario.
- If a scenario you're validating has no automated test, that is a **coverage gap** — report it
  so `test-designer` adds a row to `coverage.md` and a `test-writer` writes it; do not treat the
  manual pass as sufficient.
- For non-deterministic (LLM-driven) behaviour, run the scenario more than once if you must
  rely on it manually, and prefer pointing at the deterministic test instead.

## Run it verbatim — no substitutions

Run the file's commands **exactly as written**, in order, as a human would. Do NOT:

- abbreviate or reword inputs (e.g. agent `--soul` text) — copy them verbatim;
- swap a command for an equivalent (e.g. a custom WebSocket client instead of `mesh chat`,
  or `mesh sql` instead of a documented CLI command);
- skip a step because it's interactive or awkward to drive.

If a step genuinely cannot be driven as written (e.g. an interactive `mesh chat` prompt),
that is a **defect to surface**, not to work around silently: either the guide must be made
drivable, or you must explicitly report that the step was not executed and why. Silently
substituting a command means the guide is NOT actually validated — the exact failure a human
hits (wrong flag, expired connection, 500) is precisely what a substitute hides.

Start from a **freshly launched** stack (see the token-expiry note in `launch-app`): a
long-running service can pass early and fail later, so a fresh run is what a human gets.

## Capture evidence

For every step, record the exact command and its actual output (or a log/DB excerpt). The
final report must show, step by step, what was run and what came back — not a claim that it
"passed." No evidence for a step = that step is unverified.

## Procedure

1. Bring the stack up **fresh** using the `launch-app` skill (SurrealDB → `db:migrate` → API
   → runner → `mesh` alias). Most first-run failures are a missing migration or an unstarted
   service; failures after ~1h of uptime are usually the expired-connection bug in launch-app.
2. Execute each fenced `bash` step **verbatim**, in order, capturing IDs into shell vars as
   the file does. Record command + output for each.
3. Compare actual output against the file's **Expected results** table.
4. When a step fails, classify the cause:
   - **Formatting** (bad fence, missing newline, unresolved var) → fix `verification.md`.
   - **Guide is wrong** (command/flag/expected value incorrect) → fix `verification.md`.
   - **Code is wrong** (implementation doesn't meet the spec) → do NOT paper over it in the
     guide; report it as a gap for the implementer to fix, then re-run.
5. Re-run until every step passes against its expected result.

## Reporting

- When the whole guide runs clean, state exactly: **"verification.md runs clean end-to-end"**
  — the phrase the `/goal` evaluator looks for.
- If blocked by a code defect, say so clearly and name the failing step so the loop routes it
  back to implementation rather than declaring success.

## Ground rules

You may edit `verification.md` to fix guide-level issues. Do not edit implementation code to
force a pass. Do not commit. The human still runs their own UAT and commits afterward.
