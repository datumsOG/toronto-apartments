// logic.js — everything the map decides that is not drawing: colours, sizes, lit and
// dim, which buildings are shown, the words in the status line. No DOM and no map
// imports, so tests/js/logic.test.mjs runs it under plain `node --test`.
//
// The map layers are built from the same tables as the functions below, so what the
// tests check is what the map draws.

// ── inspection score -> colour ───────────────────────────────────────────────
// Red to green, green is better (Blair, 2026-10-10: "green - yellow - orange - red. I want
// bright colours"). This overrides the kit's habit of reserving red and green for
// emptying and filling: on this map they mean worse and better. Below 70 everything is
// the red stop.
export const SCORE_STOPS = [
  [70, '#ff3b30'],
  [80, '#ff9500'],
  [90, '#ffe100'],
  [100, '#1ee65a'],
];
// Units available here (Blair, 2026-10-10: "instead of white ... use primary blue"). White is
// kept for the selected building alone.
export const AVAILABLE = '#1e6bff';
export const NEVER_EVALUATED = '#8e9399';   // a cool grey: no hue, so it reads as "no data"

const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgb2hex = (c) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

// Linear in sRGB between stops, the same interpolation MapLibre's expression does.
export function scoreColour(score) {
  if (score === null || score === undefined || !Number.isFinite(score)) return NEVER_EVALUATED;
  const s = SCORE_STOPS;
  if (score <= s[0][0]) return s[0][1];
  if (score >= s[s.length - 1][0]) return s[s.length - 1][1];
  for (let i = 1; i < s.length; i++) {
    if (score <= s[i][0]) {
      const [x0, c0] = s[i - 1], [x1, c1] = s[i];
      const t = (score - x0) / (x1 - x0);
      const a = hex2rgb(c0), b = hex2rgb(c1);
      return rgb2hex(a.map((v, k) => v + (b[k] - v) * t));
    }
  }
  return s[s.length - 1][1];
}

export function colourExpression() {
  return ['case', ['has', 'score'],
    ['interpolate', ['linear'], ['get', 'score'], ...SCORE_STOPS.flat()],
    NEVER_EVALUATED];
}

// ── units -> radius ──────────────────────────────────────────────────────────
// Area proportional to units, so the radius goes with the square root. The factor
// grows with zoom so the city view is not a carpet and the street view is tappable.
// [zoom, pixels of radius per sqrt(unit)]
// Halved on 2026-10-10 (Blair): the big discs are half the diameter they were, the
// minimum is unchanged.
export const RADIUS_STOPS = [[9, 0.11], [11, 0.21], [13, 0.45], [15, 0.95], [17, 1.8]];
export const MIN_RADIUS = 1.6;    // a 0-unit or 3-unit building is still a visible dot

export const unitsScale = (units) => Math.sqrt(Math.max(0, Number(units) || 0));

export function unitsRadius(units, zoom) {
  const s = RADIUS_STOPS;
  let k;
  if (zoom <= s[0][0]) k = s[0][1];
  else if (zoom >= s[s.length - 1][0]) k = s[s.length - 1][1];
  else {
    for (let i = 1; i < s.length; i++) {
      if (zoom <= s[i][0]) {
        const t = (zoom - s[i - 1][0]) / (s[i][0] - s[i - 1][0]);
        k = s[i - 1][1] + (s[i][1] - s[i - 1][1]) * t;
        break;
      }
    }
  }
  return Math.max(MIN_RADIUS, k * unitsScale(units));
}

// Feature property `s` carries unitsScale(units), precomputed once. `extra` pixels are
// added inside the expression (the selection ring), because MapLibre only accepts
// ['zoom'] in a top-level interpolate.
export function radiusExpression(extra = 0) {
  return ['interpolate', ['linear'], ['zoom'],
    ...RADIUS_STOPS.flatMap(([z, k]) => [z, ['+', extra, ['max', MIN_RADIUS, ['*', k, ['get', 's']]]]])];
}

// ── lit and dim ──────────────────────────────────────────────────────────────
// availability.json (docs/CONTRACT.md section 3) is optional. Without it every building
// is drawn at normal strength. With it:
//   lit     listed, with at least one unit available
//   normal  listed with no units ("we looked, nothing available")
//   dim     absent from the file ("we cannot see this building")
export const LIT = 2, NORMAL = 1, DIM = 0;

// The colour of a building's own 3D footprint: its score colour, pulled most of the way
// to the unlit tower colour when it is DIM, so lit and normal towers stand out the same
// way lit and normal circles do.
export const TOWER_UNLIT = '#2b2a27';
export function towerColour(score, a) {
  const c = scoreColour(score);
  if (a !== DIM) return c;
  const x = hex2rgb(c), y = hex2rgb(TOWER_UNLIT);
  return rgb2hex(x.map((v, i) => v * 0.3 + y[i] * 0.7));
}

