/* Weather for the morning board.
   The page asks for weather.json beside itself. The mini serves that file.
   GitHub Pages does not, so a missing file falls back to Open-Meteo and
   Nominatim. A first-load 500 or 502 does the same. Once weather.json has
   returned JSON, a later network error, 500, or 502 stays on that file. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MorningWeather = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var REFRESH_MS = 10 * 60 * 1000;
  var RETRY_MS = 60 * 1000;
  var STALE_MS = 2 * 60 * 60 * 1000;
  var BOARD_URL = 'weather.json';
  var FORECAST_URL = 'https://api.open-meteo.com/v1/forecast?latitude=33.67&longitude=-117.82&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day&hourly=temperature_2m&daily=temperature_2m_max,weather_code,sunrise,sunset&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto&forecast_days=7&models=gfs_seamless';
  var NOMINATIM_URL = 'https://nominatim.openstreetmap.org/reverse?lat=33.67&lon=-117.82&format=json';

  function state() {
    return { fetchedAt: 0, fromBoard: false };
  }

  function cityLine(geo) {
    var address = geo && geo.address ? geo.address : {};
    var city = address.city || address.town || address.village || address.county || '';
    var region = address.state_code || address.state || '';
    if (city && region) return city + ', ' + region;
    return city || region;
  }

  function fetchWithTimeout(fetchImpl, url) {
    var ctrl = typeof AbortController === 'function' ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
    var opts = { cache: 'no-store' };
    if (ctrl) opts.signal = ctrl.signal;
    return Promise.resolve().then(function () {
      return fetchImpl(url, opts);
    }).then(function (res) {
      if (timer) clearTimeout(timer);
      return res;
    }, function (err) {
      if (timer) clearTimeout(timer);
      throw err;
    });
  }

  function copyForecast(data) {
    var copy = {};
    var key;
    for (key in data) {
      if (Object.prototype.hasOwnProperty.call(data, key)) copy[key] = data[key];
    }
    return copy;
  }

  function loadDirect(fetchImpl) {
    return fetchWithTimeout(fetchImpl, FORECAST_URL).then(function (res) {
      if (!res.ok) throw new Error('open-meteo ' + res.status);
      return res.json();
    }).then(function (data) {
      if (!data || typeof data !== 'object' || !data.current) throw new Error('open-meteo shape');
      var forecast = copyForecast(data);
      forecast.age_seconds = 0;
      forecast.city = '';
      return fetchWithTimeout(fetchImpl, NOMINATIM_URL).then(function (geoRes) {
        return geoRes.json().then(function (geo) {
          forecast.city = cityLine(geo);
          return forecast;
        }, function () {
          return forecast;
        });
      }, function () {
        return forecast;
      });
    });
  }

  function acceptBoard(state, data) {
    if (!data || typeof data !== 'object' || !data.current) throw new Error('weather shape');
    state.fromBoard = true;
    return data;
  }

  /* Resolve a page-relative path against the document URL. A leading slash
     would hit the site root, which is the wrong file on GitHub Pages. */
  function resolvePageUrl(path, documentUrl) {
    var base = documentUrl;
    if (!base && typeof document !== 'undefined' && document.URL) base = document.URL;
    if (!base) return path;
    return new URL(path, base).href;
  }

  function boardMiss(status, weatherState) {
    if (status === 404) return true;
    return (status === 500 || status === 502) && !weatherState.fromBoard;
  }

  function load(weatherState, fetchImpl, documentUrl) {
    var fetchFn = fetchImpl || (typeof fetch === 'function' ? fetch : null);
    return fetchWithTimeout(fetchFn, resolvePageUrl(BOARD_URL, documentUrl)).then(function (res) {
      if (boardMiss(res.status, weatherState)) return loadDirect(fetchFn);
      return res.text().then(function (text) {
        var data;
        try {
          data = JSON.parse(text);
        } catch (e) {
          return loadDirect(fetchFn);
        }
        if (!res.ok) throw new Error('weather ' + res.status);
        return acceptBoard(weatherState, data);
      });
    }, function (err) {
      if (!weatherState.fromBoard) return loadDirect(fetchFn);
      throw err;
    });
  }

  function settle(weatherState, now, data, failed) {
    var age;
    var fetchedAt;
    if (!failed && data) {
      age = Number(data.age_seconds);
      if (isFinite(age) && age >= 0 && age * 1000 <= STALE_MS) {
        fetchedAt = now - age * 1000;
        weatherState.fetchedAt = fetchedAt;
        return { paint: data, error: false, retryMs: REFRESH_MS, fetchedAt: fetchedAt };
      }
    }
    fetchedAt = weatherState.fetchedAt || 0;
    return {
      paint: null,
      error: !fetchedAt || (now - fetchedAt) > STALE_MS,
      retryMs: RETRY_MS,
      fetchedAt: fetchedAt
    };
  }

  return {
    REFRESH_MS: REFRESH_MS,
    RETRY_MS: RETRY_MS,
    STALE_MS: STALE_MS,
    BOARD_URL: BOARD_URL,
    FORECAST_URL: FORECAST_URL,
    NOMINATIM_URL: NOMINATIM_URL,
    state: state,
    cityLine: cityLine,
    resolvePageUrl: resolvePageUrl,
    load: load,
    settle: settle
  };
});
