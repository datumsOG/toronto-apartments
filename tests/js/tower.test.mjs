import test from 'node:test';
import assert from 'node:assert/strict';
import { towerColour, scoreColour, TOWER_UNLIT, DIM, NORMAL, LIT } from '../../app/js/logic.js';

test('a tower takes its score colour unless dim', () => {
  assert.equal(towerColour(90, NORMAL), scoreColour(90));
  assert.equal(towerColour(90, LIT), scoreColour(90));
  assert.equal(towerColour(null, NORMAL), scoreColour(null));
});

test('a dim tower sits between its score colour and the unlit tower', () => {
  const c = towerColour(90, DIM);
  assert.match(c, /^#[0-9a-f]{6}$/);
  assert.notEqual(c, scoreColour(90));
  assert.notEqual(c, TOWER_UNLIT);
});
