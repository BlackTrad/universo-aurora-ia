import {useEffect,useRef,useState} from 'react';
import {api} from '@/lib/api';

export function HistoryClear({day,onCleared}:{day:string;onCleared:()=>Promise<void>}){
 const [scope,setScope]=useState('day'),[period,setPeriod]=useState(day),[confirming,setConfirming]=useState(false);
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const submitting=useRef(false);
 useEffect(()=>{if(!day)return;setPeriod(scope==='month'?day.slice(0,7):day);setConfirming(false)},[day,scope]);
 async function clear(){
  if(submitting.current)return;
  submitting.current=true;setBusy(true);setError('');setMessage('');
  try{
   const response=await api('/history/clear',{scope,period:scope==='all'?null:period,confirmed:true});
   setConfirming(false);
   setMessage(`${response.cleared} operação(ões) removida(s) do histórico visível. ${response.pending_preserved} pendente(s) preservada(s).`);
   try{await onCleared()}catch{setError('Limpeza concluída. Recarregue a página para atualizar a lista.')}
  }catch(e:any){setError(e.message)}finally{submitting.current=false;setBusy(false)}
 }
 return <section className="mb-4 rounded border border-border bg-card p-3 text-sm" aria-label="Limpar histórico">
  <form className="flex flex-wrap items-end gap-2" onSubmit={e=>{e.preventDefault();setMessage('');setError('');setConfirming(true)}}>
   <label className="min-w-0">Limpar histórico<select aria-label="Período da limpeza" disabled={busy} value={scope} onChange={e=>{setScope(e.target.value);setConfirming(false)}} className="mt-1 block rounded border border-input bg-background p-2"><option value="day">Um dia</option><option value="month">Um mês</option><option value="all">Todo o histórico</option></select></label>
   {scope!=='all'&&<label className="min-w-0">{scope==='day'?'Dia em Brasília':'Mês em Brasília'}<input aria-label="Data da limpeza" disabled={busy} type={scope==='day'?'date':'month'} required value={period} onChange={e=>{setPeriod(e.target.value);setConfirming(false)}} className="mt-1 block min-w-0 max-w-full rounded border border-input bg-background p-2"/></label>}
   <button disabled={busy||confirming} className="rounded border border-bear px-3 py-2 text-bear disabled:opacity-50">Revisar limpeza</button>
  </form>
  <p className="mt-2 text-xs text-muted-foreground">Retira concluídas da visualização. Preserva originais, métricas e controles; pendentes continuam.</p>
  {confirming&&<div role="dialog" aria-label="Confirmar limpeza do histórico" className="mt-3 rounded border border-bear p-3">
   <h2 className="font-semibold">Confirmar limpeza</h2>
   <p className="my-2">Limpar {scope==='all'?'todo o histórico visível':scope==='month'?'o mês '+period:'o dia '+period}?</p>
   <div className="flex flex-wrap gap-2"><button type="button" onClick={clear} disabled={busy} className="rounded bg-bear px-3 py-2 text-background disabled:opacity-50">{busy?'Limpando...':'Confirmar limpeza'}</button><button type="button" onClick={()=>setConfirming(false)} disabled={busy} className="rounded border border-border px-3 py-2">Cancelar</button></div>
  </div>}
  {message&&<p role="status" className="mt-2 text-xs">{message}</p>}
  {error&&<p role="alert" className="mt-2 text-bear">{error}</p>}
 </section>
}
