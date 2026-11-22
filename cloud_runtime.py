"""Market-only Drive snapshots for the separate index options project."""
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path


FORMAT = 'nse-index-options-market-v1'
ARCHIVE_NAME = 'index-market.zip'
DATABASE_NAME = 'index_market.sqlite3'
MAX_DRIVE_BYTES = 350_000_000
MAX_DATABASE_BYTES = 1_500_000_000


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _get(c, key, default=''):
    row = c.execute('SELECT value FROM cloud_state WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def _set(c, key, value):
    c.execute('INSERT OR REPLACE INTO cloud_state(key,value) VALUES(?,?)', (key, str(value)))


def _current_analysis(c, app, through):
    if not through:
        return None
    row = c.execute('''SELECT run_at,input_revision FROM analysis_runs
        WHERE as_of=? ORDER BY id DESC LIMIT 1''', (through,)).fetchone()
    if not row or row['input_revision'] != app.data_revision(c, through):
        return None
    return dict(run_at=row['run_at'], input_revision=row['input_revision'])


def _manifest_counts(c):
    return {table: c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            for table in ('index_bars', 'option_bars', 'futures_bars', 'analysis_runs',
                          'index_forecasts', 'forecast_outcomes', 'model_evaluations')}


def make_snapshot(app):
    """Create an atomic ZIP of the full index-only local database."""
    app.DATA.mkdir(parents=True, exist_ok=True)
    destination = app.DATA / ARCHIVE_NAME
    with tempfile.TemporaryDirectory(dir=app.DATA) as work:
        database = Path(work) / DATABASE_NAME
        source = app.connect()
        target = sqlite3.connect(database)
        target.row_factory = sqlite3.Row
        try:
            source.backup(target)
            latest = target.execute('SELECT MAX(day) FROM index_bars').fetchone()[0]
            if not latest:
                raise ValueError('Import index OHLC history before preparing the Drive market file.')
            present = {row[0] for row in target.execute('SELECT DISTINCT underlying FROM index_bars')}
            missing = sorted(set(app.BY_SYMBOL) - present)
            if missing:
                raise ValueError(f'Download BSE index OHLC history from Data centre before preparing the seven-index Drive file. Missing: {", ".join(missing)}.')
            analysis = _current_analysis(target, app, latest)
            if not analysis:
                raise ValueError('Run analysis through the latest stored index date before preparing the Drive market file.')
            revision = app.data_revision(target)
            now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            _set(target, 'cloud_source_through', latest)
            _set(target, 'cloud_source_revision', revision)
            _set(target, 'cloud_source_created_at', now)
            target.commit()
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('The market database did not pass its integrity check.')
            counts = _manifest_counts(target)
            first = target.execute('SELECT MIN(day) FROM index_bars').fetchone()[0]
            target.execute('PRAGMA journal_mode=DELETE')
            target.commit()
        finally:
            target.close()
            source.close()

        database_bytes = database.stat().st_size
        if database_bytes > MAX_DATABASE_BYTES:
            raise ValueError('The index database is too large for a Drive snapshot.')
        manifest = {
            'format': FORMAT,
            'created_at': now,
            'through': latest,
            'from': first,
            'underlyings': sorted(app.BY_SYMBOL),
            'input_revision': revision,
            'analysis_run_at': analysis['run_at'],
            'counts': counts,
            'database_bytes': database_bytes,
            'database_sha256': _digest(database),
            'market_only': True,
            'symbols_or_personal_data_included': False,
        }
        partial = destination.with_suffix('.zip.part')
        try:
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(database, DATABASE_NAME)
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            if partial.stat().st_size > MAX_DRIVE_BYTES:
                raise ValueError('The prepared ZIP exceeds the Google Drive download limit for this dashboard.')
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)

    with app.connect() as c:
        _set(c, 'last_export_through', latest)
        _set(c, 'last_export_revision', revision)
        _set(c, 'last_export_created_at', now)
    app.job_log(f'Index-only Drive file prepared · through {latest} · {destination.stat().st_size / 1048576:.1f} MB. No stock symbols or personal data are included.')
    return manifest


