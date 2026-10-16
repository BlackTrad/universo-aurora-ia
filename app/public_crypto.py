"""Public spot quotes only. Explicit source selection, no account/order endpoints."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import math
import time
import httpx
import json
from websockets.asyncio.client import connect
from .market import Candle, CandleBatch, DataError, price, valid_candle
from .logging_setup import event


class PublicCrypto:
    ready=True
    timeframes=['M1','M5']

    def __init__(self,client):
        self.client=client
        self.slots=asyncio.Semaphore(2)
        self.request_times=deque()
        self.cooldown_until=0
        self.cache={};self.locks={};self.products={}

    def symbol(self,asset):
        if asset not in self.assets: raise DataError('Ativo não habilitado nesta fonte.','unsupported_asset')
        return asset.replace('/',self.separator)

    async def get(self,path,params=None):
        async with self.slots:
            now=time.monotonic()
            while self.request_times and now-self.request_times[0]>=60:self.request_times.popleft()
            if now<self.cooldown_until or len(self.request_times)>=60:
                raise DataError('Limite de consultas da fonte atingido. Aguarde.','rate_limit')
            self.request_times.append(now)
            try:
                response=await self.client.get(self.url+path,params=params,timeout=8)
                if response.status_code in (418,429):
                    try:delay=max(60,float(response.headers.get('Retry-After','60')))
                    except ValueError:delay=60
                    if not math.isfinite(delay):delay=60
                    self.cooldown_until=time.monotonic()+delay
                    raise DataError('Fonte limitada; nova consulta após o prazo do servidor.','rate_limit')
                if response.status_code in (403,451):raise DataError('Fonte indisponível nesta região ou rede.','connection')
                if response.status_code in (400,404):raise DataError('Ativo ou consulta recusados pela fonte.','unsupported_asset')
                response.raise_for_status()
                data=response.json()
                event('public_crypto.response',provider=self.name,path=path,http_status=response.status_code)
                return data
            except DataError:raise
            except httpx.TimeoutException:raise DataError('Tempo limite da fonte excedido.','timeout') from None
            except (httpx.HTTPError,ValueError):raise DataError('Resposta indisponível ou inválida da fonte.','connection') from None

    async def check_asset(self,asset):
        symbol=self.symbol(asset)
        async with self.locks.setdefault(('product',asset),asyncio.Lock()):
            cached=self.products.get(asset)
            if cached and time.monotonic()-cached<300:return
            if self.key=='binance':
                data=await self.get('/api/v3/exchangeInfo',{'symbol':symbol})
                if not isinstance(data,dict) or not isinstance(data.get('symbols'),list):raise DataError('Catálogo da Binance inválido.')
                matches=[x for x in data['symbols'] if isinstance(x,dict) and x.get('symbol')==symbol]
                if len(matches)!=1:raise DataError('Par indisponível na Binance.','unsupported_asset')
                row=matches[0]
                if row.get('status')!='TRADING' or row.get('isSpotTradingAllowed') is not True:
                    raise DataError('Par spot suspenso ou fechado.','market_closed')
                if f"{row.get('baseAsset')}/{row.get('quoteAsset')}"!=asset:
                    raise DataError('Identidade do par inválida.')
            else:
                row=await self.get('/products/'+symbol)
                if not isinstance(row,dict) or row.get('id')!=symbol or f"{row.get('base_currency')}/{row.get('quote_currency')}"!=asset:
                    raise DataError('Identidade do produto inválida.')
                if row.get('status')!='online' or row.get('trading_disabled') is not False:
                    raise DataError('Produto suspenso ou fechado.','market_closed')
            self.products[asset]=time.monotonic()

    async def candles(self,asset,interval=300,count=100,*,end='latest'):
        self.symbol(asset)
        if interval not in (60,300) or isinstance(interval,bool) or isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=300:
            raise DataError('Parâmetros de candles inválidos.')
        if end!='latest' and (isinstance(end,bool) or not isinstance(end,int) or end<=0):
            raise DataError('Final do histórico inválido.')
        if end!='latest':return await self._candles(asset,interval,count,end)
        key=(asset,interval)
        async with self.locks.setdefault(key,asyncio.Lock()):
            bucket=int(time.time()//interval);cached=self.cache.get(key)
            if cached and time.monotonic()-cached[0]<30 and cached[1]==bucket and len(cached[2])>=count:
                return CandleBatch(cached[2][-count:],self.identity)
            self.cache.pop(key,None)
            rows=await self._candles(asset,interval,max(count,100),end)
            if rows and bucket==int(time.time()//interval):self.cache[key]=(time.monotonic(),bucket,tuple(rows))
            return CandleBatch(rows[-count:],self.identity)

    async def live_price(self,asset):
        from .live_price import PricePoint
        symbol=self.symbol(asset)
        data = (await self.get('/api/v3/trades',{'symbol':symbol,'limit':1}) if self.key=='binance'
                else await self.get('/products/'+symbol+'/ticker'))
        try:
            row = data[-1] if self.key=='binance' else data
            if not isinstance(row,dict):raise ValueError()
            if self.key=='binance':
                raw=row['time']
                if type(raw) is not int or raw<=0:raise ValueError()
                at=raw/1000
            else:
                raw=row['time']
                if not isinstance(raw,str):raise ValueError()
                instant=datetime.fromisoformat(raw.replace('Z','+00:00'))
                if instant.tzinfo is None:raise ValueError()
                at=instant.timestamp()
            trade_id=row.get('id') if self.key=='binance' else row.get('trade_id')
            if type(trade_id) is not int or trade_id<0:raise ValueError()
            return PricePoint(price(row['price']),at,time.time(),asset,self.identity,str(trade_id))
        except (ValueError,TypeError,KeyError,IndexError,OverflowError):
            raise DataError('Negócio público sem preço/horário válido.','live_price_invalid') from None

    async def stream_prices(self, assets):
        from .live_price import PricePoint
        symbols = {self.symbol(asset): asset for asset in assets}
        if not symbols: return
        if self.key == 'binance':
            streams = '/'.join(symbol.lower()+'@trade' for symbol in symbols)
            url = 'wss://data-stream.binance.vision/stream?streams='+streams
        else:
            url = 'wss://ws-feed.exchange.coinbase.com'
        async with connect(url, open_timeout=5, close_timeout=1, max_size=2**20,
                           ping_interval=20, ping_timeout=10) as ws:
            if self.key == 'coinbase':
                await ws.send(json.dumps({'type': 'subscribe', 'product_ids': list(symbols),
                                          'channels': ['ticker', 'heartbeat']}))
            async for message in ws:
                try:
                    data = json.loads(message)
                    if not isinstance(data, dict): raise ValueError()
                    if self.key == 'binance':
                        row = data['data']
                        if row.get('e') == 'serverShutdown': raise DataError('Fonte reiniciando.', 'stream_disconnected')
                        if row.get('e') != 'trade': continue
                        symbol = row['s']; raw = row['T']; trade = row['t']
                        if data.get('stream') != symbol.lower()+'@trade': raise ValueError()
                        if type(raw) is not int or raw <= 0: raise ValueError()
                        at = raw/1000; value = row['p']
                    else:
                        if data.get('type') == 'error': raise DataError('Assinatura pública Coinbase recusada.', 'stream_rejected')
                        if data.get('type') != 'ticker': continue
                        symbol = data['product_id']; trade = data['trade_id']; value = data['price']
                        instant = datetime.fromisoformat(data['time'].replace('Z', '+00:00'))
                        if instant.tzinfo is None: raise ValueError()
                        at = instant.timestamp()
                    if symbol not in symbols or type(trade) is not int or trade < 0: raise ValueError()
                    yield PricePoint(price(value), at, time.time(), symbols[symbol], self.identity, str(trade))
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    raise DataError('Negócio inválido na assinatura original.', 'live_price_invalid') from None

    async def _candles(self,asset,interval,count,end):
        symbol=self.symbol(asset)
        upper=int(time.time()) if end=='latest' else end
        if self.key=='binance':
            params={'symbol':symbol,'interval':'1m' if interval==60 else '5m','limit':count}
            if end!='latest':params['endTime']=end*1000+999
            data=await self.get('/api/v3/klines',params)
        else:
            iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
            start=(upper//interval-count+1)*interval
            data=await self.get('/products/'+symbol+'/candles',{'granularity':interval,'start':iso(start),'end':iso(upper)})
        try:
            if not isinstance(data,list):raise ValueError()
            rows=[]
            for row in data:
                if not isinstance(row,list) or len(row)<6 or isinstance(row[0],bool):raise ValueError()
                epoch=float(row[0])/(1000 if self.key=='binance' else 1)
                if not epoch.is_integer():raise ValueError()
                if isinstance(row[5],bool):raise ValueError()
                volume=float(row[5])
                values=row[1:5] if self.key=='binance' else [row[3],row[2],row[1],row[4]]
                c=Candle(int(epoch),*[price(v) for v in values],volume)
                if not valid_candle(c) or c.t<=0 or c.t%interval or c.t>time.time():raise ValueError()
                if c.t<=upper:rows.append(c)
            rows.sort(key=lambda c:c.t)
            if any(a.t>=b.t for a,b in zip(rows,rows[1:])):raise ValueError()
            return CandleBatch(rows[-count:],self.identity)
        except (ValueError,TypeError,OverflowError,IndexError):
            raise DataError('Candles incompletos ou inconsistentes na fonte.') from None

    async def reference_candle(self,asset,midnight):
        rows=await self.candles(asset,60,2,end=midnight-1)
        eligible=[c for c in rows if c.t+60<=midnight]
        if not eligible:raise DataError('Fechamento de referência indisponível.')
        return eligible[-1]

    async def settlement_candles(self,asset,entry_at,expiry_at):
        if (any(isinstance(t,bool) or not isinstance(t,int) or t<=0 or t%60 for t in (entry_at,expiry_at))
                or not 0<expiry_at-entry_at<=300):raise DataError('Horários inválidos para apuração.')
        return await self.candles(asset,60,(expiry_at-entry_at)//60+2,end=expiry_at+59)


class BinanceProvider(PublicCrypto):
    key='binance';name='Binance';identity='binance:spot:USDT:public'
    url='https://data-api.binance.vision';separator=''
    assets=['BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BNB/USDT','ADA/USDT','DOGE/USDT','DOT/USDT']


class CoinbaseProvider(PublicCrypto):
    key='coinbase';name='Coinbase';identity='coinbase:spot:USD:public'
    url='https://api.exchange.coinbase.com';separator='-'
    assets=['BTC/USD','ETH/USD','SOL/USD','XRP/USD','ADA/USD','DOGE/USD','DOT/USD','LTC/USD']
