/* node test-weather.js */
var assert = require('assert');
var weather = require('./weather.js');

function response(status, body) {
  var text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status: status,
    text: function () { return Promise.resolve(text); },
    json: function () {
      return Promise.resolve().then(function () { return JSON.parse(text); });
    }
  };
}

function scripted(steps) {
  var i = 0;
  var calls = [];
  function fn(url) {
    calls.push(String(url));
    var step = steps[i++];
    if (!step) return Promise.reject(new Error('unexpected ' + url));
    if (step.url && String(url).indexOf(step.url) === -1) {
      return Promise.reject(new Error('want ' + step.url + ' got ' + url));
    }
    if (step.throw) return Promise.reject(step.throw);
    return Promise.resolve(response(step.status, step.body));
  }
  fn.calls = calls;
  return fn;
}

var forecast = {
  current: {
    time: '2026-11-14T05:00',
    temperature_2m: 72.2,
    apparent_temperature: 70,
    relative_humidity_2m: 40,
    wind_speed_10m: 5,
    weather_code: 1,
    is_day: 1
  },
  hourly: { time: ['2026-11-14T05:00'], temperature_2m: [72.2] },
  daily: {
    time: ['2026-11-14'],
    temperature_2m_max: [80],
    weather_code: [1],
    sunrise: ['2026-11-14T06:30'],
    sunset: ['2026-11-14T17:00']
  }
};
var geo = { address: { city: 'Irvine', state: 'California' } };

function fallback(first) {
  return [
    first,
    { url: 'api.open-meteo.com', status: 200, body: forecast },
    { url: 'nominatim.openstreetmap.org', status: 200, body: geo }
  ];
}

function rejects(promise) {
  return promise.then(function () {
    throw new Error('expected rejection');
  }, function () {
    return true;
  });
}

async function main() {
  var fn = scripted(fallback({ url: '/weather.json', status: 404, body: 'missing' }));
  var st = weather.state();
  var data = await weather.load(st, fn);
  assert.strictEqual(data.city, 'Irvine, California');
  assert.strictEqual(data.age_seconds, 0);
  assert.strictEqual(data.current.temperature_2m, 72.2);
  assert.strictEqual(st.fromBoard, false);
  assert.strictEqual(fn.calls.length, 3);
  assert.ok(fn.calls[1].indexOf('latitude=33.6695') !== -1);
  assert.ok(fn.calls[1].indexOf('longitude=-117.8231') !== -1);
  assert.ok(fn.calls[1].indexOf('models=gfs_seamless') !== -1);
  assert.strictEqual(fn.calls[1], weather.FORECAST_URL);

  fn = scripted(fallback({ url: '/weather.json', throw: new Error('offline') }));
  st = weather.state();
  data = await weather.load(st, fn);
  assert.strictEqual(data.city, 'Irvine, California');
  assert.strictEqual(fn.calls.length, 3);
  assert.strictEqual(st.fromBoard, false);

  fn = scripted(fallback({ url: '/weather.json', status: 200, body: '<!DOCTYPE html>not json' }));
  data = await weather.load(weather.state(), fn);
  assert.strictEqual(data.age_seconds, 0);
  assert.ok(fn.calls[1].indexOf('api.open-meteo.com') !== -1);

  var boardBody = {
    current: forecast.current,
    hourly: forecast.hourly,
    daily: forecast.daily,
    city: 'Irvine, California',
    age_seconds: 12
  };
  fn = scripted([{ url: '/weather.json', status: 200, body: boardBody }]);
  st = weather.state();
  data = await weather.load(st, fn);
  assert.strictEqual(st.fromBoard, true);
  assert.strictEqual(data.age_seconds, 12);
  assert.strictEqual(data.city, 'Irvine, California');
  assert.strictEqual(fn.calls.length, 1);

  fn = scripted([{ url: '/weather.json', throw: new Error('reset') }]);
  assert.strictEqual(await rejects(weather.load(st, fn)), true);
  assert.strictEqual(fn.calls.length, 1);

  fn = scripted([{ url: '/weather.json', status: 502, body: { error: 'weather unavailable' } }]);
  assert.strictEqual(await rejects(weather.load(weather.state(), fn)), true);
  assert.strictEqual(fn.calls.length, 1);

  fn = scripted([
    { url: '/weather.json', status: 404, body: '' },
    { url: 'api.open-meteo.com', status: 200, body: forecast },
    { url: 'nominatim.openstreetmap.org', throw: new Error('geo') }
  ]);
  data = await weather.load(weather.state(), fn);
  assert.strictEqual(data.city, '');
  assert.strictEqual(data.current.temperature_2m, 72.2);

  fn = scripted([
    { url: '/weather.json', status: 404, body: '' },
    { url: 'api.open-meteo.com', status: 503, body: 'down' }
  ]);
  assert.strictEqual(await rejects(weather.load(weather.state(), fn)), true);
  assert.strictEqual(fn.calls.length, 2);

  var now = 1700000000000;
  st = weather.state();
  var missed = weather.settle(st, now, null, true);
  assert.strictEqual(missed.error, true);
  assert.strictEqual(missed.paint, null);
  assert.strictEqual(missed.retryMs, 60 * 1000);

  var painted = weather.settle(st, now, data, false);
  assert.strictEqual(painted.error, false);
  assert.strictEqual(painted.retryMs, 10 * 60 * 1000);
  assert.strictEqual(painted.paint.current.temperature_2m, 72.2);

  var kept = weather.settle(st, now + 60 * 1000, null, true);
  assert.strictEqual(kept.error, false);
  assert.strictEqual(kept.retryMs, 60 * 1000);

  var stale = weather.settle(st, now + weather.STALE_MS + 1000, null, true);
  assert.strictEqual(stale.error, true);
  assert.strictEqual(stale.retryMs, 60 * 1000);

  var old = weather.settle(weather.state(), now, { age_seconds: (2 * 60 * 60) + 1, current: { temperature_2m: 1 } }, false);
  assert.strictEqual(old.error, true);
  assert.strictEqual(old.paint, null);

  console.log('weather fallback ok');
}

main().catch(function (err) {
  console.error(err);
  process.exit(1);
});
