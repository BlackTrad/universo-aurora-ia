"""Read-only chart annotations from the signal's original decision geometry."""
import math

from .market import Candle, valid_candle
from .timeframes import TIMEFRAMES

LABELS = {
    'frozen_ema21': 'EMA21 original', 'frozen_bollinger_boundary': 'Banda original',
    'original_region_boundary': 'Região técnica', 'second_divergence_pivot': 'Pivô original',
    'frozen_fibonacci_level': 'Fibonacci', 'manual_region':'Região do manual',
    'frozen_cloud_boundary': 'Nuvem original', 'frozen_vwap21': 'VWAP original',
    'confirmation_extreme': 'Limite da confirmação',
}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def annotations(signal):
    """Never infer a region from current chart candles or manufacture an entry price."""
    entry, expiry = signal.get('entry_at'), signal.get('expiry_at')
    if (not number(entry) or not number(expiry) or entry % 60 or expiry % 60
            or expiry <= entry or expiry - entry != signal.get('duration_seconds')
            or signal.get('direction') not in ('CALL', 'PUT')
            or signal.get('source_key') not in ('deriv', 'binance', 'coinbase')
            or not isinstance(signal.get('source_identity'),str) or not signal['source_identity']
            or not isinstance(signal.get('asset'),str) or signal.get('timeframe') not in TIMEFRAMES):
        return None
    contract = signal.get('setup_contract') or {}
    if not isinstance(contract, dict):
        contract = {}
    levels, zones = [], []
    original_levels = contract.get('levels') or {}
    if not isinstance(original_levels, dict):
        original_levels = {}
    ratio = contract.get('retracement_ratio')
    ratio = ratio if type(ratio) in (int, float) and ratio in (.5, .618, .786) else None
    for name, value in original_levels.items():
        if name not in LABELS or not number(value):
            continue
        label = f'Fibo {ratio * 100:g}%' if name == 'frozen_fibonacci_level' and ratio else LABELS[name]
        levels.append({'id': name, 'label': label, 'price': value, 'selected': True})
    # Extra Fibonacci lines require the exact original anchor candles and a
    # consistent frozen trigger. Old records lacking them show only their level.
    anchors = contract.get('anchors') or []
    if not isinstance(anchors,(list,tuple)):anchors=[]
    evidence = signal.get('evidence') or {}
    rows = (evidence.get('candles') or []) if isinstance(evidence,dict) else []
    if ratio and len(anchors) >= 2 and isinstance(rows, list):
        try:
            raw = [next(c for c in rows if c.get('t') == anchor) for anchor in anchors[:2]]
            bottom, top = [Candle(**{k: c.get(k) for k in ('t', 'o', 'h', 'l', 'c')}) for c in raw]
            confirmed=contract.get('confirmed_at')
            interval=TIMEFRAMES[signal['timeframe']]
            causal=number(confirmed) and all(type(c.t) is int and c.t>0 and c.t%interval==0
                                            and c.t+interval<=confirmed for c in (bottom,top))
            if causal and valid_candle(bottom) and valid_candle(top) and top.h > bottom.l:
                span = top.h - bottom.l
                up = signal['direction'] == 'CALL'
                selected = top.h - ratio * span if up else bottom.l + ratio * span
                frozen = original_levels.get('frozen_fibonacci_level')
                if number(frozen) and math.isclose(frozen, selected, rel_tol=1e-10, abs_tol=1e-10):
                    levels = [level for level in levels if level['id'] != 'frozen_fibonacci_level']
                    levels.extend({'id': f'fibonacci_{r}', 'label': f'Fibo {r * 100:g}%',
                                   'price': top.h - r * span if up else bottom.l + r * span,
                                   'selected': r == ratio} for r in (.5, .618, .786))
        except (StopIteration, TypeError, AttributeError):
            pass
    geometry = contract.get('geometry') or {}
    if isinstance(geometry, dict):
        region,start=geometry.get('region'),geometry.get('start_at')
        if (contract.get('engine','').startswith('pa_') and isinstance(region,list) and len(region)==2
                and all(number(p) for p in region) and region[0]<region[1] and number(start) and start<=entry):
            zones.append({'id':'manual_region','label':'Região original do manual','lower':region[0],
                          'upper':region[1],'start_at':start,'end_at':expiry})
    return {'signal_id': signal.get('id'), 'source_key': signal['source_key'], 'asset': signal['asset'],
            'source_identity': signal['source_identity'], 'timeframe': signal['timeframe'],
            'direction': signal['direction'], 'entry_at': entry, 'expiry_at': expiry,
            'cancelled': signal.get('state') == 'cancelado', 'levels': levels[:8], 'zones': zones[:2],
            'basis': 'original_decision_geometry', 'entry_price_inferred': False}
