/* node test-quotes.js */
var assert = require('assert');
var fs = require('fs');
var path = require('path');
var daily = require('./daily.js');
var quotes = require('./quotes.js');

global.MorningDaily = daily;

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
  throw new Error('could not build Pacific instant');
}

function makeQuotes(n) {
  var list = [];
  var i;
  for (i = 0; i < n; i++) {
    list.push({
      quote_date: '2026-01-' + String(28 - (i % 27)).padStart(2, '0'),
      text: 'Q' + i,
      excerpt_credit: i % 5 === 0 ? '' : 'Excerpt ' + i,
      word_count: 10 + i,
      source_url: ''
    });
  }
  return quotes.normalize(list);
}

function indexesOn(date, list, config) {
  var plan = quotes.planFor(date, list, config);
  return plan.map(function (item) { return item.index; });
}

function assertUnique(values, label) {
  var seen = {};
  var i;
  for (i = 0; i < values.length; i++) {
    assert.strictEqual(seen[values[i]], undefined, label + ' repeated ' + values[i]);
    seen[values[i]] = 1;
  }
}

function nextOlder(index, count) {
  if (index + 1 >= count) return 1;
  return index + 1;
}

var example = JSON.parse(fs.readFileSync(path.join(__dirname, 'board-config.example.json'), 'utf8'));
var config = quotes.configOf(example);

assert.strictEqual(example.quotesUrl, '');
assert.strictEqual(example.photosUrl, '');
assert.strictEqual(config.quoteHours.weekday.start, '08:30');
assert.strictEqual(config.quoteHours.weekday.end, '19:00');
assert.strictEqual(config.quoteHours.weekend.start, '09:00');
assert.strictEqual(config.quoteHours.weekend.end, '21:00');
assert.strictEqual(quotes.slotsOn('2026-10-19', config), 11);
assert.strictEqual(quotes.slotsOn('2026-10-18', config), 12);
assert.strictEqual(quotes.slotsOn('2026-10-17', config), 12);

var earlyEnd = quotes.configOf({
  quoteHours: {
    weekday: { start: '08:30', end: '15:00' },
    weekend: { start: '11:00', end: '15:00' }
  }
});
assert.notStrictEqual(quotes.slotsOn('2026-10-19', earlyEnd), quotes.slotsOn('2026-10-19', config));
assert.strictEqual(quotes.slotsOn('2026-10-19', earlyEnd), 7);

var list = makeQuotes(80);
var sunday = instantAtPacific(2026, 10, 18, 9, 0);
var sundayLate = instantAtPacific(2026, 10, 18, 20, 30);
var sundayClosed = instantAtPacific(2026, 10, 18, 21, 0);
var sundayBefore = instantAtPacific(2026, 10, 18, 8, 30);
var mondayOpen = instantAtPacific(2026, 10, 19, 8, 30);
var mondayBefore = instantAtPacific(2026, 10, 19, 8, 29);
var mondayMid = instantAtPacific(2026, 10, 19, 16, 0);
var mondayLast = instantAtPacific(2026, 10, 19, 18, 30);
var mondayEnd = instantAtPacific(2026, 10, 19, 19, 0);
var mondaySameA = instantAtPacific(2026, 10, 19, 9, 40);
var mondaySameB = instantAtPacific(2026, 10, 19, 10, 20);
var mondayNext = instantAtPacific(2026, 10, 19, 10, 30);

assert.strictEqual(quotes.quoteFor(sundayBefore, list, config), null);
assert.strictEqual(quotes.quoteFor(sundayClosed, list, config), null);
assert.strictEqual(quotes.quoteFor(mondayBefore, list, config), null);
assert.strictEqual(quotes.quoteFor(mondayEnd, list, config), null);
assert.ok(quotes.quoteFor(sunday, list, config));
assert.ok(quotes.quoteFor(sundayLate, list, config));
assert.ok(quotes.quoteFor(mondayOpen, list, config));
assert.ok(quotes.quoteFor(mondayMid, list, config));
assert.ok(quotes.quoteFor(mondayLast, list, config));

assert.strictEqual(quotes.quoteFor(earlyEnd && mondayMid, list, earlyEnd), null);

var sunIdx = indexesOn(sunday, list, config);
var monIdx = indexesOn(mondayOpen, list, config);
assert.strictEqual(sunIdx.length, 12);
assert.strictEqual(monIdx.length, 11);
assert.strictEqual(sunIdx[0], 0);
assert.strictEqual(monIdx[0], 0);
assertUnique(sunIdx, 'sunday');
assertUnique(monIdx, 'monday');
assert.strictEqual(monIdx[1], nextOlder(sunIdx[sunIdx.length - 1], list.length));
var i;
for (i = 2; i < monIdx.length; i++) {
  assert.strictEqual(monIdx[i], nextOlder(monIdx[i - 1], list.length));
}

var first = quotes.quoteFor(mondaySameA, list, config);
var again = quotes.quoteFor(mondaySameA, list, config);
var laterMinute = quotes.quoteFor(mondaySameB, list, config);
var nextHour = quotes.quoteFor(mondayNext, list, config);
assert.strictEqual(first.index, again.index);
assert.strictEqual(first.slot, again.slot);
assert.strictEqual(first.slot, 1);
assert.strictEqual(first.index, laterMinute.index);
assert.notStrictEqual(first.index, nextHour.index);
assert.strictEqual(quotes.quoteFor(mondayOpen, list, config).index, 0);
assert.strictEqual(quotes.quoteFor(sunday, list, config).index, 0);

var tuesday = instantAtPacific(2026, 10, 20, 8, 30);
var tueIdx = indexesOn(tuesday, list, config);
assert.strictEqual(tueIdx[0], 0);
assertUnique(tueIdx, 'tuesday');
assert.strictEqual(tueIdx[1], nextOlder(monIdx[monIdx.length - 1], list.length));

var tiny = makeQuotes(4);
var tinyCfg = quotes.configOf({ epoch: '2026-10-19' });
var tinyPlan = indexesOn(mondayOpen, tiny, tinyCfg);
assert.strictEqual(tinyPlan[0], 0);
assert.deepStrictEqual(tinyPlan.slice(0, 4), [0, 1, 2, 3]);
assert.strictEqual(new Set(tinyPlan.slice(0, 4)).size, 4);

var nextTiny = indexesOn(tuesday, tiny, tinyCfg);
assert.strictEqual(nextTiny[0], 0);
assert.strictEqual(nextTiny[1], 1);

assert.strictEqual(quotes.quoteFor(mondayOpen, [], config), null);
assert.strictEqual(quotes.quoteFor(mondayOpen, quotes.normalize({ quotes: [] }), config), null);
assert.strictEqual(quotes.quoteFor(mondayOpen, quotes.normalize([]), config), null);

var blankCredit = quotes.creditLine({ excerpt_credit: '  ' });
var namedCredit = quotes.creditLine({ excerpt_credit: 'San Diego, CA' });
assert.strictEqual(blankCredit, 'Abraham-Hicks Publications');
assert.strictEqual(namedCredit, 'San Diego, CA · Abraham-Hicks Publications');

console.log('quote rotation ok');
console.log('weekday slots ' + quotes.slotsOn('2026-10-19', config));
console.log('weekend slots ' + quotes.slotsOn('2026-10-18', config));
console.log('empty file stays off the quote');
