const $ = (id) => document.getElementById(id);
let state = null;
let selected = 'NIFTY';
let jobPoller = null;
let jobPollInFlight = false;
let researchDateReady = false;
let hosted = false;
let authToken = '';
const tableSortState = new WeakMap();
const authStorageKey = 'index-options-research-auth';
let activeTouchTooltip = null;
let historyTooltipLayer = null;

function isTouchPointer(event) {
  return event.pointerType === 'touch'
    || (!event.pointerType && ((navigator.maxTouchPoints || 0) > 0 || 'ontouchstart' in window));
}

function dismissTouchTooltip(target) {
  const visibleLayer = activeTouchTooltip
    || (historyTooltipLayer && !historyTooltipLayer.tooltip.hidden ? historyTooltipLayer : null);
  if (visibleLayer && !visibleLayer.host.contains(target)) {
    visibleLayer.tooltip.hidden = true;
    if (activeTouchTooltip === visibleLayer) activeTouchTooltip = null;
  }
}

// Capture outside taps before Safari's synthesized click sequence reaches page controls.
document.addEventListener('pointerdown', (event) => dismissTouchTooltip(event.target), {capture: true, passive: true});
document.addEventListener('touchstart', (event) => dismissTouchTooltip(event.target), {capture: true, passive: true});
document.addEventListener('click', (event) => dismissTouchTooltip(event.target), true);

function storedToken() {
  try { return sessionStorage.getItem(authStorageKey) || ''; } catch { return ''; }
}
function storeToken(value) {
  try { if (value) sessionStorage.setItem(authStorageKey, value); else sessionStorage.removeItem(authStorageKey); } catch {}
}
function showSignIn() {
  document.body.classList.add('needs-signin');
  $('cloud-login').hidden = false;
  $('cloud-password').focus();
}
function showHostedDriveAction() {
  const button = $('cloud-refresh-button');
  button.hidden = false;
  button.disabled = false;
  $('cloud-state-badge').className = 'state-badge up';
  $('cloud-state-badge').textContent = 'DRIVE FILE READY';
  $('cloud-status').textContent = 'Click Load saved Google Drive copy to fetch the saved index market file.';
  $('cloud-action-help').textContent = 'The project is connected to your separate index market ZIP. Replace that file’s contents in Drive to keep its link unchanged.';
}
async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (hosted && authToken) headers.set('Authorization', `Bearer ${authToken}`);
  const response = await fetch(path, {...options, headers, cache: 'no-store'});
  if (hosted && response.status === 401) {
    authToken = '';
    storeToken('');
    showSignIn();
  }
  return response;
}
async function boot() {
  try {
    const response = await fetch('/api/config', {cache: 'no-store'});
    if (!response.ok) throw new Error('Could not read dashboard setup.');
    hosted = !!(await response.json()).hosted;
    $('workspace-mode').textContent = hosted ? 'HOSTED COPY' : 'LOCAL MASTER';
    $('cloud-signout').hidden = !hosted;
    authToken = storedToken();
    if (hosted && !authToken) { showSignIn(); return; }
    if (hosted) showHostedDriveAction();
    await refresh();
  } catch (error) {
    $('research-date-status').textContent = `${error.message} The service may be starting; click Refresh to retry.`;
    $('cloud-state-badge').className = 'state-badge down';
    $('cloud-state-badge').textContent = 'Dashboard unavailable';
    $('cloud-status').textContent = `${error.message} The service may be starting; click Refresh to retry.`;
  }
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('en-IN', {minimumFractionDigits: digits, maximumFractionDigits: digits});
}
function compact(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString('en-IN', {maximumFractionDigits: 0});
}
function signed(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value) > 0 ? '+' : ''}${num(value)}%`;
}
function signedOne(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value) > 0 ? '+' : ''}${num(value, 1)}%`;
}
function trendClass(value) {
  if (value === 'Upward structure') return 'up';
  if (value === 'Downward structure') return 'down';
  return 'neutral';
}
async function refresh(asOf = null) {
  $('refresh').disabled = true;
  try {
    const requestedDate = asOf || (researchDateReady ? $('research-date').value : '');
    const endpoint = requestedDate ? `/api/state?as_of=${encodeURIComponent(requestedDate)}` : '/api/state';
    const response = await apiFetch(endpoint);
    state = await response.json();
    if (!response.ok) throw new Error(state.error || 'Could not load research data.');
    if (!researchDateReady) {
      $('research-date').value = state.as_of || state.latest_date || state.today;
      $('research-date').min = state.first_date || '';
      $('research-date').max = state.today;
      researchDateReady = true;
    }
    render();
    return true;
  } catch (error) {
    $('index-tiles').innerHTML = `<div class="empty error">${esc(error.message)}</div>`;
    $('research-date-status').textContent = error.message;
    return false;
  } finally {
    $('refresh').disabled = false;
  }
}

function render() {
  $('research-date').min = state.first_date || '';
  $('research-date').max = state.latest_date || state.today;
  $('data-stats').innerHTML = `<span>${compact(state.counts.index_bars)} index rows</span><span>${compact(state.counts.option_bars)} option rows</span><span>${compact(state.counts.futures_bars)} futures rows</span>`;
  renderIndexTiles();
  const chosen = state.indexes.find((x) => x.underlying === selected) || state.indexes[0];
  renderDetail(chosen);
  renderForecast(chosen);
  renderEvaluation(state.evaluations?.[chosen.underlying], state.job, chosen);
  renderAnalysis(state.analysis, state.job, chosen);
  renderCoverage(state.coverage, state.database);
  renderFiles(state.files);
  renderCloud(state.cloud || {}, state.job || {});
  $('research-date-status').textContent = startupDriveRestorePending()
    ? 'Restoring the saved index data from Google Drive…'
    : state.counts.index_bars
      ? `Showing stored market rows on or before ${state.as_of}; later rows are excluded.`
      : `No index history is stored on or before ${state.as_of}. Import reports from Data centre.`;
  renderJob(state.job);
  enableSortableTables();
}

function renderIndexTiles() {
  $('index-tiles').innerHTML = state.indexes.map((item) => {
    const active = item.underlying === selected;
    const move = item.return_1;
    return `<button type="button" class="index-tile${active ? ' selected-tile' : ''}" data-index="${esc(item.underlying)}" aria-pressed="${active}">
      <span>${esc(item.name)}</span><strong class="${move > 0 ? 'positive' : move < 0 ? 'negative' : ''}">${item.close === null ? '—' : `₹${num(item.close)}`}</strong>
      <small>${esc(item.underlying)} · ${esc(item.exchange)}</small><small>${esc(item.day || 'No data through selected date')}</small>
      <small class="tile-change">1D ${signed(move)}</small><small class="tile-forecast">${item.forecast ? `Next: ${esc(item.forecast.direction)}` : 'Next: select a date'}</small></button>`;
  }).join('');
  $('index-tiles').querySelectorAll('[data-index]').forEach((node) => node.addEventListener('click', () => {
    selected = node.dataset.index;
    render();
    $('forecast-title').scrollIntoView({behavior: 'smooth', block: 'start'});
  }));
}