def status(app):
    with app.connect() as c:
        revision = app.data_revision(c)
        through = c.execute('SELECT MAX(day) FROM index_bars').fetchone()[0]
        present_underlyings = {row[0] for row in c.execute('SELECT DISTINCT underlying FROM index_bars')}
        missing_underlyings = sorted(set(app.BY_SYMBOL) - present_underlyings)
        source_through = _get(c, 'cloud_source_through') or None
        source_revision = _get(c, 'cloud_source_revision')
        last_export_through = _get(c, 'last_export_through') or None
        last_export_revision = _get(c, 'last_export_revision')
        last_export_created_at = _get(c, 'last_export_created_at') or None
        current_analysis = _current_analysis(c, app, through)
        analysis_ready = bool(current_analysis)
        analysis_run_at = current_analysis['run_at'] if current_analysis else None

    export_path = app.DATA / ARCHIVE_NAME
    export_info = None
    if export_path.exists():
        try:
            with zipfile.ZipFile(export_path) as archive:
                export_info = json.loads(archive.read('manifest.json'))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            export_info = None
    export_ready = bool(not app.HOSTED and analysis_ready and not missing_underlyings and export_info
                        and export_info.get('format') == FORMAT
                        and export_info.get('input_revision') == revision
                        and export_info.get('analysis_run_at') == analysis_run_at)
    baseline = source_revision if app.HOSTED else (last_export_revision or source_revision)
    return {
        'hosted': app.HOSTED,
        'drive_configured': bool(os.environ.get('INDEX_DRIVE_FILE_ID')),
        'saved_through': source_through,
        'last_export_through': last_export_through,
        'last_export_created_at': last_export_created_at,
        'unsaved_market_changes': bool(baseline and baseline != revision),
        'analysis_ready': analysis_ready,
        'missing_underlyings': missing_underlyings,
        'export_ready': export_ready,
        'export_mb': round(export_path.stat().st_size / 1048576, 1) if export_ready else None,
        'export_reason': (f'Download BSE index OHLC history from Data centre before preparing the seven-index file. Missing: {", ".join(missing_underlyings)}.'
                          if missing_underlyings else
                          'Run current analysis after updating market data.' if not analysis_ready else ''),
    }


def _validate_database(app, database, manifest):
    c = sqlite3.connect(database)
    try:
        c.row_factory = sqlite3.Row
        if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('The Drive market database failed its integrity check; the current data was preserved.')
        required = {'index_bars', 'option_bars', 'futures_bars', 'source_files', 'analysis_runs',
                    'index_forecasts', 'forecast_outcomes', 'model_evaluations', 'cloud_state'}
        available = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(available):
            raise ValueError('The Drive file is missing required index research tables; the current data was preserved.')
        allowed = set(app.BY_SYMBOL)
        for table in ('index_bars', 'option_bars', 'futures_bars', 'index_forecasts', 'model_evaluations'):
            symbols = {row[0] for row in c.execute(f'SELECT DISTINCT underlying FROM {table}')}
            if not symbols.issubset(allowed):
                raise ValueError('The Drive file contains an underlying outside the fixed seven-index universe; current data was preserved.')
        latest = c.execute('SELECT MAX(day) FROM index_bars').fetchone()[0]
        if not latest or latest != manifest.get('through'):
            raise ValueError('The Drive file date does not match its manifest; the current data was preserved.')
        present = {row[0] for row in c.execute('SELECT DISTINCT underlying FROM index_bars')}
        if present != allowed:
            raise ValueError('The Drive file does not contain OHLC history for all seven supported indices; the current data was preserved.')
        if app.data_revision(c) != manifest.get('input_revision'):
            raise ValueError('The Drive file data revision does not match its manifest; the current data was preserved.')
        analysis = _current_analysis(c, app, latest)
        if not analysis or analysis['run_at'] != manifest.get('analysis_run_at'):
            raise ValueError('The Drive file has no current saved analysis; the current data was preserved.')
        if _manifest_counts(c) != manifest.get('counts'):
            raise ValueError('The Drive file row counts do not match its manifest; the current data was preserved.')
        return latest
    finally:
        c.close()


