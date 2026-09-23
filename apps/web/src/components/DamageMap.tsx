import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MapLibreMap, MapLayerMouseEvent } from "maplibre-gl";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { DAMAGE_COLORS, DAMAGE_STATES, DEBRIS_COLOR } from "../damageColors";
import {
  getBuildingInfo,
  TILES_API_URL,
  type BuildingDamageResult,
  type EvaluatedRegion,
  type Fault,
  type MunicipalityStats,
} from "../scenarioApi";

// Precomputed-tiling architecture (docs/decisions/0003-precomputed-building-tiles.md):
// building geometry is a static PMTiles layer, tiled once offline by the
// exposure pipeline. Once a scenario has run, its own results are joined
// onto that geometry server-side, one tile at a time, by
// services/scenario/tile_join.py -- each `buildings` feature comes back
// with `damage_state_code`/`prob_*` already attached when this scenario
// touched that building, so the frontend never needs to fetch a
// building_id list and setFeatureState it in one-by-one (the previous
// design, which didn't scale past ~100k affected buildings). Before any
// scenario has run, the map falls back to the plain static PMTiles archive
// with no join.
const BUILDINGS_PMTILES_URL =
  import.meta.env.VITE_BUILDINGS_PMTILES_URL ?? "/data/buildings.pmtiles";

const BUILDINGS_SOURCE_ID = "buildings";
const BUILDINGS_LAYER_ID = "buildings-fill";
const BUILDINGS_OUTLINE_LAYER_ID = "buildings-selected-outline";

// Click-to-highlight (buildings and debris): a pink outline on its own thin
// line layer, painted from the "selected" feature-state -- kept separate
// from each layer's own fill-color/fill-opacity choropleth paint so the
// highlight never fights with it (see the two *_OUTLINE_LAYER_ID layers
// below).
const SELECTED_OUTLINE_COLOR = "#ff2d95";
const SELECTED_OUTLINE_PAINT: maplibregl.ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  SELECTED_OUTLINE_COLOR,
  "rgba(0,0,0,0)",
];

// Debris envelopes (ADR-0010, docs/decisions/0010-debris-envelope-precompute.md):
// precomputed offline, one PMTiles layer per building's 1-4m rings, same
// static-tiling pattern as buildings above. A scenario run computes nothing
// new for this layer -- it just sets each building's damage_state_code as
// feature-state (below), same as the buildings layer, and the paint
// expression shows only the one ring matching that code (each ring's
// geometry is cumulative -- 0m to the ring's own distance -- so the
// matching ring already covers everything a lower ring would show).
const DEBRIS_PMTILES_URL = import.meta.env.VITE_DEBRIS_PMTILES_URL ?? "/data/debris.pmtiles";
const DEBRIS_SOURCE_ID = "debris";
const DEBRIS_LAYER_ID = "debris-fill";
const DEBRIS_OUTLINE_LAYER_ID = "debris-selected-outline";
const DEBRIS_RING_OPACITY = 1;

// Municipal boundaries (IGN/CNIG, see pipelines/exposure/src/exposure/
// municipalities.py and DATA-SOURCES.md): a low-zoom choropleth of
// aggregate per-municipality stats, standing in for individual buildings
// until the user zooms in far enough to make picking one out useful.
// `n_buildings` is a precomputed tile property (the pipeline's own count
// of that municipality's buildings.parquet part) -- the denominator for
// "percent affected"; the numerator (`n_evaluated`/per-damage-state
// counts) comes from the current scenario's `municipality_stats` and is
// set as feature-state, same promoteId-keyed pattern as buildings' damage
// color.
const MUNICIPALITIES_PMTILES_URL =
  import.meta.env.VITE_MUNICIPALITIES_PMTILES_URL ?? "/data/municipalities.pmtiles";
const MUNICIPALITIES_SOURCE_ID = "municipalities";
const MUNICIPALITIES_LAYER_ID = "municipalities-fill";

// Below this zoom: municipality choropleth, no individual buildings/debris
// (there are too many to usefully pick one out, and MERISUR-scale damage
// review starts at "which areas", not "which building"). At/above it:
// buildings + debris, no choropleth. Picked empirically once buildings
// render on screen -- no functional reason it has to be exactly 11 beyond
// "roughly city-district scale."
const BUILDING_DETAIL_MINZOOM = 11;

// Interpolated over `mean_severity` (the evaluated buildings' damage_state_code
// average, 0=all None .. 4=all Complete -- see its own feature-state comment
// below) through the *same* 5 DAMAGE_COLORS stops/order a single building's
// fill color is chosen from, not a separate 2-stop green->red gradient --
// so a municipality painted, say, "mostly orange" reads as "mostly Extensive"
// the same way an individual building painted orange does, rather than the
// two scales implying different severities for the same color.
const MUNICIPALITY_FILL_COLOR: maplibregl.ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["coalesce", ["feature-state", "mean_severity"], 0],
  0,
  DAMAGE_COLORS.None,
  1,
  DAMAGE_COLORS.Slight,
  2,
  DAMAGE_COLORS.Moderate,
  3,
  DAMAGE_COLORS.Extensive,
  4,
  DAMAGE_COLORS.Complete,
];

// `damage_state_code` is a tile property once tile_join.py has joined this
// scenario's result onto a building's feature -- not present at all for
// the confidently-undamaged majority a scenario doesn't individually list
// (see scenarioApi.ts's own comment on BuildingDamageResult). `match`'s
// final argument is its fallback, used only if a joined feature somehow
// carried a code outside 0..4.
const BUILDING_DAMAGE_COLOR_EXPR = [
  "match",
  ["get", "damage_state_code"],
  ...DAMAGE_STATES.flatMap((state, index) => [index, DAMAGE_COLORS[state]]),
  DAMAGE_COLORS.Unknown,
] as unknown as maplibregl.ExpressionSpecification;

// A building with no joined `damage_state_code` is either outside this
// scenario's evaluated region (never assessed -- Unknown/grey) or inside it
// but not a close call (confidently undamaged -- None/green), same
// evaluated-circle distinction the popup makes (isWithinEvaluatedRegion).
// `null` (no scenario has run) falls back to Unknown everywhere.
function buildingsFillColor(
  evaluatedRegion: EvaluatedRegion | null
): maplibregl.ExpressionSpecification {
  const fallback: maplibregl.ExpressionSpecification | string = evaluatedRegion
    ? [
        "case",
        [
          "<",
          ["distance", { type: "Point", coordinates: [evaluatedRegion.lon, evaluatedRegion.lat] }],
          evaluatedRegion.radius_km * 1000,
        ],
        DAMAGE_COLORS.None,
        DAMAGE_COLORS.Unknown,
      ]
    : DAMAGE_COLORS.Unknown;
  return ["case", ["has", "damage_state_code"], BUILDING_DAMAGE_COLOR_EXPR, fallback];
}