function renderForecast(item) {
  const forecast = item?.forecast;
  const badge = $('forecast-badge');
  if (!forecast) {
    $('forecast-title').textContent = `${item?.name || 'Selected index'} · next-session view`;
    $('forecast-title').className = '';
    $('forecast-meta').textContent = item?.day
      ? `No saved forecast for ${item.day}. It will calculate when you change the selected date.`
      : 'No index price history is available through the selected date.';
    badge.textContent = 'No forecast saved';
    badge.className = 'state-badge';
    $('forecast-scores').innerHTML = '<div class="empty">No saved evidence scores for this date.</div>';
    $('forecast-reasons').innerHTML = '<li>Import index OHLC and derivatives history, then select a date.</li>';
    $('forecast-warnings').innerHTML = '<li>Forecasts are dated and saved separately from the stock dashboard.</li>';
    return;
  }
  $('forecast-title').textContent = `${item.name} · ${forecast.direction}`;
  badge.textContent = forecast.confidence;
  badge.className = 'state-badge';
  $('forecast-title').className = forecast.direction === 'RISE' ? 'positive' : forecast.direction === 'FALL' ? 'negative' : '';
  const outcome = forecast.outcome
    ? ` · next session ${forecast.outcome.outcome} on ${forecast.outcome.actual_day} (${signed(forecast.outcome.close_change_pct)})`
    : ' · next-session outcome pending';
  $('forecast-meta').textContent = `Signal close ${forecast.as_of} · ${forecast.model_version} · net rule score ${forecast.net_score > 0 ? '+' : ''}${num(forecast.net_score)}${forecast.stale ? ' · saved inputs changed; use the top Run analysis button to refresh' : ''}${outcome}`;
  const labels = {index_structure: 'Index structure', futures_positioning: 'Futures positioning', options_positioning: 'Options positioning', cross_index_context: 'Cross-index context'};
  $('forecast-scores').innerHTML = Object.entries(forecast.scores || {}).map(([key, value]) => `<div class="forecast-score"><span>${esc(labels[key] || key)}</span><strong class="${value > 0 ? 'positive' : value < 0 ? 'negative' : ''}">${value > 0 ? '+' : ''}${num(value)}</strong><small>bounded score points</small></div>`).join('') || '<div class="empty">No component scores.</div>';
  $('forecast-reasons').innerHTML = (forecast.reasons || []).length
    ? forecast.reasons.map((reason) => `<li>${esc(reason)}</li>`).join('') : '<li>No directional evidence passed the reporting checks.</li>';
  const warnings = [...(forecast.warnings || [])];
  if (forecast.stale) warnings.unshift('Inputs changed after this saved forecast. Use the top Run analysis button to create the current version.');
  $('forecast-warnings').innerHTML = warnings.length
    ? warnings.map((warning) => `<li>${esc(warning)}</li>`).join('') : '<li>No additional data coverage warnings.</li>';
}

function renderEvaluation(evaluation, job = {}, item = null) {
  const status = $('evaluation-status');
  const button = $('evaluate-model');
  const running = job.kind === 'evaluation' && job.running;
  button.disabled = !!job.running;
  if (!evaluation) {
    status.textContent = running ? `Replaying ${item?.name || 'selected index'} sessions through ${state.as_of}…`
      : `${item?.name || 'Selected index'} · no saved historical check. Evaluate after importing more daily sessions.`;
    $('evaluation-results').innerHTML = '<div class="empty">Chronological results will appear here. Fewer than 30 directional holdout observations are marked too small to interpret.</div>';
    return;
  }
  const stale = evaluation.stale ? ' · stored inputs have changed since this check; rerun evaluation' : '';
  status.textContent = `${evaluation.name} · evaluated through ${evaluation.as_of} · ${evaluation.replay_observations} signal/outcome pairs · ${evaluation.model_version}${stale}${running ? ' · another evaluation is running' : ''}`;
  const splits = evaluation.splits || [];
  $('evaluation-results').innerHTML = `<div class="table-wrap"><table data-sortable><thead><tr><th>Chronological period</th><th>Observations</th><th>Calls made</th><th>Coverage</th><th>3-state accuracy</th><th>Directional calls</th><th>Directional hit rate</th><th>Majority baseline</th></tr></thead><tbody>${splits.map((split) => `<tr><td>${esc(split.period)}${split.too_small_to_interpret ? '<small class="small-sample">Too few directional cases</small>' : ''}</td><td>${compact(split.observations)}</td><td>${compact(split.actionable)}</td><td>${split.coverage_pct == null ? '—' : `${num(split.coverage_pct, 1)}%`}</td><td>${split.three_state_accuracy_pct == null ? '—' : `${num(split.three_state_accuracy_pct, 1)}%`}</td><td>${compact(split.directional_calls)}</td><td>${split.directional_hit_rate_pct == null ? '—' : `${num(split.directional_hit_rate_pct, 1)}%`}</td><td>${split.majority_class_baseline_pct == null ? '—' : `${num(split.majority_class_baseline_pct, 1)}%`}</td></tr>`).join('')}</tbody></table></div><p class="table-foot">${esc(evaluation.replay_note)} ${esc(evaluation.label_definition)}</p>`;
  enableSortableTables();
}

