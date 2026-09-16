# ADR-0002: React for the frontend

Status: accepted

## Context

The frontend needs a UI around a MapLibre GL map: rupture input forms (fault
picker or manual parameters), scenario triggering, results/legend display,
and — as the project grows past milestone 1 — more panels (scenario
comparison, layer toggles, national vs. urban scale switching).

## Decision

Build the frontend in **React**.

## Alternatives considered

- **Vanilla JS / lightweight signal-based framework**: less ceremony for a
  map-first MVP, but the planned growth in UI surface (multiple panels,
  scenario management, eventually multi-hazard views) favors a component
  model sooner rather than migrating later.

## Consequences

- Standard React tooling (bundler, component structure) applies; MapLibre GL
  is wrapped in a thin React component rather than driving the DOM directly.
- No state-management library is prescribed yet — start with built-in React
  state/context and revisit only if scenario/session state grows complex
  enough to justify one (record as a follow-up ADR if so).
