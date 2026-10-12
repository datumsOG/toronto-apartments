// node --test tests/js/*.test.mjs   (from the repo root)
// The pure decisions behind the map: score -> colour, units -> radius, lit and dim, and
// the words in the status line. Fixtures are invented.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  scoreColour, SCORE_STOPS, NEVER_EVALUATED, colourExpression, unitsRadius, unitsScale,
  radiusExpression, RADIUS_STOPS, MIN_RADIUS, parseAvailability, availabilityState, unitsFor,
  LIT, NORMAL, DIM, DIM_FROM_SHARE, isShown, toFeatures, countOnScreen, coverageSentence, screenSentence,
  fmtDate, bedsLabel, rentLabel, safeUrl, escapeHtml, googleMapsUrl,
} from '../../app/js/logic.js';

const SAMPLE = readFileSync(new URL('./availability.sample.json', import.meta.url), 'utf8');

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

test('score colour: stops are exact, ends clamp, unknown is the neutral', () => {
  for (const [s, c] of SCORE_STOPS) assert.equal(scoreColour(s), c);
  assert.equal(scoreColour(12), SCORE_STOPS[0][1]);
  assert.equal(scoreColour(100), SCORE_STOPS[SCORE_STOPS.length - 1][1]);
  assert.equal(scoreColour(null), NEVER_EVALUATED);
  assert.equal(scoreColour(undefined), NEVER_EVALUATED);
  assert.equal(scoreColour(NaN), NEVER_EVALUATED);
  assert.match(scoreColour(83.5), /^#[0-9a-f]{6}$/);
});

test('score colour: red at the bottom, green at the top, and visible on black', () => {
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [r0, g0] = rgb(SCORE_STOPS[0][1]);
  const [r1, g1] = rgb(SCORE_STOPS[SCORE_STOPS.length - 1][1]);
  assert.ok(r0 > g0 * 2, 'the lowest stop is red');
  assert.ok(g1 > r1 * 2, 'the highest stop is green');
  for (const [, c] of SCORE_STOPS) assert.ok((lum(c) + 0.05) / (lum('#0e0e0e') + 0.05) >= 3);
  // contrast against a near-black basemap of at least 3:1 for the darkest stop
  assert.ok((lum(SCORE_STOPS[0][1]) + 0.05) / (lum('#0e0e0e') + 0.05) >= 3);
  // the neutral has no hue
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(NEVER_EVALUATED.slice(i, i + 2), 16));
  assert.ok(Math.max(r, g, b) - Math.min(r, g, b) < 16);
});

test('colour expression is built from the same stops', () => {
  const e = colourExpression();
  assert.equal(e[0], 'case');
  assert.deepEqual(e[1], ['has', 'score']);
  assert.deepEqual(e[2].slice(3), SCORE_STOPS.flat());
  assert.equal(e[3], NEVER_EVALUATED);
});

test('radius: area is proportional to units', () => {
  for (const z of [10, 12.5, 15]) {
    const r100 = unitsRadius(100, z), r400 = unitsRadius(400, z);
    assert.ok(Math.abs((r400 * r400) / (r100 * r100) - 4) < 1e-9, `4x units is 4x area at z${z}`);
  }
  assert.equal(unitsRadius(0, 12), MIN_RADIUS);
  assert.equal(unitsRadius(undefined, 12), MIN_RADIUS);
  assert.ok(unitsRadius(100, 15) > unitsRadius(100, 11), 'grows with zoom');
  assert.equal(unitsRadius(100, RADIUS_STOPS[0][0] - 3), unitsRadius(100, RADIUS_STOPS[0][0]));
  assert.equal(unitsScale(49), 7);
});

test('radius expression keeps zoom at the top level and matches the function at the stops', () => {
  const e = radiusExpression();
  assert.deepEqual(e.slice(0, 3), ['interpolate', ['linear'], ['zoom']]);
  assert.equal(JSON.stringify(e).split('"zoom"').length - 1, 1);
  const withRing = radiusExpression(4);
  assert.equal(JSON.stringify(withRing).split('"zoom"').length - 1, 1);
  // evaluate the stop outputs by hand for s = 10 (100 units)
  for (let i = 0; i < RADIUS_STOPS.length; i++) {
    const [z, k] = RADIUS_STOPS[i];
    const [, extra, [, min, [, kk]]] = e[4 + i * 2];
    assert.equal(e[3 + i * 2], z);
    assert.equal(extra + Math.max(min, kk * 10), unitsRadius(100, z));
    assert.equal(kk, k);
  }
});

test('lit and dim: no file means every building is normal', () => {
  assert.equal(availabilityState(null, 9000001), NORMAL);
  assert.equal(availabilityState(null, 123), NORMAL);
  assert.equal(unitsFor(null, 9000001), null);
});

