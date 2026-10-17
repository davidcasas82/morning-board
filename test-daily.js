/* node test-daily.js */
var assert = require('assert');
var daily = require('./daily.js');

var faces = { nixie: 1, segment: 1 };
var themes = { slate: 1, dusk: 1, ember: 1, bone: 1 };

function pairKey(pair) {
  return pair.face + '/' + pair.theme;
}

function assertSamePair(ymd) {
  var a = daily.pairForBoardDay(ymd);
  var b = daily.pairForBoardDay(ymd);
  assert.deepStrictEqual(a, b, ymd + ' changed between calls');
  assert.strictEqual(faces[a.face], 1, ymd + ' face');
  assert.strictEqual(themes[a.theme], 1, ymd + ' theme');
  return a;
}

function walk(start, days) {
  var prev = null;
  var cursor = start;
  var unique = {};
  var i;
  for (i = 0; i < days; i++) {
    var pair = assertSamePair(cursor);
    var key = pairKey(pair);
    if (prev === key) {
      throw new Error('consecutive duplicate on ' + cursor + ' ' + key);
    }
    prev = key;
    unique[key] = 1;
    cursor = daily.addDays(cursor, 1);
  }
  return Object.keys(unique).length;
}

/* Pacific wall time → UTC instant. Confirms the clock really is that hour in LA. */
function instantAtPacific(y, m, d, h, min) {
  var guess = Date.UTC(y, m - 1, d, h + 8, min, 0);
  var i;
  for (i = 0; i < 6; i++) {
    var p = daily.pacificParts(new Date(guess));
    var got = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second || 0);
    var want = Date.UTC(y, m - 1, d, h, min, 0);
    if (got === want) return new Date(guess);
    guess += want - got;
  }
  throw new Error('could not build Pacific ' + y + '-' + m + '-' + d + ' ' + h + ':' + min);
}

function expectBoard(label, y, m, d, h, min, expected) {
  var dt = instantAtPacific(y, m, d, h, min);
  var p = daily.pacificParts(dt);
  assert.strictEqual(p.year, y, label + ' year');
  assert.strictEqual(p.month, m, label + ' month');
  assert.strictEqual(p.day, d, label + ' day');
  assert.strictEqual(p.hour, h, label + ' hour');
  assert.strictEqual(p.minute, min, label + ' minute');
  var got = daily.boardDay(dt);
  assert.strictEqual(got, expected, label + ' board day ' + got + ' !== ' + expected + ' (' + dt.toISOString() + ')');
  console.log(label + ' ' + dt.toISOString() + ' -> ' + got);
}

var sample = assertSamePair('2026-10-17');
var again = daily.pairForBoardDay('2026-10-17');
assert.deepStrictEqual(sample, again);
var early = assertSamePair('2024-02-28');
assert.deepStrictEqual(early, daily.pairForBoardDay('2024-02-28'));
assert.deepStrictEqual(sample, daily.pairForBoardDay('2026-10-17'), 'cache must not change an earlier date');

var uniq2026 = walk('2026-01-01', 366);
var uniqLeap = walk('2024-01-01', 366);
var uniqDst = walk('2026-03-01', 40);
console.log('366 days from 2026-01-01 unique pairs: ' + uniq2026);
console.log('366 days from 2024-01-01 unique pairs: ' + uniqLeap);
console.log('2026-10-17 pair: ' + pairKey(sample));
console.log('pair order: ' + daily.PAIRS.map(pairKey).join(', '));

expectBoard('winter 2:59', 2026, 1, 15, 2, 59, '2026-01-14');
expectBoard('winter 3:00', 2026, 1, 15, 3, 0, '2026-01-15');
expectBoard('summer 2:59', 2026, 7, 15, 2, 59, '2026-07-14');
expectBoard('summer 3:00', 2026, 7, 15, 3, 0, '2026-07-15');
expectBoard('spring-forward 1:59', 2026, 3, 8, 1, 59, '2026-03-07');
expectBoard('spring-forward 3:00', 2026, 3, 8, 3, 0, '2026-03-08');
expectBoard('fall-back 2:59', 2026, 11, 1, 2, 59, '2026-10-31');
expectBoard('fall-back 3:00', 2026, 11, 1, 3, 0, '2026-11-01');

assert.strictEqual(daily.boardDay(instantAtPacific(2026, 10, 18, 2, 59)), '2026-10-17');
assert.strictEqual(daily.boardDay(instantAtPacific(2026, 10, 18, 3, 0)), '2026-10-18');
assert.notStrictEqual(
  pairKey(daily.pairForBoardDay('2026-10-17')),
  pairKey(daily.pairForBoardDay('2026-10-18'))
);

assert.strictEqual(daily.activeOverride('slate', '2026-10-17', themes), null);
assert.strictEqual(daily.activeOverride('nixie', '2026-10-17', faces), null);
assert.strictEqual(daily.activeOverride('fallout', '2026-10-17', themes), null);
assert.strictEqual(daily.activeOverride('{"board":"2026-10-16","value":"ember"}', '2026-10-17', themes), null);
assert.strictEqual(daily.activeOverride('{"board":"2026-10-17","value":"ember"}', '2026-10-17', themes), 'ember');
assert.strictEqual(daily.activeOverride('{"board":"2026-10-17","value":"segment"}', '2026-10-17', faces), 'segment');
assert.strictEqual(daily.activeOverride('{"board":"2026-10-17","value":"nope"}', '2026-10-17', themes), null);

console.log('ok');
