/* Quote rotation for the morning board.
   Hours come from config. Slot count is the 60-minute slices that fit
   between start and end. Pacific wall time, America/Los_Angeles.
   Slots are numbered across days, so the last slot of one day is next to
   the first slot of the next. Each run of n slots shows a seeded shuffle
   of all n quotes. Same instant, same quote, with no state to keep. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MorningQuotes = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var DEFAULTS = {
    timezone: 'America/Los_Angeles',
    quotesUrl: '',
    photosUrl: '',
    quoteMinutes: 60,
    quoteMinPx: 32,
    quoteMaxPx: 180,
    quoteMarginPx: 150,
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

  var DAY_MS = 86400000;

  function dayNumber(ymd) {
    var p = parseYmd(ymd);
    return Math.floor(Date.UTC(p.y, p.mo - 1, p.d) / DAY_MS);
  }

  function ymdOfDay(day) {
    return ymdFromUtc(new Date(day * DAY_MS));
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

  function slotsBefore(day, config) {
    var week = 0;
    var i;
    for (i = 0; i < 7; i++) week += slotsOn(ymdOfDay(i), config);
    var total = Math.floor(day / 7) * week;
    for (i = day - (day % 7); i < day; i++) total += slotsOn(ymdOfDay(i), config);
    return total;
  }

  function shuffled(n, seed) {
    var state = seed >>> 0;
    var order = [];
    var i;
    for (i = 0; i < n; i++) order.push(i);
    for (i = n - 1; i > 0; i--) {
      state = (state + 0x6d2b79f5) >>> 0;
      var t = Math.imul(state ^ (state >>> 15), state | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      var j = Math.floor((((t ^ (t >>> 14)) >>> 0) / 4294967296) * (i + 1));
      var swap = order[i];
      order[i] = order[j];
      order[j] = swap;
    }
    return order;
  }

  /* A block's first pick only swaps with its second, never its last, so
     comparing against the raw previous block is exact once n > 2. */
  function indexAt(n, slot) {
    if (n === 1) return 0;
    if (n === 2) return slot % 2;
    var block = Math.floor(slot / n);
    var order = shuffled(n, block);
    var previous = shuffled(n, block - 1)[n - 1];
    if (order[0] === previous) {
      order[0] = order[1];
      order[1] = previous;
    }
    return order[slot % n];
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
    return out;
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
    var index = indexAt(list.length, slotsBefore(dayNumber(ymd), cfg) + slot);
    return {
      index: index,
      slot: slot,
      ymd: ymd,
      quote: list[index]
    };
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
    creditLine: creditLine,
    clockText: clockText,
    pacificParts: pacificParts,
    isWeekendYmd: isWeekendYmd
  };
});
