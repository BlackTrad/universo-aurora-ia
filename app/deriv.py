"""Deriv public market data only. No account authorization or order methods."""
import asyncio
from collections import deque
import json
import math
import re
import time
from .logging_setup import event, traced
from websockets.exceptions import InvalidStatus
from websockets.asyncio.client import connect
from .market import Candle, CandleBatch, DataError, price, valid_candle
from .timeframes import TIMEFRAMES
from .source_routing import FOREX_ASSETS, CRYPTO_BINARY_ASSETS

class DerivProvider:
    name = 'Deriv'
    identity = 'deriv:forex:public'
    ready = True
    url = 'wss://api.derivws.com/trading/v1/options/ws/public'

    def __init__(self, config):
        self.config = config
        if config.asset_scope=='crypto_binary':
            self.identity='deriv:crypto:binary:public'
        self.contract_cache = {}
        self.catalog_cache = None
        self.catalog_lock = asyncio.Lock()
        self.candle_cache = {}
        self.candle_locks = {}
        self.request_slots = asyncio.Semaphore(2)
        self.request_times = deque()
        self.cooldown_until = 0

    async def request(self, payload, expected):
        # Shared budget across chart, quotes, analysis and settlement.
        async with self.request_slots:
            now=time.monotonic()
            while self.request_times and now-self.request_times[0]>=60:
                self.request_times.popleft()
            if now<self.cooldown_until or len(self.request_times)>=90:
                event('deriv.budget.blocked',operation=expected)
                raise DataError('Limite local de consultas atingido. Aguarde.','rate_limit')
            self.request_times.append(now)
            try:return await self._request(payload,expected)
            except DataError as error:
                if error.code=='rate_limit':self.cooldown_until=time.monotonic()+60
                raise

    async def catalog(self):
        async with self.catalog_lock:
            now=time.monotonic()
            if self.catalog_cache and now-self.catalog_cache[0]<15:
                event('deriv.cache.hit',operation='active_symbols')
                return self.catalog_cache[1]
            self.catalog_cache=None
            data=await self.request({'active_symbols':'brief'},'active_symbols')
            if not isinstance(data.get('active_symbols'),list):
                raise DataError('Lista de ativos da Deriv inválida.')
            self.catalog_cache=(time.monotonic(),data)
            return data

    @traced('deriv.request')
    async def _request(self, payload, expected):
        event('deriv.request.parameters',operation=expected,
              symbol=payload.get('ticks') or payload.get('ticks_history') or payload.get('contracts_for'),
              granularity=payload.get('granularity'),count=payload.get('count'),end=payload.get('end'))
        try:
            async with asyncio.timeout(8):
                async with connect(self.url, open_timeout=5, close_timeout=1, max_size=2**20) as ws:
                    await ws.send(json.dumps({**payload, 'req_id': 1}))
                    data = json.loads(await ws.recv())
                    if not isinstance(data,dict): raise DataError('Resposta da Deriv em formato inválido.','invalid_data')
                    if data.get('error') or data.get('errors'):
                        errors=[data.get('error')]+(data['errors'] if isinstance(data.get('errors'),list) else [])
                        codes={error.get('code') for error in errors if isinstance(error,dict) and isinstance(error.get('code'),str)}
                        event('deriv.request.rejected',operation=expected,
                              provider_codes=sorted(code for code in codes if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',code))[:8])
                        if codes & {'RateLimit','RateLimitExceeded','TooManyRequests'}: raise DataError('Limite de consultas da Deriv atingido. Aguarde.','rate_limit')
                        if codes & {'MarketIsClosed','TradingSuspended'}: raise DataError('Mercado fechado ou suspenso na Deriv.','market_closed')
                        raise DataError('Deriv recusou a consulta pública. Confira ativo e compatibilidade da API.','provider_rejected')
                    if data.get('msg_type') != expected or data.get('req_id', 1) != 1:
                        raise DataError('Resposta da Deriv não corresponde à consulta.')
                    event('deriv.response',operation=expected,response_count=len(data.get(expected,[])),status='received')
                    return data
        except DataError:
            raise
        except (asyncio.TimeoutError,TimeoutError):
            raise DataError('Tempo limite da Deriv excedido.','timeout') from None
        except (json.JSONDecodeError,UnicodeDecodeError):
            raise DataError('Resposta da Deriv em formato inválido.','invalid_data') from None
        except InvalidStatus as exc:
            status=exc.response.status_code
            if status==429: raise DataError('Limite de consultas da Deriv atingido. Aguarde.','rate_limit') from None
            raise DataError('Endpoint público Deriv recusou a conexão.','connection') from None
        except Exception:
            raise DataError('Não foi possível conectar à fonte pública da Deriv.','connection') from None

    def symbol(self, asset):
        if getattr(self.config,'asset_scope','legacy')=='crypto_binary':
            if asset not in CRYPTO_BINARY_ASSETS:
                raise DataError('Ativo fora dos 18 contratos cripto /USD.', 'unsupported_asset')
            # A possible canonical code, never proof of contract availability.
            # check_asset() MUST verify exact code and market against active_symbols.
            return 'cry'+asset.replace('/', '')
        if getattr(self.config,'asset_scope','legacy')=='forex' and asset not in FOREX_ASSETS:
            raise DataError('Ativo fora da allowlist Forex.','unsupported_asset')
        if not isinstance(asset,str) or not re.fullmatch(r'[A-Z]{3}/[A-Z]{3}',asset) or 'OTC' in asset:
            raise DataError('Par de moedas inválido para Deriv.','unsupported_asset')
        return 'frx' + asset.replace('/', '')

    async def browse_instruments(self):
        """Return catalogue-verified symbols; unavailable assets remain visible but unusable."""
        if self.config.asset_scope=='crypto_binary':
            data=await self.catalog()
            rows=data['active_symbols']
            result=[]
            for asset in CRYPTO_BINARY_ASSETS:
                code=self.symbol(asset)
                found=[r for r in rows if isinstance(r,dict)
                       and r.get('underlying_symbol')==code and r.get('market')=='cryptocurrency']
                match=found[0] if len(found)==1 else None
                state=('suspended' if match.get('is_trading_suspended')==1 else
                       'closed' if match.get('exchange_is_open')==0 else
                       'open' if match.get('exchange_is_open')==1 and match.get('is_trading_suspended')==0
                       else 'unknown') if match else 'unavailable'
                result.append({'symbol':asset,'availability':state,
                               'detail':'Catálogo público Deriv: contrato ainda requer validação e payout.' if match else
                                        'Par não confirmado no catálogo de cripto da Deriv.'})
            return result
        return await self.browse_forex_instruments()

    async def browse_forex_instruments(self):
        """Return regular Forex symbols confirmed by Deriv active_symbols.

        This method is read-only and never changes config.assets or monitoring.
        """
        data=await self.catalog()
        rows=data.get('active_symbols')
        if not isinstance(rows,list):
            raise DataError('Lista de ativos da Deriv inválida.','invalid_data')
        found={}
        for row in rows:
            if not isinstance(row,dict) or row.get('market')!='forex':
                continue
            raw=row.get('underlying_symbol')
            match=re.fullmatch(r'frx([A-Z]{3})([A-Z]{3})',raw or '')
            if not match:
                continue
            symbol=match.group(1)+'/'+match.group(2)
            suspended=row.get('is_trading_suspended')
            opened=row.get('exchange_is_open')
            if suspended==1:
                availability='suspended'
            elif opened==0:
                availability='closed'
            elif opened==1 and suspended==0:
                availability='open'
            else:
                availability='unknown'
            found[symbol]={'symbol':symbol,'availability':availability,
                           'detail':'Deriv active_symbols; pares de moedas regulares como referência de preço.'}
        if not found:
            raise DataError('Catálogo de pares de moedas da Deriv indisponível.','invalid_data')
        return [found[s] for s in sorted(found)]

    async def check_asset(self, asset):
        symbol = self.symbol(asset)
        data = await self.catalog()
        rows = data.get('active_symbols')
        if not isinstance(rows, list):
            raise DataError('Lista de ativos da Deriv inválida.')
        expected_market='cryptocurrency' if self.config.asset_scope=='crypto_binary' else 'forex'
        matches = [r for r in rows if isinstance(r, dict) and r.get('underlying_symbol') == symbol]
        if len(matches)!=1 or matches[0].get('market')!=expected_market:
            raise DataError('Ativo não disponível no catálogo compatível da Deriv.','unsupported_asset')
        if len(matches)!=1 or matches[0].get('exchange_is_open') not in (0,1) or matches[0].get('is_trading_suspended') not in (0,1):
            raise DataError('Metadados de disponibilidade da Deriv incompletos.','invalid_data')
        event('deriv.market',asset=asset,status='open' if matches[0]['exchange_is_open']==1 else 'closed')
        if matches[0]['is_trading_suspended']==1:
            raise DataError('Negociação do ativo suspensa na Deriv.','market_suspended')
        if matches[0]['exchange_is_open']==0:
            raise DataError('Mercado do ativo fechado na Deriv.','market_closed')

    async def live_price(self, asset):
        from .live_price import PricePoint
        symbol = self.symbol(asset)
        # Read the first native tick, then _request closes its own connection.
        # This uses the same public subscription contract as stream_prices.
        data = await self.request({'ticks':symbol,'subscribe':1}, 'tick')
        try:
            echo = data.get('echo_req')
            if echo is not None and (not isinstance(echo,dict) or echo.get('ticks') != symbol):
                raise ValueError()
            tick = data['tick']
            if not isinstance(tick,dict) or tick.get('symbol') != symbol:
                raise ValueError()
            epoch = tick['epoch']
            if type(epoch) not in (int,float) or not math.isfinite(epoch) or epoch<=0:
                raise ValueError()
            return PricePoint(price(tick['quote']),float(epoch),time.time(),asset,self.identity)
        except (KeyError,TypeError,ValueError,OverflowError,AttributeError):
            raise DataError('Tick público Deriv sem preço/horário válido.','live_price_invalid') from None

    async def stream_prices(self, assets):
        from .live_price import PricePoint
        symbols = {self.symbol(asset): asset for asset in assets}
        if not symbols: return
        # Subscription requests share the existing local request budget.
        async with self.request_slots:
            now = time.monotonic()
            while self.request_times and now-self.request_times[0] >= 60: self.request_times.popleft()
            if now < self.cooldown_until or len(self.request_times)+len(symbols) > 90:
                raise DataError('Orçamento de assinaturas da Deriv esgotado.', 'rate_limit')
            self.request_times.extend([now]*len(symbols))
        async with connect(self.url, open_timeout=5, close_timeout=1, max_size=2**20,
                           ping_interval=20, ping_timeout=10) as ws:
            for index, symbol in enumerate(symbols, 1):
                await ws.send(json.dumps({'ticks': symbol, 'subscribe': 1, 'req_id': index}))
            async for message in ws:
                try:
                    data = json.loads(message)
                    if not isinstance(data, dict): raise ValueError()
                    if data.get('error') or data.get('errors'):
                        raise DataError('Assinatura pública Deriv recusada.', 'stream_rejected')
                    if data.get('msg_type') != 'tick': continue
                    row = data['tick']; symbol = row['symbol']
                    if symbol not in symbols: raise ValueError()
                    at = row['epoch']
                    if type(at) not in (int, float) or not math.isfinite(at) or at <= 0: raise ValueError()
                    echo = data.get('echo_req')
                    if echo is not None and echo.get('ticks') != symbol: raise ValueError()
                    yield PricePoint(price(row['quote']), at, time.time(), symbols[symbol], self.identity)
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise DataError('Tick Deriv inválido na assinatura original.', 'live_price_invalid') from None

    async def candles(self, asset, interval=300, count=100, *, end="latest"):
        self.symbol(asset)
        if interval not in TIMEFRAMES.values():
            raise DataError('Intervalo não habilitado para Deriv.')
        if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=5000:
            raise DataError('Parâmetros de candles inválidos.')
        # Historical settlement always queries its exact original range.
        if end!='latest':return await self._candles(asset,interval,count,end=end)
        key=(asset,interval)
        async with self.candle_locks.setdefault(key,asyncio.Lock()):
            now=time.monotonic();bucket=int(time.time()//interval)
            cached=self.candle_cache.get(key)
            if cached and now-cached[0]<10 and bucket==cached[1] and len(cached[2])>=count:
                event('deriv.cache.hit',operation='candles',symbol=self.symbol(asset))
                return CandleBatch(cached[2][-count:],self.identity)
            self.candle_cache.pop(key,None)
            rows=await self._candles(asset,interval,count,end=end)
            # Do not cache responses crossing a candle boundary or malformed rows.
            if (rows and bucket==int(time.time()//interval)
                and all(valid_candle(c) and c.t>0 and c.t%interval==0 and c.t<=time.time() for c in rows)
                and all(b.t>a.t for a,b in zip(rows,rows[1:]))):
                self.candle_cache[key]=(time.monotonic(),bucket,tuple(rows))
            return rows

    async def _candles(self, asset, interval=300, count=100, *, end="latest"):
        if interval not in TIMEFRAMES.values():
            raise DataError('Intervalo não habilitado para Deriv.')
        symbol = self.symbol(asset)
        data = await self.request({'ticks_history': symbol, 'end': end, 'style': 'candles',
                                   'granularity': interval, 'count': count}, 'candles')
        try:
            echo = data.get('echo_req')
            if echo is not None and (echo.get('ticks_history') != symbol or echo.get('granularity') != interval or (end!='latest' and str(echo.get('end')) != str(end))):
                raise ValueError()
            rows = data['candles']
            if not isinstance(rows, list): raise ValueError()
            candles = []
            for row in rows:
                if isinstance(row['epoch'],bool): raise ValueError()
                epoch = float(row['epoch'])
                if not epoch.is_integer(): raise ValueError()
                volume=row.get('volume')
                if volume is not None:
                    if isinstance(volume,bool): raise ValueError()
                    volume=float(volume)
                    if not math.isfinite(volume) or volume < 0: raise ValueError()
                candles.append(Candle(int(epoch), *[price(row[k]) for k in ('open','high','low','close')], volume))
            return CandleBatch(candles, self.identity)
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            raise DataError('Candles da Deriv incompletos ou inconsistentes.') from None

    async def settlement_candles(self, asset, entry_at, expiry_at):
        if (not isinstance(entry_at, int) or not isinstance(expiry_at, int)
                or entry_at % 60 or expiry_at % 60 or not 0 < expiry_at-entry_at <= 300):
            raise DataError('Horários inválidos para apuração histórica.')
        return await self.candles(asset,60,(expiry_at-entry_at)//60+2,end=expiry_at+59)

    async def confirmed_payout(self, asset, direction, duration):
        """Read-only quote for a binary-option contract. Unverifiable => no signal.

        Return NET profit/stake, not gross payout/stake or a user-entered estimate.
        """
        if direction not in ('CALL','PUT') or duration not in (60,120,180,300):
            raise DataError('Contrato de opção binária inválido.', 'payout_unavailable')
        from decimal import Decimal,InvalidOperation
        code=self.symbol(asset)
        payload={'proposal':1,'amount':1,'basis':'stake','contract_type':direction,
                 'currency':'USD','duration':duration//60,'duration_unit':'m',
                 'underlying_symbol':code}
        data=await self.request(payload,'proposal')
        try:
            row=data['proposal']
            if not isinstance(row,dict) or not row.get('id'):
                raise ValueError()
            echo=data.get('echo_req')
            if echo is not None and (not isinstance(echo,dict) or
                    any(str(echo.get(k))!=str(v) for k,v in payload.items())):
                raise ValueError()
            cost=Decimal(str(row['ask_price']))
            gross=Decimal(str(row['payout']))
            if not cost.is_finite() or not gross.is_finite() or cost<=0 or gross<=cost:
                raise ValueError()
            net=gross/cost-1
            if not net.is_finite() or net<Decimal('0.90') or net>Decimal('10'):
                raise ValueError()
            return float(net)
        except (KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
            raise DataError('Proposta não confirmou payout líquido de pelo menos 90%.','payout_unavailable') from None

    async def supports(self, asset, direction, duration):
        symbol=self.symbol(asset)
        cached=self.contract_cache.get(symbol)
        if cached and time.monotonic()-cached[0]<30:
            data=cached[1]
        else:
            self.contract_cache.pop(symbol,None)
            data=await self.request({'contracts_for':symbol},'contracts_for')
            if isinstance(data.get('contracts_for'),dict) and isinstance(data['contracts_for'].get('available'),list):
                self.contract_cache[symbol]=(time.monotonic(),data)
        def seconds(value):
            match = re.fullmatch(r'(\d+)([smhd])', str(value))
            return int(match[1]) * {'s':1,'m':60,'h':3600,'d':86400}[match[2]] if match else None
        missing_duration=False
        matching=False
        try:
            for c in data['contracts_for']['available']:
                low, high = seconds(c.get('min_contract_duration')), seconds(c.get('max_contract_duration'))
                if (c.get('contract_type') == direction and c.get('barriers') == 0
                    and c.get('underlying_symbol') == self.symbol(asset)):
                    matching=True
                    if low is None or high is None:
                        missing_duration=True
                    elif low <= duration <= high:
                        return
        except (KeyError, TypeError, AttributeError):
            raise DataError('Metadados dos contratos da Deriv inválidos.','contract_metadata') from None
        if missing_duration:
            raise DataError('Deriv retornou o contrato, mas não confirmou seus limites de duração em tempo. Sinal bloqueado.','contract_duration_unknown')
        if matching:
            raise DataError(f'Deriv não confirmou duração de {duration//60} minutos para {direction} neste ativo.','contract_duration')
        raise DataError(f'Deriv não confirmou contrato {direction} sem barreira neste ativo.','contract_unsupported')