// No scenario run yet -- the layer's own filter (set from
// `municipalityStats`, see its feature-state effect below) excludes every
// feature, matching this.
const NO_MUNICIPALITIES_FILTER: maplibregl.FilterSpecification = ["in", ["get", "ine_code"], ["literal", []]];

// Faults are a plain GeoJSON source (not tiled): only 201 nationwide --
// nowhere near the scale that justifies PMTiles the way buildings.pmtiles
// does.
const FAULTS_SOURCE_ID = "faults";
const FAULTS_LAYER_ID = "faults-line";
const FAULTS_SELECTED_LAYER_ID = "faults-line-selected";

// Free, no-API-key basemap style. Swap for a twin-r-branded style later.
const BASEMAP_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

// Mainland Spain, zoomed out enough to see most of it at once.
const SPAIN_CENTER: [number, number] = [-3.7038, 40.0];
const SPAIN_ZOOM = 5.3;

interface Props {
  // Damaged, or "None"-modal-but-uncertain, buildings only -- the
  // confidently-undamaged majority isn't sent at all (see scenarioApi.ts);
  // `evaluatedRegion` is how those get colored anyway.
  results: BuildingDamageResult[] | null;
  // Keys the per-scenario tile-join endpoint (GET
  // /tiles/{scenario_id}/{z}/{x}/{y}.mvt, services/scenario/tile_join.py).
  // null before any scenario has run (or for the deployed-Lambda path,
  // which doesn't populate results_store.py yet) -- the buildings layer
  // then falls back to the plain static PMTiles archive, unjoined.
  scenarioId: string | null;
  // Server-computed aggregate stats per municipality (services/scenario's
  // compute_municipality_stats) -- drives the low-zoom choropleth. Always
  // `[]` (never absent) when a scenario has run but the backend had no
  // municipalities dataset available, same "additive, not required" shape
  // as `results` being empty.
  municipalityStats: MunicipalityStats[];
  evaluatedRegion: EvaluatedRegion | null;
  faults: Fault[] | null;
  selectedFaultId: string | null;
  // lat/lon here is the actual point clicked on the fault trace -- not a
  // fixed reference point. Matters because the backend places a fault's
  // rupture at "the point on its trace closest to this reference" (see
  // scenario/faults.py); for a long fault, a single nationwide default
  // (e.g. Madrid) can anchor the rupture at the wrong end of the trace
  // entirely for whichever municipality the user is actually looking at.
  onFaultClick: (faultId: string, lat: number, lon: number) => void;
  // Fires for a click anywhere on the map that *didn't* hit a fault line
  // (those go to onFaultClick instead) -- drives manual mode's lat/lon.
  onMapClick: (lat: number, lon: number) => void;
  onMapMove: (lat: number, lon: number) => void;
}

// The API sends `damage_state_code`, the index into this same
// DAMAGE_STATES ordering (services/scenario/response.py), not a string --
// decode it back to a label/color here rather than shipping the string
// itself over the wire on every one of a few hundred thousand rows.
function damageStateLabel(code: number): string {
  return DAMAGE_STATES[code] ?? "Unknown";
}

// Reconstructs a BuildingDamageResult straight from a clicked buildings
// tile feature's own properties (tile_join.py joins these in server-side,
// see this file's top-of-file docstring) -- null when this building wasn't
// a close call for the current scenario (no damage_state_code property at
// all, the ordinary case for the confidently-undamaged majority).
function tileDamageResult(
  buildingId: string,
  tileProps: Record<string, unknown>
): BuildingDamageResult | null {
  if (tileProps.damage_state_code === undefined) return null;
  return {
    building_id: buildingId,
    damage_state_code: Number(tileProps.damage_state_code),
    prob_none: Number(tileProps.prob_none),
    prob_slight: Number(tileProps.prob_slight),
    prob_moderate: Number(tileProps.prob_moderate),
    prob_extensive: Number(tileProps.prob_extensive),
    prob_complete: Number(tileProps.prob_complete),
  };
}

function escapeHtml(value: unknown): string {
  return String(value ?? "—").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string
  );
}

// A national very-low-probability scenario can return 100k+ buildings
// (elevated ground motion + the 85th-percentile damage threshold both
// widen how many buildings are a genuine close call, see
// docs/validation-region-expansion.md) -- applying setFeatureState to all
// of them one at a time in a single synchronous loop is enough work to
// visibly freeze the tab for several seconds. Spreading the same calls
// across idle/animation-frame chunks keeps the map interactive while the
// coloring catches up progressively instead of all at once; `isStale`
// lets a still-running chunk of an older result abandon itself once a
// newer one has started (e.g. the user ran a second scenario before the
// first finished applying).
const FEATURE_STATE_CHUNK_SIZE = 5000;

function scheduleChunked<T>(
  items: T[],
  apply: (item: T) => void,
  isStale: () => boolean,
  onDone?: () => void,
  chunkSize = FEATURE_STATE_CHUNK_SIZE
): void {
  let i = 0;
  const schedule =
    typeof requestIdleCallback === "function" ? requestIdleCallback : requestAnimationFrame;
  const step = () => {
    if (isStale()) return;
    const end = Math.min(i + chunkSize, items.length);
    for (; i < end; i++) apply(items[i]);
    if (i < items.length) schedule(step);
    else onDone?.();
  };
  step();
}

const EARTH_RADIUS_KM = 6371;

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(a));
}

function isWithinEvaluatedRegion(
  region: EvaluatedRegion | null,
  lat: number,
  lon: number
): boolean {
  if (!region) return false;
  return haversineKm(lat, lon, region.lat, region.lon) <= region.radius_km;
}

