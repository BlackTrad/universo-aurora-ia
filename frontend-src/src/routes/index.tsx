import {useEffect,useState,useRef,type CSSProperties} from 'react';
import {AppShell} from '@/components/AppShell';
import {DashboardHero} from '@/components/DashboardHero';
import {AssetCatalogue} from '@/components/AssetCatalogue';
import {MarketChart} from '@/components/MarketChart';
import {ObservedRate} from '@/components/ObservedRate';
import {matchingAnalysis} from '@/components/AssetPreviews';
import {api,useData,mapSignal} from '@/lib/api';
import {assetLabel,tfLabel,fmtHM,fmtHMS,fmtCountdown,fmtPrice,type Signal} from '@/lib/apex';
import {strategyLabel} from '@/lib/presentation';
import {chartTimeframeFor} from '@/lib/chartTimeframe';
import {signalStage,telegramStage} from '@/lib/signalStatus';
type MarketCandle={openTime:number;open:number;high:number;low:number;close:number};
export default function Dashboard(){
 const {snapshot,connected,error,setError,now,refresh,reportChartStatus}=useData();
 const [selectedAsset,setSelectedAsset]=useState('EUR/USD'),[chartTimeframe,setChartTimeframe]=useState<string>('1m'),[chartSource,setChartSource]=useState(''),[followSignals,setFollowSignals]=useState(true);
 const [chartCandles,setChartCandles]=useState<MarketCandle[]>([]),[chartLoading,setChartLoading]=useState(false),[actionBusy,setActionBusy]=useState(false);
 const [chartMeta,setChartMeta]=useState<any>(null);
 const [showAnnotations,setShowAnnotations]=useState(true);
 const actionPending=useRef(false),instrumentInitialized=useRef(false);
 const [explicitInstrument]=useState(()=>Boolean(new URLSearchParams(location.search).get('source')&&new URLSearchParams(location.search).get('asset')));
 const source=snapshot?.provider||'';
 const chartInterval=chartTimeframe==='5m'?300000:chartTimeframe==='M3'?180000:chartTimeframe==='M2'?120000:60000;
 const observedSignal=useRef<{at:number;id:string}>({at:0,id:''});
 const selectInstrument=(provider:string,asset:string,tf?:string)=>{setChartSource(provider);setSelectedAsset(asset);setChartTimeframe(current=>chartTimeframeFor(provider,tf??current))};
 useEffect(()=>{if(!snapshot)return;const params=new URLSearchParams(instrumentInitialized.current?'':location.search),requested=params.get('source');instrumentInitialized.current=true;const asset=params.get('asset')||snapshot.settings.assets[0];const provider=snapshot.providers?.some((p:any)=>p.key===requested)?requested:(snapshot.asset_sources?.[asset]||source);selectInstrument(provider,asset,params.get('timeframe')||undefined);},[source]);
 useEffect(()=>{if(!snapshot||!connected)return;const latest=[...(snapshot.signals||[])].sort((a:any,b:any)=>(b.emitted_at??b.created_at)-(a.emitted_at??a.created_at)||String(b.id).localeCompare(String(a.id)))[0];if(!latest)return;const at=latest.emitted_at??latest.created_at;const seen=observedSignal.current;if(at<seen.at||(at===seen.at&&String(latest.id)<=seen.id))return;observedSignal.current={at,id:String(latest.id)};if(followSignals&&(seen.at!==0||(!explicitInstrument&&['aguardando entrada','em andamento'].includes(latest.state))))selectInstrument(latest.source_key,latest.asset,latest.timeframe);},[snapshot,connected,followSignals]);
 const settings=snapshot?{...snapshot.settings,active_assets:snapshot.settings.assets,monitoring:snapshot.scan.active}:null;

 useEffect(()=>{if(!chartSource)return;let stop=false,timer:any;const controller=new AbortController();const info={source_key:chartSource,asset:selectedAsset,timeframe:tfLabel(chartTimeframe),interval_seconds:chartInterval/1000};async function load(){if(document.hidden){timer=setTimeout(load,15000);return;}setChartLoading(true);try{const d=await api('/chart?asset='+encodeURIComponent(selectedAsset)+'&source='+chartSource+'&timeframe='+tfLabel(chartTimeframe),undefined,undefined,controller.signal);if(d.source_key!==chartSource||d.asset!==selectedAsset||d.timeframe!==tfLabel(chartTimeframe))throw Error('Resposta do gráfico não corresponde ao instrumento solicitado.');if(!stop){setChartCandles(d.candles.map((c:any)=>({openTime:c.t*1000,open:c.o,high:c.h,low:c.l,close:c.c})));setChartMeta(d);reportChartStatus({...info,...d});}}catch(e:any){if(!stop){setChartMeta((old:any)=>({...old,status:'unavailable',detail:e.message}));reportChartStatus({...info,status:'unavailable'})}}finally{if(!stop){setChartLoading(false);timer=setTimeout(load,15000)}}}setChartCandles([]);setChartMeta(null);reportChartStatus({...info,status:'loading'});load();return()=>{stop=true;controller.abort();clearTimeout(timer)}},[selectedAsset,chartTimeframe,chartSource]);
 const toggleMonitoring=async()=>{
  if(!snapshot||!connected||actionPending.current)return;
  actionPending.current=true;setActionBusy(true);setError('');
  try{const stop=Boolean(snapshot.scan.active||snapshot.analysis_active);await api(stop?'/scan/stop':'/scan/start',stop?{}:snapshot.settings);await refresh()}
  catch(e:any){setError(e.message)}finally{actionPending.current=false;setActionBusy(false)};
 };
 const runScan=async()=>{if(!snapshot||actionPending.current)return;actionPending.current=true;setActionBusy(true);setError('');try{await api('/analyze',snapshot.settings);await refresh();}catch(e:any){setError(e.message)}finally{actionPending.current=false;setActionBusy(false)}};
 const scanning=actionBusy||snapshot?.busy;
 const lastScan=snapshot?.analyses?.[0]?.finished_at*1000||null;
 const signals=(snapshot?.signals||[]).map(mapSignal);
 const current=snapshot?.current_signal?mapSignal(snapshot.current_signal):null;
 const pending=(s:any)=>s&&['PENDING','APURANDO'].includes(s.result);
 const active=connected?(pending(current)?current:signals.find(pending)):null;
 const lastResolved=signals.find((s:any)=>s.result!=='PENDING');
 const risk={blockedReason:snapshot?.apex_limits?.pause_until>now/1000?'Pausa após perdas consecutivas':snapshot?.apex_limits?.remaining===0?'Limite de sinais atingido':null,pauseUntil:(snapshot?.apex_limits?.pause_until||0)*1000};
 const rawAI=snapshot?.ai || {status:'unavailable',detail:'Status da camada AI indisponível.',last_decision:null};
 const aiStatus=connected?{...rawAI,last_decision:['approved','rejected'].includes(rawAI.status)?rawAI.last_decision:null}:
  {...rawAI,status:'unavailable',detail:'Sem conexão com o backend. O consenso atual não pode ser confirmado.',last_decision:null};
 const analysis=matchingAnalysis(snapshot);
 const canStop=Boolean(settings?.monitoring||snapshot?.analysis_active);
 const chartSignal=[current,...signals].find((s:any)=>s?.chart_annotations&&s.source_key===chartSource&&s.asset===selectedAsset&&s.timeframe===tfLabel(chartTimeframe)&&s.source_identity===chartMeta?.source_identity);
 const annotation=showAnnotations&&connected?chartSignal?.chart_annotations:null;

 if(!settings)return <AppShell><div className="dashboard-layout"><DashboardHero/><section className="panel-card p-5" aria-label="Conexão com o painel"><h2 className="mb-2 font-semibold">{error?'Conexão indisponível':'Carregando painel'}</h2><p role={error?'alert':'status'} className={error?'text-sm text-warn':'text-sm text-muted-foreground'}>{error||'Aguardando as configurações e os dados do AURORA.'}</p>{error&&<p className="mt-2 text-xs text-muted-foreground">Uma nova tentativa é feita automaticamente.</p>}</section></div></AppShell>;
 const selectedPrice=chartMeta;
 const chartLabel=chartMeta?.source||({deriv:'Deriv',binance:'Binance',coinbase:'Coinbase'} as Record<string,string>)[chartSource]||chartSource;
  return (
    <AppShell>
      <div className="dashboard-layout">
        <DashboardHero assetCount={settings.active_assets.length} timeframe={settings.timeframe}/>
        {error && <p role="alert" className="rounded border border-bear p-3 text-sm text-bear">{error}</p>}

        <section className="panel-card control-card relative overflow-hidden p-3 animate-fade-in" style={{ animationDelay: "80ms" }}>
          {settings.monitoring && <span className="scan-sweep" />}
          <div className="relative flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-3">
              <span className={`h-3 w-3 rounded-full ${settings.monitoring ? "animate-pulse bg-bull" : "bg-muted-foreground"}`} />
              <div><h2 className="font-semibold">{settings.monitoring ? (snapshot.scan.preview ? "Prévia automática sem envio" : "Análise em andamento") : snapshot?.analysis_active ? "Prévia em andamento" : "Iniciar análise"}</h2><p className="text-xs text-muted-foreground">{!settings.monitoring&&snapshot?.analysis_active ? "Prévia técnica sem envio. Você pode interromper em Parar." : snapshot?.scan?.message || "Aguardando início"} · última {lastScan ? fmtHMS(lastScan) : "—"}</p>{settings.monitoring&&snapshot.scan.next_run_at&&<p className="mt-1 text-xs text-muted-foreground" role="timer">Próxima avaliação em {fmtCountdown(snapshot.scan.next_run_at*1000-now)} · {tfLabel(settings.timeframe)}</p>}</div>
            </div>
            <div className="flex w-full gap-2 sm:w-auto">
              <button onClick={runScan} disabled={scanning || settings.monitoring || !connected} className="flex-1 whitespace-nowrap rounded border border-border px-3 py-2 text-sm transition-colors hover:bg-accent disabled:opacity-50">{scanning ? "Analisando..." : "Analisar agora"}</button>
              <button disabled={actionBusy || !connected || (!canStop&&snapshot.busy)} onClick={toggleMonitoring} className={`flex-1 rounded px-4 py-2 text-sm font-semibold transition-all sm:flex-none ${canStop ? "bg-bear text-background" : "bg-primary text-primary-foreground"}`}>{canStop ? "Parar" : "Iniciar"}</button>
            </div>
          </div>
        </section>

        <section className="panel-card p-3 animate-fade-in" style={{ animationDelay: "110ms" }} aria-label="Status da IA planetária">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-xs uppercase text-muted-foreground">Consenso planetário</p>
              <h2 className="font-semibold">IA · {aiStatus.status === 'approved' ? 'Aprovado' : aiStatus.status === 'rejected' ? 'Rejeitado' : aiStatus.status === 'processing' ? 'Processando' : aiStatus.status === 'ready' ? 'Pronta' : 'Indisponível'}</h2>
            </div>
            <div className={`rounded border px-2 py-1 text-xs font-semibold ${aiStatus.status === 'approved' ? 'border-bull/30 bg-bull/10 text-bull' : aiStatus.status === 'rejected' ? 'border-bear/30 bg-bear/10 text-bear' : aiStatus.status === 'processing' ? 'border-primary/30 bg-primary/10 text-primary' : 'border-border bg-secondary/30 text-muted-foreground'}`}>
              {aiStatus.last_decision?.direcao || '—'} {aiStatus.last_decision?.confianca != null ? `· ${(aiStatus.last_decision.confianca).toFixed(0)}%` : ''}
            </div>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">{aiStatus.detail || 'Sem decisão recente.'}</p>
          {aiStatus.context?.ativo&&<p className="mt-1 text-xs text-muted-foreground">{connected?'Rodada':'Última rodada recebida'}: {aiStatus.context.ativo} · {aiStatus.context.timeframe||'Período não informado'}{aiStatus.context.fonte?' · '+aiStatus.context.fonte:''}</p>}
          {aiStatus.last_decision && <p className="mt-2 text-xs text-muted-foreground">Última decisão: {aiStatus.last_decision.direcao} com {Number(aiStatus.last_decision.confianca || 0).toFixed(0)}% de confiança{aiStatus.last_decision.motivo ? ` · ${aiStatus.last_decision.motivo}` : ''}.</p>}
        </section>

        <div className="dashboard-grid">
          <div className="dashboard-primary">
            <section className={`panel-card signal-card p-3 text-sm animate-fade-in ${active ? "signal-glow" : ""}`} style={active ? ({ "--glow-color": active.direction === "CALL" ? "var(--bull)" : "var(--bear)", animationDelay: "160ms" } as CSSProperties) : { animationDelay: "160ms" }}>
              <h2 className="mb-2 font-sans text-xs font-semibold uppercase text-muted-foreground">Resultado da análise</h2>
              {active ? <div key={active.id} className="animate-scale-in"><SignalCard s={active} now={now} /><button className="mt-2 rounded border border-border p-2 text-xs" onClick={()=>selectInstrument(active.source_key,active.asset,active.timeframe)}>Ver sinal no gráfico</button></div> : <div className="space-y-3"><p className="text-muted-foreground">{analysis?.message || "Inicie uma análise para avaliar os ativos com esta configuração."}</p>{risk.blockedReason && <p className="text-warn">⛔ {risk.blockedReason}{risk.pauseUntil ? ` — libera em ${fmtCountdown(risk.pauseUntil - now)}` : ""}</p>}{lastResolved && <p className="text-xs text-muted-foreground"><b className={lastResolved.result==="WIN"?"text-bull":lastResolved.result==="LOSS"||lastResolved.result==="CANCELADO"?"text-bear":"text-warn"}>{signalStage(lastResolved,now).label}</b> · Telegram: {telegramStage(lastResolved.telegram)}</p>}{lastResolved && <p className="text-sm text-muted-foreground">Último sinal: {assetLabel(lastResolved.asset)} {lastResolved.direction} → <span className={lastResolved.result === "WIN" ? "text-bull" : lastResolved.result === "LOSS" ? "text-bear" : "text-warn"}>{lastResolved.result}</span>{lastResolved.cancellation_reason && <span className="block text-xs">{lastResolved.cancellation_reason}</span>}</p>}</div>}
            </section>

            <section className="panel-card chart-card overflow-hidden animate-fade-in" style={{ animationDelay: "120ms" }}>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-3 py-2">
                <div><p className="text-xs uppercase text-muted-foreground">Gráfico</p><h2 className="font-mono font-bold">{assetLabel(selectedAsset)} <span className={(selectedPrice?.change_pct ?? 0) >= 0 ? "text-bull" : "text-bear"}>{selectedPrice?.price ? fmtPrice(selectedPrice.price) : ""}</span></h2></div>
                <div role="group" aria-label="Período do gráfico" className="flex rounded border border-border p-0.5">
                  {(chartSource==='deriv'?["1m","M2","M3","5m"]:["1m","5m"]).map((tf) => <button key={tf} aria-pressed={chartTimeframe===tf} onClick={() => setChartTimeframe(tf)} className={`rounded px-3 py-1 font-mono text-xs ${chartTimeframe === tf ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>{tfLabel(tf)}</button>)}
                </div>
              </div>
              <p className="px-3 pt-1.5 text-xs text-muted-foreground">{chartLabel} · {marketStatus(chartMeta,now,chartInterval/1000*2+30)}{chartMeta?.data_at ? ` · Vela ${fmtHMS(chartMeta.data_at*1000)} BRT${now<chartMeta.data_at*1000+chartInterval?' · em formação':''}` : ""}{chartMeta?.detail ? ` · ${chartMeta.detail}` : ""}</p>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-xs"><label className="flex items-center gap-2"><input type="checkbox" checked={followSignals} onChange={e=>setFollowSignals(e.target.checked)}/> Acompanhar automaticamente novos sinais · Visualização: {chartLabel}</label><label className="flex items-center gap-2"><input type="checkbox" checked={showAnnotations} onChange={e=>setShowAnnotations(e.target.checked)}/> Marcações do sinal</label></div>
              <MarketChart key={chartSource+selectedAsset+chartTimeframe} candles={chartCandles} loading={chartLoading} source={chartLabel} intervalMs={chartInterval} annotations={annotation} />
            </section>


          </div>

          <aside className="panel-card catalogue-card dashboard-catalogue p-3"><AssetCatalogue onSelect={selectInstrument} selected={chartSource+':'+selectedAsset} now={now} revision={source+settings.active_assets.join(',')}/></aside>
        </div>
      </div>
    </AppShell>
  );
}



function SignalCard({ s, now }: { s: Signal; now: number }) {
  const entry = new Date(s.entry_time).getTime();
  const call = s.direction === "CALL";
  const started = now >= entry;
  const expired = now >= new Date(s.expiry_time).getTime();
  const aiConsensus=s.ai_consensus,aiVotes=Array.isArray(aiConsensus?.votes)?aiConsensus.votes:[];
  const aiDirection=aiConsensus?.decision?.direcao;
  const aiCount=aiVotes.filter((vote:any)=>vote.status==='disponivel'&&!vote.dados_insuficientes&&vote.direcao===aiDirection).length;
  const stage=signalStage(s,now),tone=({bull:'text-bull border-bull/30 bg-bull/10',bear:'text-bear border-bear/30 bg-bear/10',warn:'text-warn border-warn/30 bg-warn/10',primary:'text-primary border-primary/30 bg-primary/10'} as Record<string,string>)[stage.tone];
  return (
    <div className="space-y-2 text-sm leading-snug">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-semibold text-primary">{expired?'Resultado em apuração':'Nova oportunidade identificada'}</p><span className="source-badge" data-source={s.source_key}>{s.source||s.source_key}</span></div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono">
       <b className="text-base">{assetLabel(s.asset)}</b>
       <b className={call ? "direction-badge text-bull" : "direction-badge text-bear"}>{call ? "CALL ⬆️" : "PUT ⬇️"}</b>
       <span>Entrada: <b>{fmtHM(s.entry_time)}</b> <span className="text-xs text-muted-foreground">BRT</span></span>
       <span>Expiração: {s.expiration_minutes} {s.expiration_minutes === 1 ? "Minuto" : "Minutos"}</span>
       <span role="timer" className="text-warn">{expired ? "Aguardando apuração pelo motor." : started ? <>Expira em <b>{fmtCountdown(new Date(s.expiry_time).getTime() - now)}</b></> : <>Entrar em: <b>{fmtCountdown(entry - now)}</b></>}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs"><span role="status" aria-label="Estado do sinal" className={'rounded border px-2 py-1 font-semibold '+tone}>{stage.label}</span><span data-testid="signal-telegram-status" className={s.telegram?.status==='falha'||s.telegram?.lead_status==='late'?'text-bear':'text-muted-foreground'}>Telegram: {telegramStage(s.telegram)}</span></div>
      <div className="text-xs"><ObservedRate value={s.observed_win_rate} inline/></div>
      {aiConsensus?.approved&&<details className="text-xs"><summary className="cursor-pointer text-primary">Consenso IA: {aiDirection} · {aiCount}/6 · evidência {Number(aiConsensus.decision?.confianca||0).toFixed(0)}%</summary><div className="mt-2 space-y-1 text-muted-foreground"><p>A confiança representa suporte analítico, não probabilidade de vitória.</p>{aiVotes.map((vote:any)=><p key={vote.agente}>{vote.agente}: {vote.status==='disponivel'?vote.direcao:vote.status.toUpperCase()}</p>)}</div></details>}
      {s.entry_timing&&<p className="text-xs text-muted-foreground" data-testid="signal-entry-timing">{s.entry_timing.label} · {tfLabel(s.timeframe)} · horário programado {fmtHMS(s.entry_time)} BRT</p>}
      {s.manual_intrabar&&<p className="text-xs text-primary">Gatilho observado durante a vela · entrada programada sujeita à revalidação.</p>}
      {s.telegram?.lead_status==='late'&&<p role="alert" className="text-warn">Telegram confirmado com atraso. Não executar esta entrada.</p>}
      <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">Explicação do motor</summary><div className="mt-2 space-y-1 break-words"><p>Estratégia: {strategyLabel(s.strategy)} · Timeframe: {tfLabel(s.timeframe)}</p><p>{s.engine_explanation?.text || (Array.isArray(s.reasons) && s.reasons.length ? s.reasons.join("; ") : "Motivos técnicos não disponíveis neste registro.")}</p></div></details>
    </div>
  );
}

function marketStatus(data:any,now:number,maxAge:number){
 if(data?.status==='closed')return 'Mercado fechado';
 if(!data || data.status==='unavailable')return 'Sem atualização';
 if(data.status==='stale'||!data.data_at||now/1000-data.data_at>maxAge)return 'Dados atrasados';
 return 'Atualizado';
}
