"""Fixed experimental selectors over the original candidate pool; no Telegram or risk API."""
import hashlib
import json
import math
import sqlite3
import time
from functools import wraps
from .learning import profiles, PIPELINE_VERSION, RULES_VERSION, PROFILE_VERSION, selection_profile_key
from .research_store import ResearchStore, code_fingerprint
from .strategies import select_reference, SELECTOR_VERSION as OPERATIONAL_SELECTOR_VERSION
from .strategies.quality import FAMILIES
from .strategies.regime import classify
from .strategies.helpers import atr
from .validation_metrics import grouped, compare_selectors
from .version import VERSION
from .accuracy_target import POLICY as ACCURACY_POLICY, phase_groups
from .experiments import KEYS as PRICE_ACTION_KEYS, rule_snapshot
from .manual_book import snapshot as manual_snapshot
from .price_action_selection import SELECTOR as PRICE_ACTION_SELECTOR, policy_snapshot, as_candidate, select as select_price_action, study_summary
from .outcome_evidence import research_metric_view
from .entry_timing import next_entry, describe_entry
from .delivery_policy import SEND_TIMEOUT_SECONDS

SELECTOR_VERSION='1.26.16-observation-1'
PROTOCOL={
 'version':'1.26.16-manual-protocol-1','phase_days':{'development':14,'validation':28,'test':28},
 'entry_scheduling':{'alignment':'minute_open','send_budget_seconds':SEND_TIMEOUT_SECONDS},
 'minimum_resolved_per_combination':100,'minimum_resolved_days':20,
 'minimum_availability_pct':95,'primary_comparisons':['regime_router','history_frozen'],
 'exploratory_comparisons':[PRICE_ACTION_SELECTOR,'regime_structure_router','htf_regime_router',
                           *['price_action:'+key for key in PRICE_ACTION_KEYS]],
 'primary_reference':'reference_fixed','final_test_tuning':False,'automatic_promotion':False,
 'expectation_required_if_payout_known':True,'confidence':'95% whole-day blocks; Wilson descriptive only',
 'activation_rule':'Manual review only: adequate validation AND final test; positive paired day-block lower bound where paired comparison is meaningful, stable availability/frequency/regime, expectation if known. Exclusives require separate assessment. No automatic promotion.',
 'embargo_seconds':65,'strategies_added':'manual_operational_by_user_request',
 'manual_rules':manual_snapshot(),
 'price_action_rules':{key:rule_snapshot(key) for key in PRICE_ACTION_KEYS},
 'price_action_selection':policy_snapshot(),
 'comparison_policy':'Common same-direction signals AND same-decision pairs including opposing directions; exclusive sets assessed separately. Price/time proofs required.',
 'accuracy_goal':ACCURACY_POLICY,
}


def config_snapshot(config,settings):
    # Collection is not an operational rule and must not split normal history.
    settings={k:v for k,v in settings.items() if k!='selection_observation'}
    values={'code_version':VERSION,'code_sha256':code_fingerprint(),'rules_version':RULES_VERSION,
            'pipeline_version':PIPELINE_VERSION,'selector':'manual_price_action_ob','selector_version':OPERATIONAL_SELECTOR_VERSION,
            'settings':settings,'min_lead_seconds':config.min_lead,'max_age_seconds':config.max_age,
            'htf_active':False,'news_filter_active':False,'protocol':PROTOCOL}
    values['id']=hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return values


def freeze_profiles(records,cutoff):
    by_regime={}
    for row in records:
        if row.get('selector','').startswith('candidate:') and row.get('state')=='settled' and row.get('settled_at',math.inf)<=cutoff:
            by_regime.setdefault(row['regime'],[]).append({**row,'mode':'observacao'})
    frozen=[]
    for regime,rows in by_regime.items():
        for key,value in profiles(rows,cutoff).items():
            frozen.append({'key':list(key),'regime':regime,**value})
    return frozen


