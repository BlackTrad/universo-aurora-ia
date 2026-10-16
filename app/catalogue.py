"""Read-only instrument catalogue separated from the operational monitoring allowlist."""
import re
from .market import DataError

NAMES={'BTC':'Bitcoin','ETH':'Ethereum','SOL':'Solana','XRP':'XRP','BNB':'BNB','ADA':'Cardano','DOGE':'Dogecoin','DOT':'Polkadot','LTC':'Litecoin','APT':'Aptos','ARB':'Arbitrum','ATOM':'Cosmos','AVAX':'Avalanche','BCH':'Bitcoin Cash','ICP':'Internet Computer','IMX':'Immutable','INJ':'Injective','LINK':'Chainlink','NEAR':'NEAR','ETC':'Ethereum Classic','FIL':'Filecoin','EUR':'Euro','USD':'Dólar americano','GBP':'Libra esterlina','JPY':'Iene','AUD':'Dólar australiano','CAD':'Dólar canadense','CHF':'Franco suíço','NZD':'Dólar neozelandês','USDT':'USDT'}
REGULAR_FOREX=re.compile(r'^[A-Z]{3}/[A-Z]{3}$')


def _name(symbol):
    return ' / '.join(NAMES.get(part,part) for part in symbol.split('/'))


def _analysis_assets(service,key,provider):
    """Assets that the current operational configuration can actually monitor."""
    if getattr(service,'automatic_sources',False):
        from .source_routing import assets_for_source
        return assets_for_source(service,key)
    return list(getattr(provider,'assets',service.config.assets if key=='deriv' else []))


def _selectable(service,key,provider,symbol,monitorable):
    scope=getattr(service.config,'asset_scope','legacy')
    if scope in ('forex','crypto_binary'):return key=='deriv' and symbol in monitorable
    return symbol in monitorable


def _enabled(service,key,provider,symbol,monitorable):
    return (_selectable(service,key,provider,symbol,monitorable)
            and (getattr(service,'automatic_sources',False) or key==service.provider_key)
            and symbol in service.settings()['assets'])


async def _rows_for_provider(service,key,provider):
    monitorable=_analysis_assets(service,key,provider)
    scope=getattr(service.config,'asset_scope','legacy')
    market='crypto' if scope=='crypto_binary' else 'forex' if scope=='forex' or key=='deriv' else 'crypto'
    source=getattr(provider,'name',key)
    timeframes=list(getattr(provider,'timeframes',['M1','M2','M3','M5'] if key=='deriv' else ['M1','M5']))

    # Deriv can enumerate the current public regular-Forex catalogue.  This is
    # intentionally read-only and does not expand the operational allowlist.
    if hasattr(provider,'browse_instruments'):
        try:
            discovered=await provider.browse_instruments()
        except DataError as error:
            # Keep configured instruments visible when the catalogue endpoint is
            # temporarily unavailable, but mark the support basis explicitly.
            return [{'id':key+':'+symbol,'symbol':symbol,'name':_name(symbol),'source_key':key,'source':source,
                     'market':market,'timeframes':timeframes,'implemented':True,'availability':'unavailable',
                     'availability_detail':str(error),'analysis_selectable':_selectable(service,key,provider,symbol,monitorable),
                     'analysis_enabled':_enabled(service,key,provider,symbol,monitorable),
                     'support_basis':'configured operational allowlist; live provider catalogue unavailable'}
                    for symbol in monitorable]
        rows=[]
        for item in discovered:
            symbol=item.get('symbol') if isinstance(item,dict) else None
            if not isinstance(symbol,str):
                continue
            rows.append({'id':key+':'+symbol,'symbol':symbol,'name':_name(symbol),'source_key':key,'source':source,
                         'market':market,'timeframes':timeframes,'implemented':True,
                         'availability':item.get('availability','not_checked'),
                         'availability_detail':item.get('detail'),
                         'analysis_selectable':_selectable(service,key,provider,symbol,monitorable),
                         'analysis_enabled':_enabled(service,key,provider,symbol,monitorable),
                         'support_basis':'provider live catalogue; visualization does not enable monitoring'})
        return rows

    return [{'id':key+':'+symbol,'symbol':symbol,'name':_name(symbol),'source_key':key,'source':source,
             'market':market,'timeframes':timeframes,'implemented':True,'availability':'not_checked',
             'availability_detail':None,'analysis_selectable':_selectable(service,key,provider,symbol,monitorable),
             'analysis_enabled':_enabled(service,key,provider,symbol,monitorable),
             'support_basis':'provider implementation allowlist; live availability requires quote/check'}
            for symbol in monitorable]


