// app.js — the building map. Reads data/buildings.json (always) and
// data/availability.json (when it exists), draws one circle per building, and opens a
// sheet for the one you tap. Static: there is no backend.
//
// Map setup follows the sibling app (reference/pulse/js/app.js): compact attribution
// collapsed on load, a style-ready watchdog instead of waiting on 'load', the glass
// layer added last, and #ui revealed only once the map is drawing.

import { createGlassLayer } from '../kit/glass/host-maplibre.js?v=1.0.0';
import { STYLE_URL } from './mapstyle.js';
import {
  colourExpression, radiusExpression, toFeatures, countOnScreen, parseAvailability,
  availabilityState, unitsFor, screenSentence, coverageSentence, fmtInt, fmtDate,
  bedsLabel, rentLabel, safeUrl, googleMapsUrl, escapeHtml, scoreColour, SCORE_STOPS,
  NEVER_EVALUATED, AVAILABLE, LIT, DIM, isShown, towerColour, TOWER_UNLIT, dimsTheRest,
} from './logic.js';

const state = {
  buildings: [],
  byId: new Map(),
  managers: [],
  avail: null,              // null: no availability.json, or it did not parse
  layouts: {},              // building id -> { url, plans }: the landlord's page of floor plans
  view: { mode: 'private' },
  selected: null,
};
let map = null;
const glassLayer = createGlassLayer();

const $ = (id) => document.getElementById(id);

// ─────────────────────────────────────────────────────────── data

async function loadBuildings() {
  const r = await fetch('data/buildings.json', { cache: 'no-cache' });
  if (!r.ok) throw new Error(`buildings.json: HTTP ${r.status}`);
  return r.json();
}

// Missing, unreadable or malformed: every building is drawn at normal strength.
async function loadAvailability() {
  try {
    const r = await fetch('data/availability.json', { cache: 'no-cache' });
    if (!r.ok) return null;
    return parseAvailability(await r.text());
  } catch (e) {
    return null;
  }
}

// data/layouts.json (build/layouts.py): for a building, the landlord's own page that shows its
// floor plans, and how many it has. Optional. The map links to that page and shows no plan
// itself (photos and plans are stored, never shown here).
async function loadLayouts() {
  try {
    const r = await fetch('data/layouts.json', { cache: 'no-cache' });
    if (!r.ok) return {};
    return (await r.json()).buildings || {};
  } catch (e) {
    return {};
  }
}

// ─────────────────────────────────────────────────────────── map layers

// Two circle layers over one source. 'b' holds the buildings with no tower drawn; 'bt' holds
// the ones whose own footprint is drawn (property m). From MATCH_ZOOM in, 'bt' fades out and
// the towers fade in, over FADE_MS, and back again on the way out.
const A_OPACITY = ['match', ['get', 'a'], LIT, 1, DIM, 0.22, 0.82];
const A_STROKE_OPACITY = ['match', ['get', 'a'], DIM, 0.3, 1];

function paint() {
  return {
    'circle-radius': radiusExpression(),
    'circle-color': colourExpression(),
    'circle-opacity': A_OPACITY,
    'circle-stroke-color': ['match', ['get', 'a'], LIT, AVAILABLE, 'rgba(0,0,0,0.55)'],
    'circle-stroke-width': ['match', ['get', 'a'], LIT, 2.5, 0.6],
    'circle-stroke-opacity': A_STROKE_OPACITY,
    'circle-pitch-alignment': 'map',
  };
}

// The basemap's own footprints, extruded to their OpenStreetMap heights. Under the circles,
// so a building's dot is never hidden by its tower. Opaque on purpose, and warm-dark, both
// lessons from Pulse (reference/pulse/js/app.js, addBuildings). The basemap carries no
// buildings below zoom 13, and not every footprint has a height: those stay flat.
function addBuildings3d() {
  try {
    map.addLayer({
      id: 'building-3d', type: 'fill-extrusion', source: 'openmaptiles', 'source-layer': 'building',
      minzoom: 13,
      paint: {
        'fill-extrusion-color': TOWER_UNLIT,
        'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 0],
        'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
      },
    });
  } catch (e) { console.warn('3D buildings unavailable', e); }
}