def install_snapshot(app, archive_path):
    """Validate the complete archive before atomically replacing the hosted copy."""
    app.DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=app.DATA) as work:
        staged = Path(work) / DATABASE_NAME
        with zipfile.ZipFile(archive_path) as archive:
            if set(archive.namelist()) != {DATABASE_NAME, 'manifest.json'} or len(archive.namelist()) != 2:
                raise ValueError('Choose the market ZIP prepared by the index research dashboard.')
            if archive.getinfo('manifest.json').file_size > 128_000:
                raise ValueError('The Drive manifest exceeds the allowed size.')
            if archive.getinfo(DATABASE_NAME).file_size > MAX_DATABASE_BYTES:
                raise ValueError('The Drive market database exceeds the allowed size.')
            manifest = json.loads(archive.read('manifest.json'))
            if (manifest.get('format') != FORMAT or manifest.get('market_only') is not True
                    or manifest.get('symbols_or_personal_data_included') is not False
                    or manifest.get('underlyings') != sorted(app.BY_SYMBOL)):
                raise ValueError('This is not a compatible seven-index market snapshot.')
            with archive.open(DATABASE_NAME) as source, staged.open('wb') as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
        if _digest(staged) != manifest.get('database_sha256'):
            raise ValueError('The Drive ZIP checksum failed; the current database was preserved.')
        if staged.stat().st_size != manifest.get('database_bytes'):
            raise ValueError('The Drive database size does not match its manifest; current data was preserved.')
        latest = _validate_database(app, staged, manifest)
        staged_connection = sqlite3.connect(staged)
        try:
            c = staged_connection
            c.execute('INSERT OR REPLACE INTO cloud_state(key,value) VALUES(?,?)',
                      ('cloud_source_through', latest))
            c.execute('INSERT OR REPLACE INTO cloud_state(key,value) VALUES(?,?)',
                      ('cloud_source_revision', manifest['input_revision']))
            c.execute('INSERT OR REPLACE INTO cloud_state(key,value) VALUES(?,?)',
                      ('cloud_source_created_at', manifest['created_at']))
            c.commit()
        finally:
            staged_connection.close()
        staged_target = app.DATA / (app.DB_PATH.name + '.incoming')
        shutil.copyfile(staged, staged_target)
        try:
            old = app.connect()
            try:
                old.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                old.close()
            for suffix in ('-wal', '-shm'):
                Path(str(app.DB_PATH) + suffix).unlink(missing_ok=True)
            os.replace(staged_target, app.DB_PATH)
        finally:
            staged_target.unlink(missing_ok=True)
    app.init_db()
    app.job_log(f'Saved Google Drive index snapshot loaded · through {latest}.')
    return manifest


def refresh_drive(app, discard=False):
    current = status(app)
    if current['unsaved_market_changes'] and not discard:
        raise ValueError('Temporary market data is present. Confirm replacement from the saved Drive file before continuing.')
    file_id = os.environ.get('INDEX_DRIVE_FILE_ID', '')
    if not file_id or not all(ch.isalnum() or ch in '_-' for ch in file_id) or len(file_id) < 10:
        raise ValueError('Set INDEX_DRIVE_FILE_ID in the Render service settings to the separate index market ZIP file.')
    import gdown
    app.job_log('Fetching the configured index market ZIP from Google Drive…')
    with tempfile.TemporaryDirectory(dir=app.DATA) as work:
        path = Path(work) / 'download.zip'
        result = gdown.download(id=file_id, output=str(path), quiet=True, use_cookies=False)
        if not result or not path.exists() or not path.stat().st_size or path.stat().st_size > MAX_DRIVE_BYTES:
            raise ValueError('Google Drive download failed or exceeded the configured file limit. Check that the separate market ZIP is accessible and retry.')
        return install_snapshot(app, path)
