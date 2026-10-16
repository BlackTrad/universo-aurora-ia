"""Causal technical guards. Thresholds are heuristics, not win probabilities."""
from .regime import classify
from ..market import DataError, valid_candle

FAMILIES = {
    'ema_pullback':'trend', 'ema_macd':'trend', 'heikin_ashi':'trend', 'ichimoku':'trend',
    'rsi_divergence':'oscillator', 'stochastic':'oscillator', 'cci':'oscillator',
    'bb_reversion':'rejection', 'range_pivot':'rejection', 'candlestick_sr':'rejection',
    'consolidation':'structure', 'fibonacci':'structure',
    'sr_volume_breakout':'participation', 'vwap':'participation',
}
from ..manual_book import CATALOG
FAMILIES.update({key:spec[2] for key,spec in CATALOG.items()})

def evidence_groups(candidates):
    """Related engines are one family, not independent votes or probabilities."""
    families={}
    duplicates={}
    for c in candidates:
        key=c['strategy_resolved'];family=FAMILIES.get(key,key)
        families.setdefault(family,[]).append(key)
    return {'families':[{'family':k,'engines':sorted(set(v))} for k,v in sorted(families.items())],
            'family_count':len(families),'independent_confirmation':False,
            'same_ob_wrappers':[{'direction':k[0],'setup_candle':k[1],'engines':v}
                                for k,v in duplicates.items() if len(v)>1],
            'note':'Famílias e wrappers relacionados não somam votos nem probabilidade.'}

def selection_guard(candles, candidates):
    families=sorted({FAMILIES.get(c['strategy_resolved'], c['strategy_resolved']) for c in candidates})
    regime=classify(candles)
    audit={'families':families, 'regime':regime,
           'evidence_groups':evidence_groups(candidates)}
    for candidate in candidates:
        if candidate.get('manual') and (not candidate.get('mandatory_criteria') or
                any(c.get('passed') is not True for c in candidate['mandatory_criteria'])):
            return audit,'Critério obrigatório do manual não confirmado.'
    return audit, ''

def entry_guard(signal, rows, now):
    """Use only completed M1 candles from the original feed before scheduled entry."""
    if now>=signal['entry_at']-15:
        raise DataError('Revalidação não concluída com antecedência suficiente.', 'entry_deadline')
    if (not rows or any(not valid_candle(c) or not isinstance(c.t,int) or isinstance(c.t,bool)
                       or c.t<=0 or c.t%60 or c.t>now for c in rows)
            or any(b.t-a.t!=60 for a,b in zip(rows,rows[1:]))):
        raise DataError('Candles M1 inválidos na revalidação.', 'invalid_data')
    closed=[c for c in rows if c.t+60<=now]
    if not closed or closed[-1].t+60!=int(now//60)*60:
        raise DataError('Última vela M1 fechada indisponível.', 'missing_latest_candle')
    if len(closed)>=3 and all(c.h==c.l for c in closed[-3:]):
        raise DataError('Cotações recentes sem variação.', 'invalid_data')
    reference=(signal['live_observation']['price'] if signal.get('manual_intrabar')
               else signal['evidence']['candles'][-1]['c'])
    volatility=signal['entry_validation']['atr']
    if volatility<=0:
        raise DataError('ATR de referência indisponível.', 'invalid_data')
    last=closed[-1]
    sign=1 if signal['direction']=='CALL' else -1
    displacement=sign*(last.c-reference)/volatility
    audit={'checked_at':now, 'candle_at':last.t, 'price':last.c,
           'reference_price':reference, 'atr':volatility, 'displacement_atr':round(displacement,4)}
    from ..setup_contract import check_setup
    setup_audit,setup_reason=check_setup(signal,closed,now)
    audit.update(setup_audit)
    if setup_reason:return audit,setup_reason
    if displacement<-.35:
        return audit, 'Preço avançou contra o setup antes da entrada.'
    if displacement>.75:
        return audit, 'Preço já se afastou da entrada; evitar perseguir o movimento.'
    if sign*(last.c-last.o)<-.35*volatility:
        return audit, 'Última vela M1 fechou com impulso contrário ao setup.'
    return audit, ''
