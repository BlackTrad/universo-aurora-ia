"""Render real React components for both roles without external market access."""
import json
import pytest
from tests.test_frontend_compat_381 import node_check


@pytest.mark.parametrize('is_admin',[False,True])
def test_navigation_controls_and_history_match_authenticated_role(is_admin):
    script=r"""
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const modules=path.join(process.cwd(),'frontend-src/node_modules');
const ts=require(path.join(modules,'typescript')),React=require(path.join(modules,'react'));
const server=require(path.join(modules,'react-dom/server'));
global.location={search:'',pathname:'/bot'};
const isAdmin=ROLE;
const data={isAdmin,user:{name:'Pessoa de teste',role:isAdmin?'admin':'viewer'},connected:true,
 now:1792065600000,refresh:()=>{},setError:()=>{},snapshot:{version:'test',
 access:{public_access:true},provider:'deriv',settings:{assets:['EUR/USD'],timeframe:'M5',duration_seconds:300},
 scan:{active:false},signals:[],analyses:[],ai:{status:'ready',agents:[]}}};
function compile(file){const code=ts.transpileModule(fs.readFileSync('frontend-src/src/'+file,'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
 const out={};new Function('require','exports',code)(load,out);return out;}
function load(name){
 if(name==='react')return React;
 if(name==='react/jsx-runtime')return require(path.join(modules,'react/jsx-runtime'));
 if(name==='@/lib/api')return {useData:()=>data,api:async()=>({}),mapSignal:s=>s};
 if(name==='@/components/CosmicBackground')return {CosmicBackground:()=>null,useCosmicScene:()=>({root:{current:null},controls:null})};
 if(name==='@/components/AssetPreviews')return {matchingAnalysis:()=>null};
 if(name.startsWith('@/components/'))return compile(name.slice(2)+'.tsx');
 if(name.startsWith('@/lib/'))return compile(name.slice(2)+'.ts');
 throw Error('Unexpected dependency '+name);
}
function render(file,props={}){return server.renderToStaticMarkup(React.createElement(compile(file).default||compile(file)[path.basename(file,'.tsx')],props));}
const dashboard=render('routes/index.tsx');
assert.equal(dashboard.includes('Analisar agora'),isAdmin);
assert.equal(dashboard.includes('Definir monitorados'),isAdmin);
assert.equal(dashboard.includes('href="/configuracoes"'),isAdmin);
assert.equal(dashboard.includes('href="/conexoes"'),isAdmin);
assert.equal(dashboard.includes('href="/telegram"'),isAdmin);
assert(dashboard.includes('action="/api/auth/logout"'));
assert(dashboard.includes('href="/bot"'));
if(!isAdmin){assert(dashboard.includes('Aguardando uma oportunidade validada pelo bot.'));
 assert(dashboard.includes('O administrador gerencia o monitoramento.'));}
const history=render('routes/historico.tsx');
assert.equal(history.includes('P/L estimado'),isAdmin);
assert(!history.includes('undefined'));
assert(history.includes('history-table'));
"""
    node_check(script.replace('ROLE',json.dumps(is_admin)))
