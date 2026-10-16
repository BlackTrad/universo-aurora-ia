// UI fixtures only. No real market prices, trades, Telegram messages or rate claims.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),url='http://127.0.0.1:8140';
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_12610_server:app','--host','127.0.0.1','--port','8140'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code))});server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'America/Sao_Paulo'}),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(scan\/start|analyze|scan\/stop)$/.test(r.url()))posts.push(r.url())});
  await page.request.post(url+'/_test/public_sources');await page.request.post(url+'/_test/seed_fibonacci');
  await page.goto(url);await page.getByText('Nova oportunidade identificada',{exact:true}).waitFor();
  await page.getByRole('status',{name:'Estado do sinal',exact:true}).filter({hasText:'Aguardando confirmação'}).waitFor();
  await page.getByLabel('Versão do projeto').filter({hasText:'1.26.13'}).waitFor();
  const chart=page.getByTestId('market-chart');await page.locator('[data-marker-kind="entry"]').waitFor();await page.locator('[data-marker-kind="expiry"]').waitFor();
  assert.equal(await chart.locator('[data-level-id]').count(),3);
  assert.equal(await page.getByTestId('signal-telegram-status').innerText(),'Telegram: Enviado');
  assert.equal(await page.locator('.signal-card [data-testid="observed-rate"] b').innerText(),'Histórico insuficiente');
  assert((await page.getByRole('status',{name:'Disponibilidade da fonte de dados'}).innerText()).includes('Atualizado'));
  assert.equal(await page.getByTestId('monitoring-status').innerText(),'Monitoramento parado');
  const state=async()=>await(await page.request.get(url+'/api/state')).json();const before=(await state()).settings;
  const start=+(await chart.getAttribute('data-time-start')),count=+(await chart.getAttribute('data-visible-bars'));
  const marker=page.locator('[data-marker-kind="entry"]'),at=+(await marker.getAttribute('data-marker-time'));
  const expected=8+(at*1000-start)/300000*((await chart.boundingBox()).width-84)/count;
  assert(Math.abs(+(await marker.getAttribute('data-marker-x'))-expected)<1,'entry marker must use candle opening, not centre');
  await page.getByLabel('Marcações do sinal',{exact:true}).uncheck();assert.equal(await page.getByTestId('signal-markings').count(),0);
  await page.getByLabel('Marcações do sinal',{exact:true}).check();await marker.waitFor();
  await page.getByRole('button',{name:'M1',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-testid="market-chart"]')?.dataset.intervalMs==='60000');assert.equal(await page.getByTestId('signal-markings').count(),0);
  await page.getByRole('button',{name:'M5',exact:true}).click();await marker.waitFor();
  await page.getByRole('button',{name:'Ampliar gráfico',exact:true}).click();const manual=await chart.getAttribute('data-time-start');
  await page.request.post(url+'/_test/signal_stage?stage=confirmed');await page.getByRole('status',{name:'Estado do sinal'}).filter({hasText:'Entrada confirmada'}).waitFor();assert.equal(await chart.getAttribute('data-time-start'),manual);
  for(const [stage,label] of [['revalidating','Revalidando entrada'],['late','Envio atrasado · não entrar'],['apuring','Apurando resultado']]){
   await page.request.post(url+'/_test/signal_stage?stage='+stage);await page.getByRole('status',{name:'Estado do sinal'}).filter({hasText:label}).waitFor();
  }
  await page.request.post(url+'/_test/signal_stage?stage=cancelled');await page.getByText('Entrada cancelada',{exact:true}).waitFor();await page.getByText('Região original invalidada · teste sintético',{exact:true}).waitFor();
  await page.request.post(url+'/_test/signal_stage?stage=pending');await page.getByRole('status',{name:'Estado do sinal'}).filter({hasText:'Aguardando confirmação'}).waitFor();await page.getByRole('button',{name:'Atual',exact:true}).click();
  for(const width of [360,390,768,1440]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'dashboard-12610-'+width+'.png'),fullPage:true})}
  const catalogue=page.getByRole('region',{name:'Ativos disponíveis',exact:true});await catalogue.getByLabel('Pesquisar ativo').fill('BTC/USD');await catalogue.locator('[data-instrument-id="coinbase:BTC/USD"]').click();await page.locator('.chart-card h2').filter({hasText:'BTC/USD'}).waitFor();assert.equal(await page.getByTestId('signal-markings').count(),0);
  assert.deepEqual((await state()).settings,before);
  await page.request.post(url+'/_test/seed_review_history');await page.getByRole('link',{name:'Aprendizado',exact:true}).click();
  const review=page.getByRole('region',{name:'Avaliação das estratégias',exact:true});await review.getByText(/Fibonacci considera/).waitFor();
  await review.getByLabel('Nível Fibonacci da avaliação').selectOption('50');await review.getByText('18 WIN · 12 LOSS · 0 empate',{exact:true}).waitFor();
  assert.equal(await review.locator('details').filter({hasText:'18 WIN · 12 LOSS · 0 empate'}).getByTestId('observed-rate').locator('b').innerText(),'60%');
  await review.getByLabel('Nível Fibonacci da avaliação').selectOption('61.8');await review.getByText('Histórico insuficiente',{exact:true}).waitFor();
  await review.getByText('Seleção do motor · critérios do manual',{exact:true}).click();await review.getByText(/Sem coleta comparável/).waitFor();
  for(const width of [360,1440]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'learning-12610-'+width+'.png'),fullPage:true})}
  assert.deepEqual(errors,[]);assert.equal(posts.length,0);assert.equal((await state()).scan.active,false);
  console.log('PASS frozen Fibonacci levels and temporal markers, source/timeframe isolation, status lifecycle, original historical rate, header version/data/monitoring, manual chart retained, descriptive level-separated metrics, comparator cold start, responsive desktop and no automatic analysis or JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
