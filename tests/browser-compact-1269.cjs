// Local synthetic providers only. These checks do not validate trading efficacy.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),url='http://127.0.0.1:8138';
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8138'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser,release;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code))});server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const context=await browser.newContext({viewport:{width:1440,height:950}}),page=await context.newPage(),errors=[],posts=[],chartRequests=[];
  page.on('pageerror',e=>errors.push(e.message));context.on('request',r=>{if(r.method()==='POST'&&/\/api\/(analyze|scan\/start|scan\/stop)$/.test(r.url()))posts.push(r.url());if(r.url().includes('/api/chart?'))chartRequests.push(new URL(r.url()))});
  const state=async()=>await(await page.request.get(url+'/api/state')).json();
  await page.request.post(url+'/_test/public_sources');await page.goto(url);
  await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();const chart=page.getByTestId('market-chart');await chart.waitFor();
  assert.equal(await page.getByLabel('Período da análise',{exact:true}).count(),0);
  assert.equal(await page.getByLabel('Aprendizado por histórico',{exact:true}).count(),0);
  assert.equal(await page.getByTestId('winning-setups').count(),0);
  assert.equal(posts.length,0);assert.equal((await state()).analyses.length,0);
  const catalogue=page.getByRole('region',{name:'Ativos disponíveis',exact:true});await catalogue.locator('.asset-card').first().waitFor();
  const before=(await state()).settings;
  const assertPeriod=async(tf,interval)=>{
   const loaded=page.waitForResponse(r=>r.url().includes('/api/chart?')&&new URL(r.url()).searchParams.get('timeframe')===tf&&r.status()===200);
   await page.getByRole('button',{name:tf,exact:true}).click();await loaded;
   await page.waitForFunction(ms=>document.querySelector('[data-testid="market-chart"]')?.dataset.intervalMs===String(ms),interval);
   const times=await chart.locator('[data-candle-time]').evaluateAll(rows=>rows.map(el=>+el.dataset.candleTime));assert(times.length>2);assert.equal(times[1]-times[0],interval);
   assert.equal(await chart.getAttribute('data-chart-mode'),'latest');assert.equal(await chart.getAttribute('data-price-mode'),'auto');
  };
  await page.getByRole('button',{name:'Ampliar gráfico',exact:true}).click();assert.equal(await chart.getAttribute('data-chart-mode'),'manual');
  await assertPeriod('M5',300000);await assertPeriod('M1',60000);assert.deepEqual((await state()).settings,before,'visual chart timeframe changed analysis settings');
  // A delayed M5 response cannot replace a newer M1 viewport or price.
  let captured;const held=new Promise(resolve=>captured=resolve),gate=new Promise(resolve=>release=resolve);
  const slow=async route=>{const tf=new URL(route.request().url()).searchParams.get('timeframe');if(tf!=='M5')return route.continue();const data=await(await route.fetch()).json();captured();await gate;try{await route.fulfill({json:{...data,price:999999}})}catch{}};
  await page.route('**/api/chart?*',slow);await page.getByRole('button',{name:'M5',exact:true}).click();await held;await assertPeriod('M1',60000);release();release=null;await page.unroute('**/api/chart?*',slow);
  assert(!await page.locator('.chart-card h2').innerText().then(t=>t.includes('999999')));assert.equal(await chart.getAttribute('data-interval-ms'),'60000');
  const negative=async route=>{const d=await(await route.fetch()).json();await route.fulfill({json:{...d,change_pct:-0.5}})};
  await page.route('**/api/chart?*',negative);await assertPeriod('M5',300000);assert((await page.locator('.chart-card h2 span').getAttribute('class')).includes('text-bear'));await page.unroute('**/api/chart?*',negative);
  // Same currencies on different providers remain distinct and never modify analysis.
  for(const [source,symbol] of [['binance','BTC/USDT'],['coinbase','BTC/USD'],['deriv','EUR/USD']]){
   await catalogue.getByLabel('Pesquisar ativo').fill(symbol);
   const instrument=catalogue.locator('[data-instrument-id="'+source+':'+symbol+'"]');await instrument.waitFor();
   const loaded=page.waitForResponse(r=>r.url().includes('/api/chart?')&&new URL(r.url()).searchParams.get('source')===source&&new URL(r.url()).searchParams.get('asset')===symbol&&r.status()===200);await instrument.click();await loaded;await chart.waitFor();
   await assertPeriod('M5',300000);await assertPeriod('M1',60000);
  }
  assert.deepEqual((await state()).settings,before);assert.equal(posts.length,0);
  // Compact content, source colours and responsive graph all fit their container.
  for(const width of [360,390,768,1440,1920]){
   await page.setViewportSize({width,height:width<1024?844:950});await chart.waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const card=await catalogue.locator('.asset-card').first().boundingBox();assert(card.height<=100,'asset card is no longer compact');
   await page.screenshot({path:path.join(out,'compact-dashboard-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:950});
  await page.getByRole('link',{name:'Configurações',exact:true}).click();await page.getByRole('heading',{name:'Configurações de análise',exact:true}).waitFor();
  await page.getByLabel('Período da análise',{exact:true}).selectOption('M1');await page.getByRole('status').filter({hasText:'Configurações salvas.'}).waitFor();
  await page.getByLabel('Sinais por hora',{exact:true}).fill('8');await page.getByRole('button',{name:'Salvar limites',exact:true}).dblclick();
  await page.waitForFunction(async()=>(await(await fetch('/api/state')).json()).settings.hourly_limit===8);
  await page.reload();await page.getByLabel('Período da análise',{exact:true}).waitFor();assert.equal(await page.getByLabel('Período da análise',{exact:true}).inputValue(),'M1');assert.equal(await page.getByLabel('Sinais por hora',{exact:true}).inputValue(),'8');
  await page.getByText('Valores e payout (opcional)',{exact:true}).click();await page.getByLabel('Entrada (R$)').fill('10');await page.getByLabel('Payout (%)').fill('80');await page.getByRole('button',{name:'Salvar valores',exact:true}).click();await page.waitForFunction(async()=>(await(await fetch('/api/state')).json()).settings.payout_pct===80);
  await page.getByText('Prévia por ativo · detalhes da última análise',{exact:true}).click();await page.getByRole('region',{name:'Prévia por ativo',exact:true}).getByRole('button',{name:'Ver GBP/USD no gráfico',exact:true}).click();
  await page.locator('.chart-card h2').filter({hasText:'GBP/USD'}).waitFor();assert.equal(await chart.getAttribute('data-interval-ms'),'60000');
  assert.equal(posts.length,0);
  await page.getByRole('link',{name:'Aprendizado',exact:true}).click();await page.getByRole('heading',{name:'Aprendizado dos sinais',exact:true}).waitFor();await page.getByTestId('winning-setups').waitFor();
  await page.getByLabel('Aprendizado por histórico',{exact:true}).locator('summary').click();await page.getByText(/Preferências e vetos exigem 60 resultados/).waitFor();
  for(const route of ['/configuracoes','/aprendizado']){await page.goto(url+route);for(const width of [360,390,1440]){await page.setViewportSize({width,height:950});await page.getByRole('heading',{level:1}).waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,route.slice(1)+'-'+width+'.png'),fullPage:true})}}
  assert.equal(posts.length,0);assert.equal((await state()).signals.length,0);
  await page.goto(url);await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();
  const fixture=(await(await page.request.post(url+'/_test/emit_catalogue_signal?source=coinbase&timeframe=M1')).json()).signal;
  await page.getByText('Nova oportunidade identificada',{exact:true}).waitFor();await page.locator('.chart-card h2').filter({hasText:'BTC/USD'}).waitFor();await page.waitForFunction(()=>document.querySelector('[data-testid="market-chart"]')?.dataset.intervalMs==='60000');
  assert.equal(fixture.duration_seconds,300,'fixture must distinguish M1 analysis from 5-minute expiry');await page.getByText('Expiração: 5 Minutos',{exact:true}).waitFor();
  const explanation=page.getByText('Explicação do motor',{exact:true});await explanation.click();await page.getByText(/Estratégia: CCI/).waitFor();
  await page.screenshot({path:path.join(out,'compact-active-signal.png'),fullPage:true});
  await page.getByRole('button',{name:'Iniciar',exact:true}).dblclick();await page.getByRole('button',{name:'Parar',exact:true}).waitFor();assert.equal(posts.length,1);
  await page.getByRole('link',{name:'Configurações',exact:true}).click();await page.getByLabel('Período da análise',{exact:true}).waitFor();assert(await page.getByLabel('Período da análise',{exact:true}).isDisabled());
  // Extra pages are reachable from the compact menu, including keyboard close.
  await page.locator('header summary').click();await page.locator('header').getByRole('link',{name:'Histórico',exact:true}).waitFor();await page.keyboard.press('Escape');assert.equal(await page.locator('header details').getAttribute('open'),null);
  await page.locator('header summary').click();await page.locator('header').getByRole('link',{name:'Histórico',exact:true}).click();await page.getByRole('heading',{name:'Histórico de operações',exact:true}).waitFor();
  await page.getByRole('link',{name:'Painel',exact:true}).click();await page.getByRole('button',{name:'Parar',exact:true}).click();await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();
  assert.deepEqual(errors,[]);assert.equal(posts.length,2);assert(!(await state()).scan.active);assert(chartRequests.length>10);
  console.log('PASS compact tabs, persisted settings, no automatic scans, compact colours/cards, M1/M5 for three providers, temporal spacing, stale response, price colour, manual chart controls, signal follow with independent expiry, motor context, desktop/mobile, locked settings, menu and no JS errors');
 }finally{if(release)release();if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
