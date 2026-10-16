"""Outcome feedback; never infer which technical criterion caused a loss."""
from copy import deepcopy
from .learning import (MIN_TRAIN, MIN_VALIDATION, MAX_LEARNING_BONUS, PROFILE_VERSION,
                       operational_records, profile_key)
from .outcome_evidence import comparable_outcome

FEEDBACK_VERSION='1.26.5-feedback-1'


def state(learned,now,last_feedback=None):
    enabled=sum(bool(p['eligible']) for p in learned.values())
    blocked=sum(bool(p.get('blocked')) for p in learned.values())
    return {'status':'preferência empírica disponível' if enabled else
            'vetos por histórico disponíveis' if blocked else 'coletando resultados',
            'evaluated_at':now,'eligible_profiles':enabled,'blocked_profiles':blocked,
            'max_context_sample':max((p['sample'] for p in learned.values()),default=0),
            'profiles':[{'context':list(key),**value} for key,value in learned.items()],
            'minimum_train':MIN_TRAIN,'minimum_validation':MIN_VALIDATION,
            'maximum_bonus':MAX_LEARNING_BONUS,'profile_version':PROFILE_VERSION,
            'source':'sinais emitidos','last_feedback':last_feedback}


def feedback(signal,learned,now):
    evidence=signal.get('evidence') or {}
    trace=evidence.get('decision_trace') or {}
    row=next(iter(operational_records([signal])),None)
    profile=learned.get(profile_key(row)) if row else None
    valid=comparable_outcome(signal,now)
    training_result=valid and signal.get('result') in ('WIN','LOSS')
    included=bool(training_result and profile and profile.get('pipeline_compatible'))
    used=bool(included and signal['id'] in profile.get('train_ids',[])+profile.get('validation_ids',[]))
    return {'version':FEEDBACK_VERSION,'signal_id':signal['id'],'known_at':now,
        'result_known_at':signal.get('settled_at'),'asset':signal.get('asset'),
        'source_key':signal.get('source_key'),'source_identity':signal.get('source_identity'),
        'timeframe':signal.get('timeframe'),'duration_seconds':signal.get('duration_seconds'),
        'strategy':signal.get('strategy_resolved',signal.get('strategy')),'direction':signal.get('direction'),
        'regime':evidence.get('regime',trace.get('regime')),'result':signal.get('result'),
        'result_kind':'indicativo','broker_confirmed':False,
        'entry_price':signal.get('entry_price'),'expiry_price':signal.get('expiry_price'),
        'configuration_id':(evidence.get('configuration') or {}).get('id'),
        'code_version':(evidence.get('configuration') or {}).get('code_version'),
        'rules_version':evidence.get('rules_version'),'pipeline_version':evidence.get('pipeline_version'),
        'profile_version':PROFILE_VERSION,'setup_variant':signal.get('apex_setup') or 'base',
        'original_reasons':deepcopy(signal.get('reasons',[])),
        'original_score_composition':deepcopy(trace.get('score_composition')),
        'original_mandatory_criteria':deepcopy(trace.get('mandatory_criteria',[])),
        'original_filters':deepcopy(trace.get('filters',{})),
        'valid_quote_outcome':valid,'binary_training_result':training_result,
        'context_compatible':included,'used_in_training_or_validation':used,
        'context_sample':profile.get('sample',0) if profile else 0,
        'train_sample':profile.get('train_sample',0) if profile else 0,
        'validation_sample':profile.get('validation_sample',0) if profile else 0,
        'preference_enabled':bool(included and profile.get('eligible')),
        'veto_enabled':bool(included and profile.get('blocked')),
        'interpretation':'Desfecho contrário à direção na expiração; causa técnica não comprovada.' if signal.get('result')=='LOSS' else
            'Desfecho favorável na expiração; não comprova eficácia isoladamente.' if signal.get('result')=='WIN' else
            'Empate, cancelamento ou ausência de cotação não contam como vitória ou derrota no aprendizado.',
        'rule_change_applied':False,'minimum_train':MIN_TRAIN,'minimum_validation':MIN_VALIDATION}