def alternatives(rows,candidates,*,minimum,strict,context,frozen,scope=None,context_rows=None,context_status=None,now=None,min_lead=60):
    from .strategies import eligible
    from .selection_context import structure_context,higher_context
    pool=[c for c in candidates if eligible(c,minimum)]
    ref,reason=select_reference(rows,pool,minimum=minimum)
    decisions=[{'selector':'reference_fixed','candidate':ref,'reason':reason,'pool':pool}]
    interval={'M1':60,'M2':120,'M3':180,'M5':300}.get(context[2],300)
    cutoff=rows[-1].t+interval
    structural=structure_context(rows,interval,cutoff)
    regime=structural['regime']
    families={'tendencial':{'retest','flow','breakout'},'lateral':{'reversal'},
              'transicao':{'reversal','retest','breakout'}}[regime]
    routed=[c for c in pool if FAMILIES.get(c['strategy_resolved']) in families and
            (regime!='tendencial' or c['direction']==structural.get('direction'))]
    choice,why=select_reference(rows,routed,minimum=minimum)
    decisions.append({'selector':'regime_router','candidate':choice,'reason':why,'pool':routed,'context_evidence':structural})
    qualified=[]
    for c in routed:
        key=selection_profile_key(context,c,{**(scope or {}),'regime':regime},mode='observacao')
        profile=next((p for p in frozen if p['key']==list(key) and p.get('eligible') and p.get('profile_version')==PROFILE_VERSION),None)
        if profile:qualified.append((c,profile))
    historical=[c for c,p in qualified]
    choice,why=select_reference(rows,historical,minimum=minimum)
    decisions.append({'selector':'history_frozen','candidate':choice,'reason':why if qualified else
        'Amostra de desenvolvimento selada insuficiente neste contexto.', 'pool':historical,
        'profile_evidence':[{'strategy':c['strategy_resolved'],**p} for c,p in qualified]})
    decisions.append({'selector':'regime_structure_router','candidate':decisions[1]['candidate'],
                      'reason':decisions[1]['reason'],'pool':routed,'context_evidence':structural})
    htf=higher_context(context_rows if context_rows is not None else rows,interval,cutoff)
    if context_status:htf['buffer']=context_status
    current=decisions[1]['candidate']
    aligned=current and htf['status']=='available' and htf['direction']==current['direction']
    decisions.append({'selector':'htf_regime_router','candidate':current if aligned else None,
        'reason':'' if aligned else 'Período superior fechado indisponível ou sem alinhamento.',
        'pool':routed,'context_evidence':htf})
    from .experiments import evaluate
    results=[]
    for key in PRICE_ACTION_KEYS:
        research=evaluate(key,rows,now=cutoff if now is None else now,interval=interval)
        results.append(research);candidate=as_candidate(research) if research['accepted'] else None
        decisions.append({'selector':'price_action:'+key,'strategy':key,'candidate':candidate,
                          'reason':research['reason'],'pool':[candidate] if candidate else [],
                          'rules_version':research['version'],'context_evidence':research})
    decisions.append(select_price_action(results,structural,now=cutoff if now is None else now,interval=interval,min_lead=min_lead))
    return decisions


