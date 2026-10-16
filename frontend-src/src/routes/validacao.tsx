import {useCallback,useEffect,useState,useRef} from 'react';
import {AppShell} from '@/components/AppShell';
import {ValidationMetrics} from '@/components/ValidationMetrics';
import {AccuracyTarget} from '@/components/AccuracyTarget';
import {PriceActionStudy} from '@/components/PriceActionStudy';
import {api} from '@/lib/api';

const views=[['bot','Sinais do bot'],['manual','Conferência manual'],['experiments','Experimentos']] as const;
export default function Validation(){
 const initialSignal=new URLSearchParams(location.search).get('signal')||'';
 const submitting=useRef(false),requestSequence=useRef(0),mounted=useRef(true);
 const [view,setView]=useState(initialSignal?'manual':'bot'),[sessionId,setSessionId]=useState(''),[showManualMetrics,setShowManualMetrics]=useState(false);
 const [metricsBusy,setMetricsBusy]=useState(true),[report,setReport]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[revision,setRevision]=useState(0),[sessionReport,setSessionReport]=useState<any>(null),[observationBusy,setObservationBusy]=useState(false);
 const observationPending=useRef(false);
 const [sid,setSid]=useState(initialSignal),[broker,setBroker]=useState(''),[result,setResult]=useState('WIN'),[executed,setExecuted]=useState(true),[note,setNote]=useState('');
 const [entryTime,setEntryTime]=useState(''),[expiryTime,setExpiryTime]=useState(''),[entryPrice,setEntryPrice]=useState(''),[expiryPrice,setExpiryPrice]=useState(''),[payout,setPayout]=useState('');
 const load=useCallback(async()=>{const sequence=++requestSequence.current;setMetricsBusy(true);try{const value=await api('/validation/summary');if(mounted.current&&sequence===requestSequence.current){setReport(value);setRevision(r=>r+1);setError('')}}catch(e:any){if(mounted.current&&sequence===requestSequence.current)setError(e.message)}finally{if(mounted.current&&sequence===requestSequence.current)setMetricsBusy(false)}},[]);
 useEffect(()=>{mounted.current=true;load();return()=>{mounted.current=false;requestSequence.current++}},[load]);
 async function confirm(e:React.FormEvent){e.preventDefault();if(submitting.current)return;submitting.current=true;setBusy(true);setMessage('');try{
  const number=(v:string)=>{if(v.trim()==='')return null;const n=Number(v.replace(',','.'));if(!Number.isFinite(n))throw Error('Valor numérico inválido.');return n};const stamp=(v:string)=>v===''?null:new Date(v+'-03:00').getTime()/1000;
  await api('/signals/'+encodeURIComponent(sid)+'/manual-confirmation',{executed,broker,result:executed?result:null,note,
   entry_at:executed?stamp(entryTime):null,expiry_at:executed?stamp(expiryTime):null,entry_price:executed?number(entryPrice):null,expiry_price:executed?number(expiryPrice):null,payout_pct:executed?number(payout):null});
  setMessage('Conferência registrada separadamente. O resultado indicativo foi preservado.');await load();
 }catch(e:any){setError(e.message)}finally{submitting.current=false;setBusy(false)}}
 const input='mt-1 w-full rounded border border-input bg-background p-2';
 const reportMessage=metricsBusy?'Carregando validação...':'Métricas indisponíveis. Clique em Atualizar métricas para tentar novamente.';
 const sessions=report?.observation?.sessions||[],session=sessions.find((s:any)=>s.session.id===sessionId)||sessions[0];
 const selectedSession=session?.session.id||'';
 useEffect(()=>{if(view!=='experiments'||!selectedSession){setSessionReport(null);return}const controller=new AbortController();setSessionReport(null);api('/validation/session?session_id='+encodeURIComponent(selectedSession),undefined,undefined,controller.signal).then(d=>{if(!controller.signal.aborted)setSessionReport(d)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)});return()=>controller.abort()},[view,selectedSession,revision]);
 async function toggleObservation(){if(observationPending.current)return;observationPending.current=true;setObservationBusy(true);setError('');try{await api('/validation/observation',{enabled:!report?.observation?.collection_enabled},'PUT');await load()}catch(e:any){setError(e.message)}finally{observationPending.current=false;setObservationBusy(false)}}
 return <AppShell>
  <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-lg font-bold">Validação dos sinais</h1><p className="mt-1 text-sm text-muted-foreground">Consulte resultados ou registre sua conferência na corretora.</p></div><button disabled={metricsBusy} onClick={load} className="rounded border border-border px-3 py-2 text-sm disabled:opacity-50">{metricsBusy?'Atualizando métricas...':'Atualizar métricas'}</button></div>
  {error&&<p role="alert" className="mb-3 text-bear">{error}</p>}
  <div className="mb-4 grid gap-2 sm:grid-cols-3">{[['Gerados',report?.counts.generated],['Entrega confirmada no Telegram',report?.counts.telegram_confirmed_delivery],['Execuções declaradas',report?.counts.manual_executed_declared]].map(([label,value])=><p key={String(label)} className="rounded border border-border bg-card p-3 text-sm">{label}: <b>{value??'—'}</b></p>)}</div>
  <div role="tablist" aria-label="Visões da validação" className="mb-4 flex flex-wrap gap-2">{views.map(([key,label],index)=><button key={key} type="button" id={'validation-tab-'+key} role="tab" tabIndex={view===key?0:-1} aria-selected={view===key} aria-controls={'validation-panel-'+key} onClick={()=>setView(key)} onKeyDown={e=>{const next=e.key==='ArrowRight'?(index+1)%views.length:e.key==='ArrowLeft'?(index+views.length-1)%views.length:e.key==='Home'?0:e.key==='End'?views.length-1:null;if(next===null)return;e.preventDefault();setView(views[next][0]);document.getElementById('validation-tab-'+views[next][0])?.focus()}} className={'rounded px-3 py-2 text-sm '+(view===key?'bg-primary text-primary-foreground':'border border-border text-muted-foreground')}>{label}</button>)}</div>
  <section role="tabpanel" id={'validation-panel-'+view} aria-labelledby={'validation-tab-'+view} className="space-y-4">
   {view==='bot'&&<><p className="text-xs text-muted-foreground">Resultados indicativos da fonte original, sem confirmação da corretora. Inclui {report?.history_visibility?.archived??0} operação(ões) arquivada(s) pela limpeza.</p>{report?<ValidationMetrics scope="bot" revision={revision}/>:<p>{reportMessage}</p>}</>}
   {view==='manual'&&<>
    <p className="text-xs text-muted-foreground">Registre o que conferiu na corretora. O resultado original permanece preservado.</p>
    <form onSubmit={confirm} className="grid gap-3 rounded border border-border bg-card p-4 sm:grid-cols-2"><h2 className="font-semibold sm:col-span-2">Registrar conferência manual</h2>
     <label className="text-sm">ID do sinal<input required value={sid} onChange={e=>setSid(e.target.value)} className={input}/></label><label className="text-sm">Corretora<input required maxLength={80} value={broker} onChange={e=>setBroker(e.target.value)} className={input}/></label>
     <label className="text-sm"><input type="checkbox" checked={executed} onChange={e=>setExecuted(e.target.checked)}/> Executei esta operação</label><label className="text-sm">Resultado<select aria-label="Resultado da conferência" disabled={!executed} value={result} onChange={e=>setResult(e.target.value)} className="ml-2 rounded border border-input bg-background p-2">{['WIN','LOSS','EMPATE'].map(r=><option key={r}>{r}</option>)}</select></label>
     {executed&&<details className="rounded border border-border p-3 sm:col-span-2"><summary className="cursor-pointer text-sm">Horários, preços e payout (opcional)</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">
      <label className="text-sm">Entrada real (Brasília UTC−3)<input type="datetime-local" step="1" value={entryTime} onChange={e=>setEntryTime(e.target.value)} className={input}/></label><label className="text-sm">Expiração real (Brasília UTC−3)<input type="datetime-local" step="1" value={expiryTime} onChange={e=>setExpiryTime(e.target.value)} className={input}/></label>
      <label className="text-sm">Preço de entrada (opcional)<input inputMode="decimal" value={entryPrice} onChange={e=>setEntryPrice(e.target.value)} className={input}/></label><label className="text-sm">Preço de expiração (opcional)<input inputMode="decimal" value={expiryPrice} onChange={e=>setExpiryPrice(e.target.value)} className={input}/></label><label className="text-sm">Payout % (opcional)<input inputMode="decimal" value={payout} onChange={e=>setPayout(e.target.value)} className={input}/></label>
     </div><p className="mt-2 text-xs text-muted-foreground">Payout declarado produz apenas uma expectativa estimada.</p></details>}
     <label className="text-sm sm:col-span-2">Observações sobre divergências<input maxLength={500} value={note} onChange={e=>setNote(e.target.value)} className={input}/></label><button disabled={busy} className="rounded bg-primary px-3 py-2 text-primary-foreground sm:col-span-2">{busy?'Registrando...':'Registrar conferência'}</button>{message&&<p role="status" className="text-sm sm:col-span-2">{message}</p>}
    </form>
    <button type="button" aria-expanded={showManualMetrics} onClick={()=>setShowManualMetrics(!showManualMetrics)} className="text-sm text-primary underline">{showManualMetrics?'Ocultar métricas das conferências':'Ver métricas das conferências'}</button>
    {showManualMetrics&&(report?<ValidationMetrics scope="manual" revision={revision}/>:<p>{reportMessage}</p>)}
   </>}
   {view==='experiments'&&<>
    <p className="text-sm">{report?.observation.collection_enabled?'Coleta experimental habilitada; ocorre durante Iniciar.':'Coleta experimental pausada.'} Sem envio ao Telegram.</p>
    <button type="button" disabled={!report||observationBusy||report.monitoring} onClick={toggleObservation} className="rounded border border-border px-3 py-2 text-sm disabled:opacity-50">{observationBusy?'Salvando...':report?.observation.collection_enabled?'Pausar coleta experimental':'Habilitar coleta experimental'}</button>
    {report?.monitoring&&<p className="text-xs text-muted-foreground">Pare a análise para alterar a coleta.</p>}
    <PriceActionStudy study={sessionReport?.price_action_study} loading={!!session&&!sessionReport}/>
    {!report?<p>{reportMessage}</p>:session?<>
     <label className="block text-sm">Configuração experimental<select aria-label="Configuração experimental" value={session.session.id} onChange={e=>setSessionId(e.target.value)} className="mt-1 block w-full min-w-0 rounded border border-input bg-card p-2 sm:max-w-md">{sessions.map((s:any,index:number)=><option key={s.session.id} value={s.session.id} title={s.session.id}>Configuração {index+1} · {String(s.session.id).slice(0,12)}</option>)}</select></label>
     <p className="text-xs text-muted-foreground">Decisões: {sessionReport?.decisions??'—'} · Rejeitadas: {sessionReport?.rejected??'—'} · Teste final reservado.</p>
     <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm">Avaliar a meta de 90%</summary><AccuracyTarget key={session.session.id} sessionId={session.session.id} revision={revision}/></details>
     <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm">Comparar seletores</summary><div className="mt-2 space-y-2">{[...(sessionReport?.comparisons||[]),...(sessionReport?.exploratory_comparisons||[])].map((c:any)=><p key={c.right} className="break-words text-xs">{c.left} × {c.right==='price_action_router'?'Price Action por regime':c.right}: {c.common_signals} comuns · {c.exclusive_left.signals}/{c.exclusive_right.signals} exclusivos. {c.opposing_direction_decisions??0} decisões com direções opostas · {c.cancelled_left??0}/{c.cancelled_right??0} canceladas. {c.comparison_valid===false?'Comparação inconsistente; estimativa pareada indisponível. ':''}Avalie os conjuntos exclusivos separadamente.</p>)}</div></details>
     {!!sessionReport?.outcomes_without_quote_proof&&<p className="text-xs text-warn">{sessionReport.outcomes_without_quote_proof} resultado(s) experimental(is) sem prova válida dos preços: indisponíveis nas métricas, com registro original preservado.</p>}
     <ValidationMetrics key={session.session.id} scope="experimental" sessionId={session.session.id} revision={revision}/>
    </>:<p className="text-xs text-muted-foreground">Nenhuma configuração experimental com dados.</p>}
    <details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm">Sobre a observação</summary><p className="mt-2 text-xs text-muted-foreground">Habilite a coleta com a análise parada, depois clique em Iniciar no painel. Compara a referência com seletores por regime, estrutura e histórico congelado, sobre os mesmos candles. Parar encerra novas observações e mantém apuração das anteriores. Nenhuma alternativa é ativada automaticamente.</p><p className="mt-2 text-xs text-muted-foreground">Price Action em M1/M5: pullback estrutural, rompimento e reteste, varredura com mudança de estrutura, reversão na faixa e terceiro toque. São hipóteses separadas; não somam confirmação ao seletor principal.</p><p className="mt-2 text-xs text-muted-foreground">No terceiro toque, a própria vela fecha na marcação e a referência é a abertura seguinte. Observações sem antecedência ficam canceladas, com o horário original preservado. Não há aviso antes do fechamento nem mudança automática de vela.</p><p className="mt-2 text-xs text-muted-foreground">Contexto superior usa somente candles completos agregados da mesma fonte, sem novas consultas. Notícias continuam informativas: não existe filtro operacional de eventos. Score permanece interno, separado da taxa de vitória.</p></details>
   </>}
  </section>
 </AppShell>
}
