import type { DamageState } from "./damageColors";
import { staticDataUrl } from "./staticData";

// Client for the scenario function (services/scenario). Defaults to the
// local dev server (uvicorn scenario.local:app); override via
// VITE_SCENARIO_API_URL for a deployed Lambda Function URL.
export const API_URL = import.meta.env.VITE_SCENARIO_API_URL ?? "http://localhost:8000";

// Client for the tiles function (services/tiles) -- a separate deployed
// Lambda from the scenario one above (see infra/stacks/twiner_stack.py's
// own comment on why: no openquake/numpy/scipy weight, fast cold start,
// doesn't compete with scenario compute for concurrency). Defaults to the
// *same* local dev server as API_URL, since local.py serves both
// /scenarios/* and /tiles/* itself (no separate local process) -- only
// diverges from API_URL when VITE_TILES_API_URL is set, i.e. in the cloud.
// Used by DamageMap.tsx to build the per-scenario tile-join URL template
// (GET /tiles/{scenario_id}/{z}/{x}/{y}.mvt).
export const TILES_API_URL = import.meta.env.VITE_TILES_API_URL ?? API_URL;

// MERISUR's three selectable scenario probability levels
// (services/scenario/probability_level.py, docs/merisur.md §4.7): "high"
// (median ground motion, modal damage state) / "low" (median+1sigma ground
// motion, still modal damage state) / "very_low" (median+1sigma ground
// motion, 85th-percentile damage state).
export type ProbabilityLevel = "high" | "low" | "very_low";

export interface ManualRuptureRequest {
  lat: number;
  lon: number;
  mag: number;
  rake?: number;
  // ADR-0008: only combined into a finite rupture surface when all three
  // are given -- omit any one to fall back to a point source with `rake`.
  strike?: number;
  dip?: number;
  ztor_km?: number;
  probability_level?: ProbabilityLevel;
}

export interface Fault {
  fault_id: string;
  name: string;
  mmax: number;
  mmax_source: string;
  length_km: number;
  // Trace + dip + depth range all present in QAFI, i.e. the backend builds
  // a finite rupture surface for it (ADR-0007) and the rupture's location
  // comes from the fault's own geometry. Only when this is false does
  // runFaultScenario's `near` point matter (point-source fallback).
  has_rupture_geometry: boolean;
  geometry_geojson: string; // GeoJSON (Multi)LineString, parse before use
}

// One building's scenario result, as the tile-join attaches it to each
// feature of the buildings tiles (services/tiles/tile_join.py) --
// DamageMap.tsx's tileDamageResult reads it back off a clicked feature.
// Never downloaded as a list: the scenario response carries no
// per-building data (ADR-0019).
export interface BuildingDamageResult {
  building_id: string;
  damage_state_code: number;
  prob_none: number;
  prob_slight: number;
  prob_moderate: number;
  prob_extensive: number;
  prob_complete: number;
}

// The circle the backend actually searched for buildings to evaluate
// (services/scenario/local.py's UNCERTAINTY_MARGIN) -- any rendered
// building inside it and *not* individually listed in `buildings` below
// was evaluated but its "None" probability wasn't a close call against
// any other damage state (green, no detail); anything outside it was
// never evaluated at all (grey). See DamageMap.tsx.
export interface EvaluatedRegion {
  lat: number;
  lon: number;
  radius_km: number;
}

// Per-municipality damage-state counts, aggregated server-side from the
// scenario's *full* evaluated set (services/scenario/response.py's
// compute_municipality_stats) -- powers the map's low-zoom choropleth
// (DamageMap.tsx), joined against municipalities.pmtiles' own precomputed
// `n_buildings` property (denominator) by `municipality_code`. Always an
// array, empty when the backend has no municipalities dataset available
// yet (see compute_municipality_stats's own docstring) -- not an error.
export interface MunicipalityStats {
  municipality_code: string;
  n_evaluated: number;
  // Buildings with a predicted damage state other than None, over the
  // whole evaluated set (same definition as municipality_stats).
  n_damaged: number;
  counts: Record<DamageState, number>;
}

export interface ScenarioResult {
  // Addresses this scenario's results in the per-scenario tile-join
  // endpoint (GET /tiles/{scenario_id}/{z}/{x}/{y}.mvt -- DamageMap.tsx's
  // buildings source). Both local.py and the deployed handler.py set this
  // (services/scenario/results_store.py locally, services/tiles'
  // S3-backed version in the cloud) -- not optional in practice, but kept
  // so a caller that somehow gets an older/malformed response doesn't
  // crash on a missing field.
  scenario_id?: string;
  rupture: {
    lat: number;
    lon: number;
    mag: number;
    source: string;
    finite_rupture: boolean;
    probability_level: ProbabilityLevel;
  };
  evaluated_region: EvaluatedRegion;
  // True when the backend served a stored result for this exact request
  // (content-addressed scenario_id, services/scenario/scenario_id.py)
  // instead of recomputing it.
  cached?: boolean;
  n_evaluated: number;
  // Buildings with a predicted damage state other than None, over the
  // whole evaluated set (same definition as municipality_stats).
  n_damaged: number;
  elapsed_ms?: number;
  municipality_stats: MunicipalityStats[];
}

