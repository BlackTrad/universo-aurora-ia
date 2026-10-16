import {createContext,useContext,useEffect,useState,useCallback,useRef} from 'react';
export async function api(path:string,body?:unknown,method?:string,signal?:AbortSignal){
 const timeout=path.startsWith('/quote?')||path.startsWith('/chart?')||path==='/connection/check'?30000:15000;
 // Use the broadly supported controller API. Static AbortSignal helpers are
 // absent in some browsers/WebViews; one missing helper must not stop the app.
 const controller=new AbortController(),hasBody=body!==undefined;
 const abort=()=>controller.abort(signal?.reason);
 if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
 const timer=setTimeout(()=>controller.abort(),timeout);
 try{
  const response=await fetch('/api'+path,{method:method||(hasBody?'POST':'GET'),headers:hasBody?{'Content-Type':'application/json'}:undefined,body:hasBody?JSON.stringify(body):undefined,signal:controller.signal});
  if(!response.ok){let message='Falha na consulta ('+response.status+')';try{const d=await response.json();message=typeof d.detail==='string'?d.detail:message;}catch{}throw Error(message);}return await response.json();
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
const Context=createContext<any>(null);
export function DataProvider({children}:any){const [snapshot,setSnapshot]=useState<any>(null),[error,setError]=useState(''),[connected,setConnected]=useState(false),[now,setNow]=useState(Date.now());
 const [chartStatus,setChartStatus]=useState<any>(null);
 const reportChartStatus=useCallback((value:any)=>setChartStatus(value),[]);
 const [connectionError,setConnectionError]=useState('');
 const sequence=useRef(0),applied=useRef(0),mounted=useRef(true);
 const refresh=useCallback(async()=>{const request=++sequence.current;try{const s=await api('/state');if(mounted.current&&request>applied.current){applied.current=request;setSnapshot({...s,received:Date.now()});setConnected(true);setConnectionError('');}return s;}catch(e:any){if(mounted.current&&request>applied.current){applied.current=request;setConnectionError(e.message);setConnected(false);}}},[]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{let stop=false;let timer:any;async function loop(){await refresh();if(!stop)timer=setTimeout(loop,3000);}loop();return()=>{stop=true;clearTimeout(timer)}},[refresh]);
 useEffect(()=>{const timer=setInterval(()=>setNow(snapshot?snapshot.server_time*1000+(Date.now()-snapshot.received):Date.now()),1000);return()=>clearInterval(timer)},[snapshot]);
 return <Context.Provider value={{snapshot,error:connectionError||error,setError,connected,now,refresh,chartStatus,reportChartStatus}}>{children}</Context.Provider>;
}
export const useData=()=>useContext(Context);
export const mapSignal=(s:any)=>({...s,entry_time:new Date(s.entry_at*1000).toISOString(),expiry_time:new Date(s.expiry_at*1000).toISOString(),timeframe:s.timeframe,expiration_minutes:s.duration_seconds/60,strategy:s.strategy_resolved||s.strategy,entry_price:s.entry_price??null,exit_price:s.expiry_price??null,result:s.state==='cancelado'?'CANCELADO':s.state==='encerrado'?(s.settlement_pending?'APURANDO':(s.result||'INDISPONÍVEL')):'PENDING'});
