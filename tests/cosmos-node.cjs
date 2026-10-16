// Teste de ciclo de vida Canvas sem navegador ou dependências externas.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../espaco.html'), 'utf8');
assert(!/<script[^>]+src=/i.test(html));
assert(!/<link[^>]+(?:href|rel)=/i.test(html));
assert(!/\bfetch\s*\(|@import|url\(\s*['"]?https?:/i.test(html));
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

class Target {
  constructor(){this.events = new Map();this.attributes={};this.style={};}
  addEventListener(name,fn){if(!this.events.has(name))this.events.set(name,new Set());this.events.get(name).add(fn);}
  removeEventListener(name,fn){this.events.get(name)?.delete(fn);}
  emit(name,event={}){for(const fn of [...(this.events.get(name)||[])])fn(event);}
  setAttribute(key,value){this.attributes[key]=value;}
  get listeners(){return [...this.events.values()].reduce((n,v)=>n+v.size,0);}
}
class Canvas extends Target {
  constructor(){super();this.width=0;this.height=0;this.paints=0;this.clears=0;this.parent=null;
    this.context={setTransform(){},clearRect:()=>{this.clears++;},fillRect(){},beginPath(){},arc(){},fill(){},stroke(){},save(){},restore(){},translate(){},rotate(){},scale(){},moveTo(){},lineTo(){},fillText(){},drawImage(){},
      createRadialGradient(){return {addColorStop(){}};},createLinearGradient(){return {addColorStop(){}};},
      createImageData:(w,h)=>({width:w,height:h,data:new Uint8ClampedArray(w*h*4)}),putImageData:()=>{this.paints++;}};}
  getContext(){return this.context;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;}
}
function environment({reduced=false,mobile=false,canvasFailure=false}={}){
  const root=new Target();Object.assign(root,{clientWidth:mobile?390:1440,clientHeight:mobile?844:900,children:[],appendChild(c){this.children.push(c);c.parent=this;}});
  const elements={cosmos:root,motion:new Target(),'motion-status':new Target(),structure:new Target()};
  const document=new Target();Object.assign(document,{hidden:false,getElementById:id=>elements[id],createElement:()=>{const c=new Canvas();if(canvasFailure)c.getContext=()=>{throw Error('Controlled Canvas initialization failure');};return c;}});
  const media=new Target(),coarse=new Target();media.matches=reduced;coarse.matches=mobile;
  const window=new Target();Object.assign(window,{innerWidth:root.clientWidth,innerHeight:root.clientHeight,devicePixelRatio:3,matchMedia:q=>q.includes('reduced')?media:coarse});
  const frames=new Map();let id=0,now=0;
  const initialize=new Function('window','document','requestAnimationFrame','cancelAnimationFrame',script);
  initialize(window,document,fn=>{frames.set(++id,fn);return id;},n=>frames.delete(n));
  return {window,document,root,media,elements,frames,advance(ms=16.667){now+=ms;const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn(now));}};
}
for(const mobile of [false,true]){
  const e=environment({mobile});
  assert.equal(e.root.children.length,5);
  assert.equal(e.root.children[0].width,Math.round(e.root.clientWidth*(mobile?1.5:2)));
  assert.equal(e.frames.size,1);
  for(let i=0;i<80;i++)e.advance();
  assert.equal(e.frames.size,1);
  e.elements.motion.emit('click');
  assert.equal(e.frames.size,0);
  assert.equal(e.elements.motion.textContent,'Retomar movimento');
  e.elements.motion.emit('click');
  assert.equal(e.frames.size,1);
  e.document.hidden=true;e.document.emit('visibilitychange');assert.equal(e.frames.size,0);
  e.document.hidden=false;e.document.emit('visibilitychange');assert.equal(e.frames.size,1);
  e.media.matches=true;e.media.emit('change');assert.equal(e.frames.size,0);
  assert.equal(e.elements.motion.disabled,true);
  e.root.clientWidth=640;e.root.clientHeight=480;e.window.emit('resize');
  assert.equal(e.root.children[0].height,Math.round(480*(mobile?1.5:2)));
  e.media.matches=false;e.media.emit('change');assert.equal(e.frames.size,1);
  e.window.emit('pagehide',{persisted:true});assert.equal(e.root.children.length,5);
  e.window.emit('pagehide',{persisted:false});assert.equal(e.root.children.length,0);assert.equal(e.frames.size,0);
  assert.equal(e.media.listeners,0);assert.equal(e.document.listeners,0);
}
const reduced=environment({reduced:true});
assert.equal(reduced.root.children.length,5);assert.equal(reduced.frames.size,0);
assert.equal(reduced.elements.motion.textContent,'Movimento reduzido');
reduced.window.emit('pagehide',{});
const highRefresh=environment();
const before=highRefresh.root.children[0].clears;
for(let i=0;i<121;i++)highRefresh.advance(1000/120);
const draws=highRefresh.root.children[0].clears-before;
assert(draws>=58&&draws<=61,'120 Hz display must not double Canvas painting: '+draws);
highRefresh.window.emit('pagehide',{});
const failure=environment({canvasFailure:true});
assert.equal(failure.root.children.length,0);assert.equal(failure.frames.size,0);assert.equal(failure.elements.motion.disabled,true);
console.log('Canvas lifecycle: desktop/mobile, DPR, pause, visibility, reduced motion, resize, bfcache, 60 FPS painting cap on 120 Hz and safe failure passed.');
