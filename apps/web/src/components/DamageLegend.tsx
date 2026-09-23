import { DAMAGE_COLORS, DAMAGE_STATES, DEBRIS_COLOR } from "../damageColors";

export type LayerStatus = "idle" | "loading" | "ready";

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

// A section heading for one scenario layer (municipal stats / buildings /
// debris), with a small status affordance next to it. Scenario compute is
// still synchronous end to end (services/scenario/results_store.py's
// docstring), so today `status` only ever flashes through "loading" for the
// duration of one request -- this exists so the UI already has the right
// shape once compute becomes an async job the frontend polls for, one
// layer's readiness at a time (municipal stats first, then buildings, then
// debris).
function LayerSection({
  title,
  status,
  children,
}: {
  title: string;
  status: LayerStatus;
  children?: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 600 }}>
        <span>{title}</span>
        {status === "loading" && (
          <span aria-label="loading" style={{ fontSize: "0.75rem", opacity: 0.6 }}>
            loading…
          </span>
        )}
      </div>
      <div style={{ opacity: status === "loading" ? 0.5 : 1 }}>{children}</div>
    </div>
  );
}

export interface DamageLegendProps {
  municipalStatsStatus?: LayerStatus;
  buildingsStatus?: LayerStatus;
  debrisStatus?: LayerStatus;
}

export function DamageLegend({
  municipalStatsStatus = "idle",
  buildingsStatus = "idle",
  debrisStatus = "idle",
}: DamageLegendProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <LayerSection title="Municipal stats" status={municipalStatsStatus}>
        {/* The low-zoom choropleth's own color scale lives in DamageMap.tsx
            (IGN/CNIG municipality boundaries) -- this row is just the
            section's loading affordance, no separate swatch needed. */}
      </LayerSection>
      <LayerSection title="Buildings" status={buildingsStatus}>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
          {DAMAGE_STATES.map((state) => (
            <LegendRow key={state} color={DAMAGE_COLORS[state]} label={state} />
          ))}
          {/* Distinct from "None": grey means the scenario never evaluated
              this building at all (outside the affected radius), not that
              it came out undamaged -- see DamageMap.tsx. */}
          <LegendRow color={DAMAGE_COLORS.Unknown} label="Not evaluated" />
        </div>
      </LayerSection>
      <LayerSection title="Debris" status={debrisStatus}>
        {/* Debris rings (ADR-0010) are always shown after a scenario run --
            see DamageMap.tsx -- a separate concept from a building's own
            damage color, not a restatement of it. */}
        <LegendRow color={DEBRIS_COLOR} label="Debris (façade buffer)" />
      </LayerSection>
    </div>
  );
}
