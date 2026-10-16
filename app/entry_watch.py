"""The same original-price checks for operational and observed entries."""
import math
from .live_price import validate_point
from .market import DataError


def check_live_entry(signal, point, now):
    validate_point(point, asset=signal['asset'], identity=signal['source_identity'], now=now)
    reference = (signal.get('live_observation') or {}).get('price')
    if reference is None:
        reference = signal['evidence']['candles'][-1]['c']
    average = signal.get('manual_atr', signal['entry_validation']['atr'])
    if type(average) not in (int, float) or not math.isfinite(average) or average <= 0:
        raise DataError('ATR original indisponível.', 'invalid_data')
    earliest=(signal.get('live_observation') or {}).get('at',(signal.get('setup_contract') or {}).get('confirmed_at',0))
    if point.at < earliest:
        raise DataError('Cotação anterior à decisão original.', 'live_price_before_signal')
    sign = 1 if signal['direction'] == 'CALL' else -1
    contract = signal.get('setup_contract') or {}
    geometry = contract.get('geometry') or {}
    audit = {'live_revalidation': point.json(), 'live_displacement_atr': sign*(point.price-reference)/average}
    # A published book entry must not regain validity after its frozen boundary
    # was lost. This deliberately uses the current observed price.
    boundary = geometry.get('invalidation')
    if boundary is not None and sign*(point.price-boundary) < 0:
        return audit, 'Cotação perdeu o limite original de invalidação do setup.'
    displacement = audit['live_displacement_atr']
    if displacement < -.35 or displacement > .75:
        return audit, 'Preço observado se afastou do gatilho original.'
    if contract.get('entry_kind') in ('live_retest', 'live_retracement'):
        if abs(point.price-geometry['level']) > .40*average:
            return audit, 'Preço não permanece na região original de retorno.'
    target = geometry.get('target')
    if target is not None and sign*(target-point.price) < .5*average:
        return audit, 'Alvo original já não deixa espaço para a entrada.'
    for level in geometry.get('interference_levels', []):
        if sign*(level-point.price) < .5*average:
            return audit, 'Cotação chegou à interferência original antes da entrada.'
    return audit, ''
