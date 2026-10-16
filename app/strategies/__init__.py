"""Manual Price Action registry for binary-option signals; no SMC engine."""
from ..logging_setup import event
from ..manual_book import CATALOG, VERSION, RULES, evaluate as manual_evaluate, evaluate_all
from ..manual_price_action import structural_context
from . import apex_engines as apex
from .ema_pullback import evaluate as evaluate_ema_pullback
from .ema_macd import evaluate as evaluate_ema_macd
from .bb_reversion import evaluate as evaluate_bb_reversion
from .range_pivot import evaluate as evaluate_range_pivot
from .auto import evaluate as evaluate_auto
from .quality import selection_guard

RETIRED_KEYS = frozenset(('order_block','ob_fvg','fvg_fill','liquidity_grab','sweep_structure'))
ACTIVE_KEYS = (*CATALOG, 'fibonacci')
STRATEGIES = {key:(lambda rows,key=key:manual_evaluate(key,rows)) for key in CATALOG}
DEFAULT_MANUAL_EVALUATORS = dict(STRATEGIES)
# Non-SMC standalone tools remain available to local integrations.
# The automatic operational pool is explicitly the manual plus Fibonacci.
STRATEGIES.update(ema_pullback=evaluate_ema_pullback,ema_macd=evaluate_ema_macd,
                  bb_reversion=evaluate_bb_reversion,range_pivot=evaluate_range_pivot,
                  auto=evaluate_auto,rsi_divergence=apex.rsi_divergence,
                  stochastic=apex.stochastic,cci=apex.cci,consolidation=apex.consolidation,
                  ichimoku=apex.ichimoku,fibonacci=apex.fibonacci,heikin_ashi=apex.heikin_ashi,
                  candlestick_sr=apex.candlestick_sr,sr_volume_breakout=apex.sr_volume_breakout,vwap=apex.vwap)
SELECTOR_VERSION = VERSION
SPECIFICITY = {key:index for index,key in enumerate(CATALOG)}
SPECIFICITY.update(pa_engulfing_three=45,pa_head_shoulders=50,pa_double_extreme=49,
                   pa_breakout_90=42,pa_breakout_50=41,fibonacci=-1)


def eligible(candidate,minimum):
    if candidate.get('manual'):
        criteria=candidate.get('mandatory_criteria')
        return bool(criteria) and all(c.get('passed') is True for c in criteria)
    return candidate.get('score',0) >= minimum


def collect_candidates(candles):
    candidates=[];evaluations=[]
    bulk_error=None
    bulk_keys=[key for key in CATALOG if STRATEGIES.get(key) is DEFAULT_MANUAL_EVALUATORS[key]]
    try:manual=evaluate_all(candles,keys=bulk_keys)
    except Exception as exc:
        manual={};bulk_error=type(exc).__name__
        event('strategy.failed',strategy='manual_price_action',status='error',exc_info=True)
    for key in ACTIVE_KEYS:
        if key not in STRATEGIES:continue
        if key in CATALOG and bulk_error:
            evaluations.append({'strategy':key,'candidate':None,'qualified':False,'status':'error',
                                'reason':'Motor indisponível por falha interna.','error_type':bulk_error})
            continue
        try:
            candidate,reason=manual[key] if key in manual else STRATEGIES[key](candles)
            if candidate and not candidate.get('setup_contract'):
                from ..setup_contract import capture_setup
                interval=candles[-1].t-candles[-2].t
                candidate=capture_setup(key,candidate,candles,interval)
            if candidate and key=='fibonacci':
                from ..manual_book import fibonacci_context
                candidate,reason=fibonacci_context(candidate,candles)
        except Exception as exc:
            evaluations.append({'strategy':key,'candidate':None,'qualified':False,'status':'error',
                                'reason':'Motor indisponível por falha interna.','error_type':type(exc).__name__})
            event('strategy.failed',strategy=key,status='error',exc_info=True)
            continue
        if candidate:candidate={**candidate,'strategy_resolved':key,
                                'regime':structural_context(candles[-RULES.lookback:])['regime']}
        from ..tracing import score_composition
        evaluations.append({'strategy':key,'candidate':candidate,'reason':reason,
                            'qualified':bool(candidate and eligible(candidate,70)),
                            'score_composition':score_composition(candidate) if candidate else None})
        event('strategy.evaluated',strategy=key,status='candidate' if candidate else 'rejected',reason=reason,candidate=candidate)
        if candidate:candidates.append(candidate)
    return candidates,[],evaluations


