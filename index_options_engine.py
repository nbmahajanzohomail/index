"""Transparent index-only next-session positioning model.

This is a frozen, explainable rule baseline. Its scores are not probabilities,
and it is deliberately prevented from emitting a high-confidence forecast.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics

MODEL_VERSION = 'INDEX-PRESSURE-1.0'
UNIVERSE = [
    {'underlying': 'NIFTY', 'exchange': 'NSE', 'name': 'Nifty 50'},
    {'underlying': 'BANKNIFTY', 'exchange': 'NSE', 'name': 'Nifty Bank'},
    {'underlying': 'SENSEX', 'exchange': 'BSE', 'name': 'Sensex'},
    {'underlying': 'FINNIFTY', 'exchange': 'NSE', 'name': 'FINNIFTY (Nifty Financial Services)'},
    {'underlying': 'BANKEX', 'exchange': 'BSE', 'name': 'Bankex'},
    {'underlying': 'MIDCPNIFTY', 'exchange': 'NSE', 'name': 'Nifty Midcap Select'},
    {'underlying': 'NIFTYNXT50', 'exchange': 'NSE', 'name': 'Nifty Next 50'},
]


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def _pct(now, before):
    return (now / before - 1) * 100 if before not in (None, 0) and now is not None else None


def _tr(row, previous_close):
    return max(row['high'] - row['low'], abs(row['high'] - previous_close), abs(row['low'] - previous_close))


def _cash_history(c, symbol, as_of, limit=None):
    sql = 'SELECT day,open,high,low,close FROM index_bars WHERE underlying=? AND day<=? ORDER BY day DESC'
    params = [symbol, as_of]
    if limit:
        sql += ' LIMIT ?'
        params.append(limit)
    rows = [dict(row) for row in c.execute(sql, params)]
    rows.reverse()
    return rows


def _futures(c, symbol, day):
    return [dict(row) for row in c.execute('''SELECT expiry,close,volume,oi,lot_size FROM futures_bars
        WHERE underlying=? AND day=? AND volume>0 AND expiry>? ORDER BY expiry''', (symbol, day, day))]


def _option_rows(c, symbol, day, spot, as_of):
    start = (dt.date.fromisoformat(day) + dt.timedelta(days=2)).isoformat()
    end = (dt.date.fromisoformat(day) + dt.timedelta(days=45)).isoformat()
    low, high = spot * 0.92, spot * 1.08
    return [dict(row) for row in c.execute('''SELECT expiry,strike,option_type,close,volume,oi,lot_size
        FROM option_bars WHERE underlying=? AND day=? AND expiry>=? AND expiry<=?
        AND strike>=? AND strike<=? AND volume>0 AND oi>0 AND close>0
        ORDER BY expiry,strike,option_type''', (symbol, as_of, start, end, low, high))]


def _price_features(rows):
    if not rows:
        return {}, 0.0, ['No index OHLC is available on or before the selected date.'], []
    latest = rows[-1]
    closes = [row['close'] for row in rows]
    tr_values = []
    for i, row in enumerate(rows):
        previous = rows[i - 1]['close'] if i else row['close']
        tr_values.append(_tr(row, previous))
    atr_count = min(14, len(tr_values))
    atr = statistics.mean(tr_values[-atr_count:]) if atr_count >= 5 else None
    atr_pct = atr / latest['close'] * 100 if atr and latest['close'] > 0 else None
    ret1 = _pct(closes[-1], closes[-2]) if len(closes) >= 2 else None
    ret5 = _pct(closes[-1], closes[-6]) if len(closes) >= 6 else None
    sma20 = statistics.mean(closes[-20:]) if len(closes) >= 20 else None
    close_location = ((2 * latest['close'] - latest['high'] - latest['low']) /
                      (latest['high'] - latest['low'])) if latest['high'] > latest['low'] else 0.0
    prior = rows[-21:-1] if len(rows) >= 21 else []
    prior_high = max((row['high'] for row in prior), default=None)
    prior_low = min((row['low'] for row in prior), default=None)
    current_range = statistics.mean(tr_values[-5:]) if len(tr_values) >= 5 else None
    base_ranges = tr_values[-21:-5] if len(tr_values) >= 21 else tr_values[:-5]
    baseline_range = statistics.median(base_ranges) if len(base_ranges) >= 5 else None
    volatility_ratio = current_range / baseline_range if current_range and baseline_range else None

    components = []
    if ret5 is not None and atr_pct:
        components.append(14 * _clamp(ret5 / (atr_pct * math.sqrt(5))))
    if ret1 is not None and atr_pct:
        components.append(7 * _clamp(ret1 / atr_pct))
    components.append(3 * _clamp(close_location))
    if sma20 is not None:
        components.append(4 if latest['close'] > sma20 else -4 if latest['close'] < sma20 else 0)
    breakout = None
    if prior_high is not None and latest['close'] > prior_high:
        components.append(7)
        breakout = 'up'
    elif prior_low is not None and latest['close'] < prior_low:
        components.append(-7)
        breakout = 'down'
    price_score = _clamp(sum(components), -35, 35)
    features = {'sessions': len(rows), 'close': latest['close'], 'ret1_pct': ret1, 'ret5_pct': ret5,
                'sma20': sma20, 'atr_pct': atr_pct, 'atr_sessions': atr_count,
                'close_location': close_location, 'prior20_high': prior_high, 'prior20_low': prior_low,
                'breakout': breakout, 'volatility_ratio': volatility_ratio}
    reasons = []
    if ret5 is not None:
        reasons.append(f'Index 5-session move {ret5:+.2f}% with an ATR-normalized trend component.')
    if ret1 is not None:
        reasons.append(f'Latest index session move {ret1:+.2f}%; close location {close_location:+.2f}.')
    if breakout:
        reasons.append(f'Close moved beyond the prior 20-session {breakout} boundary.')
    warnings = []
    if len(rows) < 20:
        warnings.append(f'Only {len(rows)} index sessions are available; the 20-session context is incomplete.')
    if atr_pct is None:
        warnings.append('Insufficient sessions for a stable ATR reference.')
    return features, price_score, reasons, warnings


def _futures_features(c, symbol, day, rows):
    today = _futures(c, symbol, day)
    if not today:
        return {'expiry': None, 'oi': None, 'oi1d_pct': None, 'oi5d_pct': None,
                'price1d_pct': None, 'basis_pct': None}, 0.0, [], ['No matching-date traded index futures.']
    nearest = today[0]
    previous_day = rows[-2]['day'] if len(rows) >= 2 else None
    prior = None
    if previous_day:
        prior = c.execute('''SELECT close,oi,lot_size FROM futures_bars
            WHERE underlying=? AND day=? AND expiry=?''', (symbol, previous_day, nearest['expiry'])).fetchone()
    price1d = _pct(nearest['close'], prior['close']) if prior else None
    oi1d = _pct(nearest['oi'], prior['oi']) if prior and prior['oi'] and prior['oi'] > 0 else None
    one_day_score = 0.0
    if price1d is not None and oi1d is not None:
        if price1d > 0 and oi1d > 0:
            one_day_score = 14.0
        elif price1d < 0 and oi1d > 0:
            one_day_score = -14.0
        elif price1d > 0 and oi1d < 0:
            one_day_score = 7.0
        elif price1d < 0 and oi1d < 0:
            one_day_score = -7.0

    oi5d = None
    matched = []
    if len(rows) >= 6:
        old_day = rows[-6]['day']
        old_rows = {row['expiry']: dict(row) for row in c.execute('''SELECT expiry,oi,lot_size FROM futures_bars
            WHERE underlying=? AND day=?''', (symbol, old_day))}
        for current in today[:3]:
            old = old_rows.get(current['expiry'])
            if not old or not old['oi'] or old['oi'] <= 0:
                continue
            if current['lot_size'] and old['lot_size'] and current['lot_size'] != old['lot_size']:
                continue
            matched.append((current, old))
        old_total = sum(old['oi'] for _, old in matched)
        new_total = sum(current['oi'] for current, _ in matched)
        if matched and old_total > 0:
            oi5d = _pct(new_total, old_total)
    price5d = _pct(rows[-1]['close'], rows[-6]['close']) if len(rows) >= 6 else None
    five_day_score = 0.0
    if oi5d is not None and price5d is not None:
        if price5d > 0 and oi5d > 0:
            five_day_score = 8.0
        elif price5d < 0 and oi5d > 0:
            five_day_score = -8.0
        elif price5d > 0 and oi5d < 0:
            five_day_score = 4.0
        elif price5d < 0 and oi5d < 0:
            five_day_score = -4.0
    score = _clamp(one_day_score + five_day_score, -25, 25)
    spot = rows[-1]['close']
    features = {'expiry': nearest['expiry'], 'oi': nearest['oi'], 'oi1d_pct': oi1d,
                'oi5d_pct': oi5d, 'price1d_pct': price1d, 'price5d_pct': price5d,
                'basis_pct': _pct(nearest['close'], spot), 'matched5d_expiries': len(matched)}
    reasons = []
    if price1d is not None and oi1d is not None:
        reasons.append(f'Nearest futures {nearest["expiry"]}: price {price1d:+.2f}% and same-expiry OI {oi1d:+.2f}% 1D (positioning hypothesis).')
    if oi5d is not None:
        reasons.append(f'Matched-basket futures OI {oi5d:+.2f}% over five sessions across {len(matched)} expiries.')
    warnings = []
    if oi1d is None:
        warnings.append('Same-expiry futures comparison is missing; the one-day futures signal is unavailable.')
    if len(rows) < 6 or oi5d is None:
        warnings.append('Five-session futures evidence is incomplete.')
    return features, score, reasons, warnings


def _options_features(c, symbol, day, spot, previous_day):
    current = _option_rows(c, symbol, day, spot, day)
    if not current or not previous_day:
        return {'expiry': current[0]['expiry'] if current else None, 'matched_contracts': 0,
                'call_oi_change': None, 'put_oi_change': None, 'put_support': None, 'call_resistance': None}, 0.0, [], ['No matching-date option contracts with a prior-session comparison.']
    expiry = min(row['expiry'] for row in current)
    current = [row for row in current if row['expiry'] == expiry]
    previous = _option_rows(c, symbol, previous_day, spot, previous_day)
    previous = { (row['expiry'], row['strike'], row['option_type']): row for row in previous }
    numerator = denominator = 0.0
    calls_oi_change = puts_oi_change = 0.0
    calls_oi_prev = puts_oi_prev = 0.0
    matched = 0
    for row in current:
        key = (row['expiry'], row['strike'], row['option_type'])
        old = previous.get(key)
        if not old or not old['close'] or old['close'] <= 0 or not old['oi'] or old['oi'] <= 0:
            continue
        if row['lot_size'] and old['lot_size'] and row['lot_size'] != old['lot_size']:
            continue
        oi_change = _pct(row['oi'], old['oi'])
        premium_change = _pct(row['close'], old['close'])
        if oi_change is None or premium_change is None or abs(oi_change) < 1.0 or abs(premium_change) < 0.25:
            continue
        matched += 1
        if row['option_type'] == 'CE':
            calls_oi_change += row['oi'] - old['oi']
            calls_oi_prev += old['oi']
            side = 1.0
        else:
            puts_oi_change += row['oi'] - old['oi']
            puts_oi_prev += old['oi']
            side = -1.0
        distance = abs(row['strike'] / spot - 1)
        proximity = max(0.15, 1 - distance / 0.08)
        activity = min(abs(oi_change) / 15, 1.5) * proximity
        # Premium/OI quadrants are weak positioning hints; falling premium with
        # added OI is consistent with writing pressure, while rising premium
        # with added OI is consistent with option demand. Neither proves intent.
        if oi_change < 0:
            activity *= 0.35
        premium_sign = 1.0 if premium_change > 0 else -1.0
        numerator += side * premium_sign * activity
        denominator += activity
    raw_score = numerator / denominator * 20 if denominator else 0.0
    score = _clamp(raw_score, -20, 20)
    puts_below = [row for row in current if row['option_type'] == 'PE' and row['strike'] <= spot]
    calls_above = [row for row in current if row['option_type'] == 'CE' and row['strike'] >= spot]
    put_support = max(puts_below, key=lambda row: row['oi']) if puts_below else None
    call_resistance = max(calls_above, key=lambda row: row['oi']) if calls_above else None
    features = {'expiry': expiry, 'matched_contracts': matched,
                'call_oi_change_pct': _pct(calls_oi_change + calls_oi_prev, calls_oi_prev) if calls_oi_prev else None,
                'put_oi_change_pct': _pct(puts_oi_change + puts_oi_prev, puts_oi_prev) if puts_oi_prev else None,
                'put_support': {'strike': put_support['strike'], 'oi': put_support['oi']} if put_support else None,
                'call_resistance': {'strike': call_resistance['strike'], 'oi': call_resistance['oi']} if call_resistance else None}
    reasons = []
    if matched:
        reasons.append(f'{matched} same-expiry near-spot option contracts show premium/OI changes; score is a weak quadrant proxy.')
    if put_support:
        reasons.append(f'Largest included PE OI below spot is at {put_support["strike"]:g}; an OI landmark, not guaranteed support.')
    if call_resistance:
        reasons.append(f'Largest included CE OI above spot is at {call_resistance["strike"]:g}; an OI landmark, not guaranteed resistance.')
    warnings = []
    if matched < 6:
        warnings.append(f'Only {matched} same-expiry option comparisons are usable; option evidence is sparse.')
    warnings.append('Option OI and premium quadrants do not identify who initiated a trade.')
    return features, score, reasons, warnings


def _cross_index(c, symbol, day, universe):
    signs = []
    for other in universe:
        if other['underlying'] == symbol:
            continue
        rows = [dict(row) for row in c.execute('''SELECT day,close FROM index_bars
            WHERE underlying=? AND day<=? ORDER BY day DESC LIMIT 2''', (other['underlying'], day))]
        if len(rows) == 2 and rows[0]['day'] == day and rows[1]['close']:
            signs.append(1 if rows[0]['close'] > rows[1]['close'] else -1 if rows[0]['close'] < rows[1]['close'] else 0)
    score = 8 * sum(signs) / 6 if signs else 0.0
    return {'aligned_indices': len(signs), 'positive_indices': sum(value > 0 for value in signs),
            'negative_indices': sum(value < 0 for value in signs)}, score


def forecast(c, item, as_of, universe=None):
    """Return an as-of-only next-session bias, component scores, and evidence."""
    universe = universe or UNIVERSE
    symbol = item['underlying']
    rows = _cash_history(c, symbol, as_of, 31)
    if not rows:
        return {'model_version': MODEL_VERSION, 'underlying': symbol, 'exchange': item['exchange'],
                'name': item['name'], 'as_of': None, 'direction': 'INSUFFICIENT DATA',
                'confidence': 'None', 'bullish_score': 0, 'bearish_score': 0, 'net_score': 0,
                'scores': {}, 'features': {}, 'reasons': [], 'warnings': ['No index price history is available.']}
    day = rows[-1]['day']
    price_features, price_score, price_reasons, price_warnings = _price_features(rows)
    future_features, future_score, future_reasons, future_warnings = _futures_features(c, symbol, day, rows)
    option_features, option_score, option_reasons, option_warnings = _options_features(
        c, symbol, day, price_features['close'], rows[-2]['day'] if len(rows) >= 2 else None)
    cross_features, cross_score = _cross_index(c, symbol, day, universe)
    scores = {'index_structure': round(price_score, 2), 'futures_positioning': round(future_score, 2),
              'options_positioning': round(option_score, 2), 'cross_index_context': round(cross_score, 2)}
    bullish = sum(max(value, 0) for value in scores.values())
    bearish = sum(max(-value, 0) for value in scores.values())
    net = bullish - bearish
    positive_groups = sum(value >= 3 for value in scores.values())
    negative_groups = sum(value <= -3 for value in scores.values())
    atr_pct = price_features.get('atr_pct')
    volatility_ratio = price_features.get('volatility_ratio')
    prior_high, prior_low = price_features.get('prior20_high'), price_features.get('prior20_low')
    close = price_features.get('close')
    inside_prior_range = prior_high is not None and prior_low is not None and prior_low <= close <= prior_high

    if len(rows) < 20:
        direction = 'INSUFFICIENT DATA'
    elif net >= 25 and price_score > 0 and (future_score > 0 or option_score > 0 or cross_score > 0):
        direction = 'RISE'
    elif net <= -25 and price_score < 0 and (future_score < 0 or option_score < 0 or cross_score < 0):
        direction = 'FALL'
    elif (len(rows) >= 20 and volatility_ratio is not None and volatility_ratio < 0.85
          and abs(net) < 15 and abs(price_score) < 10 and inside_prior_range):
        direction = 'RANGE'
    else:
        direction = 'NO EDGE'

    aligned = positive_groups if direction == 'RISE' else negative_groups if direction == 'FALL' else 0
    confidence = 'LOW'
    if (direction in ('RISE', 'FALL') and len(rows) >= 20 and aligned >= 3
            and abs(net) >= 40 and cross_features['aligned_indices'] >= 3):
        confidence = 'MODERATE — UNCALIBRATED'
    elif (direction == 'RANGE' and len(rows) >= 20 and volatility_ratio is not None
          and volatility_ratio < 0.70 and cross_features['aligned_indices'] >= 3):
        confidence = 'MODERATE — UNCALIBRATED'

    reasons = price_reasons + future_reasons + option_reasons
    if cross_features['aligned_indices']:
        reasons.append(f"Same-session cross-index context: {cross_features['positive_indices']} up, {cross_features['negative_indices']} down, out of {cross_features['aligned_indices']} aligned peers.")
    if direction == 'RANGE':
        reasons.append(f'Volatility compression ({volatility_ratio:.2f}× baseline) with weak net directional pressure.')
    warnings = price_warnings + future_warnings + option_warnings
    if len(rows) < 20:
        warnings.append('At least 20 index sessions are required before the model will classify rise, fall, or range.')
    if cross_features['aligned_indices'] < 3:
        warnings.append('Cross-index confirmation is thin for this date.')
    if confidence != 'MODERATE — UNCALIBRATED':
        warnings.append('Forecast confidence is low; this rule score has not been probability-calibrated.')
    warnings.append('EOD evidence cannot identify institutions or guarantee next-session direction.')
    target_threshold = max(0.10, 0.20 * atr_pct) if atr_pct is not None else None
    range_cap = 1.10 * atr_pct if atr_pct is not None else None
    return {'model_version': MODEL_VERSION, 'underlying': symbol, 'exchange': item['exchange'],
            'name': item['name'], 'as_of': day, 'direction': direction, 'confidence': confidence,
            'bullish_score': round(bullish, 2), 'bearish_score': round(bearish, 2),
            'net_score': round(net, 2), 'scores': scores,
            'features': {'price': price_features, 'futures': future_features,
                         'options': option_features, 'cross_index': cross_features,
                         'outcome_threshold_pct': target_threshold, 'range_cap_pct': range_cap},
            'reasons': reasons, 'warnings': list(dict.fromkeys(warnings))}


def actual_outcome(previous, following, prediction):
    threshold = prediction.get('features', {}).get('outcome_threshold_pct')
    atr_pct = prediction.get('features', {}).get('price', {}).get('atr_pct')
    if not threshold or not atr_pct or previous['close'] <= 0:
        return None
    close_change = _pct(following['close'], previous['close'])
    range_pct = (following['high'] - following['low']) / previous['close'] * 100
    if close_change >= threshold:
        label = 'RISE'
    elif close_change <= -threshold:
        label = 'FALL'
    elif range_pct <= prediction['features']['range_cap_pct']:
        label = 'RANGE'
    else:
        label = 'VOLATILE'
    return {'label': label, 'close_change_pct': round(close_change, 4),
            'range_pct': round(range_pct, 4), 'threshold_pct': threshold}


def _metrics(rows):
    actionable = [row for row in rows if row['prediction'] in ('RISE', 'FALL', 'RANGE')]
    hits = sum(row['prediction'] == row['actual'] for row in actionable)
    directional = [row for row in actionable if row['prediction'] in ('RISE', 'FALL')]
    directional_hits = sum(row['prediction'] == row['actual'] for row in directional)
    actuals = [row['actual'] for row in rows]
    majority = max((actuals.count(label) for label in ('RISE', 'FALL', 'RANGE', 'VOLATILE')), default=0)
    return {'observations': len(rows), 'actionable': len(actionable),
            'coverage_pct': round(len(actionable) / len(rows) * 100, 1) if rows else None,
            'three_state_accuracy_pct': round(hits / len(actionable) * 100, 1) if actionable else None,
            'directional_calls': len(directional),
            'directional_hit_rate_pct': round(directional_hits / len(directional) * 100, 1) if directional else None,
            'majority_class_baseline_pct': round(majority / len(actuals) * 100, 1) if actuals else None,
            'too_small_to_interpret': len(directional) < 30}


def evaluate_history(c, item, end_date, universe=None):
    """Chronological replay using only data dated through each signal close."""
    universe = universe or UNIVERSE
    bars = _cash_history(c, item['underlying'], end_date)
    replay = []
    for index in range(5, len(bars) - 1):
        signal_bar = bars[index]
        next_bar = bars[index + 1]
        prediction = forecast(c, item, signal_bar['day'], universe)
        outcome = actual_outcome(signal_bar, next_bar, prediction)
        if not outcome:
            continue
        replay.append({'day': signal_bar['day'], 'prediction': prediction['direction'],
                       'actual': outcome['label'], 'return_pct': outcome['close_change_pct'],
                       'range_pct': outcome['range_pct']})
    count = len(replay)
    train_end = int(count * 0.60)
    validation_end = int(count * 0.80)
    splits = [
        {'period': 'Earlier 60%', **_metrics(replay[:train_end])},
        {'period': 'Next 20%', **_metrics(replay[train_end:validation_end])},
        {'period': 'Latest 20% · holdout', **_metrics(replay[validation_end:])},
        {'period': 'All history', **_metrics(replay)},
    ]
    return {'underlying': item['underlying'], 'name': item['name'], 'model_version': MODEL_VERSION,
            'as_of': end_date, 'history_sessions': len(bars), 'replay_observations': count,
            'splits': splits, 'label_definition': 'Rise/Fall require a close move beyond 0.20× prior ATR14 (minimum 0.10%); Range requires a smaller close move and next-session range no wider than 1.10× prior ATR14; wider low-net moves are Volatile.',
            'replay_note': 'Rules are fixed and probabilities are not calibrated. The latest 20% is a chronological diagnostic, not proof of future profitability.'}
