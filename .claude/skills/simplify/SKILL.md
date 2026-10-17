---
name: simplify
description: Use as a QC pass on a freshly written design.md to strip over-engineering and premature complexity before implementation.
---

## Objective

Review a `design.md` (usually just produced by the `design-spec` skill) and make it
**simpler**. A first pass often includes too much technical detail for the current maturity
of the project. Your job is to cut, not to add.

## What to cut

- Abstractions, interfaces, and config knobs with only one caller or no near-term second use.
- Infrastructure the phase doesn't strictly need yet (queues, caches, brokers) when an
  in-process approach would prove the same behaviour. See `STATE.md` for what is actually
  wired vs. specced (e.g. Redis is specced but NOT wired — don't add more of that).
- Speculative extensibility ("we might later…") — move it to **Out of Scope** or `TECH_DEBT.md`.
- Premature performance/scale work not justified by the current phase.

## What to preserve

- The phase's actual scope and acceptance criteria.
- Correctness, security, and data-integrity requirements.
- Anything genuinely load-bearing for later phases (note it, don't delete it).

## Procedure

1. Read the `design.md` and `STATE.md`.
2. For each section, ask: _does this phase need this now, or is it intent for later?_
3. Rewrite `design.md` in place, moving deferred items to **Out of Scope** (and, if they are
   real future work, note them for `capture-tech-debt`).
4. End with a short "Simplifications made" note at the bottom of the spec so the human
   reviewer can see what you removed at the spec gate.

## Ground rules

Only write markdown. Do not touch code. Do not commit.