function renderDetail(item) {
  if (!item) return;
  $('detail-title').textContent = item.name;
  $('detail-subtitle').textContent = `${item.exchange} · ${item.underlying}`;
  $('trend-badge').textContent = item.trend;
  $('trend-badge').className = `state-badge ${trendClass(item.trend)}`;
  $('metrics').innerHTML = [
    ['AS OF', item.day || '—'], ['LATEST CLOSE', num(item.close)], ['1-SESSION MOVE', signed(item.return_1)],
    ['5-SESSION MOVE', signed(item.return_5)],
    ['20-SESSION MEAN', num(item.sma20)], ['ATR 14', num(item.atr14)],
    ['OPTIONS AS OF', item.option_day || '—'], ['NEAREST OPTION EXPIRY', item.options?.expiry || '—'],
    ['FUTURES CONTRACT', item.futures?.expiry || '—'], ['FUTURES CLOSE', num(item.futures?.close)],
    ['FUTURES OPEN INTEREST', compact(item.futures?.oi)], ['FUTURES ΔOI', signedNumber(item.futures?.oi_change)],
    ['FUTURES BASIS TO SPOT', signed(item.futures?.basis_pct)]
  ].map(([label, value]) => `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
  renderChart(item.bars || []);
  $('chain-date').textContent = item.option_day
    ? `Chain date ${item.option_day}${item.option_day === item.day ? '' : ' · earlier than index close'}${item.option_previous_day ? ` · comparison ${item.option_previous_day}` : ''}`
    : 'No chain loaded';
  $('expiry-rows').innerHTML = (item.expiries || []).length ? item.expiries.map((x) => `<tr>
    <td>${esc(x.expiry)}</td><td>${x.days_to_expiry}</td><td>${compact(x.contracts)}</td>
    <td>${compact(x.call_oi)}</td><td>${compact(x.put_oi)}</td><td>${num(x.put_call_oi)}${x.pcr_oi_1d_pct == null ? '' : ` (${signedOne(x.pcr_oi_1d_pct)})`}</td>
    <td class="${x.call_oi_change_pct > 0 ? 'positive' : x.call_oi_change_pct < 0 ? 'negative' : ''}">${signedNumber(x.call_oi_change)}${x.call_oi_change_pct == null ? '' : ` (${signedOne(x.call_oi_change_pct)})`}</td>
    <td class="${x.put_oi_change_pct > 0 ? 'positive' : x.put_oi_change_pct < 0 ? 'negative' : ''}">${signedNumber(x.put_oi_change)}${x.put_oi_change_pct == null ? '' : ` (${signedOne(x.put_oi_change_pct)})`}</td></tr>`).join('')
    : '<tr><td colspan="8" class="empty">No option rows yet.</td></tr>';
  const spot = item.option_spot ?? item.close;
  $('chain-rows').innerHTML = (item.chain || []).length ? item.chain.map((x) => {
    const distance = spot ? (x.strike / spot - 1) * 100 : null;
    const atm = distance !== null && Math.abs(distance) <= 3;
    return `<tr class="${atm ? 'atm-row' : ''}"><td class="strike-cell">${num(x.strike, 0)}</td><td>${esc(x.expiry)}</td><td><span class="side ${x.option_type === 'CE' ? 'call' : 'put'}">${esc(x.option_type)}</span></td>
      <td>${signed(distance)}</td><td>${num(x.close)}</td><td>${compact(x.volume)}</td><td>${compact(x.oi)}</td><td>${signedNumber(x.oi_change)}</td></tr>`;
  }).join('') : '<tr><td colspan="8" class="empty">Import an option-chain file for this index and date.</td></tr>';
  renderOptionExpiryCharts(item);
}

function renderOptionExpiryCharts(item) {
  const holder = $('option-expiry-charts');
  const expiries = item.map_expiries || [];
  const slots = [
    {label: 'Current / nearest expiry', expiry: expiries[0]},
    {label: 'Upcoming expiry', expiry: expiries[1]},
  ];
  holder.innerHTML = slots.map((slot) => {
    const title = slot.expiry ? `${esc(slot.label)} · ${esc(slot.expiry)}` : slot.label;
    const rows = slot.expiry ? (item.chain || []).filter((row) => row.expiry === slot.expiry) : [];
    if (!slot.expiry || !rows.length) {
      return `<article class="expiry-chart-card"><h4>${title}</h4><div class="expiry-chart empty-chart">${slot.expiry ? 'No strikes available in the selected spot band.' : 'No second expiry is available in the stored chain.'}</div></article>`;
    }
    const byStrike = new Map();
    for (const row of rows) {
      const key = Number(row.strike);
      if (!byStrike.has(key)) byStrike.set(key, {CE: 0, PE: 0});
      byStrike.get(key)[row.option_type] += Number(row.oi || 0);
    }
    const strikes = [...byStrike.keys()].sort((a, b) => a - b);
    const maxOi = Math.max(1, ...[...byStrike.values()].flatMap((value) => [value.CE, value.PE]));
    const width = 660, height = 290, left = 52, right = 17, top = 22, bottom = 68;
    const plotWidth = width - left - right, plotHeight = height - top - bottom, baseY = top + plotHeight;
    const groupWidth = plotWidth / strikes.length, barWidth = Math.max(2, Math.min(12, groupWidth * 0.30));
    const xAt = (index) => left + (index + 0.5) * groupWidth;
    const grid = [0, 0.5, 1].map((fraction) => {
      const y = baseY - fraction * plotHeight;
      return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="oi-gridline"/><text x="${left - 7}" y="${y + 3}" text-anchor="end" class="oi-axis-label">${esc(compact(maxOi * fraction))}</text>`;
    }).join('');
    const bars = strikes.map((strike, index) => {
      const center = xAt(index), values = byStrike.get(strike);
      const callHeight = values.CE / maxOi * plotHeight, putHeight = values.PE / maxOi * plotHeight;
      const labelStep = Math.max(1, Math.ceil(strikes.length / 9));
      const label = index % labelStep === 0 || index === strikes.length - 1
        ? `<text x="${center}" y="${baseY + 21}" text-anchor="middle" class="oi-axis-label">${esc(num(strike, 0))}</text>` : '';
      return `<rect x="${center - barWidth - 1}" y="${baseY - callHeight}" width="${barWidth}" height="${Math.max(0.5, callHeight)}" rx="1" class="oi-call-bar"><title>${esc(num(strike, 0))} CE OI ${esc(compact(values.CE))}</title></rect><rect x="${center + 1}" y="${baseY - putHeight}" width="${barWidth}" height="${Math.max(0.5, putHeight)}" rx="1" class="oi-put-bar"><title>${esc(num(strike, 0))} PE OI ${esc(compact(values.PE))}</title></rect>${label}`;
    }).join('');
    const spot = Number(item.option_spot ?? item.close);
    const minStrike = strikes[0], maxStrike = strikes.at(-1);
    const spotX = maxStrike > minStrike && spot >= minStrike && spot <= maxStrike
      ? left + (spot - minStrike) / (maxStrike - minStrike) * plotWidth : null;
    const spotLine = spotX == null ? '' : `<line x1="${spotX}" y1="${top}" x2="${spotX}" y2="${baseY}" class="oi-spot-line"/><text x="${Math.min(spotX + 4, width - 82)}" y="${top + 10}" class="oi-spot-label">Spot ${esc(num(spot, 0))}</text>`;
    const dte = item.expiries?.find((expiry) => expiry.expiry === slot.expiry)?.days_to_expiry;
    return `<article class="expiry-chart-card"><div class="expiry-chart-title"><h4>${title}</h4><small>${dte == null ? '' : `${dte} days to expiry`}</small></div><svg class="expiry-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Call and put open interest by strike for ${esc(item.name)} expiry ${esc(slot.expiry)}">${grid}<line x1="${left}" y1="${baseY}" x2="${width - right}" y2="${baseY}" class="oi-axis"/>${bars}${spotLine}<g class="oi-legend"><rect x="${left}" y="${height - 20}" width="9" height="9" class="oi-call-bar"/><text x="${left + 14}" y="${height - 12}" class="oi-axis-label">CE OI</text><rect x="${left + 72}" y="${height - 20}" width="9" height="9" class="oi-put-bar"/><text x="${left + 86}" y="${height - 12}" class="oi-axis-label">PE OI</text><line x1="${left + 147}" y1="${height - 15}" x2="${left + 162}" y2="${height - 15}" class="oi-spot-line"/><text x="${left + 168}" y="${height - 12}" class="oi-axis-label">Spot</text></g></svg></article>`;
  }).join('');
}

function signedNumber(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value) > 0 ? '+' : ''}${compact(value)}`;
}

function renderChart(bars) {
  const holder = $('chart');
  if (!bars.length) {
    holder.className = 'chart empty-chart';
    holder.textContent = 'Import index bars to see price history.';
    $('chart-period').textContent = 'Recent stored sessions';
    return;
  }
  holder.className = 'chart';
  const width = 760, height = 190, pad = 12;
  const values = bars.map((x) => x.close);
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const points = values.map((v, i) => `${pad + i * (width - 2 * pad) / Math.max(values.length - 1, 1)},${height - pad - (v - min) * (height - 2 * pad) / span}`).join(' ');
  holder.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Recent index closes"><line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" class="axis"/><polyline points="${points}" class="price-line"/><text x="${pad}" y="16" class="chart-label">${num(max)}</text><text x="${pad}" y="${height - 20}" class="chart-label">${num(min)}</text></svg>`;
  $('chart-period').textContent = `${bars[0].day} to ${bars[bars.length - 1].day} · ${bars.length} sessions`;
}

