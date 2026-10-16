// Apenas apresentação: reúne análises da mesma sessão, sem autorizar entradas.
export function matchingAnalysis(snapshot:any):any {
 const settings=snapshot?.settings;
 if(!settings)return null;
 const matches=(a:any)=>a.timeframe===settings.timeframe&&
  (a.duration_seconds??a.configuration?.settings?.duration_seconds)===settings.duration_seconds&&a.strategy===settings.strategy;
 if(snapshot.source_mode!=='automatic'&&settings.source_mode!=='automatic'){
  return (snapshot.analyses||[]).find((a:any)=>matches(a)&&a.source_key===snapshot.provider&&
   a.assets?.length===settings.assets.length&&settings.assets.every((asset:string)=>a.assets.includes(asset)))||null;
 }
 const groups=new Map<string,string[]>();
 for(const asset of settings.assets){const key=snapshot.asset_sources?.[asset]||snapshot.provider;groups.set(key,[...(groups.get(key)||[]),asset]);}
 const candidates=(snapshot.analyses||[]).filter((a:any)=>{
  const assets=groups.get(a.source_key);
  return assets&&matches(a)&&a.assets?.length===assets.length&&assets.every(asset=>a.assets.includes(asset));
 }).slice().sort((a:any,b:any)=>b.created_at-a.created_at);
 const session=snapshot.scan?.id??candidates[0]?.scan_id;
 const analyses=[...groups.keys()].map(key=>candidates.find((a:any)=>a.source_key===key&&a.scan_id===session)).filter(Boolean);
 if(!analyses.length)return null;
 const completed=analyses.filter((a:any)=>a.finished_at!=null).length;
 const waiting=(asset:string)=>({asset,source_key:snapshot.asset_sources?.[asset]||snapshot.provider,
  timeframe:settings.timeframe,status:'waiting',reason:'Aguardando avaliação desta fonte.'});
 const rows=settings.assets.map((asset:string)=>{
  const key=snapshot.asset_sources?.[asset]||snapshot.provider;
  return analyses.find((a:any)=>a.source_key===key)?.asset_previews?.find((row:any)=>row.asset===asset)??waiting(asset);
 });
 const updated=Math.max(...analyses.map((a:any)=>a.finished_at??a.created_at));
 return {id:'automatic:'+String(session??analyses[0].id),assets:settings.assets,source_mode:'automatic',
  source_keys:analyses.map((a:any)=>a.source_key),strategy:settings.strategy,timeframe:settings.timeframe,
  duration_seconds:settings.duration_seconds,created_at:Math.min(...analyses.map((a:any)=>a.created_at)),
  finished_at:completed===groups.size?updated:undefined,updated_at:updated,
  status:completed<analyses.length?'analisando':completed<groups.size?'parcial':'concluída',
  message:`Fontes automáticas · ${completed}/${groups.size} fontes avaliadas.${analyses.every((a:any)=>a.preview)?' Prévia técnica sem envio.':''}`,
  asset_previews:rows};
}
