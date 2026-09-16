import { useEffect, useMemo, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MapLibreMap, MapLayerMouseEvent } from "maplibre-gl";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { DAMAGE_COLORS, DAMAGE_STATES } from "../damageColors";
import {
  getBuildingInfo,
  type BuildingDamageResult,
  type EvaluatedRegion,
  type Fault,
} from "../scenarioApi";

// Precomputed-tiling architecture (docs/decisions/0003-precomputed-building-tiles.md):
// building geometry is a static PMTiles layer, tiled once offline by the
// exposure pipeline. Scenario results never touch tiling -- we join the
// thin per-building result onto this static layer at render time via
// MapLibre's setFeatureState, keyed by building_id (promoted as the tile
// feature id below).
const BUILDINGS_PMTILES_URL =
  import.meta.env.VITE_BUILDINGS_PMTILES_URL ?? "/data/buildings.pmtiles";

const BUILDINGS_SOURCE_ID = "buildings";
const BUILDINGS_LAYER_ID = "buildings-fill";

// Debris envelopes (ADR-0010, docs/decisions/0010-debris-envelope-precompute.md):
// precomputed offline, one PMTiles layer per building's 1-4m rings, same
// static-tiling pattern as buildings above. A scenario run computes nothing
// new for this layer -- it just sets each building's damage_state_code as
// feature-state (below), same as the buildings layer, and the paint
// expression shows only the rings at or under that code.
const DEBRIS_PMTILES_URL = import.meta.env.VITE_DEBRIS_PMTILES_URL ?? "/data/debris.pmtiles";
const DEBRIS_SOURCE_ID = "debris";
const DEBRIS_LAYER_ID = "debris-fill";
// A neutral tone independent of DAMAGE_COLORS on purpose -- debris is a
// separate concept from a building's own damage color (matching MERISUR's
// own separate "Load debris on map" toggle, docs/merisur.md §5), not a
// restatement of it. Opacity instead falls off per ring (closest to the
// building is most opaque) to hint at "denser near the façade."
const DEBRIS_COLOR = "#5c4433";
const DEBRIS_RING_OPACITY: maplibregl.ExpressionSpecification = [
  "match",
  ["get", "ring"],
  1,
  0.55,
  2,
  0.45,
  3,
  0.35,
  4,
  0.25,
  0,
];

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
  // Debris (ADR-0010) is opt-in, off by default -- it's a static PMTiles
  // layer (no per-request compute, unlike MERISUR's own "heavier" debris
  // step) but visually busy on top of the damage layer, so a toggle keeps
  // it out of the way until asked for, matching MERISUR's own separate
  // "Load debris on map" action (docs/merisur.md §5).
  showDebris: boolean;
}

// The API sends `damage_state_code`, the index into this same
// DAMAGE_STATES ordering (services/scenario/response.py), not a string --
// decode it back to a label/color here rather than shipping the string
// itself over the wire on every one of a few hundred thousand rows.
function damageStateLabel(code: number): string {
  return DAMAGE_STATES[code] ?? "Unknown";
}

