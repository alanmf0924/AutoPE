# Proposal

## Change Classification

<!-- Why this is a major/cross-module/high-risk change that needs OpenSpec. For an
     ordinary maintenance change, do not create an OpenSpec change. -->

## Why

<!-- Explain the motivation for this change. What problem does this solve? Why now? -->

## What Changes

<!-- Describe what will change. Be specific about new capabilities, modifications, or removals. -->

## Boundaries and Human Gates

<!-- State the non-goals and any required human authorization. Explicitly call out
     data migration, external Dify/model calls, credentials, production publishing,
     rollback, retention, or other irreversible operations. Write “none” only after
     checking each category. -->

## Capabilities

### New Capabilities
<!-- Capabilities being introduced. Name each capability for a cohesive system
     behavior that can own related requirements as the system evolves. Do not name
     implementation tasks or proposal sections. Avoid broad catch-all names. Use
     kebab-case for path segments you introduce (e.g., user-auth or identity/user-auth)
     that follow the project's existing spec organization. Each creates
     specs/<capability-path>/spec.md. -->
- `<capability-path>`: <brief description of what this capability covers>

### Modified Capabilities
<!-- Existing capabilities whose REQUIREMENTS are changing (not just implementation).
     Only list here if spec-level behavior changes. Each needs a delta spec file.
     Use the exact existing path under openspec/specs/. Leave empty if no requirement
     changes. A change with no capabilities at all (pure refactor, tooling, docs)
     must set `skip_specs: true` in its .openspec.yaml - openspec validate rejects
     a zero-delta change without that marker. Do not invent a requirement just to
     satisfy validation. -->
- `<existing-capability-path>`: <what requirement is changing>

## Impact

<!-- Affected code, APIs, dependencies, systems, audit records, and existing evidence. -->

## Acceptance Evidence

<!-- List the evidence required to call this change complete. Separate executed
     checks from planned checks and from manual follow-ups. -->
