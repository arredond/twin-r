import { useEffect, useMemo, useState } from "react";
import { DamageMap } from "./components/DamageMap";
import { RuptureForm, type ManualParams } from "./components/RuptureForm";
import { PROBABILITY_LEVEL_LABELS } from "./probabilityLevels";
import { DamageLegend } from "./components/DamageLegend";
import {
  listFaults,
  warmUpScenarioApi,
  runFaultScenario,
  runManualScenario,
  type Fault,
  type ProbabilityLevel,
  type ScenarioResult,
} from "./scenarioApi";

// twiner milestone-1 MVP shell: source panel -> run -> damage layer,
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

  // Kept live from the map's own center (moveend-driven, see DamageMap's
  // onMapMove -- not per-frame): orders the fault dropdown nearest-first,
  // and is the fallback reference point for the rare fault without full
  // rupture geometry (see runFaultScenario).
  const [mapCenter, setMapCenter] = useState({ lat: 40.0, lon: -3.7038 });
  const faultsByDistance = useMemo(
    () => (faults ? sortFaultsByDistance(faults, mapCenter) : null),
    [faults, mapCenter]
  );

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

  // Fetched once: the backend always returns every fault (no location
  // args), and re-ordering for the current view is local (faultsByDistance
  // above). The old per-pan refetch was a Lambda round trip -- sometimes a
  // cold start -- on every map move.
  useEffect(() => {
    listFaults()
      .then(setFaults)
      .catch((e) => setFaultsError(e instanceof Error ? e.message : String(e)));
    warmUpScenarioApi();
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

  // Fault identity comes from the loaded list (has_rupture_geometry decides
  // whether a reference point is sent at all, see runFaultScenario). An id
  // not in the list -- shouldn't happen, both entry points come from it --
  // is sent with the reference point, which the backend ignores unless it
  // needs it.
  function runFault(faultId: string, near: { lat: number; lon: number }) {
    const fault = faults?.find((f) => f.fault_id === faultId) ?? {
      fault_id: faultId,
      has_rupture_geometry: false,
    };
    return runScenario(() => runFaultScenario(fault, probabilityLevel, near));
  }

  // Dropdown selection: no click coordinate, so the map's current center is
  // the reference point (only used for a fault without rupture geometry).
  function handleFaultSubmit(faultId: string) {
    setSelectedFaultId(faultId);
    return runFault(faultId, mapCenter);
  }

  // Clicking a fault on the map selects *and* runs it immediately, matching
  // MERISUR's "click a fault, get its max-magnitude earthquake" flow more
  // directly than the sidebar's select-then-press-"Run scenario" two-step.
  // The clicked point is the reference point (again, only used for a fault
  // without rupture geometry).
  function handleFaultClick(faultId: string, lat: number, lon: number) {
    setSelectedFaultId(faultId);
    void runFault(faultId, { lat, lon });
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
          <h1 style={{ fontSize: "1.1rem" }}>twiner</h1>
          <p style={{ fontSize: "0.85rem", color: "#666" }}>
            Seismic scenario simulator — Spain
          </p>
        </div>

        <RuptureForm
          mode={mode}
          onModeChange={setMode}
          probabilityLevel={probabilityLevel}
          onProbabilityLevelChange={setProbabilityLevel}
          faults={faultsByDistance}
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
            {result.n_damaged.toLocaleString()} damaged, for Mw{" "}
            {result.rupture.mag.toFixed(2)}
            {result.rupture.finite_rupture && " (finite rupture plane)"}
            {result.cached && " — cached"}
            <br />
            <span style={{ color: "#666" }}>
              {result.rupture.source} — {PROBABILITY_LEVEL_LABELS[result.rupture.probability_level]}
            </span>
          </p>
        )}

        <div>
          <h2 style={{ fontSize: "0.9rem" }}>Damage state</h2>
          <DamageLegend
            municipalStatsStatus={isRunning ? "loading" : result ? "ready" : "idle"}
            buildingsStatus={isRunning ? "loading" : result ? "ready" : "idle"}
            debrisStatus={isRunning ? "loading" : result ? "ready" : "idle"}
          />
        </div>

        <p style={{ fontSize: "0.75rem", color: "#999" }}>
          Dashed purple lines are QAFI faults — click one to run its
          maximum-magnitude earthquake. In Manual mode, click anywhere else
          on the map to set the rupture location.
        </p>
      </aside>
      <main style={{ flex: 1 }}>
        <DamageMap
          scenarioId={result?.scenario_id ?? null}
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

// Nearest-first relative to `center`, by each trace's closest *vertex* --
// an approximation of true point-to-line distance, plenty for ordering a
// dropdown (QAFI traces are densely digitized). Stable for ties, so equal
// distances keep the backend's name order.
function sortFaultsByDistance(faults: Fault[], center: { lat: number; lon: number }): Fault[] {
  const distance = (fault: Fault) => {
    const geometry = JSON.parse(fault.geometry_geojson) as
      | { type: "LineString"; coordinates: number[][] }
      | { type: "MultiLineString"; coordinates: number[][][] };
    const vertices = geometry.type === "LineString" ? geometry.coordinates : geometry.coordinates.flat();
    const cosLat = Math.cos((center.lat * Math.PI) / 180);
    let best = Infinity;
    for (const [lon, lat] of vertices) {
      // Equirectangular, squared -- only compared, never displayed.
      const dx = (lon - center.lon) * cosLat;
      const dy = lat - center.lat;
      best = Math.min(best, dx * dx + dy * dy);
    }
    return best;
  };
  return faults
    .map((fault) => ({ fault, d: distance(fault) }))
    .sort((a, b) => a.d - b.d)
    .map(({ fault }) => fault);
}
