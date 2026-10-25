---
name: design-spec
description: Using when creating a design specification for the next phase of implementation
---

## Ground Rules

Enter planning mode. Only write markdown. Do not commit to Git.

## Objective

Produce a `design.md` that describes the technical goals of the phase, including a thorough description of what needs to
be build, using which frameworks, what trade-offs are being made, etc.

## Spec Contents

Typical sections will be as follows.

Relevant items would be:

- API specs
- Database schema changes
- Dataflow diagrams
- Scenarios to cover and test
- Implementation checklist of files to edit
- What's out of scope or reserved for later

```markdown
# design.md

## Overview

## Technical Requirements (multiple sections)

## Data Flow

## Open Questions

## Out of Scope

## Implementation Checklist
```

## Procedure

1. Determine how far the implementation has got by reading `STATE.md`
2. Read `docs/specs/0000-initial-planning/high-level-implementation-plan.md` to understand the next phase
3. Read prior design specs (e.g. `docs/specs/0006-trust-circle/design.md` ) for more detail about earlier phases
4. Create a technical design specification `design.md` for the next phase

## Implementation

During implementation of this spec, Claude may still enter plan mode to make a detailed, parallelizable implementation
plan. We commonly store this as `plan.md` in case we want to refer to it later.