function renderFiles(files) {
  const labels = {indexes: 'Index OHLC · manual', nse_indexes: 'NSE index OHLC', bse_indexes: 'BSE index OHLC', options: 'Index options · manual', futures: 'Index futures · manual', nse_fo: 'NSE index options/futures', bse_fo: 'BSE index options/futures'};
  $('file-list').innerHTML = files.length ? files.map((f) => `<tr><td>${esc(f.first_day || '—')}</td><td>${labels[f.dataset] || esc(f.dataset)}</td><td>${compact(f.row_count)}</td><td>${esc(f.filename)}</td><td>${esc(f.imported_at)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty">No files imported yet.</td></tr>';
}

function renderCoverage(coverage, database) {
  const labels = {indexes: 'INDEX OHLC', options: 'INDEX OPTIONS', futures: 'INDEX FUTURES'};
  $('data-summary').innerHTML = Object.entries(labels).map(([key, label]) => {
    const item = coverage[key] || {rows: 0, first: null, last: null};
    const period = item.first ? `${item.first} → ${item.last}` : 'No history yet';
    return `<div class="coverage-card"><span>${label}</span><strong>${compact(item.rows)} rows</strong><small>${period}</small></div>`;
  }).join('');
  $('database-summary').textContent = `${database.name} · ${num(database.size_mb, 1)} MB · ${database.path}`;
  const lastDayFor = (dataset) => [...new Set((state.files || []).filter((f) => f.dataset === dataset).map((f) => f.first_day))].sort().at(-1) || 'not yet';
  $('gaps').textContent = `Last NSE F&O report processed: ${lastDayFor('nse_fo')} · Last BSE F&O report processed: ${lastDayFor('bse_fo')}. The database stores only the seven selected indices; source filenames and date coverage are listed below.`;
}

function startupDriveRestorePending(cloud = state?.cloud, job = state?.job) {
  const restoreFailed = job?.kind === 'cloud_refresh' && job.error && !job.running;
  return hosted && !!cloud?.drive_configured && !state?.coverage?.indexes?.rows && !restoreFailed;
}

function renderCloud(cloud, job) {
  const running = !!job.running;
  const hostedCopy = !!cloud.hosted;
  const restorePending = hostedCopy && startupDriveRestorePending(cloud, job);
  $('workspace-mode').textContent = hostedCopy ? 'HOSTED COPY' : 'LOCAL MASTER';
  $('cloud-signout').hidden = !hostedCopy;
  $('cloud-refresh-button').hidden = !hostedCopy;
  $('data-centre-grid').hidden = false;
  $('data-centre-grid').classList.toggle('hosted-data', hostedCopy);
  $('manual-import-panel').hidden = hostedCopy;
  $('hosted-temporary-note').style.display = hostedCopy ? '' : 'none';
  $('cloud-action-help').textContent = hostedCopy
    ? 'On Render: download reports, use the top Run analysis button if needed, prepare this ZIP, then download it to your phone and replace the contents of the same separate Drive file so its ID stays unchanged.'
    : 'Upload the downloaded index ZIP as a separate file in your Google Drive. This does not update the stock dashboard’s file.';
  $('cloud-export-button').disabled = running || !cloud.analysis_ready || !!cloud.missing_underlyings?.length;
  $('cloud-file-button').disabled = running || !cloud.export_ready;
  $('cloud-file-button').textContent = cloud.export_ready ? `Download prepared market file (${cloud.export_mb} MB)` : 'Download prepared market file';
  $('cloud-refresh-button').disabled = running || restorePending || !cloud.drive_configured;
  const badge = $('cloud-state-badge');
  if (hostedCopy) {
    const driveLoadFailed = job.kind === 'cloud_refresh' && job.error;
    const taskLabel = job.kind === 'download' ? 'DOWNLOADING REPORTS'
      : job.kind === 'analysis' ? 'RUNNING ANALYSIS'
        : job.kind === 'cloud_export' ? 'PREPARING DRIVE FILE'
          : job.kind === 'cloud_refresh' ? 'LOADING FROM DRIVE' : 'TASK RUNNING';
    badge.textContent = running ? taskLabel
      : driveLoadFailed ? 'DRIVE LOAD FAILED'
      : restorePending ? 'LOADING FROM DRIVE'
      : cloud.export_ready ? 'DRIVE FILE READY'
        : cloud.saved_through ? 'DRIVE COPY LOADED'
          : cloud.drive_configured ? 'WAITING FOR DRIVE' : 'DRIVE SETUP NEEDED';
    badge.className = `state-badge ${(running && job.kind === 'cloud_refresh') || restorePending ? 'loading' : driveLoadFailed ? 'down' : cloud.export_ready || cloud.saved_through ? 'up' : ''}`;
    $('cloud-status').textContent = running
      ? (job.message || 'The hosted data task is running…')
      : driveLoadFailed ? ((job.log || []).at(-1) || 'Could not load the saved Google Drive copy.')
      : restorePending ? 'Restoring the saved market data from Google Drive. This page will refresh as soon as loading finishes.'
      : cloud.export_ready
        ? `A checked, index-only market file is ready${cloud.last_export_through ? ` through ${cloud.last_export_through}` : ''}. Download it, upload it to the same separate Drive file, then load the saved copy here.`
        : cloud.missing_underlyings?.length
          ? `Download NSE index OHLC for the five required NSE indices before preparing the Drive file. BSE index OHLC is optional. Missing: ${cloud.missing_underlyings.join(', ')}.`
          : cloud.analysis_ready
            ? 'The current market data and analysis are ready. Prepare and download the index-only ZIP, upload it to the separate Drive file, then load that saved copy here.'
            : state.counts.index_bars
              ? 'Market data is in this temporary Render copy. Use the top Run analysis button if needed, then prepare and download a Drive file before the hosted copy is restarted.'
              : cloud.saved_through
                ? `The hosted copy loaded the saved index data through ${cloud.saved_through}. You can download fresh NSE and BSE reports here, use the top Run analysis button if needed, and save a new Drive file.`
                : cloud.drive_configured
                  ? 'No saved market snapshot is loaded yet. Download reports here or load the configured Drive file.'
                  : 'Download reports here. The separate index ZIP file is configured for this project; click Load saved Google Drive copy to fetch it.';
    return;
  }

  badge.textContent = cloud.export_ready ? 'FILE READY'
    : cloud.missing_underlyings?.length ? 'NSE INDEX DATA NEEDED'
      : cloud.analysis_ready ? 'READY TO PREPARE' : state.counts.index_bars ? 'RUN ANALYSIS FIRST' : 'NO INDEX DATA';
  badge.className = `state-badge ${cloud.export_ready ? 'up' : ''}`;
  $('cloud-status').textContent = cloud.export_ready
    ? `Prepared market file matches the current local data and analysis${cloud.last_export_through ? ` · through ${cloud.last_export_through}` : ''}. Download it and upload it as a separate index file in Google Drive.`
    : cloud.missing_underlyings?.length
      ? `Download NSE index OHLC for the five required NSE indices before preparing the Drive file. BSE index OHLC is optional. Missing: ${cloud.missing_underlyings.join(', ')}.`
      : cloud.analysis_ready
        ? 'Local index data and analysis are ready. Prepare a fresh market file after each update, then download and upload it to Drive.'
        : cloud.export_reason || 'Download or import index data, then use the top Run analysis button if needed.';
}

function renderAnalysis(analysis, job = {}, item = null) {
  const status = $('analysis-status');
  const dataStatus = $('analysis-data-status');
  const analysisFailure = job.kind === 'analysis' && job.error
    ? `Analysis could not complete: ${(job.log || []).at(-1) || 'Check the task log.'}` : '';
  if (!analysis) {
    const message = analysisFailure || (job.kind === 'analysis' && job.running
      ? `Calculating analysis and two-month history through ${state.as_of}…`
      : `No saved analysis for ${state.as_of}. Change the date to calculate this view automatically.`);
    status.textContent = message;
    dataStatus.textContent = message;
    status.className = `muted analysis-status ${analysisFailure ? 'stale' : ''}`;
    dataStatus.className = `muted analysis-data-status ${analysisFailure ? 'stale' : ''}`;
    $('history-chart-range').textContent = '';
    renderHistoryChart([]);
    $('analysis-rows').innerHTML = '<tr><td colspan="9" class="empty">The selected date’s analysis and history will appear automatically.</td></tr>';
    return;
  }
  const stale = !!analysis.stale;
  const runLabel = analysis.run_at ? `Last saved run ${analysis.run_at} · data through ${analysis.as_of}` : `Calculated through ${analysis.as_of}`;
  const selectedRow = (analysis.results || []).find((result) => result.underlying === item?.underlying);
  const historyRows = selectedRow?.history?.length ? selectedRow.history : selectedRow?.day ? [selectedRow] : [];
  const firstDay = historyRows.length ? historyRows.at(-1).day : null;
  const lastDay = historyRows.length ? historyRows[0].day : null;
  const historyNote = historyRows.length ? ` · ${historyRows.length} sessions from ${firstDay} to ${lastDay} in the two-month window` : '';
  $('history-chart-range').textContent = historyRows.length ? `${firstDay} → ${lastDay} · ${historyRows.length} sessions` : '';
  renderHistoryChart(historyRows);
  const message = analysisFailure || `${stale
    ? `Data through ${analysis.as_of} changed since this saved run. Use the top Run analysis button to refresh these results.`
    : `${runLabel} · saved results match the selected date.`}${historyNote}`;
  status.textContent = job.kind === 'analysis' && job.running ? `Calculating analysis and two-month history through ${state.as_of}…` : message;
  dataStatus.textContent = job.kind === 'analysis' && job.running ? `Calculating analysis and two-month history through ${state.as_of}…` : message;
  status.className = `muted analysis-status ${stale || analysisFailure ? 'stale' : ''}`;
  dataStatus.className = `muted analysis-data-status ${stale || analysisFailure ? 'stale' : ''}`;
  $('analysis-rows').innerHTML = historyRows.length ? historyRows.map((row) => {
    const fiveDayTitle = row.futures_oi_5d_pct === null || row.futures_oi_5d_pct === undefined
      ? `${row.futures_5d_observations || 0} of 6 sessions have futures data; no complete matched basket is available.`
      : `Matched expiry basket from ${row.futures_5d_start_day || 'five sessions earlier'}; futures present on ${row.futures_5d_observations || 0} of the six cash sessions.`;
    return `<tr>
      <td><strong>${esc(row.name)}</strong><small>${esc(row.underlying)} · ${esc(row.exchange)}</small></td>
      <td data-sort-value="${esc(row.day || '')}"><strong>${esc(row.day || '—')}</strong><small class="close-detail">₹${num(row.index_close)} (${signedOne(row.index_close_1d_pct)})</small></td><td>${esc(row.option_expiry || '—')}</td>
      <td>${num(row.pcr_oi)}</td><td class="${row.pcr_oi_1d_pct > 0 ? 'positive' : row.pcr_oi_1d_pct < 0 ? 'negative' : ''}">${signedOne(row.pcr_oi_1d_pct)}</td>
      <td>${esc(row.futures_expiry || '—')}</td><td>${compact(row.futures_oi)}</td>
      <td class="${row.futures_oi_1d_pct > 0 ? 'positive' : row.futures_oi_1d_pct < 0 ? 'negative' : ''}">${signed(row.futures_oi_1d_pct)}</td>
      <td title="${esc(fiveDayTitle)}" class="${row.futures_oi_5d_pct > 0 ? 'positive' : row.futures_oi_5d_pct < 0 ? 'negative' : ''}">${compact(row.futures_oi)} (${signed(row.futures_oi_5d_pct)})</td>
    </tr>`;
  }).join('') : '<tr><td colspan="9" class="empty">No index results exist through this date.</td></tr>';
}

function renderHistoryChart(historyRows) {
  const host = $('analysis-chart-wrap');
  if (historyTooltipLayer?.host === host) {
    historyTooltipLayer.tooltip.remove();
    historyTooltipLayer = null;
  }
  if (activeTouchTooltip?.host === host) activeTouchTooltip = null;
  const rows = [...(historyRows || [])].filter((row) => row.day).reverse();
  if (!rows.length) {
    host.innerHTML = '<div class="empty">The selected date’s two-month history will appear after its automatic calculation.</div>';
    return;
  }

  const series = [
    {key: 'index_close', label: 'Index close (left axis)', unit: '₹', color: '#24668a', digits: 2, dash: '', marker: 'circle'},
    {key: 'pcr_oi_1d_pct', label: 'PCR OI 1D %', unit: '%', color: '#9b4c83', digits: 2, dash: '6 3', marker: 'square'},
    {key: 'futures_oi_1d_pct', label: 'Futures OI 1D %', unit: '%', color: '#b47726', digits: 2, dash: '2 3', marker: 'diamond'},
    {key: 'futures_oi_5d_pct', label: 'Futures OI 5D %', unit: '%', color: '#438060', digits: 2, dash: '8 3 2 3', marker: 'triangle'},
  ];
  const width = 1140;
  const height = 360;
  const left = 96;
  const right = 96;
  const top = 48;
  const bottom = 38;
  const plotBottom = height - bottom;
  const plotHeight = plotBottom - top;
  const plotWidth = width - left - right;
  const xAt = (index) => rows.length === 1 ? left + plotWidth / 2 : left + (index / (rows.length - 1)) * plotWidth;
  const svgParts = [];

  const valuesByKey = Object.fromEntries(series.map((item) => [item.key, rows.map((row) =>
    row[item.key] == null || !Number.isFinite(Number(row[item.key])) ? null : Number(row[item.key]))]));
  const closeValues = valuesByKey.index_close.filter((value) => value !== null);
  const closeMin = closeValues.length ? Math.min(...closeValues) : 0;
  const closeMax = closeValues.length ? Math.max(...closeValues) : 1;
  const closePad = closeMax === closeMin ? Math.max(Math.abs(closeMax) * 0.01, 1) : (closeMax - closeMin) * 0.08;
  const priceMin = closeMin - closePad;
  const priceMax = closeMax + closePad;
  const percentValues = ['pcr_oi_1d_pct', 'futures_oi_1d_pct', 'futures_oi_5d_pct']
    .flatMap((key) => valuesByKey[key].filter((value) => value !== null));
  const maxAbsPct = percentValues.length ? Math.max(...percentValues.map((value) => Math.abs(value))) : 1;
  const pctBound = maxAbsPct === 0 ? 1 : maxAbsPct * 1.12;
  const percentMin = -pctBound;
  const percentMax = pctBound;
  const yPrice = (value) => plotBottom - ((value - priceMin) / (priceMax - priceMin)) * plotHeight;
  const yPercent = (value) => plotBottom - ((value - percentMin) / (percentMax - percentMin)) * plotHeight;

  const legendXs = [left + 4, left + 234, left + 466, left + 704];
  series.forEach((item, index) => {
    const x = legendXs[index];
    const dashAttribute = item.dash ? `stroke-dasharray="${item.dash}"` : '';
    svgParts.push(`<line class="history-legend-line" x1="${x}" y1="20" x2="${x + 22}" y2="20" stroke="${item.color}" ${dashAttribute}/>`);
    svgParts.push(`<text class="history-legend-label" x="${x + 28}" y="24">${item.label}</text>`);
  });

  for (let tickIndex = 0; tickIndex <= 4; tickIndex += 1) {
    const fraction = tickIndex / 4;
    const y = top + fraction * plotHeight;
    const priceTick = priceMax - fraction * (priceMax - priceMin);
    const pctTick = percentMax - fraction * (percentMax - percentMin);
    svgParts.push(`<line class="history-gridline" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"/>`);
    svgParts.push(`<text class="history-axis-label" text-anchor="end" x="${left - 9}" y="${y + 3}">₹${num(priceTick, 2)}</text>`);
    svgParts.push(`<text class="history-axis-label" x="${width - right + 9}" y="${y + 3}">${num(pctTick, 2)}%</text>`);
  }
  svgParts.push(`<line class="history-axis" x1="${left}" y1="${top}" x2="${left}" y2="${plotBottom}"/>`);
  svgParts.push(`<line class="history-axis" x1="${width - right}" y1="${top}" x2="${width - right}" y2="${plotBottom}"/>`);
  svgParts.push(`<line class="history-axis" x1="${left}" y1="${plotBottom}" x2="${width - right}" y2="${plotBottom}"/>`);

  const lineParts = [];
  const pointParts = [];
  series.forEach((item) => {
    const values = valuesByKey[item.key];
    const yScale = item.key === 'index_close' ? yPrice : yPercent;
    const dashAttribute = item.dash ? ` stroke-dasharray="${item.dash}"` : '';
    let segment = [];
    const flushSegment = () => {
      if (segment.length > 1) lineParts.push(`<path class="history-line" stroke="${item.color}"${dashAttribute} d="${segment.join(' ')}"/>`);
      segment = [];
    };
    values.forEach((value, index) => {
      if (value === null) { flushSegment(); return; }
      const x = xAt(index);
      const y = yScale(value);
      segment.push(`${segment.length ? 'L' : 'M'} ${x.toFixed(2)} ${y.toFixed(2)}`);
      if (item.marker === 'square') pointParts.push(`<rect class="history-point" x="${(x - 2.7).toFixed(2)}" y="${(y - 2.7).toFixed(2)}" width="5.4" height="5.4" fill="${item.color}"/>`);
      else if (item.marker === 'diamond') pointParts.push(`<path class="history-point" fill="${item.color}" d="M ${x.toFixed(2)} ${(y - 3.3).toFixed(2)} L ${(x + 3.3).toFixed(2)} ${y.toFixed(2)} L ${x.toFixed(2)} ${(y + 3.3).toFixed(2)} L ${(x - 3.3).toFixed(2)} ${y.toFixed(2)} Z"/>`);
      else if (item.marker === 'triangle') pointParts.push(`<path class="history-point" fill="${item.color}" d="M ${x.toFixed(2)} ${(y - 3.4).toFixed(2)} L ${(x + 3.2).toFixed(2)} ${(y + 2.5).toFixed(2)} L ${(x - 3.2).toFixed(2)} ${(y + 2.5).toFixed(2)} Z"/>`);
      else pointParts.push(`<circle class="history-point" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="3" fill="${item.color}"/>`);
    });
    flushSegment();
  });
  svgParts.push(...lineParts, ...pointParts);

  const labelStep = Math.max(1, Math.ceil(rows.length / 8));
  rows.forEach((row, index) => {
    if (index % labelStep !== 0 && index !== rows.length - 1) return;
    const label = row.day.slice(5);
    svgParts.push(`<text class="history-date-label" text-anchor="middle" x="${xAt(index)}" y="${height - 8}">${esc(label)}</text>`);
  });
  const bandEdges = rows.map((_, index) => index === 0 ? left - 8 : (xAt(index - 1) + xAt(index)) / 2)
    .map((edge, index) => index === rows.length - 1 ? {start: edge, end: width - right + 8} : {start: edge, end: (xAt(index) + xAt(index + 1)) / 2});
  bandEdges.forEach((band, index) => {
    svgParts.push(`<rect class="history-hit" data-history-index="${index}" x="${Math.max(left - 8, band.start)}" y="${top}" width="${Math.max(1, band.end - band.start)}" height="${plotBottom - top}"/>`);
  });
  host.innerHTML = `<svg class="analysis-history-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="One chart of index close on the left axis and PCR and futures open-interest percentage changes on the shared right axis">${svgParts.join('')}</svg>`;

  const svg = host.querySelector('svg');
  const tooltip = document.createElement('div');
  tooltip.className = 'history-tooltip';
  tooltip.hidden = true;
  document.body.append(tooltip);
  historyTooltipLayer = {host, tooltip};
  let touchPinned = false;
  host.querySelectorAll('[data-history-index]').forEach((hit) => {
    const show = (event, pinForTouch = false) => {
      const index = Number(hit.dataset.historyIndex);
      const row = rows[index];
      if (!row) return;
      tooltip.innerHTML = `<strong>${esc(row.day)}</strong>${series.map((item) => {
        const value = row[item.key];
        const shown = value == null ? '—' : item.unit === '₹' ? `₹${num(value, item.digits)}` : `${signed(Number(value))}`;
        return `<span><i style="background:${item.color}"></i>${item.label}: <b>${shown}</b></span>`;
      }).join('')}`;
      tooltip.hidden = false;
      const bounds = tooltip.getBoundingClientRect();
      const pageX = Number.isFinite(event.pageX) ? event.pageX : event.clientX + window.scrollX;
      const pageY = Number.isFinite(event.pageY) ? event.pageY : event.clientY + window.scrollY;
      const viewportLeft = window.scrollX + 8;
      const viewportTop = window.scrollY + 8;
      const viewportRight = window.scrollX + (document.documentElement.clientWidth || window.innerWidth) - 8;
      const viewportBottom = window.scrollY + (document.documentElement.clientHeight || window.innerHeight) - 8;
      const maxLeft = Math.max(viewportLeft, viewportRight - bounds.width);
      const maxTop = Math.max(viewportTop, viewportBottom - bounds.height);
      tooltip.style.left = `${Math.max(viewportLeft, Math.min(pageX + 14, maxLeft))}px`;
      const aboveFinger = pageY - bounds.height - 12;
      const desiredY = pinForTouch && aboveFinger >= viewportTop ? aboveFinger : pageY + 12;
      tooltip.style.top = `${Math.max(viewportTop, Math.min(desiredY, maxTop))}px`;
      touchPinned = pinForTouch;
    };
    hit.addEventListener('pointerenter', (event) => {
      if (event.pointerType !== 'touch') show(event);
    });
    hit.addEventListener('pointermove', (event) => {
      if (event.pointerType !== 'touch') show(event);
    });
    hit.addEventListener('click', (event) => {
      const touch = isTouchPointer(event);
      if (touch) activeTouchTooltip = {host, tooltip};
      show(event, touch);
    });
  });
  svg.addEventListener('pointerleave', (event) => {
    if (event.pointerType === 'touch' || touchPinned) return;
    tooltip.hidden = true;
  });
}