// ─────────────────────────────────────────────────────────── lit towers
//
// The join between a registry building (a point) and its footprint (a polygon in the
// basemap's vector tiles) is made here, in the browser, for what is on screen: ask the
// flat 'building' layer for the footprint under the point, copy that one polygon into
// our own source, and extrude it in the score colour over the unlit one.
//
// Why a copy and not feature state on the basemap: at its deepest zoom the basemap merges
// every building in a tile that has the same height and colour into ONE multi-polygon
// feature. State set on a low-rise lights every house in the tile with it.
//
// Limits, both rare: a footprint cut by a tile edge lights only the piece under the
// point, and a tower mapped as several parts lights the outline plus the parts under it.
const MATCH_ZOOM = 14;       // below this the tiles carry simplified, block-merged shapes
const FADE_MS = 500;
const TOWER_ZOOM = 14.8;     // discs give way to towers here (Blair: "a bit more zoom" than 14)
const towers = {
  parts: new Map(),          // registry id -> [{ ring coordinates, h, base }]
  dirty: true,               // the view or the tiles changed since the last match
};

function inRing(pt, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

const polygonsOf = (f) => (f.geometry.type === 'Polygon' ? [f.geometry.coordinates]
  : f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates : []);

// Drawn a hair larger than the unlit footprint it covers, so the two walls never share
// a depth and flicker: 1% about the centre, and half a metre taller.
function swell(poly) {
  const o = poly[0];
  let cx = 0, cy = 0;
  for (const [x, y] of o) { cx += x; cy += y; }
  cx /= o.length; cy /= o.length;
  return poly.map((ring) => ring.map(([x, y]) => [cx + (x - cx) * 1.01, cy + (y - cy) * 1.01]));
}

function footprintsAt(b) {
  const pt = [b.lon, b.lat], p = map.project(pt), out = [];
  const part = (f, poly) => ({ poly: swell(poly), h: (f.properties.render_height || 0) + 0.5, base: f.properties.render_min_height || 0 });
  for (const f of map.queryRenderedFeatures(p, { layers: ['building'] })) {
    for (const poly of polygonsOf(f)) if (inRing(pt, poly[0])) out.push(part(f, poly));
  }
  if (out.length) return out;
  // Registry points are address points and can sit just outside the wall: take the
  // footprint with the nearest corner within 15 m.
  const k = Math.cos(b.lat * Math.PI / 180), mPerDeg = 111320;
  const px = 15 / (78271.5 * k / 2 ** map.getZoom());
  let best = null, bestD = 15 * 15;
  for (const f of map.queryRenderedFeatures([[p.x - px, p.y - px], [p.x + px, p.y + px]], { layers: ['building'] })) {
    for (const poly of polygonsOf(f)) for (const [x, y] of poly[0]) {
      const d = ((x - pt[0]) * k * mPerDeg) ** 2 + ((y - pt[1]) * mPerDeg) ** 2;
      if (d < bestD) { bestD = d; best = part(f, poly); }
    }
  }
  return best ? [best] : [];
}

function drawTowers() {
  const features = [];
  for (const [id, parts] of towers.parts) {
    const b = state.byId.get(id);
    if (!isShown(b, state.view)) continue;
    const c = state.selected === id ? '#ffffff' : towerColour(b.score, availabilityState(state.avail, id));
    for (const t of parts) features.push({ type: 'Feature', properties: { id, c, h: t.h, base: t.base },
      geometry: { type: 'Polygon', coordinates: t.poly } });
  }
  map.getSource('towers').setData({ type: 'FeatureCollection', features });
}

function matchTowers() {
  if (!towers.dirty || !map.getSource('towers') || map.getZoom() < MATCH_ZOOM) return;
  towers.dirty = false;
  const bb = map.getBounds();
  let found = 0;
  for (const b of state.buildings) {
    if (towers.parts.has(b.id) || !isShown(b, state.view)) continue;
    if (b.lon < bb.getWest() || b.lon > bb.getEast() || b.lat < bb.getSouth() || b.lat > bb.getNorth()) continue;
    const parts = footprintsAt(b);
    if (!parts.length) continue;        // not loaded yet, or no footprint: asked again next time
    towers.parts.set(b.id, parts);
    found++;
  }
  if (found) { drawTowers(); setCircles(); }
}

// k: the opacity of the discs whose tower is drawn. 1 zoomed out, 0 once the towers are up.
const fade = { k: 1, target: 1, raf: 0 };

function applyFade() {
  map.setPaintProperty('bt', 'circle-opacity', ['*', fade.k, A_OPACITY]);
  map.setPaintProperty('bt', 'circle-stroke-opacity', ['*', fade.k, A_STROKE_OPACITY]);
  map.setPaintProperty('ground', 'circle-opacity', 1 - fade.k);
  map.setPaintProperty('ground', 'circle-stroke-opacity', 1 - fade.k);
  const k = towers.parts.has(state.selected) ? fade.k : 1;     // the white disc goes with its tower
  map.setPaintProperty('sel', 'circle-opacity', k);
  map.setPaintProperty('sel', 'circle-stroke-opacity', k);
}

function setTowerMode(on) {
  const target = on ? 0 : 1;
  if (target === fade.target) return;
  fade.target = target;
  map.setPaintProperty('towers-3d', 'fill-extrusion-opacity', on ? 1 : 0);   // MapLibre runs this one
  cancelAnimationFrame(fade.raf);
  const from = fade.k, t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / FADE_MS);
    fade.k = from + (target - from) * t;
    applyFade();
    if (t < 1) fade.raf = requestAnimationFrame(step);
  };
  fade.raf = requestAnimationFrame(step);
}

