import type { Fault, ProbabilityLevel } from "../scenarioApi";
import { PROBABILITY_LEVEL_LABELS } from "../probabilityLevels";

// MERISUR's two rupture-entry modes (docs/merisur.md §4.1/§5): "Automatic"
// (pick a QAFI fault, get its maximum-magnitude earthquake) and "Manual"
// (type in rupture parameters directly).

// Manual mode's progressive complexity (ADR-0008): magnitude is the only
// required input; style-of-faulting and full geometry are optional tiers
// on top, not separate forms -- lifted to App so a map click can drive it.
export interface ManualParams {
  lat: number;
  lon: number;
  mag: number;
  styleOfFaulting: "strike-slip" | "normal" | "reverse";
  advancedEnabled: boolean; // whether strike/dip/ztorKm are sent at all
  strike: number;
  dip: number;
  ztorKm: number;
}

interface Props {
  mode: "automatic" | "manual";
  onModeChange: (mode: "automatic" | "manual") => void;
  probabilityLevel: ProbabilityLevel;
  onProbabilityLevelChange: (level: ProbabilityLevel) => void;
  faults: Fault[] | null;
  faultsError: string | null;
  selectedFaultId: string | null;
  onSelectFault: (faultId: string) => void;
  onFaultSubmit: (faultId: string) => void;
  manualParams: ManualParams;
  onManualParamsChange: (update: (params: ManualParams) => ManualParams) => void;
  onManualSubmit: () => void;
  isRunning: boolean;
}

export function RuptureForm({
  mode,
  onModeChange,
  probabilityLevel,
  onProbabilityLevelChange,
  faults,
  faultsError,
  selectedFaultId,
  onSelectFault,
  onFaultSubmit,
  manualParams,
  onManualParamsChange,
  onManualSubmit,
  isRunning,
}: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button
          type="button"
          onClick={() => onModeChange("automatic")}
          disabled={mode === "automatic"}
        >
          Automatic
        </button>
        <button
          type="button"
          onClick={() => onModeChange("manual")}
          disabled={mode === "manual"}
        >
          Manual
        </button>
      </div>

      <fieldset style={{ border: "1px solid #ddd", borderRadius: 4 }}>
        <legend style={{ fontSize: "0.85rem" }}>Probability level</legend>
        {(Object.keys(PROBABILITY_LEVEL_LABELS) as ProbabilityLevel[]).map((level) => (
          <label key={level} style={{ flexDirection: "row", alignItems: "center", gap: "0.4rem" }}>
            <input
              type="radio"
              name="probabilityLevel"
              checked={probabilityLevel === level}
              onChange={() => onProbabilityLevelChange(level)}
            />
            {PROBABILITY_LEVEL_LABELS[level]}
          </label>
        ))}
      </fieldset>

      {mode === "automatic" ? (
        <AutomaticForm
          faults={faults}
          faultsError={faultsError}
          selectedFaultId={selectedFaultId}
          onSelectFault={onSelectFault}
          onSubmit={onFaultSubmit}
          isRunning={isRunning}
        />
      ) : (
        <ManualForm
          params={manualParams}
          onChange={onManualParamsChange}
          onSubmit={onManualSubmit}
          isRunning={isRunning}
        />
      )}
    </div>
  );
}

