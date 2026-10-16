import {Fragment,useEffect,useMemo,useState} from 'react';
import {fmtDateTime} from '@/lib/apex';
import {strategyLabel} from '@/lib/presentation';
import {api} from '@/lib/api';

const PAGE_SIZE=10;
const field='min-w-0 rounded border border-input bg-background px-3 py-2 text-sm';
const button='rounded border border-border px-3 py-2 text-sm disabled:opacity-50';
const providers:Record<string,string>={deriv:'Deriv',binance:'Binance',coinbase:'Coinbase'};
const provider=(key:string)=>providers[key]||key;
const interval=(value:any)=>Array.isArray(value)?value.join('–')+'%':'Amostra insuficiente';
const stamp=(value:any)=>typeof value==='number'?fmtDateTime(value*1000):'Não informado';

export function ValidationMetrics({groups=[],scope,sessionId='',revision=0}:{groups?:any[];scope?:'bot'|'manual'|'experimental';sessionId?:string;revision?:number}){
 const [search,setSearch]=useState(''),[source,setSource]=useState(''),[strategy,setStrategy]=useState(''),[selector,setSelector]=useState(''),[page,setPage]=useState(1),[expanded,setExpanded]=useState<number|null>(null);
 const [server,setServer]=useState<any>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[query,setQuery]=useState('');
 useEffect(()=>{const timer=setTimeout(()=>setQuery(search.trim()),180);return()=>clearTimeout(timer)},[search]);
 useEffect(()=>{if(!scope)return;const controller=new AbortController();setLoading(true);setError('');api('/validation/groups?scope='+scope+'&page='+page+'&page_size='+PAGE_SIZE+'&source='+encodeURIComponent(source)+'&strategy='+encodeURIComponent(strategy)+'&selector='+encodeURIComponent(selector)+'&q='+encodeURIComponent(query)+'&session_id='+encodeURIComponent(sessionId),undefined,undefined,controller.signal).then(value=>{if(!controller.signal.aborted){setServer(value);setExpanded(null)}}).catch(e=>{if(!controller.signal.aborted)setError(e.message)}).finally(()=>{if(!controller.signal.aborted)setLoading(false)});return()=>controller.abort()},[scope,page,source,strategy,selector,query,sessionId,revision]);
 if(scope)groups=server?.items||[];
 useEffect(()=>{setExpanded(null)},[groups]);
 const sources:string[]=useMemo(()=>scope?server?.facets?.source_key||[]:[...new Set(groups.map(g=>g.source_key))].sort(),[groups,scope,server]);
 const strategies:string[]=useMemo(()=>scope?server?.facets?.strategy||[]:[...new Set(groups.map(g=>g.strategy))].sort(),[groups,scope,server]);
 const filtered=useMemo(()=>{const query=search.trim().toLocaleLowerCase('pt-BR');return groups.map((g,index)=>({g,index})).filter(({g})=>(!source||g.source_key===source)&&(!strategy||g.strategy===strategy)&&(!query||[g.strategy,strategyLabel(g.strategy),g.asset,g.source_key,provider(g.source_key),g.timeframe,g.regime,g.rules_version,g.pipeline_version,g.configuration_id,g.selector].join(' ').toLocaleLowerCase('pt-BR').includes(query)))},[groups,search,source,strategy]);
 const total=scope?server?.total||0:filtered.length;
 const pages=Math.max(1,Math.ceil(total/PAGE_SIZE)),current=scope?server?.page||page:Math.min(page,pages),rows=scope?groups.map((g,index)=>({g,index})):filtered.slice((current-1)*PAGE_SIZE,current*PAGE_SIZE);
 function filter(update:()=>void){update();setPage(1);setExpanded(null)}
 function clear(){filter(()=>{setSearch('');setSource('');setStrategy('');setSelector('')})}
 return <section aria-label="Combinações de validação" className="space-y-3">
  {error&&<p role="alert" className="text-sm text-bear">{error}{server?' · Exibindo a última página recebida; atualização dos filtros não confirmada.':''}</p>}
  <div className="flex flex-wrap gap-2">
   <input aria-label="Pesquisar combinação" placeholder="Pesquisar ativo, estratégia ou configuração" value={search} onChange={e=>filter(()=>setSearch(e.target.value))} className={field+' basis-full flex-1 sm:basis-auto'}/>
   <select aria-label="Fonte das métricas" value={source} onChange={e=>filter(()=>setSource(e.target.value))} className={field}><option value="">Todas as fontes</option>{sources.map(s=><option key={s} value={s}>{provider(s)}</option>)}</select>
   {scope==='experimental'&&<select aria-label="Seletor experimental" value={selector} onChange={e=>filter(()=>setSelector(e.target.value))} className={field}><option value="">Todos os seletores</option>{(server?.facets?.selector||[]).map((s:string)=><option key={s} value={s}>{s==='price_action_router'?'Price Action por regime':s}</option>)}</select>}
   <select aria-label="Estratégia das métricas" value={strategy} onChange={e=>filter(()=>setStrategy(e.target.value))} className={field}><option value="">Todas as estratégias</option>{strategies.map(s=><option key={s} value={s}>{strategyLabel(s)}</option>)}</select>
   {(search||source||strategy||selector)&&<button type="button" onClick={clear} className={button}>Limpar filtros</button>}
  </div>
  <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground"><span>{total} combinações{loading?' · carregando...':''}</span><span>Taxa de vitória = WIN ÷ (WIN + LOSS).</span></div>
  <div className="relative overflow-x-auto rounded border border-border bg-card">
   <table className="w-full text-left text-sm"><caption className="sr-only">Resultados por combinação de estratégia, fonte, ativo, período, duração, regras e configuração</caption>
    <thead className="border-b border-border text-xs text-muted-foreground"><tr>{['Estratégia / ativo','Fonte / período','Sinais','WIN / LOSS','Taxa observada',''].map((h,i)=><th key={i} className="px-3 py-2 font-normal">{h||<span className="sr-only">Detalhes</span>}</th>)}</tr></thead>
    <tbody>{rows.length===0?<tr><td colSpan={6} className="p-4 text-muted-foreground">{loading?'Carregando combinações...':search||source||strategy||selector?'Nenhuma combinação corresponde aos filtros.':'Sem dados suficientes para avaliar.'}</td></tr>:rows.map(({g,index})=>{const rate=g.price_verified||g;return <Fragment key={g.group_id||index}>
     <tr data-testid="metric-group" className="border-t border-border align-top">
      <td className="px-3 py-2"><span className="block font-medium">{g.apex_setup&&g.apex_setup!=='base'?g.apex_setup:g.strategy_label||strategyLabel(g.strategy)}</span><span className="block text-xs text-muted-foreground">{g.asset}</span><span title={g.configuration_id} className="block whitespace-nowrap text-xs text-muted-foreground">Config. {String(g.configuration_id||'—').slice(0,12)}</span></td>
      <td className="px-3 py-2"><span className="block">{provider(g.source_key)}</span><span className="block whitespace-nowrap text-xs text-muted-foreground">Análise {g.timeframe}</span><span className="block whitespace-nowrap text-xs text-muted-foreground">Duração {g.duration_seconds%60===0?g.duration_seconds/60+' min':g.duration_seconds+'s'}</span>{g.duration_basis==='manual_actual'&&<span className="block text-xs text-muted-foreground">Horários declarados</span>}{g.duration_basis==='planned_unconfirmed'&&<span className="block text-xs text-warn">Duração planejada, não conferida</span>}</td>
      <td className="px-3 py-2">{g.signals}</td><td className="whitespace-nowrap px-3 py-2"><span className="text-bull">{rate.wins}</span> / <span className="text-bear">{rate.losses}</span></td>
      <td className="px-3 py-2"><span className="whitespace-nowrap font-medium">{rate.observed_rate_pct==null?'—':rate.observed_rate_pct+'%'}</span><span className="block whitespace-nowrap text-xs text-muted-foreground">n={rate.evaluated} · {rate.evidence_status==='insufficient'?'Amostra insuficiente':'Descritivo'}</span>{g.legacy_unverified_outcomes>0&&<span className="block text-[10px] text-warn">{g.legacy_unverified_outcomes} legado(s) sem comprovação de preço</span>}</td>
      <td className="px-3 py-2"><button type="button" aria-expanded={expanded===index} aria-controls={'metric-detail-'+index} onClick={()=>setExpanded(expanded===index?null:index)} className="whitespace-nowrap text-xs text-primary underline underline-offset-4">{expanded===index?'Fechar detalhes':'Ver detalhes'}</button></td>
     </tr>
     {expanded===index&&<tr id={'metric-detail-'+index}><td colSpan={6} className="border-t border-border bg-secondary/30 p-3">
      <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
       <Detail title="Estratégia original" value={g.strategy}/><Detail title="Regras / pipeline" value={g.rules_version+' / '+g.pipeline_version}/><Detail title="Configuração" value={g.configuration_id}/><Detail title="Seletor / regime" value={g.selector+' / '+g.regime}/><Detail title="Direção / conferência" value={(g.direction||'Não informada')+' / '+(g.broker||'fonte externa')}/>
       <Detail title="Empates / pendentes" value={rate.ties+' / '+rate.pending}/><Detail title="Indisponíveis / cancelados" value={rate.unavailable+' / '+rate.cancelled}/><Detail title="Indisponíveis entre encerrados" value={rate.unavailable_pct==null?'—':rate.unavailable_pct+'%'}/>
       <Detail title="Intervalo Wilson 95%" value={interval(rate.wilson_95_pct)}/><Detail title="Intervalo por blocos de dias 95%" value={interval(rate.day_block_95_pct)}/><Detail title="Frequência de sinais / hora" value={rate.frequency_per_hour??'—'}/>
       {g.price_verified&&<Detail title="Resultados originalmente registrados WIN / LOSS / empate" value={g.wins+' / '+g.losses+' / '+g.ties}/>}<Detail title="Base da taxa" value={g.price_verified?'Preços e horários exatos da fonte original':scope==='manual'?'Declaração manual do usuário':'Resultado experimental indicativo'}/>
       <Detail title="Máximo de perdas seguidas" value={rate.max_loss_streak}/><Detail title="Amostra sem sobreposição" value={rate.nonoverlapping_sample??'—'}/><Detail title="Taxa sem sobreposição" value={rate.nonoverlapping_win_rate_pct==null?'—':rate.nonoverlapping_win_rate_pct+'%'}/>
       <Detail title="Período observado (Brasília)" value={stamp(g.observation_start??g.first_signal_at)+' até '+stamp(g.observation_end??g.last_expiry_at)}/>
       <Detail title="Expectativa estimada por unidade" value={rate.expected_roi_per_operation==null?'Sem payout informado':rate.expected_roi_per_operation.toFixed(4)+' (n='+rate.expectation_sample+')'}/><Detail title="Origem do payout" value={rate.payout_origins?.length?rate.payout_origins.join(', '):'Não informada'}/>
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">Intervalos são descritivos; sinais sobrepostos ou correlacionados podem aumentar a incerteza. Frequência usa o período registrado, sem presumir cobertura contínua. Nenhuma configuração é ativada por esta tabela.</p>
     </td></tr>}
    </Fragment>})}</tbody>
   </table>
  </div>
  <div className="flex flex-wrap items-center justify-between gap-2"><button type="button" disabled={loading||current<=1} onClick={()=>{setPage(current-1);setExpanded(null)}} className={button}>Página anterior</button><span className="text-xs">Página {current}/{pages}</span><button type="button" disabled={loading||current>=pages} onClick={()=>{setPage(current+1);setExpanded(null)}} className={button}>Próxima página</button></div>
 </section>
}

function Detail({title,value}:{title:string;value:any}){return <div className="min-w-0"><dt className="text-muted-foreground">{title}</dt><dd className="mt-1 break-words [overflow-wrap:anywhere]">{value??'Não informado'}</dd></div>}
