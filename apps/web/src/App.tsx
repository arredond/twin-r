import { useEffect, useState } from "react";
import { DamageMap } from "./components/DamageMap";
import { RuptureForm, type ManualParams } from "./components/RuptureForm";
import { PROBABILITY_LEVEL_LABELS } from "./probabilityLevels";
import { DamageLegend } from "./components/DamageLegend";
import {
  listFaults,
  runFaultScenario,
  runManualScenario,
  type Fault,
  type ProbabilityLevel,
  type ScenarioResult,
} from "./scenarioApi";

// twin-r milestone-1 MVP shell: source panel -> run -> damage layer,
// matching MERISUR's own UX shape (docs/merisur.md §5). The
// probability-level selector (docs/merisur.md §4.7) landed per
// docs/validation-lorca-2011.md §10.5.
export default function App() {
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Faults are fetched once here (not inside RuptureForm) because both the
  // sidebar dropdown and the map's clickable fault layer need the same
  // list -- MERISUR lets you pick a fault either way (docs/merisur.md §4.1
  // "select an existing fault", §5 map + list selection).
  const [faults, setFaults] = useState<Fault[] | null>(null);
  const [faultsError, setFaultsError] = useState<string | null>(null);
  const [selectedFaultId, setSelectedFaultId] = useState<string | null>(null);

  // Where a fault's rupture gets anchored on its trace (scenario/faults.py:
  // "closest point on the trace to this reference") -- kept live from the
  // map's own center/click, never a fixed default. See DamageMap's
  // onMapMove/onFaultClick docs: a single nationwide default point makes a
  // long fault's rupture location wrong for most of Spain except wherever
  // that default happens to sit.
  const [mapCenter, setMapCenter] = useState({ lat: 40.0, lon: -3.7038 });

  // Mode + manual-mode form state live here (not inside RuptureForm) so a
  // map click (DamageMap) can drive both -- clicking empty space switches
  // to manual mode and fills in the clicked coordinates, matching "clicking
  // on the map should set lat/long."
  const [mode, setMode] = useState<"automatic" | "manual">("automatic");
  // MERISUR's probability-level selector (docs/merisur.md §4.7): shared
  // across both modes, same as `mode` itself -- lifted here rather than
  // duplicated per-mode since it means the same thing (which ground-motion/
  // damage percentile to use) regardless of how the rupture was defined.
  const [probabilityLevel, setProbabilityLevel] = useState<ProbabilityLevel>("high");
  const [manualParams, setManualParams] = useState<ManualParams>({
    lat: 40.4168,
    lon: -3.7038, // Madrid -- arbitrary, recognizable starting point, not seismically special
    mag: 6.0,
    styleOfFaulting: "strike-slip",
    advancedEnabled: false,
    strike: 0,
    dip: 90,
    ztorKm: 5,
  });

  useEffect(() => {
    listFaults()
      .then(setFaults)
      .catch((e) => setFaultsError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function runScenario(run: () => Promise<ScenarioResult>) {
    setIsRunning(true);
    setError(null);
    try {
      setResult(await run());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsRunning(false);
    }
  }

  function handleManualSubmit() {
    const { lat, lon, mag, styleOfFaulting, advancedEnabled, strike, dip, ztorKm } = manualParams;
    const rake = STYLE_OF_FAULTING_RAKE[styleOfFaulting];
    return runScenario(() =>
      runManualScenario({
        lat,
        lon,
        mag,
        rake,
        // ADR-0008: only sent when the user has opted into the advanced
        // tier -- omitting them (not just leaving them at a default value)
        // is what tells the backend to stay a point source.
        ...(advancedEnabled ? { strike, dip, ztor_km: ztorKm } : {}),
        probability_level: probabilityLevel,
      })
    );
  }

  // Dropdown selection: no click coordinate, so the fault ruptures at the
  // point on its trace closest to wherever the map currently happens to be
  // centered.
  function handleFaultSubmit(faultId: string) {
    setSelectedFaultId(faultId);
    return runScenario(() =>
      runFaultScenario(faultId, mapCenter.lat, mapCenter.lon, probabilityLevel)
    );
  }

  // Clicking a fault on the map selects *and* runs it immediately, matching
  // MERISUR's "click a fault, get its max-magnitude earthquake" flow more
  // directly than the sidebar's select-then-press-"Run scenario" two-step --
  // and, unlike the dropdown, we have the exact clicked point to anchor the
  // rupture to, which is what actually determines where on the fault's
  // trace it occurs.
  function handleFaultClick(faultId: string, lat: number, lon: number) {
    setSelectedFaultId(faultId);
    void runScenario(() => runFaultScenario(faultId, lat, lon, probabilityLevel));
  }

  // Clicking anywhere else on the map (DamageMap already excludes fault-line
  // hits, which go to handleFaultClick instead) sets manual mode's rupture
  // location -- but only while manual mode is already active. Switching
  // modes on a stray map click would be surprising while browsing the map
  // in automatic mode (e.g. panning near a building); the user has to
  // deliberately pick "Manual" first.
  function handleMapClick(lat: number, lon: number) {
    if (mode !== "manual") return;
    setManualParams((p) => ({ ...p, lat, lon }));
  }

  return (
    <div style={{ display: "flex", width: "100vw", height: "100vh" }}>
      <aside
        style={{
          width: "20rem",
          padding: "1rem",
          display: "flex",
          flexDirection: "column",
          gap: "1.5rem",
          overflowY: "auto",
          borderRight: "1px solid #ddd",
        }}
      >
        <div>
          <h1 style={{ fontSize: "1.1rem" }}>twin-r</h1>
          <p style={{ fontSize: "0.85rem", color: "#666" }}>
            Seismic scenario simulator — Spain
          </p>
        </div>

        <RuptureForm
          mode={mode}
          onModeChange={setMode}
          probabilityLevel={probabilityLevel}
          onProbabilityLevelChange={setProbabilityLevel}
          faults={faults}
          faultsError={faultsError}
          selectedFaultId={selectedFaultId}
          onSelectFault={setSelectedFaultId}
          onFaultSubmit={handleFaultSubmit}
          manualParams={manualParams}
          onManualParamsChange={setManualParams}
          onManualSubmit={handleManualSubmit}
          isRunning={isRunning}
        />

        {error && <p style={{ color: "#c1121f" }}>{error}</p>}

        {result && (
          <p style={{ fontSize: "0.85rem" }}>
            {result.n_evaluated.toLocaleString()} buildings evaluated,{" "}
            {result.buildings.length.toLocaleString()} damaged, for Mw{" "}
            {result.rupture.mag.toFixed(2)}
            {result.rupture.finite_rupture && " (finite rupture plane)"}
            <br />
            <span style={{ color: "#666" }}>
              {result.rupture.source} — {PROBABILITY_LEVEL_LABELS[result.rupture.probability_level]}
            </span>
          </p>
        )}

        <div>
          <h2 style={{ fontSize: "0.9rem" }}>Damage state</h2>
          <DamageLegend />
        </div>

        <p style={{ fontSize: "0.75rem", color: "#999" }}>
          Dashed purple lines are QAFI faults — click one to run its
          maximum-magnitude earthquake. In Manual mode, click anywhere else
          on the map to set the rupture location.
        </p>
      </aside>
      <main style={{ flex: 1 }}>
        <DamageMap
          results={result?.buildings ?? null}
          municipalityStats={result?.municipality_stats ?? []}
          evaluatedRegion={result?.evaluated_region ?? null}
          faults={faults}
          selectedFaultId={selectedFaultId}
          onFaultClick={handleFaultClick}
          onMapClick={handleMapClick}
          onMapMove={(lat, lon) => setMapCenter({ lat, lon })}
        />
      </main>
    </div>
  );
}

const STYLE_OF_FAULTING_RAKE: Record<ManualParams["styleOfFaulting"], number> = {
  "strike-slip": 0,
  normal: -90,
  reverse: 90,
};
