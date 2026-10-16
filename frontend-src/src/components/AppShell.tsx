import {useEffect,useRef,useState,type ReactNode} from 'react';
import {api,useData} from '@/lib/api';
import {CosmicBackground,useCosmicScene} from '@/components/CosmicBackground';

export function AppShell({children}:{children:ReactNode}){
 const cosmos=useCosmicScene();
 const {snapshot,connected,now,chartStatus}=useData();
 const stale=chartStatus?.data_at&&now/1000-chartStatus.data_at>chartStatus.interval_seconds*2+30;
 const chartLabel=chartStatus?.status==='closed'?'Mercado fechado':chartStatus?.status==='unavailable'?'Indisponível':chartStatus?.status==='loading'?'Carregando':chartStatus?.status==='stale'||stale?'Dados atrasados':chartStatus?.status==='live'?'Atualizado':'Não verificado';
 const status=connected?(chartStatus?'Gráfico: '+chartLabel:'Análise: '+(snapshot?.feed?.status||'Não verificado')):(snapshot?'Sem conexão':'Não verificado');
 const healthy=connected&&(chartStatus?chartLabel==='Atualizado':snapshot?.feed?.status==='disponível');
 const monitoring=snapshot?.scan?.active?(snapshot.scan.preview?'Prévia automática sem envio':'Monitoramento ativo'):snapshot?.analysis_active?'Prévia em andamento':'Monitoramento parado';
 const menu=useRef<HTMLDetailsElement>(null);
 const [publicAccess,setPublicAccess]=useState(false);
 useEffect(()=>{let active=true;api('/auth/status').then(r=>{if(active)setPublicAccess(Boolean(r.public_access))}).catch(()=>{});return()=>{active=false}},[]);
 const primary=[['/','Dashboard'],['/sinais','Sinais'],['/historico','Histórico'],['/calendario','Calendário diário'],['/noticias','Notícias'],['/telegram','Telegram']];
 const secondary=[['/universo-ia','Universo IA'],['/aprendizado','Aprendizado'],['/configuracoes','Configurações'],['/conexoes','Conexões'],['/validacao','Validação']];
 const active=(path:string)=>location.pathname===path||(path==='/'&&location.pathname==='/analise');
 useEffect(()=>{
  const close=(event:Event)=>{if(!menu.current?.open)return;const keyboard=event.type==='keydown';if(keyboard?(event as KeyboardEvent).key==='Escape':!menu.current.contains(event.target as Node)){menu.current.open=false;if(keyboard)menu.current.querySelector('summary')?.focus()}};
  document.addEventListener('click',close);document.addEventListener('keydown',close);
  return()=>{document.removeEventListener('click',close);document.removeEventListener('keydown',close)};
 },[]);
 return <div className="cosmic-app flex min-h-screen flex-col text-foreground">
  <a href="#conteudo-principal" className="skip-link">Ir para o conteúdo</a>
  <CosmicBackground root={cosmos.root}/>
  <header className="app-header border-b border-border">
   <div className="app-header-layout">
    <div className="app-header-brand"><a href="/" className="aurora-wordmark" aria-label="AURORA · Painel"><svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="9"/><ellipse cx="20" cy="20" rx="19" ry="7" transform="rotate(-35 20 20)"/><circle cx="32" cy="11" r="2"/></svg>AURORA</a><span aria-label="Versão do projeto" className="app-version">v{snapshot?.version||'—'}</span></div>
    <nav aria-label="Navegação principal" className="app-header-nav">
     {primary.map(([path,title])=><a key={path} href={path} aria-current={active(path)?'page':undefined} className={'app-header-link rounded '+(active(path)?'bg-primary/10 text-primary':'text-muted-foreground hover:text-foreground')}>{title}</a>)}
     <details ref={menu} className="relative">
      <summary className={'app-header-link list-none cursor-pointer rounded [&::-webkit-details-marker]:hidden '+(secondary.some(([path])=>active(path))?'bg-secondary':'text-muted-foreground hover:text-foreground')}>Mais <span aria-hidden="true">⌄</span></summary>
      <div className="absolute right-0 z-30 mt-2 w-44 rounded-lg border border-border bg-card p-1 shadow-xl">
       {secondary.map(([path,title])=><a key={path} href={path} aria-current={active(path)?'page':undefined} className={'block rounded px-3 py-2 '+(active(path)?'bg-secondary text-foreground':'text-muted-foreground hover:bg-accent hover:text-foreground')}>{title}</a>)}
       {publicAccess&&<form method="post" action="/api/auth/logout"><button type="submit" className="w-full rounded px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-foreground">Sair com segurança</button></form>}
      </div>
     </details>
    </nav>
    <div className="app-header-status">
     <div role="status" aria-live="polite" aria-label="Disponibilidade da fonte de dados" title={connected?(chartStatus?chartStatus.asset+' · '+chartStatus.source_key+' · '+chartStatus.timeframe:snapshot?.feed?.detail):'Aguardando conexão com o backend.'} className="app-header-connection font-mono font-semibold text-muted-foreground">
      <span aria-hidden="true" className={'h-2 w-2 shrink-0 rounded-full '+(healthy?'bg-bull':!connected||chartLabel==='Indisponível'?'bg-bear':'bg-warn')}/><span className="app-header-status-text">{status}</span>
     </div>
     <span data-testid="monitoring-status" className={'app-header-monitoring '+(snapshot?.scan?.active?'text-primary':'text-muted-foreground')} title={snapshot?.scan?.message}>{monitoring}</span>
    </div>
   </div>
  </header>
  <main id="conteudo-principal" tabIndex={-1} className="app-main">{children}</main>
  <footer className="app-footer"><p>Resultados indicativos <span aria-hidden="true">·</span> Decisão manual <span aria-hidden="true">·</span> Sem martingale</p>{cosmos.controls}</footer>
 </div>;
}
