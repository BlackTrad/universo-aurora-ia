import {useEffect,useState} from 'react';
import {assetLabel} from '@/lib/apex';

export function AnalysisSettings({snapshot,busy,save}:{snapshot:any;busy:boolean;save:(patch:any)=>Promise<void>}){
 const settings={...snapshot.settings,monitoring:busy};
 const crypto=snapshot.asset_scope==='crypto_binary';
 const maximum=crypto?18:8;
 return <div className="grid items-start gap-3 lg:grid-cols-2">
  <section className="panel-card settings-card p-4">
   <h2 className="mb-3 font-semibold">Análise e ativos</h2>
   <div className="grid grid-cols-2 gap-3 text-sm">
    <label>Período da análise<select aria-label="Período da análise" disabled={busy} value={settings.timeframe} onChange={e=>save({timeframe:e.target.value})} className="mt-1 w-full rounded border border-input bg-background p-2">{snapshot.timeframes.map((tf:string)=><option key={tf}>{tf}</option>)}</select></label>
    <label>Duração<select aria-label="Duração" disabled={busy} value={settings.duration_seconds} onChange={e=>save({duration_seconds:Number(e.target.value)})} className="mt-1 w-full rounded border border-input bg-background p-2">{[60,120,180,300].map(n=><option key={n} value={n}>{n/60} minuto(s)</option>)}</select></label>
   </div>
   <p className="mt-2 text-xs text-muted-foreground">Período das velas e duração da operação são independentes.</p>
   <fieldset disabled={busy} className="mt-4">
    <legend className="mb-2 text-sm">{crypto?'Criptomoedas de opções binárias':'Pares de moedas configurados'} · {snapshot.available_assets.length}</legend>
    <div className="flex flex-wrap gap-2">{snapshot.available_assets.map((asset:string)=>{
     const on=settings.assets.includes(asset);
    return <button key={asset} type="button" disabled={!on&&settings.assets.length>=maximum} aria-pressed={on} onClick={()=>{const next=on?settings.assets.filter((a:string)=>a!==asset):[...settings.assets,asset];if(next.length)save({assets:next})}} className={'rounded border px-3 py-2 font-mono text-xs disabled:opacity-50 '+(on?'border-primary bg-primary/10 text-primary':'border-border text-muted-foreground hover:bg-accent')}>{assetLabel(asset)}</button>;
    })}</div>
   </fieldset>
    <p className="mt-3 text-xs text-muted-foreground">{crypto?'Somente os 18 pares de criptomoedas /USD da corretora.':'Os pares existentes no seu arquivo de configuração foram preservados.'} Ativos só geram sinais após confirmação de contrato e payout em tempo real. Sem spot, ordens automáticas ou martingale.</p>
  </section>
  <section className="panel-card settings-card p-4"><h2 className="mb-3 font-semibold">Controles de emissão</h2><LimitSettings settings={settings} busy={busy} save={save}/>
   <details className="mt-4 border-t border-border pt-3"><summary className="cursor-pointer text-sm text-muted-foreground">Valores e payout (opcional)</summary><div className="mt-3"><MoneySettings settings={settings} busy={busy} save={save}/></div></details>
  </section>
 </div>;
}

function LimitSettings({settings,busy,save}:any){
 const fields=[['hourly_limit','Sinais por hora',1,60],['simultaneous_limit','Sinais simultâneos',1,2],['minimum_score','Nota técnica mínima',70,100],['loss_pause_after','Pausar após perdas consecutivas',1,10],['pause_minutes','Duração da pausa (min)',1,120]] as const;
 const [draft,setDraft]=useState<Record<string,string>>({});
 useEffect(()=>{setDraft(Object.fromEntries(fields.map(([k])=>[k,String(settings[k])])));},[settings.hourly_limit,settings.simultaneous_limit,settings.minimum_score,settings.loss_pause_after,settings.pause_minutes]);
 return <form className="space-y-3 text-sm" onSubmit={e=>{e.preventDefault();save(Object.fromEntries(fields.map(([k])=>[k,Number(draft[k])])));}}>
 <p className="font-semibold">Limites e qualidade</p>
 <div className="grid grid-cols-2 gap-2">{fields.filter(([key])=>key!=='minimum_score').map(([key,label,min,max])=><label key={key} className="block">{label}<input type="number" min={min} max={max} step="1" required disabled={settings.monitoring||busy} value={draft[key]??''} onChange={e=>setDraft({...draft,[key]:e.target.value})} className="mt-1 w-full rounded border border-input bg-background p-2"/></label>)}</div>
 <details><summary className="cursor-pointer text-muted-foreground">Critério interno do motor (avançado)</summary><label className="mt-2 block">Critério complementar do Fibonacci<input type="number" min="70" max="100" step="1" required disabled={settings.monitoring||busy} value={draft.minimum_score??''} onChange={e=>setDraft({...draft,minimum_score:e.target.value})} className="mt-1 w-full rounded border border-input bg-background p-2"/></label><p className="mt-1 text-muted-foreground">As estratégias do manual usam critérios obrigatórios. Este filtro numérico se aplica ao Fibonacci; não representa percentual de vitória.</p></details><p className="text-muted-foreground">Limites da janela móvel de 60 minutos. Pare a análise para editar.</p>
 <button disabled={settings.monitoring||busy} className="rounded border border-border px-3 py-2 disabled:opacity-50">Salvar limites</button>
 </form>;
}
function MoneySettings({settings,busy,save}:any){
 const [stake,setStake]=useState(''),[payout,setPayout]=useState('');
 useEffect(()=>{setStake(settings.stake_brl==null?'':String(settings.stake_brl));setPayout(settings.payout_pct==null?'':String(settings.payout_pct))},[settings.stake_brl,settings.payout_pct]);
 return <form onSubmit={e=>{e.preventDefault();save({stake_brl:stake===''?null:Number(stake),payout_pct:payout===''?null:Number(payout)})}} className="space-y-3 text-sm">
 <label className="block">Entrada (R$)<input className="mt-1 w-full rounded border border-input bg-background p-2" type="number" min="0.01" max="1000000" step="0.01" required={payout!==''} value={stake} disabled={settings.monitoring||busy} onChange={e=>setStake(e.target.value)}/></label>
 <label className="block">Payout (%)<input className="mt-1 w-full rounded border border-input bg-background p-2" type="number" min="0.01" max="100" step="any" required={stake!==''} value={payout} disabled={settings.monitoring||busy} onChange={e=>setPayout(e.target.value)}/></label>
 <p className="text-muted-foreground">Valores opcionais para estimar P/L; não representam saldo da corretora.</p>
 <button disabled={settings.monitoring||busy} className="rounded border border-border px-3 py-2 disabled:opacity-50">Salvar valores</button></form>
}
