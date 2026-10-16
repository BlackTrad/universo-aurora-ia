"""Causal, versioned empirical tie-breaking; never predicts a win probability."""
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from .outcome_evidence import comparable_outcome, research_outcome

RULES_VERSION = '1.26.13-book-1'
PIPELINE_VERSION = '1.26.13-book-ob'
PROFILE_VERSION = '1.26.13-book-ob-1'
MIN_TRAIN = 60
MIN_VALIDATION = 30
MAX_PROFILE_AGE = 30 * 86400


def profile_key(row):
    """Never transfer a preference across configuration, regime or setup variant."""
    return (row.get('mode'), row.get('source_identity'), row.get('asset'), row.get('timeframe'),
            row.get('duration_seconds'), row.get('direction'), row.get('strategy'),
            row.get('pipeline_version', 'não informada'), row.get('source_key'),
            row.get('regime'), row.get('configuration_id'), row.get('apex_setup') or 'base',
            row.get('duration_basis','indicative_planned'))


def selection_profile_key(context, candidate, scope, mode='operacional'):
    if not scope:
        return None
    identity, asset, timeframe, duration = context
    return profile_key({**scope, 'mode': mode, 'source_identity': identity, 'asset': asset,
                        'timeframe': timeframe, 'duration_seconds': duration,
                        'direction': candidate['direction'], 'strategy': candidate['strategy_resolved'],
                        'apex_setup': candidate.get('apex_setup'), 'pipeline_version': PIPELINE_VERSION,
                        'duration_basis':'intrabar_planned' if candidate.get('manual_intrabar') else 'indicative_planned'})


def lower_bound(wins, count):
    if not count:
        return 0.0
    z = 1.96
    p = wins / count
    return (p + z*z/(2*count) - z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1+z*z/count)


def profiles(records, now):
    """Split each strategy chronologically after excluding overlapping intervals.

    Non-overlap is a sampling safeguard, not proof of statistical independence.
    Older rule versions, future settlements and missing quote evidence are excluded.
    """
    groups = defaultdict(list)
    for row in records:
        if row.get('rules_version') != RULES_VERSION or row.get('result') not in ('WIN', 'LOSS'):
            continue
        numeric = ('entry_at', 'expiry_at', 'settled_at', 'entry_price', 'expiry_price')
        if any(not isinstance(row.get(k), (int, float)) or isinstance(row.get(k), bool) or not math.isfinite(row[k]) for k in numeric):
            continue
        if row['settled_at'] > now or not 0 < row['entry_at'] < row['expiry_at'] <= row['settled_at']:
            continue
        if any(not isinstance(row.get(k), str) or not row[k] for k in ('mode','source_identity','asset','timeframe','strategy')):
            continue
        if any(row.get(k) is not None and not isinstance(row[k], str)
               for k in ('source_key', 'regime', 'configuration_id', 'apex_setup')):
            continue
        if (row.get('direction') not in ('CALL', 'PUT') or row['entry_price'] <= 0 or row['expiry_price'] <= 0
                or row['expiry_at']-row['entry_at'] != row.get('duration_seconds')
                or row['entry_price'] == row['expiry_price']):
            continue
        expected = 'WIN' if (row['expiry_price'] > row['entry_price']) == (row['direction'] == 'CALL') else 'LOSS'
        if row['result'] != expected:
            continue
        proof = research_outcome(row, now) if row.get('mode') == 'observacao' else comparable_outcome(row, now)
        if not proof:
            continue
        key = profile_key(row)
        groups[key].append(row)
    result = {}
    for key, rows in groups.items():
        selected = []
        last_end = 0
        for row in sorted(rows, key=lambda r: (r['entry_at'], r['expiry_at'])):
            if row['entry_at'] < last_end:
                continue
            selected.append(row)
            last_end = max(row['expiry_at'], row['settled_at'])
        sample = selected[-(MIN_TRAIN + MIN_VALIDATION):]
        train = sample[:MIN_TRAIN] if len(sample) >= MIN_TRAIN + MIN_VALIDATION else []
        # A training outcome must already be known before validation begins.
        validation = sample[MIN_TRAIN:] if train else []
        if validation:
            train = [r for r in train if r['settled_at'] <= validation[0]['entry_at']]
        train_bound = lower_bound(sum(r['result'] == 'WIN' for r in train), len(train))
        validation_bound = lower_bound(sum(r['result'] == 'WIN' for r in validation), len(validation))
        train_upper = 1-lower_bound(sum(r['result']=='LOSS' for r in train),len(train))
        validation_upper = 1-lower_bound(sum(r['result']=='LOSS' for r in validation),len(validation))
        recent = now-max(r['settled_at'] for r in selected) <= MAX_PROFILE_AGE
        complete = bool(key[8] and key[9] in ('tendencial', 'lateral', 'transicao') and key[10])
        compatible = key[7] == PIPELINE_VERSION and complete
        eligible = compatible and recent and len(train) >= MIN_TRAIN and len(validation) >= MIN_VALIDATION and min(train_bound, validation_bound) > .5
        blocked = compatible and recent and len(train)>=MIN_TRAIN and len(validation)>=MIN_VALIDATION and max(train_upper,validation_upper)<.5
        result[key] = {'mode': key[0], 'profile_version': PROFILE_VERSION, 'sample': len(selected), 'train_sample': len(train),
                       'validation_sample': len(validation), 'eligible': eligible, 'recent':recent, 'blocked':blocked,
                       'train_upper_bound':train_upper, 'validation_upper_bound':validation_upper,
                       'validation_win_rate_pct': round(100*sum(r['result']=='WIN' for r in validation)/len(validation), 2) if validation else None,
                       'validation_lower_bound': validation_bound, 'rules_version': RULES_VERSION,
                       'last_settled_at': max(r['settled_at'] for r in selected),
                       'train_ids': [r.get('id') for r in train], 'validation_ids': [r.get('id') for r in validation],
                       'excluded_overlap': len(rows)-len(selected),
                       'train_start': train[0]['entry_at'] if train else None,
                       'validation_start': validation[0]['entry_at'] if validation else None,
                       'pipeline_version': key[7], 'pipeline_compatible':compatible, 'context_complete':complete,
                       'source_key':key[8], 'regime':key[9], 'configuration_id':key[10], 'apex_setup':key[11],
                       'quote_evidence_verified':True,
                       'reason': 'Desempate empírico habilitado; não comprova rentabilidade.' if eligible else 'Amostra, confirmação temporal ou atualização insuficiente; preferência técnica mantida.'}
    return result


