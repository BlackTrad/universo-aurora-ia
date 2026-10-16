import {useEffect,useMemo,useState,useRef} from 'react';
import {AppShell} from '@/components/AppShell';
import {HistoryClear} from '@/components/HistoryClear';
import {ObservedRate} from '@/components/ObservedRate';
import {api,mapSignal,useData} from '@/lib/api';
import {assetLabel,tfLabel,fmtDateTime,fmtPrice,type Signal} from '@/lib/apex';
export default function History() {
  const marker=useRef<number|undefined>(undefined),filters=useRef('');
  const [facets,setFacets]=useState<any>({assets:[],strategies:[]});
  const {snapshot,isAdmin,refresh}=useData();
  const [revision,setRevision]=useState(0);
  async function historyCleared(){marker.current=undefined;setPage(1);setRevision(v=>v+1);const d=await api('/history/days');setDays(d.days);setFacets(d);await refresh()}
  const [day,setDay]=useState<string|null>(null),[days,setDays]=useState<any[]>([]),[page,setPage]=useState(1),[total,setTotal]=useState(0);
  const [signals, setSignals] = useState<Signal[] | null>(null);
  const [asset, setAsset] = useState("");
  const [strategy, setStrategy] = useState("");

  const [error,setError]=useState('');
  useEffect(()=>{let stopped=false;api('/history/days').then(d=>{if(stopped)return;setDays(d.days);setFacets(d);const date=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date((snapshot?.server_time||Date.now()/1000)*1000));setDay(d.days.some((x:any)=>x.date===date)?date:(d.days[0]?.date||date))}).catch(e=>{if(!stopped)setError(e.message)});return()=>{stopped=true}},[]);
  useEffect(()=>{if(day===null)return;const key=[day,asset,strategy,revision].join('|');if(key!==filters.current){filters.current=key;marker.current=undefined}let stopped=false,timer:any;const controller=new AbortController();async function load(){try{const d=await api('/history?page='+page+'&page_size=50'+(day?'&start_date='+day+'&end_date='+day:'')+(asset?'&asset='+encodeURIComponent(asset):'')+(strategy?'&strategy='+encodeURIComponent(strategy):'')+(page>1&&marker.current!==undefined?'&snapshot_id='+marker.current:''),undefined,undefined,controller.signal);if(!stopped){marker.current=d.snapshot_id;setSignals(d.items.map(mapSignal));setTotal(d.total);setError('')}}catch(e:any){if(!stopped)setError(e.message)}finally{if(!stopped)timer=setTimeout(load,15000)}}setSignals(null);load();return()=>{stopped=true;controller.abort();clearTimeout(timer)}},[day,page,asset,strategy,revision]);


  const list = useMemo(
    () => (signals ?? []).filter((s) => (!asset || s.asset === asset) && (!strategy || s.strategy === strategy)),
    [signals, asset, strategy],
  );
  const wins = list.filter((s) => s.result === "WIN").length;
  const losses = list.filter((s) => s.result === "LOSS").length;
  const rate = wins + losses ? (wins / (wins + losses)) * 100 : 0;

  const [groups,setGroups]=useState<any[]>([]);
  useEffect(()=>{if(!isAdmin)return;let stopped=false;api('/validation/groups?scope=bot&page_size=5').then(d=>{if(!stopped)setGroups(d.items)}).catch(e=>{if(!stopped)setError(e.message)});return()=>{stopped=true}},[signals,isAdmin]);
  const byStrategy=groups.filter(g=>g.win_rate_pct!=null).slice(0,5).map(g=>[`${g.strategy} · ${g.asset} · ${g.timeframe} · ${g.duration_seconds}s · ${g.source_key} · regras ${g.rules_version} / ${g.pipeline_version}`,{w:g.wins,l:g.losses}] as const);

  const assets:string[] = facets.assets;
  const strategies:string[] = facets.strategies;

  return (
    <AppShell>
      {error && <p role="alert" className="mb-4 text-bear">{error}</p>}
      <h1 className="mb-3 text-lg font-bold">Histórico de operações</h1>
      {isAdmin&&day!==null&&<HistoryClear day={day||days[0]?.date||new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date((snapshot?.server_time||Date.now()/1000)*1000))} onCleared={historyCleared}/>}
      <p className="mb-4 text-xs text-muted-foreground">{day?'Operações de '+day+' · Brasília (UTC−3)':'Todos os dias · Brasília (UTC−3)'} · {total} registros · Resumo da página filtrada · Resultados indicativos.</p>
      <div className="mb-6 grid gap-3 font-mono sm:grid-cols-4">
        <Stat k="Taxa desta página" v={wins+losses?`${rate.toFixed(1)}%`:"—"} />
        <Stat k="Wins" v={String(wins)} cls="text-bull" />
        <Stat k="Losses" v={String(losses)} cls="text-bear" />
        <Stat k="Nesta página" v={String(list.length)} />
      </div>

      {byStrategy.length > 0 && (
        <details className="mb-6 rounded-lg border border-border bg-card p-4">
          <summary className="cursor-pointer text-sm font-semibold">Grupos com taxa observada</summary>
          <h2 className="sr-only">Grupos com taxa observada</h2>
          <p className="mb-2 text-xs text-muted-foreground">Algumas combinações do histórico completo, sem filtro por dia ou ranking pela maior taxa. Consulte Validação para ver todas as combinações e sua incerteza.</p>
          {byStrategy.map(([name, e]) => (
            <div key={name} className="flex flex-wrap justify-between gap-2 py-1 font-mono text-sm">
              <span className="min-w-0 break-words">{name}</span>
              <span className="shrink-0 text-muted-foreground">{e.w}W/{e.l}L · {((e.w / (e.w + e.l)) * 100).toFixed(0)}%</span>
            </div>
          ))}
        </details>
      )}

      <div className="mb-3 flex flex-wrap gap-2 text-sm">{isAdmin&&day&&<a href={"/api/history/export-day?day="+day} className="rounded border border-border px-2 py-1.5">Salvar histórico deste dia</a>}<label>Dia (BRT) <select aria-label="Dia do histórico" value={day??''} onChange={e=>{setDay(e.target.value);setPage(1)}} className="rounded border border-input bg-card px-2 py-1.5"><option value="">Todos os dias</option>{days.map(d=><option key={d.date} value={d.date}>{d.date} ({d.signals})</option>)}{day&&!days.some(d=>d.date===day)&&<option value={day}>{day} (0)</option>}</select></label>
        <select aria-label="Ativo do histórico" value={asset} onChange={(e) => {setAsset(e.target.value);setPage(1)}} className="rounded border border-input bg-card px-2 py-1.5">
          <option value="">Todos os ativos</option>
          {assets.map((a) => <option key={a} value={a}>{assetLabel(a)}</option>)}
        </select>
        <select aria-label="Estratégia do histórico" value={strategy} onChange={(e) => {setStrategy(e.target.value);setPage(1)}} className="rounded border border-input bg-card px-2 py-1.5">
          <option value="">Todas as estratégias</option>
          {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="history-table w-full font-mono text-sm">
          <thead className="border-b border-border text-left text-xs text-muted-foreground">
            <tr>{["Entrada (BRT)", "Ativo", "Direção", "TF", "Vitórias observadas", "Estratégia", "Entrada", "Saída", "Resultado", ...(isAdmin?["P/L estimado"]:[])].map((h) => <th key={h} className="px-3 py-2 font-normal">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-border">
            {signals === null && <tr><td colSpan={isAdmin?10:9} className="px-3 py-6 text-left text-muted-foreground">Carregando...</td></tr>}
            {signals && list.length === 0 && <tr><td colSpan={isAdmin?10:9} className="px-3 py-6 text-left text-muted-foreground">Nenhum sinal ainda.</td></tr>}
            {list.map((s) => (
              <tr key={s.id}>
                <td data-label="Entrada (BRT)" className="px-3 py-2">{fmtDateTime(s.entry_time)}{isAdmin&&<a href={"/validacao?signal="+encodeURIComponent(s.id)} className="block text-xs text-primary">Conferir operação</a>}</td>
                <td data-label="Ativo" className="px-3 py-2">{assetLabel(s.asset)}</td>
                <td data-label="Direção" className={`px-3 py-2 ${s.direction === "CALL" ? "text-bull" : "text-bear"}`}>{s.direction === "CALL" ? "CALL ⬆️" : "PUT ⬇️"}</td>
                <td data-label="TF" className="px-3 py-2">{tfLabel(s.timeframe)}</td>
                <td data-label="Histórico observado" className="px-3 py-2"><ObservedRate value={s.observed_win_rate} compact/></td>
                <td data-label="Estratégia" className="px-3 py-2 font-sans">{s.strategy}</td>
                <td data-label="Preço de entrada" className="px-3 py-2">{fmtPrice(s.entry_price)}</td>
                <td data-label="Preço de saída" className="px-3 py-2">{s.exit_price != null ? fmtPrice(s.exit_price) : "—"}</td>
                <td data-label="Resultado" title={s.cancellation_reason} className={`px-3 py-2 font-semibold ${s.result === "WIN" ? "text-bull" : s.result === "LOSS" ? "text-bear" : "text-warn"}`}>{s.result === "PENDING" ? "PENDENTE" : s.result === "APURANDO" ? "APURANDO" : s.result}<span aria-label="Entrega do resultado no Telegram" title={s.telegram_result?.detail} className="block text-xs font-normal text-muted-foreground">Telegram: {resultDelivery(s)}</span></td>
                {isAdmin&&<td data-label="P/L estimado" className="px-3 py-2">{s.estimated_pnl_brl==null?"—":s.estimated_pnl_brl.toLocaleString("pt-BR",{style:"currency",currency:"BRL"})}{s.telegram?.lead_status==='late'&&<span className="block text-xs text-warn">Sinal enviado com atraso</span>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)} className="rounded border border-border p-2 disabled:opacity-50">Anterior</button><span>Página {page}/{Math.max(1,Math.ceil(total/50))} · 50 registros por página</span><button disabled={page*50>=total} onClick={()=>setPage(p=>p+1)} className="rounded border border-border p-2 disabled:opacity-50">Próxima</button></div>
    </AppShell>
  );
}

function resultDelivery(s:any){
 const labels:Record<string,string>={pendente:'aguardando envio',repetir:'reenvio agendado',enviando:'enviando',enviado:'enviado',incerto:'sem confirmação',falha:'falha','não configurado':'não configurado'};
 return labels[s.telegram_result?.status]||(['PENDING','APURANDO'].includes(s.result)?'aguardando apuração':'não registrado');
}

function Stat({ k, v, cls = "" }: { k: string; v: string; cls?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs text-muted-foreground">{k}</p>
      <p className={`text-2xl font-bold ${cls}`}>{v}</p>
    </div>
  );
}
