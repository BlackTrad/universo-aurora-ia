"""Automatic routing keeps native instruments and their original quote source."""

FOREX_ASSETS = ('EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CAD', 'USD/CHF', 'NZD/USD')
# Immutable binary-options crypto universe. These are contract names, NOT spot substitutes.
CRYPTO_BINARY_ASSETS = (
    'ADA/USD', 'APT/USD', 'ARB/USD', 'ATOM/USD', 'AVAX/USD', 'BCH/USD',
    'ICP/USD', 'IMX/USD', 'INJ/USD', 'LINK/USD', 'LTC/USD', 'NEAR/USD',
    'BTC/USD', 'DOGE/USD', 'DOT/USD', 'ETC/USD', 'ETH/USD', 'FIL/USD',
)


def assets_for_source(service, key):
    provider = service.providers[key]
    scope=getattr(service.config,'asset_scope','legacy')
    if scope=='forex':return list(FOREX_ASSETS) if key=='deriv' else []
    if scope=='crypto_binary':return list(CRYPTO_BINARY_ASSETS) if key=='deriv' else []
    fallback = service.config.assets if key == getattr(service.config, 'provider', 'deriv') else FOREX_ASSETS if key == 'deriv' else ()
    return list(getattr(provider, 'assets', fallback))


def available_assets(service):
    groups = [assets_for_source(service, key) for key in service.providers]
    # Interleaving preserves legacy behavior; strict Forex scope returns only Deriv.
    return list(dict.fromkeys(asset for i in range(max(map(len, groups), default=0))
                              for group in groups if i < len(group) for asset in (group[i],)))


def plans(service, assets, timeframe):
    return [{'source_key': key, 'assets': [a for a in assets if a in assets_for_source(service, key)]}
            for key, provider in service.providers.items()
            if (getattr(service.config,'asset_scope','legacy')=='legacy' or key=='deriv')
            and timeframe in getattr(provider, 'timeframes', ('M1', 'M5'))
            and any(a in assets_for_source(service, key) for a in assets)]


def next_check(analysis, now, interval):
    if analysis.get('retry_after_seconds') and not analysis.get('failed_assets'):
        return now + analysis['retry_after_seconds']
    if analysis['status'] == 'erro' or analysis.get('failed_assets'):
        checks = analysis.get('checks', [])
        closed = bool(checks) and all(c.get('code') == 'market_closed' for c in checks)
        limited = any(c.get('code') == 'rate_limit' for c in checks)
        return now + (300 if closed else 60 if limited else 30)
    return (int(now // interval) + 1) * interval + 2


def fresh_health(record, now, max_age):
    result = dict(record)
    if result.get('status') in ('disponível', 'parcial'):
        stamp = result.get('data_at', 0)
        if not stamp or now - stamp > result.get('max_age', max_age):
            result.update(status='desatualizado',
                          detail='Última consulta não representa mais dados atuais. Verifique novamente ou inicie a análise.')
    return result


def summarize_health(records, now, max_age):
    rows = [fresh_health(record, now, max_age) for record in records]
    good = sum(row.get('status') == 'disponível' for row in rows)
    some = any(row.get('status') in ('disponível', 'parcial') for row in rows)
    old = any(row.get('status') == 'desatualizado' for row in rows)
    checked = any(row.get('checked_at') is not None for row in rows)
    status = ('disponível' if good == len(rows) and good else 'parcial' if some
              else 'desatualizado' if old else 'indisponível' if checked else 'não verificado')
    return {'status': status, 'detail': f'Fontes automáticas · {good}/{len(rows)} disponíveis.' +
            (' Há dados antigos; atualize a consulta.' if old else ''),
            'sources': rows, 'checked_at': max((r.get('checked_at', 0) for r in rows), default=0),
            'data_at': min((r['data_at'] for r in rows if r.get('data_at')), default=0),
            'max_age': max_age}
