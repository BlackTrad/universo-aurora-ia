import {AppShell} from '@/components/AppShell';
import {useData} from '@/lib/api';

const planets=[
 {name:'Groq',focus:'Price Action',color:'groq'},
 {name:'NVIDIA',focus:'Tendência',color:'nvidia'},
 {name:'Nemotron',focus:'Estrutura SMC',color:'nemotron'},
 {name:'GLM',focus:'Liquidez',color:'glm'},
 {name:'Gemini',focus:'Confluências',color:'gemini'},
 {name:'Kimi',focus:'Análise crítica',color:'kimi'},
];

function statusLabel(status:string,vote:any){
 if(vote?.status==='disponivel')return vote.dados_insuficientes?'NEUTRO':vote.direcao;
 return ({disponivel:'PRONTO',analisando:'ANALISANDO',timeout:'TIMEOUT',erro:'ERRO',rate_limit:'LIMITE',indisponivel:'INDISPONÍVEL'} as Record<string,string>)[status]||'INDISPONÍVEL';
}

function coreLabel(status:string){
 return ({approved:'APROVADO',rejected:'REJEITADO',processing:'ANALISANDO',unavailable:'INDISPONÍVEL',ready:'DISPONÍVEL'} as Record<string,string>)[status]||'INDISPONÍVEL';
}

function updatedTime(timestamp:unknown){
 const value=typeof timestamp==='number'&&Number.isFinite(timestamp)?new Date(timestamp*1000):null;
 if(!value||!Number.isFinite(value.getTime()))return null;
 return new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(value);
}

export default function UniverseAI(){
 const {snapshot,connected}=useData();
 const ai=snapshot?.ai;
 const processing=connected&&ai?.status==='processing';
 // Votos de uma rodada concluída não representam a rodada em andamento.
 const currentVotes=connected&&!processing?(ai?.last_votes||[]):[];
 const votes=new Map(currentVotes.map((vote:any)=>[vote.agente,vote]));
 const agents=new Map((ai?.agents||[]).map((agent:any)=>[agent.agente,agent]));
 const decision=connected&&!processing?ai?.last_decision:null;
 const decisionCount=currentVotes.filter((vote:any)=>vote.status==='disponivel'&&!vote.dados_insuficientes&&vote.direcao===decision?.direcao).length;
 const context=ai?.context;
 const updated=updatedTime(ai?.updated_at);

 return <AppShell>
  <div className="universe-page">
   <header className="universe-heading">
    <div><p className="universe-kicker">AURORA / AGENT TEAMS</p><h1>Universo IA</h1><p>Votos independentes alimentados pelo pacote de dados do backend.</p></div>
    <span className={'universe-connection '+(connected?'is-connected':'')}>{connected?'BACKEND CONECTADO':'SEM CONEXÃO'}</span>
   </header>

   <section className="universe-core" aria-label="Consenso Aurora Core">
    <div>
     <p className="universe-kicker">AURORA CORE</p>
     <h2>{connected?coreLabel(ai?.status||'unavailable'):'SEM CONEXÃO'}</h2>
     <p className="universe-detail">{connected?(ai?.detail||'Aguardando o estado do backend.'):'O estado atual dos agentes não pode ser confirmado.'}</p>
     {context?.ativo&&<p className="universe-detail" data-testid="ai-analysis-context">{connected?'Rodada':'Última rodada recebida'}: <b>{context.ativo}</b> · {context.timeframe||'Período não informado'}{context.fonte?' · '+context.fonte:''}</p>}
     {updated&&<p className="universe-detail">Atualizado em {updated} · Brasília</p>}
    </div>
    <div className="universe-decision" aria-live="polite">
     <span>{decision?.direcao||'—'}</span>
     {decision?.direcao&&<small>{decisionCount}/6 votos direcionais</small>}
     {decision?.confianca>0&&<small>Evidência analítica {Number(decision.confianca).toFixed(0)}% · não é probabilidade de vitória</small>}
    </div>
   </section>

   <section className="planet-grid" aria-label="Estado dos seis planetas">
    {planets.map(planet=>{
     const vote:any=votes.get(planet.name);
     const agent:any=agents.get(planet.name);
     const state=!connected?'indisponivel':processing?(agent?.status==='indisponivel'?'indisponivel':'analisando'):vote?.status||agent?.status||'indisponivel';
     const label=statusLabel(state,vote);
     return <article key={planet.name} className="planet-card" data-status={state}>
      <div className="planet-card-top">
       <span className="planet-sphere" data-planet={planet.color} aria-hidden="true"/>
       <div className="planet-name"><h2>{planet.name}</h2><p>{planet.focus}</p></div>
       <span className="planet-status" data-status={state}>{label}</span>
      </div>
      <p className="planet-model">{agent?.model||'Modelo não configurado'}</p>
      <p className="planet-detail">{!connected?'Sem conexão com o backend.':processing&&state==='analisando'?'Aguardando o voto desta rodada.':vote?.justificativa||agent?.detail||'Aguardando validação do provedor.'}</p>
      {vote?.confianca>0&&<p className="planet-confidence">Força de evidência: {Number(vote.confianca).toFixed(0)}% · não é taxa de acerto</p>}
      {vote?.latency_ms!=null&&<p className="planet-latency">Resposta em {Number(vote.latency_ms).toFixed(0)} ms</p>}
     </article>;
    })}
   </section>
  </div>
 </AppShell>;
}
