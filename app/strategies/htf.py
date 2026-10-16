"""Prepared H1 confirmation. Production activation remains gated in Config."""
import asyncio
import time
from ..market import validate, DataError
from .helpers import ema_series, closes

async def confirm(provider, asset, direction, cutoff, identity, timeout=3):
    started = time.monotonic()
    try:
        rows = await asyncio.wait_for(provider.candles(asset, 3600, 80), timeout)
        actual = getattr(rows, 'source_identity', getattr(provider, 'identity', provider.name))
        if actual != identity:
            raise DataError('Identidade da fonte H1 divergente.', 'source_mismatch')
        # Nothing after the M5 decision cutoff may confirm this setup.
        rows = [c for c in rows if c.t <= cutoff]
        closed = validate(rows, cutoff, max_age=3930, interval=3600)
        values = ema_series(closes(closed), 21)
        delta = values[-1] - values[-2]
        passed = delta > 0 if direction == 'CALL' else delta < 0
        result = {'status': 'confirmed' if passed else 'rejected',
                  'detail': 'EMA21 H1 alinhada.' if passed else 'EMA21 H1 sem alinhamento.',
                  'source_identity': actual, 'cutoff': cutoff, 'ema21_delta': delta,
                  'candles': [c.json() for c in closed]}
    except (DataError, asyncio.TimeoutError) as error:
        result = {'status': 'unavailable', 'detail': 'Confluência H1 indisponível; sinal sem filtro H1.',
                  'code': getattr(error, 'code', 'timeout')}
    except Exception:
        result = {'status': 'unavailable', 'detail': 'Confluência H1 indisponível; sinal sem filtro H1.', 'code': 'provider_error'}
    result['elapsed_ms'] = round((time.monotonic()-started)*1000, 2)
    return result
