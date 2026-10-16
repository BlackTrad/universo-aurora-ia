// Contratos de tempo, resolução, pausa e liberação de recursos, sem navegador.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ts=require('../frontend-src/node_modules/typescript');
const base=path.join(__dirname,'../frontend-src/src/lib/space');
const cache=new Map();
function load(name,globals={}){
 const filename=path.join(base,name+'.ts');
 const key=filename+Object.keys(globals).join(',');if(cache.has(key))return cache.get(key);
 const source=fs.readFileSync(filename,'utf8'),output={};
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 new Function('exports','require',...Object.keys(globals),code)(output,p=>load(p.replace('./',''),globals),...Object.values(globals));
 cache.set(key,output);return output;
}
const {FrameClock,resolution,UNIVERSE}=load('config');
for(const rate of [60,90,120,144,240]){
 const clock=new FrameClock();let paints=0,elapsed=0;
 for(let i=0;i<=rate*2;i++){const r=clock.advance(i*1000/rate);if(r.paint){paints++;elapsed+=r.dt;}}
 assert.equal(paints,120,rate+' Hz');assert(Math.abs(clock.time-2)<1e-10);assert(Math.abs(elapsed-2)<1e-10);
 clock.reset();assert.equal(clock.advance(80000).paint,false);assert(Math.abs(clock.time-2)<1e-10);
 clock.advance(80000+1000/60);assert(clock.time<2.02,'Resume never includes time spent paused.');
}
for(const [w,h,m] of [[3840,2160,false],[1440,900,false],[390,844,true],[320,568,true],[1,1,false]]){
 for(const level of [0,1,2]){
  const r=resolution(w,h,3,m,level),limit=(m?UNIVERSE.mobilePixels:UNIVERSE.maxPixels)*.67**level;
  assert(r.width>=1&&r.height>=1);assert(r.width*r.height<=limit+Math.max(w,h)*2);
  assert(r.ratio<=(m?1.25:2));
 }
}
class Target {
 constructor(){this.events=new Map();this.attributes={};this.style={};this.dataset={};}
 addEventListener(n,fn){if(!this.events.has(n))this.events.set(n,new Set());this.events.get(n).add(fn);}
 removeEventListener(n,fn){this.events.get(n)?.delete(fn);}
 emit(n,e={}){for(const fn of [...this.events.get(n)||[]])fn(e);}
 setAttribute(n,v){this.attributes[n]=v;}
 get listeners(){return [...this.events.values()].reduce((n,s)=>n+s.size,0);}
}
function environment({reduced=false,mobile=false,compileFailure=false}={}){
 const resources=new Set(),calls=[];let next=0,layoutReads=0;
 const constants=new Map();
 const gl=new Proxy({}, {get(_,key){
  if(key===key.toUpperCase()){if(!constants.has(key))constants.set(key,constants.size+1);return constants.get(key);}
  if(key.startsWith('create'))return ()=>{const resource={kind:key,id:++next};resources.add(resource);return resource;};
  if(key.startsWith('delete'))return r=>resources.delete(r);
  if(key==='getShaderParameter')return ()=>!compileFailure;
  if(key==='getProgramParameter')return ()=>true;
  if(key==='getUniformLocation')return (_,n)=>({name:n});
  if(key==='checkFramebufferStatus')return ()=>gl.FRAMEBUFFER_COMPLETE;
  if(key==='isContextLost')return ()=>false;
  return (...args)=>{calls.push({name:key,args});};
 }});
 const root=new Target();Object.assign(root,{clientWidth:mobile?390:1440,clientHeight:mobile?844:900,children:[],appendChild(c){this.children.push(c);c.parent=this;}});
 const header={getBoundingClientRect(){layoutReads++;return {bottom:mobile?178:86};}};
 const doc=new Target();Object.assign(doc,{hidden:false,querySelector:()=>header,createElement:()=>{const canvas=new Target();canvas.getContext=()=>gl;canvas.remove=()=>{root.children=root.children.filter(c=>c!==canvas);};return canvas;}});root.ownerDocument=doc;
 const media=new Target(),coarse=new Target();media.matches=reduced;coarse.matches=mobile;
 const win=new Target();Object.assign(win,{innerWidth:root.clientWidth,innerHeight:root.clientHeight,devicePixelRatio:3,matchMedia:q=>q.includes('reduced')?media:coarse});
 const frames=new Map();let id=0,now=0;
 const globals={window:win,document:doc,requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id)};
 // Each environment needs a fresh closure for its window/document.
 cache.clear();const {mountWebGLUniverse}=load('webgl',globals);
 const states=[];let failures=0;
 let scene;
 if(compileFailure){assert.throws(()=>mountWebGLUniverse(root,{onFailure(){failures++;}}));}
 else scene=mountWebGLUniverse(root,{onFailure(){failures++;},onMotionChange:s=>states.push(s)});
 return {root,win,doc,media,coarse,frames,scene,resources,calls,states,get failures(){return failures;},get layoutReads(){return layoutReads;},advance(ms=1000/60){now+=ms;const run=[...frames.values()];frames.clear();run.forEach(fn=>fn(now));}};
}
for(const mobile of [false,true]){
 const e=environment({mobile}),canvas=e.root.children[0];assert(canvas);assert.equal(e.frames.size,1);
 assert(canvas.width*canvas.height<=(mobile?UNIVERSE.mobilePixels:UNIVERSE.maxPixels)+4000);
 const layout=e.layoutReads;
 for(let i=0;i<145;i++)e.advance(1000/144);
 assert.equal(e.layoutReads,layout,'Animation does not read DOM geometry each frame.');
 assert.equal(e.frames.size,1);e.scene.setPaused(true);assert.equal(e.frames.size,0);assert(e.scene.paused);
 e.scene.setPaused(false);assert.equal(e.frames.size,1);
 e.doc.hidden=true;e.doc.emit('visibilitychange');assert.equal(e.frames.size,0);
 e.doc.hidden=false;e.doc.emit('visibilitychange');assert.equal(e.frames.size,1);
 e.media.matches=true;e.media.emit('change');assert.equal(e.frames.size,0);assert(e.scene.reduced);
 e.root.clientWidth=640;e.root.clientHeight=480;e.win.emit('resize');assert(canvas.width>0);
 e.media.matches=false;e.media.emit('change');assert.equal(e.frames.size,1);
 e.win.emit('pagehide',{persisted:true});assert.equal(e.frames.size,0);assert.equal(e.root.children.length,1);
 e.win.emit('pageshow',{});assert.equal(e.frames.size,1);
 e.scene.destroy();e.scene.destroy();assert.equal(e.resources.size,0);assert.equal(e.root.children.length,0);
 assert.equal(e.frames.size,0);assert.equal(e.win.listeners+e.doc.listeners+e.media.listeners,0);
}
const quiet=environment({reduced:true});assert.equal(quiet.frames.size,0);quiet.scene.destroy();assert.equal(quiet.resources.size,0);
const failure=environment({compileFailure:true});assert.equal(failure.resources.size,0);assert.equal(failure.root.children.length,0);
const lost=environment();let prevented=false;
lost.root.children[0].emit('webglcontextlost',{preventDefault(){prevented=true;}});
assert(prevented);assert.equal(lost.failures,1);assert.equal(lost.resources.size,0);assert.equal(lost.frames.size,0);lost.scene.destroy();
console.log('Universe: frame timing at 60/90/120/144/240 Hz, pixel budgets, desktop/mobile lifecycle, pause/reduced motion, visibility, bfcache, context loss and all GPU resources released.');
// A troca de motor preserva a pausa, inclusive quando o contexto some em uso.
const adapterCode=ts.transpileModule(fs.readFileSync(path.join(base,'universe.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
for(const startFailure of [false,true]){
 const root={dataset:{},childElementCount:0};let fail,canvasStarts=0,gpuDestroyed=0,canvasDestroyed=0;
 const states=[],out={};
 new Function('exports','require',adapterCode)(out,name=>name==='./webgl'?{mountWebGLUniverse(_,options){
  if(startFailure)throw Error('Controlled unsupported GPU');
  fail=options.onFailure;let paused=false;root.childElementCount=1;
  return {get paused(){return paused;},get reduced(){return false;},setPaused(v){paused=v;},destroy(){gpuDestroyed++;root.childElementCount=0;}};
 }}:{mountCosmos(_,options){
  canvasStarts++;root.childElementCount=5;let paused=false;
  return {get paused(){return paused;},get reduced(){return false;},setPaused(v){paused=v;options.onMotionChange({paused,reduced:false});},destroy(){canvasDestroyed++;root.childElementCount=0;}};
 }});
 const controller=out.mountUniverse(root,s=>states.push(s));controller.setPaused(true);
 if(!startFailure)fail();
 assert.equal(root.dataset.renderer,'canvas');assert.equal(canvasStarts,1);assert(controller.paused);assert.equal(states.at(-1).paused,true);
 controller.destroy();assert.equal(canvasDestroyed,1);assert.equal(root.childElementCount,0);
 if(!startFailure){assert.equal(gpuDestroyed,1);fail();assert.equal(canvasStarts,1,'A destroyed scene never restarts the fallback.');}
}
console.log('Universe fallback: unsupported GPU/context loss preserve pause; teardown never restarts another engine.');