const circleData = () => toFeatures(state.buildings, state.view, state.avail, new Set(towers.parts.keys()));
function setCircles() { map.getSource('b').setData(circleData()); }

function addTowers() {
  if (!map.getLayer('building-3d')) return;
  map.addSource('towers', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: 'towers-3d', type: 'fill-extrusion', source: 'towers', minzoom: MATCH_ZOOM - 1,
    paint: {
      'fill-extrusion-opacity': 0,                                   // setTowerMode fades it
      'fill-extrusion-opacity-transition': { duration: FADE_MS, delay: 0 },
      'fill-extrusion-color': ['to-color', ['get', 'c']],
      'fill-extrusion-height': ['get', 'h'],
      'fill-extrusion-base': ['get', 'base'],
    },
  });
}

function addLayers() {
  addBuildings3d();
  addTowers();
  map.addSource('b', { type: 'geojson', data: circleData() });
  const layout = { 'circle-sort-key': ['get', 'k'] };   // lit on top, then small over large
  map.addLayer({ id: 'b', type: 'circle', source: 'b', layout, paint: paint(), filter: ['!=', ['get', 'm'], 1] });
  map.addLayer({ id: 'bt', type: 'circle', source: 'b', layout, paint: paint(), filter: ['==', ['get', 'm'], 1] });
  // Units available here, once the towers are up: the same disc with its blue ring, drawn
  // UNDER the extrusion layers. MapLibre paints layers in order, so every building drawn
  // after it covers the part of the disc behind it, and the tower stands on its disc
  // (the sun app's ground lines are occluded the same way).
  map.addLayer({
    id: 'ground', type: 'circle', source: 'b', layout,
    filter: ['all', ['==', ['get', 'a'], LIT], ['==', ['get', 'm'], 1]],
    paint: { ...paint(), 'circle-radius': radiusExpression(3), 'circle-opacity': 0, 'circle-stroke-opacity': 0 },
  }, map.getLayer('building-3d') ? 'building-3d' : undefined);
  // The selected building: white, as a disc here and as a tower in drawTowers.
  map.addLayer({
    id: 'sel', type: 'circle', source: 'b',
    filter: ['==', ['get', 'id'], -1],
    paint: {
      'circle-radius': radiusExpression(1),
      'circle-color': '#ffffff',
      'circle-stroke-color': 'rgba(0,0,0,0.55)',
      'circle-stroke-width': 1,
      'circle-pitch-alignment': 'map',
    },
  });
}

// Every change to what is shown comes through here: the mode now, filters later.
function refresh() {
  if (map && map.getSource('b')) setCircles();
  if (map && map.getSource('towers')) { drawTowers(); towers.dirty = true; matchTowers(); }
  updateStatus();
}

// ─────────────────────────────────────────────────────────── status line

function updateStatus() {}   // the status line went with the top pane (Blair, 2026-10-10)

// ─────────────────────────────────────────────────────────── legend

