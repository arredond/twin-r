import type { DamageState } from "./damageColors";

// Client for the scenario function (services/scenario). Defaults to the
// local dev server (uvicorn scenario.local:app); override via
// VITE_SCENARIO_API_URL for a deployed Lambda Function URL.
export const API_URL = import.meta.env.VITE_SCENARIO_API_URL ?? "http://localhost:8000";

// Client for the tiles function (services/tiles) -- a separate deployed
// Lambda from the scenario one above (see infra/stacks/twin_r_stack.py's
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
  lat: number;
  lon: number;
  distance_km: number;
  geometry_geojson: string; // GeoJSON (Multi)LineString, parse before use
}

// Thin per-building payload (services/scenario/response.py): building_id +
// damage_state_code (index into DAMAGE_STATES, ../damageColors.ts) + the
// five probabilities. No lon/lat/im_value/im_type -- every building here is
// already a feature in the buildings PMTiles layer, joined by building_id
// (DamageMap.tsx), so shipping coordinates a second time would be pure
// waste; the intensity value/type each building was evaluated against
// isn't rendered anywhere.
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
  // Only buildings that are actually damaged, or "None"-modal but still a
  // genuine close call against the runner-up damage state (margin under
  // UNCERTAINTY_MARGIN) -- everything else is deliberately left out
  // (docs/validation-region-expansion.md §4: shipping every evaluated
  // building blew the payload up to 634MB for a single scenario, and a
  // flat P(None) cutoff alone wasn't tight enough for a long,
  // large-magnitude fault's gradual intensity decay -- see
  // UNCERTAINTY_MARGIN's own comment). A building absent here is either
  // not a close call (inside evaluated_region) or never evaluated
  // (outside it).
  buildings: BuildingDamageResult[];
  n_evaluated: number;
  elapsed_ms?: number;
  municipality_stats: MunicipalityStats[];
}

// The deployed Lambda writes large results to S3 instead of returning them
// inline (a Function URL's default BUFFERED invoke mode caps responses at
// 6MB -- see handler.py's `_write_to_s3`/`_response`), and hands back
// `{ result_url: <presigned HTTPS URL> }` instead of a ScenarioResult
// directly. The local dev server (local.py) never does this -- it always
// returns the ScenarioResult inline -- so this has to handle both shapes.
async function resolveScenarioResult(body: unknown): Promise<ScenarioResult> {
  if (body && typeof body === "object" && "result_url" in body) {
    const resultUrl = (body as { result_url: string }).result_url;
    const resp = await fetch(resultUrl);
    if (!resp.ok) {
      throw new Error(`fetching scenario result failed (${resp.status}): ${await resp.text()}`);
    }
    return resp.json();
  }
  return body as ScenarioResult;
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
  return resolveScenarioResult(await resp.json());
}

async function getScenario(path: string, params: Record<string, string | number>): Promise<ScenarioResult> {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
  const resp = await fetch(`${API_URL}${path}?${query}`);
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`scenario request failed (${resp.status}): ${detail}`);
  }
  return resolveScenarioResult(await resp.json());
}

export function runManualScenario(req: ManualRuptureRequest): Promise<ScenarioResult> {
  return postScenario("/scenarios/manual", req);
}

// "Automatic" mode (docs/merisur.md §4.1): pick a QAFI fault, run its
// maximum-magnitude earthquake. A GET -- unlike manual mode, mmax/geometry/
// dip/rake all come from QAFI (fault_id alone fully determines the
// evaluated buildings, see services/scenario/local.py's docstring on this
// route); nearLat/nearLon only anchor *which point on the fault's trace*
// gets echoed back for display (backend: "closest point to this
// reference" -- see scenario/faults.py) -- always pass the user's actual
// point of interest (map click, or current view center), never a fixed
// default: a long fault's closest-to-Madrid point can be a poor stand-in
// for its closest point to wherever the user is actually looking.
export function runFaultScenario(
  faultId: string,
  nearLat: number,
  nearLon: number,
  probabilityLevel: ProbabilityLevel = "high"
): Promise<ScenarioResult> {
  return getScenario("/scenarios/fault", {
    fault_id: faultId,
    near_lat: nearLat,
    near_lon: nearLon,
    probability_level: probabilityLevel,
  });
}

// 3000km comfortably covers all of Spain regardless of reference point --
// QAFI only has 201 faults nationwide (see local.py's /faults docstring),
// so there's no volume reason to restrict this; a smaller radius here was
// a leftover from when the app defaulted to a Lorca-centered view and
// silently produced zero results once the default reference point moved
// away from any nearby fault (found: 0 faults within 150km of Madrid).
//
// nearLat/nearLon: same "distance from the user's actual point of
// interest, never a fixed default" rule as runFaultScenario above -- it's
// what orders the returned list nearest-first (RuptureForm.tsx's dropdown
// doesn't display the distance itself, just benefits from the ordering).
// Omitting these previously left the backend's own Madrid default in
// place regardless of where the map was actually centered, so every
// session's dropdown was quietly ordered around Madrid no matter what the
// user was looking at -- always pass the caller's current map center.
export async function listFaults(
  nearLat: number,
  nearLon: number,
  radiusKm = 3000
): Promise<Fault[]> {
  const resp = await fetch(
    `${API_URL}/faults?lat=${nearLat}&lon=${nearLon}&radius_km=${radiusKm}`
  );
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`faults request failed (${resp.status}): ${detail}`);
  }
  const data = await resp.json();
  return data.faults;
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
