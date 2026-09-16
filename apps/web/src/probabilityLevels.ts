import type { ProbabilityLevel } from "./scenarioApi";

// MERISUR's own labels for its three tiers (docs/merisur.md §4.7) -- kept
// verbatim rather than paraphrased, so this reads as "the same selector
// MERISUR has," not a twin-r invention.
export const PROBABILITY_LEVEL_LABELS: Record<ProbabilityLevel, string> = {
  high: "High probability",
  low: "Low probability / high impact",
  very_low: "Very low probability / very high impact",
};
