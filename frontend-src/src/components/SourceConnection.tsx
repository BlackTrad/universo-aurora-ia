import {useRef,useState} from 'react';
import {api,useData} from '@/lib/api';

export function SourceConnection(){
 const {snapshot,refresh}=useData();const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
 async function check(){if(pending.current)return;pending.current=true;
  setBusy(true);setMessage('');
  try{const r=await api('/connection/check',{});await refresh();setMessage(r.detail||'Verificação concluída.');}
  catch(e:any){setMessage(e.message)}finally{pending.current=false;setBusy(false)}
 }
 const pending=useRef(false);
 const locked=!snapshot||busy||snapshot?.scan?.active||snapshot?.busy;
 return <section className="space-y-3 rounded-lg border border-border bg-card p-5">
  <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Fontes de dados</h2><span className="source-badge">Automático</span></div>
  <p className="text-sm text-muted-foreground">A AURORA consulta a fonte compatível com cada ativo. Você só escolhe o que deseja monitorar.</p>
  <div className="space-y-2">{(snapshot?.providers||[]).map((p:any)=><div key={p.key} className="flex items-center justify-between gap-2 rounded border border-border p-2 text-xs"><span className="source-badge" data-source={p.key}>{p.name}</span><span className={p.status==='disponível'?'text-bull':p.status==='indisponível'?'text-bear':['parcial','desatualizado'].includes(p.status)?'text-warn':'text-muted-foreground'} title={p.detail}>{p.status||'Não verificado'}</span></div>)}</div>
  <p className="text-xs text-muted-foreground">Sem chave de conta. Cada sinal, gráfico e apuração conservam a fonte e o par originais.</p>
  <p className="text-xs text-muted-foreground">{snapshot?.feed?.detail||'Conexões ainda não verificadas.'}</p>
  <button disabled={locked} onClick={check} className="rounded bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50">{busy?'Verificando…':'Verificar conexões'}</button>
  {message&&<p role="status" className="text-xs">{message}</p>}
 </section>
}