function renderJob(job = {}) {
  const restorePending = startupDriveRestorePending();
  const busy = !!job.running || restorePending;
  $('task-panel').hidden = !busy && !(job.log || []).length;
  $('task-label').textContent = restorePending && !job.running ? 'Restoring saved index data from Google Drive…' : job.message || 'Waiting for a download';
  $('task-badge').textContent = job.running ? 'WORKING' : restorePending ? 'LOADING' : job.error ? 'NEEDS ATTENTION' : 'FINISHED';
  $('task-badge').className = `state-badge ${busy ? 'loading' : job.error ? 'down' : 'neutral'}`;
  $('task-log').textContent = (job.log || []).join('\n');
  $('progress').hidden = !busy;
  $('download-button').disabled = busy;
  $('import-button').disabled = busy;
  $('research-date').disabled = busy;
  $('research-latest').disabled = busy;
  document.querySelectorAll('[data-run-analysis]').forEach((button) => { button.disabled = busy; });
  if (busy && !jobPoller) {
    jobPoller = setInterval(async () => {
      if (jobPollInFlight) return;
      jobPollInFlight = true;
      try {
        await pollJobStatus();
      } catch (error) {
        $('task-label').textContent = `Reconnecting to task status… ${error.message}`;
      } finally {
        jobPollInFlight = false;
      }
    }, 1400);
  } else if (!job.running && !restorePending && jobPoller) {
    clearInterval(jobPoller);
    jobPoller = null;
  }
  if (job.message && job.kind === 'download') {
    $('download-status').textContent = job.message;
    $('download-status').className = `import-status ${job.error ? 'error' : job.running ? '' : 'success'}`;
  }
}