def operational_records(signals):
    for s in signals:
        if s.get('state') == 'cancelado' or s.get('settlement_pending', True):
            continue
        evidence = s.get('evidence', {})
        configuration = evidence.get('configuration') or {}
        yield {**s, 'mode': 'operacional', 'strategy': s.get('strategy_resolved', s.get('strategy')),
               'rules_version': evidence.get('rules_version'),
               'pipeline_version': evidence.get('pipeline_version', 'não informada'),
               'regime':evidence.get('regime', (evidence.get('decision_trace') or {}).get('regime', s.get('regime'))),
               'configuration_id':configuration.get('id')}


def study_records(path):
    """Read an existing study only; never create a DB or mix its source with live history."""
    if not Path(path).is_file():
        return []
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        records = []
        for item in db.execute("SELECT * FROM observations WHERE result IN ('WIN','LOSS')"):
            row = dict(item)
            evidence = json.loads(row['evidence'])
            if not evidence.get('source_identity'):
                continue
            records.append({**row, 'mode': 'estudo', 'source_identity': evidence['source_identity'],
                            'timeframe': 'M5', 'duration_seconds': 300,
                            'rules_version': evidence.get('rules_version')})
        return records
    finally:
        db.close()


MAX_LEARNING_BONUS = 3.0


def choose_tie(candidates, learned, context, *, scope=None):
    """Bounded preference within one technical priority band; keep raw score intact."""
    ranked=[]
    for candidate in candidates:
        profile=learned.get(selection_profile_key(context, candidate, scope))
        bonus=0.0
        if profile and profile.get('eligible'):
            bonus=round(MAX_LEARNING_BONUS*max(0,min(1,2*(profile['validation_lower_bound']-.5))),4)
        ranked.append((candidate['score']+bonus,candidate,profile,bonus))
    available=[item for item in ranked if not (item[2] and item[2].get('blocked'))]
    _,best,profile,bonus=max(available or ranked,key=lambda item:item[0])
    audit=[{'strategy':c['strategy_resolved'],'technical_score':c['score'],'preference_bonus':b,'selection_score':value,
            'blocked':bool(p and p.get('blocked'))}
           for value,c,p,b in ranked]
    learning={'applied':bool(bonus) or len(available)!=len(ranked), 'blocked':not bool(available),
              'scope':'mesma fonte, ativo, período, duração, direção, regime, setup, configuração e pipeline; bônus limitado a 3 pontos',
              'profile_version':PROFILE_VERSION, 'context':scope,
              'preference_bonus':bonus,'selection_score':best['score']+bonus,'candidates':audit,
              'reason':('Preferência por histórico operacional elegível; pontuação técnica preservada.' if bonus else
                        'Veto por treino e validação negativos.' if len(available)!=len(ranked) else
                        'Seleção técnica; nenhum bônus aplicado ao candidato escolhido.')}
    if bonus:learning={**profile,**learning}
    return {**best,'learning':learning}