def _same_entry(a,b,average):
    if a['direction'] != b['direction'] or a.get('setup_candle') != b.get('setup_candle'):return False
    def price(c):
        values=(c.get('setup_contract') or {}).get('levels') or {}
        return values.get('manual_region',values.get('frozen_fibonacci_level'))
    pa,pb=price(a),price(b)
    return pa is not None and pb is not None and abs(pa-pb) <= .30*average


def select_reference(candles,candidates,*,learned=None,context=None,scope=None,strict=False,unavailable=(),minimum=70):
    pool=[c for c in candidates if c.get('strategy_resolved') not in RETIRED_KEYS and eligible(c,minimum)]
    if not pool:
        if any(c.get('manual') and not eligible(c,minimum) for c in candidates):
            return None,'Critérios obrigatórios do manual incompletos; nenhuma entrada autorizada.'
        return None,'Nenhuma estratégia do manual ou Fibonacci encontrou uma sequência confirmada.'
    if len({c['direction'] for c in pool}) > 1:
        return None,'Padrões elegíveis apontam CALL e PUT; conflito direcional bloqueado.'
    quality,reason=selection_guard(candles,pool)
    if reason:return None,reason
    from .helpers import atr
    average=atr(candles[:-1]) if candles else None
    if len(pool)>1 and (not average or not all(_same_entry(pool[0],c,average) for c in pool[1:])):
        return None,'Mais de uma região de entrada elegível; ambiguidade bloqueada.'
    # Most descriptive variant of the SAME frozen entry; no score/rate ranking.
    variants_pool=[c for c in pool if c.get('manual')] or pool
    ordered=sorted(variants_pool,key=lambda c:SPECIFICITY.get(c['strategy_resolved'],-2),reverse=True)
    chosen=ordered[0]
    if learned and context:
        from ..learning import choose_tie
        chosen=choose_tie(ordered,learned,context,scope=scope)
        if chosen.get('learning',{}).get('blocked'):
            return None,'Treino e validação do mesmo contexto vetaram as variações desta entrada.'
    variants=[c['strategy_resolved'] for c in pool]
    quality.update(variants=variants,independent_confirmation=False,
                   selection_basis='specificity_of_same_frozen_entry')
    return {**chosen,'agreement_strategies':variants,'quality':quality,
            'learning':chosen.get('learning') or {'applied':False,'preference_bonus':0,'selection_score':chosen['score'],
                        'reason':'Seleção por regras e região; nenhum bônus de histórico altera o padrão.'}},''


def scan_all(candles,*,learned=None,context=None,scope=None,strict=False,audit=None,minimum=70):
    candidates,unavailable,evaluations=collect_candidates(candles)
    errors=[e for e in evaluations if e.get('status')=='error']
    if errors:result,reason=None,'Comparação incompleta: falha em motor; nenhuma entrada autorizada.'
    else:result,reason=select_reference(candles,candidates,learned=learned,context=context,scope=scope,strict=strict,minimum=minimum)
    if audit is not None:
        audit.update(selector='manual_price_action_ob',selector_version=VERSION,evaluations=evaluations,
                     candidates=candidates,selected=result,reason=reason,pool_complete=not errors,engine_errors=errors,
                     configured_minimum=minimum,manual_requires_mandatory_criteria=True,
                     eligible_candidates=[c for c in candidates if eligible(c,minimum)],
                     eligibility_rejections=[{'strategy':c['strategy_resolved'],'reason':'mandatory_criteria_failed' if c.get('manual') else 'below_configured_minimum'}
                                             for c in candidates if not eligible(c,minimum)],
                     execution_order=list(ACTIVE_KEYS),priority='same-entry variant specificity; no cross-entry ranking',
                     conflict_policy='opposing eligible direction or distinct entry regions reject all',
                     independent_confirmation=False,smc_active=False)
    return result,reason


STRATEGIES['scan_all']=scan_all
