"""Descriptive WIN ranking from compact projections, never candidate selection.

Assets/regimes/configurations may be pooled for counts within an explicitly
labelled source/timeframe/duration/rule-version group. This is not a forecast.
"""
import hashlib
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from .learning import PIPELINE_VERSION, lower_bound
from .strategies.presentation import NAMES

VERSION = '1.25.1-wins-1'
CONTEXT = ('strategy', 'apex_setup', 'source_key', 'source_identity', 'timeframe',
           'duration_seconds', 'rules_version', 'pipeline_version', 'broker', 'duration_basis')


def setup_label(strategy, variant):
    return NAMES.get(strategy, strategy or 'Não identificado')


def period_window(now, period):
    tz = ZoneInfo('America/Sao_Paulo')
    today = datetime.fromtimestamp(now, tz).date()
    if period == 'all':
        return None, None
    if period == 'day':
        first, following = today, today + timedelta(days=1)
    elif period == 'month':
        first = today.replace(day=1)
        following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        raise ValueError('Período inválido.')
    return (datetime.combine(first, time.min, tzinfo=tz).timestamp(),
            datetime.combine(following, time.min, tzinfo=tz).timestamp())


def ranking(index, now, *, scope='bot', period='all', source='', pipeline='current',
            asset='', timeframe='', duration_seconds=None, limit=5):
    from .validation_index import MIN_RATE_SAMPLE
    if scope not in ('bot', 'manual') or not 1 <= limit <= 10:
        raise ValueError('Ranking inválido.')
    start, end = period_window(now, period)
    selected_pipeline = PIPELINE_VERSION if pipeline == 'current' else pipeline
    where = ['scope=?', 'created<=?']
    args = [scope, now]
    if scope == 'manual':
        where.append(index.manual_condition())
        args.extend((now, now))
    if start is not None:
        where.append("COALESCE(json_extract(payload,'$.entry_at'),created)>=? AND "
                     "COALESCE(json_extract(payload,'$.entry_at'),created)<?")
        args.extend((start, end))
    for key, value in (('source_key', source), ('pipeline_version', selected_pipeline),
                       ('asset', asset), ('timeframe', timeframe), ('duration_seconds', duration_seconds)):
        if value not in ('', None):
            where.append("json_extract(metadata,'$." + key + "')=?")
            args.append(value)
    attributes = ','.join("COALESCE(json_extract(metadata,'$." + key + "'),'') AS " + key
                          for key in CONTEXT if key != 'apex_setup')
    # Unproven legacy outcomes are visible as exclusions, never as verified WIN.
    query = ("WITH classified AS (SELECT group_id,created,payload," + attributes + ","
             "COALESCE(json_extract(payload,'$.apex_setup'),'') AS apex_setup,"
             "CASE WHEN outcome='CANCELADO' THEN 'CANCELADO' "
             "WHEN outcome='PENDENTE' OR resolved_at>? OR json_extract(payload,'$.entry_at')>? "
             "OR json_extract(payload,'$.expiry_at')>? THEN 'PENDENTE' "
             "WHEN scope='bot' AND outcome IN ('WIN','LOSS','EMPATE') AND price_verified=0 "
             "THEN 'SEM_COMPROVACAO' ELSE outcome END AS known_outcome "
             "FROM metric_projection WHERE " + ' AND '.join(where) + ") ")
    query_args = (now, now, now, *args)
    counts = "count(*) AS signals," + ','.join(
        "COALESCE(sum(known_outcome='" + result + "'),0) AS " + field
        for result, field in (('WIN', 'wins'), ('LOSS', 'losses'), ('EMPATE', 'ties'),
                              ('PENDENTE', 'pending'), ('INDISPONIVEL', 'unavailable'),
                              ('CANCELADO', 'cancelled'), ('SEM_COMPROVACAO', 'unverified')))
    summary = dict(index.db.execute(query + 'SELECT ' + counts + ' FROM classified', query_args).fetchone())
    attributable = "strategy NOT IN ('','auto','scan_all','unknown','não informada')"
    grouped = ('SELECT ' + ','.join(CONTEXT) + ',' + counts +
               ",count(DISTINCT group_id) AS context_count,"
               "count(DISTINCT json_extract(payload,'$.asset')) AS asset_count,"
               "min(json_extract(payload,'$.entry_at')) AS first_entry_at,"
               "max(json_extract(payload,'$.expiry_at')) AS last_expiry_at "
               "FROM classified WHERE " + attributable + ' GROUP BY ' + ','.join(CONTEXT) +
               " HAVING sum(known_outcome='WIN')>0")
    total = index.db.execute(query + 'SELECT count(*) FROM (' + grouped + ')', query_args).fetchone()[0]
    unknown = index.db.execute(query + 'SELECT count(*) FROM classified WHERE NOT (' + attributable + ')', query_args).fetchone()[0]
    raw = index.db.execute(query + grouped +
        ' ORDER BY wins DESC,(wins+losses) DESC,' + ','.join(CONTEXT) + ' LIMIT ?', (*query_args, limit))
    items = []
    for record in raw:
        row = dict(record)
        sample = row['wins'] + row['losses']
        context = {k: row[k] for k in CONTEXT}
        row.update(id=hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(),
                   setup_label=setup_label(row['strategy'], row['apex_setup']), sample=sample,
                   win_rate_pct=round(100 * row['wins'] / sample, 2) if sample >= MIN_RATE_SAMPLE else None,
                   wilson_95_pct=[round(100 * lower_bound(row['wins'], sample), 2),
                                  round(100 * (1 - lower_bound(row['losses'], sample)), 2)],
                   sample_status='descriptive' if sample >= MIN_RATE_SAMPLE else 'insufficient')
        items.append(row)
    facets = {}
    for key in ('source_key', 'pipeline_version', 'asset', 'timeframe', 'duration_seconds'):
        records = index.db.execute("SELECT DISTINCT json_extract(metadata,'$." + key + "') FROM metric_projection "
                                   "WHERE scope=? AND created<=? ORDER BY 1", (scope, now))
        facets[key] = [r[0] for r in records if r[0] not in ('', None)]
    return {'version': VERSION, 'at': now, 'items': items, 'total': total, 'limit': limit,
            'scope': scope, 'period': period, 'start': start, 'end': end, 'timezone': 'America/Sao_Paulo',
            'filters': {'source': source, 'pipeline': selected_pipeline, 'asset': asset,
                        'timeframe': timeframe, 'duration_seconds': duration_seconds},
            'facets': facets, 'counts': summary, 'unknown_strategy': unknown,
            'current_pipeline': PIPELINE_VERSION, 'minimum_sample': MIN_RATE_SAMPLE,
            'order': 'wins DESC, evaluated DESC, stable context identifiers',
            'rate_denominator': 'WIN + LOSS; exclui empates, pendentes, indisponíveis e não comprovados',
            'basis': 'indicative_external_verified_prices' if scope == 'bot' else 'manual_user_declared',
            'aggregation': 'Assets, directions, regimes and configurations pooled only for descriptive counts; source identities, timeframe, duration, rules, pipeline and broker remain separate.',
            'history_scope': 'complete_including_archived', 'probability_estimate': False,
            'note': 'Mais WINs não significa maior eficácia: setups com mais sinais podem somar mais vitórias. Nenhuma preferência do motor é alterada.'}