export function parseAvailability(text) {
  let doc;
  try { doc = JSON.parse(text); } catch (e) { return null; }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return null;
  if (!doc.buildings || typeof doc.buildings !== 'object' || Array.isArray(doc.buildings)) return null;
  return doc;
}

// Dimming every building without a source only reads right once most of the city has
// one. Below this share of private units it would darken nearly the whole map to say
// "no data", so until then a building without a source is drawn at normal strength and
// only the ones with units available are marked.
// Held at 2 (never) on 2026-10-11: coverage crossed 50% and the dimming would have
// switched on unannounced. Set it to a share between 0 and 1 when Blair wants it.
export const DIM_FROM_SHARE = 2;
export const dimsTheRest = (avail) => !!(avail && avail.coverage && avail.coverage.share >= DIM_FROM_SHARE);

export function availabilityState(avail, id) {
  if (!avail) return NORMAL;
  const entry = avail.buildings[String(id)];
  if (!entry) return dimsTheRest(avail) ? DIM : NORMAL;
  return Array.isArray(entry.units) && entry.units.length > 0 ? LIT : NORMAL;
}

export function unitsFor(avail, id) {
  const entry = avail && avail.buildings[String(id)];
  return entry && Array.isArray(entry.units) ? entry.units : null;
}

// ── which buildings are shown ────────────────────────────────────────────────
// THE place a filter goes. Every building on the map, and every count in the status
// line, passes through here. Filters (rent, beds, parking) come later (Blair,
// 2026-10-09): add a field to `view` and a line below.
export function isShown(b, view) {
  if (view.mode !== 'all' && b.type !== 'private') return false;
  return true;
}

// matched: a Set of building ids whose own footprint is drawn as a tower (property m: 1).
export function toFeatures(buildings, view, avail, matched) {
  const out = [];
  for (const b of buildings) {
    if (!isShown(b, view)) continue;
    const a = availabilityState(avail, b.id);
    const props = { id: b.id, s: unitsScale(b.units), a, k: a * 1e4 - (b.units || 0) };
    if (b.score !== null && b.score !== undefined) props.score = b.score;
    if (matched && matched.has(b.id)) props.m = 1;
    out.push({ type: 'Feature', id: b.id, properties: props,
      geometry: { type: 'Point', coordinates: [b.lon, b.lat] } });
  }
  return { type: 'FeatureCollection', features: out };
}

// bounds: {west, south, east, north} in degrees
export function countOnScreen(buildings, view, bounds) {
  let n = 0, units = 0;
  for (const b of buildings) {
    if (!isShown(b, view)) continue;
    if (b.lon < bounds.west || b.lon > bounds.east || b.lat < bounds.south || b.lat > bounds.north) continue;
    n++; units += b.units || 0;
  }
  return { buildings: n, units };
}

// ── words ────────────────────────────────────────────────────────────────────
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const fmtInt = (n) => Math.round(n).toLocaleString('en-CA');

export function fmtDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  if (!m) return '';
  return `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}`;
}

export function plural(n, one, many) { return `${fmtInt(n)} ${n === 1 ? one : many}`; }

export function screenSentence({ buildings, units }) {
  return `${plural(buildings, 'building', 'buildings')} with ${plural(units, 'unit', 'units')} on screen`;
}

export function coverageSentence(avail) {
  const c = avail && avail.coverage;
  if (!c || !Number.isFinite(c.units_private) || c.units_private <= 0 || !Number.isFinite(c.units_covered)) {
    return 'No availability sources yet';
  }
  const pct = (100 * c.units_covered) / c.units_private;
  const shown = pct > 0 && pct < 1 ? pct.toFixed(1) : Math.round(pct);
  const where = Number.isFinite(c.buildings_covered) ? ` in ${plural(c.buildings_covered, 'building', 'buildings')}` : '';
  return `Availability seen for ${shown}% of private rental units (${fmtInt(c.units_covered)} of ${fmtInt(c.units_private)})${where}`;
}

export function bedsLabel(beds) {
  if (beds === 0) return 'Bachelor';
  if (!Number.isFinite(beds)) return 'Unit';
  return `${beds} bed`;
}

export function rentLabel(u) {
  if (!Number.isFinite(u.rent)) return '';
  return (u.rent_is_floor ? 'from ' : '') + '$' + fmtInt(u.rent);
}

// Only http(s) links leave the app; anything else in a data file is dropped.
export function safeUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch (e) { return null; }
}

export function googleMapsUrl(b) {
  return `https://www.google.com/maps/search/?api=1&query=${b.lat},${b.lon}`;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