function renderLegend() {
  const lo = SCORE_STOPS[0][0], hi = SCORE_STOPS[SCORE_STOPS.length - 1][0];
  const grad = SCORE_STOPS.map(([s, c]) => `${c} ${(((s - lo) / (hi - lo)) * 100).toFixed(1)}%`).join(', ');
  $('legend-body').innerHTML = `
    <div class="e-label">Inspection score</div>
    <div class="ramp" style="background: linear-gradient(90deg, ${grad})"></div>
    <div class="ramp-ticks e-caption e-num"><span>${lo}</span><span>${hi}</span></div>`;
}

// ─────────────────────────────────────────────────────────── the sheet

const yesNo = (v) => (v === true ? 'Yes' : v === false ? 'No' : null);
const AC = { none: 'No air conditioning', units: 'Air conditioning in units', central: 'Central air' };
const PARKING = { surface: 'Surface', underground: 'Underground garage', garage: 'Ground level garage',
  indoor_access: 'Garage through the building', carport: 'Carport', deck: 'Parking deck' };
const VISITOR = { free: 'Free visitor parking', paid: 'Paid visitor parking', both: 'Free and paid visitor parking',
  unavailable: 'No visitor parking' };

function row(label, value) {
  if (value === null || value === undefined || value === '') return '';
  return `<dt>${label}</dt><dd>${escapeHtml(value)}</dd>`;
}

function unitsHtml(b) {
  if (!state.avail) return '';
  const st = availabilityState(state.avail, b.id);
  if (st === DIM) return '<p class="e-caption avail-note">No availability source covers this building yet.</p>';
  const units = unitsFor(state.avail, b.id) || [];
  if (!units.length) return '<p class="e-caption avail-note">Covered: nothing listed as available today.</p>';
  const items = units.map((u) => {
    const links = (u.links || []).map((l) => {
      const url = safeUrl(l.url);
      return url ? `<a class="e-btn src" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(l.source || 'Listing')}</a>` : '';
    }).join('');
    const when = u.available ? `From ${fmtDate(u.available)}` : '';
    return `<li>
      <span class="u-beds">${escapeHtml(bedsLabel(u.beds))}</span>
      <span class="u-rent e-num">${escapeHtml(rentLabel(u))}</span>
      <span class="u-when e-caption">${escapeHtml(when)}</span>
      <span class="u-links">${links}</span>
    </li>`;
  }).join('');
  // A "from $X" row is a suite type at some landlords and one unit at others (Tricon), and
  // the data does not say which: call those listings. Exact-rent rows are units.
  const floors = units.every((u) => u.rent_is_floor);
  const noun = floors ? (units.length === 1 ? 'listing' : 'listings') : (units.length === 1 ? 'unit' : 'units');
  return `<div class="e-label avail-head">${units.length} ${noun} available</div><ul class="units">${items}</ul>`;
}

function select(id) {
  const was = state.selected;
  state.selected = id;
  if (was !== id && map && map.getSource('towers')) {
    if (towers.parts.has(was) || towers.parts.has(id)) drawTowers();
    applyFade();
  }
}

function openSheet(b) {
  select(b.id);
  map.setFilter('sel', ['==', ['get', 'id'], b.id]);
  const f = b.flags || {};
  const score = b.score === null || b.score === undefined
    ? 'Never evaluated' : `${b.score} out of 100${b.score_year ? ` (${b.score_year})` : ''}`;
  const mgr = b.mgr === null || b.mgr === undefined ? null : state.managers[b.mgr];
  const parking = (b.parking || []).map((p) => PARKING[p] || p).join(', ');
  const lay = state.layouts[b.id], layUrl = lay && safeUrl(lay.url);
  const layoutsBtn = layUrl ? `<a class="e-btn" id="layouts" href="${escapeHtml(layUrl)}" target="_blank" rel="noopener noreferrer">${lay.plans === 1 ? '1 layout' : `${fmtInt(lay.plans)} layouts`} on the landlord's site</a>` : '';
  $('sheet-body').innerHTML = `
    <h2 class="e-title">${escapeHtml(b.addr)}</h2>
    ${unitsHtml(b)}
    <dl class="facts">
      ${row('Units', b.units !== undefined ? fmtInt(b.units) : null)}
      ${row('Storeys', b.storeys)}
      ${row('Built', b.year)}
      ${row('Manager', mgr)}
      ${row('Inspection', score)}
      ${row('Parking', parking)}
      ${row('Visitors', VISITOR[f.visitor_parking])}
      ${row('Balconies', yesNo(f.balconies))}
      ${row('Pets', f.pets === true ? 'Allowed' : f.pets === false ? 'Not allowed' : null)}
      ${row('Laundry', f.laundry === true ? 'Laundry room' : f.laundry === false ? 'No laundry room' : null)}
      ${row('Cooling', AC[f.ac])}
      ${row('Elevators', f.elevators)}
      ${row('Smoking', f.nonsmoking === true ? 'Non-smoking building' : f.nonsmoking === false ? 'Allowed' : null)}
    </dl>
    ${layoutsBtn}
    <a class="e-btn" id="gmaps" href="${googleMapsUrl(b)}" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>`;
  $('sheet-body').scrollTop = 0;
  $('sheet').hidden = false;
  document.body.classList.add('sheet-open');
  $('bottom').hidden = true;
  window.EmberUI && window.EmberUI.skin($('sheet'));
  map.triggerRepaint();      // the glass is measured per frame; draw one with the sheet in it
}

