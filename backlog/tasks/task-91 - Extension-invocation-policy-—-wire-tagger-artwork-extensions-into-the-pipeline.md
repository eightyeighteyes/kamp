---
id: TASK-91
title: Extension invocation policy — wire tagger/artwork extensions into the pipeline
status: To Do
assignee: []
created_date: '2026-04-06 12:55'
labels:
  - feature
  - design
  - 'estimate: lp'
milestone: m-2
dependencies:
  - TASK-85
  - TASK-17
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Design and implement the policy that governs when and how the host invokes registered tagger and artwork-source extensions against library tracks.

The current pipeline (extract → tag → artwork → move) uses first-party MusicBrainz and Cover Art Archive code exclusively. `invoke_extension()` and `apply_mutations()` exist but nothing calls them from the pipeline yet. This task decides the invocation model and wires it in.

## Key design constraint: audit log inflation

Every mutation issued by an extension is appended to `extension_audit_log` (TASK-85). The log is append-only and never pruned. Invocation policy therefore directly controls database growth:

- **Correct policy** (invoke each extension once per track at ingest, skip on re-scan): log grows O(library size) — negligible.
- **Incorrect policy** (invoke on every scan over all existing tracks): log grows O(library x time) — can reach hundreds of MB within a year at 100 new tracks/week.

As a general principle, kamp should be a good citizen in the client's filesystem. Generating unbounded write traffic on a local SQLite database to record mutations that have no effect (re-tagging a track that is already correctly tagged) is not acceptable.

The invocation policy must therefore guarantee that extensions are not offered tracks they have already processed, unless there is a deliberate reason to reprocess (e.g. user-triggered re-tag, new extension version).

## Questions to resolve

1. **Trigger point**: ingest-time only (new tracks entering from staging), on-demand (user action), or both?
2. **Already-processed detection**: use the audit log to detect this extension has already run on this track and skip, or rely on extension skip logic, or filter at the host level by track age/MBID presence?
3. **Extension version changes**: should a new version of an installed extension cause re-processing of existing tracks? If so, how is that scoped?
4. **Ordering**: do registered taggers run before or after the first-party MusicBrainz step? Do they replace it or supplement it?
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Registered tagger extensions are invoked at ingest time for new tracks entering the library
- [ ] #2 Each track is offered to each extension at most once per ingest event — no redundant mutations on re-scan
- [ ] #3 The host uses the audit log or equivalent mechanism to enforce the single-invocation guarantee, not extension skip logic (extension skip logic is unreliable and not under our control)
- [ ] #4 Registered artwork-source extensions are invoked under the same policy as taggers
- [ ] #5 The invocation policy is documented in a comment at the call site explaining why re-scan invocation is explicitly excluded
<!-- AC:END -->
