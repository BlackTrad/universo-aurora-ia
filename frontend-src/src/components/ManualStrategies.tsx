import {useEffect,useState} from 'react';
import {api} from '@/lib/api';

export function ManualStrategies(){
 const [open,setOpen]=useState(false),[data,setData]=useState<any>(null),[error,setError]=useState(''),[query,setQuery]=useState(''),[page,setPage]=useState(1);
 useEffect(()=>{if(!open)return;setError('');const controller=new AbortController();api('/manual/strategies',undefined,undefined,controller.signal).then(d=>{if(!controller.signal.aborted)setData(d)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)});return()=>controller.abort()},[open]);
 const rows=(data?.strategies||[]).filter((s:any)=>(s.label+' '+s.pages.join(' ')).toLocaleLowerCase('pt-BR').includes(query.toLocaleLowerCase('pt-BR'))),pages=Math.max(1,Math.ceil(rows.length/8)),actual=Math.min(page,pages);
 return <details className="panel-card p-4" onToggle={e=>setOpen(e.currentTarget.open)}><summary className="cursor-pointer font-semibold">Estratégias do manual · Price Action para opções binárias</summary><div className="mt-3 space-y-3 text-sm">
  <p className="text-muted-foreground">O motor avalia os padrões e variações do manual, além do Fibonacci. Região, confirmação, espaço e prazo de entrada são verificados antes da emissão.</p>
  {error&&<p role="alert" className="text-bear">{error}</p>}
  {!data?<p>Carregando critérios…</p>:<><label className="block text-xs">Pesquisar padrão ou página<input aria-label="Pesquisar estratégias do manual" value={query} onChange={e=>{setQuery(e.target.value);setPage(1)}} className="mt-1 w-full rounded border border-input bg-background p-2"/></label>
   <div className="grid gap-2 sm:grid-cols-2">{rows.slice((actual-1)*8,actual*8).map((s:any)=><article key={s.key} className="rounded border border-border p-3"><b className="text-xs">{s.label}</b><p className="mt-1 text-[11px] text-muted-foreground">Manual · páginas {s.pages.join(', ')} · {({reversal:'reversão',retest:'reteste',flow:'fluxo',breakout:'rompimento'} as Record<string,string>)[s.family]}</p><span className="mt-1 inline-block text-[11px] text-bull">{s.timing==='intrabar_observed'?'Durante a vela · cotação observada':'Após confirmação fechada'}</span></article>)}</div>
   {!rows.length&&<p>Nenhum padrão corresponde à pesquisa.</p>}
   <div className="flex items-center justify-between gap-2 text-xs"><button disabled={actual<=1} onClick={()=>setPage(actual-1)} className="rounded border border-border p-2 disabled:opacity-40">Anterior</button><span>{rows.length} padrões · {actual}/{pages}</span><button disabled={actual>=pages} onClick={()=>setPage(actual+1)} className="rounded border border-border p-2 disabled:opacity-40">Próxima</button></div>
   <p className="text-xs text-muted-foreground">No manual, 50% e 90% descrevem a vela rompendo uma marcação. No motor, a medida usa a parte do corpo além da região; Fibonacci mede a correção de um impulso.</p>
   <p className="text-xs text-muted-foreground">As entradas durante a vela usam ticks contínuos da fonte original. A entrada programada precisa caber na vela original com antecedência mínima de 60 segundos. Região perdida ou cotação interrompida cancela a entrada. M5 oferece uma janela maior.</p>
   <p className="text-xs text-muted-foreground">As tolerâncias são definições documentadas do motor. Resultados reais ficam em Aprendizado e Validação.</p>
  </>}
 </div></details>;
}