function closeSheet() {
  select(null);
  if (map) map.setFilter('sel', ['==', ['get', 'id'], -1]);
  $('sheet').hidden = true;
  document.body.classList.remove('sheet-open');
  $('bottom').hidden = false;
  if (map) map.triggerRepaint();
}

// A finger is not a cursor: look in a box around the tap and take the nearest circle.
function onTap(e) {
  const p = e.point, pad = 14;
  // A lit tower first: the tap lands on its wall or roof, not on the ground point.
  if (map.getLayer('towers-3d')) {
    for (const h of map.queryRenderedFeatures(p, { layers: ['towers-3d'] })) {
      const b = state.byId.get(h.properties.id);
      if (b && isShown(b, state.view)) { openSheet(b); return; }
    }
  }
  const layers = fade.k > 0.5 ? ['b', 'bt'] : ['b', 'ground'];      // a faded-out disc is not a target
  const hits = map.queryRenderedFeatures([[p.x - pad, p.y - pad], [p.x + pad, p.y + pad]], { layers });
  if (!hits.length) { closeSheet(); return; }
  let best = null, bestD = Infinity;
  for (const h of hits) {
    const q = map.project(h.geometry.coordinates);
    const d = (q.x - p.x) ** 2 + (q.y - p.y) ** 2;
    if (d < bestD) { bestD = d; best = h; }
  }
  const b = state.byId.get(best.properties.id);
  if (b) openSheet(b);
}

// ─────────────────────────────────────────────────────────── camera
//
// The map opens on one neighbourhood at the widest zoom that still shows buildings, behind
// the loading screen until they are drawn. Then one small move: about 20% closer, 15
// degrees of turn and 10 of tilt, ending on Toronto's street grid (17 degrees west of
// north). Enough to say that zoom, rotation and tilt all move. Any touch stops it.
const HOME = { center: [-79.3745, 43.6680], zoom: 15.11, pitch: 55, bearing: -17 };
const START = { center: HOME.center, zoom: 14.85, pitch: HOME.pitch - 10, bearing: HOME.bearing - 15 };   // just past TOWER_ZOOM; 2^0.26 = 1.2
const INTRO_MS = 2600;

function intro() {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { map.jumpTo(HOME); return; }
  setTimeout(() => map.easeTo({ ...HOME, duration: INTRO_MS, essential: true,
    easing: (t) => (t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2) }), 700);
}

// Resolves once the opening view is fully drawn: basemap tiles in, registry buildings
// matched to their footprints and extruded. Never waits past 10 seconds.
function buildingsDrawn() {
  return new Promise((res) => {
    let done = false;
    const go = () => { if (!done) { done = true; res(); } };
    setTimeout(go, 10000);
    map.once('idle', () => {            // tiles are in, and matchTowers (an earlier idle handler) has run
      map.once('idle', () => setTimeout(go, FADE_MS));      // the towers' own first frame, then their fade
      setTimeout(go, 2500);             // nothing matched here: no second idle will come
      map.triggerRepaint();
    });
  });
}

