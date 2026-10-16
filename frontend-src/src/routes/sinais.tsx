import {useMemo,useState} from 'react';
import {AppShell} from '@/components/AppShell';
import {useData,mapSignal} from '@/lib/api';
import {assetLabel,fmtDateTime,fmtPrice} from '@/lib/apex';
export default function Signals(){
 const {snapshot,connected}=useData();
 const [asset,setAsset]=useState('');
 const [status,setStatus]=useState('');
 const signals=useMemo(()=>[...(snapshot?.signals||[])].map(mapSignal).sort((a:any,b:any)=>(b.entry_at||0)-(a.entry_at||0)),[snapshot]);
 const assets=[...new Set(signals.map((s:any)=>String(s.asset)))];
 const filtered=signals.filter((s:any)=>(!asset||s.asset===asset)&&(!status||s.result===status||(status==='Resultado indisponível'&&s.result==='INDISPONÍVEL')));
 return <AppShell><div className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><h1 className="text-2xl font-bold text-primary">Sinais</h1><p className="text-xs text-muted-foreground">Últimos sinais registrados pelo backend (até 20). Para pesquisar todo o período, utilize Histórico. Resultados não confirmados permanecem pendentes ou indisponíveis.</p></div><a href="/" className="rounded border border-border px-3 py-2 text-sm">Abrir gráfico e radar</a></div>
 <div className="flex flex-wrap gap-2"><select aria-label="Ativo" className="rounded border border-input bg-card p-2" value={asset} onChange={e=>setAsset(e.target.value)}><option value="">Todos os ativos</option>{assets.map(a=><option key={a} value={a}>{assetLabel(a)}</option>)}</select><select aria-label="Resultado" className="rounded border border-input bg-card p-2" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Todos os resultados</option>{['PENDING','APURANDO','WIN','LOSS','EMPATE','Resultado indisponível','CANCELADO'].map(a=><option key={a} value={a}>{a==='Resultado indisponível'?'INDISPONÍVEL':a==='PENDING'?'PENDENTE':a}</option>)}</select></div>
 {!connected&&<p role="status" className="text-warn">Conexão com o backend indisponível. Não há sinal ao vivo confirmado.</p>}
 <div className="grid gap-3 md:grid-cols-2">{filtered.map((s:any)=><article className="panel-card p-4" key={s.id}><div className="flex items-center justify-between"><h2 className="font-semibold">{assetLabel(s.asset)}</h2><span className={s.direction==='CALL'?'text-bull':'text-bear'}>{s.direction}</span></div><p className="mt-2 text-xs text-muted-foreground">{fmtDateTime((s.entry_at||0)*1000)} BRT · {s.timeframe} · Expiração {s.duration_seconds/60} min</p><p className="mt-1 text-sm">Entrada: {s.entry_price!=null?fmtPrice(s.entry_price):'Indisponível'} · Payout: {s.payout_pct!=null?s.payout_pct+'%':'Não confirmado'}</p><p className="mt-2 text-sm">Resultado: <strong>{s.result}</strong></p><a className="mt-2 inline-block text-xs text-primary underline" href={'/?asset='+encodeURIComponent(s.asset)+'&source='+encodeURIComponent(s.source_key||'deriv')+'&timeframe='+encodeURIComponent(s.timeframe||'M5')}>Ver gráfico</a></article>)}{filtered.length===0&&<p className="text-sm text-muted-foreground">Nenhum sinal encontrado com estes filtros.</p>}</div><a className="text-sm text-primary underline" href="/historico">Abrir histórico detalhado →</a></div></AppShell>
}