// Bounding box approximation of `region`'s circle, in the same style as
// the backend's own padded-box spatial pre-filter (engine.py's
// _load_sites: plain lat/lon degree padding, narrowing longitude by
// cos(lat) -- cheap, and always at least as wide as the true circle).
function boundsFromRegion(region: EvaluatedRegion): maplibregl.LngLatBounds {
  const kmPerDegreeLat = 111.0;
  const latPad = region.radius_km / kmPerDegreeLat;
  const lonPad =
    region.radius_km / (kmPerDegreeLat * Math.max(0.1, Math.abs(Math.cos((region.lat * Math.PI) / 180))));
  return new maplibregl.LngLatBounds(
    [region.lon - lonPad, region.lat - latPad],
    [region.lon + lonPad, region.lat + latPad]
  );
}

// Weighted average of DAMAGE_STATES' own index (0=None .. 4=Complete) over
// a municipality's evaluated buildings -- the choropleth's severity input
// (MUNICIPALITY_FILL_COLOR), so its color reads on the same scale as an
// individual building's (DAMAGE_COLORS[damage_state]) rather than a
// separately-scaled "fraction affected" ramp.
function meanSeverity(stats: MunicipalityStats): number {
  const total = stats.n_evaluated || 1;
  const weighted = DAMAGE_STATES.reduce(
    (sum, state, index) => sum + index * (stats.counts[state] ?? 0),
    0
  );
  return weighted / total;
}

// Proportional-width stacked bar, one segment per damage class -- native
// `title=` gives the hover tooltip (name + percentage) for free, no JS
// wiring needed inside a Popup's detached DOM.
function renderProbabilityBar(damage: BuildingDamageResult): string {
  const probs: Array<[string, number]> = [
    ["None", damage.prob_none],
    ["Slight", damage.prob_slight],
    ["Moderate", damage.prob_moderate],
    ["Extensive", damage.prob_extensive],
    ["Complete", damage.prob_complete],
  ];
  const segments = probs
    .filter(([, p]) => p > 0)
    .map(([state, p]) => {
      const pct = Math.round(p * 100);
      return (
        `<div title="${escapeHtml(state)}: ${pct}%" ` +
        `style="flex:${p}; background:${DAMAGE_COLORS[state]}; height:100%"></div>`
      );
    })
    .join("");
  return (
    `<div style="display:flex; width:100%; height:0.9rem; border-radius:2px; ` +
    `overflow:hidden; margin:3px 0">${segments}</div>`
  );
}

// Popup content for a clicked building: static exposure attributes come
// from two places -- floors/construction year/use/cadastral id (=
// building_id) are already on the clicked PMTiles feature itself (no
// request needed), while taxonomy_class/height_class only live in
// exposure.parquet and need the /buildings/{id} lookup (buildingInfo,
// still loading -> null while the request is in flight). Damage state
// comes from the current scenario `results`, if this building is in it.
function renderBuildingPopupHtml(
  tileProps: Record<string, unknown>,
  damage: BuildingDamageResult | null,
  withinEvaluatedRegion: boolean,
  buildingInfo: Record<string, unknown> | null | "loading"
): string {
  const rows: Array<[string, string]> = [
    ["Floors", escapeHtml(tileProps.floors)],
    ["Built", escapeHtml(tileProps.construction_year)],
    ["Use", escapeHtml(tileProps.current_use)],
  ];
  if (buildingInfo === "loading") {
    rows.push(["Construction typology", "loading…"]);
  } else if (buildingInfo) {
    rows.push(["Construction typology", escapeHtml(buildingInfo.taxonomy_class)]);
    rows.push(["Height class", escapeHtml(buildingInfo.height_class)]);
  }

  let damageHtml: string;
  if (damage) {
    // This building was individually returned by the API -- either
    // actually damaged, or "None"-modal but still meaningfully uncertain
    // (a genuine close call against the runner-up damage state, see
    // scenarioApi.ts) -- either way we have its real probability
    // breakdown, unlike the not-a-close-call case below.
    const label = damageStateLabel(damage.damage_state_code);
    damageHtml =
      `<div style="margin-bottom:2px"><span style="color:#666">Predicted damage:</span> ` +
      `<strong>${escapeHtml(label)}</strong></div>${renderProbabilityBar(damage)}`;
  } else if (withinEvaluatedRegion) {
    // Inside the evaluated circle, but not individually listed -- the API
    // omitted it for being "None"-modal without a genuine close call
    // against another damage state, so no per-building probabilities are
    // available, just the summary.
    damageHtml =
      '<div style="margin-bottom:2px"><span style="color:#666">Predicted damage:</span> Likely None (not individually evaluated)</div>';
  } else {
    // Outside the evaluated circle (or no scenario has run at all) --
    // distinct from the confidently-undamaged case above: this building
    // was never assessed one way or the other.
    damageHtml =
      '<div style="margin-bottom:2px"><span style="color:#666">Predicted damage:</span> not evaluated</div>';
  }

  const body = rows
    .map(
      ([label, value]) =>
        `<div style="margin-bottom:2px"><span style="color:#666">${label}:</span> ${value}</div>`
    )
    .join("");
  return (
    `<div style="font-size:0.8rem; max-width:16rem">` +
    `<h3 style="font-size:0.95rem; font-weight:600; margin:0 0 4px">${escapeHtml(tileProps.building_id)}</h3>` +
    `${body}${damageHtml}</div>`
  );
}

// Debris popup: only building_id + which ring (=which damage state first
// triggers it, ring N === DAMAGE_STATES[N] per debris.py's module
// docstring) -- no volume field exists yet (deferred, ADR-0010), so none is
// shown rather than fabricated.
function renderDebrisPopupHtml(tileProps: Record<string, unknown>): string {
  const ring = Number(tileProps.ring);
  const label = Number.isFinite(ring) ? damageStateLabel(ring) : "Unknown";
  return (
    `<div style="font-size:0.8rem; max-width:16rem">` +
    `<h3 style="font-size:0.95rem; font-weight:600; margin:0 0 4px">${escapeHtml(tileProps.building_id)}</h3>` +
    `<div style="margin-bottom:2px"><span style="color:#666">Debris ring:</span> ${escapeHtml(ring)} (${escapeHtml(label)}+)</div>` +
    `</div>`
  );
}