// Two fingers moved up or down together tilt the map, however the fingers sit relative
// to each other. MapLibre's own gesture (touchPitch) only accepts fingers side by side,
// so it is switched off and this replaces it. Fingers moving apart, together or around
// each other are still MapLibre's pinch and rotate.
function twoFingerTilt() {
  map.touchPitch.disable();
  const el = map.getCanvasContainer();
  let g = null;
  const read = (e) => new Map([...e.touches].map((t) => [t.identifier, { x: t.clientX, y: t.clientY }]));
  const release = () => {
    if (g && g.tilting) { map.touchZoomRotate.enable(); map.dragPan.enable(); }
    g = null;
  };
  el.addEventListener('touchstart', (e) => {
    release();
    if (e.touches.length === 2) g = { start: read(e), pitch: map.getPitch(), decided: false, tilting: false };
  }, { capture: true, passive: true });
  el.addEventListener('touchmove', (e) => {
    if (!g || e.touches.length !== 2) return;
    const now = read(e), d = [];
    for (const [id, p] of now) { const s = g.start.get(id); if (s) d.push({ x: p.x - s.x, y: p.y - s.y }); }
    if (d.length !== 2) return;
    if (!g.decided) {
      if (Math.max(Math.hypot(d[0].x, d[0].y), Math.hypot(d[1].x, d[1].y)) < 12) return;
      g.decided = true;
      const ay = d.map((v) => Math.abs(v.y));
      g.tilting = d[0].y * d[1].y > 0                              // the same way, up or down
        && Math.min(...ay) >= 0.5 * Math.max(...ay)                // about as far as each other
        && d.every((v) => Math.abs(v.y) > Math.abs(v.x));          // more vertical than sideways
      if (g.tilting) { map.stop(); map.touchZoomRotate.disable(); map.dragPan.disable(); }
    }
    if (g.tilting) map.setPitch(Math.max(0, Math.min(map.getMaxPitch(), g.pitch - ((d[0].y + d[1].y) / 2) * 0.5)));
  }, { capture: true, passive: true });
  for (const ev of ['touchend', 'touchcancel']) el.addEventListener(ev, release, { capture: true, passive: true });
}

// The compass needle turns with the map; a tap turns the map north up.
function compass() {
  const turn = () => $('needle').setAttribute('transform', `rotate(${-map.getBearing()})`);
  map.on('rotate', turn);
  turn();
  $('compass').addEventListener('click', () => map.easeTo({ bearing: 0, duration: 500 }));
}

// The tilt stick. Pull the knob up to tilt the map over, down to stand it back up; the
// further it is pulled, the faster it goes, up to STICK_MAX degrees a second. Let go and
// the knob springs back and the map stays where it is.
const STICK_TRAVEL = 22;     // px the knob can leave centre
const STICK_MAX = 50;        // degrees of tilt per second at full pull
function tiltStick() {
  const el = $('stick'), knob = $('stick-knob');
  let pull = 0, y0 = 0, raf = 0, last = 0;
  const frame = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (Math.abs(pull) > 0.08) {      // a small dead zone at the centre
      map.setPitch(Math.max(0, Math.min(map.getMaxPitch(), map.getPitch() + pull * STICK_MAX * dt)));
    }
    raf = requestAnimationFrame(frame);
  };
  const set = (dy) => {
    const d = Math.max(-STICK_TRAVEL, Math.min(STICK_TRAVEL, dy));
    pull = -d / STICK_TRAVEL;         // up is positive: more tilt
    knob.style.transform = `translateY(${d}px)`;
    map.triggerRepaint();
  };
  const stop = () => {
    cancelAnimationFrame(raf); raf = 0; el.classList.remove('held'); set(0);
    // The glass knob is drawn in the map's canvas: keep drawing while it springs back.
    const until = performance.now() + 300;
    const settle = () => { map.triggerRepaint(); if (performance.now() < until) requestAnimationFrame(settle); };
    settle();
  };
  el.addEventListener('pointerdown', (e) => {
    e.preventDefault(); el.setPointerCapture(e.pointerId); el.classList.add('held');
    map.stop();
    const r = el.getBoundingClientRect(); y0 = r.top + r.height / 2;
    set(e.clientY - y0); last = performance.now(); raf = requestAnimationFrame(frame);
  });
  el.addEventListener('pointermove', (e) => { if (raf) set(e.clientY - y0); });
  for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) el.addEventListener(ev, stop);
  el.addEventListener('keydown', (e) => {
    const step = e.key === 'ArrowUp' ? 5 : e.key === 'ArrowDown' ? -5 : 0;
    if (step) { e.preventDefault(); map.setPitch(Math.max(0, Math.min(map.getMaxPitch(), map.getPitch() + step))); }
  });
}

