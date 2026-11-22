from __future__ import annotations

import calendar
import http.cookiejar
import csv
import datetime as dt
import http.client
import io
import json
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import index_options_engine as index_engine
import cloud_access
import cloud_runtime

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB_PATH = DATA / 'index_options_research.sqlite3'
STATIC = ROOT / 'static'
LOCAL_MODE = '--local' in sys.argv
HOSTED = not LOCAL_MODE and (
    os.environ.get('INDEX_DEPLOYMENT', '').lower() == 'render'
    or os.environ.get('RENDER', '').lower() == 'true'
)
HOST = '0.0.0.0' if HOSTED else '127.0.0.1'
PORT = int(os.environ.get('PORT', '10000')) if HOSTED else 0
ACTIVE_PORT = PORT
ACCESS = cloud_access.Access(HOSTED)
MAX_UPLOAD = 60_000_000
MAX_UNPACKED_UPLOAD = 180_000_000
MAX_DOWNLOAD_DAYS = 93
JOB_LOCK = threading.Lock()
TASK_LOCK = threading.Lock()
JOB = {'running': False, 'kind': '', 'message': '', 'log': [], 'error': False}


class ClosingConnection(sqlite3.Connection):
    """Close each short-lived database connection when its with-block ends."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

UNIVERSE = [
    {'underlying': 'NIFTY', 'exchange': 'NSE', 'name': 'Nifty 50'},
    {'underlying': 'BANKNIFTY', 'exchange': 'NSE', 'name': 'Nifty Bank'},
    {'underlying': 'SENSEX', 'exchange': 'BSE', 'name': 'Sensex'},
    {'underlying': 'FINNIFTY', 'exchange': 'NSE', 'name': 'FINNIFTY (Nifty Financial Services)'},
    {'underlying': 'BANKEX', 'exchange': 'BSE', 'name': 'Bankex'},
    {'underlying': 'MIDCPNIFTY', 'exchange': 'NSE', 'name': 'Nifty Midcap Select'},
    {'underlying': 'NIFTYNXT50', 'exchange': 'NSE', 'name': 'Nifty Next 50'},
]
BY_SYMBOL = {x['underlying']: x for x in UNIVERSE}
NSE_UNDERLYINGS = tuple(x['underlying'] for x in UNIVERSE if x['exchange'] == 'NSE')
BSE_UNDERLYINGS = tuple(x['underlying'] for x in UNIVERSE if x['exchange'] == 'BSE')
BSE_INDEX_CODES = {'SENSEX': 'SENSEX', 'BANKEX': 'SIBANK'}
ALIASES = {
    'NIFTY': 'NIFTY', 'NIFTY50': 'NIFTY', 'NIFTY50INDEX': 'NIFTY',
    'BANKNIFTY': 'BANKNIFTY', 'NIFTYBANK': 'BANKNIFTY', 'NIFTYBANKINDEX': 'BANKNIFTY',
    'SENSEX': 'SENSEX', 'BSESENSEX': 'SENSEX',
    'FINNIFTY': 'FINNIFTY', 'NIFTYFINANCIALSERVICES': 'FINNIFTY', 'NIFTYFINANCIAL': 'FINNIFTY',
    'BANKEX': 'BANKEX', 'BSEBANKEX': 'BANKEX',
    'MIDCPNIFTY': 'MIDCPNIFTY', 'NIFTYMIDCAPSELECT': 'MIDCPNIFTY', 'NIFTYMIDSELECT': 'MIDCPNIFTY',
    'NIFTYNXT50': 'NIFTYNXT50', 'NIFTYNEXT50': 'NIFTYNXT50',
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS index_bars(
  exchange TEXT NOT NULL, underlying TEXT NOT NULL, day TEXT NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  source TEXT NOT NULL, imported_at TEXT NOT NULL,
  PRIMARY KEY(exchange, underlying, day)
);
CREATE INDEX IF NOT EXISTS index_bars_lookup ON index_bars(underlying, day);
CREATE TABLE IF NOT EXISTS option_bars(
  exchange TEXT NOT NULL, underlying TEXT NOT NULL, day TEXT NOT NULL,
  expiry TEXT NOT NULL, strike REAL NOT NULL, option_type TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL NOT NULL,
  volume REAL NOT NULL, oi REAL NOT NULL, oi_change REAL,
  lot_size REAL, source TEXT NOT NULL, imported_at TEXT NOT NULL,
  PRIMARY KEY(exchange, underlying, day, expiry, strike, option_type)
);
CREATE INDEX IF NOT EXISTS option_bars_lookup ON option_bars(underlying, day, expiry);
CREATE TABLE IF NOT EXISTS futures_bars(
  exchange TEXT NOT NULL, underlying TEXT NOT NULL, day TEXT NOT NULL,
  expiry TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL NOT NULL,
  volume REAL NOT NULL, oi REAL NOT NULL, oi_change REAL,
  lot_size REAL, source TEXT NOT NULL, imported_at TEXT NOT NULL,
  PRIMARY KEY(exchange, underlying, day, expiry)
);
CREATE INDEX IF NOT EXISTS futures_bars_lookup ON futures_bars(underlying, day, expiry);
CREATE TABLE IF NOT EXISTS source_files(
  id INTEGER PRIMARY KEY, dataset TEXT NOT NULL, filename TEXT NOT NULL,
  imported_at TEXT NOT NULL, row_count INTEGER NOT NULL,
  first_day TEXT, last_day TEXT
);
CREATE TABLE IF NOT EXISTS analysis_runs(
  id INTEGER PRIMARY KEY, run_at TEXT NOT NULL, as_of TEXT,
  input_revision TEXT NOT NULL, results_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS index_forecasts(
  id INTEGER PRIMARY KEY, underlying TEXT NOT NULL, exchange TEXT NOT NULL,
  as_of TEXT NOT NULL, model_version TEXT NOT NULL, input_revision TEXT NOT NULL,
  direction TEXT NOT NULL, confidence TEXT NOT NULL, net_score REAL NOT NULL,
  forecast_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(underlying, as_of, model_version, input_revision)
);
CREATE INDEX IF NOT EXISTS index_forecasts_lookup ON index_forecasts(underlying, as_of, model_version, id);
CREATE TABLE IF NOT EXISTS forecast_outcomes(
  id INTEGER PRIMARY KEY, forecast_id INTEGER NOT NULL, actual_day TEXT NOT NULL,
  outcome TEXT NOT NULL, close_change_pct REAL, range_pct REAL,
  outcome_revision TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(forecast_id, outcome_revision)
);
CREATE TABLE IF NOT EXISTS model_evaluations(
  id INTEGER PRIMARY KEY, underlying TEXT NOT NULL, model_version TEXT NOT NULL,
  as_of TEXT NOT NULL, input_revision TEXT NOT NULL, run_at TEXT NOT NULL,
  evaluation_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_evaluations_lookup ON model_evaluations(underlying, model_version, id);
CREATE TABLE IF NOT EXISTS cloud_state(
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
'''

HEADER_ALIASES = {
    'exchange': ['exchange', 'exch'],
    'underlying': ['underlying', 'symbol', 'index', 'indexsymbol', 'indexname', 'underlyingsymbol', 'tckrsymb'],
    'day': ['day', 'date', 'tradingdate', 'tradedate', 'businessdate', 'bizdt', 'timestamp', 'indexdate', 'trad_dt', 'traddt'],
    'expiry': ['expiry', 'expirydate', 'expirydt', 'expiration', 'expirationdate', 'xprydt'],
    'strike': ['strike', 'strikeprice', 'strikepr', 'strkpric'],
    'option_type': ['optiontype', 'optiontyp', 'option_type', 'optn_tp', 'instrumenttype', 'right', 'side', 'cp'],
    'instrument': ['fininstrmtp', 'instrument', 'insttype'],
    'open': ['open', 'openprice', 'opnpric', 'openindexvalue'],
    'high': ['high', 'highprice', 'hghpric', 'highindexvalue'],
    'low': ['low', 'lowprice', 'lwpric', 'lowindexvalue'],
    'close': ['close', 'closeprice', 'clspric', 'closingindexvalue', 'indexclose', 'ltp', 'settle', 'settlementprice'],
    'volume': ['volume', 'tradedquantity', 'tradedqty', 'contracts', 'numberofcontracts', 'ttltradgvol'],
    'oi': ['oi', 'openinterest', 'openint', 'opnintrst'],
    'oi_change': ['oichange', 'changeinoi', 'chginoi', 'changeinopeninterest', 'chnginopnintrst'],
    'lot_size': ['lotsize', 'marketlot', 'contractsize', 'newbrdlotqty'],
}


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


def job_log(message):
    with JOB_LOCK:
        JOB['message'] = message
        JOB['log'] = (JOB.get('log') or [])[-119:] + [message]


def start_job(action, message='Starting task…', kind='task'):
    if not TASK_LOCK.acquire(False):
        raise ValueError('Wait for the current data task to finish.')
    with JOB_LOCK:
        JOB.update(running=True, kind=kind, message=message, log=[], error=False)

    def runner():
        try:
            action()
        except Exception as exc:
            with JOB_LOCK:
                JOB['error'] = True
            job_log(f'Task stopped: {exc}')
        finally:
            with JOB_LOCK:
                JOB['running'] = False
            TASK_LOCK.release()

    threading.Thread(target=runner, daemon=True).start()
    return {'accepted': True}


def norm_header(value):
    return re.sub(r'[^a-z0-9]', '', (value or '').strip().lower())


def find(row, key):
    normalized = {norm_header(k): v for k, v in row.items() if k is not None}
    for alias in HEADER_ALIASES[key]:
        value = normalized.get(norm_header(alias))
        if value is not None and str(value).strip() != '':
            return str(value).strip()
    return None


def parse_date(value, label):
    if not value:
        raise ValueError(f'Missing {label}.')
    value = value.strip()
    if 'T' in value:
        value = value.split('T', 1)[0]
    for pattern in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d-%b-%Y', '%d-%B-%Y', '%d%b%Y', '%Y%m%d'):
        try:
            return dt.datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Could not read {label} date “{value}”. Use YYYY-MM-DD or DD-MM-YYYY.')


def number(value, label, required=False):
    if value is None or not str(value).strip() or str(value).strip().upper() in ('-', '—', 'N/A', 'NA', 'NULL'):
        if required:
            raise ValueError(f'Missing {label}.')
        return None
    try:
        result = float(str(value).replace(',', '').strip())
    except ValueError:
        raise ValueError(f'Invalid {label}: {value}')
    if result != result or result in (float('inf'), float('-inf')):
        raise ValueError(f'Invalid {label}: {value}')
    return result


def symbol_info(value):
    key = re.sub(r'[^A-Z0-9]', '', (value or '').upper())
    symbol = ALIASES.get(key)
    if not symbol:
        raise ValueError(f'{value!r} is outside the fixed seven-index universe.')
    return BY_SYMBOL[symbol]


def option_type(value):
    key = re.sub(r'[^A-Z]', '', (value or '').upper())
    if key in ('CE', 'C', 'CALL', 'CALLS'):
        return 'CE'
    if key in ('PE', 'P', 'PUT', 'PUTS'):
        return 'PE'
    raise ValueError(f'Invalid option type {value!r}; use CE or PE.')


def safe_source(name):
    return (name or 'import.csv').replace('\\', '/').split('/')[-1][:180] or 'import.csv'


def decode_csv(data):
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError('Could not read the CSV encoding.')


def read_records(data, filename, allow_zip=False):
    source_blobs = [(filename, data)]
    if allow_zip and zipfile.is_zipfile(io.BytesIO(data)):
        source_blobs = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = [item for item in archive.infolist() if not item.is_dir() and item.filename.lower().endswith('.csv')]
                if sum(item.file_size for item in entries) > MAX_UNPACKED_UPLOAD:
                    raise ValueError('The ZIP expands beyond the safe import size.')
                source_blobs = [(item.filename, archive.read(item)) for item in entries]
        except zipfile.BadZipFile:
            raise ValueError('The selected ZIP file is damaged or unreadable.')
        if not source_blobs:
            raise ValueError('The ZIP contains no CSV report.')
    for name, blob in source_blobs:
        reader = csv.DictReader(io.StringIO(decode_csv(blob)))
        if reader.fieldnames:
            records = list(reader)
            if records:
                if any(None in row or any(value is None for value in row.values()) for row in records):
                    raise ValueError(f'{name} contains an incomplete CSV row; no rows were imported.')
                return records
    raise ValueError('The selected file contains no CSV data rows.')


def import_csv(dataset, filename, content, selected_underlying=None, from_date=None, to_date=None, expected_day=None):
    if dataset not in ('indexes', 'options', 'futures', 'nse_fo', 'nse_indexes', 'bse_fo', 'bse_indexes'):
        raise ValueError('Choose an NSE/BSE index report, exchange F&O report, or normalized index CSV.')
    from_date = parse_date(from_date, 'start of report range') if from_date else None
    to_date = parse_date(to_date, 'end of report range') if to_date else None
    expected_day = parse_date(expected_day, 'expected report') if expected_day else None
    if from_date and to_date and from_date > to_date:
        raise ValueError('The report range start must be on or before its end.')
    records = read_records(content, filename, allow_zip=(dataset == 'nse_fo'))
    if dataset in ('nse_fo', 'bse_fo'):
        fields = {norm_header(k) for k in records[0] if k}
        groups = [
            {'tckrsymb', 'symbol', 'underlying'},
            {'traddt', 'trad_dt', 'timestamp', 'date', 'tradingdate'},
            {'xprydt', 'expiry', 'expirydate', 'expirydt'},
            {'clspric', 'close', 'closeprice', 'settlementprice'},
            {'ttltradgvol', 'contracts', 'volume', 'numberofcontracts'},
            {'opnintrst', 'openint', 'openinterest', 'oi'},
        ]
        if any(not fields.intersection(group) for group in groups):
            raise ValueError(f'The selected file is not a supported {dataset.split("_")[0].upper()} F&O bhavcopy/contract-wise report.')
    stamp = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    source = safe_source(filename)
    days = []
    day_counts = {}
    symbols_by_day = {}
    types_by_day = {}
    inserted = 0
    with connect() as c:
        try:
            previous_rows = 0
            if expected_day and dataset in ('nse_indexes', 'bse_indexes'):
                exchange = 'NSE' if dataset == 'nse_indexes' else 'BSE'
                underlyings = NSE_UNDERLYINGS if exchange == 'NSE' else BSE_UNDERLYINGS
                placeholders = ','.join('?' for _ in underlyings)
                previous_rows = c.execute(f'''SELECT COUNT(*) FROM index_bars
                    WHERE exchange=? AND day=? AND underlying IN ({placeholders})''',
                    (exchange, expected_day, *underlyings)).fetchone()[0]
                c.execute(f'''DELETE FROM index_bars
                    WHERE exchange=? AND day=? AND underlying IN ({placeholders})''',
                    (exchange, expected_day, *underlyings))
            elif expected_day and dataset in ('nse_fo', 'bse_fo'):
                exchange = 'NSE' if dataset == 'nse_fo' else 'BSE'
                underlyings = NSE_UNDERLYINGS if exchange == 'NSE' else BSE_UNDERLYINGS
                placeholders = ','.join('?' for _ in underlyings)
                previous_rows = sum(c.execute(f'''SELECT COUNT(*) FROM {table}
                    WHERE exchange=? AND day=? AND underlying IN ({placeholders})''',
                    (exchange, expected_day, *underlyings)).fetchone()[0]
                    for table in ('option_bars', 'futures_bars'))
                for table in ('option_bars', 'futures_bars'):
                    c.execute(f'''DELETE FROM {table}
                        WHERE exchange=? AND day=? AND underlying IN ({placeholders})''',
                        (exchange, expected_day, *underlyings))
            for raw in records:
                if dataset in ('nse_indexes', 'bse_indexes'):
                    expected_exchange = 'NSE' if dataset == 'nse_indexes' else 'BSE'
                    try:
                        info = symbol_info(find(raw, 'underlying') or selected_underlying)
                    except ValueError:
                        continue
                    if info['exchange'] != expected_exchange:
                        continue
                    row_dataset = 'indexes'
                elif dataset in ('nse_fo', 'bse_fo'):
                    expected_exchange = 'NSE' if dataset == 'nse_fo' else 'BSE'
                    try:
                        info = symbol_info(find(raw, 'underlying'))
                    except ValueError:
                        continue
                    # Exchange bhavcopies include out-of-scope securities. Inspect them in memory,
                    # but persist only the selected five NSE and two BSE index underlyings.
                    if info['exchange'] != expected_exchange:
                        continue
                    option_raw = find(raw, 'option_type')
                    option_key = re.sub(r'[^A-Z]', '', (option_raw or '').upper())
                    strike_raw = find(raw, 'strike')
                    strike_value = number(strike_raw, 'strike') if strike_raw else None
                    instrument = (find(raw, 'instrument') or '').upper()
                    is_option = bool(option_key in ('CE', 'PE') and strike_value and strike_value > 0)
                    is_future = not is_option and (instrument in ('IDF', 'FUTIDX', 'FUTURE', 'FUTURES') or 'FUT' in instrument or bool(find(raw, 'expiry')))
                    if not is_option and not is_future:
                        continue
                    row_dataset = 'options' if is_option else 'futures'
                else:
                    underlying = find(raw, 'underlying') or (selected_underlying if dataset == 'indexes' else None)
                    info = symbol_info(underlying)
                    row_dataset = dataset
                exchange = (find(raw, 'exchange') or info['exchange']).strip().upper()
                if exchange != info['exchange']:
                    raise ValueError(f'{info["name"]} must use {info["exchange"]}, not {exchange}.')
                day = parse_date(find(raw, 'day'), 'trading')
                if expected_day and day != expected_day:
                    raise ValueError(f'Report date {day} does not match requested trading date {expected_day}.')
                if (from_date and day < from_date) or (to_date and day > to_date):
                    continue
                days.append(day)
                symbols_by_day.setdefault(day, set()).add(info['underlying'])
                types_by_day.setdefault(day, set()).add(row_dataset)
                if row_dataset == 'indexes':
                    o = number(find(raw, 'open'), 'open', True)
                    h = number(find(raw, 'high'), 'high', True)
                    l = number(find(raw, 'low'), 'low', True)
                    close = number(find(raw, 'close'), 'close', True)
                    if min(o, h, l, close) <= 0 or h < max(o, l, close) or l > min(o, h, close):
                        raise ValueError(f'Invalid index OHLC values for {info["underlying"]} on {day}.')
                    c.execute('''INSERT INTO index_bars VALUES(?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(exchange,underlying,day) DO UPDATE SET open=excluded.open,high=excluded.high,
                      low=excluded.low,close=excluded.close,source=excluded.source,imported_at=excluded.imported_at''',
                      (exchange, info['underlying'], day, o, h, l, close, source, stamp))
                elif row_dataset == 'options':
                    expiry = parse_date(find(raw, 'expiry'), 'expiry')
                    if expiry < day:
                        raise ValueError(f'Option expiry {expiry} is before trading date {day}.')
                    strike = number(find(raw, 'strike'), 'strike', True)
                    close = number(find(raw, 'close'), 'option close', True)
                    volume = number(find(raw, 'volume'), 'volume', True)
                    oi = number(find(raw, 'oi'), 'open interest', True)
                    if strike <= 0 or close <= 0 or volume < 0 or oi < 0:
                        raise ValueError(f'Invalid option values for {info["underlying"]} {day} {strike}.')
                    ot = option_type(find(raw, 'option_type'))
                    o = number(find(raw, 'open'), 'option open')
                    h = number(find(raw, 'high'), 'option high')
                    l = number(find(raw, 'low'), 'option low')
                    oi_change = number(find(raw, 'oi_change'), 'OI change')
                    lot_size = number(find(raw, 'lot_size'), 'lot size')
                    c.execute('''INSERT INTO option_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(exchange,underlying,day,expiry,strike,option_type) DO UPDATE SET
                      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                      volume=excluded.volume,oi=excluded.oi,oi_change=excluded.oi_change,lot_size=excluded.lot_size,
                      source=excluded.source,imported_at=excluded.imported_at''',
                      (exchange, info['underlying'], day, expiry, strike, ot, o, h, l, close, volume, oi,
                       oi_change, lot_size, source, stamp))
                else:
                    expiry = parse_date(find(raw, 'expiry'), 'expiry')
                    if expiry < day:
                        raise ValueError(f'Futures expiry {expiry} is before trading date {day}.')
                    close = number(find(raw, 'close'), 'futures close', True)
                    volume = number(find(raw, 'volume'), 'volume', True)
                    oi = number(find(raw, 'oi'), 'open interest', True)
                    if close <= 0 or volume < 0 or oi < 0:
                        raise ValueError(f'Invalid futures values for {info["underlying"]} on {day}.')
                    o = number(find(raw, 'open'), 'futures open')
                    h = number(find(raw, 'high'), 'futures high')
                    l = number(find(raw, 'low'), 'futures low')
                    oi_change = number(find(raw, 'oi_change'), 'OI change')
                    lot_size = number(find(raw, 'lot_size'), 'lot size')
                    c.execute('''INSERT INTO futures_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(exchange,underlying,day,expiry) DO UPDATE SET
                      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                      volume=excluded.volume,oi=excluded.oi,oi_change=excluded.oi_change,lot_size=excluded.lot_size,
                      source=excluded.source,imported_at=excluded.imported_at''',
                      (exchange, info['underlying'], day, expiry, o, h, l, close, volume, oi,
                       oi_change, lot_size, source, stamp))
                inserted += 1
                day_counts[day] = day_counts.get(day, 0) + 1
            if dataset in ('nse_indexes', 'bse_indexes'):
                required = set(NSE_UNDERLYINGS if dataset == 'nse_indexes' else BSE_UNDERLYINGS)
                exchange_name = 'NSE' if dataset == 'nse_indexes' else 'BSE'
                for report_day in day_counts:
                    if report_day >= '2021-01-01':
                        missing = sorted(required - symbols_by_day.get(report_day, set()))
                        if missing:
                            raise ValueError(f'{exchange_name} index report for {report_day} is incomplete: missing {", ".join(missing)}. Existing rows were preserved; retry the download.')
            if dataset in ('nse_fo', 'bse_fo'):
                exchange_name = 'NSE' if dataset == 'nse_fo' else 'BSE'
                min_report_date = '2024-07-08' if dataset == 'nse_fo' else '2024-01-01'
                required_symbols = set(NSE_UNDERLYINGS if dataset == 'nse_fo' else BSE_UNDERLYINGS)
                for report_day, row_count in day_counts.items():
                    if report_day >= min_report_date:
                        covered_types = types_by_day.get(report_day, set())
                        if (row_count < 25 or not required_symbols.issubset(symbols_by_day.get(report_day, set()))
                                or not {'options', 'futures'}.issubset(covered_types)):
                            raise ValueError(f'{exchange_name} F&O report for {report_day} failed the completeness checks. Existing rows were preserved; retry the download.')
                if expected_day and previous_rows and day_counts.get(expected_day, 0) < previous_rows * 0.8:
                    raise ValueError(f'The refreshed F&O report has materially fewer index contracts than the stored copy for {expected_day}. Existing rows were preserved for review; retry the download.')
            if not inserted:
                if from_date or to_date:
                    c.rollback()
                    return {'rows': 0, 'first_day': None, 'last_day': None, 'dataset': dataset}
                if dataset in ('nse_fo', 'bse_fo'):
                    expected_exchange = 'NSE' if dataset == 'nse_fo' else 'BSE'
                    names = ', '.join(NSE_UNDERLYINGS if dataset == 'nse_fo' else BSE_UNDERLYINGS)
                    raise ValueError(f'No supported {expected_exchange} index contracts were found for {names}.')
                raise ValueError('No usable rows were found for the selected index dataset.')
            for report_day, row_count in sorted(day_counts.items()):
                c.execute('INSERT INTO source_files(dataset,filename,imported_at,row_count,first_day,last_day) VALUES(?,?,?,?,?,?)',
                          (dataset, source, stamp, row_count, report_day, report_day))
        except Exception:
            c.rollback()
            raise
    return {'rows': inserted, 'first_day': min(days), 'last_day': max(days), 'dataset': dataset}


def fetch_nse_report(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://www.nseindia.com/',
        'Accept': '*/*',
    })
    with urllib.request.urlopen(request, timeout=35) as response:
        declared_size = response.headers.get('Content-Length')
        if declared_size:
            try:
                declared_size = int(declared_size)
            except ValueError as exc:
                raise ValueError('NSE returned an invalid report size header.') from exc
            if declared_size <= 0:
                raise ValueError('NSE returned an empty report.')
            if declared_size > 50_000_000:
                raise ValueError('Report exceeded the 50 MB safety limit.')
        payload = response.read(50_000_001)
    if len(payload) > 50_000_000:
        raise ValueError('Report exceeded the 50 MB safety limit.')
    if not payload:
        raise ValueError('NSE returned an empty report.')
    if declared_size is not None and len(payload) != declared_size:
        raise ValueError(f'The NSE response was incomplete ({len(payload):,} of {declared_size:,} bytes); stored data was left untouched.')
    return payload


def open_bse_session():
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    request = urllib.request.Request('https://www.bseindia.com/indices/IndexArchiveData.html', headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36',
        'Referer': 'https://www.bseindia.com/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    with opener.open(request, timeout=25) as response:
        if response.status != 200:
            raise ValueError(f'Could not establish the BSE report session (HTTP {response.status}).')
        response.read(250_001)
    return opener


def fetch_bse_report(url, opener):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36',
        'Origin': 'https://www.bseindia.com',
        'Referer': 'https://www.bseindia.com/indices/IndexArchiveData.html',
        'Accept': 'text/csv, application/json, application/octet-stream, */*',
        'Sec-Fetch-Site': 'same-site',
        'Sec-Fetch-Mode': 'cors',
    })
    with opener.open(request, timeout=35) as response:
        declared_size = response.headers.get('Content-Length')
        if declared_size:
            try:
                declared_size = int(declared_size)
            except ValueError as exc:
                raise ValueError('BSE returned an invalid report size header.') from exc
            if declared_size <= 0:
                raise ValueError('BSE returned an empty report.')
            if declared_size > 50_000_000:
                raise ValueError('BSE report exceeded the 50 MB safety limit.')
        payload = response.read(50_000_001)
    if len(payload) > 50_000_000:
        raise ValueError('BSE report exceeded the 50 MB safety limit.')
    if not payload:
        raise ValueError('BSE returned an empty report for this date range.')
    if declared_size is not None and len(payload) != declared_size:
        raise ValueError(f'The BSE response was incomplete ({len(payload):,} of {declared_size:,} bytes); stored data was left untouched.')
    if payload.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        raise ValueError('BSE returned a web page instead of a market report.')
    return payload


def fetch_bse_index_range(start, end, opener):
    first = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    reports = {}
    for underlying, index_code in BSE_INDEX_CODES.items():
        query = urlencode({
            'strIndex': index_code,
            'dtFromDate': first.strftime('%d/%m/%Y'),
            'dtToDate': last.strftime('%d/%m/%Y'),
            'period': 'D',
        })
        url = f'https://api.bseindia.com/BseIndiaAPI/api/ProduceCSVForDate/w?{query}'
        payload = fetch_bse_report(url, opener)
        records = read_records(payload, f'{underlying}.csv')
        normalized = {}
        for raw in records:
            day = parse_date(find(raw, 'day'), 'BSE index trading')
            if day < start or day > end:
                raise ValueError(f'BSE {underlying} report included an out-of-range date {day}.')
            if day in normalized:
                raise ValueError(f'BSE {underlying} report contains duplicate rows for {day}.')
            normalized[day] = {
                'exchange': 'BSE', 'underlying': underlying, 'date': day,
                'open': find(raw, 'open'), 'high': find(raw, 'high'),
                'low': find(raw, 'low'), 'close': find(raw, 'close'),
            }
        if not normalized:
            raise ValueError(f'BSE returned no {underlying} index rows from {start} through {end}.')
        reports[underlying] = normalized

    sensex_days = set(reports['SENSEX'])
    bankex_days = set(reports['BANKEX'])
    if sensex_days != bankex_days:
        missing_sensex = sorted(bankex_days - sensex_days)
        missing_bankex = sorted(sensex_days - bankex_days)
        raise ValueError(f'BSE index reports do not cover the same sessions; missing Sensex={missing_sensex[:3]}, Bankex={missing_bankex[:3]}. Existing rows were preserved.')

    output = io.StringIO(newline='')
    fields = ('exchange', 'underlying', 'date', 'open', 'high', 'low', 'close')
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for underlying in ('SENSEX', 'BANKEX'):
        for day in sorted(reports[underlying]):
            writer.writerow(reports[underlying][day])
    return output.getvalue().encode('utf-8')


def download_reports(start, end, segments):
    first = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    if first > last:
        raise ValueError('Choose a start date on or before the end date.')
    if (last - first).days >= MAX_DOWNLOAD_DAYS:
        raise ValueError('Choose a range of at most 93 calendar days per download.')
    if last > dt.date.today():
        raise ValueError('The end date cannot be in the future.')
    allowed_segments = ('indexes', 'nse_fo', 'bse_indexes', 'bse_fo')
    if not segments or any(x not in allowed_segments for x in segments):
        raise ValueError('Select one or more NSE or BSE index report types.')

    checked = {'indexes': 0, 'nse_fo': 0, 'bse_indexes': 0, 'bse_fo': 0}
    unavailable = 0
    job_log(f'Downloading selected NSE and BSE index reports from {start} through {end}.')
    bse_opener = None
    if 'bse_indexes' in segments or 'bse_fo' in segments:
        try:
            bse_opener = open_bse_session()
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError) as exc:
            unavailable += 1
            job_log(f'BSE report session could not be established: {exc}. No BSE rows were changed.')
    if 'bse_indexes' in segments and bse_opener:
        job_log(f'{start} to {end} · fetching fresh BSE Sensex and Bankex index OHLC history.')
        try:
            payload = fetch_bse_index_range(start, end, bse_opener)
            result = import_csv('bse_indexes', f'BSE_index_OHLC_{start}_{end}.csv', payload,
                                from_date=start, to_date=end)
            if not result['rows']:
                raise ValueError('BSE returned no Sensex/Bankex sessions in the selected range.')
            checked['bse_indexes'] = result['rows']
            job_log(f'{start} to {end} · validated and updated {result["rows"]:,} BSE index rows.')
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError, sqlite3.Error) as exc:
            unavailable += 1
            job_log(f'{start} to {end} · BSE index OHLC not refreshed: {exc}. Existing rows were preserved; retry later.')
    cursor = first
    while cursor <= last:
        day = cursor.isoformat()
        if cursor.weekday() < 5:
            if 'indexes' in segments:
                filename = f'ind_close_all_{cursor:%d%m%Y}.csv'
                url = f'https://nsearchives.nseindia.com/content/indices/{filename}'
                job_log(f'{day} · fetching a fresh NSE index OHLC report.')
                try:
                    result = import_csv('nse_indexes', filename, fetch_nse_report(url), expected_day=day)
                    checked['indexes'] += 1
                    job_log(f'{day} · validated and replaced {result["rows"]:,} index rows.')
                except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError, sqlite3.Error) as exc:
                    unavailable += 1
                    job_log(f'{day} · index OHLC not refreshed: {exc}. Existing rows were preserved; retry later if this was a trading date.')

            if 'nse_fo' in segments:
                if cursor >= dt.date(2024, 7, 8):
                    filename = f'BhavCopy_NSE_FO_0_0_0_{cursor:%Y%m%d}_F_0000.csv.zip'
                    url = f'https://nsearchives.nseindia.com/content/fo/{filename}'
                else:
                    filename = f'fo{cursor:%d%b%Y}bhav.csv.zip'.upper().replace('FO', 'fo', 1)
                    url = f'https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{cursor:%Y}/{cursor:%b}'.upper()
                    url = url.replace('HTTPS://NSEARCHIVES.NSEINDIA.COM/CONTENT/HISTORICAL/DERIVATIVES/', 'https://nsearchives.nseindia.com/content/historical/DERIVATIVES/') + '/' + filename
                job_log(f'{day} · fetching a fresh NSE index futures/options report.')
                try:
                    result = import_csv('nse_fo', filename, fetch_nse_report(url), expected_day=day)
                    checked['nse_fo'] += 1
                    job_log(f'{day} · validated and replaced {result["rows"]:,} index contracts; company contracts discarded.')
                except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError, sqlite3.Error) as exc:
                    unavailable += 1
                    job_log(f'{day} · F&O report not refreshed: {exc}. Existing rows were preserved; retry later if this was a trading date.')

            if 'bse_fo' in segments and bse_opener:
                filename = f'BhavCopy_BSE_FO_0_0_0_{cursor:%Y%m%d}_F_0000.CSV'
                url = f'https://www.bseindia.com/download/Bhavcopy/Derivative/{filename}'
                job_log(f'{day} · fetching a fresh BSE index futures/options bhavcopy.')
                try:
                    result = import_csv('bse_fo', filename, fetch_bse_report(url, bse_opener), expected_day=day)
                    checked['bse_fo'] += 1
                    job_log(f'{day} · validated and replaced {result["rows"]:,} BSE index contracts; other securities discarded.')
                except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError, sqlite3.Error) as exc:
                    unavailable += 1
                    job_log(f'{day} · BSE F&O report not refreshed: {exc}. Existing rows were preserved; retry later if this was a trading date.')
        cursor += dt.timedelta(days=1)
    job_log(f'Exchange downloads finished · NSE index report days: {checked["indexes"]:,} · NSE F&O days: {checked["nse_fo"]:,} · BSE index rows: {checked["bse_indexes"]:,} · BSE F&O days: {checked["bse_fo"]:,} · unavailable: {unavailable:,}.')
    if unavailable:
        with JOB_LOCK:
            JOB['error'] = True


def mean(values):
    return sum(values) / len(values) if values else None


def rounded(value, digits=2):
    return round(value, digits) if value is not None else None


def index_state(c, item, as_of=None):
    symbol, exchange = item['underlying'], item['exchange']
    if as_of:
        rows = [dict(r) for r in c.execute('SELECT * FROM index_bars WHERE underlying=? AND day<=? ORDER BY day DESC LIMIT 100', (symbol, as_of))]
    else:
        rows = [dict(r) for r in c.execute('SELECT * FROM index_bars WHERE underlying=? ORDER BY day DESC LIMIT 100', (symbol,))]
    rows.reverse()
    latest = rows[-1] if rows else None
    if not latest:
        return {**item, 'day': None, 'close': None, 'return_1': None, 'return_5': None, 'sma20': None, 'atr14': None,
                'trend': 'No index data', 'options': None, 'futures': None, 'bars': [], 'chain': [], 'expiries': []}
    closes = [r['close'] for r in rows]
    ret1 = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 else None
    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
    sma20 = mean(closes[-20:]) if len(closes) >= 20 else None
    trs = []
    for i, row in enumerate(rows):
        previous = rows[i - 1]['close'] if i else row['close']
        trs.append(max(row['high'] - row['low'], abs(row['high'] - previous), abs(row['low'] - previous)))
    atr14 = mean(trs[-14:]) if len(trs) >= 14 else None
    if sma20 is None or ret5 is None:
        trend = 'Building history'
    elif latest['close'] > sma20 and ret5 > 0:
        trend = 'Upward structure'
    elif latest['close'] < sma20 and ret5 < 0:
        trend = 'Downward structure'
    else:
        trend = 'Mixed structure'
    opt_day_row = c.execute('SELECT MAX(day) d FROM option_bars WHERE underlying=? AND day<=?', (symbol, latest['day'])).fetchone()
    option_day = opt_day_row['d']
    expiries = []
    chain = []
    options = None
    map_expiries = []
    option_previous_day = None
    option_spot = None
    if option_day:
        option_spot_row = c.execute('''SELECT close FROM index_bars
            WHERE underlying=? AND day<=? ORDER BY day DESC LIMIT 1''', (symbol, option_day)).fetchone()
        option_spot = option_spot_row['close'] if option_spot_row else latest['close']
        previous_option_row = c.execute('''SELECT MAX(day) AS day FROM option_bars
            WHERE underlying=? AND day<?''', (symbol, option_day)).fetchone()
        option_previous_day = previous_option_row['day']
        previous_groups = {}
        if option_previous_day:
            previous_chain = c.execute('''SELECT expiry,option_type,oi FROM option_bars
                WHERE underlying=? AND day=?''', (symbol, option_previous_day)).fetchall()
            for row in previous_chain:
                old_group = previous_groups.setdefault(row['expiry'], {'call_oi': 0.0, 'put_oi': 0.0})
                old_group['call_oi' if row['option_type'] == 'CE' else 'put_oi'] += row['oi'] or 0
        chain = [dict(r) for r in c.execute('''SELECT expiry,strike,option_type,close,volume,oi,oi_change,lot_size
            FROM option_bars WHERE underlying=? AND day=? ORDER BY expiry,strike,option_type''', (symbol, option_day))]
        groups = {}
        reported_change_sides = set()
        for row in chain:
            exp = row['expiry']
            group = groups.setdefault(exp, {'expiry': exp, 'contracts': 0, 'calls': 0, 'puts': 0,
                'call_oi': 0.0, 'put_oi': 0.0, 'call_oi_change': 0.0, 'put_oi_change': 0.0,
                'call_volume': 0.0, 'put_volume': 0.0, 'near_atm_contracts': 0})
            group['contracts'] += 1
            side = 'call' if row['option_type'] == 'CE' else 'put'
            group[f'{side}s'] += 1
            group[f'{side}_oi'] += row['oi'] or 0
            group[f'{side}_oi_change'] += row['oi_change'] or 0
            if row['oi_change'] is not None:
                reported_change_sides.add((exp, side))
            group[f'{side}_volume'] += row['volume'] or 0
            if option_spot and abs(row['strike'] / option_spot - 1) <= 0.03:
                group['near_atm_contracts'] += 1
        for exp, group in sorted(groups.items()):
            current_pcr = group['put_oi'] / group['call_oi'] if group['call_oi'] else None
            prior = previous_groups.get(exp)
            prior_pcr = prior['put_oi'] / prior['call_oi'] if prior and prior['call_oi'] else None
            group['put_call_oi'] = rounded(current_pcr)
            group['pcr_oi_1d_pct'] = rounded((current_pcr - prior_pcr) / abs(prior_pcr) * 100) if current_pcr is not None and prior_pcr not in (None, 0) else None
            if prior:
                group['call_oi_change'] = group['call_oi'] - prior['call_oi']
                group['put_oi_change'] = group['put_oi'] - prior['put_oi']
                group['call_oi_change_pct'] = rounded(group['call_oi_change'] / prior['call_oi'] * 100) if prior['call_oi'] else None
                group['put_oi_change_pct'] = rounded(group['put_oi_change'] / prior['put_oi'] * 100) if prior['put_oi'] else None
            else:
                if (exp, 'call') not in reported_change_sides:
                    group['call_oi_change'] = None
                if (exp, 'put') not in reported_change_sides:
                    group['put_oi_change'] = None
                group['call_oi_change_pct'] = None
                group['put_oi_change_pct'] = None
            group['days_to_expiry'] = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(option_day)).days
            expiries.append(group)
        map_expiries = [group['expiry'] for group in expiries if group['days_to_expiry'] >= 0][:2]
        near = next((x for x in expiries if x['days_to_expiry'] >= 0), None)
        if near:
            options = {**near, 'day': option_day, 'freshness': 'Same day' if option_day == latest['day'] else 'Earlier date',
                       'coverage': 'Both sides observed' if near['calls'] and near['puts'] else 'One side or no contracts'}
    fut = c.execute('''SELECT expiry,close,volume,oi,oi_change,lot_size FROM futures_bars
        WHERE underlying=? AND day=? ORDER BY expiry LIMIT 1''', (symbol, latest['day'])).fetchone()
    future = dict(fut) if fut else None
    if future:
        future['basis_pct'] = rounded((future['close'] / latest['close'] - 1) * 100) if latest['close'] else None
    selected_chain = [r for r in chain if r['expiry'] in map_expiries and option_spot and abs(r['strike'] / option_spot - 1) <= 0.10]
    return {**item, 'day': latest['day'], 'close': rounded(latest['close'], 2), 'return_1': rounded(ret1), 'return_5': rounded(ret5),
            'sma20': rounded(sma20, 2), 'atr14': rounded(atr14, 2), 'trend': trend,
            'options': options, 'futures': future, 'option_day': option_day,
            'option_previous_day': option_previous_day, 'option_spot': rounded(option_spot, 2) if option_spot else None,
            'map_expiries': map_expiries,
            'bars': [{'day': r['day'], 'close': rounded(r['close'], 2)} for r in rows[-60:]],
            'chain': selected_chain, 'expiries': expiries}


def option_oi_snapshot(c, symbol, day, spot):
    if not day or not spot or spot <= 0:
        return None
    rows = c.execute('''SELECT expiry,strike,option_type,close,volume,oi FROM option_bars
        WHERE underlying=? AND day=? AND volume>0 AND oi>0 AND close>0
        ORDER BY expiry,strike,option_type''', (symbol, day)).fetchall()
    eligible = []
    for row in rows:
        dte = (dt.date.fromisoformat(row['expiry']) - dt.date.fromisoformat(day)).days
        if 2 <= dte <= 90 and abs(row['strike'] / spot - 1) <= 0.10:
            eligible.append(row)
    if not eligible:
        return None
    expiry = min(row['expiry'] for row in eligible)
    chain = [row for row in eligible if row['expiry'] == expiry]
    calls = sum(row['oi'] for row in chain if row['option_type'] == 'CE')
    puts = sum(row['oi'] for row in chain if row['option_type'] == 'PE')
    return {'expiry': expiry, 'pcr_oi': rounded(puts / calls) if calls > 0 else None,
            'call_oi': calls, 'put_oi': puts, 'contracts': len(chain)}


def futures_oi_snapshot(c, symbol, day, traded_only=True):
    if not day:
        return []
    volume_filter = 'AND volume>0' if traded_only else ''
    return [dict(row) for row in c.execute('''SELECT expiry,oi,volume,lot_size FROM futures_bars
        WHERE underlying=? AND day=? {volume_filter} AND expiry>? ORDER BY expiry'''.format(volume_filter=volume_filter), (symbol, day, day))]


def analysis_metrics(c, item, as_of=None):
    symbol = item['underlying']
    if as_of:
        cash_rows = [dict(row) for row in c.execute('''SELECT day,close FROM index_bars
            WHERE underlying=? AND day<=? ORDER BY day''', (symbol, as_of))]
    else:
        cash_rows = [dict(row) for row in c.execute('''SELECT day,close FROM index_bars
            WHERE underlying=? ORDER BY day''', (symbol,))]
    if not cash_rows:
        return {**item, 'day': None, 'option_expiry': None, 'pcr_oi': None, 'pcr_oi_1d_pct': None,
                'index_close': None, 'index_close_1d_pct': None,
                'futures_expiry': None, 'futures_oi': None, 'futures_oi_1d_pct': None,
                'futures_oi_5d_pct': None, 'futures_5d_observations': 0, 'futures_5d_start_day': None}

    day = cash_rows[-1]['day']
    spot = cash_rows[-1]['close']
    cash_days = [row['day'] for row in cash_rows]
    previous_day = cash_days[-2] if len(cash_days) >= 2 else None
    five_day_start = cash_days[-6] if len(cash_days) >= 6 else None
    index_close_1d_pct = rounded((spot / cash_rows[-2]['close'] - 1) * 100) if len(cash_rows) >= 2 and cash_rows[-2]['close'] else None

    current_options = option_oi_snapshot(c, symbol, day, spot)
    prior_options = option_oi_snapshot(c, symbol, previous_day,
        cash_rows[-2]['close'] if len(cash_rows) >= 2 else None) if previous_day else None
    pcr_change = None
    if (current_options and prior_options and current_options['expiry'] == prior_options['expiry']
            and prior_options['pcr_oi'] is not None and prior_options['pcr_oi'] > 0
            and current_options['pcr_oi'] is not None):
        pcr_change = rounded((current_options['pcr_oi'] - prior_options['pcr_oi']) /
                             abs(prior_options['pcr_oi']) * 100)

    current_futures = futures_oi_snapshot(c, symbol, day)
    nearest = current_futures[0] if current_futures else None
    prior_futures = futures_oi_snapshot(c, symbol, previous_day) if previous_day else []
    futures_1d = None
    prior_nearest = prior_futures[0] if prior_futures else None
    if nearest and prior_nearest and nearest['expiry'] == prior_nearest['expiry']:
        old_oi = prior_nearest['oi']
        if old_oi is not None and old_oi > 0 and nearest['oi'] is not None:
            futures_1d = rounded((nearest['oi'] - old_oi) / old_oi * 100)

    futures_5d = None
    observations = 0
    if len(cash_days) >= 6:
        window = cash_days[-6:]
        observations = sum(bool(c.execute('SELECT 1 FROM futures_bars WHERE underlying=? AND day=? LIMIT 1',
                             (symbol, session)).fetchone()) for session in window)
        old_rows = {row['expiry']: dict(row) for row in c.execute('''SELECT expiry,oi,lot_size FROM futures_bars
            WHERE underlying=? AND day=?''', (symbol, five_day_start)).fetchall()}
        matched = []
        for future in current_futures[:3]:
            old = old_rows.get(future['expiry'])
            if not old or old['oi'] is None or old['oi'] <= 0 or future['oi'] is None:
                continue
            if future.get('lot_size') and old.get('lot_size') and future['lot_size'] != old['lot_size']:
                continue
            matched.append((future, old))
        old_total = sum(old['oi'] for _, old in matched)
        new_total = sum(now['oi'] for now, _ in matched)
        if matched and old_total > 0:
            futures_5d = rounded((new_total - old_total) / old_total * 100)

    return {**item, 'day': day, 'index_close': rounded(spot, 2),
            'index_close_1d_pct': index_close_1d_pct,
            'option_expiry': current_options['expiry'] if current_options else None,
            'pcr_oi': current_options['pcr_oi'] if current_options else None,
            'pcr_oi_1d_pct': pcr_change,
            'futures_expiry': nearest['expiry'] if nearest else None,
            'futures_oi': nearest['oi'] if nearest else None,
            'futures_oi_1d_pct': futures_1d,
            'futures_oi_5d_pct': futures_5d,
            'futures_5d_observations': observations,
            'futures_5d_start_day': five_day_start}


def subtract_calendar_months(day, months):
    source = dt.date.fromisoformat(day)
    month_index = source.year * 12 + source.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    return dt.date(year, month, min(source.day, calendar.monthrange(year, month)[1])).isoformat()


def analysis_history(c, item, as_of, months=2):
    """Return all trading sessions in the two calendar months through as_of, newest first."""
    start_day = subtract_calendar_months(as_of, months)
    days = [row['day'] for row in c.execute('''SELECT day FROM index_bars
        WHERE underlying=? AND day>=? AND day<=? ORDER BY day DESC''',
        (item['underlying'], start_day, as_of)).fetchall()]
    return [analysis_metrics(c, item, day) for day in days]


def data_revision(c, as_of=None):
    revision = {}
    for key, table in (('index', 'index_bars'), ('options', 'option_bars'), ('futures', 'futures_bars')):
        if as_of:
            row = c.execute(f'SELECT COUNT(*) AS rows,MAX(imported_at) AS imported FROM {table} WHERE day<=?', (as_of,)).fetchone()
        else:
            row = c.execute(f'SELECT COUNT(*) AS rows,MAX(imported_at) AS imported FROM {table}').fetchone()
        revision[key] = {'rows': row['rows'], 'latest_import': row['imported']}
    if as_of:
        revision['source_id'] = c.execute('SELECT COALESCE(MAX(id),0) FROM source_files WHERE first_day<=? AND last_day<=?',
                                          (as_of, as_of)).fetchone()[0]
    else:
        revision['source_id'] = c.execute('SELECT COALESCE(MAX(id),0) FROM source_files').fetchone()[0]
    return json.dumps(revision, sort_keys=True)


def save_forecast(c, forecast):
    if not forecast.get('as_of'):
        return None
    revision = data_revision(c, forecast['as_of'])
    created_at = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    c.execute('''INSERT OR IGNORE INTO index_forecasts
        (underlying,exchange,as_of,model_version,input_revision,direction,confidence,net_score,forecast_json,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)''',
        (forecast['underlying'], forecast['exchange'], forecast['as_of'], forecast['model_version'], revision,
         forecast['direction'], forecast['confidence'], forecast['net_score'],
         json.dumps(forecast, ensure_ascii=False), created_at))
    row = c.execute('''SELECT id FROM index_forecasts
        WHERE underlying=? AND as_of=? AND model_version=? AND input_revision=?''',
        (forecast['underlying'], forecast['as_of'], forecast['model_version'], revision)).fetchone()
    return row['id'] if row else None


def mature_forecasts(c, through_day):
    """Attach the first subsequently imported index session to each saved forecast."""
    rows = c.execute('''SELECT id,underlying,as_of,forecast_json FROM index_forecasts
        WHERE model_version=? AND as_of<? ORDER BY as_of,id''', (index_engine.MODEL_VERSION, through_day)).fetchall()
    revisions = {}
    for saved in rows:
        next_bar = c.execute('''SELECT day,open,high,low,close FROM index_bars
            WHERE underlying=? AND day>? AND day<=? ORDER BY day LIMIT 1''',
            (saved['underlying'], saved['as_of'], through_day)).fetchone()
        if not next_bar:
            continue
        previous = c.execute('''SELECT day,open,high,low,close FROM index_bars
            WHERE underlying=? AND day=?''', (saved['underlying'], saved['as_of'])).fetchone()
        if not previous:
            continue
        prediction = json.loads(saved['forecast_json'])
        outcome = index_engine.actual_outcome(dict(previous), dict(next_bar), prediction)
        if not outcome:
            continue
        actual_day = next_bar['day']
        if actual_day not in revisions:
            revisions[actual_day] = data_revision(c, actual_day)
        revision = revisions[actual_day]
        c.execute('''INSERT OR IGNORE INTO forecast_outcomes
            (forecast_id,actual_day,outcome,close_change_pct,range_pct,outcome_revision,created_at)
            VALUES(?,?,?,?,?,?,?)''',
            (saved['id'], next_bar['day'], outcome['label'], outcome['close_change_pct'], outcome['range_pct'],
             revision, dt.datetime.now().astimezone().isoformat(timespec='seconds')))


def run_analysis(as_of=None):
    as_of = parse_date(as_of, 'analysis cutoff') if as_of else dt.date.today().isoformat()
    with connect() as c:
        results = []
        for item in UNIVERSE:
            row = analysis_metrics(c, item, as_of)
            row['history'] = analysis_history(c, item, as_of, 2)
            row['forecast'] = index_engine.forecast(c, item, as_of)
            results.append(row)
        available = [row for row in results if row['day']]
        if not available:
            raise ValueError('Import index OHLC data before running analysis.')
        as_of = max(row['day'] for row in available)
        for row in results:
            if row['forecast'].get('as_of'):
                save_forecast(c, row['forecast'])
        # Historical date selections are read-only research snapshots. Forecast outcomes
        # can only mature when analysing the latest imported session, so avoid scanning
        # the full forecast history on every historical date change (local and Render).
        latest_day = c.execute('SELECT MAX(day) FROM index_bars WHERE day<=?',
                               (dt.date.today().isoformat(),)).fetchone()[0]
        if as_of == latest_day:
            mature_forecasts(c, as_of)
        revision = data_revision(c, as_of)
        run_at = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        c.execute('INSERT INTO analysis_runs(run_at,as_of,input_revision,results_json) VALUES(?,?,?,?)',
                  (run_at, as_of, revision, json.dumps(results, ensure_ascii=False)))
        c.execute('DELETE FROM analysis_runs WHERE id NOT IN (SELECT id FROM analysis_runs ORDER BY id DESC LIMIT 100)')
    for row in results:
        if row['day']:
            fc = row['forecast']
            job_log(f"{row['underlying']} · {fc['direction']} ({fc['confidence']}; net {fc['net_score']:+.1f}) · PCR OI {row['pcr_oi'] if row['pcr_oi'] is not None else 'N/A'} · Futures OI 1D {row['futures_oi_1d_pct'] if row['futures_oi_1d_pct'] is not None else 'N/A'}% / 5D {row['futures_oi_5d_pct'] if row['futures_oi_5d_pct'] is not None else 'N/A'}%.")
    job_log(f'Analysis and provisional forecasts saved through {as_of}; data available for {len(available)} of {len(UNIVERSE)} indices.')


def run_evaluation(underlying, as_of=None):
    item = BY_SYMBOL.get(underlying)
    if not item:
        raise ValueError('Choose one of the seven supported indices.')
    as_of = parse_date(as_of, 'evaluation cutoff') if as_of else dt.date.today().isoformat()
    with connect() as c:
        evaluation = index_engine.evaluate_history(c, item, as_of, UNIVERSE)
        bars = c.execute('SELECT MAX(day) AS day FROM index_bars WHERE underlying=? AND day<=?',
                         (underlying, as_of)).fetchone()
        if not bars or not bars['day']:
            raise ValueError(f'Import index OHLC history for {item["name"]} before evaluating.')
        evaluation['as_of'] = bars['day']
        revision = data_revision(c, bars['day'])
        c.execute('''INSERT INTO model_evaluations
            (underlying,model_version,as_of,input_revision,run_at,evaluation_json)
            VALUES(?,?,?,?,?,?)''',
            (underlying, index_engine.MODEL_VERSION, bars['day'], revision,
             dt.datetime.now().astimezone().isoformat(timespec='seconds'),
             json.dumps(evaluation, ensure_ascii=False)))
    holdout = next(split for split in evaluation['splits'] if split['period'] == 'Latest 20% · holdout')
    job_log(f"{item['name']} · {evaluation['replay_observations']} replay observations · holdout directional calls {holdout['directional_calls']} · hit rate {holdout['directional_hit_rate_pct'] if holdout['directional_hit_rate_pct'] is not None else 'N/A'}%.")
    job_log(evaluation['replay_note'])


def get_state(as_of=None):
    init_db()
    with connect() as c:
        today = dt.date.today().isoformat()
        date_limits = c.execute('SELECT MIN(day) AS first,MAX(day) AS latest FROM index_bars WHERE day<=?', (today,)).fetchone()
        latest_date = date_limits['latest']
        selected_as_of = parse_date(as_of, 'research cutoff') if as_of else (latest_date or today)
        if selected_as_of > today:
            raise ValueError('Choose a research date on or before today.')
        indexes = [index_state(c, item, selected_as_of) for item in UNIVERSE]
        for item, index in zip(UNIVERSE, indexes):
            index['forecast'] = None
            if not index.get('day'):
                continue
            saved = c.execute('''SELECT id,input_revision,forecast_json FROM index_forecasts
                WHERE underlying=? AND as_of=? AND model_version=? ORDER BY id DESC LIMIT 1''',
                (item['underlying'], index['day'], index_engine.MODEL_VERSION)).fetchone()
            if saved:
                forecast = json.loads(saved['forecast_json'])
                forecast['stale'] = saved['input_revision'] != data_revision(c, index['day'])
                outcome = c.execute('''SELECT actual_day,outcome,close_change_pct,range_pct
                    FROM forecast_outcomes WHERE forecast_id=? AND actual_day<=? ORDER BY id DESC LIMIT 1''',
                    (saved['id'], selected_as_of)).fetchone()
                forecast['outcome'] = dict(outcome) if outcome else None
                forecast['forecast_id'] = saved['id']
                index['forecast'] = forecast
        counts = {
            'index_bars': c.execute('SELECT COUNT(*) FROM index_bars WHERE day<=?', (selected_as_of,)).fetchone()[0],
            'option_bars': c.execute('SELECT COUNT(*) FROM option_bars WHERE day<=?', (selected_as_of,)).fetchone()[0],
            'futures_bars': c.execute('SELECT COUNT(*) FROM futures_bars WHERE day<=?', (selected_as_of,)).fetchone()[0],
        }
        coverage = {}
        for key, table in (('indexes', 'index_bars'), ('options', 'option_bars'), ('futures', 'futures_bars')):
            row = c.execute(f'SELECT COUNT(*) AS rows,MIN(day) AS first,MAX(day) AS last FROM {table}').fetchone()
            coverage[key] = dict(row)
        files = [dict(r) for r in c.execute('SELECT dataset,filename,imported_at,row_count,first_day,last_day FROM source_files ORDER BY id DESC LIMIT 100')]
        available_days = [row['day'] for row in indexes if row['day']]
        analysis_as_of = max(available_days, default=None)
        revision = data_revision(c, analysis_as_of or selected_as_of)
        analysis_row = c.execute('SELECT run_at,as_of,input_revision,results_json FROM analysis_runs WHERE as_of=? ORDER BY id DESC LIMIT 1',
                                 (analysis_as_of,)).fetchone() if analysis_as_of else None
        analysis = None
        if analysis_row:
            analysis = {'run_at': analysis_row['run_at'] if analysis_row else None, 'as_of': analysis_as_of,
                        'input_revision': analysis_row['input_revision'], 'saved': True,
                        'stale': analysis_row['input_revision'] != revision,
                        'results': json.loads(analysis_row['results_json'])}
        evaluations = {}
        for item in UNIVERSE:
            saved_evaluation = c.execute('''SELECT run_at,input_revision,evaluation_json FROM model_evaluations
                WHERE underlying=? AND model_version=? AND as_of<=? ORDER BY id DESC LIMIT 1''',
                (item['underlying'], index_engine.MODEL_VERSION, selected_as_of)).fetchone()
            if saved_evaluation:
                evaluation = json.loads(saved_evaluation['evaluation_json'])
                evaluation['run_at'] = saved_evaluation['run_at']
                evaluation['stale'] = saved_evaluation['input_revision'] != data_revision(c, evaluation['as_of'])
                evaluations[item['underlying']] = evaluation
        database_path = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        with JOB_LOCK:
            job = {**JOB, 'log': list(JOB.get('log') or [])}
        return {'universe': UNIVERSE, 'indexes': indexes, 'counts': counts, 'coverage': coverage, 'files': files,
                'database': {'name': DB_PATH.name, 'path': 'Temporary hosted copy' if HOSTED else str(DB_PATH),
                             'size_mb': round(database_path / 1048576, 1)},
                'job': job, 'analysis': analysis, 'evaluations': evaluations,
                'input_revision': revision, 'today': today,
                'as_of': selected_as_of, 'first_date': date_limits['first'], 'latest_date': latest_date,
                'port': ACTIVE_PORT, 'mode': 'HOSTED WORKING COPY' if HOSTED else 'LOCAL MASTER',
                'cloud': cloud_runtime.status(sys.modules[__name__]),
                'model_status': f'{index_engine.MODEL_VERSION} · rule-based · not probability-calibrated'}


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=200):
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', status)

    def authorized(self):
        return not HOSTED or ACCESS.valid(self.headers.get('Authorization', ''))

    def send_file(self, path, filename):
        if not path.exists():
            return self.send_json({'error': 'Prepare the market file before downloading it.'}, 404)
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        with path.open('rb') as source:
            import shutil
            shutil.copyfileobj(source, self.wfile, 256 * 1024)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/health':
            return self.send_json({'ok': True})
        if path == '/api/config':
            return self.send_json({'hosted': HOSTED})
        if path.startswith('/api/') and not self.authorized():
            return self.send_json({'error': 'Sign in to the index research dashboard.'}, 401)
        if path == '/api/state':
            try:
                query = parse_qs(parsed.query)
                return self.send_json(get_state((query.get('as_of') or [None])[0]))
            except Exception as exc:
                return self.send_json({'error': str(exc)}, 500)
        if path == '/api/cloud/file':
            return self.send_file(DATA / cloud_runtime.ARCHIVE_NAME, 'Index Options Market.zip')
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in files:
            return self.send_json({'error': 'Not found'}, 404)
        filename, content_type = files[path]
        try:
            return self.send_bytes((STATIC / filename).read_bytes(), content_type)
        except OSError:
            return self.send_json({'error': 'Dashboard files are missing.'}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/login':
            if not HOSTED:
                return self.send_json({'error': 'Hosted sign-in is not enabled locally.'}, 404)
            if not ACCESS.origin_allowed(self.headers.get('Origin', '')):
                return self.send_json({'error': 'Origin not allowed.'}, 403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Invalid sign-in request.')
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(body, dict):
                    raise ValueError('Invalid sign-in request.')
                return self.send_json({'token': ACCESS.login(body.get('password', ''))})
            except (ValueError, TypeError, json.JSONDecodeError):
                return self.send_json({'error': 'Sign-in failed. Check the password or wait one minute after repeated attempts.'}, 401)
        if path not in ('/api/import', '/api/download', '/api/analyze', '/api/evaluate',
                        '/api/cloud/export', '/api/cloud/refresh'):
            return self.send_json({'error': 'Not found'}, 404)
        if not self.authorized():
            return self.send_json({'error': 'Sign in to the index research dashboard.'}, 401)
        if HOSTED and not ACCESS.origin_allowed(self.headers.get('Origin', '')):
            return self.send_json({'error': 'Origin not allowed.'}, 403)
        if HOSTED and path == '/api/import':
            return self.send_json({'error': 'Manual report-file imports are available on the laptop. Use the hosted report downloader to update this copy.'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            limit = MAX_UPLOAD if path == '/api/import' else 4096
            if not 0 < length <= limit:
                raise ValueError('Choose a report file under 60 MB.' if path == '/api/import' else 'Invalid request.')
            body = self.rfile.read(length)
            if path == '/api/cloud/export':
                start_job(lambda: cloud_runtime.make_snapshot(sys.modules[__name__]),
                          message='Preparing the index-only Google Drive market file…', kind='cloud_export')
                return self.send_json({'ok': True, 'accepted': True}, 202)
            if path == '/api/cloud/refresh':
                if not HOSTED:
                    raise ValueError('Load the Drive market file from the hosted dashboard, not from the laptop master.')
                payload = json.loads(body.decode('utf-8')) if body else {}
                if not isinstance(payload, dict):
                    raise ValueError('Invalid Drive refresh request.')
                start_job(lambda: cloud_runtime.refresh_drive(sys.modules[__name__], payload.get('discard_temporary') is True),
                          message='Loading the saved Google Drive market file…', kind='cloud_refresh')
                return self.send_json({'ok': True, 'accepted': True}, 202)
            if path == '/api/analyze':
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid analysis request.')
                requested_as_of = payload.get('as_of')
                if requested_as_of:
                    requested_as_of = parse_date(requested_as_of, 'analysis cutoff')
                    if requested_as_of > dt.date.today().isoformat():
                        raise ValueError('Choose a research date on or before today.')
                analysis_through = requested_as_of or 'latest available session'
                start_job(lambda: run_analysis(requested_as_of),
                          message=f'Calculating analysis and two-month history through {analysis_through}…', kind='analysis')
                return self.send_json({'ok': True, 'accepted': True}, 202)
            if path == '/api/evaluate':
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid history evaluation request.')
                underlying = str(payload.get('underlying', '')).upper()
                if underlying not in BY_SYMBOL:
                    raise ValueError('Choose one of the seven supported indices.')
                requested_as_of = payload.get('as_of')
                if requested_as_of:
                    requested_as_of = parse_date(requested_as_of, 'evaluation cutoff')
                    if requested_as_of > dt.date.today().isoformat():
                        raise ValueError('Choose a research date on or before today.')
                start_job(lambda: run_evaluation(underlying, requested_as_of),
                          message=f'Evaluating frozen rules for {BY_SYMBOL[underlying]["name"]}…', kind='evaluation')
                return self.send_json({'ok': True, 'accepted': True}, 202)
            if path == '/api/download':
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('Invalid download request.')
                start_job(lambda: download_reports(payload.get('start', ''), payload.get('end', ''), payload.get('segments', [])),
                          message='Downloading selected NSE and BSE reports…', kind='download')
                return self.send_json({'ok': True, 'accepted': True}, 202)
            if not TASK_LOCK.acquire(False):
                raise ValueError('Wait for the current data task to finish.')
            try:
                dataset = self.headers.get('X-Import-Mode', '')
                filename = unquote(self.headers.get('X-File-Name', 'import.csv'))
                result = import_csv(dataset, filename, body, self.headers.get('X-Selected-Index'),
                                    self.headers.get('X-From-Date'), self.headers.get('X-To-Date'))
            finally:
                TASK_LOCK.release()
            return self.send_json({'ok': True, **result})
        except (ValueError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
            return self.send_json({'error': str(exc)}, 400)

    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ('200',):
            super().log_message(fmt, *args)


def main():
    global ACTIVE_PORT
    init_db()
    # Mark the automatic Drive restore as running before the server accepts page
    # requests. Otherwise the first hosted /api/state request can see an empty
    # database and a non-running job, leaving the browser with nothing to poll.
    if HOSTED and cloud_runtime.drive_file_id():
        with connect() as c:
            empty = c.execute('SELECT COUNT(*) FROM index_bars').fetchone()[0] == 0
        if empty:
            start_job(lambda: cloud_runtime.refresh_drive(sys.modules[__name__]),
                      message='Loading the saved market data from Google Drive…', kind='cloud_refresh')
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    ACTIVE_PORT = server.server_address[1]
    url = f'http://127.0.0.1:{ACTIVE_PORT}'
    mode_message = ('Hosted working copy. The saved Drive snapshot is the durable market-data source.'
                    if HOSTED else 'Local master. Data is stored in this project folder. Close this window to stop.')
    print(f'Index Options Research: {url}\n{mode_message}', flush=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + '/health', timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f'Dashboard health check returned HTTP {response.status}.')
        print('Dashboard health check: passed.', flush=True)
        if not HOSTED and '--no-browser' not in sys.argv:
            webbrowser.open(url)
        server_thread.join()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f'Local dashboard check or startup failed: {exc}', file=sys.stderr, flush=True)
        raise
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
