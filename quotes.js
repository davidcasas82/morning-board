/* Quote rotation for the morning board.
   Hours come from config. Slot count is the 60-minute slices that fit
   between start and end. Pacific wall time, America/Los_Angeles.
   First slot of a day is the newest quote. Later slots walk older quotes
   and do not repeat. The next day starts at the newest again, then
   continues through older quotes that were not used on earlier days.
   When that list is exhausted, it wraps. Same instant, same quote. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MorningQuotes = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var EPOCH = '2020-01-01';
  var DEFAULTS = {
    timezone: 'America/Los_Angeles',
    quotesUrl: '',
    photosUrl: '',
    quoteMinutes: 60,
    quoteMinPx: 32,
    quoteMaxPx: 180,
    quoteMarginPx: 150,
    epoch: EPOCH,
    quoteHours: {
      weekday: { start: '08:30', end: '19:00' },
      weekend: { start: '09:00', end: '21:00' }
    }
  };

  function pad2(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function parseYmd(ymd) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd || '');
    if (!m) return null;
    return { y: +m[1], mo: +m[2], d: +m[3] };
  }

  function ymdFromUtc(dt) {
    return dt.getUTCFullYear() + '-' + pad2(dt.getUTCMonth() + 1) + '-' + pad2(dt.getUTCDate());
  }

  function validYmd(ymd) {
    var p = parseYmd(ymd);
    if (!p || p.mo < 1 || p.mo > 12 || p.d < 1 || p.d > 31) return false;
    var dt = new Date(Date.UTC(p.y, p.mo - 1, p.d));
    return dt.getUTCFullYear() === p.y && dt.getUTCMonth() === p.mo - 1 && dt.getUTCDate() === p.d;
  }

  function addDays(ymd, days) {
    var p = parseYmd(ymd);
    if (!p) return ymd;
    return ymdFromUtc(new Date(Date.UTC(p.y, p.mo - 1, p.d + days)));
  }

  function weekdayIndex(ymd) {
    var p = parseYmd(ymd);
    if (!p) return 0;
    return new Date(Date.UTC(p.y, p.mo - 1, p.d)).getUTCDay();
  }

  function isWeekendYmd(ymd) {
    var day = weekdayIndex(ymd);
    return day === 0 || day === 6;
  }

  function parseHm(value) {
    var m = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!m) return null;
    var h = +m[1];
    var min = +m[2];
    if (h < 0 || h > 24 || min < 0 || min > 59) return null;
    if (h === 24 && min !== 0) return null;
    return h * 60 + min;
  }

  function configOf(config) {
    var src = config || {};
    var hours = src.quoteHours || {};
    var weekday = hours.weekday || DEFAULTS.quoteHours.weekday;
    var weekend = hours.weekend || DEFAULTS.quoteHours.weekend;
    return {
      timezone: src.timezone || DEFAULTS.timezone,
      quotesUrl: src.quotesUrl || '',
      photosUrl: src.photosUrl || '',
      quoteMinutes: src.quoteMinutes > 0 ? src.quoteMinutes : DEFAULTS.quoteMinutes,
      quoteMinPx: src.quoteMinPx > 0 ? src.quoteMinPx : DEFAULTS.quoteMinPx,
      quoteMaxPx: src.quoteMaxPx > 0 ? src.quoteMaxPx : DEFAULTS.quoteMaxPx,
      quoteMarginPx: src.quoteMarginPx > 0 ? src.quoteMarginPx : DEFAULTS.quoteMarginPx,
      epoch: validYmd(src.epoch) ? src.epoch : DEFAULTS.epoch,
      quoteHours: {
        weekday: { start: weekday.start, end: weekday.end },
        weekend: { start: weekend.start, end: weekend.end }
      }
    };
  }

  function pacificParts(date) {
    if (typeof MorningDaily === 'object' && MorningDaily.pacificParts) {
      return MorningDaily.pacificParts(date);
    }
    var fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/Los_Angeles',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23'
    });
    var parts = fmt.formatToParts(date);
    var out = {};
    var i;
    for (i = 0; i < parts.length; i++) out[parts[i].type] = parts[i].value;
    var year = +out.year;
    var month = +out.month;
    var day = +out.day;
    var hour = +out.hour;
    if (hour === 24) {
      hour = 0;
      var next = new Date(Date.UTC(year, month - 1, day + 1));
      year = next.getUTCFullYear();
      month = next.getUTCMonth() + 1;
      day = next.getUTCDate();
    }
    return { year: year, month: month, day: day, hour: hour, minute: +out.minute, second: +out.second };
  }

  function ymdFromParts(p) {
    return p.year + '-' + pad2(p.month) + '-' + pad2(p.day);
  }

  function windowForYmd(ymd, config) {
    var cfg = configOf(config);
    var spec = isWeekendYmd(ymd) ? cfg.quoteHours.weekend : cfg.quoteHours.weekday;
    var start = parseHm(spec.start);
    var end = parseHm(spec.end);
    if (start == null || end == null || end <= start) return null;
    return { start: start, end: end, minutes: cfg.quoteMinutes };
  }

  function slotCount(win) {
    if (!win) return 0;
    return Math.ceil((win.end - win.start) / win.minutes);
  }

  function slotsOn(ymd, config) {
    return slotCount(windowForYmd(ymd, config));
  }

  function slotIndexAt(minute, win) {
    if (!win || minute < win.start || minute >= win.end) return -1;
    var idx = Math.floor((minute - win.start) / win.minutes);
    var n = slotCount(win);
    if (idx < 0) return -1;
    if (idx >= n) return n - 1;
    return idx;
  }

  function advanceForDay(ymd, olderLen, config) {
    var extra = Math.max(0, slotsOn(ymd, config) - 1);
    if (olderLen <= 0) return 0;
    return Math.min(extra, olderLen);
  }

  function continuationBefore(ymd, olderLen, config) {
    var cfg = configOf(config);
    if (olderLen <= 0 || !validYmd(ymd) || ymd <= cfg.epoch) return 0;
    var total = 0;
    var cursor = cfg.epoch;
    while (cursor < ymd) {
      total += advanceForDay(cursor, olderLen, cfg);
      cursor = addDays(cursor, 1);
    }
    return total;
  }

  function planIndices(nQuotes, ymd, config) {
    var cfg = configOf(config);
    var slots = slotsOn(ymd, cfg);
    var plan = [];
    var s;
    if (nQuotes <= 0 || slots <= 0) return plan;
    plan.push(0);
    if (nQuotes === 1) {
      for (s = 1; s < slots; s++) plan.push(0);
      return plan;
    }
    var olderLen = nQuotes - 1;
    var off = continuationBefore(ymd, olderLen, cfg) % olderLen;
    var used = { 0: 1 };
    for (s = 1; s < slots; s++) {
      var pick = null;
      var t;
      var o;
      for (t = 0; t < olderLen; t++) {
        o = (off + t) % olderLen;
        var idx = 1 + o;
        if (!used[idx]) {
          pick = idx;
          off = (o + 1) % olderLen;
          break;
        }
      }
      if (pick == null) pick = s % nQuotes;
      used[pick] = 1;
      plan.push(pick);
    }
    return plan;
  }

  function sortQuotes(list) {
    var copy = list.slice();
    var i;
    for (i = 0; i < copy.length; i++) copy[i]._order = i;
    copy.sort(function (a, b) {
      var da = a.quote_date || '';
      var db = b.quote_date || '';
      if (da !== db) {
        if (!da) return 1;
        if (!db) return -1;
        return da < db ? 1 : -1;
      }
      return a._order - b._order;
    });
    return copy;
  }

  function normalize(data) {
    var rows = [];
    if (Array.isArray(data)) rows = data;
    else if (data && Array.isArray(data.quotes)) rows = data.quotes;
    var out = [];
    var i;
    for (i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (!row || typeof row.text !== 'string' || !row.text.trim()) continue;
      out.push({
        quote_date: row.quote_date || '',
        text: row.text,
        excerpt_credit: row.excerpt_credit || '',
        word_count: row.word_count,
        source_url: row.source_url || ''
      });
    }
    return sortQuotes(out);
  }

  function quoteFor(date, quotes, config) {
    var list = quotes || [];
    if (!list.length || !date) return null;
    var cfg = configOf(config);
    var parts = pacificParts(date);
    var ymd = ymdFromParts(parts);
    var win = windowForYmd(ymd, cfg);
    var minute = parts.hour * 60 + parts.minute;
    var slot = slotIndexAt(minute, win);
    if (slot < 0) return null;
    var plan = planIndices(list.length, ymd, cfg);
    if (slot >= plan.length) return null;
    var index = plan[slot];
    return {
      index: index,
      slot: slot,
      slots: plan.length,
      ymd: ymd,
      quote: list[index]
    };
  }

  function planFor(date, quotes, config) {
    var list = quotes || [];
    var cfg = configOf(config);
    var parts = pacificParts(date);
    var ymd = ymdFromParts(parts);
    var indices = planIndices(list.length, ymd, cfg);
    var plan = [];
    var i;
    for (i = 0; i < indices.length; i++) {
      plan.push({ index: indices[i], quote: list[indices[i]] || null });
    }
    return plan;
  }

  function creditLine(quote) {
    var credit = quote && quote.excerpt_credit ? String(quote.excerpt_credit).trim() : '';
    if (!credit) return 'Abraham-Hicks Publications';
    return credit + ' · Abraham-Hicks Publications';
  }

  function clockText(date) {
    var p = pacificParts(date);
    var h = p.hour;
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return h + ':' + pad2(p.minute) + ' ' + ampm;
  }

  return {
    DEFAULTS: DEFAULTS,
    configOf: configOf,
    normalize: normalize,
    slotsOn: slotsOn,
    quoteFor: quoteFor,
    planFor: planFor,
    creditLine: creditLine,
    clockText: clockText,
    pacificParts: pacificParts,
    addDays: addDays,
    isWeekendYmd: isWeekendYmd
  };
});