function escapeHtml(value: unknown): string {
  return String(value ?? "—").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string
  );
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
    ["Cadastral ID", escapeHtml(tileProps.building_id)],
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
  if (damage) {
    // This building was individually returned by the API -- either
    // actually damaged, or "None"-modal but still meaningfully uncertain
    // (a genuine close call against the runner-up damage state, see
    // scenarioApi.ts) -- either way we have its real probability
    // breakdown, unlike the not-a-close-call case below.
    const label = damageStateLabel(damage.damage_state_code);
    rows.push(["Predicted damage", `<strong>${escapeHtml(label)}</strong>`]);
    rows.push([
      "Damage probabilities",
      `None ${(damage.prob_none * 100).toFixed(0)}% · Slight ${(damage.prob_slight * 100).toFixed(0)}% · ` +
        `Moderate ${(damage.prob_moderate * 100).toFixed(0)}% · Extensive ${(damage.prob_extensive * 100).toFixed(0)}% · ` +
        `Complete ${(damage.prob_complete * 100).toFixed(0)}%`,
    ]);
  } else if (withinEvaluatedRegion) {
    // Inside the evaluated circle, but not individually listed -- the API
    // omitted it for being "None"-modal without a genuine close call
    // against another damage state, so no per-building probabilities are
    // available, just the summary.
    rows.push(["Predicted damage", "Likely None (not individually evaluated)"]);
  } else {
    // Outside the evaluated circle (or no scenario has run at all) --
    // distinct from the confidently-undamaged case above: this building
    // was never assessed one way or the other.
    rows.push(["Predicted damage", "not evaluated"]);
  }

  const body = rows
    .map(
      ([label, value]) =>
        `<div style="margin-bottom:2px"><span style="color:#666">${label}:</span> ${value}</div>`
    )
    .join("");
  return `<div style="font-size:0.8rem; max-width:16rem">${body}</div>`;
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
  evaluatedRegion,
  faults,
  selectedFaultId,
  onFaultClick,
  onMapClick,
  onMapMove,
  showDebris,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const mapLoadedRef = useRef(false);
  const loadedBuildingIdsRef = useRef<Set<string>>(new Set());
  const loadedDebrisBuildingIdsRef = useRef<Set<string>>(new Set());
  // Click/hover/move handlers are bound once (map.on is idempotent-unfriendly
  // to re-bind per render) but need the latest callback -- a ref sidesteps
  // stale closures without re-registering listeners on every render.
  const onFaultClickRef = useRef(onFaultClick);
  onFaultClickRef.current = onFaultClick;
  const onMapClickRef = useRef(onMapClick);
  onMapClickRef.current = onMapClick;
  const onMapMoveRef = useRef(onMapMove);
  onMapMoveRef.current = onMapMove;
  // Building-click popup needs the latest scenario results (and the
  // region they were evaluated against) to classify a clicked building,
  // without re-binding the click handler.
  const resultsRef = useRef(results);
  resultsRef.current = results;
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
    });
    mapRef.current = map;

    map.on("load", () => {
      map.addSource(BUILDINGS_SOURCE_ID, {
        type: "vector",
        url: `pmtiles://${BUILDINGS_PMTILES_URL}`,
        promoteId: "building_id",
      });
      map.addLayer({
        id: BUILDINGS_LAYER_ID,
        type: "fill",
        source: BUILDINGS_SOURCE_ID,
        "source-layer": "buildings",
        paint: {
          // Grey ("Unknown") until a scenario has run -- updated to the
          // evaluated-region-aware expression below once one has (see the
          // evaluatedRegion effect). A per-building "color" feature-state
          // (damaged, or uncertain-None) always wins when set.
          "fill-color": ["coalesce", ["feature-state", "color"], DAMAGE_COLORS.Unknown],
          "fill-opacity": 0.85,
          "fill-outline-color": "#00000033",
        },
      });

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
        layout: { visibility: showDebris ? "visible" : "none" },
        paint: {
          "fill-color": DEBRIS_COLOR,
          // A ring only renders once its building's damage state has
          // reached/passed that ring's number (ring 1 = Slight, ADR-0010) --
          // ["feature-state", "damage_state_code"] is unset (null) for any
          // building no scenario has touched yet, so `coalesce` to -1
          // keeps every ring hidden by default rather than comparing
          // against null.
          "fill-opacity": [
            "case",
            ["<=", ["get", "ring"], ["coalesce", ["feature-state", "damage_state_code"], -1]],
            DEBRIS_RING_OPACITY,
            0,
          ],
          "fill-outline-color": "#00000022",
        },
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

      // Building click popup: floors/construction year/use/cadastral id
      // come straight off the clicked tile feature (no request needed);
      // taxonomy/height class need a /buildings/{id} lookup, and damage
      // comes from whatever scenario has already been run (resultsRef).
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

        const damage =
          resultsRef.current?.find((b) => b.building_id === buildingId) ?? null;
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

      // General map click (manual mode's "click to set lat/lon") -- skips
      // clicks that landed on a fault line (handled above) or a building
      // (handled by the popup click handler below) so one click doesn't
      // trigger two different behaviors at once.
      map.on("click", (e: MapLayerMouseEvent) => {
        const hits = map.queryRenderedFeatures(e.point, {
          layers: [FAULTS_LAYER_ID, BUILDINGS_LAYER_ID],
        });
        if (hits.length === 0) {
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

  // Per-building coloring: only the buildings the API actually returned
  // (damaged, or uncertain-None) get an explicit feature-state -- the
  // confidently-undamaged majority is deliberately absent (see
  // scenarioApi.ts) and picks up its color from the evaluated-region
  // paint expression below instead, never from a per-building call.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const applyFeatureState = () => {
      // Vector sources require sourceLayer on every feature-state call --
      // omitting it fails silently-ish (throws, caught nowhere, leaving
      // every building on the fallback color) rather than erroring loudly
      // in the UI.
      const target = { source: BUILDINGS_SOURCE_ID, sourceLayer: "buildings" };

      // Clear previous run's coloring first, so damaged buildings from a
      // smaller/differently-located scenario don't stay colored.
      for (const id of loadedBuildingIdsRef.current) {
        map.removeFeatureState({ ...target, id });
      }
      loadedBuildingIdsRef.current = new Set();

      for (const building of results ?? []) {
        const color = DAMAGE_COLORS[damageStateLabel(building.damage_state_code)] ?? DAMAGE_COLORS.Unknown;
        map.setFeatureState({ ...target, id: building.building_id }, { color });
        loadedBuildingIdsRef.current.add(building.building_id);
      }
    };

    if (map.isSourceLoaded(BUILDINGS_SOURCE_ID)) {
      applyFeatureState();
    } else {
      map.once("sourcedata", applyFeatureState);
    }
  }, [results]);

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

    const applyDebrisFeatureState = () => {
      const target = { source: DEBRIS_SOURCE_ID, sourceLayer: "debris" };

      for (const id of loadedDebrisBuildingIdsRef.current) {
        map.removeFeatureState({ ...target, id });
      }
      loadedDebrisBuildingIdsRef.current = new Set();

      for (const building of results ?? []) {
        map.setFeatureState(
          { ...target, id: building.building_id },
          { damage_state_code: building.damage_state_code }
        );
        loadedDebrisBuildingIdsRef.current.add(building.building_id);
      }
    };

    if (map.isSourceLoaded(DEBRIS_SOURCE_ID)) {
      applyDebrisFeatureState();
    } else {
      map.once("sourcedata", applyDebrisFeatureState);
    }
  }, [results]);

  // Debris layer visibility toggle -- off by default (see Props.showDebris).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;
    map.setLayoutProperty(DEBRIS_LAYER_ID, "visibility", showDebris ? "visible" : "none");
  }, [showDebris]);

  // Everything else a scenario run changes: the fallback color for
  // buildings *not* individually listed (green inside the evaluated
  // circle, grey outside it -- computed per-feature on the GPU via the
  // `distance` expression, so this scales with what's on screen, not with
  // how many buildings the search radius actually covers) and the
  // viewport, framed to the evaluated circle so "affected" buildings
  // (green included) are in view without pulling in unrelated grey ones.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoadedRef.current) return;

    if (!evaluatedRegion) {
      map.setPaintProperty(BUILDINGS_LAYER_ID, "fill-color", [
        "coalesce",
        ["feature-state", "color"],
        DAMAGE_COLORS.Unknown,
      ]);
      return;
    }

    map.setPaintProperty(BUILDINGS_LAYER_ID, "fill-color", [
      "coalesce",
      ["feature-state", "color"],
      [
        "case",
        [
          "<",
          ["distance", { type: "Point", coordinates: [evaluatedRegion.lon, evaluatedRegion.lat] }],
          evaluatedRegion.radius_km * 1000,
        ],
        DAMAGE_COLORS.None,
        DAMAGE_COLORS.Unknown,
      ],
    ] as maplibregl.ExpressionSpecification);

    map.fitBounds(boundsFromRegion(evaluatedRegion), { padding: 48, maxZoom: 15, duration: 500 });
  }, [evaluatedRegion]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
