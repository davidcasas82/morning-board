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

function slotStarts(y, m, d, count, config) {
  var out = [];
  var day = Date.UTC(y, m - 1, d);
  while (out.length < count) {
    var dt = new Date(day);
    var ymd = dt.toISOString().slice(0, 10);
    var spec = quotes.isWeekendYmd(ymd) ? config.quoteHours.weekend : config.quoteHours.weekday;
    var hm = spec.start.split(':');
    var start = +hm[0] * 60 + +hm[1];
    var endHm = spec.end.split(':');
    var close = +endHm[0] * 60 + +endHm[1];
    var s;
    for (s = 0; s < quotes.slotsOn(ymd, config) && out.length < count; s++) {
      var at = start + s * config.quoteMinutes;
      var last = Math.min(at + config.quoteMinutes, close) - 1;
      out.push({
        first: s === 0,
        start: instantAtPacific(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate(), Math.floor(at / 60), at % 60),
        end: instantAtPacific(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate(), Math.floor(last / 60), last % 60)
      });
    }
    day += 86400000;
  }
  return out;
}

function picksOver(slots, list, config) {
  return slots.map(function (slot) { return quotes.quoteFor(slot.start, list, config).index; });
}

function assertNoBackToBack(picks, label) {
  var i;
  for (i = 1; i < picks.length; i++) {
    assert.notStrictEqual(picks[i], picks[i - 1], label + ' repeated index ' + picks[i] + ' at slot ' + i);
  }
}

var example = JSON.parse(fs.readFileSync(path.join(__dirname, 'board-config.example.json'), 'utf8'));
var config = quotes.configOf(example);

assert.strictEqual(example.quotesUrl, '/quotes.json');
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

var first = quotes.quoteFor(mondaySameA, list, config);
var again = quotes.quoteFor(mondaySameA, list, config);
var laterMinute = quotes.quoteFor(mondaySameB, list, config);
var nextHour = quotes.quoteFor(mondayNext, list, config);
assert.strictEqual(first.index, again.index);
assert.strictEqual(first.slot, again.slot);
assert.strictEqual(first.slot, 1);
assert.strictEqual(first.index, laterMinute.index);
assert.notStrictEqual(first.index, nextHour.index);
assert.strictEqual(first.quote, list[first.index]);

var big = makeQuotes(380);
var run = slotStarts(2026, 10, 20, 1000, config);
var picks = picksOver(run, big, config);
var dayStarts = run.filter(function (slot, i) { return i > 0 && slot.first; }).length;
assert.ok(dayStarts >= 80, 'run crosses ' + dayStarts + ' day boundaries');
assertNoBackToBack(picks, '380 quotes');

var i;
var steady = 0;
var oneStep = 0;
var newer = 0;
var older = 0;
for (i = 0; i < run.length; i++) {
  if (quotes.quoteFor(run[i].end, big, config).index === picks[i]) steady++;
  if (i === 0) continue;
  if (picks[i] === picks[i - 1] + 1 || picks[i] === picks[i - 1] - 1) oneStep++;
  var was = big[picks[i - 1]].quote_date;
  var now = big[picks[i]].quote_date;
  if (now > was) newer++;
  if (now < was) older++;
}
assert.strictEqual(steady, run.length, 'pick holds from the first to the last minute of each slot');
assert.ok(oneStep < 20, 'picks step by one index ' + oneStep + ' times');
assert.ok(newer > 300 && older > 300, 'quote dates move both ways: newer ' + newer + ', older ' + older);

var distinct = new Set(picks).size;
assert.ok(distinct >= 0.75 * big.length, distinct + ' distinct quotes in 1000 slots');

var firstOfDay = run.filter(function (slot) { return slot.first; }).map(function (slot) {
  return quotes.quoteFor(slot.start, big, config).index;
});
assert.ok(new Set(firstOfDay).size > firstOfDay.length / 2, 'first slot of the day is not a fixed quote');

var reloaded = picksOver(run, makeQuotes(380), config);
assert.deepStrictEqual(reloaded, picks, 'same slots give the same picks on a fresh list');

var three = makeQuotes(3);
var threePicks = picksOver(run, three, config);
assertNoBackToBack(threePicks, '3 quotes');
assert.strictEqual(new Set(threePicks).size, 3);

var two = makeQuotes(2);
var twoPicks = picksOver(run, two, config);
assertNoBackToBack(twoPicks, '2 quotes');
assert.strictEqual(new Set(twoPicks).size, 2);

var one = makeQuotes(1);
assert.deepStrictEqual(new Set(picksOver(run.slice(0, 50), one, config)), new Set([0]));

assert.strictEqual(quotes.quoteFor(mondayOpen, [], config), null);
assert.strictEqual(quotes.quoteFor(mondayOpen, quotes.normalize({ quotes: [] }), config), null);
assert.strictEqual(quotes.quoteFor(mondayOpen, quotes.normalize([]), config), null);

var blankCredit = quotes.creditLine({ excerpt_credit: '  ' });
var namedCredit = quotes.creditLine({ excerpt_credit: 'San Diego, CA' });
assert.strictEqual(blankCredit, 'Abraham-Hicks Publications');
assert.strictEqual(namedCredit, 'San Diego, CA · Abraham-Hicks Publications');

console.log('quote rotation ok');
console.log('1000 slots, ' + distinct + ' of 380 quotes, ' + dayStarts + ' day boundaries, no back-to-back repeats');
console.log('weekday slots ' + quotes.slotsOn('2026-10-19', config));
console.log('weekend slots ' + quotes.slotsOn('2026-10-18', config));
console.log('empty file stays off the quote');
