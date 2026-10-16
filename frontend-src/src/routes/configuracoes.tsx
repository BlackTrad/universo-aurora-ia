import {ManualStrategies} from '@/components/ManualStrategies';
import {useRef,useState} from 'react';
import {AppShell} from '@/components/AppShell';
import {AnalysisSettings} from '@/components/AnalysisSettings';
import {AssetPreviews} from '@/components/AssetPreviews';
import {api,useData} from '@/lib/api';
import {chartLink} from '@/lib/chartTimeframe';

export default function Settings(){
 const {snapshot,connected,error,setError,now,refresh}=useData();
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const pending=useRef(false);
 const locked=!connected||busy||Boolean(snapshot?.scan.active||snapshot?.analysis_active||snapshot?.busy);
 const save=async(patch:any)=>{
  if(!snapshot||locked||pending.current)return;
  pending.current=true;setBusy(true);setError('');setNotice('');
  try{await api('/settings',{...snapshot.settings,...patch},'PUT');await refresh();setNotice('Configurações salvas.')}catch(e:any){setError(e.message)}finally{pending.current=false;setBusy(false)};
 };
 return <AppShell><div className="space-y-3">
  <div className="flex flex-wrap items-center justify-between gap-2"><h1 className="text-xl font-semibold">Configurações de análise</h1><a href="/" className="text-sm text-primary">Voltar ao painel</a></div>
  {error&&<p role="alert" className="rounded border border-bear p-3 text-sm text-bear">{error}</p>}
  {!snapshot?<p>Carregando configurações…</p>:<>
   <div className="flex flex-wrap items-center gap-2 text-sm"><span className="source-badge">Fontes automáticas</span><a href="/conexoes" className="text-muted-foreground underline underline-offset-4">Ver conexões</a></div>
   {(snapshot.scan.active||snapshot.analysis_active)&&<p className="text-sm text-warn">Pare a análise no painel para editar.</p>}
   {!snapshot.scan.active&&!snapshot.analysis_active&&snapshot.busy&&<p className="text-sm text-warn">Análise ocupada. Aguarde a liberação para editar.</p>}
   {notice&&<p role="status" className="text-sm text-bull">{notice}</p>}
   <AnalysisSettings snapshot={snapshot} busy={locked} save={save}/><ManualStrategies/>
   <details className="panel-card p-3"><summary className="cursor-pointer text-sm text-muted-foreground">Prévia por ativo · detalhes da última análise</summary><div className="mt-3"><AssetPreviews snapshot={snapshot} now={now} connected={connected} onSelect={(source,asset,tf)=>location.assign(chartLink(source,asset,tf))}/></div></details>
  </>}
 </div></AppShell>;
}