function AutomaticForm({
  faults,
  faultsError,
  selectedFaultId,
  onSelectFault,
  onSubmit,
  isRunning,
}: {
  faults: Fault[] | null;
  faultsError: string | null;
  selectedFaultId: string | null;
  onSelectFault: (faultId: string) => void;
  onSubmit: (faultId: string) => void;
  isRunning: boolean;
}) {
  if (faultsError) return <p style={{ color: "#c1121f" }}>{faultsError}</p>;
  if (!faults) return <p>Loading faults…</p>;

  const fault = faults.find((f) => f.fault_id === selectedFaultId);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (selectedFaultId) onSubmit(selectedFaultId);
      }}
      style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}
    >
      <label>
        Fault ({faults.length} available) — or click one on the map
        <select
          value={selectedFaultId ?? ""}
          onChange={(e) => onSelectFault(e.target.value)}
        >
          {!selectedFaultId && <option value="">Select a fault…</option>}
          {faults.map((f) => (
            <option key={f.fault_id} value={f.fault_id}>
              {f.name} (Mmax {f.mmax.toFixed(1)})
            </option>
          ))}
        </select>
      </label>
      {fault && (
        <p style={{ fontSize: "0.8rem", color: "#666" }}>
          Generates this fault's maximum-magnitude earthquake (Mw {fault.mmax.toFixed(2)}).{" "}
          {fault.mmax_source === "estimated_wells_coppersmith_1994" &&
            "Mmax estimated from fault length (Wells & Coppersmith 1994) -- QAFI has no published value for this fault."}
        </p>
      )}
      <button type="submit" disabled={isRunning || !selectedFaultId}>
        {isRunning ? "Running scenario…" : "Run scenario"}
      </button>
    </form>
  );
}

function ManualForm({
  params,
  onChange,
  onSubmit,
  isRunning,
}: {
  params: ManualParams;
  onChange: (update: (params: ManualParams) => ManualParams) => void;
  onSubmit: () => void;
  isRunning: boolean;
}) {
  function set<K extends keyof ManualParams>(key: K, value: ManualParams[K]) {
    onChange((p) => ({ ...p, [key]: value }));
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}
    >
      <label>
        Latitude
        <input
          type="number"
          step="0.001"
          value={params.lat}
          onChange={(e) => set("lat", Number(e.target.value))}
        />
      </label>
      <label>
        Longitude
        <input
          type="number"
          step="0.001"
          value={params.lon}
          onChange={(e) => set("lon", Number(e.target.value))}
        />
      </label>
      <p style={{ fontSize: "0.75rem", color: "#999", margin: 0 }}>
        Click anywhere on the map (away from a fault line) to set these instead.
      </p>

      <label>
        Magnitude (Mw) — required
        <input
          type="number"
          step="0.1"
          min={3}
          max={9}
          value={params.mag}
          onChange={(e) => set("mag", Number(e.target.value))}
        />
      </label>

      <fieldset style={{ border: "1px solid #ddd", borderRadius: 4 }}>
        <legend style={{ fontSize: "0.85rem" }}>Style of faulting</legend>
        {(["strike-slip", "normal", "reverse"] as const).map((style) => (
          <label key={style} style={{ flexDirection: "row", alignItems: "center", gap: "0.4rem" }}>
            <input
              type="radio"
              name="styleOfFaulting"
              checked={params.styleOfFaulting === style}
              onChange={() => set("styleOfFaulting", style)}
            />
            {style === "strike-slip" ? "Strike-slip (default)" : style}
          </label>
        ))}
      </fieldset>

      <details
        open={params.advancedEnabled}
        onToggle={(e) => set("advancedEnabled", e.currentTarget.open)}
      >
        <summary style={{ fontSize: "0.85rem", cursor: "pointer" }}>
          Advanced: fault geometry (strike/dip/depth)
        </summary>
        <p style={{ fontSize: "0.75rem", color: "#666" }}>
          Computes a real finite rupture plane (length/width derived from
          magnitude) instead of treating the earthquake as a single point --
          more accurate near the rupture, but requires an orientation we
          won't guess on your behalf.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <label>
            Strike (°, 0-360, direction the fault runs)
            <input
              type="number"
              step="1"
              min={0}
              max={360}
              value={params.strike}
              onChange={(e) => set("strike", Number(e.target.value))}
            />
          </label>
          <label>
            Dip (°, 0-90, tilt from horizontal)
            <input
              type="number"
              step="1"
              min={1}
              max={90}
              value={params.dip}
              onChange={(e) => set("dip", Number(e.target.value))}
            />
          </label>
          <label>
            Depth to top of rupture (km)
            <input
              type="number"
              step="0.5"
              min={0}
              value={params.ztorKm}
              onChange={(e) => set("ztorKm", Number(e.target.value))}
            />
          </label>
        </div>
      </details>

      <button type="submit" disabled={isRunning}>
        {isRunning ? "Running scenario…" : "Run scenario"}
      </button>
    </form>
  );
}