async function pollJobStatus() {
  const response = await apiFetch('/api/job');
  const job = await response.json();
  if (!response.ok) throw new Error(job.error || 'Could not read task status.');
  state.job = job;
  renderJob(job);
  renderCloud(state.cloud || {}, job);
  const unchangedDriveCopy = job.kind === 'cloud_refresh'
    && (job.log || []).some((message) => message.includes('already loaded'));
  const pageHasLoadedData = !!state?.coverage?.indexes?.rows;
  if (!job.running && !startupDriveRestorePending(state.cloud, job)
      && (!unchangedDriveCopy || !pageHasLoadedData)) await refresh();
}

function beginClientJob(kind, message) {
  state.job = {running: true, kind, message, log: [], error: false};
  renderJob(state.job);
  renderCloud(state.cloud || {}, state.job);
}

async function prepareCloudFile() {
  const button = $('cloud-export-button');
  button.disabled = true;
  $('cloud-status').textContent = 'Preparing a checked, index-only market file…';
  try {
    const response = await apiFetch('/api/cloud/export', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not prepare the market file.');
    beginClientJob('cloud_export', 'Preparing the index-only Google Drive market file…');
  } catch (error) {
    $('cloud-status').textContent = error.message;
    button.disabled = false;
  }
}

async function downloadCloudFile() {
  const button = $('cloud-file-button');
  const label = button.textContent;
  const wasDisabled = button.disabled;
  button.disabled = true;
  button.classList.add('loading');
  button.setAttribute('aria-busy', 'true');
  button.textContent = 'Downloading prepared market file…';
  try {
    const response = await apiFetch('/api/cloud/file');
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || 'Could not download the prepared market file.');
    }
    const blobUrl = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = 'Index Options Market.zip';
    link.click();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);
  } catch (error) {
    $('cloud-status').textContent = error.message;
  } finally {
    button.disabled = wasDisabled;
    button.classList.remove('loading');
    button.removeAttribute('aria-busy');
    button.textContent = label;
  }
}

