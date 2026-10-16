import asyncio
from collections import deque
import contextlib
import ipaddress
import logging
import uuid
import time
import json
from datetime import date,datetime,time as day_time,timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import httpx
from fastapi import FastAPI,HTTPException,Request,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse,Response,RedirectResponse,HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from urllib.parse import parse_qs
from .public_auth import PublicAuth, COOKIE_NAME, SESSION_SECONDS
from .config import Config,ROOT
from .store import Store
from .market import validate_latest, DataError
from .deriv import DerivProvider
from .public_crypto import BinanceProvider,CoinbaseProvider
from .quotes import Quotes
from .chart import ChartCache
from .telegram import Telegram,card
from .news import NewsFeed
from .service import Service
from .learning import RULES_VERSION, PIPELINE_VERSION
from .timeframes import Timeframe, TIMEFRAMES, max_age as timeframe_max_age, FETCH_TIMEOUT
from .http_limits import BodyLimitMiddleware
from .logging_setup import configure, event, register_secrets
from .diagnostics import build_archive
from .credentials import update as update_credentials, status as credentials_status
from .version import VERSION
from .strategies import STRATEGIES
from .manual_book import catalog as manual_catalog
from backend.ai.pipeline import AIPipeline
from backend.ai.providers import build_planetary_agents

StrategyKey = Literal[tuple(STRATEGIES)]

class AnalysisRequest(BaseModel):
    provider:Literal['deriv','binance','coinbase']
    strategy:StrategyKey|None=None
    assets:list[str]=Field(min_length=1,max_length=18)
    timeframe:Timeframe='M5'
    duration_seconds:Literal[60,120,180,300]=300

class MoneySettings(AnalysisRequest):
    hourly_limit:int=Field(default=10,ge=1,le=60,strict=True)
    simultaneous_limit:int=Field(default=2,ge=1,le=2,strict=True)
    loss_pause_after:int=Field(default=2,ge=1,le=10,strict=True)
    pause_minutes:int=Field(default=15,ge=1,le=120,strict=True)
    minimum_score:int=Field(default=70,ge=70,le=100,strict=True)
    stake_brl:float|None=Field(default=None,gt=0,le=1000000,multiple_of=0.01,strict=True,allow_inf_nan=False)
    payout_pct:float|None=Field(default=None,gt=0,le=100,multiple_of=0.01,strict=True,allow_inf_nan=False)


