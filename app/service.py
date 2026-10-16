import asyncio
import hashlib
import math
import logging
import time
import uuid
from datetime import datetime, timezone
from .learning import PIPELINE_VERSION, RULES_VERSION, profiles, operational_records
from .analysis_preview import asset_previews, decision_summary
from .learning_feedback import state as learning_state, feedback as outcome_feedback
from .version import VERSION
from .manual_price_action import structural_context as manual_context, RULES as MANUAL_RULES
from .logging_setup import event, CONTEXT
from .credentials import status as credentials_status
from .explanations import build_explanation, EXPLANATION_VERSION
from backend.ai.pipeline import AIPipeline
from .delivery_policy import SEND_TIMEOUT_SECONDS, can_start, classify_ack
from .entry_timing import next_entry, describe_entry
from .strategies import STRATEGIES
from .strategies.htf import confirm
from .strategies.quality import entry_guard
from .strategies.helpers import atr
from .timeframes import TIMEFRAMES, max_age as timeframe_max_age, FETCH_TIMEOUT, CONTRACT_TIMEOUT

# Standalone candlestick integration seam; production uses the manual pool.
evaluate = STRATEGIES['candlestick_sr']
from .market import validate, validate_latest, DataError, valid_candle

class Service:
    def __init__(self,config,store,provider,telegram,clock=time.time,providers=None,automatic_only=False,automatic_sources=False,ai_pipeline=None):
        self.config,self.store,self.provider,self.telegram,self.clock=config,store,provider,telegram,clock
        self.providers=providers or {'deriv':provider}
        self.automatic_sources=automatic_sources
        self.source_health={}
        saved_settings=store.settings({})
        self.provider_key=saved_settings.get('provider',getattr(config,'provider','deriv'))
        if self.provider_key not in self.providers:
            self.provider_key='deriv'
        if saved_settings.get('provider')!=self.provider_key:
            store.save_settings({**saved_settings,'provider':self.provider_key})
            event('provider.migrated',provider=self.provider_key)
        self.provider=self.providers[self.provider_key]
        self.automatic_only=automatic_only
        if automatic_sources and saved_settings.get('source_mode')!='automatic':
            supported=self.available_assets
            # Migrating source routing does not authorize widening the user's
            # monitored instruments or resetting a compatible analysis period.
            initial_assets=[asset for asset in saved_settings.get('assets',[]) if asset in supported]
            initial_assets=initial_assets or self._default_assets()
            previous_timeframe=saved_settings.get('timeframe')
            timeframe=previous_timeframe if previous_timeframe in self.available_timeframes else 'M5'
            store.save_settings({**store.settings({}),'source_mode':'automatic',
                                 'assets':initial_assets,'timeframe':timeframe})
        if automatic_only:
            store.save_settings({**store.settings({}),'strategy':'scan_all'})
        saved_strategy=store.settings({}).get('strategy',config.strategy)
        self.strategy_key=saved_strategy if saved_strategy in STRATEGIES else 'scan_all'
        if saved_strategy != self.strategy_key:
            store.save_settings({**store.settings({}),'strategy':self.strategy_key})
            event('strategy.migrated',strategy=self.strategy_key,reason='Estratégia anterior desativada; Price Action do manual habilitado.')
        self.learning=learning_state({},self.clock(),store.latest_learning_feedback(self.clock()))
        self.learning_tasks=set()
        self.learning_retry_at={}
        self.tasks=set()
        self.delivering=set()
        self.analysis_active=False
        self.current_analysis_task=None
        self.current_analysis=None
        self.ai_pipeline=ai_pipeline or AIPipeline()
        self.ai_status={'status':'unavailable','detail':'Nenhum agente ou provedor AI está configurado; a camada não participa das análises operacionais.',
                       'last_decision':None,'last_votes':[],'updated_at':self.clock(),
                       'context':None,'analysis_context':None}
        self.scan_task=None
        self.scan_stopping=False
        self.scan_stop_lock=asyncio.Lock()
        self.scan={'active':False,'message':'Sessão parada. Clique em Iniciar para buscar entradas.'}
        self.feed={'status':'não verificado','detail':'O monitoramento começa quando você clicar em Iniciar.'}
        self.telegram_health={'status':'não verificado' if telegram.configured else 'não configurado'}
        self.tick_lock=asyncio.Lock()
        self.telegram_check_lock=asyncio.Lock()
        self.telegram_check_at=None
        self.last_settlement={}
        self.settling=set()
        self.revalidating=set()
        from .context_window import ContextWindow
        self.context_window=ContextWindow()
        self.intrabar_contexts={}
        self.intrabar_slots=asyncio.Semaphore(2)
        self.intrabar_lock=asyncio.Lock()
        self.intrabar={'state':'parado','watching_assets':0,'last_quotes':{},
                      'detail':'Preços intravela são acompanhados somente após Iniciar.'}
        self.pending_quotes={}
        from .price_stream import PriceStreams
        self.price_streams=PriceStreams(self.providers,self.clock,self.receive_stream_price,self.stream_gap)
        self.settlement_slots=asyncio.Semaphore(2)
        self.observer=None;self.observer_task=None;self.last_observer_tick=0
        from pathlib import Path
        database=getattr(config,'database',':memory:')
        observation_path=':memory:' if database==':memory:' else str(Path(database).with_suffix('.selection.db'))
        observation_enabled=saved_settings.get('selection_observation',getattr(config,'selection_observation',False))
        if observation_enabled or (observation_path!=':memory:' and Path(observation_path).is_file()):
            from pathlib import Path
            from .selection_observer import SelectionObserver
            self.observer=SelectionObserver(observation_path)
        if 'selection_observation' not in saved_settings and self.observer is not None:
            store.save_settings({**store.settings({}),'selection_observation':bool(observation_enabled)})
        self.research=None;self.research_task=None;self.last_research_tick=0
        if getattr(config,'experimental_collect',False):
            try:
                from pathlib import Path
                from .research_store import ResearchStore
                path=':memory:' if config.database==':memory:' else str(Path(config.database).with_suffix('.research.db'))
                self.research=ResearchStore(path)
            except Exception:event('experiment.storage_failed',status='failed',exc_info=True)

    async def analyze_with_ai(self, payload):
        if not isinstance(payload, dict):
            raise TypeError('payload de análise AI deve ser um dicionário.')
        self.ai_pipeline.adapter.validate_data(payload)
        interval={'M1':60,'M5':300}.get(payload.get('timeframe'))
        rows=payload.get('candles_m1' if interval==60 else 'candles_m5',[]) if interval else []
        last=rows[-1].get('t') if rows and isinstance(rows[-1],dict) else None
        last=last if type(last) is int else None
        context={key:payload.get(key) for key in ('ativo','timeframe','fonte','source_identity',
                                                'timestamp_utc','entrada_utc')}
        context['candle_at']=last
        analysis_context={'asset':context['ativo'],'source':context['fonte'],
            'source_identity':context['source_identity'],'timeframe':context['timeframe'],
            'timestamp_utc':context['timestamp_utc'],'entry_utc':context['entrada_utc'],
            'last_closed_candle_at':last,'data_at':last+interval if last is not None and interval else None}
        self.ai_status.update(context=context,analysis_context=analysis_context)
        if not self.ai_pipeline.orchestrator.agents:
            normalized=self.ai_pipeline.adapter.create_analysis_package(payload)
            self.ai_status.update(status='unavailable',
                detail='Nenhum agente ou provedor AI está configurado; nenhuma decisão foi aplicada ao sinal.',
                updated_at=self.clock(),last_decision=None,last_votes=[])
            return {'approved':False,'decision':{'direcao':None,'confianca':0.0,
                    'motivo':'agentes_nao_configurados'},'votes':[],'payload':normalized}
        self.ai_status.update(status='processing', detail='Aguardando consenso dos agentes planetários.',
                              updated_at=self.clock(),last_decision=None,last_votes=[])
        try:
            result = await self.ai_pipeline.process(payload)
        except asyncio.CancelledError:
            self.ai_status.update(status='unavailable',detail='Análise IA cancelada; nenhum consenso aplicado.',
                                  updated_at=self.clock(),last_decision=None,last_votes=[])
            raise
        except Exception:
            self.ai_status.update(status='unavailable',detail='Análise IA indisponível; nenhum consenso aplicado.',
                                  updated_at=self.clock(),last_decision=None,last_votes=[])
            raise
        decision = result.get('decision', {}) if isinstance(result, dict) else {}
        outcome = decision.get('direcao')
        valid_agents=sum(1 for vote in result.get('votes',[]) if vote.get('status')=='disponivel' and not vote.get('dados_insuficientes'))
        status=('approved' if result.get('approved') and outcome in {'CALL','PUT'} else 'rejected') if valid_agents>=3 else 'unavailable'
        self.ai_status.update(
            status=status,
            detail='Consenso AI concluído.' if status=='approved' else 'Consenso AI rejeitado pelo risco ou pela maioria.' if status=='rejected' else 'Menos de três planetas válidos; nenhum consenso operacional.',
            updated_at=self.clock(),
            last_decision={'direcao': outcome, 'confianca': decision.get('confianca', 0), 'motivo': decision.get('motivo')},
            last_votes=result.get('votes',[])
        )
        return result

    def ai_snapshot(self):
        snapshot=dict(self.ai_status)
        snapshot['agents']=[agent.status_snapshot() for agent in self.ai_pipeline.orchestrator.agents
                            if callable(getattr(agent,'status_snapshot',None))]
        return snapshot

    def production_ai_required(self):
        return self.automatic_sources and getattr(self.config,'asset_scope','legacy') in ('forex','crypto_binary')

    def block_unverified_intrabar(self):
        """The live callback cannot await LLMs without losing native tick coverage.

        Only the closed-candle path currently validates consensus and both
        broker proposals. Keep live-only entries fail-closed in production
        instead of publishing them with a declared payout or no AI decision.
        Existing signals still retain their independent live entry watches.
        """
        self.intrabar_contexts.clear()
        self.intrabar.update(state='bloqueado',watching_assets=0,
            code='intrabar_requires_verified_consensus',
            detail='Gatilhos intravela sem consenso IA e payout confirmado não são publicados. '
                   'Análises de candles fechados continuam com todas as validações.')

    def spawn(self,coro):
        task=asyncio.create_task(coro); self.tasks.add(task); task.add_done_callback(self.task_done)
        return task

    def task_done(self,task):
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            event('task.failed',level=logging.ERROR,exc_info=(type(task.exception()),task.exception(),task.exception().__traceback__))

    def historical_learning(self, now):
        # SQLite supplies compact, original quote proofs; heavy grouping cannot
        # stall ticks, Stop or HTTP polling on the event loop.
        return profiles(operational_records(self.store.learning_signals()),now)

    def _default_assets(self):
        available=self.available_assets
        if self.automatic_sources and self.config.asset_scope in ('forex','crypto_binary'):
            return [asset for asset in self.config.assets if asset in available]
        return available[:8] if self.automatic_sources else available

    def settings(self):
        saved=self.store.settings({})
        assets=[a for a in saved.get('assets',[]) if a in self.available_assets]
        defaults=self._default_assets()
        return {**self.store.risk_settings(),'provider':self.provider_key,'strategy':self.strategy_key,
            'assets':assets or defaults,
            'timeframe':saved.get('timeframe') if saved.get('timeframe') in self.available_timeframes else 'M5',
            'duration_seconds':saved.get('duration_seconds') if saved.get('duration_seconds') in (60,120,180,300) else 300,
            'stake_brl':saved.get('stake_brl'), 'payout_pct':saved.get('payout_pct'),
            'selection_observation':self.observation_enabled,
            'source_mode':'automatic' if self.automatic_sources else 'manual'}

    @property
    def observation_enabled(self):
        return self.store.settings({}).get('selection_observation',self.observer is not None)

    def set_observation(self,enabled):
        if self.scan['active'] or self.analysis_active or self.store.busy(self.clock()):
            raise RuntimeError('Pare a análise antes de alterar a coleta experimental.')
        if enabled and self.observer is None:
            from pathlib import Path
            from .selection_observer import SelectionObserver
            path=':memory:' if self.config.database==':memory:' else str(Path(self.config.database).with_suffix('.selection.db'))
            self.observer=SelectionObserver(path)
        self.store.save_settings({**self.settings(),'selection_observation':bool(enabled)})
        event('observation.collection_changed',enabled=enabled)
        return {'enabled':self.observation_enabled,'sent_to_telegram':False,'automatic_promotion':False}

    @property
    def available_assets(self):
        if self.automatic_sources:
            from .source_routing import available_assets
            return available_assets(self)
        return list(getattr(self.provider,'assets',self.config.assets))

    @property
    def available_timeframes(self):
        if self.automatic_sources:
            return [tf for tf in TIMEFRAMES if all(tf in getattr(p,'timeframes',TIMEFRAMES) for p in self.providers.values())]
        return list(getattr(self.provider,'timeframes',TIMEFRAMES))

    def select_provider(self,key):
        if self.automatic_sources: raise ValueError('As fontes são selecionadas automaticamente por ativo.')
        if key not in self.providers: raise ValueError('Fonte não habilitada.')
        if self.scan['active'] or self.analysis_active or self.store.busy(self.clock()): raise RuntimeError('Pare a sessão antes de trocar a fonte.')
        settings=self.settings();settings['provider']=key
        if key!=self.provider_key:
            settings['assets']=list(getattr(self.providers[key],'assets',self.config.assets))
            if settings['timeframe'] not in getattr(self.providers[key],'timeframes',TIMEFRAMES):settings['timeframe']='M5'
        self.store.save_settings(settings)
        self.provider_key=key;self.provider=self.providers[key]
        event('provider.selected',provider=key)
        self.feed={'status':'não verificado','detail':'Fonte selecionada. Clique em Verificar conexão ou Iniciar.'}
        self.learning={'status':'aguardando análise','eligible_profiles':0,'profiles':[]}

    def select_strategy(self,key):
        if key not in STRATEGIES: raise ValueError('Estratégia não habilitada.')
        if self.scan['active'] or self.analysis_active or self.store.busy(self.clock()): raise RuntimeError('Pare a sessão antes de trocar a estratégia.')
        if self.automatic_only and key!='scan_all': raise ValueError('O motor usa todas as estratégias automaticamente.')
        settings=self.settings(); settings['strategy']=key
        self.store.save_settings(settings)
        self.strategy_key=key
        event('strategy.selected',strategy=key)

    def start(self,assets,duration,timeframe='M5',*,scan_id=None,preview=False,source_key=None):
        key=source_key or self.provider_key
        if key not in self.providers: raise ValueError('Fonte não habilitada.')
        if (self.automatic_sources and getattr(self.config,'asset_scope','legacy') in ('forex','crypto_binary')
                and key!='deriv'):
            raise ValueError('Somente a fonte Forex Deriv está habilitada para análise.' if self.config.asset_scope=='forex' else
                             'Somente a corretora de opções binárias está habilitada para análise.')
        from .source_routing import assets_for_source
        supported=assets_for_source(self,key) if self.automatic_sources else self.available_assets
        if not assets or len(set(assets))!=len(assets) or len(assets)>(18 if self.config.asset_scope=='crypto_binary' else 8) or any(x not in supported for x in assets):
            raise ValueError('Selecione ativos habilitados.')
        if duration not in (60,120,180,300): raise ValueError('Expiração deve ser de 1 a 5 minutos.')
        if timeframe not in getattr(self.providers[key],'timeframes',TIMEFRAMES): raise ValueError('Período de análise não habilitado nesta fonte.')
        if self.scan_stopping or (self.scan['active'] and scan_id!=self.scan.get('id')): return None
        if self.analysis_active:
            event('analysis.busy')
            return None
        aid=str(uuid.uuid4()); now=self.clock()
        if not self.store.acquire(aid,now,len(assets)*(FETCH_TIMEOUT+3)+30): return None
        self.analysis_active=True
        a={'id':aid,'created_at':now,'status':'analisando','message':'Analisando o mercado','assets':assets,'checks':[],
            'timeframe':timeframe,'duration_seconds':duration,'source_key':key,'strategy':self.strategy_key,'preview':preview,'scan_id':scan_id,
            'money':{key:self.settings().get(key) for key in ('stake_brl','payout_pct')}}
        try:
            self.current_analysis=a
            self.save_analysis(a)
            self.current_analysis_task=self.spawn(self.analyze(a,duration))
        except Exception:
            self.analysis_active=False
            self.store.release(aid)
            raise
        return a

    async def ensure_binary_contract(self,provider,asset,direction,duration):
        if getattr(self.config,'asset_scope','legacy') not in ('forex','crypto_binary') or not self.automatic_sources:
            return
        supports=getattr(provider,'supports',None)
        if not callable(supports):
            raise DataError('Fonte sem confirmação de contrato binário CALL/PUT.','contract_unsupported')
        await asyncio.wait_for(supports(asset,direction,duration),CONTRACT_TIMEOUT)
        payout_check=getattr(provider,'confirmed_payout',None)
        if not callable(payout_check):
            raise DataError('Payout binário não confirmado pela corretora.','payout_unavailable')
        payout=await asyncio.wait_for(payout_check(asset,direction,duration),CONTRACT_TIMEOUT)
        if isinstance(payout,bool) or not isinstance(payout,(int,float)) or not 0.9<=payout<=1:
            raise DataError('Payout confirmado abaixo de 90% ou inválido.','payout_unavailable')
        return float(payout)

    async def build_ai_payload(self,provider,asset,candidate,*,timeframe,interval,closed,
                               source_identity,received_at,duration,entry_at,payout):
        from .market import validate_latest
        from .strategies.helpers import closes,ema
        from .strategies.ema_macd import macd_values
        from .indicators import rsi
        from .selection_context import structure_context
        from backend.integrations.backend_analysis_adapter import BackendAnalysisAdapter

        batches={}
        for tf,target_interval in (('M1',60),('M5',300)):
            if interval==target_interval:
                rows=closed
                identity=source_identity
            else:
                if tf not in getattr(provider,'timeframes',TIMEFRAMES):
                    raise DataError(f'Candles {tf} não disponíveis na fonte original.','invalid_data')
                batch=await asyncio.wait_for(provider.candles(asset,target_interval),FETCH_TIMEOUT)
                identity=getattr(batch,'source_identity',getattr(provider,'identity',provider.name))
                rows=validate_latest(batch,received_at,timeframe_max_age(tf,self.config),target_interval)
            if identity!=source_identity:
                raise DataError('Candles M1/M5 não pertencem à mesma fonte do pré-sinal.','source_mismatch')
            batches[tf]=rows

        values=closes(batches['M5'])
        macd,macd_signal=macd_values(batches['M5'])
        indicators={'rsi':rsi(values),'ema_9':ema(values,9),'ema_21':ema(values,21),
                    'macd':macd[-1] if macd else None}
        structure=structure_context(batches['M5'],300,received_at)
        criteria=candidate.get('mandatory_criteria',[])
        filters=list(criteria) if isinstance(criteria,list) else []
        filters.extend([
            {'name':'binary_contract','passed':True},
            {'name':'confirmed_payout','passed':True,'ratio':payout},
        ])
        observation=candidate.get('live_observation')
        current_price=(observation.get('price') if isinstance(observation,dict)
                       and observation.get('asset')==asset
                       and observation.get('source_identity')==source_identity else None)
        as_utc=lambda stamp:datetime.fromtimestamp(stamp,timezone.utc).isoformat()
        resolved=candidate.get('strategy_resolved',self.strategy_key)
        return BackendAnalysisAdapter.from_backend(
            asset=asset,market='OPCOES_BINARIAS',source=provider.name,
            source_identity=source_identity,timeframe=timeframe,
            expiration_minutes=duration//60,timestamp_utc=as_utc(received_at),
            entry_at_utc=as_utc(entry_at),expiration_at_utc=as_utc(entry_at+duration),
            price=current_price,payout=payout,
            candles_m1=batches['M1'],candles_m5=batches['M5'],indicators=indicators,
            structure=structure,strategies=[resolved],filters=filters,candidate=candidate)

    async def stop_analysis(self):
        task=self.current_analysis_task
        if not task:
            return False
        if not task.done(): task.cancel()
        event('analysis.stop_requested')
        await asyncio.gather(task,return_exceptions=True)
        # Cancellation before the coroutine's first await may skip its finally.
        if self.current_analysis_task is task:
            if self.current_analysis:
                self.current_analysis.update(status='interrompida',message='Análise interrompida pelo usuário.',finished_at=self.clock())
                self.save_analysis(self.current_analysis)
                self.store.release(self.current_analysis['id'])
            self.analysis_active=False
            self.current_analysis_task=None
        return True

    def start_scan(self,assets,duration,timeframe,*,preview=False):
        if self.scan_stopping or self.scan['active'] or (self.scan_task and not self.scan_task.done()): return None
        if self.analysis_active or self.store.busy(self.clock()): return None
        if timeframe not in self.available_timeframes or duration not in (60,120,180,300): raise ValueError('Período ou duração inválidos nesta fonte.')
        if not assets or len(set(assets))!=len(assets) or len(assets)>(18 if self.config.asset_scope=='crypto_binary' else 8) or any(a not in self.available_assets for a in assets): raise ValueError('Selecione ativos habilitados.')
        self.scan={'id':str(uuid.uuid4()),'active':True,'assets':list(assets),'duration_seconds':duration,'timeframe':timeframe,
            'strategy':self.strategy_key,'started_at':self.clock(),'next_run_at':None,'cycles':0,'message':'Analisando o mercado.'}
        if self.automatic_sources:
            from .source_routing import plans
            self.scan.update(source_mode='automatic',sources=plans(self,assets,timeframe),preview=preview)
        self.scan_task=self.spawn(self._automatic_scan_loop(self.scan) if self.automatic_sources else self._scan_loop(self.scan))
        event('scan.started',scan_id=self.scan['id'],assets=assets,duration_seconds=duration,timeframe=timeframe,strategy=self.strategy_key)
        return dict(self.scan)

    async def _automatic_scan_loop(self,scan):
        from .source_routing import next_check, summarize_health
        due={plan['source_key']:0 for plan in scan['sources']}
        turn=0
        try:
            while scan['active']:
                # Rotate the first source; shared signal limits stay global.
                order=scan['sources'][turn:]+scan['sources'][:turn]
                turn=(turn+1)%len(scan['sources'])
                for plan in order:
                    key=plan['source_key']
                    if not scan['active']: break
                    if self.clock()<due[key]: continue
                    scan.update(current_source=key,next_run_at=None,
                                message='Analisando automaticamente · '+self.providers[key].name)
                    analysis=self.start(plan['assets'],scan['duration_seconds'],scan['timeframe'],
                                        scan_id=scan['id'],preview=scan.get('preview',False),source_key=key)
                    if analysis is None:
                        due[key]=self.clock()+15
                        continue
                    await self.current_analysis_task
                    if not scan['active']: break
                    scan['cycles']+=1
                    due[key]=next_check(analysis,self.clock(),TIMEFRAMES[scan['timeframe']])
                    data_times=[row['data_at'] for row in analysis.get('asset_assessments',{}).values() if row.get('data_at')]
                    self.source_health[key]={'status':'indisponível' if analysis['status']=='erro' else 'parcial' if analysis.get('failed_assets') else 'disponível',
                        'detail':analysis.get('message'),'checked_at':self.clock(),'next_check_at':due[key],
                        'data_at':min(data_times,default=0),'max_age':timeframe_max_age(scan['timeframe'],self.config)}
                if not scan['active']: break
                health=[{'source_key':p['source_key'],**self.source_health.get(p['source_key'],{})} for p in scan['sources']]
                self.feed=summarize_health(health,self.clock(),timeframe_max_age(scan['timeframe'],self.config))
                if scan.get('preview'):
                    scan['message']='Prévia automática concluída, sem envio de sinais.'
                    break
                scan.update(current_source=None,next_run_at=min(due.values()),
                            message='Fontes automáticas · aguardando a próxima avaliação.')
                while scan['active'] and self.clock()<scan['next_run_at']:
                    if self.automatic_only and self.intrabar_contexts: await self.poll_intrabar()
                    await asyncio.sleep(min(15,max(.1,scan['next_run_at']-self.clock())))
        except asyncio.CancelledError:
            raise
        except Exception:
            event('scan.failed',level=logging.ERROR,exc_info=True)
            scan['message']='Sessão interrompida por erro interno. Consulte o diagnóstico.'
        finally:
            scan.update(active=False,current_source=None,next_run_at=None)
            event('scan.stopped',timeframe=scan['timeframe'],source_mode='automatic')

    async def _scan_loop(self,scan):
        try:
            while scan['active']:
                scan.update(next_run_at=None,message='Analisando o mercado.')
                analysis=self.start(scan['assets'],scan['duration_seconds'],scan['timeframe'],scan_id=scan['id'])
                if analysis is None:
                    scan.update(next_run_at=self.clock()+15,message='Aguardando consulta anterior terminar.')
                else:
                    await self.current_analysis_task
                    if not scan['active']: break
                    scan['cycles']+=1
                    if analysis.get('retry_after_seconds') and not analysis.get('failed_assets'):
                        scan.update(next_run_at=self.clock()+analysis['retry_after_seconds'],message='Oportunidade descartada por atraso. Reavaliando em 15 segundos.')
                    elif analysis['status']=='erro' or analysis.get('failed_assets'):
                        rate_limited=any(c.get('code')=='rate_limit' for c in analysis['checks'])
                        closed_market=bool(analysis['checks']) and all(c.get('code')=='market_closed' for c in analysis['checks'])
                        scan.update(next_run_at=self.clock()+(300 if closed_market else 60 if rate_limited else 30),message='Mercado fechado. Nova verificação em 5 minutos.' if closed_market else 'Falha de dados. Nova tentativa agendada.')
                    else:
                        interval=TIMEFRAMES[scan['timeframe']]
                        scan.update(next_run_at=(int(self.clock()//interval)+1)*interval+2,message='Aguardando o próximo candle fechado.')
                event('scan.waiting',scan_id=scan['id'],next_run_at=scan['next_run_at'],cycles=scan['cycles'],reason=scan['message'])
                while scan['active'] and self.clock()<scan['next_run_at']:
                    if self.automatic_only and self.intrabar_contexts:
                        await self.poll_intrabar()
                    await asyncio.sleep(min(15,max(.1,scan['next_run_at']-self.clock())))
        except asyncio.CancelledError:
            raise
        except Exception:
            event('scan.failed',level=logging.ERROR,exc_info=True)
            scan['message']='Sessão interrompida por erro interno. Consulte o diagnóstico.'
        finally:
            scan.update(active=False,next_run_at=None)
            event('scan.stopped',timeframe=scan['timeframe'])

    async def stop_scan(self):
        async with self.scan_stop_lock:
            self.scan_stopping=True
            try:
                self.scan['active']=False
                self.intrabar_contexts.clear()
                self.intrabar.update(state='parado',watching_assets=0,
                                     detail='Monitoramento intravela parado; sinais publicados conservam sua apuração.')
                task=self.scan_task
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                await self.stop_analysis()
                await self.sync_price_streams()
                self.scan.update(message='Sessão parada. Os sinais emitidos continuam sendo apurados.',next_run_at=None)
                return dict(self.scan)
            finally:
                self.scan_stopping=False

    def save_analysis(self,a):
        a['asset_previews']=asset_previews(a)
        self.store.analysis(a)

    def arm_intrabar(self,a,asset,candles,closed,identity,received_at,learned):
        if self.production_ai_required():
            self.block_unverified_intrabar()
            return
        if (not self.automatic_only or a.get('preview') or not a.get('scan_id') or
                not self.scan.get('active') or a['scan_id']!=self.scan.get('id')):
            return
        from .manual_book import prepare_live, snapshot
        interval=TIMEFRAMES[a['timeframe']]
        key=(a['source_key'],asset)
        plans=prepare_live(closed)
        if not plans:
            self.intrabar_contexts.pop(key,None)
            return
        start=closed[-1].t+interval
        current=next((c for c in candles if c.t==start),None)
        self.intrabar_contexts[key]={'plans':plans,'points':[],'dead':set(),'touches':{},'observed_variants':set(),
            'rows':tuple(closed),'identity':identity,'source_key':a['source_key'],'asset':asset,
            'timeframe':a['timeframe'],'interval':interval,'duration':a['duration_seconds'],
            'starts_at':start,'ends_at':start+interval,'scan_id':a['scan_id'],'analysis_id':a['id'],
            'forming_open':current.o if current is not None else None,
            'forming_open_evidence':current.json() if current is not None else None,
            'configuration':a['configuration'],'money':a.get('money',{}),
            'learning':a['learning'],'learned':learned,'armed_at':received_at,'rules':snapshot()}
        self.intrabar.update(state='acompanhando',watching_assets=len(self.intrabar_contexts),
            detail='Referências fechadas; aguardando gatilhos por preço real dentro da vela original.')

    async def sync_price_streams(self):
        now=self.clock();wanted=set()
        pending_ids={s['id'] for s in self.store.active() if s['state']=='aguardando entrada'}
        self.pending_quotes={key:value for key,value in self.pending_quotes.items() if key in pending_ids}
        if self.scan.get('active'):
            wanted.update(key for key,ctx in self.intrabar_contexts.items() if
                now<ctx['ends_at'] and next_entry(now,self.config.min_lead)<ctx['ends_at'])
        for signal in self.store.active():
            if (signal['state']=='aguardando entrada' and now<signal['entry_at'] and
                    (signal.get('live_monitor_required') or signal.get('manual_intrabar'))):
                wanted.add((signal['source_key'],signal['asset']))
        if self.observer:
            wanted.update((r['source_key'],r['asset']) for r in self.observer.pending()
                          if r.get('live_monitor_required') and now<r['entry_at'])
        await self.price_streams.sync(wanted)

    def recover_entry_watches(self):
        for signal in self.store.active():
            if signal.get('live_monitor_required') and signal['state']=='aguardando entrada':
                self.cancel_entry(signal,'Monitoramento de preço interrompido por reinício antes da entrada.',code='entry_watch_interrupted')
        if self.observer:
            for row in self.observer.pending():
                if row.get('live_monitor_required') and self.clock()<row['entry_at']:
                    self.observer.stream_gap((row['source_key'],row['asset']),self.clock(),'entry_watch_interrupted')

    def watch_pending_price(self,key,point):
        from .entry_watch import check_live_entry
        now=self.clock()
        for signal in self.store.active():
            if (signal['state']!='aguardando entrada' or now>=signal['entry_at'] or
                    (signal['source_key'],signal['asset'])!=key or
                    not (signal.get('live_monitor_required') or signal.get('manual_intrabar'))):continue
            try:
                audit,reason=check_live_entry(signal,point,now)
            except DataError:
                continue
            if reason:self.cancel_entry(signal,reason,audit=audit,code='live_setup_invalidated')
            else:self.pending_quotes[signal['id']]=point
        if self.observer:self.observer.watch_price(key,point,now)

    async def receive_stream_price(self,key,point):
        self.watch_pending_price(key,point)
        if self.scan.get('active') and key in self.intrabar_contexts:
            await self.poll_intrabar(observed=(key,point))

    async def stream_gap(self,key,code):
        timing=self.price_streams.status.get(key,{}).get('price_timing')
        audit={'price_timing':timing} if timing else None
        ctx=self.intrabar_contexts.get(key)
        if ctx:
            ctx['dead'].update(p['id'] for p in ctx['plans'])
            ctx['points'].clear();ctx['touches'].clear()
        now=self.clock()
        for signal in self.store.active():
            if (signal.get('live_monitor_required') and signal['state']=='aguardando entrada' and
                    now<signal['entry_at'] and (signal['source_key'],signal['asset'])==key):
                self.cancel_entry(signal,'Continuidade da cotação original perdida antes da entrada.',code=code,audit=audit)
        if self.observer:self.observer.stream_gap(key,now,code)

    def observe_intrabar(self,ctx,candidates,selected,reason,entry,now):
        if not self.observer or not self.observation_enabled or not candidates:return
        new={c['live_plan_id'] for c in candidates}-ctx['observed_variants']
        if not new:return
        try:
            from .selection_observer import alternatives
            session=self.observer.session(ctx['configuration'],ctx['armed_at'])
            decisions=alternatives(ctx['rows'],candidates,minimum=self.settings()['minimum_score'],strict=True,
                context=(ctx['identity'],ctx['asset'],ctx['timeframe'],ctx['duration']),frozen=self.observer.frozen_profiles(session,now),
                scope={'source_key':ctx['source_key'],'configuration_id':ctx['configuration']['id']},now=now,min_lead=self.config.min_lead)
            decisions=[d for d in decisions if not d['selector'].startswith('price_action')]
            decisions.append({'selector':'reference_adaptive','candidate':selected,'reason':reason,'pool':candidates})
            decisions.extend({'selector':'candidate:'+c['strategy_resolved'],'candidate':c,'reason':'Gatilho intravela observado; não enviado.',
                              'pool':[c]} for c in candidates)
            for decision in decisions:
                self.observer.capture_selection({**decision,'intrabar_observed':True,'planned_entry_at':entry},session=session,
                    asset=ctx['asset'],source_key=ctx['source_key'],source_identity=ctx['identity'],rows=ctx['rows'],
                    interval=ctx['interval'],timeframe=ctx['timeframe'],duration=ctx['duration'],now=now,observed_at=self.clock(),min_lead=self.config.min_lead)
            ctx['observed_variants'].update(new)
        except Exception:event('selection.intrabar_observation_failed',status='failed',exc_info=True)

    @staticmethod
    def book_opportunity_id(signal):
        contract=signal.get('setup_contract') or {}
        from .manual_book import VERSION as BOOK_VERSION
        if contract.get('version')!=BOOK_VERSION:return None
        original=contract.get('entry_window_start')
        if type(original) is not int:return None
        geometry=contract.get('geometry') or {}
        if contract.get('entry_kind') in ('retest','live_retest') and geometry.get('breakout_at') is not None:
            original=('first-return',geometry.get('zone_id'),geometry['breakout_at'])
        return hashlib.sha256(repr(('book-entry-window',signal.get('source_key'),signal.get('source_identity'),
            signal.get('asset'),signal.get('timeframe'),original)).encode()).hexdigest()

    async def poll_intrabar(self,observed=None):
        if self.production_ai_required():
            self.block_unverified_intrabar()
            return []
        from .live_price import validate_point,point_after
        from .manual_book import evaluate_live,VERSION as BOOK_VERSION
        from .strategies import select_reference
        if observed is None:await self.sync_price_streams()
        async with self.intrabar_lock:
            if not self.scan.get('active') or not self.automatic_only:return []
            now=self.clock()
            contexts=[]
            for key,ctx in list(self.intrabar_contexts.items()):
                if ctx['scan_id']!=self.scan.get('id') or now>=ctx['ends_at']:
                    self.intrabar_contexts.pop(key,None);continue
                if next_entry(now,self.config.min_lead)>=ctx['ends_at']:
                    self.intrabar['last_quotes'][ctx['asset']]={'status':'prazo encerrado','checked_at':now,
                        'detail':'Gatilho não cabe mais na vela original com a antecedência manual.'}
                    continue
                if observed is not None and key!=observed[0]:continue
                contexts.append((key,ctx))
            self.intrabar['watching_assets']=len(contexts)
            async def fetch(ctx):
                provider=self.providers.get(ctx['source_key'])
                if provider is None or not hasattr(provider,'live_price'):
                    return None,DataError('Fonte não fornece tick/negócio público com horário.','live_price_unavailable')
                try:
                    if observed is not None:return observed[1],None
                    if callable(getattr(provider,'stream_prices',None)):
                        point=self.price_streams.latest.get((ctx['source_key'],ctx['asset']))
                        if point is None:raise DataError('Aguardando assinatura de preços original.','stream_waiting')
                        return point,None
                    async with self.intrabar_slots:
                        async def request():
                            await provider.check_asset(ctx['asset'])
                            return await provider.live_price(ctx['asset'])
                        point=await asyncio.wait_for(request(),8)
                        validate_point(point,asset=ctx['asset'],identity=ctx['identity'],now=self.clock())
                        return point,None
                except asyncio.CancelledError:raise
                except Exception as error:return None,error
            quotes=await asyncio.gather(*(fetch(ctx) for _,ctx in contexts))
            accepted=[]
            for (key,ctx),(point,error) in zip(contexts,quotes):
                if not self.scan.get('active') or self.intrabar_contexts.get(key) is not ctx:break
                now=self.clock();asset=ctx['asset']
                if error:
                    self.intrabar['last_quotes'][asset]={'status':'indisponível','checked_at':now,
                        'detail':str(error) if isinstance(error,DataError) else 'Cotação intravela não confirmada.',
                        'code':getattr(error,'code','live_price_failed')}
                    continue
                try:validate_point(point,asset=asset,identity=ctx['identity'],now=now)
                except DataError as error:
                    self.intrabar['last_quotes'][asset]={'status':'dados atrasados','checked_at':now,'detail':str(error)}
                    continue
                if not ctx['starts_at']<=point.at<ctx['ends_at']:continue
                self.intrabar['last_quotes'][asset]={'status':'observado','price':point.price,
                    'at':point.at,'received_at':point.received_at,'checked_at':now,'source_identity':point.source_identity}
                if ctx['points'] and not point_after(point,ctx['points'][-1]):continue
                ctx['points'].append(point);ctx['points']=ctx['points'][-12:]
                candidates,rejections=evaluate_live(ctx['plans'],ctx['points'],now=now,
                    forming_open=ctx['forming_open'],dead=ctx['dead'],asset=asset,identity=ctx['identity'],touches=ctx['touches'])
                selected,reason=select_reference(ctx['rows'],candidates,learned=ctx['learned'],
                    context=(ctx['identity'],asset,ctx['timeframe'],ctx['duration']),
                    scope={'source_key':ctx['source_key'],'regime':manual_context(ctx['rows'])['regime'],
                           'configuration_id':ctx['configuration']['id']},minimum=self.settings()['minimum_score'])
                decision_id=str(uuid.uuid4())
                entry=next_entry(now,self.config.min_lead)
                self.observe_intrabar(ctx,candidates,selected,reason,entry,now)
                signature=(tuple(sorted(c['live_plan_id'] for c in candidates)),reason)
                if ctx.get('last_audit_signature')==signature and now-ctx.get('last_audit_at',0)<2:continue
                ctx.update(last_audit_signature=signature,last_audit_at=now)
                audit={'selector':'book_intrabar','version':BOOK_VERSION,'candidates':candidates,
                       'selected':selected,'reason':reason,'rejections':rejections,
                       'quote':point.json(),'closed_references_at':ctx['starts_at'],
                       'forming_open_evidence':ctx['forming_open_evidence']}
                self.store.record_decision({'id':decision_id,'analysis_id':ctx['analysis_id'],
                    'created_at':now,'preview':False,'asset':asset,'source_key':ctx['source_key'],
                    'source_identity':ctx['identity'],'timeframe':ctx['timeframe'],'duration_seconds':ctx['duration'],
                    'configuration':ctx['configuration'],'candles':[c.json() for c in ctx['rows']],
                    'selection':audit,'reason_at_decision':reason,'regime':manual_context(ctx['rows'])['regime'],
                    'data_quality':'valid','learning_snapshot':ctx['learning'],'intrabar_observed':True})
                if selected is None:continue
                if any(s['asset']==asset and s['source_identity']==ctx['identity'] and
                       s['direction']!=selected['direction'] for s in self.store.active()):
                    self.store.decision_event(decision_id,now,{'event':'operational_block',
                        'reason':'Sinal original ativo aponta direção oposta no mesmo instrumento.'})
                    continue
                if entry>=selected['setup_contract']['entry_window_end']:
                    self.store.decision_event(decision_id,now,{'event':'operational_block','reason':'Janela intravela vencida.'})
                    continue
                provider=self.providers[ctx['source_key']]
                s={**selected,'id':str(uuid.uuid4()),'decision_id':decision_id,'analysis_at':now,
                   'asset':asset,'source':provider.name,'source_key':ctx['source_key'],'source_identity':ctx['identity'],
                   'strategy':'scan_all','market':'regular','timeframe':ctx['timeframe'],'duration_seconds':ctx['duration'],'duration_basis':'intrabar_planned',
                   'entry_at':entry,'expiry_at':entry+ctx['duration'],'created_at':now,'emitted_at':now,
                   'entry_timing':describe_entry(entry,ctx['interval'],now,self.config.min_lead),
                   'data_updated_at':ctx['starts_at'],'data_received_at':point.received_at,
                   'state':'aguardando entrada','result':'Resultado indisponível','result_kind':'indicativo','live_monitor_required':True,
                   'broker_confirmed':False,'settlement_pending':True,'telegram':{'status':'pendente'},
                   'stake_brl':ctx['money'].get('stake_brl'),'payout_pct':ctx['money'].get('payout_pct'),
                   'payout_kind':'user_declared_estimate' if ctx['money'].get('payout_pct') is not None else 'unavailable',
                   'entry_validation':{'status':'pendente','scheduled_at':entry-45,'atr':selected['manual_atr']},
                   'evidence':{'configuration':ctx['configuration'],'selection':audit,
                       'candles':[c.json() for c in ctx['rows']],'rules_version':RULES_VERSION,
                       'pipeline_version':PIPELINE_VERSION,'learning':selected.get('learning'),
                       'strategy':'scan_all','strategy_resolved':selected['strategy_resolved'],
                       'timeframe':ctx['timeframe'],'interval_seconds':ctx['interval'],
                       'live_price':point.json(),'book_rules':ctx['rules'],
                       'regime':manual_context(ctx['rows'])['regime']},
                   'htf':{'status':'disabled','detail':'Confirmação H1 desligada.'}}
                from .tracing import decision_trace
                s['evidence']['decision_trace']=decision_trace(s,ctx['rows'],now=now,interval=ctx['interval'],
                    duration=ctx['duration'],minimum=self.settings()['minimum_score'],strict=True,
                    rules_version=RULES_VERSION,pipeline_version=PIPELINE_VERSION)
                s['evidence']['decision_trace']['intrabar_price']=point.json()
                s['observed_win_rate']=self.store.win_rate_snapshot(s,now)
                s['movement_id']=self.book_opportunity_id(s)
                s['engine_explanation']=build_explanation(s)
                fingerprint=hashlib.sha256(repr(('book-live',ctx['identity'],asset,ctx['timeframe'],ctx['starts_at'])).encode()).hexdigest()
                # Recheck latency and Stop immediately before the atomic risk commit.
                now=self.clock()
                if (not self.scan.get('active') or not can_start(s,now) or now-point.at>10
                        or now>=ctx['ends_at']):continue
                committed,reason=self.store.add_apex_signal(s,fingerprint)
                self.store.decision_event(decision_id,now,{'event':'operational_commit' if committed else 'operational_block',
                    'signal_id':s['id'] if committed else None,'reason':reason})
                if committed:
                    self.pending_quotes[s['id']]=point
                    accepted.append(s)
                    self.spawn(self.deliver(s['id']))
                    self.intrabar.update(last_signal_id=s['id'],last_signal_at=now)
                    self.intrabar_contexts.pop(key,None)
            if not contexts:self.intrabar.update(state='aguardando',detail='Aguardando referências ou janela elegível.')
            return accepted

    async def analyze(self,a,duration):
        context_token=CONTEXT.set({'analysis_id':a['id'],'provider':a['source_key'],'timeframe':a.get('timeframe','M5')})
        a['ranking']=[]
        a['asset_assessments']={}
        a['emitted_assets']={}
        from .selection_observer import config_snapshot
        a['configuration']=config_snapshot(self.config,{**self.settings(),'provider':a['source_key'],'assets':a['assets'],
            'timeframe':a.get('timeframe','M5'),'duration_seconds':duration})
        observation_inputs=[]
        event('analysis.started',assets=a['assets'],duration_seconds=duration,strategy=a['strategy'])
        provider=self.providers[a['source_key']]
        timeframe=a.get('timeframe','M5')
        interval=TIMEFRAMES[timeframe]
        max_age=timeframe_max_age(timeframe,self.config)
        try:
            found=None
            pending=[]
            learning_at=self.clock()
            learned=await asyncio.to_thread(self.historical_learning,learning_at)
            frozen_learning=learning_state(learned,learning_at,self.store.latest_learning_feedback(learning_at))
            frozen_learning['rules_version']=RULES_VERSION
            if learning_at>=self.learning.get('evaluated_at',0):self.learning=frozen_learning
            a['learning']=frozen_learning
            event('learning.evaluated',learning=frozen_learning)
            evaluate_strategy=evaluate if a['strategy']=='candlestick_sr' else STRATEGIES[a['strategy']]
            failures=0
            evaluated=0
            for asset in a['assets']:
                CONTEXT.set({**CONTEXT.get(),'asset':asset})
                asset_started=time.monotonic()
                decision_id=str(uuid.uuid4());decision_recorded=False
                try:
                    a.update(current_asset=asset,stage='consultando dados')
                    self.save_analysis(a)
                    async def fetch():
                        if hasattr(provider,'check_asset'): await provider.check_asset(asset)
                        return await provider.candles(asset,interval)
                    candles=await asyncio.wait_for(fetch(),FETCH_TIMEOUT)
                    event('provider.received',analysis_id=a['id'],provider=a['source_key'],asset=asset,count=len(candles),duration_ms=round((time.monotonic()-asset_started)*1000,2))
                    event('candles.received',candles=[c.json() for c in candles])
                    received_at=self.clock()
                    if self.observer:self.observer.remember(a['source_key'],asset,interval,candles,received_at)
                    a['stage']='validando candles'
                    closed=validate_latest(candles,received_at,max_age,interval)
                    context_key=(a['source_key'],getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),asset,interval)
                    self.context_window.remember(context_key,closed,interval,received_at)
                    context_rows,context_status=self.context_window.snapshot(context_key,received_at)
                    event('candles.validated',count=len(closed),first_candle=closed[0].t,last_candle=closed[-1].t,age_seconds=received_at-closed[-1].t-interval,volume_available=all(c.v is not None for c in closed))
                    if self.config.experimental_strategies:
                        from .experiments import shadow
                        try:
                            research_results=shadow(closed,now=received_at,interval=interval,
                                                    keys=self.config.experimental_strategies)
                        except Exception:
                            # Optional research cannot stop the operational analysis.
                            research_results=[]
                            event('experiment.shadow_failed',analysis_id=a['id'],asset=asset,
                                  status='failed',reason='Falha na pesquisa opcional; análise operacional preservada.',
                                  exc_info=True)
                        for research in research_results:
                            event('experiment.shadow_evaluated',analysis_id=a['id'],asset=asset,
                                  provider=a['source_key'],timeframe=timeframe,strategy=research['strategy'],
                                  status='shadow_candidate' if research['accepted'] else 'shadow_rejected',
                                  reason=research['reason'],candidate=research)
                            if self.research and not a.get('preview'):
                                try:
                                    from .research_filters import apply_filter, context
                                    base={**research,'context':context(closed,received_at,interval)}
                                    variants=[('base',base)]
                                    if research['accepted']:
                                        for name in self.config.experimental_filters:
                                            checked=apply_filter(name,closed,research,now=received_at,interval=interval)
                                            variants.append((name,{**base,'filters':[checked],'accepted':checked['passed'],
                                                'reason':base['reason'] if checked['passed'] else checked['detail']}))
                                    for variant,observation in variants:
                                        self.research.capture(observation,asset=asset,source_key=a['source_key'],
                                            source_identity=getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),
                                            rows=closed,interval=interval,duration=duration,now=received_at,observed_at=self.clock(),
                                            min_lead=self.config.min_lead,variant=variant,payout_pct=a.get('money',{}).get('payout_pct'))
                                except Exception:event('experiment.capture_failed',status='failed',asset=asset,exc_info=True)
                    self.feed={'status':'disponível','detail':f'{asset}: candles {timeframe} validados',
                        'timeframe':timeframe,'max_age':max_age,'checked_at':self.clock(),'data_at':closed[-1].t+interval}
                    selection_audit={}
                    self.arm_intrabar(a,asset,candles,closed,
                        getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),received_at,learned)
                    if a['strategy']=='scan_all':
                        candidate,reason=evaluate_strategy(closed,learned=learned,
                            context=(getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),asset,timeframe,duration),
                            scope={'source_key':a['source_key'],'regime':manual_context(closed[-MANUAL_RULES.lookback:])['regime'],
                                   'configuration_id':a['configuration']['id']},
                            strict=self.automatic_only,audit=selection_audit,minimum=self.settings()['minimum_score'])
                    else:
                        candidate,reason=evaluate_strategy(closed)
                    # When both describe the same original entry, observe the
                    # book's continuation during this candle before publishing.
                    # A provider without real timestamped ticks keeps only the
                    # genuinely closed strategies; no OHLC close is a live quote.
                    live_context=self.intrabar_contexts.get((a['source_key'],asset))
                    if candidate and candidate.get('manual') and live_context and hasattr(provider,'live_price'):
                        level=(candidate.get('setup_contract') or {}).get('levels',{}).get('manual_region')
                        matching=[p['key'] for p in live_context['plans'] if p['trigger']=='flow'
                                  and p['direction']==candidate['direction'] and level is not None
                                  and abs((p['geometry']['level'] if p['direction']=='CALL' else
                                           p['mirror']-p['geometry']['level'])-level)<=.30*p['average']]
                        if matching:
                            candidate=None;reason='Referência confirmada; aguardando continuidade por cotação intravela.'
                            selection_audit.update(selected=None,reason=reason,pending_live_variants=matching)
                    if candidate and not candidate.get('setup_contract'):
                        from .setup_contract import capture_setup
                        candidate=capture_setup(candidate.get('strategy_resolved',a['strategy']),candidate,closed,interval)
                    if not selection_audit:
                        selection_audit={'selector':a['strategy'],'candidates':[candidate] if candidate else [],
                                         'selected':candidate,'reason':reason}
                    self.store.record_decision({'id':decision_id,'analysis_id':a['id'],'created_at':received_at,
                        'preview':a.get('preview',False),'asset':asset,'source_key':a['source_key'],
                        'source_identity':getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),
                        'timeframe':timeframe,'duration_seconds':duration,'configuration':a['configuration'],
                        'candles':[c.json() for c in closed],'selection':selection_audit,
                        'regime':candidate.get('regime') if candidate else manual_context(closed[-MANUAL_RULES.lookback:])['regime'],
                        'data_quality':'valid','reason_at_decision':reason,
                        'learning_snapshot':frozen_learning,'analysis_started_at':a['created_at']})
                    decision_recorded=True
                    a['asset_assessments'][asset]=decision_summary(
                        audit=selection_audit,candidate=candidate,reason=reason,decision_id=decision_id,
                        source_identity=getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),
                        data_at=closed[-1].t+interval,received_at=received_at)
                    if self.observer and self.observation_enabled and a['strategy']=='scan_all' and not a.get('preview') and selection_audit.get('pool_complete',True):
                        observation_inputs.append((asset,closed,selection_audit,received_at,
                            getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),context_rows,context_status))
                    event('setup.evaluated',analysis_id=a['id'],asset=asset,strategy=a['strategy'],status='candidate' if candidate else 'no_setup',resolved=candidate.get('strategy_resolved',a['strategy']) if candidate else None,reason=reason,candidate=candidate)
                    evaluated+=1
                    a.update(stage='avaliando setup',last_data_at=closed[-1].t+interval,closed_candles=len(closed))
                    if not candidate:
                        if selection_audit.get('pool_complete') is False:
                            failures+=1
                            a['checks'].append({'asset':asset,'reason':reason,'kind':'engine_error','code':'engine_error'})
                            continue
                        a['checks'].append({'asset':asset,'reason':reason,'kind':'no_setup'}); continue
                    minimum=self.settings()['minimum_score']
                    if not candidate.get('manual') and candidate.get('score',0)<minimum:
                        a['checks'].append({'asset':asset,'reason':f'Pontuação técnica inferior a {minimum}/100.','kind':'no_setup'}); continue
                    resolved=candidate.get('strategy_resolved',a['strategy'])
                    # Verify the contract once before consulting the agents;
                    # a second fresh proposal is checked only at final commit.
                    identity=getattr(candles,'source_identity',getattr(provider,'identity',provider.name))
                    htf={'status':'disabled','detail':'Confirmação H1 desligada.'}
                    if self.config.confirm_htf:
                        htf={'status':'not_applicable','detail':'Filtro H1 aplicável somente ao EMA pullback.'}
                        if resolved=='ema_pullback':
                            htf=await confirm(provider,asset,candidate['direction'],closed[-1].t+interval,identity)
                            if htf['status']=='rejected':
                                a['checks'].append({'asset':asset,'reason':htf['detail'],'kind':'no_setup','htf':htf}); continue
                            candidate={**candidate,'reasons':candidate['reasons']+[htf['detail']]}
                    production_ai=self.production_ai_required()
                    payout=await self.ensure_binary_contract(provider,asset,candidate['direction'],duration) if production_ai else None
                    # Signals use market data only; no broker contract is purchased.
                    now=self.clock()
                    # A delayed evaluation must not emit from a now-stale snapshot.
                    latest=validate(candles,now,max_age,interval)
                    if latest[-1].t!=closed[-1].t or int(now//interval)!=int(received_at//interval):
                        raise DataError('Uma nova vela fechou durante a consulta. Solicite nova análise.','candle_changed')
                    entry=next_entry(now,self.config.min_lead)
                    contract=candidate.get('setup_contract') or {}
                    if contract.get('valid_until') is not None and entry>contract['valid_until']:
                        a['checks'].append({'asset':asset,'reason':'Setup vencido antes da entrada programada.','kind':'discarded','code':'setup_expired'})
                        continue
                    ai_consensus=None
                    if production_ai:
                        payload=await self.build_ai_payload(provider,asset,candidate,timeframe=timeframe,interval=interval,
                            closed=closed,source_identity=identity,received_at=received_at,duration=duration,
                            entry_at=entry,payout=payout)
                        ai_result=await self.analyze_with_ai(payload.data)
                        decision=ai_result.get('decision',{})
                        aligned=decision.get('direcao')==candidate['direction']
                        ai_consensus={'approved':bool(ai_result.get('approved') and aligned),
                                      'decision':decision,'votes':ai_result.get('votes',[])}
                        self.store.decision_event(decision_id,self.clock(),{'event':'ai_consensus',**ai_consensus})
                        a['asset_assessments'][asset]['ai_consensus']=ai_consensus
                        self.save_analysis(a)
                        if not ai_consensus['approved']:
                            reason='Consenso AI indisponível, rejeitado ou contrário ao pré-sinal do backend.'
                            a['checks'].append({'asset':asset,'reason':reason,'kind':'ai_blocked','code':'ai_rejected'})
                            if ai_result.get('approved') and not aligned:
                                self.ai_status.update(status='rejected',detail='Consenso divergiu do pré-sinal técnico do backend.',updated_at=self.clock())
                            continue
                        candidate={**candidate,'ai_consensus':ai_consensus}
                    monetary=a.get('money',{})
                    s={**candidate,'id':str(uuid.uuid4()),'decision_id':decision_id,'analysis_at':received_at,'asset':asset,'source':provider.name,
                        'source_key':a['source_key'],'strategy':a['strategy'],'source_identity':getattr(candles,'source_identity',getattr(provider,'identity',provider.name)),'market':'regular','timeframe':timeframe,'duration_seconds':duration,
                        'entry_at':entry,'expiry_at':entry+duration,'created_at':now,'data_updated_at':closed[-1].t+interval,
                        'entry_timing':describe_entry(entry,interval,now,self.config.min_lead),
                        'data_received_at':received_at,'state':'aguardando entrada','result':'Resultado indisponível',
                        'result_kind':'indicativo','broker_confirmed':False,'settlement_pending':True,'telegram':{'status':'pendente'},
                        'strategy_resolved':resolved,'htf':{k:v for k,v in htf.items() if k!='candles'},
                        'stake_brl':monetary.get('stake_brl'),'payout_pct':payout*100 if production_ai else monetary.get('payout_pct'),
                        'payout_kind':'broker_proposal_verified' if production_ai else 'user_declared_estimate' if monetary.get('payout_pct') is not None else 'unavailable',

                        'evidence':{'configuration':a['configuration'],'selection':selection_audit,'candles':[c.json() for c in closed],'rules_version':RULES_VERSION,'pipeline_version':PIPELINE_VERSION,'learning':candidate.get('learning'),'strategy':a['strategy'],
                            'timeframe':timeframe,'interval_seconds':interval,'max_age_seconds':max_age,
                            'strategy_resolved':resolved,'regime':candidate.get('regime'),'regime_metrics':candidate.get('regime_metrics'),
                            'routing_version':'1.4.0' if a['strategy']=='auto' else None,'htf':htf,
                            'asset_elapsed_ms':round((time.monotonic()-asset_started)*1000,2)}}
                    if self.automatic_only:
                        s['entry_validation']={'status':'pendente','scheduled_at':entry-45,'atr':candidate.get('manual_atr',atr(closed))}
                        s['live_monitor_required']=bool(candidate.get('manual') or resolved=='fibonacci')
                    from .tracing import decision_trace
                    s['evidence']['decision_trace']=decision_trace({**candidate,'strategy_resolved':resolved},closed,
                        now=now,interval=interval,duration=duration,minimum=minimum,strict=self.automatic_only,
                        rules_version=RULES_VERSION,pipeline_version=PIPELINE_VERSION)
                    s['evidence']['decision_trace']['filters']['htf']=s['htf']
                    s['evidence']['regime']=s['evidence']['decision_trace']['regime']
                    from .selection_context import structure_context, higher_context
                    s['evidence']['observation_context']={
                        'structure':structure_context(closed,interval,received_at),
                        'higher_timeframe':{**higher_context(context_rows,interval,received_at),'buffer':context_status},
                        'news':{'state':'informative','active_filter':False}}
                    # The historical rate is a sealed decision-time observation,
                    # never a score conversion or a forecast from future results.
                    s['observed_win_rate']=self.store.win_rate_snapshot(s,now)
                    from .setup_contract import movement_id
                    s['movement_id']=movement_id(s)
                    if s.get('manual'):
                        s['movement_id']=self.book_opportunity_id(s) or s['movement_id']
                    fingerprint=hashlib.sha256(f"{provider.name}|{asset}|{timeframe}|{resolved}|{candidate['direction']}|{candidate['setup_candle']}".encode()).hexdigest()
                    if not can_start(s,self.clock()):
                        a['checks'].append({'asset':asset,'reason':'Oportunidade descartada por atraso.','kind':'discarded'}); continue
                    a['ranking'].append({'asset':asset,'direction':candidate['direction'],'score':candidate.get('score',0),
                        'strategy':resolved,'reasons':candidate.get('reasons',[]),'data_at':closed[-1].t+interval,
                        'observed_win_rate':s['observed_win_rate']})
                    pending.append((s,fingerprint,candles))
                except (DataError,asyncio.TimeoutError) as e:
                    message=str(e) if isinstance(e,DataError) else 'Tempo limite da fonte excedido.'
                    if getattr(e,'code','') in ('contract_unsupported','contract_duration','contract_duration_unknown','contract_metadata','payout_unavailable'):
                        a['checks'].append({'asset':asset,'reason':message,'kind':'contract_blocked','code':e.code,'stage':a.get('stage')})
                        continue
                    if not decision_recorded:
                        self.store.record_decision({'id':decision_id,'analysis_id':a['id'],'created_at':self.clock(),
                            'asset':asset,'source_key':a['source_key'],'timeframe':timeframe,'duration_seconds':duration,
                            'configuration':a['configuration'],'data_quality':'invalid','reason_at_decision':message,
                            'error_code':getattr(e,'code','timeout'),'preview':a.get('preview',False)})
                    else:
                        self.store.decision_event(decision_id,self.clock(),{'event':'discarded','reason':message})
                    failures+=1
                    self.feed={'status':'indisponível','detail':message,'code':getattr(e,'code','timeout'),'checked_at':self.clock()}
                    a['checks'].append({'asset':asset,'reason':message,'kind':'data_error','code':getattr(e,'code','timeout'),'stage':a.get('stage')})
            # Compare all assets before committing, then reject data that aged during the scan.
            pending.sort(key=lambda item:(item[0]['entry_at'],a['assets'].index(item[0]['asset'])))
            accepted_signals=[]
            for s,fingerprint,candles in ([] if a.get('preview') else pending):
                now=self.clock()
                asset=s['asset']
                try:
                    latest=validate(candles,now,max_age,interval)
                    if latest[-1].t+interval!=s['data_updated_at'] or int(now//interval)!=int(s['data_received_at']//interval):
                        raise DataError('Uma nova vela fechou durante a comparação dos ativos.','candle_changed')
                    if not can_start(s,now):
                        raise DataError('Prazo de entrada insuficiente após comparar os ativos.','entry_late')
                    if a.get('scan_id') is not None and (
                            not self.scan.get('active') or self.scan.get('id') != a['scan_id']):
                        raise DataError('Monitoramento interrompido antes da emissão.','scan_stopped')
                    if self.automatic_sources and self.config.asset_scope in ('forex', 'crypto_binary'):
                        # A proposal used by the agents can expire while other
                        # assets are evaluated. Never publish using that old
                        # payout; this is still read-only, never a purchase.
                        confirmed = await self.ensure_binary_contract(
                            provider, asset, s['direction'], s['duration_seconds'])
                        original_payout = s.get('payout_pct')
                        s['payout_pct'] = round(confirmed * 100, 4)
                        s['payout_kind'] = 'broker_proposal_verified'
                        now = self.clock()
                        s.setdefault('evidence', {})['binary_contract_final_check'] = {
                            'kind': 'broker_proposal_read_only',
                            'payout_pct_before': original_payout,
                            'payout_pct_final': s['payout_pct'],
                            'checked_at': now,
                        }
                        if a.get('scan_id') is not None and (
                                not self.scan.get('active') or self.scan.get('id') != a['scan_id']):
                            raise DataError('Monitoramento interrompido durante confirmação final.','scan_stopped')
                        latest = validate(candles, now, max_age, interval)
                        if (latest[-1].t + interval != s['data_updated_at']
                                or int(now // interval) != int(s['data_received_at'] // interval)):
                            raise DataError('Uma nova vela fechou durante a confirmação final.','candle_changed')
                        if not can_start(s, now):
                            raise DataError('Prazo de entrada insuficiente após nova proposta.','entry_late')
                except (DataError,asyncio.TimeoutError) as error:
                    code=getattr(error,'code','contract_timeout')
                    reason=str(error) if isinstance(error,DataError) else 'Tempo esgotado na confirmação final do contrato; sinal descartado.'
                    a['checks'].append({'asset':asset,'reason':reason,'kind':'discarded','code':code})
                    self.store.decision_event(s['decision_id'],self.clock(),{'event':'discarded','code':code,'reason':reason})
                    continue
                s['created_at']=now
                s['emitted_at']=now
                # Retain broker provenance verified at final commit.  Manual
                # estimates must never be silently labelled as broker quotes.
                s.setdefault('payout_kind', 'user_declared_estimate' if s.get('payout_pct') is not None else 'unavailable')
                s['engine_explanation']=build_explanation(s)
                accepted,gate_reason=self.store.add_apex_signal(s,fingerprint)
                self.store.decision_event(s['decision_id'],now,{'event':'operational_commit' if accepted else 'operational_block',
                    'signal_id':s['id'] if accepted else None,'reason':gate_reason})
                event('risk.checked',asset=asset,signal_id=s['id'],status='accepted' if accepted else 'blocked',reason=gate_reason,
                    entry_at=s['entry_at'],expiry_at=s['expiry_at'],limits=self.store.apex_limits(now,s['entry_at']))
                if not accepted:
                    kind='duplicate' if 'duplicado' in gate_reason else 'pause' if 'pausa' in gate_reason else 'limit'
                    a['checks'].append({'asset':asset,'reason':gate_reason,'kind':kind})
                    continue
                self.intrabar_contexts.pop((a['source_key'],asset),None)
                accepted_signals.append(s)
                a['emitted_assets'][asset]=s['id']
                if found is None:
                    found=s
                event('selection.completed',asset=asset,candidates=len(pending),score=s['score'],learning=s.get('learning'),slot=len(accepted_signals))
                if len(accepted_signals) >= self.settings()['simultaneous_limit']:
                    break
            if self.observer and observation_inputs:
                try:
                    from .selection_observer import alternatives
                    session=self.observer.session(a['configuration'],a['created_at'])
                    for asset,rows,audit,received_at,identity,context_rows,context_status in observation_inputs:
                        decisions=alternatives(rows,audit['candidates'],minimum=self.settings()['minimum_score'],
                            strict=self.automatic_only,context=(identity,asset,timeframe,duration),
                            scope={'source_key':a['source_key'],'configuration_id':a['configuration']['id']},
                            frozen=self.observer.frozen_profiles(session,self.clock()),context_rows=context_rows,
                            context_status=context_status,now=received_at,min_lead=self.config.min_lead)
                        actual=audit['selected']
                        if actual and not actual.get('manual') and actual['score']<self.settings()['minimum_score']:actual=None
                        decisions.append({'selector':'reference_adaptive','candidate':actual,'reason':audit['reason'],
                                          'pool':audit['candidates']})
                        # Every qualifying existing engine has its own prospective outcome stream.
                        decisions += [{'selector':'candidate:'+c['strategy_resolved'],'candidate':c,'reason':'candidate universe; not sent',
                                       'pool':[c]} for c in audit['candidates'] if c.get('manual') or c['score']>=self.settings()['minimum_score']]
                        for decision in decisions:
                            self.observer.capture_selection(decision,session=session,asset=asset,source_key=a['source_key'],
                                source_identity=identity,rows=rows,interval=interval,timeframe=timeframe,duration=duration,
                                now=received_at,observed_at=self.clock(),min_lead=self.config.min_lead)
                except Exception:
                    event('selection.observation_failed',status='failed',exc_info=True)
            discarded=sum(c.get('kind')=='discarded' for c in a['checks'])
            status='sinal' if found else ('erro' if failures==len(a['assets']) else 'prévia' if a.get('preview') else 'sem oportunidade')
            message='Sinal identificado' if found else ('Não foi possível concluir a análise por falha nos dados ou na compatibilidade do contrato.' if status=='erro' else 'Prévia atualizada. Inicie o monitoramento para emitir sinais.' if a.get('preview') else '🔍 Nenhum setup confirmado no momento. Seguindo com a análise...')
            if not found and failures==len(a['assets']) and all(c.get('code')=='market_closed' for c in a['checks']):
                message='Mercado fechado para todos os ativos selecionados. Aguardando disponibilidade da Deriv.'
            if not found and discarded:
                status='descartada por atraso'
                message='Oportunidade encontrada, mas descartada por atraso na consulta ou prazo de entrada insuficiente.'
                a['retry_after_seconds']=15
            a['blocked_assets']=sum(c.get('kind')=='contract_blocked' for c in a['checks'])
            a['signals_emitted']=len(accepted_signals)
            if not found and a['blocked_assets']:
                message=f"Análise concluída · {a['blocked_assets']} ativo(s) com contrato não confirmado. Nenhum sinal liberado."
            a['discarded_assets']=discarded
            if not found and failures and status!='erro': message+=' — análise parcial: alguns ativos indisponíveis.'
            a.update(status=status,stage='concluída',evaluated_assets=evaluated,failed_assets=failures,partial=bool(failures),
                message=message,
                signal_id=found['id'] if found else None,signal_ids=[s['id'] for s in accepted_signals],finished_at=self.clock())
            self.save_analysis(a)
            for signal in accepted_signals:
                self.spawn(self.deliver(signal['id']))
        except asyncio.CancelledError:
            a.update(status='interrompida',message='Análise interrompida; solicite nova análise.',finished_at=self.clock())
            self.save_analysis(a)
            raise
        except Exception:
            event('analysis.failed',level=logging.ERROR,exc_info=True,analysis_id=a['id'])
            a.update(status='erro',message='Falha interna durante a análise. Consulte a configuração e tente novamente.',finished_at=self.clock())
            self.save_analysis(a)
        finally:
            self.analysis_active=False
            if self.current_analysis_task is asyncio.current_task(): self.current_analysis_task=None
            self.store.release(a['id'])
            event('analysis.finished',status=a['status'],reason=a.get('message'),evaluated_assets=a.get('evaluated_assets'),failed_assets=a.get('failed_assets'))
            CONTEXT.reset(context_token)

    async def deliver(self,sid):
        if sid in self.delivering: return
        context_token=CONTEXT.set({'signal_id':sid})
        self.delivering.add(sid)
        try: await self._deliver(sid)
        finally:
            self.delivering.discard(sid)
            CONTEXT.reset(context_token)

    async def _deliver(self,sid):
        for attempt in range(1,4):
            s=self.store.signal(sid)
            if not s: return
            if s['telegram']['status'] in ('enviado','incerto','falha','não configurado','expirado','enviando'): return
            # Reserve the entire preparation/queue/HTTP timeout before the
            # manual minimum, rather than checking only the start of delivery.
            started_at=self.clock()
            if s['state']=='cancelado' or not can_start(s,started_at):
                self.store.patch(sid,{'telegram':{'status':'expirado','detail':'Envio bloqueado: prazo de entrada insuficiente.'}},'telegram',self.clock()); return
            destination_key=getattr(self.telegram,'destination_key',lambda:None)()
            delivery={'status':'enviando','attempt':attempt,'delivery_started_at':started_at}
            if destination_key is not None:delivery['destination_key']=destination_key
            self.store.patch(sid,{'telegram':delivery},'telegram',self.clock())
            try:
                result=await asyncio.wait_for(self.telegram.send(s),SEND_TIMEOUT_SECONDS)
            except Exception:
                result={'status':'incerto','detail':'Envio sem confirmação dentro do prazo; sem reenvio automático.'}
            result['attempt']=attempt
            current=self.store.signal(sid) or s
            result=classify_ack(current,result,self.clock(),started_at)
            if result['status']=='repetir':
                delay=result.pop('retry_after')
                if type(delay) is not int or delay<1 or attempt == 3 or not can_start(s,self.clock()+delay):
                    result={'status':'falha','detail':'Limite de tentativas ou prazo de entrada atingido.','attempt':attempt}
                else:
                    result['retry_at']=self.clock()+delay
            # The outer deadline may cancel send() before it returns its metadata.
            # Retain only the opaque destination fingerprint, including on failure.
            if destination_key is not None:result.setdefault('destination_key',destination_key)
            self.store.patch(sid,{'telegram':result},'telegram',self.clock(),prepare_result=True)
            self.telegram_health={'status':result['status'],'detail':result.get('detail','')}
            if result['status']!='repetir': return
            await asyncio.sleep(delay)

    def cancel_entry(self, s, reason, code='setup_invalidated', audit=None):
        self.pending_quotes.pop(s['id'],None)
        current=self.store.signal(s['id'])
        if not current or current['state']!='aguardando entrada': return
        update={'state':'cancelado','result':'CANCELADO','settlement_pending':False,
                'estimated_pnl_brl':None,'cancellation_reason':reason,
                'entry_validation':{**current.get('entry_validation',{}),**(audit or {}),
                                    'status':'rejeitada','code':code,'reason':reason}}
        saved=self.store.patch(s['id'],update,'cancelado',self.clock(),prepare_result=True)
        event('entry.cancelled',signal_id=s['id'],asset=s['asset'],code=code,reason=reason,audit=audit)
        if saved.get('telegram_result',{}).get('status')=='pendente': self.spawn(self.deliver_result(s['id']))

    async def revalidate_entry(self, s):
        try:
            provider=self.providers.get(s['source_key'])
            if provider is None: raise DataError('Fonte original indisponível.', 'source_unavailable')
            rows=await asyncio.wait_for(provider.candles(s['asset'],60,4),8)
            identity=getattr(rows,'source_identity',getattr(provider,'identity',provider.name))
            if identity!=s['source_identity']: raise DataError('Identidade da fonte alterada.', 'source_mismatch')
            if self.observer:self.observer.remember(s['source_key'],s['asset'],60,rows,self.clock())
            audit,reason=entry_guard(s,rows,self.clock())
            if not reason and (s.get('manual_intrabar') or s.get('live_monitor_required')):
                from .entry_watch import check_live_entry
                point=await asyncio.wait_for(provider.live_price(s['asset']),8)
                now=self.clock()
                if now>=s['entry_at']-15:raise DataError('Cotação recebida depois do prazo de revalidação.','entry_deadline')
                live_audit,reason=check_live_entry(s,point,now);audit.update(live_audit)
                if not reason:self.pending_quotes[s['id']]=point
            current=self.store.signal(s['id'])
            if not current or current['state']!='aguardando entrada': return
            if reason:
                self.cancel_entry(current,reason,audit=audit)
            else:
                self.store.patch(s['id'],{'entry_validation':{**s['entry_validation'],**audit,'status':'confirmada'}},
                                 'entry_validated',self.clock())
                event('entry.confirmed',signal_id=s['id'],asset=s['asset'],audit=audit)
        except Exception as exc:
            code=getattr(exc,'code','timeout' if isinstance(exc,asyncio.TimeoutError) else 'validation_failed')
            timing=getattr(exc,'price_timing',None)
            self.cancel_entry(s,'Dados recentes não confirmados antes da entrada.',code,
                              audit={'price_timing':timing} if timing else None)
        finally:
            self.revalidating.discard(s['id'])

    async def tick(self):
        # State transitions cannot wait behind network requests for older outcomes.
        async with self.tick_lock:
            now=self.clock()
            await self.sync_price_streams()
            if not self.learning_tasks:
                for resolved in self.store.pending_learning_feedback(now):
                    if now>=self.learning_retry_at.get(resolved['id'],0):self.spawn(self.learn_result(resolved))
            for result_signal in self.store.pending_result_messages(now):
                self.spawn(self.deliver_result(result_signal['id']))
            for s in self.store.active():
                if s['telegram']['status']=='pendente': self.spawn(self.deliver(s['id']))
                validation=s.get('entry_validation')
                if s['state']=='aguardando entrada' and validation:
                    if validation['status']!='confirmada' and now>=s['entry_at']-15:
                        self.cancel_entry(s,'Revalidação não concluída antes da entrada.','entry_deadline')
                        continue
                    if validation['status']=='pendente' and now>=validation['scheduled_at']:
                        if s['id'] not in self.revalidating:
                            self.revalidating.add(s['id'])
                            self.spawn(self.revalidate_entry(s))
                        continue
                if now>=s['entry_at'] and s['state']=='aguardando entrada':
                    if s.get('live_monitor_required'):
                        from .entry_watch import check_live_entry
                        try:
                            point=self.pending_quotes.get(s['id'])
                            final_audit,reason=check_live_entry(s,point,s['entry_at'])
                        except (DataError,AttributeError):
                            final_audit={};reason='Cotação original recente indisponível antes da entrada.'
                        if reason:
                            self.cancel_entry(s,reason,audit=final_audit,code='entry_live_coverage');continue
                        self.store.patch(s['id'],{'entry_watch':{**final_audit,'covered_at_entry':True}},'entry_watch_complete',now)
                    self.store.patch(s['id'],{'state':'em andamento'},'em andamento',now)
                if now>=s['expiry_at']:
                    if s['state']!='encerrado':
                        self.store.patch(s['id'],{'state':'encerrado'},'encerrado',now)
                    if now<s['expiry_at']+65 or s['id'] in self.settling or len(self.settling)>=2: continue
                    if now-self.last_settlement.get(s['id'],0)<60: continue
                    self.settling.add(s['id'])
                    self.spawn(self.settle(s))
            if (self.observer and not self.analysis_active and not self.settling and not self.revalidating
                    and now-self.last_observer_tick>=10 and (self.observer_task is None or self.observer_task.done())):
                self.last_observer_tick=now
                self.observer_task=self.spawn(self.observer.step(self.providers,self.clock))
            # Optional research yields network capacity to operational work.
            if (self.research and not self.analysis_active and not self.settling and not self.revalidating
                    and now-self.last_research_tick>=10 and (self.research_task is None or self.research_task.done())):
                self.last_research_tick=now
                self.research_task=self.spawn(self.research.step(self.providers,self.clock))

    async def learn_result(self,signal):
        """Recompute existing causal preferences without blocking result delivery."""
        sid=signal['id']
        if sid in self.learning_tasks:return
        self.learning_tasks.add(sid)
        try:
            now=self.clock()
            if signal.get('settlement_pending') or signal.get('state')!='encerrado':return
            if (signal.get('expiry_at',math.inf)+60>now or
                    signal.get('settled_at',now)>now):return
            learned=await asyncio.to_thread(self.historical_learning,now)
            feedback=outcome_feedback(signal,learned,now)
            if self.store.record_learning_feedback(feedback):
                event('learning.feedback',signal_id=sid,asset=signal.get('asset'),result=signal.get('result'),
                      context_sample=feedback['context_sample'],preference_enabled=feedback['preference_enabled'],
                      veto_enabled=feedback['veto_enabled'],rule_change_applied=False)
            if now>=self.learning.get('evaluated_at',0):
                self.learning=learning_state(learned,now,self.store.latest_learning_feedback(now))
                self.learning['rules_version']=RULES_VERSION
            self.learning_retry_at.pop(sid,None)
        except Exception:
            self.learning_retry_at[sid]=self.clock()+60
            self.store.defer_learning_feedback(sid,self.learning_retry_at[sid])
            event('learning.feedback_failed',signal_id=sid,exc_info=True)
        finally:self.learning_tasks.discard(sid)

    async def settle(self,s):
        event('settlement.started',signal_id=s['id'],asset=s['asset'],entry_at=s['entry_at'],expiry_at=s['expiry_at'])
        try:
            async with self.settlement_slots:
                now=self.clock()
                self.last_settlement[s['id']]=now
                result={'state':'encerrado','result':'Aguardando apuração','result_kind':'indicativo',
                    'broker_confirmed':False,'settlement_pending':True,'settled_by_version':VERSION}
                try:
                    provider=self.providers.get(s['source_key'])
                    if provider is None:
                        result['settlement_rule']='Fonte original desativada nesta versão. Histórico preservado; sem apuração por outra fonte.'
                        result['settlement_pending']=False
                        result['result']='Resultado indisponível'
                        result['settled_at']=self.clock()
                        saved=self.store.patch(s['id'],result,'apuração',self.clock(),prepare_result=True)
                        self.spawn(self.learn_result(saved))
                        if saved.get('telegram_result',{}).get('status')=='pendente': self.spawn(self.deliver_result(s['id']))
                        self.last_settlement.pop(s['id'],None)
                        return
                    if hasattr(provider,'settlement_candles'):
                        rows=await asyncio.wait_for(provider.settlement_candles(s['asset'],s['entry_at'],s['expiry_at']),12)
                    else:
                        rows=await asyncio.wait_for(provider.candles(s['asset'],60,100),12)
                    if self.observer:self.observer.remember(s['source_key'],s['asset'],60,rows,self.clock())
                    identity=getattr(rows,'source_identity',getattr(provider,'identity',provider.name))
                    if s.get('source_identity')!=identity: raise DataError('Identidade da fonte alterada.')
                    if any(b.t<=a.t for a,b in zip(rows,rows[1:])) or any(not valid_candle(c) or not isinstance(c.t,int) or isinstance(c.t,bool) or c.t<=0 or c.t%60 or c.t>self.clock() for c in rows):
                        raise DataError('Cotações inválidas na apuração.')
                    quotes={c.t:c for c in rows}
                    start,end=quotes.get(s['entry_at']),quotes.get(s['expiry_at'])
                    if (not start or not end or start.t != s['entry_at'] or end.t != s['expiry_at']
                            or end.t <= start.t or s['expiry_at']-s['entry_at'] != s['duration_seconds']
                            or end.t+60 > self.clock()):
                        raise DataError('Cotações exatas fechadas não disponíveis.')
                    change=end.o-start.o
                    outcome='EMPATE' if change==0 else ('WIN' if (change>0)==(s['direction']=='CALL') else 'LOSS')
                    result.update(result=outcome,entry_price=start.o,expiry_price=end.o,
                        settlement_rule='Aberturas M1 exatas em entrada e expiração, mesma fonte. Indicativo, não confirmado pela corretora.',
                        settlement_evidence=[start.json(),end.json()],
                        settlement_source_identity=identity, settlement_entry_at=start.t,
                        settlement_expiry_at=end.t, settled_at=self.clock(), settlement_pending=False)
                    if s.get('stake_brl') is not None and s.get('payout_pct') is not None:
                        stake=s['stake_brl']
                        from decimal import Decimal, ROUND_HALF_UP
                        amount=Decimal(str(stake))
                        estimate=amount*Decimal(str(s['payout_pct']))/100 if outcome=='WIN' else -amount if outcome=='LOSS' else Decimal('0')
                        result['estimated_pnl_brl']=float(estimate.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
                except Exception:
                    event('settlement.unavailable',signal_id=s['id'],provider=s.get('source_key'),exc_info=True)
                    if self.clock()<max(s['expiry_at']+600,s.get('settlement_retry_until',0)): return
                    result['settlement_rule']='Sem cotações exatas válidas até o fim da janela de apuração ou recuperação histórica.'
                    result['result']='Resultado indisponível'
                    result['settlement_pending']=False
                    result['settled_at']=self.clock()
                event('settlement.finished',signal_id=s['id'],result=result['result'],entry_price=result.get('entry_price'),expiry_price=result.get('expiry_price'),settlement_rule=result.get('settlement_rule'))
                saved=self.store.patch(s['id'],result,'apuração',self.clock(),prepare_result=True)
                self.spawn(self.learn_result(saved))
                if saved.get('telegram_result',{}).get('status')=='pendente':
                    self.spawn(self.deliver_result(s['id']))
                self.last_settlement.pop(s['id'],None)
        finally:
            self.settling.discard(s['id'])

    async def deliver_result(self, sid):
        if not self.store.claim_result_message(sid,self.clock()): return
        signal=self.store.signal(sid)
        context_token=CONTEXT.set({'signal_id':sid})
        try:
            try:
                result=await asyncio.wait_for(self.telegram.send_result(signal,self.store.result_totals(verified=True,now=self.clock())),12)
            except Exception:
                result={'status':'incerto','detail':'Resultado sem confirmação de envio; sem reenvio automático.'}
            attempt=signal['telegram_result']['attempt']
            result.update(at=self.clock(),attempt=attempt)
            if result.get('status')=='repetir':
                delay=result.get('retry_after',5)
                if type(delay) is not int or not 1<=delay<=86400 or attempt>=3:
                    result.update(status='falha',code='retry_limit',detail='Limite de tentativas de resultado atingido ou prazo de repetição inválido. Resultado preservado no histórico.')
                else:
                    result['retry_at']=self.clock()+delay
                    result['detail']='Telegram limitou o envio; nova tentativa de resultado agendada.'
            self.store.patch(sid,{'telegram_result':result},'telegram resultado',self.clock())
            event('telegram.result_delivery',signal_id=sid,status=result.get('status'),attempt=attempt,
                  code=result.get('code'),retry_after=result.get('retry_after'),detail=result.get('detail'))
        finally:
            CONTEXT.reset(context_token)

    def snapshot(self):
        from .signal_visuals import annotations
        from .source_routing import assets_for_source, fresh_health, summarize_health, plans
        signals=self.store.visible_signals(20)
        public=[{**{k:v for k,v in s.items() if k!='evidence'},'chart_annotations':annotations(s)} for s in signals]
        feed=dict(self.feed)
        provider_health={key:fresh_health(self.source_health.get(key,{}),self.clock(),self.config.max_age) for key in self.providers}
        if self.automatic_sources and self.source_health:
            selected=plans(self,self.settings()['assets'],self.settings()['timeframe'])
            feed=summarize_health([{'source_key':p['source_key'],**provider_health[p['source_key']]} for p in selected],
                                  self.clock(),timeframe_max_age(self.settings()['timeframe'],self.config))
        if feed['status']=='disponível' and self.clock()-feed.get('data_at',0)>feed.get('max_age',self.config.max_age):
            feed.update(status='desatualizado',detail='Última consulta não representa mais dados atuais. Faça uma nova análise.')
        current=self.store.current_signal()
        if current:
            original=next((s for s in signals if s['id']==current['id']),None) or self.store.signal(current['id'])
            current={**current,'chart_annotations':annotations(original or current)}
        return {'version':VERSION,'operational_market':'OPCOES_BINARIAS' if self.config.asset_scope=='crypto_binary' else 'MODO_LEGADO',
            'asset_scope':self.config.asset_scope,'server_time':self.clock(),'timezone':'America/Sao_Paulo','busy':self.analysis_active or self.store.busy(self.clock()),
            'analysis_active':self.analysis_active,
            'apex_limits':self.store.apex_limits(self.clock()),'result_totals':self.store.result_totals(include_archived=False,verified=True,now=self.clock()),
            'learning':{**self.learning,'last_feedback':self.store.latest_learning_feedback(self.clock())},
            'risk_guidance':'1% a 2% da banca por operação; sem Martingale',
            'ai':self.ai_snapshot(),
            'scan':dict(self.scan),'intrabar':{**self.intrabar,'last_quotes':dict(self.intrabar['last_quotes']),
                'streams':[{'source_key':key[0],'asset':key[1],**value} for key,value in self.price_streams.status.items()]},'timeframes':self.available_timeframes,
            'credentials':credentials_status(self.config),
            'explanation':{'source':'motor','version':EXPLANATION_VERSION},
            'provider':self.provider_key,'source_mode':'automatic' if self.automatic_sources else 'manual','strategy':self.strategy_key,
            'strategies':[{'key':key,'name':key.replace('_',' ').title()} for key in STRATEGIES],
            'providers':[{'key':key,'name':provider.name,'configured':provider.ready,**provider_health[key]} for key,provider in self.providers.items()],
            'settings':self.settings(),'available_assets':self.available_assets,
            'asset_sources':{asset:next((key for key in self.providers if asset in assets_for_source(self,key)),self.provider_key) for asset in self.available_assets},
            'source':'Fontes automáticas' if self.automatic_sources else self.provider.name,
            'current_signal':current,'feed':feed,'telegram':self.telegram_health,'signals':public,
            'analyses':[{**a,'asset_previews':asset_previews(a)} for a in self.store.analyses()],
            'limitation':('Opções binárias cripto: 18 pares /USD configurados. Cada ativo precisa existir no catálogo da corretora, possuir contrato CALL/PUT M5 e payout confirmado >=90%; caso contrário nenhum sinal é publicado. Resultados são indicativos, sem execução.' if self.config.asset_scope=='crypto_binary' else 'Pares Forex legados: sujeitos à validação de contrato e payout na Deriv. Resultados indicativos; sem execução.')}
