import {useEffect,useMemo,useRef,useState} from 'react';
import {AppShell} from '@/components/AppShell';
import {api} from '@/lib/api';
type Day={date:string;signals:number};
type Summary={total:number;wins:number;losses:number;ties:number;pending:number;unavailable:number;cancelled:number;win_rate_pct:number|null;basis:string};
const brt=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
export default function DailyCalendar(){
 const [month,setMonth]=useState(brt().slice(0,7)),[days,setDays]=useState<Day[]>([]),[selected,setSelected]=useState(brt()),[records,setRecords]=useState<any[]>([]),[summary,setSummary]=useState<Summary|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const generation=useRef(0);
 useEffect(()=>{
  const current=++generation.current,controller=new AbortController();
  let dead=false,timer:ReturnType<typeof setTimeout>|undefined;
  const active=()=>!dead&&generation.current===current;
  setBusy(true);setSummary(null);setRecords([]);setError('');
  async function load(){
   // Espera todas as consultas da rodada antes de agendar a próxima.
   const results=await Promise.allSettled([
    api('/history/daily-summary?day='+selected,undefined,undefined,controller.signal),
    api('/history?page=1&page_size=20&start_date='+selected+'&end_date='+selected,undefined,undefined,controller.signal),
    api('/history/days',undefined,undefined,controller.signal),
   ]);
   if(!active())return;
   const [summaryResult,recordsResult,daysResult]=results;
   if(summaryResult?.status==='fulfilled')setSummary(summaryResult.value);
   if(recordsResult?.status==='fulfilled')setRecords(recordsResult.value.items||[]);
   if(daysResult?.status==='fulfilled')setDays(daysResult.value.days||[]);
   const failure=results.find(result=>result.status==='rejected');
   setError(failure?.status==='rejected'?(failure.reason instanceof Error?failure.reason.message:'Não foi possível atualizar o calendário.'):'');
   setBusy(false);timer=setTimeout(load,5000);
  }
  load();
  return()=>{dead=true;controller.abort();clearTimeout(timer)};
 },[selected]);
 const [year,mo]=month.split('-').map(Number),offset=new Date(Date.UTC(year,mo-1,1)).getUTCDay(),count=new Date(Date.UTC(year,mo,0)).getUTCDate();
 const counts=useMemo(()=>new Map(days.map(d=>[d.date,d.signals])),[days]);

 return <AppShell><div className="space-y-4"><h1 className="text-2xl font-bold text-primary">Calendário diário</h1><p className="text-xs text-muted-foreground">Dias e contagens reais do histórico · Horário de Brasília · Resultados indicativos conforme a fonte de liquidação.</p>
 <div className="panel-card p-4"><label className="text-sm">Mês <input type="month" aria-label="Mês do calendário" className="ml-2 rounded border border-input bg-card p-2" value={month} onChange={e=>setMonth(e.target.value)}/></label><div className="mt-4 grid grid-cols-7 gap-1 text-center text-xs">{['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'].map(d=><div className="py-2 text-muted-foreground" key={d}>{d}</div>)}{Array.from({length:offset},(_,i)=><div key={'pad'+i}/>)}{Array.from({length:count},(_,i)=>{const date=month+'-'+String(i+1).padStart(2,'0'),n=counts.get(date)||0;return <button type="button" aria-label={date+' '+n+' sinais'} key={date} onClick={()=>setSelected(date)} className={'min-h-14 rounded border p-1 '+(selected===date?'border-primary bg-primary/10':'border-border hover:bg-accent')}><span className="block">{i+1}</span><span className={'text-xs '+(n?'text-primary':'text-muted-foreground')}>{n?n+' sinais':'—'}</span></button>})}</div></div>
 <div className="panel-card p-4"><h2 className="font-semibold">{selected} · {busy?'Carregando':(summary?.total??'—')+' sinais no dia'}</h2>{error&&<p role="alert" className="text-bear">{error}</p>}<p className="mt-2 text-sm">WIN: {summary?.wins??'—'} · LOSS: {summary?.losses??'—'} · Empates: {summary?.ties??'—'} · Pendentes: {summary?.pending??'—'} · Indisponíveis: {summary?.unavailable??'—'} · Taxa observada: {summary?.win_rate_pct!=null?summary.win_rate_pct.toFixed(1)+'%':'—'}</p><p className="mt-1 text-xs text-muted-foreground">Métricas agregadas no backend para o dia inteiro, independentemente da paginação. Resultados indicativos; não representam operações executadas.</p><p className="mt-2 text-xs text-muted-foreground">Exibindo {records.length} registros recentes desse dia em consulta de amostra.</p><a className="mt-3 inline-block text-sm text-primary underline" href={'/historico'}>Consultar histórico completo e exportação →</a></div></div></AppShell>
}
