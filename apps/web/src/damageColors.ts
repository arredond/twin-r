// Damage-state color scale, matching MERISUR's damage-state legend
// (docs/merisur.md §4.6): None / Slight / Moderate / Extensive / Complete.
// Sequential, colorblind-considerate scale from green (no damage) to dark
// red (complete) -- not the final design pass, just a legible placeholder.
export const DAMAGE_COLORS: Record<string, string> = {
  None: "#2f9e44",
  Slight: "#f9c74f",
  Moderate: "#f3722c",
  Extensive: "#e5383b",
  Complete: "#7f1d1d",
  Unknown: "#adb5bd",
};

export const DAMAGE_STATES = ["None", "Slight", "Moderate", "Extensive", "Complete"] as const;
export type DamageState = (typeof DAMAGE_STATES)[number];
