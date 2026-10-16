"""A predeclared, observation-only router. No provider, order or Telegram API."""
from copy import deepcopy
import math

VERSION = 'price-action-router-1.0.0'
SELECTOR = 'price_action_router'
LABELS = {
    'structure_pullback': 'Pullback estrutural',
    'breakout_retest': 'Rompimento e reteste',
    'range_reversal': 'Reversão na lateralização',
    'third_touch': 'Terceiro toque',
}
POLICY = {
    'version': VERSION, 'selector': SELECTOR, 'experimental': True,
    'operational_enabled': False, 'automatic_promotion': False,
    'timeframes': ['M1', 'M5'],
    'regime_method': 'pivôs 2+2 confirmados, disponíveis no corte',
    'routes': {
        'tendencial': ['structure_pullback', 'breakout_retest', 'third_touch'],
        'lateral': ['range_reversal', 'third_touch'],
        'transicao': ['breakout_retest'],
    },
    'trend_alignment_required': True,
    'third_touch_levels': {'tendencial': ['LTA', 'LTB'], 'lateral': ['suporte', 'resistência']},
    'conflict_policy': 'Direções opostas entre candidatos elegíveis rejeitam a decisão.',
    'multiple_setup_policy': 'Mais de um setup elegível rejeita a decisão; sem prioridade ou ranking por score.',
    'score_used_for_selection': False, 'independent_votes': False,
    'learning_used': False, 'payout_used_for_selection': False,
    'third_touch_timing': 'Próxima abertura original; nunca deslocar entrada para obter antecedência.',
}


def policy_snapshot():
    return deepcopy(POLICY)


def as_candidate(research):
    label = LABELS[research['strategy']]
    if research['strategy'] == 'third_touch':
        label += ' · ' + research['level_kind']
    return {**research, 'strategy_resolved': research['strategy'], 'apex_setup': label,
            'reasons': ['Sequência obrigatória e confirmação causal (80 pts)']}


def select(results, structural, *, now, interval, min_lead):
    """Reuse evaluated setups; mandatory gates precede conflict/ambiguity checks."""
    regime = structural.get('regime', 'transicao')
    planned_entry = math.ceil((now + min_lead) / 60) * 60
    checks=[]; eligible=[]
    for result in results:
        key=result['strategy']; code='eligible'; reason='Condições obrigatórias e contexto compatíveis.'
        entry=result.get('target_entry_at', planned_entry) if result.get('entry_policy') == 'next_analysis_candle' else planned_entry
        if not result.get('accepted'):
            code='setup_rejected'; reason=result.get('reason', 'Sem setup confirmado.')
        elif interval not in (60, 300):
            code='unsupported_timeframe'; reason='Disponível somente em M1/M5.'
        elif not result.get('mandatory_criteria') or any(not check.get('passed') for check in result['mandatory_criteria']):
            code='mandatory_failed'; reason='Critério obrigatório não confirmado.'
        elif entry - now < min_lead:
            code='insufficient_lead'; reason='A próxima abertura original não oferece antecedência após a confirmação fechada.'
        elif entry > result['valid_until']:
            code='setup_expired'; reason='Setup vencido antes da entrada planejada.'
        elif structural.get('status') != 'available':
            code='regime_unavailable'; reason='Estrutura confirmada insuficiente para rotear o setup.'
        elif key not in POLICY['routes'].get(regime, []):
            code='incompatible_regime'; reason='Setup incompatível com o regime estrutural deste seletor.'
        elif regime == 'tendencial' and result.get('direction') != structural.get('direction'):
            code='against_structure'; reason='Direção contrária à tendência estrutural confirmada.'
        elif key == 'third_touch' and result.get('level_kind') not in POLICY['third_touch_levels'].get(regime, []):
            code='incompatible_level'; reason='Tipo de marcação incompatível com o regime.'
        checks.append({'strategy':key, 'label':LABELS[key], 'technical_accepted':bool(result.get('accepted')),
                       'direction':result.get('direction'), 'eligible':code=='eligible', 'code':code,
                       'reason':reason, 'entry_at':entry, 'valid_until':result.get('valid_until')})
        if code=='eligible': eligible.append(as_candidate(result))
    choice=None
    if len({c['direction'] for c in eligible})>1:
        reason='CALL e PUT entre setups elegíveis; conflito bloqueado.'; code='direction_conflict'
    elif len(eligible)>1:
        reason='Mais de um setup elegível; sem vantagem validada para desempatar.'; code='multiple_setups'
    elif eligible:
        choice=eligible[0]; reason='Único setup válido para o regime e o horário planejado.'; code='selected'
    else:
        reason='Sem oportunidade válida neste seletor experimental.'; code='no_valid_opportunity'
    return {'selector':SELECTOR, 'candidate':choice, 'reason':reason, 'pool':eligible,
            'rules_version':choice['version'] if choice else VERSION,
            'regime':regime, 'regime_method':POLICY['regime_method'],
            'context_evidence':{'policy':policy_snapshot(), 'structure':structural, 'candidate_checks':checks,
                                'selection_code':code, 'decision_at':now, 'planned_entry_at':planned_entry,
                                'minimum_lead_seconds':min_lead, 'additional_api_calls':0}}


def study_summary(rows, protocol):
    """Counts only. Outcome rates remain separated in the combination table."""
    policy=protocol.get('price_action_selection')
    routed=[r for r in rows if r.get('selector')==SELECTOR]
    setups=[]
    for key,label in LABELS.items():
        observations=[r for r in rows if r.get('selector')=='price_action:'+key]
        setups.append({'strategy':key,'label':label,'evaluations':len(observations),
                       'detected':sum(bool(r.get('accepted')) for r in observations),
                       'rejected':sum(not r.get('accepted') for r in observations),
                       'cancelled':sum(r.get('state')=='cancelled' for r in observations),
                       'chosen':sum(r.get('accepted') and r.get('strategy')==key for r in routed)})
    latest=max(routed,key=lambda r:(r['created_at'],r['id']),default=None)
    return {'status':'experimental' if policy else 'legacy_session', 'policy':policy,
            'setups':setups, 'evaluations':len(routed),
            'selected':sum(bool(r.get('accepted')) for r in routed),
            'no_opportunity':sum(not r.get('accepted') for r in routed),
            'latest':{k:latest.get(k) for k in ('created_at','asset','source_key','timeframe','duration_seconds',
                                             'direction','strategy','reason','context_evidence','state','cancellation_reason')} if latest else None,
            'rates_scope':'Resultados por ativo, fonte, timeframe, expiração e versão na tabela de combinações.',
            'sent_to_telegram':False, 'automatic_promotion':False}
