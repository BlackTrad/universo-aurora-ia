import {useEffect,useState} from 'react';
import {api} from '@/lib/api';

type Row={id:string;setup_label:string;strategy:string;source_key:string;source_identity:string;
 timeframe:string;duration_seconds:number;duration_basis?:string;rules_version:string;pipeline_version:string;broker:string;
 signals:number;wins:number;losses:number;ties:number;pending:number;unavailable:number;cancelled:number;
 unverified:number;sample:number;win_rate_pct:number|null;wilson_95_pct:number[];context_count:number;asset_count:number};
type Result={items:Row[];total:number;at:number;minimum_sample:number;
 facets:{source_key:string[];asset:string[];timeframe:string[];duration_seconds:number[];pipeline_version:string[]};
 counts:{signals:number;wins:number;unverified:number};unknown_strategy:number;current_pipeline:string};
const sourceName=(key:string)=>({deriv:'Deriv',binance:'Binance',coinbase:'Coinbase'} as Record<string,string>)[key]||key;
const control='mt-1 w-full min-w-0 rounded border border-input bg-background px-2 py-1.5 text-xs';
const button='rounded border border-border px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-50';

export function WinningSetups({connected,revision}:{connected:boolean;revision:string}){
 const [period,setPeriod]=useState('all'),[scope,setScope]=useState('bot'),[source,setSource]=useState('');
 const [pipeline,setPipeline]=useState('current'),[asset,setAsset]=useState(''),[timeframe,setTimeframe]=useState(''),[duration,setDuration]=useState('');
 const [limit,setLimit]=useState(5),[expanded,setExpanded]=useState<string|null>(null);
 const [saved,setSaved]=useState<{key:string;result:Result}|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState('');
 const params=new URLSearchParams({scope,period,source,pipeline,asset,timeframe,limit:String(limit)});
 if(duration)params.set('duration_seconds',duration);
 const key=params.toString();
 const data=saved?.key===key?saved.result:null;
 const choices=saved&&new URLSearchParams(saved.key).get('scope')===scope?saved.result:null;
 useEffect(()=>{
  if(!connected){setLoading(false);return;}
  let stopped=false,timer:ReturnType<typeof setTimeout>;const controller=new AbortController();
  async function load(){
   if(document.hidden){timer=setTimeout(load,30000);return;}
   setLoading(true);
   try{const result=await api('/validation/setups?'+key,undefined,undefined,controller.signal);
    if(!stopped){setSaved({key,result});setError('');}}
   catch(e:any){if(!stopped)setError(e.message);}
   finally{if(!stopped){setLoading(false);timer=setTimeout(load,30000);}}
  }
  load();return()=>{stopped=true;controller.abort();clearTimeout(timer);};
 },[key,connected,revision]);
 const changeScope=(value:string)=>{setScope(value);setExpanded(null);setAsset('');setTimeframe('');setDuration('');setSource('');setLimit(5);};
 const counts=data?.counts;
 return <section aria-label="Setups com mais WINs" data-testid="winning-setups" className="rounded-lg border border-border bg-card p-3">
  <div className="flex flex-wrap items-start justify-between gap-2">
   <div><h2 className="font-semibold">Setups com mais WINs</h2><p className="mt-1 text-xs text-muted-foreground">Histórico completo · {scope==='bot'?'resultados indicativos do motor':'conferências manuais'} · {pipeline==='current'?'regras atuais':'versões separadas'}</p></div>
   <a href="/validacao" className={button}>Validação</a>
  </div>
  <div className="mt-3 grid gap-2 sm:grid-cols-3">
   <label className="text-xs text-muted-foreground">Período das entradas<select aria-label="Período dos setups" className={control} value={period} onChange={e=>{setPeriod(e.target.value);setExpanded(null)}}>
    <option value="all">Todo o histórico</option><option value="day">Hoje · Brasília</option><option value="month">Este mês · Brasília</option></select></label>
   <label className="text-xs text-muted-foreground">Resultados<select aria-label="Resultados dos setups" className={control} value={scope} onChange={e=>changeScope(e.target.value)}>
    <option value="bot">Motor · indicativos</option><option value="manual">Conferidos manualmente</option></select></label>
   <label className="text-xs text-muted-foreground">Fonte<select aria-label="Fonte dos setups" className={control} value={source} onChange={e=>{setSource(e.target.value);setExpanded(null)}}>
    <option value="">Todas as fontes</option>{(choices?.facets.source_key||[]).map(k=><option key={k} value={k}>{sourceName(k)}</option>)}{source&&!choices?.facets.source_key.includes(source)&&<option value={source}>{sourceName(source)}</option>}</select></label>
  </div>
  <details className="mt-2 text-xs"><summary className="cursor-pointer text-muted-foreground">Filtrar ativo, períodos e versão</summary>
   <div className="mt-2 grid grid-cols-2 gap-2 lg:grid-cols-4">
    <label>Ativo<select aria-label="Ativo dos setups" className={control} value={asset} onChange={e=>setAsset(e.target.value)}><option value="">Todos os ativos</option>{(choices?.facets.asset||[]).map(k=><option key={k}>{k}</option>)}{asset&&!choices?.facets.asset.includes(asset)&&<option>{asset}</option>}</select></label>
    <label>Timeframe<select aria-label="Timeframe dos setups" className={control} value={timeframe} onChange={e=>setTimeframe(e.target.value)}><option value="">Todos os timeframes</option>{(choices?.facets.timeframe||[]).map(k=><option key={k}>{k}</option>)}{timeframe&&!choices?.facets.timeframe.includes(timeframe)&&<option>{timeframe}</option>}</select></label>
    <label>Expiração<select aria-label="Expiração dos setups" className={control} value={duration} onChange={e=>setDuration(e.target.value)}><option value="">Todas as durações</option>{(choices?.facets.duration_seconds||[]).filter(n=>Number.isFinite(n)&&n>0).map(n=><option key={n} value={n}>{n%60===0?n/60+' min':n+'s'}</option>)}{duration&&!choices?.facets.duration_seconds.includes(Number(duration))&&<option value={duration}>{Number(duration)/60} min</option>}</select></label>
    <label>Versão do motor<select aria-label="Versão dos setups" className={control} value={pipeline} onChange={e=>setPipeline(e.target.value)}><option value="current">Atual</option><option value="">Todas · separadas</option>{(choices?.facets.pipeline_version||[]).filter(k=>k!==choices?.current_pipeline).map(k=><option key={k}>{k}</option>)}{pipeline&&pipeline!=='current'&&!choices?.facets.pipeline_version.includes(pipeline)&&<option>{pipeline}</option>}</select></label>
   </div>
  </details>
  {!connected&&<p role="status" className="mt-3 text-xs text-warn">Sem conexão. {data?'Últimos resultados preservados; atualização pendente.':'Aguardando o backend.'}</p>}
  {error&&<p role="alert" className="mt-3 text-xs text-bear">{error}. {data?'Últimos resultados preservados; atualização pendente.':'Ranking indisponível para estes filtros.'}</p>}
  {loading&&!data&&<p role="status" className="mt-3 text-xs text-muted-foreground">Carregando setups...</p>}
  {data&&!data.items.length&&<p className="mt-3 text-sm text-muted-foreground">Nenhum setup com WIN nesta seleção.{scope==='manual'?' Registre sua conferência na Validação.':''}</p>}
  {data&&!!data.items.length&&<ol className="mt-3 max-h-[34rem] space-y-2 overflow-y-auto pr-1" aria-label="Ranking por quantidade de WINs">
   {data.items.map((row,i)=><li key={row.id} data-testid="winning-setup-row" className="rounded border border-border px-3 py-2">
    <div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-words text-sm font-semibold"><span className="mr-2 font-mono text-muted-foreground">{i+1}.</span>{row.setup_label}</h3><b className="shrink-0 font-mono text-sm text-bull">{row.wins} WIN</b></div>
    <p className="mt-1 break-words text-xs text-muted-foreground">{sourceName(row.source_key)} · {row.timeframe||'TF não informado'} · expiração {row.duration_seconds?(row.duration_seconds%60===0?row.duration_seconds/60+' min':row.duration_seconds+'s'):'não informada'}{scope==='manual'?' · '+row.broker:''}</p>{row.duration_basis==='planned_unconfirmed'&&<p className="text-xs text-warn">Duração planejada, não conferida</p>}
    <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-xs"><span><b className="text-bear">{row.losses} LOSS</b> · {row.ties} empate(s) · n={row.sample}</span><span title={'WIN ÷ (WIN + LOSS). Wilson 95%: '+row.wilson_95_pct.join('–')+'%. Sinais correlacionados podem aumentar a incerteza.'}>{row.win_rate_pct==null?'Amostra pequena':row.win_rate_pct.toLocaleString('pt-BR',{maximumFractionDigits:2})+'% de vitórias'}</span>
     <button type="button" className="text-primary underline" aria-expanded={expanded===row.id} onClick={()=>setExpanded(expanded===row.id?null:row.id)}>Detalhes<span className="sr-only"> de {row.setup_label} em {sourceName(row.source_key)}</span></button></div>
    {expanded===row.id&&<div className="mt-2 space-y-1 border-t border-border pt-2 text-xs text-muted-foreground">
     <p>{row.signals} sinais · {row.asset_count} ativo(s) · {row.context_count} combinação(ões) de regime/direção/configuração.</p>
     <p>Pendentes: {row.pending} · Indisponíveis: {row.unavailable} · Cancelados: {row.cancelled} · Sem comprovação: {row.unverified}.</p>
     <p className="break-words">Fonte original: {row.source_identity} · Regras: {row.rules_version} · Seleção: {row.pipeline_version}.</p>
     <p>{row.win_rate_pct==null?'O percentual exige '+data.minimum_sample+' resultados WIN/LOSS. ':''}Este resumo agrupa ativos e contextos para contar vitórias; não estima a próxima entrada.</p>
    </div>}
   </li>)}
  </ol>}
  {data&&<div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{data.items.length} de {data.total} combinação(ões) com WIN · atualizado {new Date(data.at*1000).toLocaleTimeString('pt-BR',{timeZone:'America/Sao_Paulo',hour:'2-digit',minute:'2-digit'})} BRT</span>{data.total>5&&<button type="button" disabled={loading} className={button} onClick={()=>setLimit(limit===5?10:5)}>{limit===5?'Mostrar até 10':'Mostrar 5'}</button>}</div>}
  {!!counts?.unverified&&<p className="mt-2 text-xs text-warn">{counts.unverified} resultado(s) sem preços/horários comprovados ficaram fora dos WINs e da taxa.</p>}
  {!!data?.unknown_strategy&&<p className="mt-2 text-xs text-warn">{data.unknown_strategy} sinal(is) sem setup identificado não entram no ranking.</p>}
  <p className="mt-3 text-xs text-muted-foreground">Ordem por quantidade de WINs, com fontes, timeframe, expiração e versões separados. Mais sinais podem gerar mais vitórias. Inclui histórico arquivado; não altera a escolha do motor.</p>
 </section>
}
