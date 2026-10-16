// Software checks only: a perfect tiny sample must not certify performance.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_accuracy_server:app','--host','127.0.0.1','--port','8126'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.request.post('http://127.0.0.1:8126/_test/seed_accuracy');
  await page.goto('http://127.0.0.1:8126/validacao');await page.getByRole('tab',{name:'Experimentos',exact:true}).click();
  await page.getByText('Sobre a observação',{exact:true}).click();
  await page.getByText(/Price Action em M1\/M5: pullback estrutural/).waitFor();
  await page.getByText(/No terceiro toque, a própria vela fecha na marcação/).waitFor();
  await page.getByText('Sobre a observação',{exact:true}).click();
  await page.getByText('Avaliar a meta de 90%',{exact:true}).click();
  const board=page.getByTestId('accuracy-target'),rows=board.getByTestId('target-row');
  await rows.first().getByText('n=10',{exact:true}).waitFor();
  assert((await rows.first().innerText()).includes('100%'));assert((await rows.first().innerText()).includes('Coleta em andamento'));
  assert(!(await rows.first().innerText()).includes('Critérios desta fase atendidos'));
  await rows.first().getByText('Conferir critérios',{exact:true}).click();await rows.first().getByText('○ 100 WIN/LOSS',{exact:true}).waitFor();
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:950});await board.scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await board.screenshot({path:path.join(out,'accuracy-target-'+width+'.png')})}
  await board.getByLabel('Fase da meta',{exact:true}).selectOption('test');await board.getByText(/Teste final reservado\. Abra o relatório/).waitFor();assert.equal(await rows.count(),0);
  await board.getByLabel('Fase da meta',{exact:true}).selectOption('validation');await rows.first().getByText('n=10',{exact:true}).waitFor();
  let held,release;const captured=new Promise(r=>held=r),gate=new Promise(r=>release=r);
  const slow=async route=>{const url=new URL(route.request().url());if(url.searchParams.get('phase')==='development'){held();await gate;try{await route.continue()}catch{}}else await route.continue()};
  await page.route('**/api/validation/target?*',slow);await board.getByLabel('Fase da meta').selectOption('development');await captured;
  await board.getByLabel('Fase da meta').selectOption('validation');await rows.first().getByText('n=10',{exact:true}).waitFor();release();await page.unroute('**/api/validation/target?*',slow);
  assert((await board.innerText()).includes('Coleta em andamento'));
  const fail=route=>route.fulfill({status:503,json:{detail:'Avaliação temporariamente indisponível'}});
  await page.route('**/api/validation/target?*',fail);await board.getByLabel('Seletor da meta').selectOption('regime_router');await board.getByRole('alert').waitFor();assert.equal(await rows.count(),0);
  await page.unroute('**/api/validation/target?*',fail);await board.getByLabel('Seletor da meta').selectOption('');await rows.first().getByText('n=10',{exact:true}).waitFor();
  const state=await(await page.request.get('http://127.0.0.1:8126/api/state')).json();assert.equal(state.scan.active,false);assert.equal(state.signals.length,0);assert.deepEqual(errors,[]);
  console.log('PASS accuracy target tiny sample not certified, final test sealed, phase and request isolation, API failure, no monitoring or emitted signals, desktop/mobile and no JavaScript errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
