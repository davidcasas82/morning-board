/* Daily style + color for the morning board.
   Board day is the America/Los_Angeles calendar date of (Pacific wall time minus 3 hours).
   2:59 AM Pacific is still the previous board day. 3:00 AM starts the new one.
   The pair is a hash of that YYYY-MM-DD. It is not stored.
   If the hash matches the previous board day's resolved pair, take the next pair.
   Order is fixed: style nixie then segment; color slate, dusk, ember, bone. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.MorningDaily = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  var TZ = 'America/Los_Angeles';
  var EPOCH = '1970-01-01';
  var STYLES = ['nixie', 'segment'];
  var THEMES = ['slate', 'dusk', 'ember', 'bone'];
  var PAIRS = [];
  var s, t;
  for (s = 0; s < STYLES.length; s++) {
    for (t = 0; t < THEMES.length; t++) {
      PAIRS.push({ face: STYLES[s], theme: THEMES[t] });
    }
  }

  function pad2(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function parseYmd(ymd) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd || '');
    if (!m) return null;
    return { y: +m[1], m: +m[2], d: +m[3] };
  }

  function ymdFromUtc(dt) {
    return dt.getUTCFullYear() + '-' + pad2(dt.getUTCMonth() + 1) + '-' + pad2(dt.getUTCDate());
  }

  function validYmd(ymd) {
    var p = parseYmd(ymd);
    if (!p || p.m < 1 || p.m > 12 || p.d < 1 || p.d > 31) return false;
    var dt = new Date(Date.UTC(p.y, p.m - 1, p.d));
    return dt.getUTCFullYear() === p.y && dt.getUTCMonth() === p.m - 1 && dt.getUTCDate() === p.d;
  }

  function addDays(ymd, days) {
    var p = parseYmd(ymd);
    if (!p) return ymd;
    return ymdFromUtc(new Date(Date.UTC(p.y, p.m - 1, p.d + days)));
  }

  function pacificParts(date) {
    var fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: TZ,
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
    /* Some engines write midnight as 24:00 on the previous calendar date. */
    if (hour === 24) {
      hour = 0;
      var next = new Date(Date.UTC(year, month - 1, day + 1));
      year = next.getUTCFullYear();
      month = next.getUTCMonth() + 1;
      day = next.getUTCDate();
    }
    return { year: year, month: month, day: day, hour: hour, minute: +out.minute, second: +out.second };
  }

  /* Subtract 3 hours on the Pacific wall clock, then read that calendar date.
     Shifting the UTC instant instead would land 3:00 AM on the wrong day
     when Pacific springs forward or falls back. */
  function boardDay(date) {
    var p = pacificParts(date);
    var shifted = new Date(Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second || 0) - 3 * 60 * 60 * 1000);
    return ymdFromUtc(shifted);
  }

  function hashYmd(ymd) {
    var h = 2166136261;
    var i;
    for (i = 0; i < ymd.length; i++) {
      h ^= ymd.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function rawIndex(ymd) {
    return hashYmd(ymd) % PAIRS.length;
  }

  var cacheUpto = null;
  var cacheIndex = 0;

  function resolvedIndex(ymd) {
    if (!validYmd(ymd) || ymd < EPOCH) return rawIndex(ymd);
    var idx;
    var cursor;
    if (cacheUpto && ymd >= cacheUpto) {
      idx = cacheIndex;
      cursor = cacheUpto;
    } else {
      idx = rawIndex(EPOCH);
      cursor = EPOCH;
    }
    while (cursor < ymd) {
      cursor = addDays(cursor, 1);
      var raw = rawIndex(cursor);
      idx = raw === idx ? (raw + 1) % PAIRS.length : raw;
    }
    cacheUpto = ymd;
    cacheIndex = idx;
    return idx;
  }

  function pairForBoardDay(ymd) {
    var pair = PAIRS[resolvedIndex(ymd)];
    return { face: pair.face, theme: pair.theme };
  }

  /* A legacy plain string (morning-board-theme / morning-board-face) is not an override.
     Only { board, value } stamped for this board day counts. */
  function activeOverride(raw, board, allowed) {
    if (raw == null || raw === '') return null;
    var parsed = raw;
    if (typeof raw === 'string') {
      try { parsed = JSON.parse(raw); } catch (e) { return null; }
    }
    if (!parsed || typeof parsed !== 'object') return null;
    if (parsed.board !== board) return null;
    if (!allowed || allowed[parsed.value] !== 1) return null;
    return parsed.value;
  }

  return {
    TZ: TZ,
    STYLES: STYLES,
    THEMES: THEMES,
    PAIRS: PAIRS,
    boardDay: boardDay,
    pairForBoardDay: pairForBoardDay,
    addDays: addDays,
    validYmd: validYmd,
    activeOverride: activeOverride,
    pacificParts: pacificParts,
    hashYmd: hashYmd
  };
});