async function postScenario(path: string, body: unknown): Promise<ScenarioResult> {
  const resp = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`scenario request failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

async function getScenario(path: string, params: Record<string, string | number>): Promise<ScenarioResult> {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  const resp = await fetch(`${API_URL}${path}?${query}`);
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`scenario request failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}

// Coordinates straight off a MapLibre click/center carry ~15 decimals
// (e.g. 40.394511287068866), i.e. sub-nanometre "precision" -- noise in
// the request, and in a manual scenario's content-addressed scenario_id,
// which then never repeats for two clicks on the same spot. 5 decimals is
// ~1.1 m of latitude and ~0.85 m of longitude at Spanish latitudes (36-44
// degN), well under building scale.
const COORD_DECIMALS = 5;

export function roundCoord(deg: number): number {
  return Number(deg.toFixed(COORD_DECIMALS));
}

export function runManualScenario(req: ManualRuptureRequest): Promise<ScenarioResult> {
  return postScenario("/scenarios/manual", {
    ...req,
    lat: roundCoord(req.lat),
    lon: roundCoord(req.lon),
  });
}

// "Automatic" mode (docs/merisur.md §4.1): pick a QAFI fault, run its
// maximum-magnitude earthquake. A GET: mmax/geometry/dip/rake -- and the
// rupture's own location, its trace midpoint -- all come from QAFI, so
// fault_id + probabilityLevel fully determine the result, which is what
// lets the backend cache it (services/scenario/scenario_id.py).
//
// `near` is only sent for a fault *without* full rupture geometry
// (`has_rupture_geometry` false), where the backend falls back to a point
// source at the trace point closest to it. Sending it for any other fault
// would be ignored anyway, but leaving it out keeps the request URL (and
// any HTTP cache in front of it) identical regardless of map view.
export function runFaultScenario(
  fault: Pick<Fault, "fault_id" | "has_rupture_geometry">,
  probabilityLevel: ProbabilityLevel = "high",
  near?: { lat: number; lon: number }
): Promise<ScenarioResult> {
  return getScenario("/scenarios/fault", {
    fault_id: fault.fault_id,
    probability_level: probabilityLevel,
    ...(!fault.has_rupture_geometry && near ? { near_lat: roundCoord(near.lat), near_lon: roundCoord(near.lon) } : {}),
  });
}

// Every fault in the dataset (QAFI v4: 201 nationwide), sorted by name --
// small enough to load once. Ordering relative to the map view happens
// client-side (App.tsx's sortFaultsByDistance), not by refetching.
//
// Read from the static faults.json next to the PMTiles (ADR-0022), so
// opening the app doesn't wait on a scenario-Lambda cold start. Same body
// as `GET /faults` (services/scenario/export_faults.py), which stays as the
// fallback if the static file is missing (e.g. not exported yet locally).
export async function listFaults(): Promise<Fault[]> {
  try {
    const resp = await fetch(staticDataUrl("faults.json"));
    if (resp.ok) return (await resp.json()).faults;
  } catch {
    // Network error or unparseable body: fall through to the API.
  }
  const resp = await fetch(`${API_URL}/faults`);
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`faults request failed (${resp.status}): ${detail}`);
  }
  const data = await resp.json();
  return data.faults;
}

// Fire-and-forget, once on page load (App.tsx): gets a scenario-Lambda
// execution environment through its one-time setup (hazardlib import,
// numba cache, DuckDB extensions -- services/scenario/warmup.py) while the
// user is still looking at the map, so their first scenario doesn't pay
// for it. Never awaited and never surfaces an error: a failed warm-up
// only means the first scenario request is slower.
export function warmUpScenarioApi(): void {
  fetch(`${API_URL}/warmup`).catch(() => {});
}

// Static exposure attributes for one building (taxonomy_class/height_class
// -- see services/scenario/building_lookup.py; floors/construction
// year/use/cadastral id already come from the clicked PMTiles feature
// directly, this only covers what isn't baked into the tiles). Returns
// null on 404 (building not in the exposure dataset) rather than throwing,
// since a popup should just omit the extra fields, not break the click.
export async function getBuildingInfo(buildingId: string): Promise<Record<string, unknown> | null> {
  const resp = await fetch(`${API_URL}/buildings/${encodeURIComponent(buildingId)}`);
  if (resp.status === 404) return null;
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`building lookup failed (${resp.status}): ${detail}`);
  }
  return resp.json();
}
