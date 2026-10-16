import {useEffect,useState} from 'react';
import {api} from '@/lib/api';
import {fmtPrice,fmtHMS} from '@/lib/apex';
export function AssetCatalogue({onSelect,selected,now,revision}:{onSelect:(source:string,asset:string,tf?:string)=>void;selected:string;now:number;revision:string}){
 const [market,setMarket]=useState(''),[search,setSearch]=useState(''),[query,setQuery]=useState(''),[page,setPage]=useState(1),[catalogue,setCatalogue]=useState<any>(null),[quotes,setQuotes]=useState<Record<string,any>>({}),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 useEffect(()=>{const timer=setTimeout(()=>{setQuery(search);setPage(1)},250);return()=>clearTimeout(timer)},[search]);
 useEffect(()=>{const controller=new AbortController();setCatalogue(null);setError('');api('/instruments?page='+page+'&page_size=6'+(market?'&market='+market:'')+'&q='+encodeURIComponent(query),undefined,undefined,controller.signal).then(d=>{if(!controller.signal.aborted)setCatalogue(d)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)});return()=>controller.abort()},[market,query,page,revision,retry]);
 useEffect(()=>{if(!catalogue)return;let stop=false,timer:any;const controller=new AbortController();
 async function load(){if(document.hidden){timer=setTimeout(load,30000);return}let index=0;
 async function worker(){while(!stop&&index<catalogue.items.length){const item=catalogue.items[index++];try{const q=await api('/quote?source='+item.source_key+'&asset='+encodeURIComponent(item.symbol),undefined,undefined,controller.signal);if(!stop)setQuotes(old=>({...old,[item.id]:q}))}catch(e:any){if(!stop)setQuotes(old=>({...old,[item.id]:{status:'unavailable',price:null,change_pct:null,detail:e.message}}))}}}
 await Promise.all([worker(),worker()]);if(!stop)timer=setTimeout(load,30000)}load();return()=>{stop=true;controller.abort();clearTimeout(timer)}},[catalogue]);
 return <section aria-label="Ativos disponíveis">
  <div className="mb-2 flex items-center justify-between gap-2"><h2 className="font-semibold">Ativos</h2><span className="font-mono text-[11px] text-muted-foreground">{fmtHMS(now)} BRT</span></div>
  <div className="catalogue-filters mb-2 grid grid-cols-2 gap-2 text-xs">
   <input aria-label="Pesquisar ativo" placeholder="Nome ou símbolo" value={search} onChange={e=>setSearch(e.target.value)} className="col-span-2 min-w-0 rounded border border-input bg-background p-2"/>
   <span className="flex items-center px-1 text-primary">Fontes automáticas</span>
   <select aria-label="Filtrar mercado" value={market} onChange={e=>{setMarket(e.target.value);setPage(1)}} className="min-w-0 rounded border border-input bg-background p-2"><option value="">Todos os mercados</option><option value="forex">Pares de moedas</option><option value="crypto">Cripto</option></select>
  </div>
  {error&&<div className="mb-2 text-xs"><p role="alert" className="text-bear">{error}</p><button onClick={()=>setRetry(r=>r+1)} className="mt-2 rounded border border-border p-2">Tentar novamente</button></div>}
  {!catalogue?(!error&&<p className="text-xs text-muted-foreground">Carregando catálogo...</p>):<>
   <div className="asset-grid">{catalogue.items.map((item:any)=>{
    const q=quotes[item.id],status=q?.status==='closed'?'Mercado fechado':q?.status==='stale'||(q?.data_at&&now/1000-q.data_at>120)?'Dados atrasados':q?.status==='live'?'Atualizado':q?.status==='unavailable'?'Indisponível':'Não verificado';
    return <button key={item.id} data-instrument-id={item.id} aria-pressed={item.id===selected} onClick={()=>onSelect(item.source_key,item.symbol,item.timeframes[0])} className={'asset-card min-w-0 rounded-md border p-2 text-left transition-colors '+(item.id===selected?'border-primary bg-primary/10':'border-border bg-background/60 hover:bg-accent')} title={[q?.detail||q?.change_basis,item.analysis_enabled?'Monitorado nesta fonte':'Disponível para visualizar'].filter(Boolean).join(' · ')}>
     <span className="flex items-center justify-between gap-1 font-mono text-xs font-bold"><span className="truncate">{item.symbol}</span>{item.analysis_enabled&&<span className="text-bull" title="Monitorado nesta fonte" aria-label="Monitorado nesta fonte">✓</span>}</span>
     <span className="mt-0.5 flex items-center gap-1.5 text-[10px]"><span className="source-badge" data-source={item.source_key}>{item.source}</span><span className="min-w-0 truncate text-muted-foreground" title={item.market==='forex'?'Pares de moedas':'Cripto'}>{item.market==='forex'?'Pares de moedas':'Cripto'}</span></span>
     <span className="mt-1 flex items-center justify-between gap-1 font-mono text-[11px]"><span>{fmtPrice(q?.price??null)}</span><span className={(q?.change_pct??0)>=0?'text-bull':'text-bear'}>{q?.change_pct==null?'—':(q.change_pct>=0?'+':'')+q.change_pct.toFixed(2)+'%'}</span></span>
     <span className={'mt-0.5 block truncate text-[10px] '+(status==='Atualizado'?'text-muted-foreground':status==='Indisponível'?'text-bear':'text-warn')} title={q?.data_at?fmtHMS(q.data_at*1000)+' BRT':''}>{status}</span>
     <span className="sr-only">{q?.data_at?fmtHMS(q.data_at*1000)+' BRT':'Sem horário de cotação'} · {item.analysis_enabled?'Monitorado nesta fonte':'Disponível para visualizar'}</span>
    </button>;
   })}</div>
   {catalogue.items.length===0&&<p className="text-xs">Nenhum instrumento encontrado.</p>}
   <div className="mt-2 flex items-center justify-between gap-1 text-[11px]"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)} className="rounded border border-border p-2 disabled:opacity-50">Anterior</button><span>{catalogue.total} instrumentos · Página {page}/{Math.max(1,Math.ceil(catalogue.total/6))}</span><button disabled={page*6>=catalogue.total} onClick={()=>setPage(p=>p+1)} className="rounded border border-border p-2 disabled:opacity-50">Próxima</button></div>
  </>}
  <p className="mt-2 text-[11px] text-muted-foreground">Clique para ver no gráfico. ✓ Monitorado. <a href="/configuracoes" className="text-primary">Definir monitorados</a></p>
 </section>;
}
