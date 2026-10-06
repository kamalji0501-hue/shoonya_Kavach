/**
 * Kavach subscription API — Google Apps Script Web App
 *
 * Sheet tab: Licenses
 * Columns (row 1 headers, exact names):
 *   Serial Number | Name | Server ID | Start date | End date |
 *   Max sell lots (Directional) | Max ATO lots (Directional) |
 *   Max sell lots (Batman) | Max ATO lots (Batman) | Server Alias | Status
 *
 * Deploy: Deploy > New deployment > Web app
 *   Execute as: Me
 *   Who has access: Anyone
 *
 * Set Script Property SUBSCRIPTION_API_KEY to the same secret as BOT4.
 * Call: GET ?server_id=<url-encoded>&api_key=<secret>
 *   or header X-API-KEY: <secret>
 */

var SHEET_NAME = 'Licenses';

var HEADERS = [
  'Serial Number',
  'Name',
  'Server ID',
  'Start date',
  'End date',
  'Max sell lots (Directional)',
  'Max ATO lots (Directional)',
  'Max sell lots (Batman)',
  'Max ATO lots (Batman)',
  'Server Alias',
  'Status'
];

function doGet(e) {
  try {
    var key = _requestKey(e);
    var expected = PropertiesService.getScriptProperties().getProperty('SUBSCRIPTION_API_KEY') || '';
    if (!expected || key !== expected) {
      return _json({ ok: false, error: 'unauthorized' }, 401);
    }
    var serverId = String((e && e.parameter && e.parameter.server_id) || '').trim();
    if (!serverId) {
      return _json({ ok: false, error: 'server_id required' }, 400);
    }
    var row = _findRow(serverId);
    if (!row) {
      return _json({ ok: false, error: 'not_found', server_id: serverId }, 404);
    }
    return _json({ ok: true, subscription: row });
  } catch (err) {
    return _json({ ok: false, error: String(err) }, 500);
  }
}

function _requestKey(e) {
  if (e && e.parameter && e.parameter.api_key) {
    return String(e.parameter.api_key);
  }
  // Apps Script does not always expose custom headers; prefer query api_key.
  return '';
}

function _normalizeServerId(s) {
  s = String(s || '').trim();
  if (s.length > 1 && s.charAt(s.length - 1) === '/') {
    s = s.substring(0, s.length - 1);
  }
  return s;
}

function _findRow(serverId) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    // Fallback: first tab (in case the tab was left as Sheet1)
    sh = ss.getSheets()[0];
  }
  if (!sh) {
    throw new Error('Sheet tab "' + SHEET_NAME + '" not found and spreadsheet has no sheets');
  }
  var values = sh.getDataRange().getValues();
  if (!values || values.length < 2) {
    return null;
  }
  var header = values[0].map(function (h) { return String(h).trim(); });
  var idx = {};
  HEADERS.forEach(function (h) {
    idx[h] = header.indexOf(h);
  });
  if (idx['Server ID'] < 0) {
    throw new Error('Missing Server ID column');
  }
  var want = _normalizeServerId(serverId);
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    var sid = _normalizeServerId(row[idx['Server ID']]);
    if (!sid) continue;
    if (sid === want) {
      return {
        serial_number: _cell(row, idx['Serial Number']),
        name: _cell(row, idx['Name']),
        server_id: String(row[idx['Server ID']]).trim(),
        start_date: _dateIso(row[idx['Start date']]),
        end_date: _dateIso(row[idx['End date']]),
        max_sell_lots_directional: _int(row, idx['Max sell lots (Directional)']),
        max_ato_lots_directional: _int(row, idx['Max ATO lots (Directional)']),
        max_sell_lots_batman: _int(row, idx['Max sell lots (Batman)']),
        max_ato_lots_batman: _int(row, idx['Max ATO lots (Batman)']),
        server_alias: _cell(row, idx['Server Alias']),
        status: String(_cell(row, idx['Status']) || 'ACTIVE').trim().toUpperCase()
      };
    }
  }
  return null;
}

function _cell(row, i) {
  if (i < 0 || i >= row.length) return '';
  var v = row[i];
  if (v === null || v === undefined) return '';
  return String(v).trim();
}

function _int(row, i) {
  if (i < 0) return null;
  var v = row[i];
  if (v === '' || v === null || v === undefined) return null;
  var n = Number(v);
  if (isNaN(n)) return null;
  return Math.floor(n);
}

function _dateIso(v) {
  if (v && typeof v.getFullYear === 'function') {
    try {
      return Utilities.formatDate(v, 'Asia/Kolkata', 'yyyy-MM-dd');
    } catch (e1) {
      var y = v.getFullYear();
      var m = ('0' + (v.getMonth() + 1)).slice(-2);
      var d = ('0' + v.getDate()).slice(-2);
      return y + '-' + m + '-' + d;
    }
  }
  var s = String(v || '').trim();
  if (!s) return '';
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.substring(0, 10);
  var m = s.match(/^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\b/);
  if (m) {
    var months = {Jan:1,Feb:2,Mar:3,Apr:4,May:5,Jun:6,Jul:7,Aug:8,Sep:9,Oct:10,Nov:11,Dec:12};
    var mo = months[m[1]];
    if (mo) {
      var dd = ('0' + m[2]).slice(-2);
      var mm = ('0' + mo).slice(-2);
      return m[3] + '-' + mm + '-' + dd;
    }
  }
  return s;
}

function _json(obj, code) {
  var out = ContentService.createTextOutput(JSON.stringify(obj));
  out.setMimeType(ContentService.MimeType.JSON);
  // Apps Script web apps cannot set HTTP status reliably on all clients;
  // body.ok / body.error is the contract BOT4 uses.
  return out;
}
