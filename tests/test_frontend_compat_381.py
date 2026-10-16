"""Execute real TSX/API modules in Node; does not replace browser validation."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def node_check(script):
    if not shutil.which('node') or not (ROOT/'frontend-src/node_modules/typescript').is_dir():
        pytest.skip('Node and frontend development dependencies are required.')
    subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                   text=True, check=True, timeout=20)


def test_api_works_without_static_abortsignal_helpers_and_cleans_up():
    node_check(r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ts=require(path.join(process.cwd(),'frontend-src/node_modules/typescript'));
const code=ts.transpileModule(fs.readFileSync('frontend-src/src/lib/api.tsx','utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
const out={};new Function('require','exports',code)(name=>name==='react'?{createContext:()=>({})}:{},out);
Object.defineProperty(AbortSignal,'any',{value:undefined,configurable:true});
Object.defineProperty(AbortSignal,'timeout',{value:undefined,configurable:true});
const timers=new Map();let next=0;
global.setTimeout=callback=>{const id=++next;timers.set(id,callback);return id};
global.clearTimeout=id=>timers.delete(id);
const untilAbort=signal=>new Promise((resolve,reject)=>{
 if(signal.aborted)reject(signal.reason);
 else signal.addEventListener('abort',()=>reject(signal.reason),{once:true});
});
(async()=>{
 global.fetch=async(url,options)=>{assert(!options.signal.aborted);assert.equal(options.method,'POST');
  assert.equal(options.body,'false');return {ok:true,json:async()=>({ready:true})}};
 assert.deepEqual(await out.api('/settings',false),{ready:true});assert.equal(timers.size,0);
 let activeSignal;
 global.fetch=async(url,options)=>{activeSignal=options.signal;return untilAbort(options.signal)};
 const controller=new AbortController();
 const pending=out.api('/state',undefined,undefined,controller.signal);
 controller.abort();await assert.rejects(pending,error=>error.name==='AbortError');
 assert(activeSignal.aborted);assert.equal(timers.size,0);
 const aborted=new AbortController();aborted.abort();
 await assert.rejects(out.api('/state',undefined,undefined,aborted.signal));assert.equal(timers.size,0);
 const timeout=out.api('/state');assert.equal(timers.size,1);
 [...timers.values()][0]();await assert.rejects(timeout);assert.equal(timers.size,0);
 global.fetch=async()=>({ok:false,status:429,json:async()=>({detail:'Aguarde o provedor.'})});
 await assert.rejects(out.api('/state'),/Aguarde o provedor/);assert.equal(timers.size,0);
 global.fetch=async()=>{throw Error('offline')};
 await assert.rejects(out.api('/state'),/offline/);assert.equal(timers.size,0);
 console.log('API success, abort, timeout, JSON errors and offline cleanup verified.');
})().catch(error=>{console.error(error);process.exitCode=1});
""")


@pytest.mark.parametrize('connected,status,visible', [
    (False,'approved',False),(True,'processing',False),(True,'approved',True)])
def test_dashboard_does_not_show_stale_consensus(connected,status,visible):
    script=r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const modules=path.join(process.cwd(),'frontend-src/node_modules');
const ts=require(path.join(modules,'typescript')),React=require(path.join(modules,'react'));
const server=require(path.join(modules,'react-dom/server'));
global.location={search:'',pathname:'/'};
const input=INPUT;
const data={connected:input.connected,now:1792065600000,setError:()=>{},refresh:()=>{},
 snapshot:{provider:'deriv',settings:{assets:['BTC/USD'],timeframe:'M5',duration_seconds:300},
 scan:{active:false},signals:[],ai:{status:input.status,detail:'Previous round',
 context:{ativo:'ETH/USD',timeframe:'M5',fonte:'deriv'},
 last_decision:{direcao:'CALL',confianca:87}}}};
function compile(file){const code=ts.transpileModule(fs.readFileSync('frontend-src/src/'+file,'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
 const out={};new Function('require','exports',code)(load,out);return out;}
function load(name){
 if(name==='react')return React;
 if(name==='react/jsx-runtime')return require(path.join(modules,'react/jsx-runtime'));
 if(name==='@/lib/api')return {useData:()=>data,mapSignal:s=>s};
 if(name==='@/components/AssetPreviews')return {matchingAnalysis:()=>null};
 if(name==='@/components/AppShell')return {AppShell:({children})=>React.createElement('main',null,children)};
 if(name.startsWith('@/components/'))return {[name.split('/').pop()]:()=>null};
 if(name.startsWith('@/lib/'))return compile(name.slice(2)+'.ts');
 throw Error('Unknown dependency '+name);
}
const html=server.renderToStaticMarkup(React.createElement(compile('routes/index.tsx').default));
assert.equal(html.includes('CALL'),input.visible,'stale decision direction in dashboard');
assert.equal(html.includes('87%'),input.visible,'stale decision confidence in dashboard');
if(!input.connected)assert(html.includes('Sem conexão'));
if(input.visible)assert(html.includes('ETH/USD'),'decision must identify its own analysis asset');
"""
    import json
    node_check(script.replace('INPUT',json.dumps({'connected':connected,'status':status,'visible':visible})))