async function loadCloudFile() {
  const button = $('cloud-refresh-button');
  if (state?.cloud?.unsaved_market_changes && !window.confirm('Replace this temporary hosted data with the saved Google Drive copy?')) return;
  button.disabled = true;
  $('cloud-state-badge').className = 'state-badge loading';
  $('cloud-state-badge').textContent = 'LOADING FROM DRIVE';
  $('cloud-status').textContent = 'Checking whether the saved Google Drive file has changed…';
  try {
    const response = await apiFetch('/api/cloud/refresh', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({discard_temporary: !!state?.cloud?.unsaved_market_changes}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not load the Google Drive copy.');
    beginClientJob('cloud_refresh', 'Loading the saved Google Drive market file…');
  } catch (error) {
    $('cloud-status').textContent = error.message;
    $('cloud-state-badge').className = 'state-badge down';
    $('cloud-state-badge').textContent = 'DRIVE LOAD FAILED';
  } finally {
    if (!state?.job?.running) button.disabled = false;
  }
}

$('cloud-export-button').addEventListener('click', prepareCloudFile);
$('cloud-file-button').addEventListener('click', downloadCloudFile);
$('cloud-refresh-button').addEventListener('click', loadCloudFile);
$('cloud-signout').addEventListener('click', () => { authToken = ''; storeToken(''); location.reload(); });
$('cloud-login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const password = $('cloud-password');
  const status = $('login-status');
  const button = event.target.querySelector('button');
  button.disabled = true;
  try {
    const response = await fetch('/api/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: password.value}), cache: 'no-store',
    });
    const result = await response.json();
    password.value = '';
    if (!response.ok) throw new Error(result.error || 'Sign-in failed.');
    authToken = result.token;
    storeToken(authToken);
    status.textContent = '';
    $('cloud-login').hidden = true;
    document.body.classList.remove('needs-signin');
    if (hosted) showHostedDriveAction();
    await refresh();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll('[data-page]').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('[data-page]').forEach((tab) => {
    const active = tab === button;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
    $(tab.dataset.page).hidden = !active;
  });
  window.scrollTo({top: 0, behavior: 'smooth'});
}));

$('refresh').addEventListener('click', () => refresh());
$('refresh-files').addEventListener('click', () => refresh());
function calendarMonthsBefore(day, months) {
  const [year, month, date] = day.split('-').map(Number);
  const targetMonthIndex = year * 12 + month - 1 - months;
  const targetYear = Math.floor(targetMonthIndex / 12);
  const targetMonth = targetMonthIndex % 12 + 1;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate();
  return `${targetYear}-${String(targetMonth).padStart(2, '0')}-${String(Math.min(date, lastDay)).padStart(2, '0')}`;
}

function selectedHistoryNeedsAnalysis() {
  const analysis = state?.analysis;
  if (!analysis || analysis.stale) return true;
  const result = (analysis.results || []).find((row) => row.underlying === selected);
  const index = (state?.indexes || []).find((row) => row.underlying === selected);
  if (!result || !index) return true;
  const through = analysis.as_of || state.as_of;
  const from = calendarMonthsBefore(through, 2);
  const expectedSessions = (index.bars || []).filter((bar) => bar.day >= from && bar.day <= through).length;
  const storedSessions = (result.history || []).filter((row) => row.day >= from && row.day <= through).length;
  return expectedSessions > storedSessions;
}