def serialized(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        with self.lock:return method(self,*args,**kwargs)
    return wrapped


class SelectionObserver(ResearchStore):
    def __init__(self,path):
        super().__init__(path)
        self._report_cache={}
        self.quote_cache={}
        self.db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created REAL, payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS profile_freezes(session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.execute("CREATE TRIGGER IF NOT EXISTS freeze_no_update BEFORE UPDATE ON profile_freezes BEGIN SELECT RAISE(ABORT,'immutable freeze'); END")
        self.db.execute('CREATE TABLE IF NOT EXISTS prediction_origins(id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS observation_events(id INTEGER PRIMARY KEY, observation_id TEXT, payload TEXT NOT NULL)')
        self.db.execute("CREATE INDEX IF NOT EXISTS observations_session ON observations(json_extract(payload,'$.session_id'),created)")
        self.db.execute("CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON prediction_origins BEGIN SELECT RAISE(ABORT,'immutable prediction'); END")
        self.db.execute("CREATE TRIGGER IF NOT EXISTS sessions_no_update BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT,'immutable session'); END")
        self.db.commit()

    def remember(self,source_key,asset,interval,rows,now):
        self.quote_cache[(source_key,asset,interval)]=(now,rows)

    async def step(self,providers,clock):
        wrapped={key:SharedQuotesProvider(self,key,p,clock) for key,p in providers.items()}
        await super().step(wrapped,clock)

    @serialized
    def patch(self,record,**updates):
        allowed={'state','result','settled_at','entry_price','expiry_price','settlement_evidence','settlement_source_identity',
                 'last_attempt','last_error','entry_validation','cancellation_reason'}
        if not set(updates)<=allowed:raise ValueError('Cannot rewrite an original selection decision.')
        with self.db:
            self.db.execute('INSERT INTO observation_events(observation_id,payload) VALUES(?,?)',
                            (record['id'],json.dumps(updates,ensure_ascii=False,allow_nan=False)))
        return super().patch(record,**updates)

    @serialized
    def session(self,configuration,now):
        sid=configuration['id']
        row=self.db.execute('SELECT payload FROM sessions WHERE id=?',(sid,)).fetchone()
        if row:return json.loads(row[0])
        frozen=[]  # Cold start. Train only on this session's sealed development window.
        item={'id':sid,'created_at':now,'configuration':configuration,'frozen_profiles':frozen,'protocol':PROTOCOL}
        with self.db:self.db.execute('INSERT INTO sessions VALUES(?,?,?)',(sid,now,json.dumps(item,ensure_ascii=False)))
        return item

    @serialized
    def frozen_profiles(self,session,now):
        cutoff=session['created_at']+session.get('protocol',PROTOCOL)['phase_days']['development']*86400
        if now<cutoff:return []
        saved=self.db.execute('SELECT payload FROM profile_freezes WHERE session_id=?',(session['id'],)).fetchone()
        if saved:return json.loads(saved[0])['profiles']
        development=[r for r in self.rows() if r.get('session_id')==session['id'] and r['created_at']<cutoff
                     and r['created_at']>=session['created_at'] and r['entry_at']>=session['created_at']
                     and r['expiry_at']+65<cutoff and r.get('settled_at',math.inf)<cutoff]
        frozen=freeze_profiles(development,cutoff)
        record={'session_id':session['id'],'training_cutoff':cutoff,'frozen_at':now,'profiles':frozen}
        with self.db:self.db.execute('INSERT INTO profile_freezes VALUES(?,?)',(session['id'],json.dumps(record,ensure_ascii=False)))
        return frozen

    @serialized
    def capture_selection(self,decision,*,session,asset,source_key,source_identity,rows,interval,timeframe,duration,now,observed_at,min_lead):
        candidate=decision['candidate'];selector=decision['selector'];regime=decision.get('regime') or classify(rows) or 'transicao'
        # Regional candidates share their original movement across later bars.
        setup=str(candidate.get('setup_identity',candidate.get('setup_candle',rows[-1].t))) if candidate else str(rows[-1].t)
        movement=None
        if candidate and candidate.get('setup_contract'):
            from .setup_contract import movement_id
            movement=movement_id({**candidate,'asset':asset,'source_key':source_key,
                                  'source_identity':source_identity,'timeframe':timeframe})
            if movement:setup=movement
        intrabar=bool(decision.get('intrabar_observed'))
        identity=(session['id'],selector,source_identity,asset,timeframe,duration,setup,candidate.get('direction') if candidate else None,intrabar)
        rid=hashlib.sha256(repr(identity).encode()).hexdigest()
        entry=next_entry(now,min_lead)
        if intrabar:entry=decision['planned_entry_at']
        if candidate and candidate.get('entry_policy')=='next_analysis_candle':
            entry=candidate['target_entry_at']
        item={'id':rid,'session_id':session['id'],'configuration_id':session['id'],
              'configuration':session['configuration'],'selector':selector,'selector_version':SELECTOR_VERSION,
              'profile_evidence':decision.get('profile_evidence'),'context_evidence':decision.get('context_evidence'),
              'strategy':candidate['strategy_resolved'] if candidate else decision.get('strategy','none'),
              'rules_version':decision.get('rules_version',session['configuration'].get('rules_version',RULES_VERSION)),
              'pipeline_version':session['configuration'].get('pipeline_version',PIPELINE_VERSION),
              'asset':asset,'source_key':source_key,'source_identity':source_identity,'regime':regime,
              'regime_method':decision.get('regime_method','EMA9/21 normalizada por ATR'),
              'timeframe':timeframe,'interval_seconds':interval,'duration_seconds':duration,
              'created_at':now,'observed_at':observed_at,'entry_at':entry,'expiry_at':entry+duration,
              'entry_timing':describe_entry(entry,interval,now,min_lead),
              'accepted':bool(candidate),'direction':candidate.get('direction') if candidate else None,
              'apex_setup':candidate.get('apex_setup') if candidate else None,
              'movement_id':movement,
              'setup_contract':candidate.get('setup_contract') if candidate else None,
              'score':candidate.get('score') if candidate else None,'reason':decision['reason'],
              'pool':decision['pool'],'decision':candidate,'result':None,
              'state':'pending_validation' if candidate else 'rejected','sent_to_telegram':False,'result_kind':'experimental_indicativo',
              'broker_confirmed':False,'invalidation':candidate.get('invalidation',[]) if candidate else [],
              'evidence':{'candles':[c.json() for c in rows],'source_identity':source_identity},
              'payout_pct':session['configuration']['settings'].get('payout_pct'),'payout_kind':'user_declared_estimate'}
        if candidate:
            from .tracing import score_composition
            item['score_composition']=score_composition(candidate)
            item['entry_validation']={'status':'pendente','atr':candidate.get('manual_atr',candidate.get('atr',atr(rows))),'scheduled_at':entry-45}
            if candidate.get('manual_intrabar'):
                item.update(manual_intrabar=True,live_monitor_required=True,manual_atr=candidate['manual_atr'],
                            live_observation=candidate['live_observation'],live_trigger_observations=candidate['live_trigger_observations'],
                            duration_basis='intrabar_planned',intrabar_observed=True)
                item['evidence']['live_observation']=candidate['live_observation']
                item['evidence']['live_trigger_observations']=candidate['live_trigger_observations']
            contract=candidate.get('setup_contract') or {}
            if contract.get('valid_until') is not None and entry>contract['valid_until']:
                item.update(state='cancelled',result='CANCELADO',settled_at=observed_at,
                            cancellation_reason='Contrato original do setup vencido antes da entrada.')
            if candidate.get('experimental'):
                item.update(experimental_setup=True,setup_identity=candidate.get('setup_identity'),
                            valid_until=candidate.get('valid_until'),invalidation_line=candidate.get('invalidation_line'),
                            duration_reason='Duração configurada pelo usuário; hipótese avaliada separadamente, sem previsão de eficácia.')
                if candidate.get('entry_policy')=='next_analysis_candle':
                    item['entry_timing']={'policy':'next_analysis_candle','target_entry_at':entry,
                        'entry_window_end':candidate['entry_window_end'],'decision_at':now,'observed_at':observed_at,
                        'lead_seconds':entry-observed_at,'latency_seconds':max(0,observed_at-entry),
                        'minimum_lead_seconds':min_lead,'actionable':observed_at<=entry-min_lead}
                    if observed_at>entry-min_lead:
                        item.update(state='cancelled',result='CANCELADO',settled_at=observed_at,
                            cancellation_reason='Abertura da próxima vela sem antecedência após confirmar fechamento; referência preservada, sem remarcar entrada.')
                if entry>candidate['valid_until']:
                    item.update(state='cancelled',result='CANCELADO',settled_at=observed_at,
                                cancellation_reason='Setup experimental vencido antes da entrada planejada.')
            # Never roll a stale decision forward to a newer minute at commit time.
            required_lead=min_lead if candidate.get('entry_policy')=='next_analysis_candle' else min_lead+SEND_TIMEOUT_SECONDS
            if item['state']=='pending_validation' and (observed_at>entry-required_lead or int(observed_at//interval)!=int(now//interval) or rows[-1].t+interval!=int(observed_at//interval)*interval):
                item.update(state='cancelled',result='CANCELADO',settled_at=observed_at,cancellation_reason='Snapshot expired during comparison.')
        try:
            with self.db:
                encoded=json.dumps(item,ensure_ascii=False,allow_nan=False)
                self.db.execute('INSERT INTO observations VALUES(?,?,?)',(rid,now,encoded))
                self.db.execute('INSERT INTO prediction_origins VALUES(?,?)',(rid,encoded))
        except sqlite3.IntegrityError:return None
        return item

    def report(self,now,include_final_test=False,session_id=None):
        with self.lock:
            key=(session_id,include_final_test,self.db.total_changes)
            cached=self._report_cache.get(key)
            if cached and cached[0]<=now<cached[0]+5 and time.monotonic()-cached[1]<5:
                return cached[2]
        report=self._report(now,include_final_test,session_id)
        with self.lock:
            if not getattr(self,'closed',False) and self.db.total_changes==key[2]:
                if len(self._report_cache)>=4:self._report_cache.clear()
                self._report_cache[key]=(now,time.monotonic(),report)
        return report

    def _report(self,now,include_final_test=False,session_id=None):
        clause=' AND id=?' if session_id else ''
        args=(now,session_id) if session_id else (now,)
        # Copy database inputs atomically, then release the reader lock before
        # bootstrap/comparison calculations. Operational writes must not wait
        # behind a report worker holding the lock for the whole computation.
        with self.lock:
            sessions=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM sessions WHERE created<=?'+clause+' ORDER BY created',args)]
            scope=" AND json_extract(payload,'$.session_id')=?" if session_id else ''
            rows=[json.loads(r[0]) for r in self.db.execute("SELECT json_remove(payload,'$.evidence','$.pool','$.decision','$.configuration','$.profile_evidence','$.score_composition') FROM observations WHERE created<=?"+scope,args)]
            freezes={r[0]:json.loads(r[1]) for r in self.db.execute('SELECT session_id,payload FROM profile_freezes '
                'WHERE session_id IN (SELECT id FROM sessions WHERE created<=?'+clause+')',args)}
        # Reporting an earlier cutoff must not expose decisions or outcomes known later.
        # Transform only this view; immutable origins and outcome events remain intact.
        rows=[r for r in rows if r.get('observed_at',r['created_at'])<=now]
        rows=[research_metric_view(r,now) for r in rows]
        reports=[]
        for session in sessions:
            scope=[r for r in rows if r['session_id']==session['id']]
            accepted=[r for r in scope if r['accepted']]
            protocol=session.get('protocol',PROTOCOL)
            days=protocol['phase_days'];start=session['created_at'];dev=start+days['development']*86400
            valid=dev+days['validation']*86400;end=valid+days['test']*86400
            visible=[r for r in scope if not valid<=r['created_at']<end or (include_final_test and now>=end)]
            visible_accepted=[r for r in visible if r['accepted']]
            phases=[]
            for name,lo,hi in [('development',start,dev),('validation',dev,valid),('test',valid,end)]:
                cutoff=min(now,hi-0.000001)
                subset=[r for r in accepted if lo<=r['created_at']<hi and r['entry_at']>=lo and r['expiry_at']+65<hi]
                subset=[research_metric_view(r,cutoff) for r in subset]
                sealed=name=='test' and not (include_final_test and now>=end)
                policy=protocol.get('accuracy_goal')
                phases.append({'phase':name,'start_at':lo,'end_at':hi,'complete':now>=hi,
                               'sealed':sealed,
                               'groups':grouped(subset,lo,min(now,hi)) if now>lo and not sealed else [],
                               'target_groups':phase_groups(subset,policy=policy,cutoff=cutoff,complete=now>=hi)
                                   if policy and now>lo and not sealed else [],
                               'target_status':'sealed' if sealed else 'available' if policy else 'legacy_protocol'})
            frozen_record=freezes.get(session['id'])
            if frozen_record and frozen_record.get('frozen_at',0)>now:frozen_record=None
            reports.append({'frozen_profile_record':frozen_record,'session':session,'decisions':len(scope),'rejected':sum(not r['accepted'] for r in scope),
                            'groups':grouped(visible_accepted,start,now),'phases':phases,
                            'outcomes_without_quote_proof':sum(r.get('outcome_proof_status')=='invalid_or_missing_price_proof' for r in visible_accepted),
                            'metrics_version':'1.26.8-research-price-proofs',
                            'price_action_study':study_summary(visible,protocol),
                            'purge_note':'Cross-boundary operations are purged; late outcomes stay pending at the phase cutoff. Aggregate comparisons are descriptive; final-test groups stay sealed.',
                            'comparisons':[compare_selectors(visible,right=x) for x in protocol['primary_comparisons']],
                            'exploratory_comparisons':[compare_selectors(visible,right=x) for x in protocol.get('exploratory_comparisons',[])],
                            'adaptive_comparison':compare_selectors(visible,right='reference_adaptive'),
                            'final_test_status':'opened' if include_final_test and now>=end else 'sealed',
                            'promotion':'experimental; no automatic activation'})
        return {'protocol':PROTOCOL,'sessions':reports,'status':'no observations' if not sessions else 'experimental',
                'note':'No claim of efficacy. Candidate-universe records are not signals sent and do not count against operational limits.'}

    @serialized
    def overview(self,now,limit=50):
        sessions=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM sessions WHERE created<=? ORDER BY created DESC LIMIT ?',(now,limit))]
        total=self.db.execute('SELECT count(*) FROM sessions WHERE created<=?',(now,)).fetchone()[0]
        return {'status':'experimental' if sessions else 'no observations','total_sessions':total,
                'sessions':[{'id':s['id'],'created_at':s['created_at'],'protocol':s.get('protocol',PROTOCOL)} for s in sessions],
                'limit':limit,'automatic_promotion':False}

class SharedQuotesProvider:
    """Reuse fresh M1 quotes with exact provenance; otherwise call the original provider."""
    def __init__(self,observer,key,provider,clock):
        self.observer,self.key,self.provider,self.clock=observer,key,provider,clock

    def __getattr__(self,name):
        return getattr(self.provider,name)

    async def candles(self,asset,interval=60,count=100):
        from .market import CandleBatch, valid_candle
        cached=self.observer.quote_cache.get((self.key,asset,interval))
        now=self.clock()
        if cached and now-cached[0]<=5:
            rows=cached[1]
            closed=[c for c in rows if c.t+interval<=now]
            if len(rows)>=count and all(b.t-a.t==interval for a,b in zip(rows,rows[1:])) and closed and closed[-1].t+interval==int(now//interval)*interval and all(valid_candle(c) for c in rows):
                return CandleBatch(rows[-count:],getattr(rows,'source_identity',getattr(self.provider,'identity',self.provider.name)))
        rows=await self.provider.candles(asset,interval,count)
        self.observer.remember(self.key,asset,interval,rows,self.clock())
        return rows

    async def settlement_candles(self,asset,entry,expiry):
        from .market import CandleBatch, valid_candle
        cached=self.observer.quote_cache.get((self.key,asset,60))
        if cached and self.clock()-cached[0]<=5:
            rows=cached[1];times={c.t for c in rows}
            if entry in times and expiry in times and expiry+60<=self.clock() and all(valid_candle(c) for c in rows):
                return CandleBatch(rows,getattr(rows,'source_identity',getattr(self.provider,'identity',self.provider.name)))
        rows=await (self.provider.settlement_candles(asset,entry,expiry) if hasattr(self.provider,'settlement_candles') else self.provider.candles(asset,60,100))
        self.observer.remember(self.key,asset,60,rows,self.clock())
        return rows
