import {useEffect,useState} from 'react';
import {AppShell} from '@/components/AppShell';
import {api} from '@/lib/api';
import {fmtDateTime} from '@/lib/apex';
export default function News(){
 const [data,setData]=useState<any>(null),[error,setError]=useState('');
 useEffect(()=>{let stop=false,timer:any;async function load(){try{const d=await api('/news');if(!stop){setData(d);setError('')}}catch(e:any){if(!stop)setError(e.message)}finally{if(!stop)timer=setTimeout(load,60000)}}load();return()=>{stop=true;clearTimeout(timer)}},[]);
 return <AppShell><h1 className="mb-4 font-mono text-2xl font-bold text-primary">Notícias</h1>
 <p className="mb-4 text-xs text-muted-foreground">Comunicados oficiais do Federal Reserve · Títulos no idioma original · Horários de Brasília. Não interferem nos sinais técnicos.</p>
 <p role="status" className="mb-4 text-sm text-muted-foreground">{error?'Falha na atualização. '+error:!data?'Carregando notícias...':data.status==='unavailable'?'Fonte indisponível. Nova tentativa automática.':data.status==='stale'?'Fonte indisponível. Exibindo notícias da última consulta.':'Fonte conectada.'}{data?.updated_at?` Última consulta: ${fmtDateTime(data.updated_at*1000)} BRT.`:''}</p>
 <div className="space-y-3">{data?.items.map((item:any)=><article key={item.url} className="rounded-lg border border-border bg-card p-4"><p className="mb-2 text-xs text-muted-foreground">{item.source} · {fmtDateTime(item.published_at*1000)} BRT</p><a href={item.url} target="_blank" rel="noopener noreferrer" className="font-semibold hover:text-primary">{item.title} ↗</a></article>)}{data?.status==='live'&&!data.items.length&&<p>Sem comunicados disponíveis na fonte.</p>}</div></AppShell>
}