async function startAnalysis(asOf) {
  if (state?.job?.running) return;
  const message = `Calculating analysis and two-month history through ${asOf}…`;
  $('analysis-status').textContent = message;
  $('analysis-data-status').textContent = message;
  try {
    const response = await apiFetch('/api/analyze', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({as_of: asOf}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not start analysis.');
    beginClientJob('analysis', message);
  } catch (error) {
    $('analysis-status').textContent = error.message;
    $('analysis-status').className = 'muted analysis-status stale';
    $('analysis-data-status').textContent = error.message;
    $('analysis-data-status').className = 'muted analysis-data-status stale';
    state.job = {running: false, kind: 'analysis', message: error.message, log: [error.message], error: true};
    renderJob(state.job);
    renderCloud(state.cloud || {}, state.job);
  }
}

async function selectResearchDate(date) {
  if (!date) return;
  $('research-date').value = date;
  $('research-date').disabled = true;
  $('research-date-status').textContent = `Loading index research through ${date}… Later rows are excluded.`;
  const loaded = await refresh(date);
  if (loaded && state?.counts.index_bars && !state?.job?.running && selectedHistoryNeedsAnalysis()) {
    await startAnalysis(date);
  }
  $('research-date').disabled = !!state?.job?.running;
}

$('research-date').addEventListener('change', () => selectResearchDate($('research-date').value));
$('research-latest').addEventListener('click', async () => {
  const latest = state?.latest_date || state?.today;
  if (!latest) return;
  $('research-latest').disabled = true;
  await selectResearchDate(latest);
  $('research-latest').disabled = !!state?.job?.running;
});
document.querySelectorAll('[data-run-analysis]').forEach((button) => button.addEventListener('click', () => {
  startAnalysis($('research-date').value);
}));
$('evaluate-model').addEventListener('click', async () => {
  $('evaluate-model').disabled = true;
  $('evaluation-status').textContent = `Starting chronological replay for ${selected} through ${$('research-date').value || state?.as_of || ''}…`;
  try {
    const response = await apiFetch('/api/evaluate', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({underlying: selected, as_of: $('research-date').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not start history evaluation.');
    await refresh();
  } catch (error) {
    $('evaluation-status').textContent = error.message;
    $('evaluate-model').disabled = false;
  }
});
$('download-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const start = $('start').value, end = $('end').value;
  const segments = [];
  if ($('download-indexes').checked) segments.push('indexes');
  if ($('download-fo').checked) segments.push('nse_fo');
  if ($('download-bse-indexes').checked) segments.push('bse_indexes');
  if ($('download-bse-fo').checked) segments.push('bse_fo');
  if (!start || !end) { $('download-status').textContent = 'Choose both From and Through dates.'; return; }
  if (start > end) { $('download-status').textContent = 'From must be on or before Through.'; return; }
  if ((new Date(`${end}T00:00:00`) - new Date(`${start}T00:00:00`)) / 86400000 > 92) { $('download-status').textContent = 'Choose at most 93 calendar days per request.'; return; }
  if (!segments.length) { $('download-status').textContent = 'Select at least one report type.'; return; }
  $('download-button').disabled = true;
  $('download-status').textContent = `Sending ${start} through ${end} to the NSE and BSE report downloader…`;
  try {
    const response = await apiFetch('/api/download', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({start, end, segments})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not start the download.');
    await refresh();
  } catch (error) {
    $('download-status').textContent = error.message;
    $('download-button').disabled = false;
  }
});

$('import-button').addEventListener('click', async () => {
  const files = [...$('csv-file').files];
  if (!files.length) {
    $('import-status').textContent = 'Choose one or more downloaded report files first.';
    $('import-status').className = 'import-status error';
    return;
  }
  const mode = $('dataset').value;
  const fromDate = $('filter-import-range').checked ? $('start').value : '';
  const toDate = $('filter-import-range').checked ? $('end').value : '';
  if (fromDate && toDate && fromDate > toDate) {
    $('import-status').textContent = 'The start date must be on or before the end date.';
    $('import-status').className = 'import-status error';
    return;
  }
  let totalRows = 0, firstDay = '', lastDay = '';
  $('import-button').disabled = true;
  $('import-status').textContent = `Preparing ${files.length} report file${files.length === 1 ? '' : 's'}…`;
  $('import-status').className = 'import-status';
  try {
    for (const [index, file] of files.entries()) {
      $('import-status').textContent = `Importing file ${index + 1} of ${files.length}: ${file.name}…`;
      const response = await apiFetch('/api/import', {method: 'POST', headers: {
        'Content-Type': 'application/octet-stream',
        'X-Import-Mode': mode,
        'X-File-Name': encodeURIComponent(file.name),
        'X-Selected-Index': $('selected-index').value,
        'X-From-Date': fromDate,
        'X-To-Date': toDate,
      }, body: file});
      const result = await response.json();
      if (!response.ok) throw new Error(`${file.name}: ${result.error || 'Import failed.'}`);
      totalRows += result.rows;
      if (result.first_day) firstDay = !firstDay || result.first_day < firstDay ? result.first_day : firstDay;
      if (result.last_day) lastDay = !lastDay || result.last_day > lastDay ? result.last_day : lastDay;
    }
    if (totalRows) {
      $('import-status').textContent = `Imported ${compact(totalRows)} rows (${firstDay} to ${lastDay}) from ${files.length} file${files.length === 1 ? '' : 's'}.`;
      $('import-status').className = 'import-status success';
    } else {
      $('import-status').textContent = 'No rows from these files matched the selected date range.';
      $('import-status').className = 'import-status error';
    }
    $('csv-file').value = '';
    await refresh();
  } catch (error) {
    $('import-status').textContent = totalRows ? `Imported ${compact(totalRows)} rows before a later file failed. ${error.message}` : error.message;
    $('import-status').className = 'import-status error';
    if (totalRows) await refresh();
  } finally {
    $('import-button').disabled = false;
  }
});

$('dataset').addEventListener('change', () => {
  const nseFo = $('dataset').value === 'nse_fo';
  $('selected-index').disabled = nseFo;
});

function sortTable(table, column, direction, updateState = true) {
  const body = table.tBodies[0];
  if (!body) return;
  if (updateState) tableSortState.set(table, {column, direction});
  const rows = [...body.rows].filter((row) => !row.querySelector('.empty'));
  const value = (row) => row.cells[column]?.dataset.sortValue ?? row.cells[column]?.textContent.trim() ?? '';
  const numeric = (raw) => {
    const clean = String(raw).replace(/[₹,%+×,\s]/g, '');
    return clean && /^-?\d+(\.\d+)?$/.test(clean) ? Number(clean) : null;
  };
  rows.sort((a, b) => {
    const av = value(a), bv = value(b), an = numeric(av), bn = numeric(bv);
    const am = !String(av).trim() || String(av).trim() === '—';
    const bm = !String(bv).trim() || String(bv).trim() === '—';
    if (am !== bm) return am ? 1 : -1;
    let compare = an !== null && bn !== null ? an - bn : String(av).localeCompare(String(bv), 'en', {numeric: true, sensitivity: 'base'});
    return direction === 'desc' ? -compare : compare;
  });
  rows.forEach((row) => body.appendChild(row));
  [...table.tHead.rows[0].cells].forEach((cell, index) => {
    cell.setAttribute('aria-sort', index === column ? (direction === 'asc' ? 'ascending' : 'descending') : 'none');
  });
}

function enableSortableTables() {
  document.querySelectorAll('table[data-sortable]').forEach((table) => {
    [...table.tHead.rows[0].cells].forEach((header, column) => {
      if (header.dataset.sortReady) return;
      header.dataset.sortReady = 'true';
      header.tabIndex = 0;
      header.setAttribute('aria-sort', 'none');
      const activate = () => {
        const current = tableSortState.get(table);
        const direction = current?.column === column && current.direction === 'asc' ? 'desc' : 'asc';
        sortTable(table, column, direction);
      };
      header.addEventListener('click', activate);
      header.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
      });
    });
    const current = tableSortState.get(table);
    if (current) sortTable(table, current.column, current.direction, false);
  });
}

boot();
