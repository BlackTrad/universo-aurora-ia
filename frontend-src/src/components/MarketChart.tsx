import {useEffect,useId,useMemo,useRef,useState,type PointerEvent as ReactPointerEvent} from 'react';
import {fmtPrice,fmtHM} from '@/lib/apex';

type Candle={openTime:number;open:number;high:number;low:number;close:number};
type Point={x:number;y:number};
type Prices={min:number;max:number};
type View={start:number|null;count:number;prices:Prices|null};
type Frame=Prices&{start:number;count:number;interval:number;left:number;right:number;top:number;bottom:number};
type Gesture={kind:'pan'|'price'|'time'|'pinch';frame:Frame;origin:Point;distance:number};
type Annotations={signal_id:string;entry_at:number;expiry_at:number;direction:string;cancelled:boolean;levels:{id:string;label:string;price:number;selected:boolean}[];zones:{id:string;label:string;lower:number;upper:number;start_at:number;end_at:number}[]};
const clamp=(n:number,min:number,max:number)=>Math.max(min,Math.min(max,n));
const initialView=():View=>({start:null,count:60,prices:null});
const automaticPrices=(rows:Candle[]):Prices=>{
 const low=rows.length?Math.min(...rows.map(c=>c.low)):0,high=rows.length?Math.max(...rows.map(c=>c.high)):1;
 const pad=Math.max((high-low)*.08,Math.abs(high)*.00002,0.000001);
 return {min:low-pad,max:high+pad};
};
const midpoint=(a:Point,b:Point):Point=>({x:(a.x+b.x)/2,y:(a.y+b.y)/2});
const distance=(a:Point,b:Point)=>Math.hypot(a.x-b.x,a.y-b.y);

