// Controlled local signal and history; no real Telegram, Gemini or market request.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8123'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[],aiRequests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
  page.on('request',r=>{if(r.url().includes('generativelanguage.googleapis.com'))aiRequests.push(r.url())});
  await page.route('**/api/news',r=>r.fulfill({json:{status:'unavailable',items:[],updated_at:null}}));
  await page.request.post('http://127.0.0.1:8123/_test/public_sources');
  const signal=(await(await page.request.post('http://127.0.0.1:8123/_test/emit_catalogue_signal?source=coinbase')).json()).signal;
  assert.equal(signal.engine_explanation.source,'motor');const original=signal.engine_explanation;
  await page.goto('http://127.0.0.1:8123');await page.getByText('Explicação do motor',{exact:true}).click();
  await page.getByText(original.text,{exact:true}).waitFor();
  assert((await(await page.request.get('http://127.0.0.1:8123/api/state')).json()).scan.active===false);
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'engine-signal-'+width+'.png'),fullPage:true})}
  // External-looking text is plain React text, never HTML or scripts.
  const unsafe='TEST_ONLY <img src=x onerror="window.auroraUnsafe=1">';
  await page.route('**/api/state',async r=>{const d=await(await r.fetch()).json();for(const s of d.signals)s.engine_explanation={...original,text:unsafe};d.current_signal={...d.current_signal,engine_explanation:{...original,text:unsafe}};await r.fulfill({json:d})});
  await page.reload();await page.getByText('Explicação do motor',{exact:true}).click();await page.getByText(unsafe,{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.auroraUnsafe),undefined);await page.unroute('**/api/state');
  await page.goto('http://127.0.0.1:8123/conexoes');
  await page.getByText('Explicações dos sinais: motor técnico local. Sem chave ou consumo de IA.',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Chave Gemini',{exact:true}).count(),0);
  assert.equal(await page.getByRole('heading',{name:'Spectro Ai · Gemini',exact:true}).count(),0);
  await page.screenshot({path:path.join(out,'engine-connections-360.png'),fullPage:true});
  await page.request.post('http://127.0.0.1:8123/_test/resolve_catalogue_signal?sid='+signal.id);
  const saved=(await(await page.request.get('http://127.0.0.1:8123/api/signals/'+signal.id)).json());
  assert.deepEqual(saved.signal.engine_explanation,original);
  await page.goto('http://127.0.0.1:8123/historico');await page.getByRole('cell').filter({hasText:/^WIN/}).waitFor();
  assert.deepEqual(errors,[]);assert.deepEqual(aiRequests,[]);
  console.log('PASS engine explanation from stored decision, unchanged after outcome, no Gemini UI/requests, escaped text, desktop/mobile and no JavaScript errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
