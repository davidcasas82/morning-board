/* node test-resolve.js
   quotes.json and weather.json resolve against the document URL.
   A site-root path would 404 at the domain root on GitHub Pages. */
var assert = require('assert');
var fs = require('fs');
var path = require('path');
var weather = require('./weather.js');

var pages = 'https://davidcasas82.github.io/morning-board/kiosk.html?v=26';
var mini = 'http://127.0.0.1:8780/kiosk.html';
var example = JSON.parse(fs.readFileSync(path.join(__dirname, 'board-config.example.json'), 'utf8'));
var kiosk = fs.readFileSync(path.join(__dirname, 'kiosk.html'), 'utf8');
var index = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');

function response(status, body) {
  var text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status: status,
    text: function () { return Promise.resolve(text); },
    json: function () { return Promise.resolve(JSON.parse(text)); }
  };
}

assert.strictEqual(weather.BOARD_URL, 'weather.json');
assert.strictEqual(example.quotesUrl, 'quotes.json');
assert.strictEqual(example.quotesUrl.indexOf('/'), -1);
assert.strictEqual(JSON.stringify(example).indexOf('http://'), -1);
assert.strictEqual(JSON.stringify(example).indexOf('https://'), -1);

assert.strictEqual(
  weather.resolvePageUrl('weather.json', pages),
  'https://davidcasas82.github.io/morning-board/weather.json'
);
assert.strictEqual(
  weather.resolvePageUrl(example.quotesUrl, pages),
  'https://davidcasas82.github.io/morning-board/quotes.json'
);
assert.strictEqual(
  weather.resolvePageUrl('weather.json', mini),
  'http://127.0.0.1:8780/weather.json'
);
assert.strictEqual(
  weather.resolvePageUrl(example.quotesUrl, mini),
  'http://127.0.0.1:8780/quotes.json'
);
assert.strictEqual(
  weather.resolvePageUrl('/weather.json', pages),
  'https://davidcasas82.github.io/weather.json'
);
assert.notStrictEqual(
  weather.resolvePageUrl('weather.json', pages),
  weather.resolvePageUrl('/weather.json', pages)
);

assert.ok(kiosk.indexOf('new URL(quoteConfig.quotesUrl, document.URL)') !== -1);
assert.ok(kiosk.indexOf("fetch('board-config.json'") !== -1);
assert.ok(kiosk.indexOf("fetch('board-config.example.json'") !== -1);
assert.strictEqual(kiosk.indexOf("'/quotes.json'"), -1);
assert.strictEqual(kiosk.indexOf('"/quotes.json"'), -1);
assert.strictEqual(kiosk.indexOf("'/weather.json'"), -1);
assert.ok(index.indexOf('rel="icon"') !== -1);
assert.ok(kiosk.indexOf('rel="icon"') !== -1);
assert.ok(kiosk.indexOf('data:image/gif;base64,') !== -1);
assert.strictEqual(kiosk.indexOf('favicon.ico'), -1);

var boardBody = {
  current: { temperature_2m: 47 },
  hourly: { time: ['2026-11-14T05:00'], temperature_2m: [47] },
  daily: { time: ['2026-11-14'], temperature_2m_max: [50] },
  city: 'Irvine, California',
  age_seconds: 3
};
var calls = [];
function fetchImpl(url) {
  calls.push(String(url));
  return Promise.resolve(response(200, boardBody));
}

weather.load(weather.state(), fetchImpl, pages).then(function (data) {
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0], 'https://davidcasas82.github.io/morning-board/weather.json');
  assert.strictEqual(data.current.temperature_2m, 47);
  assert.strictEqual(calls[0].indexOf('open-meteo'), -1);
  console.log('resolve ok');
}).catch(function (err) {
  console.error(err);
  process.exit(1);
});