// Screen navigation only. It never synthesizes candles or changes analysis/expiry.
export function MarketChart({candles,loading,source,intervalMs,annotations=null}:{candles:Candle[];loading:boolean;source:string;intervalMs:number;annotations?:Annotations|null}){
 const plotHost=useRef<HTMLDivElement>(null),svg=useRef<SVGSVGElement>(null);
 const [size,setSize]=useState({width:700,height:260}),[view,setView]=useState<View>(initialView),[hover,setHover]=useState<Point|null>(null),[dragging,setDragging]=useState(false);
 const viewRef=useRef(view),pointers=useRef(new Map<number,Point>()),gesture=useRef<Gesture|null>(null);
 const clipId='candles-'+useId().replace(/:/g,'');
 useEffect(()=>{
  const el=plotHost.current;if(!el)return;
  const ob=new ResizeObserver(([e])=>setSize({width:Math.max(240,e.contentRect.width),height:Math.max(1,e.contentRect.height)}));
  ob.observe(el);return()=>ob.disconnect();
 },[]);
 const data=useMemo(()=>[...new Map(candles.filter(c=>[c.openTime,c.open,c.high,c.low,c.close].every(Number.isFinite)).map(c=>[c.openTime,c])).values()].sort((a,b)=>a.openTime-b.openTime),[candles]);
 const interval=Number.isFinite(intervalMs)&&intervalMs>0?intervalMs:60000;
 const {width,height}=size,left=8,right=width-76,top=12,bottom=Math.max(top+20,height-28);
 const plotWidth=right-left,plotHeight=bottom-top,last=data[data.length-1];
 const latestStart=(count:number)=>{
  const future=annotations&&last?Math.ceil((annotations.expiry_at*1000-last.openTime)/interval)+2:3;
  const padding=clamp(future,3,Math.max(3,Math.floor(count*.45)));
  return (last?.openTime??0)-(count-padding)*interval;
 };
 const start=view.start??latestStart(view.count);
 const step=plotWidth/view.count,end=start+view.count*interval;
 const rows=data.filter(c=>c.openTime>=start-interval&&c.openTime<end);
 const {min,max}=view.prices??automaticPrices(rows.length?rows:data);
 const x=(time:number)=>left+((time-start)/interval+.5)*step;
 const y=(price:number)=>top+(max-price)/(max-min)*plotHeight;
 const frame:Frame={start,count:view.count,interval,left,right,top,bottom,min,max};
 const frameRef=useRef(frame);frameRef.current=frame;
 const commit=(next:View)=>{
  // Gesture handoff/wheel bursts can precede React's next paint. Rebase from
  // the committed frame immediately, rather than the last rendered frame.
  const nextStart=next.start??latestStart(next.count);
  const nextRows=data.filter(c=>c.openTime>=nextStart-interval&&c.openTime<nextStart+next.count*interval);
  const prices=next.prices??automaticPrices(nextRows.length?nextRows:data);
  viewRef.current=next;frameRef.current={...frameRef.current,start:nextStart,count:next.count,...prices};setView(next);
 };
 const point=(clientX:number,clientY:number):Point=>{
  const b=svg.current!.getBoundingClientRect();return {x:(clientX-b.left)*width/b.width,y:(clientY-b.top)*height/b.height};
 };
 const clearGesture=()=>{
  gesture.current=null;
  for(const id of pointers.current.keys())if(svg.current?.hasPointerCapture(id))svg.current.releasePointerCapture(id);
  pointers.current.clear();setDragging(false);
 };
 const reset=()=>{clearGesture();commit(initialView());setHover(null)};
 const zoomTime=(f:Frame,factor:number,anchorX:number,prices:Prices|null=viewRef.current.prices)=>{
  const ratio=clamp((anchorX-f.left)/(f.right-f.left),0,1),count=clamp(f.count*factor,8,600);
  commit({start:f.start+ratio*(f.count-count)*f.interval,count,prices});
 };
 const zoomPrice=(f:Frame,factor:number,anchorY:number)=>{
  const ratio=clamp((anchorY-f.top)/(f.bottom-f.top),0,1),price=f.max-ratio*(f.max-f.min);
  const span=clamp((f.max-f.min)*factor,Math.max(Math.abs(price)*1e-10,1e-10),1e15),newMax=price+ratio*span;
  commit({start:f.start,count:f.count,prices:{min:newMax-span,max:newMax}});
 };
 const rebaseGesture=()=>{
  const points=[...pointers.current.values()],f=frameRef.current;
  if(points.length>=2)gesture.current={kind:'pinch',frame:{...f},origin:midpoint(points[0],points[1]),distance:Math.max(1,distance(points[0],points[1]))};
  else if(points.length)gesture.current={kind:points[0].x>=f.right?'price':points[0].y>=f.bottom?'time':'pan',frame:{...f},origin:points[0],distance:0};
  else gesture.current=null;
  setDragging(points.length>0);
 };
 const onPointerDown=(e:ReactPointerEvent<SVGSVGElement>)=>{
  if(e.button!==0&&e.pointerType!=='touch')return;
  e.preventDefault();e.currentTarget.focus({preventScroll:true});e.currentTarget.setPointerCapture(e.pointerId);
  pointers.current.set(e.pointerId,point(e.clientX,e.clientY));setHover(null);rebaseGesture();
 };
 const onPointerMove=(e:ReactPointerEvent<SVGSVGElement>)=>{
  const p=point(e.clientX,e.clientY);
  if(!pointers.current.has(e.pointerId)){if(!gesture.current)setHover(p.x>=left&&p.x<=right&&p.y>=top&&p.y<=bottom?p:null);return}
  pointers.current.set(e.pointerId,p);
  const g=gesture.current;if(!g)return;
  const f=g.frame,w=f.right-f.left,h=f.bottom-f.top,span=f.max-f.min;
  if(g.kind==='pinch'){
   const points=[...pointers.current.values()];if(points.length<2)return;
   const center=midpoint(points[0],points[1]),factor=clamp(g.distance/Math.max(1,distance(points[0],points[1])),.1,10);
   const count=clamp(f.count*factor,8,600),newSpan=clamp(span*factor,Math.max(Math.abs(f.max)*1e-10,1e-10),1e15);
   const time=f.start+(g.origin.x-f.left)/w*f.count*f.interval,price=f.max-(g.origin.y-f.top)/h*span;
   const newMax=price+(center.y-f.top)/h*newSpan;
   commit({start:time-(center.x-f.left)/w*count*f.interval,count,prices:{min:newMax-newSpan,max:newMax}});
  }else if(g.kind==='price')zoomPrice(f,Math.exp(clamp((p.y-g.origin.y)/h*2,-4,4)),(f.top+f.bottom)/2);
  else if(g.kind==='time')zoomTime(f,Math.exp(clamp(-(p.x-g.origin.x)/w*2,-4,4)),(f.left+f.right)/2);
  else{
   const shift=(p.y-g.origin.y)/h*span;
   commit({start:f.start-(p.x-g.origin.x)/w*f.count*f.interval,count:f.count,prices:{min:f.min+shift,max:f.max+shift}});
  }
 };
 const releasePointer=(id:number)=>{
  if(!pointers.current.delete(id))return;
  if(svg.current?.hasPointerCapture(id))svg.current.releasePointerCapture(id);
  rebaseGesture();
 };
 const wheelHandler=useRef<(e:WheelEvent)=>void>(()=>{});
 wheelHandler.current=(e:WheelEvent)=>{
  // A non-passive listener is needed so the page stays still while zooming.
  e.preventDefault();if(gesture.current)return;
  const f=frameRef.current,p=point(e.clientX,e.clientY),unit=e.deltaMode===1?16:e.deltaMode===2?f.bottom-f.top:1;
  const delta=(e.deltaY||e.deltaX)*unit,factor=Math.exp(clamp(delta*.0015,-.7,.7));
  setHover(null);
  if(e.shiftKey)commit({start:f.start+delta/(f.right-f.left)*f.count*f.interval,count:f.count,prices:viewRef.current.prices});
  else if(p.x>=f.right)zoomPrice(f,factor,p.y);
  else if(e.ctrlKey){
   const ratio=clamp((p.y-f.top)/(f.bottom-f.top),0,1),price=f.max-ratio*(f.max-f.min),span=clamp((f.max-f.min)*factor,Math.max(Math.abs(price)*1e-10,1e-10),1e15);
   zoomTime(f,factor,p.x,{min:price+ratio*span-span,max:price+ratio*span});
  }else zoomTime(f,factor,p.x);
 };
 const hasData=data.length>0;
 useEffect(()=>{
  if(!hasData){gesture.current=null;pointers.current.clear();setDragging(false);setHover(null)}
 },[hasData]);
 useEffect(()=>{
  const el=svg.current;if(!el)return;
  const handler=(e:WheelEvent)=>wheelHandler.current(e);el.addEventListener('wheel',handler,{passive:false});
  return()=>el.removeEventListener('wheel',handler);
 },[hasData]);
 const selected=hover?rows.find(r=>Math.abs(x(r.openTime)-hover.x)<=step*.6):rows[rows.length-1];
 const lastY=last?y(last.close):0,showLast=!!last&&lastY>=top&&lastY<=bottom;
 const moveTime=(bars:number)=>{const f=frameRef.current;commit({start:f.start+bars*f.interval,count:f.count,prices:viewRef.current.prices});setHover(null)};
 const buttonClass='min-h-9 min-w-9 sm:min-h-0 sm:min-w-0 rounded border border-[#414651] px-2 py-1 hover:bg-[#252a35] disabled:opacity-40';
 return <div className="min-w-0 bg-[#131722] text-[#b2b5be]" aria-label="Gráfico de velas do ativo selecionado">
  <div className="flex flex-wrap items-center justify-between gap-2 px-2 py-1 text-[10px] font-mono">
   <span>{selected?`O ${fmtPrice(selected.open)} H ${fmtPrice(selected.high)} L ${fmtPrice(selected.low)} C ${fmtPrice(selected.close)}`:'Sem vela nesta posição'}</span>
   <div className="flex flex-wrap gap-1">
    <button className={buttonClass} disabled={!hasData} aria-label="Ver velas anteriores" onClick={()=>moveTime(-10)}>←</button>
    <button className={buttonClass} disabled={!hasData} aria-label="Ver velas seguintes" onClick={()=>moveTime(10)}>→</button>
    <button className={buttonClass} disabled={!hasData} aria-label="Ampliar gráfico" onClick={()=>zoomTime(frameRef.current,.75,(left+right)/2)}>+</button>
    <button className={buttonClass} disabled={!hasData} aria-label="Reduzir gráfico" onClick={()=>zoomTime(frameRef.current,1.25,(left+right)/2)}>−</button>
    <button className={buttonClass} disabled={!hasData} aria-label="Ajustar escala de preços" title="Ajustar preços às velas visíveis" onClick={()=>commit({...viewRef.current,prices:null})}>Escala</button>
    <button className={buttonClass} onClick={reset}>Atual</button>
   </div>
  </div>
  <div ref={plotHost} className="market-chart-host">
  {!hasData?<div className="flex h-full items-center justify-center text-sm">{loading?'Carregando gráfico...':'Gráfico indisponível.'}</div>:
  <svg ref={svg} width="100%" height={height} viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
   className={`select-none outline-offset-[-2px] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#9598a1] ${dragging?'cursor-grabbing':'cursor-grab'}`}
   style={{touchAction:'none'}} aria-label="Velas com escala de preços e horários de Brasília"
   data-testid="market-chart" data-interval-ms={interval} data-time-start={start} data-visible-bars={view.count} data-price-min={min} data-price-max={max}
   data-chart-mode={view.start===null?'latest':'manual'} data-price-mode={view.prices?'manual':'auto'} data-last-time={last?.openTime}
   onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={e=>releasePointer(e.pointerId)}
   onPointerCancel={e=>releasePointer(e.pointerId)} onLostPointerCapture={e=>releasePointer(e.pointerId)} onPointerLeave={()=>setHover(null)}
   onBlur={clearGesture} onDoubleClick={reset}
   onKeyDown={e=>{
    const f=frameRef.current;
    if(e.key==='Home'){e.preventDefault();reset()}
    else if(e.key==='Escape')clearGesture();
    else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();moveTime((e.key==='ArrowLeft'?-1:1)*f.count*.12)}
    else if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();const shift=(e.key==='ArrowUp'?1:-1)*(f.max-f.min)*.12;commit({start:f.start,count:f.count,prices:{min:f.min+shift,max:f.max+shift}})}
    else if(e.key==='+'||e.key==='='||e.key==='-'){e.preventDefault();zoomTime(f,e.key==='-'?1.25:.75,(f.left+f.right)/2)}
   }}>
   <title>Arraste em qualquer direção. Roda ou pinça para zoom. Arraste a escala direita para ajustar preços. Teclado: setas, +, − e Home.</title>
   <defs><clipPath id={clipId}><rect x={left} y={top} width={plotWidth} height={plotHeight}/></clipPath></defs>
   {[0,1,2,3,4].map(i=>{const price=max-(max-min)*i/4,yy=y(price);return <g key={i}><line x1={left} x2={right} y1={yy} y2={yy} stroke="#252a35"/><text x={right+6} y={yy+4} fontSize="10" fill="#b2b5be">{fmtPrice(price)}</text></g>})}
   {[0,1,2].map(i=>{const xx=left+plotWidth*i/2,time=start+(view.count*i/2-.5)*interval;return <g key={i}><line x1={xx} x2={xx} y1={top} y2={bottom} stroke="#252a35"/><text x={Math.max(30,Math.min(right-22,xx))} y={height-7} textAnchor="middle" fontSize="10" fill="#b2b5be">{fmtHM(time)}</text></g>})}
   <g clipPath={`url(#${clipId})`}>
    {annotations&&<g pointerEvents="none" opacity={annotations.cancelled ? .4 : 1} data-testid="signal-regions">
     {annotations.zones.filter(zone=>zone.lower<max&&zone.upper>min&&zone.end_at*1000>=start&&zone.start_at*1000<end).map(zone=>{
      const x1=clamp(left+(zone.start_at*1000-start)/interval*step,left,right),x2=clamp(left+(zone.end_at*1000-start)/interval*step,left,right);
      const y1=y(Math.min(max,zone.upper)),y2=y(Math.max(min,zone.lower)),color=zone.id==='fvg'?'#a78bfa':annotations.direction==='CALL'?'#26a69a':'#ef5350';
      return <g key={zone.id} data-zone-id={zone.id}><rect x={x1} y={y1} width={Math.max(0,x2-x1)} height={Math.max(0,y2-y1)} fill={color} fillOpacity=".12" stroke={color} strokeOpacity=".45"/><text x={x1+5} y={Math.min(bottom-3,y1+12)} fontSize="10" fill={color}>{zone.label}</text></g>;
     })}
    </g>}
    {rows.map(r=>{const xx=x(r.openTime),color=r.close>=r.open?'#26a69a':'#ef5350';return <g key={r.openTime} data-candle-time={r.openTime}><line x1={xx} x2={xx} y1={y(r.high)} y2={y(r.low)} stroke={color}/><rect x={xx-step*.3} y={y(Math.max(r.open,r.close))} width={Math.max(.8,step*.6)} height={Math.max(1,Math.abs(y(r.open)-y(r.close)))} fill={color}/></g>})}
    {annotations&&<g pointerEvents="none" data-testid="signal-markings" data-signal-id={annotations.signal_id} opacity={annotations.cancelled ? .4 : 1}>
     {annotations.levels.filter(level=>Number.isFinite(level.price)&&level.price>=min&&level.price<=max).map(level=><g key={level.id} data-level-id={level.id} data-level-price={level.price}><line x1={left} x2={right} y1={y(level.price)} y2={y(level.price)} stroke={level.selected?'#fbbf24':'#a78bfa'} strokeWidth={level.selected?1.4:.8} strokeDasharray={level.selected?'6 3':'3 5'}/><text x={left+6} y={Math.max(top+12,y(level.price)-5)} fontSize="10" fill={level.selected?'#fbbf24':'#a78bfa'}>{level.label} · {fmtPrice(level.price)}</text></g>)}
     {[{key:'entry',at:annotations.entry_at,label:'Entrada',color:'#00d4ef',offset:8},{key:'expiry',at:annotations.expiry_at,label:'Expiração',color:'#a78bfa',offset:23}].map(marker=>{
      const xx=left+(marker.at*1000-start)/interval*step;
      if(xx<left||xx>right)return null;
      return <g key={marker.key} data-marker-kind={marker.key} data-marker-time={marker.at} data-marker-x={xx}><line x1={xx} x2={xx} y1={top} y2={bottom} stroke={marker.color} strokeDasharray="5 4"/><text x={clamp(xx+5,left+5,right-104)} y={bottom-marker.offset} fill={marker.color} fontSize="10">{marker.label} {fmtHM(marker.at*1000)}</text></g>;
     })}
    </g>}
    {showLast&&<line x1={left} x2={right} y1={lastY} y2={lastY} stroke={last.close>=last.open?'#26a69a':'#ef5350'} strokeDasharray="3 3"/>}
    {hover&&<g><line x1={hover.x} x2={hover.x} y1={top} y2={bottom} stroke="#9598a1" strokeDasharray="4 4"/><line x1={left} x2={right} y1={hover.y} y2={hover.y} stroke="#9598a1" strokeDasharray="4 4"/></g>}
   </g>
   {showLast&&<g><rect x={right} y={lastY-9} width="76" height="18" fill={last.close>=last.open?'#26a69a':'#ef5350'}/><text x={right+4} y={lastY+4} fill="white" fontSize="10">{fmtPrice(last.close)}</text></g>}
   {!rows.length&&<text x={(left+right)/2} y={(top+bottom)/2} textAnchor="middle" fontSize="11" fill="#b2b5be">Fora das velas carregadas. Clique em Atual.</text>}
  </svg>}
  </div>
  <div className="px-2 text-[10px]">Arraste para mover · Roda/pinça para zoom</div>
  {annotations&&hasData&&<p className="px-2 text-[10px] text-[#fbbf24]" data-testid="signal-chart-legend">{annotations.cancelled?'Sinal cancelado · ':''}Entrada planejada {fmtHM(annotations.entry_at*1000)} · Expiração {fmtHM(annotations.expiry_at*1000)} · Níveis originais da decisão</p>}
  <div className="px-2 pb-1 text-[10px]">{source} · Brasília (UTC−3) · M{interval/60000} · {view.start===null?'Últimas velas':'Navegação manual'}{loading?' · Atualizando…':''}</div>
 </div>;
}
