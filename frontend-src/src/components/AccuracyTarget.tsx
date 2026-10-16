import {useEffect,useState} from 'react';
import {api} from '@/lib/api';
import {fmtDateTime} from '@/lib/apex';
import {strategyLabel} from '@/lib/presentation';

const status:Record<string,string>={no_data:'Sem amostra',collecting:'Coleta em andamento',insufficient:'Amostra insuficiente',criteria_not_met:'Critérios não atendidos',phase_criteria_met:'Critérios desta fase atendidos'};
const checks:Record<string,string>={phase_complete:'Período encerrado',resolved_sample:'100 WIN/LOSS',resolved_days:'20 dias com resultados',availability:'95% dos resultados disponíveis',observed_rate:'Taxa observada ≥90%',wilson_lower:'Limite inferior Wilson ≥90%',day_block_lower:'Limite inferior por dias ≥90%',nonoverlap_sample:'100 resultados sem sobreposição',nonoverlap_lower:'Limite inferior sem sobreposição ≥90%',weekly_stability:'Estabilidade semanal',known_payout_expectation:'Expectativa positiva quando há payout'};
const selectorLabel:Record<string,string>={reference_fixed:'Referência fixa',regime_router:'Regime',history_frozen:'Histórico congelado',regime_structure_router:'Estrutura',htf_regime_router:'Contexto superior'};
const pct=(value:any)=>typeof value==='number'?value.toLocaleString('pt-BR',{maximumFractionDigits:2})+'%':'—';

export function AccuracyTarget({sessionId,revision}:{sessionId:string;revision:number}){
 const [phase,setPhase]=useState('validation'),[selector,setSelector]=useState(''),[page,setPage]=useState(1);
 const [data,setData]=useState<any>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 useEffect(()=>{const controller=new AbortController();setData(null);setLoading(true);setError('');
  api('/validation/target?session_id='+encodeURIComponent(sessionId)+'&phase='+phase+'&selector='+encodeURIComponent(selector)+'&page='+page,undefined,undefined,controller.signal)
  .then(value=>{if(!controller.signal.aborted)setData(value)})
  .catch(e=>{if(!controller.signal.aborted)setError(e.message)})
  .finally(()=>{if(!controller.signal.aborted)setLoading(false)});
  return()=>controller.abort();
 },[sessionId,revision,phase,selector,page]);
 const field='min-w-0 rounded border border-input bg-background p-2 text-sm';
 return <section aria-label="Avaliação da meta de acerto" data-testid="accuracy-target" className="space-y-3 rounded border border-border bg-card p-3">
  <h2 className="font-semibold">Meta de 90% em avaliação</h2>
  <p className="text-xs text-muted-foreground">Avalia as escolhas experimentais por combinação. Validação e teste final são separados. A meta não é garantia para a próxima entrada.</p>
  <div className="flex flex-wrap gap-2"><label className="text-xs">Período<select aria-label="Fase da meta" value={phase} onChange={e=>{setPhase(e.target.value);setPage(1)}} className={'ml-2 '+field}><option value="validation">Validação</option><option value="development">Desenvolvimento (diagnóstico)</option><option value="test">Teste final (reservado)</option></select></label>
   <label className="text-xs">Seletor<select aria-label="Seletor da meta" value={selector} onChange={e=>{setSelector(e.target.value);setPage(1)}} className={'ml-2 '+field}><option value="">Todos</option>{Object.entries(selectorLabel).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label></div>
  {error&&<p role="alert" className="text-sm text-bear">{error} · A avaliação não foi carregada.</p>}
  {loading?<p role="status" className="text-xs">Carregando avaliação...</p>:data?.status==='sealed'?<p className="text-sm">Teste final reservado. Abra o relatório por exportação após o término; não ajuste parâmetros com seus resultados.</p>:data?.status==='legacy_protocol'?<p className="text-sm">Esta sessão foi iniciada com o protocolo anterior. A meta não foi definida antes da coleta; inicie uma sessão da versão atual para avaliá-la.</p>:data&&<>
   <p className="text-xs text-muted-foreground">{data.start_at?fmtDateTime(data.start_at*1000)+' até '+fmtDateTime(data.end_at*1000):'Sem sessão'} · {data.complete?'Período encerrado':'Período em andamento'} · {data.total} combinações</p>
   <div aria-label="Avaliação por combinação" className="space-y-3">
    {!data.items.length?<p className="text-xs">Nenhum resultado desta fase para avaliar.</p>:data.items.map((g:any,i:number)=><div key={i} data-testid="target-row" className="grid grid-cols-2 gap-3 rounded border border-border p-3 text-xs lg:grid-cols-5">
     <div className="min-w-0"><p className="mb-1 text-muted-foreground">Setup / ativo</p>{g.apex_setup==='base'?strategyLabel(g.strategy):g.apex_setup}<span className="block text-muted-foreground">{g.asset} · {g.source_key} · {g.timeframe} / {g.duration_seconds/60} min</span></div>
     <div className="min-w-0"><p className="mb-1 text-muted-foreground">Seletor</p>{selectorLabel[g.selector]||g.selector}<span className="block text-muted-foreground">{g.regime} · {g.direction}</span></div>
     <div><p className="mb-1 text-muted-foreground">WIN / LOSS</p>{g.wins} / {g.losses}<span className="block text-muted-foreground">{g.unavailable} indisponíveis · {g.pending} pendentes</span></div>
     <div><p className="mb-1 text-muted-foreground">Taxa histórica</p>{pct(g.observed_rate_pct)}<span className="block text-muted-foreground">n={g.evaluated}</span></div>
     <div className="col-span-2 lg:col-span-1"><p className="mb-1 text-muted-foreground">Situação</p>{status[g.status]||g.status}</div>
     <details className="col-span-2 lg:col-span-5"><summary className="cursor-pointer text-primary">Conferir critérios</summary><p className="my-2 text-muted-foreground">Wilson 95%: {g.wilson_95_pct?.map(pct).join('–')||'—'} · Disponibilidade: {pct(g.availability_pct)}</p><ul className="grid gap-1 sm:grid-cols-2">{Object.entries(g.checks).map(([key,passed])=><li key={key}>{key==='known_payout_expectation'&&!g.expectation_sample?'— Sem payout: expectativa não calculada':(passed?'✓ ':'○ ')+(checks[key]||key)}</li>)}</ul><p className="mt-2 break-all text-muted-foreground">Config. {g.configuration_id}</p></details>
    </div>)}
   </div>
   <div className="flex items-center justify-between gap-2"><button disabled={data.page<=1} onClick={()=>setPage(data.page-1)} className={field+' disabled:opacity-50'}>Anterior</button><span className="text-xs">Página {data.page}/{Math.max(1,Math.ceil(data.total/data.page_size))}</span><button disabled={data.page*data.page_size>=data.total} onClick={()=>setPage(data.page+1)} className={field+' disabled:opacity-50'}>Próxima</button></div>
  </>}
  <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Critérios e limites da meta</summary><p className="mt-2">Cada combinação precisa de pelo menos 100 WIN/LOSS e 20 dias, disponibilidade de 95%, estabilidade semanal e limites inferiores de incerteza de 90%, inclusive sem sobreposição. Empates e resultados ausentes não contam como WIN. Os intervalos são descritivos e podem subestimar dependência e ajustes em várias configurações. A seleção operacional não muda por esta tabela.</p></details>
 </section>;
}