// Municipality choropleth popup: name + aggregate stats if a scenario has
// touched it (stats param, looked up by ine_code -- null when this
// municipality has no scenario data yet, same "additive" shape as the
// fill color's own no-data branch).
function renderMunicipalityPopupHtml(
  tileProps: Record<string, unknown>,
  stats: MunicipalityStats | null
): string {
  const nBuildings = Number(tileProps.n_buildings) || 0;
  const rows: string[] = [
    `<div style="margin-bottom:2px"><span style="color:#666">Buildings:</span> ${nBuildings.toLocaleString()}</div>`,
  ];
  if (stats) {
    const nAffected = stats.n_evaluated - stats.counts.None;
    const pct = nBuildings > 0 ? ((nAffected / nBuildings) * 100).toFixed(1) : "—";
    rows.push(
      `<div style="margin-bottom:2px"><span style="color:#666">Affected:</span> ${pct}%</div>`
    );
    const total = stats.n_evaluated || 1;
    rows.push(renderProbabilityBar({
      building_id: "",
      damage_state_code: 0,
      prob_none: stats.counts.None / total,
      prob_slight: stats.counts.Slight / total,
      prob_moderate: stats.counts.Moderate / total,
      prob_extensive: stats.counts.Extensive / total,
      prob_complete: stats.counts.Complete / total,
    } as BuildingDamageResult));
  } else {
    rows.push(
      '<div style="margin-bottom:2px"><span style="color:#666">Affected:</span> not evaluated</div>'
    );
  }
  return (
    `<div style="font-size:0.8rem; max-width:16rem">` +
    `<h3 style="font-size:0.95rem; font-weight:600; margin:0 0 4px">${escapeHtml(tileProps.name)}</h3>` +
    rows.join("") +
    `</div>`
  );
}

// Adds the buildings source + its two layers, pointed at either the
// per-scenario tile-join endpoint (scenarioId given -- tile_join.py joins
// this scenario's results into each tile's `buildings` features on the
// fly) or the plain static archive (scenarioId null -- before any scenario
// has run, or for the deployed-Lambda path, which doesn't populate
// results_store.py/expose this endpoint yet). Called both on initial map
// load and, again, whenever the source-swap effect below sees scenarioId
// actually change -- a vector source's tiles/url can't be swapped in place
// between a PMTiles archive and an XYZ tile endpoint, so this always
// removes+re-adds both the source and its layers rather than mutating one
// in place.
function addBuildingsSourceAndLayers(map: MapLibreMap, scenarioId: string | null): void {
  map.addSource(BUILDINGS_SOURCE_ID, {
    type: "vector",
    ...(scenarioId
      ? {
          tiles: [`${TILES_API_URL}/tiles/${scenarioId}/{z}/{x}/{y}.mvt`],
          // A plain `tiles` array source has no TileJSON/PMTiles header to
          // read a real maxzoom from -- MapLibre defaults it to 22, which
          // made it request genuine z15+ tiles from tile_join.py instead
          // of overscaling the last real z14 one (confirmed: every z15+
          // request came back 204, since tile_join.py reads from this
          // same buildings.pmtiles archive, whose real max_zoom is 14 --
          // see the static-archive case's own maxzoom comment below).
          // Hardcoded to 14 rather than fetched from the archive at
          // runtime since it's the same constant either branch would need
          // to agree on.
          maxzoom: 14,
        }
      : { url: `pmtiles://${BUILDINGS_PMTILES_URL}` }),
    promoteId: "building_id",
    // Deliberately NOT overriding maxzoom for the static-archive case
    // (contrast debris.pmtiles' source below, also unoverridden) --
    // MapLibre already reads it from the PMTiles header itself, and
    // that's the right source of truth as long as the archive's own
    // metadata is honest. It currently isn't: buildings.pmtiles' header
    // claims maxzoom 15, but z15 is a uniformly empty ~27-byte tile
    // everywhere checked (10 major cities nationwide, not a regional
    // gap), and the file's own tilestats reports 63.2M buildings against
    // an expected ~12.9M (ADR-0010) -- a ~4.9x inflation, generator
    // "tile-join v2.79.0" rather than plain tippecanoe, pointing at a bad
    // merge, not a simple zoom-guess quirk. A hardcoded maxzoom:14
    // override was tried here first and did paper over the symptom, but
    // silently goes wrong again the moment this file is regenerated
    // correctly (caps detail below whatever the fixed archive can
    // actually support, with no error to catch it). The real fix belongs
    // in the data (see twin-r-8a's fix-municipality-aggregation
    // investigation into this same file's building_id mismatches) --
    // once buildings.pmtiles is regenerated with an honest header, this
    // default (no override) is already correct with no frontend change
    // needed. The scenario tile-join endpoint reads tiles straight from
    // that same archive, so it shares whatever this ends up being.
  });
  map.addLayer({
    id: BUILDINGS_LAYER_ID,
    type: "fill",
    source: BUILDINGS_SOURCE_ID,
    "source-layer": "buildings",
    minzoom: BUILDING_DETAIL_MINZOOM,
    paint: {
      // Grey ("Unknown") until a scenario has run -- updated to the
      // evaluated-region-aware expression once one has (see the
      // evaluatedRegion effect, buildingsFillColor). A joined
      // damage_state_code tile property always wins when present.
      "fill-color": buildingsFillColor(null),
      "fill-opacity": 0.85,
      "fill-outline-color": "#00000033",
    },
  });
  map.addLayer({
    id: BUILDINGS_OUTLINE_LAYER_ID,
    type: "line",
    source: BUILDINGS_SOURCE_ID,
    "source-layer": "buildings",
    minzoom: BUILDING_DETAIL_MINZOOM,
    paint: { "line-color": SELECTED_OUTLINE_PAINT, "line-width": 2.5 },
  });
}

function faultsToFeatureCollection(faults: Fault[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: faults.map(
      (f): Feature => ({
        type: "Feature",
        properties: { fault_id: f.fault_id, name: f.name, mmax: f.mmax },
        geometry: JSON.parse(f.geometry_geojson) as Geometry,
      })
    ),
  };
}

