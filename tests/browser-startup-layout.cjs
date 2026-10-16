// Controlled local service; synthetic data does not prove strategy efficacy.
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8117'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(t);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const context=await browser.newContext({viewport:{width:1920,height:1080}}),errors=[],searches=[];
  context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
  context.on('request',r=>{if(r.method()==='POST'&&/\/api\/(analyze|scan\/start)(\?|$)/.test(r.url()))searches.push(r.url())});
  const page=await context.newPage();
  await page.request.post('http://127.0.0.1:8117/_test/public_sources');
  const getState=async()=>await(await page.request.get('http://127.0.0.1:8117/api/state')).json();
  const quiet=async()=>{await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();await page.waitForResponse(r=>r.url().endsWith('/api/state'));await page.waitForResponse(r=>r.url().endsWith('/api/state'));const s=await getState();assert(!s.scan.active&&!s.busy);assert.equal(s.analyses.length,0);assert.equal(s.signals.length,0);assert.equal(searches.length,0)};
  await page.goto('http://127.0.0.1:8117');await quiet();
  await page.reload();await quiet();
  const second=await context.newPage();await second.goto('http://127.0.0.1:8117');await second.getByRole('button',{name:'Iniciar',exact:true}).waitFor();
  await second.goto('http://127.0.0.1:8117/conexoes');assert((await second.request.put('http://127.0.0.1:8117/api/connection',{data:{provider:'binance'}})).ok());
  await page.waitForFunction(async()=>(await(await fetch('/api/state')).json()).provider==='binance');await quiet();
  assert((await second.request.put('http://127.0.0.1:8117/api/connection',{data:{provider:'deriv'}})).ok());await page.waitForFunction(async()=>(await(await fetch('/api/state')).json()).provider==='deriv');await quiet();await second.close();
  for(const width of [360,390,768,1366,1920,2560]){
   await page.setViewportSize({width,height:width<1024?844:1080});
   await page.waitForFunction(()=>{const svg=document.querySelector('svg');if(!svg)return false;return Math.abs(svg.getBoundingClientRect().height-(innerWidth<1024?260:Math.max(300,Math.min(800,innerHeight-550))))<2});
   const main=await page.locator('main').boundingBox(),svg=await page.locator('svg').boundingBox();assert.equal(main.x,0);assert.equal(main.width,width);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(width>=1024){assert(svg.width>width*.6,'chart should expand with the page');const catalogue=await page.getByRole('region',{name:'Ativos disponíveis',exact:true}).boundingBox(),lastCard=await page.locator('.asset-grid > button').last().boundingBox();assert(Math.abs(lastCard.x+lastCard.width-(catalogue.x+catalogue.width))<2,'asset cards must fill their compact catalogue');}
   if(width===1920||width===390)await page.screenshot({path:path.join(out,width===1920?'dashboard-wide.png':'dashboard-mobile.png'),fullPage:true});
  }
  await page.setViewportSize({width:1366,height:1000});
  await page.getByRole('button',{name:'Analisar agora',exact:true}).click();
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/state')).json();return s.analyses.length===1&&!s.busy});
  assert.equal(searches.length,1);assert.equal((await getState()).signals.length,0,'explicit manual preview must not emit signals');
  await page.getByRole('button',{name:'Iniciar',exact:true}).click();await page.getByRole('button',{name:'Parar',exact:true}).waitFor();
  await page.waitForFunction(async()=>(await(await fetch('/api/state')).json()).signals.length===2);
  await page.getByRole('button',{name:'Parar',exact:true}).click();await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();
  let s=await getState(),count=s.analyses.length;assert.equal(s.signals.length,2);assert.equal(searches.length,2);
  await page.reload();await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();await page.waitForResponse(r=>r.url().endsWith('/api/state'));
  s=await getState();assert.equal(s.analyses.length,count);assert.equal(searches.length,2);assert(!s.scan.active);
  await page.request.post('http://127.0.0.1:8117/_test/expire');await page.waitForFunction(async()=>(await(await fetch('/api/state')).json()).result_totals.wins===2);
  for(const route of ['/historico','/validacao','/conexoes','/noticias','/configuracoes','/aprendizado']){
   await page.goto('http://127.0.0.1:8117'+route);await page.locator('main').waitFor();const main=await page.locator('main').boundingBox();assert.equal(main.width,1366);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  assert.deepEqual(errors,[]);
  console.log('PASS no automatic analysis on launch/reload/new tab/provider change or after Stop; explicit preview/start work; pending settlement preserved; full-width layout and chart resize at 360/390/768/1366/1920/2560px; no JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
