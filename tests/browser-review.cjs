// Review regressions using synthetic local services only.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8118'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser,releaseOld;const failures=[];
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(t);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/news',r=>r.fulfill({json:{status:'unavailable',items:[],updated_at:null}}));
  await page.request.post('http://127.0.0.1:8118/_test/public_sources');
  // During initial state loading, clicking Verify must not dereference a null snapshot.
  let releaseState;await page.route('**/api/state',async r=>{await new Promise(resolve=>releaseState=resolve);await r.continue()});
  await page.goto('http://127.0.0.1:8118/conexoes');await page.getByRole('button',{name:'Verificar conexões',exact:true}).waitFor();
  try{assert(await page.getByRole('button',{name:'Verificar conexões',exact:true}).isDisabled());console.log('PASS source controls wait for initial state')}catch(e){failures.push('initial source controls: '+e.message)}
  releaseState();await page.unroute('**/api/state');await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='Verificar conexões'&&!b.disabled));
  // Hold a pre-confirmation report; a newer refresh must remain authoritative.
  const signal=(await(await page.request.post('http://127.0.0.1:8118/_test/emit_catalogue_signal?source=coinbase')).json()).signal;
  await page.request.post('http://127.0.0.1:8118/_test/resolve_catalogue_signal?sid='+signal.id);
  let first=true,held;const captured=new Promise(resolve=>held=resolve);
  await page.route('**/api/validation/summary',async r=>{if(!first)return r.continue();first=false;const data=await(await r.fetch()).json();held();await new Promise(resolve=>releaseOld=resolve);await r.fulfill({json:data})});
  await page.goto('http://127.0.0.1:8118/validacao?signal='+signal.id);await captured;
  await page.getByLabel('Corretora',{exact:true}).fill('TEST_ONLY');await page.getByRole('button',{name:'Registrar conferência',exact:true}).click();
  await page.getByText(/Execuções declaradas: 1/).waitFor();releaseOld();releaseOld=null;await page.waitForTimeout(500);
  try{assert(await page.getByText(/Execuções declaradas: 1/).count());console.log('PASS stale validation cannot overwrite a newer manual-confirmation report')}catch(e){failures.push('validation response race: '+e.message)}
  await page.unroute('**/api/validation/summary');
  // Synchronous submits bypass disabled-button timing and exercise the action latch.
  await page.goto('http://127.0.0.1:8118/conexoes');await page.getByLabel('Token',{exact:true}).fill('123:synthetic-review-key');await page.getByLabel('Chat ID',{exact:true}).fill('1');
  let writes=0;const writeSeen=page.waitForResponse(r=>r.url().endsWith('/api/credentials')&&r.request().method()==='PUT',{timeout:15000});
  await page.route('**/api/credentials',async r=>{if(r.request().method()!=='PUT')return r.continue();writes++;await new Promise(resolve=>setTimeout(resolve,300));await r.fulfill({json:{status:'saved',detail:'Controlled save'}});});
  await page.getByRole('heading',{name:'Telegram',exact:true}).evaluate(h=>{const f=h.closest('form');f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))});await writeSeen;await page.waitForTimeout(400);
  try{assert.equal(writes,1);console.log('PASS double submit issues one credential write')}catch(e){failures.push('duplicate credential write: '+e.message)}
  await page.unroute('**/api/credentials');
  if(failures.length)console.log('REPRODUCED',JSON.stringify(failures));
  // Existing chart navigation must reset on a new instrument, and survive same-instrument polls.
  await page.goto('http://127.0.0.1:8118');await page.getByRole('img',{name:'Velas com escala de preços e horários de Brasília'}).waitFor();
  await page.getByRole('button',{name:'Ampliar gráfico',exact:true}).click();await page.getByRole('button',{name:'Ver velas anteriores',exact:true}).click();
  const chart=page.getByLabel('Gráfico de velas do ativo selecionado',{exact:true});await chart.getByText(/Navegação manual$/).waitFor();
  await page.request.post('http://127.0.0.1:8118/_test/emit_catalogue_signal?source=binance');
  await page.waitForFunction(()=>document.querySelector('svg')?.parentElement.parentElement.textContent.includes('Binance')&&document.querySelector('svg')?.parentElement.parentElement.textContent.includes('Últimas velas'));
  assert(!await chart.getByText(/Navegação manual$/).count());console.log('PASS existing chart resets navigation on signal source/asset/timeframe change');
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'review-'+width+'.png'),fullPage:true})}
  assert.deepEqual(errors,[]);assert.deepEqual(failures,[]);console.log('PASS review regression browser, responsive and no JavaScript errors');
 }finally{if(releaseOld)releaseOld();if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