export function DamageMap({
  results,
  scenarioId,
  municipalityStats,
  evaluatedRegion,
  faults,
  selectedFaultId,
  onFaultClick,
  onMapClick,
  onMapMove,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  // Debugging aid only (not tied to any rendering decision) -- lets
  // whoever's poking at zoom-dependent behavior (BUILDING_DETAIL_MINZOOM,
  // debris overzoom, tile density) read the current zoom off the map
  // itself instead of guessing from feel.
  const [zoom, setZoom] = useState<number | null>(null);
  const mapLoadedRef = useRef(false);
  const loadedDebrisBuildingIdsRef = useRef<Set<string>>(new Set());
  // Bumped once per `results` change in the debris feature-state effect
  // below -- lets a chunked run still in progress (scheduleChunked)
  // recognize it's been superseded by a newer one and stop applying.
  const debrisFeatureStateRunIdRef = useRef(0);
  // Which scenario_id (or null, meaning "no join, static archive") the
  // buildings source is currently pointed at -- lets the source-swap effect
  // below skip work when scenarioId hasn't actually changed.
  const buildingsSourceScenarioIdRef = useRef<string | null>(null);
  const loadedMunicipalityCodesRef = useRef<Set<string>>(new Set());
  // Municipality popup needs the latest stats (by municipality_code) to
  // show a clicked polygon's breakdown, without re-binding the click
  // handler -- same pattern as resultsByIdRef below.
  const municipalityStatsRef = useRef(municipalityStats);
  municipalityStatsRef.current = municipalityStats;
  // Click-to-highlight (buildings and debris share one selection -- a
  // debris ring click highlights its building_id the same as clicking the
  // building itself, since both layers key feature-state off the same id).
  // Only one source's "selected" state is ever set at a time; clearing it
  // before setting a new one keeps a stale highlight from lingering on a
  // previous source after a click elsewhere.
  const selectedRef = useRef<{ source: string; sourceLayer: string; id: string } | null>(null);
  // Click/hover/move handlers are bound once (map.on is idempotent-unfriendly
  // to re-bind per render) but need the latest callback -- a ref sidesteps
  // stale closures without re-registering listeners on every render.
  const onFaultClickRef = useRef(onFaultClick);
  onFaultClickRef.current = onFaultClick;
  const onMapClickRef = useRef(onMapClick);
  onMapClickRef.current = onMapClick;
  const onMapMoveRef = useRef(onMapMove);
  onMapMoveRef.current = onMapMove;
  // Debris click popup still needs a client-side building_id -> result
  // lookup (buildings' own popup doesn't -- see tileDamageResult -- but
  // debris.pmtiles isn't tile-joined, see this file's top-of-file
  // docstring on why that's deferred). Indexed by building_id (a national
  // very-low-probability scenario can return 100k+ buildings, see
  // docs/decisions -- a plain array .find() per click would be an O(n)
  // scan over all of them).
  const resultsById = useMemo(() => {
    const byId = new Map<string, BuildingDamageResult>();
    for (const b of results ?? []) byId.set(b.building_id, b);
    return byId;
  }, [results]);
  const resultsByIdRef = useRef(resultsById);
  resultsByIdRef.current = resultsById;
  // Building-click popup needs the region a scenario was evaluated against
  // to classify a clicked building that has no joined damage_state_code
  // (see isWithinEvaluatedRegion) -- damage itself now comes straight off
  // the clicked tile feature's own properties (tile_join.py's join),
  // not a client-side results lookup.
  const evaluatedRegionRef = useRef(evaluatedRegion);
  evaluatedRegionRef.current = evaluatedRegion;

  const faultsData = useMemo(
    () => (faults ? faultsToFeatureCollection(faults) : null),
    [faults]
  );

  useEffect(() => {
    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: containerRef.current!,
      style: BASEMAP_STYLE,
      center: SPAIN_CENTER,
      zoom: SPAIN_ZOOM,
      // MapLibre v6 defaults to "splitting" (requesting literal deeper-zoom
      // tiles rather than immediately overscaling the last real one) for
      // zoomLevelsToOverscale=4 levels below the map's own maxZoom --
      // sensible for a live tile server that can generate detail beyond
      // its advertised maxzoom, wrong for a static PMTiles archive with a
      // real, hard ceiling (buildings.pmtiles: 15, debris.pmtiles: 16).
      // Those "split" requests land past the archive's actual max zoom,
      // and the pmtiles library returns a valid-but-empty MVT tile there
      // rather than signaling "fall back to the parent" -- MapLibre then
      // renders that empty tile as real content, so buildings/debris
      // vanish right past their own maxzoom instead of continuing to
      // overscale.
      //
      // The fix is NOT `0` (tried first, made it worse) -- per MapLibre's
      // own formula, the "split" zone runs from a source's maxzoom up to
      // `map.maxZoom - zoomLevelsToOverscale`, and only zoom levels above
      // that split all the way to `map.maxZoom` overscale. `0` collapses
      // the overscale zone to nothing and makes *every* zoom level past a
      // source's maxzoom split (worse than the default-4 behavior this
      // was meant to fix). To get "overscale immediately, no split zone
      // at all" for both sources regardless of their own maxzoom, this
      // needs to be >= `map.maxZoom - min(source maxzooms)` -- with the
      // default map.maxZoom of 22 and our lowest source maxzoom (15),
      // that's >= 7. Set generously past that so it holds even if a
      // future layer's maxzoom is lower still.
      zoomLevelsToOverscale: 22,
    });
    mapRef.current = map;

    map.on("load", () => {
      // Municipality choropleth (low zoom) -- added before buildings/debris
      // so it renders underneath them once both are visible near the
      // minzoom/maxzoom seam, though in practice only one of the two sets
      // is ever visible at a given zoom (see BUILDING_DETAIL_MINZOOM).
      map.addSource(MUNICIPALITIES_SOURCE_ID, {
        type: "vector",
        url: `pmtiles://${MUNICIPALITIES_PMTILES_URL}`,
        promoteId: "ine_code",
      });
      map.addLayer({
        id: MUNICIPALITIES_LAYER_ID,
        type: "fill",
        source: MUNICIPALITIES_SOURCE_ID,
        "source-layer": "municipalities",
        maxzoom: BUILDING_DETAIL_MINZOOM,
        // No scenario has run at load time -- filter excludes every
        // feature until the municipalityStats effect below narrows it to
        // just the municipalities this scenario actually touched.
        filter: NO_MUNICIPALITIES_FILTER,
        paint: {
          "fill-color": MUNICIPALITY_FILL_COLOR,
          "fill-opacity": 0.75,
          "fill-outline-color": "#00000044",
        },
      });

      addBuildingsSourceAndLayers(map, null);
      buildingsSourceScenarioIdRef.current = null;

      map.addSource(DEBRIS_SOURCE_ID, {
        type: "vector",
        url: `pmtiles://${DEBRIS_PMTILES_URL}`,
        // Not actually unique per feature (4 ring features share one
        // building_id) -- fine here, and deliberate: every ring feature
        // for a building should receive the *same* feature-state
        // (damage_state_code), so a shared promoted id is exactly what
        // lets one setFeatureState call below drive all of a building's
        // rings at once.
        promoteId: "building_id",
      });
      map.addLayer({
        id: DEBRIS_LAYER_ID,
        type: "fill",
        source: DEBRIS_SOURCE_ID,
        "source-layer": "debris",
        minzoom: BUILDING_DETAIL_MINZOOM,
        paint: {
          "fill-color": DEBRIS_COLOR,
          // Only the ring matching a building's *current* predicted damage
          // state renders (ring 1 = Slight .. ring 4 = Complete, ADR-0010).
          // Each ring's geometry is now the *cumulative* envelope out to
          // that ring's distance (debris.py: "ring N always fully contains
          // ring N-1"), not an annulus between two distances -- so the
          // single ring matching the damage state already covers the same
          // area stacking rings 1..N used to. Rendering every ring
          // <= damage_state_code (an earlier version of this expression)
          // would now just repaint the same pixels 1-4 times over.
          // ["feature-state", "damage_state_code"] is unset (null) for any
          // building no scenario has touched yet, so `coalesce` to -1
          // keeps every ring hidden by default rather than comparing
          // against null.
          "fill-opacity": [
            "case",
            ["==", ["get", "ring"], ["coalesce", ["feature-state", "damage_state_code"], -1]],
            DEBRIS_RING_OPACITY,
            0,
          ],
        },
      });
      map.addLayer({
        id: DEBRIS_OUTLINE_LAYER_ID,
        type: "line",
        source: DEBRIS_SOURCE_ID,
        "source-layer": "debris",
        minzoom: BUILDING_DETAIL_MINZOOM,
        // Filter-based, not feature-state-based like BUILDINGS_OUTLINE_LAYER_ID
        // -- debris' `promoteId` is building_id, deliberately shared by all
        // 4 of a building's ring features (so one setFeatureState drives
        // all their damage_state_code-gated visibility at once, see the
        // source comment above). That same sharing means a feature-state
        // "selected" flag can't identify a single ring -- it would light up
        // every ring of the clicked building's debris. A filter on
        // (building_id, ring) together can, since filters read plain tile
        // properties, not the shared promoted id.
        filter: ["==", ["get", "ring"], -1],
        paint: { "line-color": SELECTED_OUTLINE_COLOR, "line-width": 2.5 },
      });

      map.addSource(FAULTS_SOURCE_ID, {
        type: "geojson",
        data: faultsData ?? { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: FAULTS_LAYER_ID,
        type: "line",
        source: FAULTS_SOURCE_ID,
        paint: { "line-color": "#7209b7", "line-width": 2, "line-dasharray": [2, 1] },
      });
      map.addLayer({
        id: FAULTS_SELECTED_LAYER_ID,
        type: "line",
        source: FAULTS_SOURCE_ID,
        filter: ["==", ["get", "fault_id"], "__none__"],
        paint: { "line-color": "#7209b7", "line-width": 4 },
      });

      map.on("click", FAULTS_LAYER_ID, (e: MapLayerMouseEvent) => {
        const faultId = e.features?.[0]?.properties?.fault_id;
        if (faultId) onFaultClickRef.current(faultId, e.lngLat.lat, e.lngLat.lng);
      });
      map.on("mouseenter", FAULTS_LAYER_ID, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", FAULTS_LAYER_ID, () => {
        map.getCanvas().style.cursor = "";
      });

      // Click-to-highlight: buildings and debris share one selection at a
      // time (selecting one clears the other), but use different
      // mechanisms. Buildings use feature-state, since a building's
      // building_id is unique per feature there. Debris can't: its
      // promoteId is building_id shared across all 4 of a building's ring
      // features on purpose (one setFeatureState drives every ring's
      // damage_state_code-gated visibility at once, see the source comment
      // above) -- a "selected" feature-state would light up every ring of
      // the clicked building, not just the one clicked. The debris outline
      // layer is filtered on (building_id, ring) together instead, which
      // only ever matches the single clicked ring feature.
      const clearBuildingSelection = () => {
        if (!selectedRef.current) return;
        map.setFeatureState(selectedRef.current, { selected: false });
        selectedRef.current = null;
      };
      const selectBuilding = (id: string) => {
        clearBuildingSelection();
        clearDebrisSelection();
        const target = { source: BUILDINGS_SOURCE_ID, sourceLayer: "buildings", id };
        map.setFeatureState(target, { selected: true });
        selectedRef.current = target;
      };
      const clearDebrisSelection = () => {
        map.setFilter(DEBRIS_OUTLINE_LAYER_ID, ["==", ["get", "ring"], -1]);
      };
      const selectDebrisRing = (buildingId: string, ring: number) => {
        clearBuildingSelection();
        map.setFilter(DEBRIS_OUTLINE_LAYER_ID, [
          "all",
          ["==", ["get", "building_id"], buildingId],
          ["==", ["get", "ring"], ring],
        ]);
      };
      const clearSelection = () => {
        clearBuildingSelection();
        clearDebrisSelection();
      };

      // Building click popup: floors/construction year/use/cadastral id
      // come straight off the clicked tile feature (no request needed);
      // taxonomy/height class need a /buildings/{id} lookup, and damage
      // comes from whatever scenario has already been run (resultsByIdRef).
      // Registered before the generic "click anywhere" handler below so a
      // building click never also falls through to onMapClick (that
      // handler checks queryRenderedFeatures itself and skips when this
      // layer was hit, but ordering here keeps the popup responsive first).
      map.on("click", BUILDINGS_LAYER_ID, (e: MapLayerMouseEvent) => {
        const feature = e.features?.[0];
        if (!feature) return;
        const tileProps = (feature.properties ?? {}) as Record<string, unknown>;
        const buildingId = tileProps.building_id as string | undefined;
        if (!buildingId) return;

        selectBuilding(buildingId);

        const damage = tileDamageResult(buildingId, tileProps);
        const withinEvaluatedRegion = isWithinEvaluatedRegion(
          evaluatedRegionRef.current,
          e.lngLat.lat,
          e.lngLat.lng
        );
        const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "18rem" })
          .setLngLat(e.lngLat)
          .setHTML(renderBuildingPopupHtml(tileProps, damage, withinEvaluatedRegion, "loading"))
          .addTo(map);

        getBuildingInfo(buildingId)
          .then((info) => {
            if (popup.isOpen())
              popup.setHTML(
                renderBuildingPopupHtml(tileProps, damage, withinEvaluatedRegion, info)
              );
          })
          .catch(() => {
            // Lookup failure shouldn't kill the popup -- just drop the
            // taxonomy/height rows, tile-derived info still shows.
            if (popup.isOpen())
              popup.setHTML(
                renderBuildingPopupHtml(tileProps, damage, withinEvaluatedRegion, null)
              );
          });
      });
      map.on("mouseenter", BUILDINGS_LAYER_ID, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", BUILDINGS_LAYER_ID, () => {
        map.getCanvas().style.cursor = "";
      });

      // Debris click popup: building_id + ring only (no volume field yet,
      // see renderDebrisPopupHtml) -- same selection/highlight treatment as
      // a building click, registered before the generic map-click handler
      // for the same reason.
      map.on("click", DEBRIS_LAYER_ID, (e: MapLayerMouseEvent) => {
        const feature = e.features?.[0];
        if (!feature) return;
        const tileProps = (feature.properties ?? {}) as Record<string, unknown>;
        const buildingId = tileProps.building_id as string | undefined;
        const ring = Number(tileProps.ring);
        if (!buildingId || !Number.isFinite(ring)) return;

        // Every ring feature is still hit-testable regardless of paint
        // opacity (fill-opacity 0 still hit-tests, unlike a filtered-out
        // feature -- see the municipality filter's own comment on this) --
        // a click on any ring below the building's actual predicted damage
        // state would otherwise select/pop up a ring that isn't actually
        // shown. Only the ring matching the current damage state (the one
        // the fill-opacity expression above actually renders) responds.
        const damage = resultsByIdRef.current.get(buildingId);
        if (!damage || ring !== damage.damage_state_code) return;

        selectDebrisRing(buildingId, ring);

        new maplibregl.Popup({ closeButton: true, maxWidth: "18rem" })
          .setLngLat(e.lngLat)
          .setHTML(renderDebrisPopupHtml(tileProps))
          .addTo(map);
      });
      map.on("mouseenter", DEBRIS_LAYER_ID, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", DEBRIS_LAYER_ID, () => {
        map.getCanvas().style.cursor = "";
      });

      // Municipality choropleth click popup -- name + aggregate stats, if
      // this municipality has any (municipalityStatsRef, keyed by
      // municipality_code === the tile's own ine_code).
      map.on("click", MUNICIPALITIES_LAYER_ID, (e: MapLayerMouseEvent) => {
        const feature = e.features?.[0];
        if (!feature) return;
        const tileProps = (feature.properties ?? {}) as Record<string, unknown>;
        const ineCode = tileProps.ine_code as string | undefined;
        if (!ineCode) return;

        const stats =
          municipalityStatsRef.current?.find((m) => m.municipality_code === ineCode) ?? null;
        new maplibregl.Popup({ closeButton: true, maxWidth: "18rem" })
          .setLngLat(e.lngLat)
          .setHTML(renderMunicipalityPopupHtml(tileProps, stats))
          .addTo(map);
      });
      map.on("mouseenter", MUNICIPALITIES_LAYER_ID, () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", MUNICIPALITIES_LAYER_ID, () => {
        map.getCanvas().style.cursor = "";
      });

      // General map click (manual mode's "click to set lat/lon") -- skips
      // clicks that landed on a fault line, building, debris ring or
      // municipality (handled by their own popup click handlers above) so
      // one click doesn't trigger two different behaviors at once, and
      // clears any highlight since this click hit none of them.
      map.on("click", (e: MapLayerMouseEvent) => {
        const hits = map.queryRenderedFeatures(e.point, {
          layers: [FAULTS_LAYER_ID, BUILDINGS_LAYER_ID, DEBRIS_LAYER_ID, MUNICIPALITIES_LAYER_ID],
        });
        if (hits.length === 0) {
          clearSelection();
          onMapClickRef.current(e.lngLat.lat, e.lngLat.lng);
        }
      });

      // Automatic mode's dropdown (no click coordinate available) uses
      // wherever the map is currently centered as its reference point --
      // kept live so it tracks panning/zooming rather than freezing at the
      // initial SPAIN_CENTER.
      const reportCenter = () => {
        const c = map.getCenter();
        onMapMoveRef.current(c.lat, c.lng);
      };
      map.on("moveend", reportCenter);
      reportCenter();

      const reportZoom = () => setZoom(map.getZoom());
      map.on("zoom", reportZoom);
      reportZoom();

      mapLoadedRef.current = true;
    });

    return () => {
      mapLoadedRef.current = false;
      map.remove();
      maplibregl.removeProtocol("pmtiles");
    };
  }, []);

  // Faults data can arrive (or change) after the map has already loaded --
  // update the source in place rather than requiring load-order luck.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !faultsData) return;

    const applyFaultsData = () => {
      (map.getSource(FAULTS_SOURCE_ID) as GeoJSONSource | undefined)?.setData(faultsData);
    };
    if (mapLoadedRef.current) {
      applyFaultsData();
    } else {
      map.once("load", applyFaultsData);
    }
  }, [faultsData]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    map.setFilter(FAULTS_SELECTED_LAYER_ID, [
      "==",
      ["get", "fault_id"],
      selectedFaultId ?? "__none__",
    ]);
  }, [selectedFaultId]);

  // Swap the buildings source to this scenario's tile-join endpoint once
  // one has run (or back to the plain static archive when it hasn't --
  // e.g. a fresh page load). Replaces the old per-building setFeatureState
  // loop entirely: since tile_join.py now joins damage straight onto each
  // tile server-side, there's no client-side coloring pass left to do, at
  // any scenario size.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    if (buildingsSourceScenarioIdRef.current === scenarioId) return;
    buildingsSourceScenarioIdRef.current = scenarioId;

    if (map.getLayer(BUILDINGS_OUTLINE_LAYER_ID)) map.removeLayer(BUILDINGS_OUTLINE_LAYER_ID);
    if (map.getLayer(BUILDINGS_LAYER_ID)) map.removeLayer(BUILDINGS_LAYER_ID);
    if (map.getSource(BUILDINGS_SOURCE_ID)) map.removeSource(BUILDINGS_SOURCE_ID);
    addBuildingsSourceAndLayers(map, scenarioId);
    // A source swap drops any feature-state the removed source held --
    // the previous selection highlight (if any) no longer refers to a
    // feature that still exists, so forget it rather than leaving a
    // dangling ref that clearBuildingSelection would act on uselessly.
    selectedRef.current = null;

    map.setPaintProperty(BUILDINGS_LAYER_ID, "fill-color", buildingsFillColor(evaluatedRegion));
  }, [scenarioId]);

  // Debris rings (ADR-0010): same building_id-keyed feature-state pattern
  // as the buildings layer above, on the separate debris source -- a
  // building's damage_state_code drives which of its precomputed rings
  // the paint expression (added above) actually shows. Kept as its own
  // effect/source rather than reusing BUILDINGS_SOURCE_ID because the
  // debris layer has its own geometry (rings, not footprints) and its own
  // per-feature `ring` attribute the paint expression reads.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    debrisFeatureStateRunIdRef.current += 1;
    const runId = debrisFeatureStateRunIdRef.current;
    const isStale = () => debrisFeatureStateRunIdRef.current !== runId;

    const applyDebrisFeatureState = () => {
      const target = { source: DEBRIS_SOURCE_ID, sourceLayer: "debris" };

      const idsToClear = Array.from(loadedDebrisBuildingIdsRef.current);
      loadedDebrisBuildingIdsRef.current = new Set();

      scheduleChunked(
        idsToClear,
        (id) => map.removeFeatureState({ ...target, id }),
        isStale,
        () => {
          scheduleChunked(results ?? [], (building) => {
            map.setFeatureState(
              { ...target, id: building.building_id },
              { damage_state_code: building.damage_state_code }
            );
            loadedDebrisBuildingIdsRef.current.add(building.building_id);
          }, isStale);
        }
      );
    };

    if (map.isSourceLoaded(DEBRIS_SOURCE_ID)) {
      applyDebrisFeatureState();
    } else {
      map.once("sourcedata", applyDebrisFeatureState);
    }
  }, [results]);

  // Municipality choropleth: same feature-state pattern, keyed by
  // municipality_code (== the tile's own ine_code, promoted as its id).
  // `n_affected` is precomputed here (n_evaluated - the None count) rather
  // than in the paint expression, since GL expressions can't easily reach
  // into a feature-state object's own sub-fields the way JS can.
  //
  // The layer's `filter` is also driven from here (not just feature-state)
  // -- unlike buildings/debris, this is ~8,200 features nationwide, and a
  // municipality with no actual damage should neither render (a grey box
  // over all of Spain before any scenario has run, or over every
  // municipality merely inside the scenario's search radius but otherwise
  // unaffected) nor be clickable (a filtered-out feature is excluded from
  // queryRenderedFeatures too, unlike fill-opacity 0, which still
  // hit-tests). See the n_affected filtering below for why "evaluated"
  // alone isn't the right bar.
  useEffect(() => {
    const map = mapRef.current;
    // Needed (unlike the buildings/debris feature-state effects above,
    // which only ever call setFeatureState) because this effect also
    // calls setFilter on MUNICIPALITIES_LAYER_ID -- that layer doesn't
    // exist until the map's "load" handler runs addLayer, so without this
    // guard a `results`/`municipalityStats` update landing before then
    // (e.g. this effect's own first run, since mapRef.current is already
    // set synchronously in the map-creation effect above, well before its
    // async "load" event fires) subscribes to the next "sourcedata" event
    // -- which can fire for the *basemap's own* sources first -- and
    // throws "Cannot filter non-existing layer" when it does. Same guard
    // the faults-filter effect below already uses for the same reason.
    if (!map || !mapLoadedRef.current) return;

    const applyMunicipalityFeatureState = () => {
      const target = { source: MUNICIPALITIES_SOURCE_ID, sourceLayer: "municipalities" };

      for (const code of loadedMunicipalityCodesRef.current) {
        map.removeFeatureState({ ...target, id: code });
      }
      loadedMunicipalityCodesRef.current = new Set();

      // Only municipalities with at least one actually-damaged building
      // (not just "inside the scenario's search radius") get shown --
      // engine.py's spatial pre-filter box is sized off the rupture's own
      // magnitude, not proximity to any particular municipality, so a big
      // enough earthquake can pull in and evaluate municipalities far from
      // the epicenter as confidently-undamaged (n_evaluated > 0, n_affected
      // == 0). Those aren't what "affected" means here.
      const affectedCodes: string[] = [];
      for (const stats of municipalityStats) {
        const nAffected = stats.n_evaluated - stats.counts.None;
        if (nAffected <= 0) continue;
        map.setFeatureState(
          { ...target, id: stats.municipality_code },
          { n_evaluated: stats.n_evaluated, n_affected: nAffected, mean_severity: meanSeverity(stats) }
        );
        loadedMunicipalityCodesRef.current.add(stats.municipality_code);
        affectedCodes.push(stats.municipality_code);
      }

      map.setFilter(
        MUNICIPALITIES_LAYER_ID,
        affectedCodes.length === 0
          ? NO_MUNICIPALITIES_FILTER
          : (["in", ["get", "ine_code"], ["literal", affectedCodes]] as maplibregl.FilterSpecification)
      );
    };

    if (map.isSourceLoaded(MUNICIPALITIES_SOURCE_ID)) {
      applyMunicipalityFeatureState();
    } else {
      map.once("sourcedata", applyMunicipalityFeatureState);
    }
  }, [municipalityStats]);

  // Everything else a scenario run changes: the fallback color for
  // buildings with no joined damage_state_code (green inside the evaluated
  // circle, grey outside it -- computed per-feature on the GPU via the
  // `distance` expression, so this scales with what's on screen, not with
  // how many buildings the search radius actually covers -- see
  // buildingsFillColor) and the viewport, framed to the evaluated circle so
  // "affected" buildings (green included) are in view without pulling in
  // unrelated grey ones.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;

    map.setPaintProperty(BUILDINGS_LAYER_ID, "fill-color", buildingsFillColor(evaluatedRegion));

    if (evaluatedRegion) {
      map.fitBounds(boundsFromRegion(evaluatedRegion), { padding: 48, maxZoom: 15, duration: 500 });
    }
  }, [evaluatedRegion]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      {zoom !== null && (
        <div
          style={{
            position: "absolute",
            top: "0.5rem",
            right: "0.5rem",
            padding: "0.15rem 0.4rem",
            background: "rgba(0,0,0,0.6)",
            color: "#fff",
            fontSize: "0.75rem",
            fontFamily: "monospace",
            borderRadius: 4,
            pointerEvents: "none",
          }}
        >
          z{zoom.toFixed(2)}
        </div>
      )}
    </div>
  );
}