async def instruments(service,source=None,market=None):
    groups=[]
    for key,provider in service.providers.items():
        if getattr(service.config,'asset_scope','legacy') in ('forex','crypto_binary') and key!='deriv':continue
        if source is not None and key!=source:
            continue
        provider_market='crypto' if getattr(service.config,'asset_scope','legacy')=='crypto_binary' else 'forex' if getattr(service.config,'asset_scope','legacy')=='forex' or key=='deriv' else 'crypto'
        if market is not None and provider_market!=market:
            continue
        groups.append(await _rows_for_provider(service,key,provider))
    # Interleave sources so all providers remain visible on the first page.
    return [group[i] for i in range(max((len(g) for g in groups),default=0)) for group in groups if i<len(group)]


def resolve(service,source,asset):
    """Resolve a visual request without changing what the scanner may monitor."""
    from fastapi import HTTPException
    if getattr(service.config,'asset_scope','legacy')=='crypto_binary':
        from .source_routing import CRYPTO_BINARY_ASSETS
        if (source not in (None,'deriv') or asset not in CRYPTO_BINARY_ASSETS
                or 'deriv' not in service.providers):
            raise HTTPException(422,'Somente os 18 pares /USD de opções binárias estão habilitados.')
        return 'deriv',service.providers['deriv']
    if getattr(service.config,'asset_scope','legacy')=='forex':
        from .source_routing import FOREX_ASSETS
        if (source not in (None,'deriv') or asset not in FOREX_ASSETS
                or 'deriv' not in service.providers):
            raise HTTPException(422,'Somente os pares Forex da allowlist são suportados.')
        return 'deriv',service.providers['deriv']
    key=source or service.provider_key
    if source is None and getattr(service,'automatic_sources',False):
        from .source_routing import assets_for_source
        matching=[k for k in service.providers if asset in assets_for_source(service,k)]
        if matching:
            key=matching[0]
        elif getattr(service.config,'asset_scope','legacy')=='forex':
            from .source_routing import FOREX_ASSETS
            if asset in FOREX_ASSETS and 'deriv' in service.providers:key='deriv'
        elif isinstance(asset,str) and REGULAR_FOREX.fullmatch(asset) and 'deriv' in service.providers:
            key='deriv'  # The live catalogue still has to confirm support.
    provider=service.providers.get(key)
    if provider is None:
        raise HTTPException(422,'Fonte não habilitada nesta instalação.')
    if key=='deriv':
        if getattr(service.config,'asset_scope','legacy')=='forex':
            from .source_routing import FOREX_ASSETS
            if asset not in FOREX_ASSETS:raise HTTPException(422,'Ativo fora da allowlist Forex.')
            return key,provider
        # Regular Forex pairs may be viewed when the live Deriv catalogue confirms
        # them. check_asset() performs that confirmation before quote/chart data.
        if not isinstance(asset,str) or not REGULAR_FOREX.fullmatch(asset) or 'OTC' in asset:
            raise HTTPException(422,'Ativo Forex inválido para visualização.')
    else:
        assets=getattr(provider,'assets',[])
        if asset not in assets:
            raise HTTPException(422,'Ativo não suportado nesta fonte.')
    return key,provider
