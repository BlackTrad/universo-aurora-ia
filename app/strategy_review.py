"""Paged descriptive results by original context and frozen Fibonacci level."""
import json
import math

from .learning import lower_bound

MIN_SAMPLE = 30


def report(store, now, *, page=1, page_size=8, source='', ratio=''):
    if not 1 <= page <= 1000000 or not 1 <= page_size <= 20:
        raise ValueError('Página inválida.')
    where = ["p.scope='bot'", 'p.created<=?']
    args = [now]
    if source:
        where.append("json_extract(p.metadata,'$.source_key')=?")
        args.append(source)
    # A legacy payload can contain invalid historical numbers. Never let its
    # JSON invalidate this report or guess its level from a human explanation.
    raw = "CASE WHEN json_valid(s.payload) THEN s.payload ELSE '{}' END"
    level = f"CASE WHEN json_extract(p.metadata,'$.strategy')='fibonacci' AND json_type({raw},'$.setup_contract.retracement_ratio') IN ('integer','real') AND json_extract({raw},'$.setup_contract.retracement_ratio') IN (0.5,0.618,0.786) THEN json_extract({raw},'$.setup_contract.retracement_ratio') ELSE NULL END"
    if ratio:
        where.append("json_extract(p.metadata,'$.strategy')='fibonacci'")
        if ratio == 'unknown':
            where.append('(' + level + ') IS NULL')
        else:
            values = {'50': .5, '61.8': .618, '78.6': .786}
            if ratio not in values:
                raise ValueError('Nível inválido.')
            where.append('(' + level + ')=?')
            args.append(values[ratio])
    clause = ' AND '.join(where)
    base = f"SELECT p.group_id,p.metadata,p.outcome,p.price_verified,p.resolved_at,p.payload,({level}) AS retracement_ratio FROM metric_projection p JOIN signals s ON s.id=p.signal_id WHERE {clause}"
    # Only outcomes already known at the report cutoff can enter the rate.
    outcome = "CASE WHEN resolved_at>? THEN 'PENDENTE' ELSE outcome END"
    grouped = f"SELECT group_id,metadata,retracement_ratio,count(*) AS signals,sum(CASE WHEN price_verified=1 AND resolved_at<=? AND outcome='WIN' THEN 1 ELSE 0 END) AS wins,sum(CASE WHEN price_verified=1 AND resolved_at<=? AND outcome='LOSS' THEN 1 ELSE 0 END) AS losses,sum(CASE WHEN price_verified=1 AND resolved_at<=? AND outcome='EMPATE' THEN 1 ELSE 0 END) AS ties,sum(CASE WHEN ({outcome})='CANCELADO' THEN 1 ELSE 0 END) AS cancelled,sum(CASE WHEN ({outcome})='PENDENTE' THEN 1 ELSE 0 END) AS pending,sum(CASE WHEN outcome IN ('WIN','LOSS','EMPATE') AND price_verified=0 AND (resolved_at IS NULL OR resolved_at<=?) THEN 1 ELSE 0 END) AS unverified FROM ({base}) GROUP BY group_id,retracement_ratio"
    query_args = [now] * 6 + args
    with store.lock:
        total = store.db.execute('SELECT count(*) FROM (' + grouped + ')', query_args).fetchone()[0]
        actual = min(page, max(1, math.ceil(total / page_size)))
        rows = [dict(row) for row in store.db.execute(grouped + ' ORDER BY metadata,group_id,retracement_ratio LIMIT ? OFFSET ?',
                (*query_args, page_size, (actual - 1) * page_size))]
    items = []
    for row in rows:
        metadata = json.loads(row.pop('metadata'))
        sample = row['wins'] + row['losses']
        complete=all(isinstance(metadata.get(k),str) and metadata[k] not in ('','unknown','legacy_unknown','não informada')
                     for k in ('source_key','source_identity','strategy','asset','timeframe','direction','regime',
                               'configuration_id','rules_version','pipeline_version'))
        complete=complete and (metadata.get('strategy')!='fibonacci' or row['retracement_ratio'] is not None)
        rate = round(100 * row['wins'] / sample, 2) if sample >= MIN_SAMPLE and complete else None
        items.append({**metadata, **row, 'sample': sample, 'rate_pct': rate,
                      'rate': {'kind': 'historical_observed', 'basis': 'indicative_external',
                               'sample': sample, 'rate_pct': rate},
                      'wilson_95_pct': [round(100 * lower_bound(row['wins'], sample), 2),
                          round(100 * (1 - lower_bound(row['losses'], sample)), 2)] if sample else None,
                      'evidence_status': 'incomplete_context' if not complete else 'descriptive_only' if sample >= MIN_SAMPLE else 'insufficient',
                      'automatic_promotion': False})
    return {'at': now, 'items': items, 'total': total, 'page': actual, 'page_size': page_size,
            'minimum_sample': MIN_SAMPLE, 'basis': 'original_source_price_verified',
            'context_fields': ['source_identity', 'asset', 'timeframe', 'duration_seconds', 'direction',
                               'regime', 'configuration_id', 'rules_version', 'pipeline_version', 'retracement_ratio', 'duration_basis'],
            'priority': 'Price Action do manual; região, critérios obrigatórios e janela original; sem ranking por score',
            'policy': 'Resultados descritivos; comparação experimental e validação separada antes de mudar a seleção.',
            'automatic_promotion': False, 'operational_rules_changed': True, 'manual_catalog': __import__('app.manual_book',fromlist=['catalog']).catalog()}
