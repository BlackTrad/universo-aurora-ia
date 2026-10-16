import {AppShell} from '@/components/AppShell';
import {LearningStatus} from '@/components/LearningStatus';
import {WinningSetups} from '@/components/WinningSetups';
import {ResultsTotals} from '@/components/ResultsTotals';
import {StrategyReview} from '@/components/StrategyReview';
import {PlanetaryPerformance} from '@/components/PlanetaryPerformance';
import {useData} from '@/lib/api';

export default function Learning(){
 const {snapshot,connected,error}=useData();
 return <AppShell><div className="space-y-3">
  <div className="flex flex-wrap items-center justify-between gap-2"><h1 className="text-xl font-semibold">Aprendizado dos sinais</h1><a href="/validacao" className="text-sm text-primary">Validação detalhada</a></div>
  {error&&<p role="alert" className="rounded border border-bear p-3 text-sm text-bear">{error}</p>}
  {!snapshot?<p>Carregando histórico…</p>:<>
   <ResultsTotals snapshot={snapshot}/>
   <LearningStatus value={snapshot.learning}/>
   <PlanetaryPerformance revision={[snapshot.result_totals?.wins,snapshot.result_totals?.losses,snapshot.result_totals?.ties,snapshot.learning?.last_feedback?.signal_id].join(':')}/>
   <StrategyReview revision={[snapshot.result_totals?.wins,snapshot.result_totals?.losses,snapshot.result_totals?.ties,snapshot.learning?.last_feedback?.signal_id].join(':')}/>
   <WinningSetups connected={connected} revision={[snapshot.result_totals?.wins,snapshot.result_totals?.losses,snapshot.result_totals?.ties].join(':')}/>
  </>}
 </div></AppShell>;
}
