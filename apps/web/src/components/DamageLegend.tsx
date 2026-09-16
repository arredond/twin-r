import { DAMAGE_COLORS, DAMAGE_STATES, DEBRIS_COLOR } from "../damageColors";

function LegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
      <span
        style={{
          display: "inline-block",
          width: "0.9rem",
          height: "0.9rem",
          background: color,
          borderRadius: 2,
        }}
      />
      <span>{label}</span>
    </div>
  );
}

export function DamageLegend() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
      {DAMAGE_STATES.map((state) => (
        <LegendRow key={state} color={DAMAGE_COLORS[state]} label={state} />
      ))}
      {/* Distinct from "None": grey means the scenario never evaluated
          this building at all (outside the affected radius), not that it
          came out undamaged -- see DamageMap.tsx. */}
      <LegendRow color={DAMAGE_COLORS.Unknown} label="Not evaluated" />
      {/* Debris rings (ADR-0010) are always shown after a scenario run --
          see DamageMap.tsx -- a separate concept from a building's own
          damage color, not a restatement of it. */}
      <LegendRow color={DEBRIS_COLOR} label="Debris (façade buffer)" />
    </div>
  );
}