test('lit and dim: with a file, units -> lit, covered and empty -> normal, absent -> dim', () => {
  const a = parseAvailability(SAMPLE);
  assert.ok(a);
  assert.equal(availabilityState(a, 9000001), LIT);
  assert.equal(availabilityState(a, '9000001'), LIT);
  assert.equal(availabilityState(a, 9000002), NORMAL);
  a.coverage.share = 0.1;
  assert.equal(availabilityState(a, 9000003), NORMAL, 'low coverage: the rest is not dimmed');
  a.coverage.share = DIM_FROM_SHARE;      // at the threshold, wherever it is set
  assert.equal(availabilityState(a, 9000003), DIM);
  assert.equal(unitsFor(a, 9000001).length, 2);
  assert.deepEqual(unitsFor(a, 9000002), []);
});

test('a file that does not parse, or has the wrong shape, counts as no file', () => {
  for (const bad of ['', '{', 'null', '[]', '42', '{"coverage":{}}', '{"buildings":[]}', '<html>404</html>']) {
    assert.equal(parseAvailability(bad), null, bad);
  }
  assert.ok(parseAvailability('{"buildings":{}}'));
});

const B = [
  { id: 1, type: 'private', units: 100, score: 90, lat: 43.65, lon: -79.38 },
  { id: 2, type: 'tchc', units: 300, score: null, lat: 43.70, lon: -79.40 },
  { id: 9000001, type: 'private', units: 20, score: 75, lat: 43.80, lon: -79.20 },
];

test('private by default, all on request', () => {
  assert.equal(isShown(B[1], { mode: 'private' }), false);
  assert.equal(isShown(B[1], { mode: 'all' }), true);
  assert.equal(isShown(B[0], { mode: 'private' }), true);
});

test('features carry what the layers read', () => {
  const a = parseAvailability(SAMPLE);
  a.coverage.share = DIM_FROM_SHARE;      // at the threshold, wherever it is set
  const fc = toFeatures(B, { mode: 'all' }, a);
  assert.equal(fc.features.length, 3);
  const [f1, f2, f3] = fc.features;
  assert.deepEqual(f1.geometry.coordinates, [-79.38, 43.65]);
  assert.equal(f1.properties.score, 90);
  assert.ok(!('score' in f2.properties), 'no score property when never evaluated');
  assert.equal(f1.properties.a, DIM);
  assert.equal(f3.properties.a, LIT);
  assert.ok(f3.properties.k > f1.properties.k, 'lit sorts above');
  assert.equal(toFeatures(B, { mode: 'private' }, null).features.length, 2);
  assert.ok(toFeatures(B, { mode: 'all' }, null).features.every((f) => f.properties.a === NORMAL));
});

test('on-screen count follows the mode and the bounds', () => {
  const box = { west: -79.5, south: 43.6, east: -79.3, north: 43.75 };
  assert.deepEqual(countOnScreen(B, { mode: 'all' }, box), { buildings: 2, units: 400 });
  assert.deepEqual(countOnScreen(B, { mode: 'private' }, box), { buildings: 1, units: 100 });
  assert.equal(screenSentence({ buildings: 1, units: 1 }), '1 building with 1 unit on screen');
  assert.equal(screenSentence({ buildings: 1204, units: 98331 }), '1,204 buildings with 98,331 units on screen');
});

test('coverage sentence states what it counts and the denominator', () => {
  assert.equal(coverageSentence(null), 'No availability sources yet');
  assert.equal(coverageSentence({ buildings: {} }), 'No availability sources yet');
  assert.equal(coverageSentence(parseAvailability(SAMPLE)),
    'Availability seen for 2% of private rental units (5,210 of 260,335) in 3 buildings');
  assert.equal(coverageSentence({ coverage: { units_private: 1000, units_covered: 3 } }),
    'Availability seen for 0.3% of private rental units (3 of 1,000)');
});

test('words and links', () => {
  assert.equal(fmtDate('2026-11-01'), '1 Nov 2026');
  assert.equal(fmtDate(null), '');
  assert.equal(bedsLabel(0), 'Bachelor');
  assert.equal(bedsLabel(2), '2 bed');
  assert.equal(rentLabel({ rent: 2350 }), '$2,350');
  assert.equal(rentLabel({ rent: 1795, rent_is_floor: true }), 'from $1,795');
  assert.equal(rentLabel({ rent: null }), '');
  assert.equal(safeUrl('https://example.com/a?b=1'), 'https://example.com/a?b=1');
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('not a url'), null);
  assert.equal(escapeHtml('<a href="x">O\'Hara & co</a>'), '&lt;a href=&quot;x&quot;&gt;O&#39;Hara &amp; co&lt;/a&gt;');
  assert.equal(googleMapsUrl({ lat: 43.66124, lon: -79.37013 }),
    'https://www.google.com/maps/search/?api=1&query=43.66124,-79.37013');
});

test('the committed buildings.json is shaped the way the app reads it', () => {
  const doc = JSON.parse(readFileSync(new URL('../../app/data/buildings.json', import.meta.url), 'utf8'));
  assert.ok(doc.buildings.length > 3000);
  for (const b of doc.buildings) {
    assert.ok(Number.isInteger(b.id) && Number.isFinite(b.lat) && Number.isFinite(b.lon));
    assert.ok(['private', 'tchc', 'social'].includes(b.type));
  }
  const fc = toFeatures(doc.buildings, { mode: 'private' }, null);
  assert.equal(fc.features.length, doc.buildings.filter((b) => b.type === 'private').length);
});