// ─────────────────────────────────────────────────────────── boot

async function boot() {
  const status = $('status');
  try {
    const [doc, avail, layouts] = await Promise.all([loadBuildings(), loadAvailability(), loadLayouts()]);
    state.buildings = doc.buildings || [];
    state.managers = doc.managers || [];
    state.avail = avail;
    state.layouts = layouts;
    for (const b of state.buildings) state.byId.set(b.id, b);
    renderLegend();

    map = new maplibregl.Map({
      container: 'map',
      style: STYLE_URL,
      ...START,
      minZoom: 9,
      maxZoom: 18,
      maxBounds: [[-80.2, 43.3], [-78.7, 44.15]],
      maxPitch: 70,      // tilt: two fingers dragged up or down; right-drag or ctrl-drag with a mouse
      attributionControl: false,
      // Smooth edges on the raised towers, and no more than 2 device px per CSS px: on a 3x
      // phone that is under half the pixels per frame, which pays for the multisampling.
      // Same setting as the Sun app (since 2026-09-15) and Pulse. ?aa=0 and ?dpr=3 to compare.
      antialias: !/[?&]aa=0\b/.test(location.search),
      pixelRatio: Math.min(devicePixelRatio || 1, +((/[?&]dpr=([\d.]+)/.exec(location.search) || [])[1]) || 2),
    });

    // Credits top right: the bottom of the screen belongs to the pane now.
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: 'Buildings: <a href="https://open.toronto.ca/">City of Toronto Open Data</a>, Open Government Licence – Toronto' }), 'top-right');

    // MapLibre mounts the compact attribution EXPANDED, a strip of page text along the
    // bottom. Collapse it; the (i) keeps the credits one tap away (as Pulse does).
    const deWeb = () => document.querySelectorAll('.maplibregl-ctrl-attrib.maplibregl-compact-show')
      .forEach((el) => el.classList.remove('maplibregl-compact-show'));
    deWeb(); setTimeout(deWeb, 400); setTimeout(deWeb, 1500);

    // 'load' waits on the style AND the first tiles, so a slow connection can hold it
    // forever. Go once the style is ready (the sun app's v90 fix, via Pulse).
    await new Promise((res) => {
      let done = false;
      const go = () => { if (!done) { done = true; res(); } };
      if (map.isStyleLoaded()) go();
      map.once('load', go);
      map.once('styledata', () => setTimeout(go, 250));
      setTimeout(go, 6000);
    });

    addLayers();
    // Glass LAST: it copies the frame everything above drew and refracts it.
    try { map.addLayer(glassLayer); } catch (e) { console.warn('glass unavailable', e); }

    map.on('click', onTap);
    map.on('moveend', updateStatus);
    map.on('moveend', () => { towers.dirty = true; });
    map.on('sourcedata', (ev) => { if (ev.sourceId === 'openmaptiles') towers.dirty = true; });
    map.on('idle', matchTowers);
    map.on('zoom', () => setTowerMode(map.getZoom() >= TOWER_ZOOM));
    twoFingerTilt();
    compass();
    tiltStick();
    updateStatus();

    window.__apt = { map, state, openSheet };   // for poking at it from the console, as Pulse's __pulse

    // The opening view is past TOWER_ZOOM and no zoom event has fired yet: go straight to
    // towers, then hold the loading screen until they are on screen.
    if (map.getZoom() >= TOWER_ZOOM) {
      fade.k = 0; fade.target = 0;
      map.setPaintProperty('towers-3d', 'fill-extrusion-opacity', 1);
      applyFade();
    }
    await buildingsDrawn();

    status.classList.add('gone');
    $('ui').classList.add('ready');
    map.triggerRepaint();
    intro();
  } catch (e) {
    status.textContent = 'Could not load the map: ' + e.message;
    console.error(e);
  }
}

$('mode').addEventListener('change', (e) => {
  state.view.mode = e.detail.value === 'all' ? 'all' : 'private';
  if (state.selected !== null) {
    const b = state.byId.get(state.selected);
    if (b && !isShown(b, state.view)) closeSheet();
  }
  refresh();
});
$('close').addEventListener('click', closeSheet);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });

boot();