def create_app(config=None,provider=None,telegram=None,credentials_path=None,clock=time.time):
    public_auth=PublicAuth.from_env()
    @asynccontextmanager
    async def lifespan(app):
        configure()
        event('backend.starting')
        try:
            cfg=(config or Config.load()).validate()
        except Exception:
            event('backend.configuration_failed',level=logging.ERROR,exc_info=True)
            raise
        # One backend process per DB. Persistent SQLite lease handles concurrent requests.
        lockfile=None
        if cfg.database!=':memory:':
            Path(cfg.database).parent.mkdir(parents=True,exist_ok=True)
            lockfile=open(cfg.database+'.lock','a+b')
            try:
                import os
                if os.name=='nt':
                    import msvcrt
                    lockfile.write(b'0');lockfile.flush();lockfile.seek(0)
                    msvcrt.locking(lockfile.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                event('backend.database_locked',level=logging.ERROR)
                lockfile.close()
                raise RuntimeError('Já existe um backend usando este banco. Use um único processo.') from None
        logging.getLogger('httpx').setLevel(logging.CRITICAL)
        logging.getLogger('httpcore').setLevel(logging.CRITICAL)
        async with httpx.AsyncClient(timeout=10,follow_redirects=False) as client:
            try: store=Store(cfg.database)
            except Exception:
                event('backend.database_failed',level=logging.ERROR,exc_info=True)
                if lockfile: lockfile.close()
                raise
            service=None;worker=None
            try:
                if provider is not None:
                    providers=None
                elif cfg.asset_scope in ('forex','crypto_binary'):
                    providers={'deriv':DerivProvider(cfg)}
                else:
                    providers={'deriv':DerivProvider(cfg),'binance':BinanceProvider(client),'coinbase':CoinbaseProvider(client)}
                ai_pipeline=AIPipeline(build_planetary_agents(cfg,client))
                service=Service(cfg,store,provider or providers[cfg.provider],telegram or Telegram(cfg,client),providers=providers,automatic_only=True,automatic_sources=provider is None,clock=clock,ai_pipeline=ai_pipeline)
                store.recover(service.clock())
                service.recover_entry_watches()
                app.state.service=service
                app.state.news=NewsFeed(client)
                async def maintenance():
                    while True:
                        try: await service.tick()
                        except Exception: event('maintenance.failed',level=logging.ERROR,exc_info=True)
                        await asyncio.sleep(2)
                worker=asyncio.create_task(maintenance())
                register_secrets(cfg.telegram_token,cfg.telegram_chat,cfg.admin_token,cfg.gemini_api_key,cfg.groq_api_key,cfg.nvidia_api_key)
                event('backend.configuration',assets=cfg.assets,telegram_configured=service.telegram.configured)
                event('backend.ready',provider=service.provider_key,strategy=service.strategy_key,
                      limits=service.store.risk_settings(),rules_version=RULES_VERSION,pipeline_version=PIPELINE_VERSION)
                yield
            finally:
                event('backend.stopping')
                if worker:
                    worker.cancel()
                    with contextlib.suppress(asyncio.CancelledError): await worker
                if service:
                    await service.price_streams.close()
                    tasks=list(service.tasks)
                    for task in tasks: task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    if service.research:await asyncio.to_thread(service.research.close)
                    if service.observer:await asyncio.to_thread(service.observer.close)
                await asyncio.to_thread(store.close)
                if lockfile: lockfile.close()
                event('backend.stopped')

    app=FastAPI(title='AURORA',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # Never echo credentials/input; NaN/Infinity cannot break JSON serialization.
        return JSONResponse(status_code=422,content={'detail':[
            {'loc':list(e['loc']),'type':e['type'],'msg':'Valor inválido para este campo.'}
            for e in exc.errors()]})

    request_windows = {'read': deque(), 'write': deque()}
    login_attempts=deque()

    @app.middleware('http')
    async def guard(request,call_next):
        # Authorization follows the ASGI routing path. request.url is rebuilt
        # using the untrusted Host header and must never select an exemption.
        path=request.scope.get('path','')
        if public_auth.enabled:
            # Liveness has no state or secrets; platform checks may use an internal Host.
            if path == '/healthz':
                return await call_next(request)
            if request.headers.get('host','').lower() != public_auth.hostname.lower():
                return Response('Host não permitido.',403)
        else:
            # Local development retains its original no-login, loopback-only policy.
            peer=request.client.host if request.client else ''
            try: local=ipaddress.ip_address(peer).is_loopback
            except ValueError: local=peer=='testclient' and config is not None
            if not local or request.url.hostname not in ('localhost','127.0.0.1','::1','testserver'):
                return Response('Painel disponível somente neste computador.',403)
        mutation=request.method not in ('GET','HEAD','OPTIONS')
        if mutation or path.startswith('/api/'):
            origin=request.headers.get('origin')
            if request.headers.get('sec-fetch-site') in ('cross-site','same-site'):
                return Response('Origem não permitida',403)
            if public_auth.enabled:
                # Strict Origin on all mutations, including login, prevents CSRF.
                if mutation and origin != public_auth.origin:
                    return Response('Origem não permitida',403)
                if origin and origin != public_auth.origin:
                    return Response('Origem não permitida',403)
            elif origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
                return Response('Origem não permitida',403)
        if public_auth.enabled and not (path in ('/login','/api/auth/login') or
                                        path.startswith('/static/')):
            if not public_auth.valid_cookie(request.cookies.get(COOKIE_NAME)):
                if path.startswith('/api/'):
                    return JSONResponse({'detail':'Faça login para acessar o painel.'},status_code=401,
                                        headers={'Cache-Control':'no-store'})
                return RedirectResponse('/login',status_code=303)
        if path.startswith('/api/'):
            now=time.monotonic()
            window=request_windows['write' if mutation else 'read']
            while window and now-window[0]>=60:window.popleft()
            if len(window)>=(60 if mutation else 240):
                return Response('Muitas requisições. Aguarde e tente novamente.',429,headers={'Retry-After':str(max(1,int(60-(now-window[0]))+1)),'Cache-Control':'no-store'})
            window.append(now)
        if mutation:
            length=request.headers.get('content-length','0') or '0'
            if not length.isdigit(): return Response('Tamanho inválido',400)
            if len(length)>10 or int(length)>8192:
                return Response('Requisição muito grande',413)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.middleware('http')
    async def diagnostics(request, call_next):
        started=time.monotonic(); request_id=uuid.uuid4().hex
        try:
            response=await call_next(request)
        except Exception:
            event('http.failed',level=logging.ERROR,exc_info=True,request_id=request_id,method=request.method,
                  route=getattr(request.scope.get('route'),'path','unmatched'))
            raise
        event('http.completed',request_id=request_id,method=request.method,
              route=getattr(request.scope.get('route'),'path','unmatched'),http_status=response.status_code,
              duration_ms=round((time.monotonic()-started)*1000,2))
        response.headers['X-Request-ID']=request_id
        return response

    @app.get('/healthz')
    async def healthz():
        return {'status':'ok'}

    @app.get('/login',response_class=HTMLResponse)
    async def login_form(request:Request):
        if not public_auth.enabled:
            raise HTTPException(404)
        if public_auth.valid_cookie(request.cookies.get(COOKIE_NAME)):
            return RedirectResponse('/',status_code=303)
        failed = request.query_params.get('error') == '1'
        error_message = '<p role="alert" class="error">Senha inválida. Tente novamente.</p>' if failed else ''
        return HTMLResponse('''<!doctype html><html lang="pt-BR"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AURORA IA · Acesso administrativo</title>
<link rel="stylesheet" href="/static/login.css"/></head><body><main><div class="brand">◉ AURORA IA</div>
<h1>Painel administrativo</h1><p>Entre para acessar seus sinais e configurações.</p>''' + error_message + '''<form action="/api/auth/login" method="post">
<label for="password">Senha de acesso</label><input id="password" name="password" type="password"
minlength="12" maxlength="256" autocomplete="current-password" required autofocus/>
<button type="submit">Entrar</button></form><small>Conexão protegida · Sessão de duração limitada</small>
</main></body></html>''')

    @app.post('/api/auth/login')
    async def authenticate(request:Request):
        if not public_auth.enabled:
            raise HTTPException(404)
        now=time.monotonic()
        while login_attempts and now-login_attempts[0]>300:
            login_attempts.popleft()
        if len(login_attempts)>=10:
            return Response('Muitas tentativas. Aguarde alguns minutos.',429,
                            headers={'Retry-After':'300','Cache-Control':'no-store'})
        if not request.headers.get('content-type','').startswith('application/x-www-form-urlencoded'):
            raise HTTPException(415)
        body=await request.body()
        if len(body)>1024:
            raise HTTPException(413)
        try:
            fields=parse_qs(body.decode('utf-8'),strict_parsing=True,max_num_fields=2)
            supplied=fields.get('password',[''])[0]
        except (UnicodeDecodeError,ValueError):
            supplied=''
        if not 12<=len(supplied)<=256 or not public_auth.correct_password(supplied):
            login_attempts.append(now)
            return RedirectResponse('/login?error=1',status_code=303)
        login_attempts.clear()
        response=RedirectResponse('/',status_code=303)
        response.set_cookie(COOKIE_NAME,public_auth.new_cookie(),secure=True,httponly=True,
                            samesite='strict',max_age=SESSION_SECONDS,path='/')
        return response

    @app.post('/api/auth/logout')
    async def logout():
        if not public_auth.enabled:
            raise HTTPException(404)
        response=RedirectResponse('/login',status_code=303)
        response.delete_cookie(COOKIE_NAME,path='/',secure=True,httponly=True,samesite='strict')
        return response

    @app.get('/api/auth/status')
    async def auth_status():
        return {'public_access':public_auth.enabled,'authenticated':True}

    quotes=Quotes()

    @app.get('/api/instruments')
    async def catalogue(source:Literal['deriv','binance','coinbase']|None=None,
            market:Literal['forex','crypto']|None=None,q:str=Query('',max_length=80),
            page:int=Query(1,ge=1,le=100000),page_size:int=Query(6,ge=1,le=24)):
        from .catalogue import instruments
        rows=await instruments(app.state.service,source=source,market=market)
        rows=[r for r in rows if (source is None or r['source_key']==source) and
              (market is None or r['market']==market) and
              (not q or q.casefold() in r['symbol'].casefold() or q.casefold() in r['name'].casefold())]
        return {'items':rows[(page-1)*page_size:page*page_size],'total':len(rows),'page':page,'page_size':page_size,
                'note':'Lista suportada pelo código; disponibilidade e preços exigem confirmação pública. Visualizar não habilita análise.'}

    @app.get('/api/quote')
    async def asset_quote(asset:str,source:Literal['deriv','binance','coinbase']|None=None):
        service=app.state.service
        from .catalogue import resolve
        source_key,provider=resolve(service,source,asset)
        return {**await quotes.get(provider,asset),'source_key':source_key}

    charts=ChartCache()

    @app.get('/api/chart')
    async def chart(asset:str,timeframe:Timeframe='M5',source:Literal['deriv','binance','coinbase']|None=None):
        service=app.state.service
        from .catalogue import resolve
        source_key,provider=resolve(service,source,asset)
        if timeframe not in getattr(provider,'timeframes',TIMEFRAMES):raise HTTPException(422,'Período não habilitado nesta fonte.')
        try:
            # One budget covers queueing, availability check and candles together.
            async with asyncio.timeout(FETCH_TIMEOUT):
                return await charts.get(provider,source_key,asset,timeframe)
        except (DataError,asyncio.TimeoutError) as error:
            event('chart.failed',asset=asset,timeframe=timeframe,code=getattr(error,'code','timeout'))
            raise HTTPException(503,str(error) if isinstance(error,DataError) else 'Tempo limite da fonte excedido.') from None

    @app.get('/api/news')
    async def news(): return await app.state.news.get()

    @app.get('/api/manual/strategies')
    def manual_strategies():
        return manual_catalog()

    @app.get('/api/state')
    async def state(): return app.state.service.snapshot()

    @app.get('/api/strategy-review')
    async def strategy_review(page:int=Query(1,ge=1,le=1000000),page_size:int=Query(8,ge=1,le=20),
            source:Literal['','deriv','binance','coinbase']='',ratio:Literal['','50','61.8','78.6','unknown']=''):
        from .strategy_review import report
        service=app.state.service
        return await asyncio.to_thread(report,service.store,service.clock(),page=page,page_size=page_size,
                                       source=source,ratio=ratio)

    @app.get('/api/credentials')
    async def credential_status():
        return credentials_status(app.state.service.config)

    @app.put('/api/credentials')
    async def credentials(request:Request):
        if public_auth.enabled:
            raise HTTPException(403,'Na hospedagem pública, configure as credenciais nas variáveis privadas do servidor.')
        service=app.state.service
        try: changes=await request.json()
        except Exception: raise HTTPException(422,'Formato inválido.') from None
        if service.scan['active'] or service.telegram_check_lock.locked() or service.analysis_active or service.tasks or service.store.busy(service.clock()) or any(s.get('telegram',{}).get('status') in ('pendente','enviando','repetir') for s in service.store.active()):
            raise HTTPException(409,'Aguarde a análise, envio ou apuração terminar antes de alterar as conexões.')
        try: update_credentials(credentials_path or ROOT/'.env',service.config,changes)
        except ValueError:
            event('credentials.rejected')
            raise HTTPException(422,'Configuração inválida. Confira os formatos; token e chat do Telegram devem ser configurados ou removidos juntos.') from None
        except OSError:
            event('credentials.save_failed',level=logging.ERROR,exc_info=True)
            raise HTTPException(503,'Não foi possível salvar as conexões no servidor.') from None
        service.feed={'status':'não verificado','detail':'Credenciais atualizadas. Verifique a conexão.'}
        service.telegram_health={'status':'não verificado' if service.telegram.configured else 'não configurado'}
        register_secrets(service.config.telegram_token,service.config.telegram_chat,service.config.admin_token,service.config.gemini_api_key,service.config.groq_api_key,service.config.nvidia_api_key)
        event('credentials.updated')
        return credentials_status(service.config)

    @app.post('/api/analyze',status_code=202)
    async def analyze(body:AnalysisRequest):
        service=app.state.service
        if not service.automatic_sources and body.provider!=service.provider_key:
            raise HTTPException(409,'Fonte alterada em outra sessão. Atualize o painel.')
        try:
            a=service.start_scan(body.assets,body.duration_seconds,body.timeframe,preview=True) if service.automatic_sources else service.start(body.assets,body.duration_seconds,body.timeframe,preview=True)
        except ValueError as e: raise HTTPException(422,str(e))
        if a is None: raise HTTPException(409,'Uma análise já está em andamento.')
        return a

    @app.post('/api/analyze/stop')
    async def stop_analyze():
        service=app.state.service
        if service.scan['active']:
            await service.stop_scan()
            stopped=True
        else: stopped=await service.stop_analysis()
        return {'stopped':stopped,'message':'Análise interrompida. Sinais já emitidos permanecem no histórico.' if stopped else 'Nenhuma análise ativa.'}

    @app.post('/api/scan/start',status_code=202)
    async def start_scan(body:AnalysisRequest):
        service=app.state.service
        if not service.automatic_sources and body.provider!=service.provider_key:
            raise HTTPException(409,'Configuração alterada em outra aba. Atualize o painel.')
        try: result=service.start_scan(body.assets,body.duration_seconds,body.timeframe)
        except ValueError as e: raise HTTPException(422,str(e))
        if result is None: raise HTTPException(409,'Uma sessão ou consulta já está em andamento.')
        return result

    @app.post('/api/scan/stop')
    async def stop_scan(): return await app.state.service.stop_scan()

    @app.get('/api/diagnostics/logs')
    async def logs_export():
        snapshot=await diagnostics_export()
        payload=await asyncio.to_thread(build_archive,ROOT/'logs',snapshot)
        return Response(payload,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="AURORA-DIAGNOSTICO.zip"'})

    class FrontendEvent(BaseModel):
        kind:Literal['error','unhandledrejection','resource_error']
        line:int=Field(default=0,ge=0,le=1000000)
        column:int=Field(default=0,ge=0,le=1000000)

    @app.post('/api/diagnostics/frontend')
    async def frontend_event(body:FrontendEvent):
        # Do not accept browser messages/URLs, which can contain unsaved secrets.
        event('frontend.failed',level=logging.ERROR,kind=body.kind,stage=f'line={body.line}, column={body.column}')
        return {'status':'recorded'}

    @app.get('/api/diagnostics')
    async def diagnostics_export():
        service=app.state.service
        snapshot=service.snapshot()
        # Explicit allow-list: no .env, credentials, raw exceptions or account data.
        return {'version':VERSION,'server_time':snapshot['server_time'],'provider':snapshot['provider'],
            'settings':snapshot['settings'],'feed':snapshot['feed'],'scan':snapshot['scan'],'learning':snapshot['learning'],
            'telegram':snapshot['telegram'],'analyses':snapshot['analyses'][:10],'timeframes':snapshot['timeframes']}

    @app.put('/api/settings')
    async def settings(body:MoneySettings):
        service=app.state.service
        if service.scan['active'] or service.analysis_active or service.store.busy(service.clock()): raise HTTPException(409,'Pare a análise antes de alterar as configurações.')
        if len(set(body.assets))!=len(body.assets) or any(a not in service.available_assets for a in body.assets):
            raise HTTPException(422,'Ativos inválidos.')
        if body.timeframe not in service.available_timeframes:raise HTTPException(422,'Período não habilitado nesta fonte.')
        if not service.automatic_sources and body.provider!=service.provider_key:
            raise HTTPException(409,'Fonte alterada. Atualize o painel.')
        saved=body.model_dump()
        previous=service.settings()
        for key in ('stake_brl','payout_pct','hourly_limit','simultaneous_limit','loss_pause_after','pause_minutes','minimum_score'):
            if key not in body.model_fields_set:saved[key]=previous.get(key)
        if (saved['stake_brl'] is None)!=(saved['payout_pct'] is None):
            raise HTTPException(422,'Informe entrada e payout juntos, ou deixe ambos vazios.')
        service.store.save_settings({**saved,'provider':service.provider_key,'strategy':service.strategy_key,
                                     'selection_observation':previous['selection_observation'],'source_mode':previous['source_mode']})
        return service.settings()

    class ConnectionRequest(BaseModel):
        provider:Literal['deriv','binance','coinbase']|None=None

    @app.put('/api/connection')
    async def connection(body:ConnectionRequest):
        try: app.state.service.select_provider(body.provider)
        except ValueError as e: raise HTTPException(422,str(e))
        except RuntimeError as e: raise HTTPException(409,str(e))
        return app.state.service.snapshot()

    class StrategyRequest(BaseModel):
        strategy:StrategyKey

    @app.put('/api/strategy')
    async def strategy(body:StrategyRequest):
        try: app.state.service.select_strategy(body.strategy)
        except ValueError as e: raise HTTPException(422,str(e))
        except RuntimeError as e: raise HTTPException(409,str(e))
        return app.state.service.snapshot()

    @app.post('/api/connection/check')
    async def check_connection(body:ConnectionRequest):
        service=app.state.service
        if not service.automatic_sources and body.provider not in (None,service.provider_key): raise HTTPException(409,'Atualize a fonte selecionada.')
        owner='connection-check-'+str(uuid.uuid4())
        if service.scan['active'] or service.analysis_active or not service.store.acquire(owner,service.clock()): raise HTTPException(409,'Pare a sessão antes de verificar a conexão.')
        try:
            if service.automatic_sources:
                from .source_routing import plans, summarize_health
                timeframe=service.settings()['timeframe'];interval=TIMEFRAMES[timeframe]
                max_age=timeframe_max_age(timeframe,service.config)
                async def check(plan):
                    key=plan['source_key'];provider=service.providers[key];asset=plan['assets'][0]
                    try:
                        async def fetch():
                            if hasattr(provider,'check_asset'): await provider.check_asset(asset)
                            return await provider.candles(asset,interval)
                        rows=await asyncio.wait_for(fetch(),FETCH_TIMEOUT)
                        closed=validate_latest(rows,service.clock(),max_age,interval)
                        result={'status':'disponível','detail':asset+': candles validados','data_at':closed[-1].t+interval}
                    except (DataError,asyncio.TimeoutError) as error:
                        result={'status':'indisponível','detail':str(error) or 'Tempo limite da fonte excedido.','code':getattr(error,'code','timeout')}
                    service.source_health[key]={**result,'checked_at':service.clock(),'max_age':max_age}
                    return {'source_key':key,**result}
                results=await asyncio.gather(*(check(plan) for plan in plans(service,service.settings()['assets'],service.settings()['timeframe'])))
                service.feed=summarize_health([{'source_key':r['source_key'],**service.source_health[r['source_key']]} for r in results],
                                              service.clock(),max_age)
                return service.feed
            async def probe():
                asset=service.settings()['assets'][0]
                if hasattr(service.provider,'check_asset'): await service.provider.check_asset(asset)
                timeframe=service.settings()['timeframe']
                interval=TIMEFRAMES[timeframe]
                max_age=timeframe_max_age(timeframe,service.config)
                rows=await service.provider.candles(asset,interval)
                closed=validate_latest(rows,service.clock(),max_age,interval)
                return {'status':'disponível','detail':f'{asset}: conexão e candles {timeframe} validados',
                        'timeframe':timeframe,'max_age':max_age,'checked_at':service.clock(),'data_at':closed[-1].t+interval}
            try: service.feed=await asyncio.wait_for(probe(),FETCH_TIMEOUT)
            except (DataError,asyncio.TimeoutError) as e:
                service.feed={'status':'indisponível','detail':str(e) or 'Tempo limite da fonte excedido.','code':getattr(e,'code','timeout')}
            event('provider.checked',provider=service.provider_key,status=service.feed['status'])
            return service.feed
        finally: service.store.release(owner)

    @app.post('/api/telegram/check')
    async def check():
        service=app.state.service
        if service.telegram_check_lock.locked(): raise HTTPException(409,'Um teste do Telegram já está em andamento.')
        if service.telegram_check_at is not None and time.monotonic()-service.telegram_check_at<15:
            raise HTTPException(429,'Aguarde 15 segundos entre testes e confira o chat antes de repetir.')
        async with service.telegram_check_lock:
            service.telegram_check_at=time.monotonic()
            event('telegram.test_started')
            try: service.telegram_health=await asyncio.wait_for(service.telegram.check(),12)
            except Exception:
                service.telegram_health={'status':'incerto','detail':'Teste sem confirmação. Confira o chat antes de repetir; sem reenvio automático.'}
            event('telegram.test_finished',status=service.telegram_health['status'])
            return service.telegram_health

    @app.get('/api/history/export-day')
    async def export_day(day:date):
        tz=ZoneInfo('America/Sao_Paulo')
        if day==date.max:raise HTTPException(422,'Data inválida.')
        start=datetime.combine(day,day_time.min,tzinfo=tz).timestamp()
        end=datetime.combine(day+timedelta(days=1),day_time.min,tzinfo=tz).timestamp()
        store=app.state.service.store
        signals,confirmations=await asyncio.to_thread(store.export_period,start,end)
        payload={'date_brt':day.isoformat(),'timezone':'America/Sao_Paulo','signals':signals,
                 'manual_confirmations':confirmations,
                 'note':'Resultados indicativos e declarações manuais preservados separadamente. Nenhum registro removido.'}
        return Response(json.dumps(payload,ensure_ascii=False,allow_nan=False),media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="AURORA-historico-{day.isoformat()}.json"'})

    @app.get('/api/history/days')
    async def history_days():
        return {'days':app.state.service.store.history_days(),'timezone':'America/Sao_Paulo',**app.state.service.store.history_facets()}

    @app.get('/api/history/daily-summary')
    async def history_daily_summary(day:date):
        if day==date.max: raise HTTPException(422,'Data inválida.')
        return await asyncio.to_thread(app.state.service.store.history_day_summary,day)

    class ClearHistoryRequest(BaseModel):
        scope:Literal['day','month','all']
        period:str|None=Field(default=None,max_length=10)
        confirmed:bool=Field(strict=True)

    @app.post('/api/history/clear')
    async def clear_history(body:ClearHistoryRequest):
        if not body.confirmed:raise HTTPException(422,'Confirme a limpeza do histórico.')
        start=end=None
        tz=ZoneInfo('America/Sao_Paulo')
        try:
            if body.scope=='all':
                if body.period is not None:raise ValueError()
            elif body.scope=='day':
                target=date.fromisoformat(body.period or '')
                if target.isoformat()!=body.period or target==date.max:raise ValueError()
                start=datetime.combine(target,day_time.min,tzinfo=tz).timestamp()
                end=datetime.combine(target+timedelta(days=1),day_time.min,tzinfo=tz).timestamp()
            else:
                target=date.fromisoformat((body.period or '')+'-01')
                if target.strftime('%Y-%m')!=body.period or target.year>=9999:raise ValueError()
                following=date(target.year+(target.month==12),1 if target.month==12 else target.month+1,1)
                start=datetime.combine(target,day_time.min,tzinfo=tz).timestamp()
                end=datetime.combine(following,day_time.min,tzinfo=tz).timestamp()
        except (ValueError,OverflowError):raise HTTPException(422,'Informe dia AAAA-MM-DD ou mês AAAA-MM válido.') from None
        service=app.state.service
        return service.store.clear_history(body.scope,body.period,service.clock(),start,end)

    @app.get('/api/history')
    async def history(page:int=Query(1,ge=1,le=1000000),page_size:int=Query(20,ge=1,le=50),
            asset:str|None=Query(None,max_length=20),source:Literal['twelvedata','deriv','binance','coinbase']|None=None,
            direction:Literal['CALL','PUT']|None=None,
            state:Literal['aguardando entrada','em andamento','encerrado','cancelado']|None=None,
            start:float|None=Query(None,ge=0,le=32503680000),end:float|None=Query(None,ge=0,le=32503680000),
            start_date:date|None=None,end_date:date|None=None,
            snapshot_id:int|None=Query(None,ge=0,le=9223372036854775807),strategy:str|None=Query(None,max_length=80)):
        if (start_date is not None or end_date is not None) and (start is not None or end is not None):
            raise HTTPException(422,'Use datas de Brasília ou timestamps UTC, sem misturar.')
        tz=ZoneInfo('America/Sao_Paulo')
        if start_date: start=datetime.combine(start_date,day_time.min,tzinfo=tz).timestamp()
        if end_date:
            if end_date==date.max: raise HTTPException(422,'Data final inválida.')
            end=datetime.combine(end_date+timedelta(days=1),day_time.min,tzinfo=tz).timestamp()
        if start is not None and end is not None and start>=end: raise HTTPException(422,'Período inválido.')
        return app.state.service.store.history(page,page_size,asset,source,direction,state,start,end,snapshot_id,strategy)

    @app.get('/api/aurora/metrics')
    @app.get('/api/apex/metrics')
    async def apex_metrics():
        return {'groups':await asyncio.to_thread(app.state.service.store.apex_metrics),
                'note':'Taxa observada apenas a partir de 30 resultados WIN/LOSS indicativos por grupo; não é previsão de acerto.'}

    @app.get('/api/validation')
    async def validation():
        service=app.state.service
        now=service.clock()
        report=await asyncio.to_thread(service.store.validation_report,now)
        report['observation']=await asyncio.to_thread(service.observer.report,now) if service.observer else {'status':'disabled','sessions':[]}
        return report

    @app.get('/api/validation/planetary-performance')
    async def planetary_performance(limit: int = Query(5000, ge=1, le=10000)):
        service = app.state.service
        return await asyncio.to_thread(service.store.planetary_performance, service.clock(), limit)

    @app.get('/api/validation/decisions')
    async def decisions(limit:int=Query(20,ge=1,le=100),after:int=Query(0,ge=0)):
        return app.state.service.store.decisions(limit,after)

    @app.get('/api/validation/summary')
    async def validation_summary():
        from .selection_context import feature_status
        service=app.state.service;now=service.clock()
        counts=await asyncio.to_thread(service.store.validation_counts,now)
        observation=(await asyncio.to_thread(service.observer.overview,now)) if service.observer else {'status':'disabled','sessions':[]}
        observation={**observation,'collection_enabled':service.observation_enabled,
                     'sessions':[{'session':s} for s in observation['sessions']]}
        return {'at':now,'counts':counts,'features':feature_status(),'monitoring':service.scan['active'] or service.analysis_active,
                'history_visibility':{'archived':service.store.archived_history_count()},'observation':observation,
                'metrics_version':'1.25.0-paged-1'}

    @app.get('/api/validation/groups')
    async def validation_groups(scope:Literal['bot','manual','experimental']='bot',page:int=Query(1,ge=1,le=1000000),
            page_size:int=Query(10,ge=1,le=50),source:str=Query('',max_length=40),strategy:str=Query('',max_length=80),
            q:str=Query('',max_length=160),session_id:str=Query('',max_length=128),selector:str=Query('',max_length=80)):
        service=app.state.service;now=service.clock()
        if scope!='experimental':
            return await asyncio.to_thread(service.store.validation_page,now,scope=scope,page=page,page_size=page_size,
                                           source=source,strategy=strategy,query=q)
        report=(await asyncio.to_thread(service.observer.report,now,False,session_id)) if service.observer and session_id else {'sessions':[]}
        groups=report['sessions'][0]['groups'] if report['sessions'] else []
        facets={k:sorted({g[k] for g in groups}) for k in ('source_key','strategy','selector')}
        filtered=[g for g in groups if (not source or g['source_key']==source) and (not strategy or g['strategy']==strategy)
                  and (not selector or g['selector']==selector)
                  and (not q.strip() or q.strip().lower() in json.dumps({k:g.get(k) for k in
                       ('source_key','asset','strategy','apex_setup','timeframe','regime','selector','configuration_id')},ensure_ascii=False).lower())]
        actual=min(page,max(1,(len(filtered)+page_size-1)//page_size))
        return {'items':filtered[(actual-1)*page_size:actual*page_size],'total':len(filtered),'page':actual,'page_size':page_size,
                'facets':facets,'scope':scope,'pagination':'server','at':now}

    @app.get('/api/validation/setups')
    async def winning_setups(scope:Literal['bot','manual']='bot',period:Literal['all','day','month']='all',
            source:str=Query('',max_length=40),pipeline:str=Query('current',max_length=80),
            asset:str=Query('',max_length=80),timeframe:str=Query('',max_length=10),
            duration_seconds:float|None=Query(None,gt=0,le=31536000),limit:int=Query(5,ge=1,le=10)):
        service=app.state.service
        if scope=='bot' and duration_seconds not in (None,60,120,180,300):
            raise HTTPException(422,'Duração operacional não habilitada.')
        return await asyncio.to_thread(service.store.setup_ranking,service.clock(),scope=scope,period=period,
            source=source,pipeline=pipeline,asset=asset,timeframe=timeframe,duration_seconds=duration_seconds,limit=limit)

    @app.get('/api/validation/session')
    async def validation_session(session_id:str=Query(...,min_length=1,max_length=128)):
        service=app.state.service
        report=await asyncio.to_thread(service.observer.report,service.clock(),False,session_id) if service.observer else {'sessions':[]}
        if not report['sessions']:return {'session':None}
        # Groups have their own page endpoint; phase groups keep the held-out test sealed.
        item=report['sessions'][0]
        return {k:v for k,v in item.items() if k not in ('groups','phases')}

    @app.get('/api/validation/target')
    async def accuracy_target(session_id:str=Query(...,min_length=1,max_length=128),
            phase:Literal['development','validation','test']='validation',page:int=Query(1,ge=1,le=1000000),
            page_size:int=Query(10,ge=1,le=50),selector:str=Query('',max_length=80)):
        service=app.state.service;now=service.clock()
        report=await asyncio.to_thread(service.observer.report,now,False,session_id) if service.observer else {'sessions':[]}
        item=report['sessions'][0] if report['sessions'] else None
        stage=next((p for p in item['phases'] if p['phase']==phase),None) if item else None
        groups=stage['target_groups'] if stage else []
        selectors=sorted({g['selector'] for g in groups})
        filtered=[g for g in groups if not selector or g['selector']==selector]
        actual=min(page,max(1,(len(filtered)+page_size-1)//page_size))
        return {'policy':item['session']['protocol'].get('accuracy_goal') if item else None,
                'status':stage['target_status'] if stage else 'no_session',
                'phase':phase,'complete':stage['complete'] if stage else False,
                'start_at':stage['start_at'] if stage else None,'end_at':stage['end_at'] if stage else None,
                'items':filtered[(actual-1)*page_size:actual*page_size], 'total':len(filtered),
                'page':actual,'page_size':page_size,'selectors':selectors,
                'automatic_promotion':False,'final_test_status':'sealed','at':now}

    class ObservationRequest(BaseModel):
        enabled:bool=Field(strict=True)

    @app.put('/api/validation/observation')
    async def observation_settings(body:ObservationRequest):
        try:return app.state.service.set_observation(body.enabled)
        except RuntimeError as error:raise HTTPException(409,str(error)) from None

    class ManualConfirmation(BaseModel):
        executed:bool=Field(strict=True)
        broker:str=Field(min_length=1,max_length=80)
        result:Literal['WIN','LOSS','EMPATE']|None=None
        entry_at:float|None=Field(default=None,gt=0,le=32503680000,allow_inf_nan=False,strict=True)
        expiry_at:float|None=Field(default=None,gt=0,le=32503680000,allow_inf_nan=False,strict=True)
        entry_price:float|None=Field(default=None,gt=0,allow_inf_nan=False,strict=True)
        expiry_price:float|None=Field(default=None,gt=0,allow_inf_nan=False,strict=True)
        payout_pct:float|None=Field(default=None,gt=0,le=100,allow_inf_nan=False,strict=True)
        note:str=Field(default='',max_length=500)

    @app.post('/api/signals/{sid}/manual-confirmation')
    async def manual_confirmation(sid:str,body:ManualConfirmation):
        service=app.state.service;now=service.clock()
        signal=service.store.signal(sid)
        if not signal:raise HTTPException(404,'Sinal não encontrado.')
        if body.executed and (body.result is None or now<signal['expiry_at']):
            raise HTTPException(422,'Declare o resultado somente após a expiração do sinal.')
        if not body.executed and body.result is not None:raise HTTPException(422,'Sinal não executado não tem resultado de operação.')
        if ((body.entry_at is None)!=(body.expiry_at is None) or
                (body.entry_at is not None and not body.entry_at<body.expiry_at<=now)):
            raise HTTPException(422,'Horários reais devem ser informados juntos, em ordem e sem dados futuros.')
        if (body.entry_price is None)!=(body.expiry_price is None):raise HTTPException(422,'Informe os dois preços ou deixe ambos vazios.')
        if not body.executed and any(v is not None for v in (body.entry_at,body.expiry_at,body.entry_price,body.expiry_price,body.payout_pct)):
            raise HTTPException(422,'Não atribua preços, horários ou payout a uma operação não executada.')
        return service.store.confirm_manual(sid,body.model_dump(),now)

    @app.get('/api/signals/{sid}')
    async def detail(sid:str):
        service=app.state.service
        s=service.store.signal(sid)
        if not s: raise HTTPException(404,'Sinal não encontrado.')
        return {'signal':s,'events':service.store.events(sid),'manual_confirmations':service.store.manual_confirmations(sid)}

    @app.get('/api/signals/{sid}/card')
    async def get_card(sid:str):
        s=app.state.service.store.signal(sid)
        if not s: raise HTTPException(404,'Sinal não encontrado.')
        return Response(card(s),media_type='image/png')

    @app.post('/api/signals/{sid}/cancel')
    async def cancel(sid:str):
        service=app.state.service;s=service.store.signal(sid)
        if not s: raise HTTPException(404,'Sinal não encontrado.')
        if s['state']!='aguardando entrada' or service.clock()>=s['entry_at']:
            raise HTTPException(409,'Apenas sinais aguardando entrada podem ser cancelados.')
        if s['telegram']['status'] in ('enviando','enviado','incerto'):
            raise HTTPException(409,'Sinal já enviado ou envio incerto. Cancelamento local não notificaria o Telegram.')
        return service.store.patch(sid,{'state':'cancelado','result':'Resultado indisponível','settlement_pending':False},'cancelado',service.clock())

    @app.get('/')
    @app.get('/analise')
    @app.get('/historico')
    @app.get('/conexoes')
    @app.get('/configuracoes')
    @app.get('/aprendizado')
    @app.get('/noticias')
    @app.get('/sinais')
    @app.get('/calendario')
    @app.get('/telegram')
    @app.get('/universo-ia')
    @app.get('/validacao')
    async def index(): return FileResponse(ROOT/'frontend/index.html')
    app.mount('/static',StaticFiles(directory=ROOT/'frontend'),name='static')
    app.add_middleware(BodyLimitMiddleware,limit=8192)
    return app

app=create_app()
