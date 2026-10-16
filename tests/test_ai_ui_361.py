"""Render the actual planet view without a browser, network or market calls."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_RENDER = r"""
const fs=require('node:fs'),path=require('node:path');
const root=process.cwd(),modules=path.join(root,'frontend-src/node_modules');
const ts=require(path.join(modules,'typescript'));
const React=require(path.join(modules,'react'));
const server=require(path.join(modules,'react-dom/server'));
const input=JSON.parse(fs.readFileSync(0,'utf8'));
let hookIndex=0;
function compile(file){
 const source=fs.readFileSync(path.join(root,'frontend-src/src',file),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,
  target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const output={};new Function('require','exports',code)(localRequire,output);return output;
}
function localRequire(name){
 if(name==='@/components/AppShell')return {AppShell:({children})=>React.createElement('main',null,children)};
 if(name==='@/lib/api')return {useData:()=>input,mapSignal:compile('lib/api.tsx').mapSignal};
 if(name==='@/lib/apex')return compile('lib/apex.ts');
 if(name==='react')return input.hookStates?{...React,useState:(initial)=>[input.hookStates[hookIndex++]??initial,()=>{}]}:React;
 if(name==='react/jsx-runtime')return require(path.join(modules,'react/jsx-runtime'));
 throw Error('Unexpected dependency: '+name);
}
const output=compile('routes/'+(input.route||'universo-ia')+'.tsx');
process.stdout.write(server.renderToStaticMarkup(React.createElement(output.default)));
"""

NODE_CALENDAR = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ts=require(path.join(process.cwd(),'frontend-src/node_modules/typescript'));
const source=fs.readFileSync('frontend-src/src/routes/calendario.tsx','utf8');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,
 target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const hooks=[],requests=[],timers=new Map();let cursor=0,timerId=0,effects=[];
const same=(a,b)=>a&&b&&a.length===b.length&&a.every((value,index)=>Object.is(value,b[index]));
const React={
 useState(initial){const index=cursor++;if(!hooks[index])hooks[index]={value:typeof initial==='function'?initial():initial};
  return [hooks[index].value,value=>{hooks[index].value=typeof value==='function'?value(hooks[index].value):value}];},
 useRef(initial){const index=cursor++;if(!hooks[index])hooks[index]={current:initial};return hooks[index];},
 useMemo(compute){cursor++;return compute();},
 useEffect(effect,deps){const index=cursor++;if(!same(hooks[index]?.deps,deps))effects.push(()=>{
   hooks[index]?.cleanup?.();hooks[index]={deps,cleanup:effect()};});},
};
const jsx=(type,props)=>({type,props});
function localRequire(name){
 if(name==='react')return React;
 if(name==='react/jsx-runtime')return {jsx,jsxs:jsx};
 if(name==='@/components/AppShell')return {AppShell:({children})=>children};
 if(name==='@/lib/api')return {api:(url,body,method,signal)=>new Promise((resolve,reject)=>requests.push({url,signal,resolve,reject}))};
 throw Error('Unexpected dependency: '+name);
}
global.setTimeout=(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;};
global.clearTimeout=id=>timers.delete(id);
const output={};new Function('require','exports',code)(localRequire,output);
function render(){cursor=0;effects=[];const tree=output.default();for(const effect of effects)effect();return tree;}
function flatten(tree){if(tree==null||typeof tree==='boolean')return '';if(Array.isArray(tree))return tree.map(flatten).join('');
 if(typeof tree!=='object')return String(tree);return flatten(tree.props?.children);}
function buttons(tree,result=[]){if(Array.isArray(tree)){for(const item of tree)buttons(item,result);}
 else if(tree&&typeof tree==='object'){if(tree.type==='button')result.push(tree);buttons(tree.props?.children,result);}return result;}
const flush=async()=>{for(let index=0;index<8;index++)await Promise.resolve();};
function finish(start,wins){requests[start].resolve({total:wins+3,wins,losses:3,ties:0,pending:0,unavailable:0,win_rate_pct:50});
 requests[start+1].resolve({items:[]});requests[start+2].resolve({days:[]});}
(async()=>{
 let tree=render();assert.equal(requests.length,3);assert.equal(timers.size,0);
 requests[0].resolve({total:4,wins:1,losses:3});await flush();
 assert.equal(timers.size,0,'a slow request must finish before another poll is scheduled');
 requests[1].resolve({items:[]});requests[2].resolve({days:[]});await flush();
 assert.equal(timers.size,1);assert.equal([...timers.values()][0].delay,5000);
 tree=render();const timer=[...timers.entries()][0];timers.delete(timer[0]);timer[1].callback();
 assert.equal(requests.length,6);assert.equal(timers.size,0);
 const selected=buttons(tree).filter(button=>String(button.props['aria-label']||'').match(/^\d{4}-\d{2}-\d{2}/));
 const initialDay=new URL('http://local'+requests[0].url).searchParams.get('day');
 const other=selected.find(button=>!button.props['aria-label'].startsWith(initialDay));assert(other);
 other.props.onClick();tree=render();assert.equal(requests.length,9);
 assert(requests[3].signal.aborted,'changing day must abort the previous poll');
 finish(3,999);await flush();tree=render();
 assert(!flatten(tree).includes('999'),'late response from another day overwrote current selection');
 assert.equal(timers.size,0,'an obsolete poll must not reschedule itself');
 finish(6,7);await flush();tree=render();assert(flatten(tree).includes('WIN: 7'));
 assert.equal(timers.size,1);
 for(const hook of hooks)hook?.cleanup?.();
 assert.equal(timers.size,0);assert(requests[6].signal.aborted);
 console.log('Calendar polls refresh settled results, wait for all requests, reject stale days and clean up.');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


def render(ai, *, connected=True):
    if not shutil.which('node') or not (ROOT/'frontend-src/node_modules/typescript').is_dir():
        pytest.skip('Node and frontend development dependencies are required for view rendering.')
    completed=subprocess.run(['node','-e',NODE_RENDER],cwd=ROOT,
        input=json.dumps({'snapshot':{'ai':ai},'connected':connected}),
        text=True,capture_output=True,timeout=20,check=True)
    return completed.stdout


def completed_state():
    return {
        'status':'approved','detail':'Consenso concluído.','updated_at':1792065600,
        'context':{'ativo':'BTC/USD','timeframe':'M5','fonte':'deriv'},
        'last_decision':{'direcao':'CALL','confianca':84},
        'last_votes':[{'agente':'Groq','status':'disponivel','direcao':'CALL',
                      'confianca':84,'dados_insuficientes':False,
                      'justificativa':'Voto exclusivo da rodada anterior.'}],
        'agents':[{'agente':'Groq','model':'configured-model','status':'analisando',
                   'detail':'Análise em andamento.'}],
    }


def test_processing_hides_old_direction_and_vote_even_if_snapshot_retains_them():
    state=completed_state()
    state['status']='processing'
    state['context']={'ativo':'ETH/USD','timeframe':'M1','fonte':'deriv'}
    html=render(state)
    assert 'ANALISANDO' in html
    assert 'ETH/USD' in html and 'BTC/USD' not in html
    assert 'Voto exclusivo da rodada anterior.' not in html
    assert '>CALL<' not in html and '84%' not in html
    assert 'Aguardando o voto desta rodada.' in html


def test_disconnected_view_never_presents_old_approval_as_current():
    html=render(completed_state(),connected=False)
    assert 'SEM CONEXÃO' in html
    assert 'APROVADO' not in html and '>CALL<' not in html
    assert 'Última rodada recebida' in html
    assert 'Voto exclusivo da rodada anterior.' not in html


def test_completed_vote_retains_its_instrument_and_brasilia_timestamp():
    state=completed_state()
    state['updated_at']=1792065600  # 15/10/2026 12:00 UTC = 09:00 Brasília.
    html=render(state)
    assert 'APROVADO' in html and '>CALL<' in html
    assert 'BTC/USD' in html and 'M5' in html and 'deriv' in html
    assert '09:00:00' in html and 'Brasília' in html
    assert 'Voto exclusivo da rodada anterior.' in html


def test_provider_ready_without_a_vote_is_not_reported_as_unavailable():
    state=completed_state()
    state['status']='ready'
    state['last_votes']=[]
    state['last_decision']=None
    state['agents'][0]['status']='disponivel'
    html=render(state)
    assert '>PRONTO<' in html
    assert '>CALL<' not in html


def test_invalid_timestamp_does_not_crash_the_page():
    state=completed_state()
    state['updated_at']='invalid'
    html=render(state)
    assert 'APROVADO' in html
    assert 'Invalid Date' not in html


def test_unavailable_filter_uses_original_result_and_legacy_missing_result():
    if not shutil.which('node') or not (ROOT/'frontend-src/node_modules/typescript').is_dir():
        pytest.skip('Node and frontend dependencies are required.')
    records=[{'id':str(index),'asset':asset,'state':'encerrado',
              'entry_at':1792065600,'expiry_at':1792065660,'timeframe':'M1',
              'duration_seconds':60,'direction':'CALL','result':result}
             for index,(asset,result) in enumerate([
                 ('BTC/USD','Resultado indisponível'),('ETH/USD',None),('SOL/USD','WIN')])]
    completed=subprocess.run(['node','-e',NODE_RENDER],cwd=ROOT,
        input=json.dumps({'route':'sinais','snapshot':{'signals':records},
                          'connected':True,'hookStates':['','Resultado indisponível']}),
        text=True,capture_output=True,timeout=20,check=True)
    html=completed.stdout
    assert re.findall(r'<h2[^>]*>(.*?)</h2>',html)==['BTC/USD','ETH/USD']
    assert 'value="Resultado indisponível" selected=""' in html
    assert '>INDISPONÍVEL</option>' in html


def test_calendar_polling_lifecycle_avoids_overlap_and_stale_day_replies():
    if not shutil.which('node') or not (ROOT/'frontend-src/node_modules/typescript').is_dir():
        pytest.skip('Node and frontend dependencies are required.')
    subprocess.run(['node','-e',NODE_CALENDAR],cwd=ROOT,text=True,
                   capture_output=True,timeout=20,check=True)
