// Chart navigation with controlled candles; never contacts a market or Telegram.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..');
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8121'],{cwd:root,stdio:['ignore','ignore','pipe']});
 let browser;const errors=[];
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timeout')),15000);server.once('exit',code=>{clearTimeout(t);reject(Error('server exited '+code))});server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(t);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8121');
  const chart=page.getByRole('img',{name:'Velas com escala de preços e horários de Brasília'});await chart.waitFor();await chart.scrollIntoViewIfNeeded();
  const drag=async(dx,dy)=>{const b=await chart.boundingBox(),x=b.x+b.width*.5,y=b.y+b.height*.45;await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+dx,y+dy,{steps:8});await page.mouse.up()};
  if(process.env.AURORA_CHART_BASELINE==='1'){
   const before=await chart.locator('g rect').first().getAttribute('y');await drag(50,30);const after=await chart.locator('g rect').first().getAttribute('y');
   console.log('BASELINE',JSON.stringify({before,after,dragChangedCandles:before!==after}));await page.screenshot({path:path.join(out,'chart-before.png')});assert.notEqual(before,after,'Dragging does not move candles vertically');return;
  }
  const view=()=>chart.evaluate(el=>({start:+el.dataset.timeStart,bars:+el.dataset.visibleBars,min:+el.dataset.priceMin,max:+el.dataset.priceMax,mode:el.dataset.chartMode,priceMode:el.dataset.priceMode}));
  const reset=()=>page.getByRole('button',{name:'Atual',exact:true}).click();
  let v=await view();assert.equal(v.mode,'latest');const initial=v;
  for(const [dx,dy] of [[65,0],[-65,0],[0,30],[0,-30],[55,25]]){
   await reset();const before=await view();await drag(dx,dy);const after=await view();
   assert.equal(after.mode,'manual');if(dx)assert(Math.abs(after.start-before.start)>1000,'Horizontal drag did not pan time');if(dy)assert(Math.abs(after.min-before.min)>0.00001,'Vertical drag did not pan prices');assert.equal(after.bars,before.bars);
  }
  console.log('PASS drag left, right, up, down and diagonal');
  const resizeView=await view();await page.setViewportSize({width:390,height:844});assert.deepEqual(await view(),resizeView);await page.setViewportSize({width:1366,height:1000});assert.deepEqual(await view(),resizeView);console.log('PASS resize preserves manual time/price range');
  const moved=await view();await page.waitForTimeout(1700);assert.deepEqual(await view(),moved,'Dashboard polling reset a manual viewport');
  let requests=0;const now=Math.floor(Date.now()/60000)*60-60;
  const data={asset:'EUR/USD',source_key:'deriv',source:'TESTE SINTÉTICO',timeframe:'M1',status:'live',data_at:now,received_at:now+60,price:105,candles:Array.from({length:70},(_,i)=>({t:now-(69-i)*60,o:100+i*.07,h:101+i*.07,l:99+i*.07,c:100.3+i*.07}))};
  await page.route('**/api/chart?*',async r=>{requests++;if(requests===1)return r.fulfill({status:503,json:{detail:'Falha controlada do gráfico'}});await r.fulfill({json:data})});
  // A failed poll preserves data/navigation, then a later successful poll recovers.
  await page.waitForResponse(r=>r.url().includes('/api/chart?')&&r.status()===503,{timeout:18000});
  await page.getByText(/Falha controlada do gráfico/).waitFor();assert.deepEqual(await view(),moved);console.log('PASS failed poll preserves chart and releases loading state');
  await page.waitForResponse(r=>r.url().includes('/api/chart?')&&r.status()===200,{timeout:18000});
  await page.waitForFunction(last=>document.querySelector('[data-testid="market-chart"]')?.getAttribute('data-last-time')===last,String(now*1000));
  assert(requests>=2);assert.deepEqual(await view(),moved,'Updated candle window reset manual navigation');console.log('PASS new candles preserve absolute time/price viewport');
  await reset();v=await view();assert.equal(v.mode,'latest');assert.equal(v.priceMode,'auto');assert.equal(v.bars,60);
  const b=await chart.boundingBox();const mouseX=Math.round(b.x+b.width*.42);await page.mouse.move(mouseX,Math.round(b.y+b.height*.45));
  await page.mouse.wheel(0,-240);await page.waitForTimeout(80);const zoomed=await view();assert(zoomed.bars<v.bars);assert.equal(zoomed.mode,'manual');
  // Zoom keeps the time under the cursor fixed.
  const width=b.width-84,anchorRatio=(mouseX-b.x-8)/width;
  console.log('ZOOM_ANCHOR',JSON.stringify({b,mouseX,before:v,after:zoomed,errorMs:(v.start+anchorRatio*v.bars*60000)-(zoomed.start+anchorRatio*zoomed.bars*60000)}));
  assert(Math.abs((v.start+anchorRatio*v.bars*60000)-(zoomed.start+anchorRatio*zoomed.bars*60000))<2);
  await page.mouse.wheel(0,240);await page.waitForTimeout(80);assert((await view()).bars>zoomed.bars);
  const pBefore=await view();await page.mouse.move(b.x+b.width-25,b.y+b.height*.45);await page.mouse.wheel(0,-160);await page.waitForTimeout(80);const pAfter=await view();assert(pAfter.max-pAfter.min<pBefore.max-pBefore.min);assert.equal(pAfter.bars,pBefore.bars);assert.equal(pAfter.priceMode,'manual');
  await page.mouse.down();await page.mouse.move(b.x+b.width-25,b.y+b.height*.45+35,{steps:5});await page.mouse.up();const scaled=await view();assert.notEqual(scaled.max-scaled.min,pAfter.max-pAfter.min);
  await page.getByRole('button',{name:'Ajustar escala de preços',exact:true}).click();assert.equal((await view()).priceMode,'auto');
  console.log('PASS anchored mouse zoom and independent price-axis scale');
  await chart.focus();const keyStart=await view();await page.keyboard.press('ArrowLeft');assert((await view()).start<keyStart.start);await page.keyboard.press('ArrowUp');assert.equal((await view()).priceMode,'manual');await page.keyboard.press('Home');assert.equal((await view()).mode,'latest');
  await page.getByRole('button',{name:'Ver velas anteriores',exact:true}).click();assert.equal((await view()).mode,'manual');await page.getByRole('button',{name:'Ver velas seguintes',exact:true}).click();await reset();
  // Pointer capture continues a drag outside the SVG and releases at mouse-up.
  await page.mouse.move(b.x+b.width*.4,b.y+b.height*.5);await page.mouse.down();await page.mouse.move(b.x-20,b.y+b.height*.5,{steps:8});await page.mouse.up();const outside=await view();await page.mouse.move(b.x+b.width*.4,b.y+b.height*.5);assert.deepEqual(await view(),outside);await reset();
  console.log('PASS keyboard, navigation controls and pointer capture outside the plot');
  await chart.evaluate(el=>el.addEventListener('pointerdown',e=>el.dataset.testPointerId=String(e.pointerId),{once:true}));
  await page.mouse.move(b.x+b.width*.4,b.y+b.height*.4);await page.mouse.down();await page.mouse.move(b.x+b.width*.4+20,b.y+b.height*.4+15);
  const cancelled=await view(),pointerId=+(await chart.getAttribute('data-test-pointer-id'));await chart.dispatchEvent('pointercancel',{pointerId,pointerType:'mouse'});
  await page.mouse.move(b.x+b.width*.4+50,b.y+b.height*.4+35);await page.mouse.up();assert.deepEqual(await view(),cancelled);
  await chart.dblclick({position:{x:b.width*.5,y:b.height*.5}});assert.equal((await view()).mode,'latest');
  await page.mouse.move(b.x+b.width*.4,b.y+b.height*.4);await page.mouse.down();await page.mouse.move(b.x+b.width*.4+20,b.y+b.height*.4+15);const blurred=await view();
  await page.getByRole('button',{name:'Atual',exact:true}).focus();await page.mouse.move(b.x+b.width*.4+70,b.y+b.height*.4+50);await page.mouse.up();assert.deepEqual(await view(),blurred);await reset();
  console.log('PASS pointer cancellation, focus loss and double-click reset');
  // Pan beyond loaded candles: show the limit instead of fabricating history.
  await chart.focus();for(let i=0;i<12;i++)await page.keyboard.press('ArrowLeft');await page.getByText('Fora das velas carregadas. Clique em Atual.',{exact:true}).waitFor();assert.equal(await chart.locator('[data-candle-time]').count(),0);await reset();
  await page.screenshot({path:path.join(out,'chart-desktop.png')});
  // Touch events through Chromium's input protocol, with real pointer capture/pinch.
  const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const touchPage=await mobile.newPage();touchPage.on('pageerror',e=>errors.push(e.message));await touchPage.route('**/api/chart?*',r=>r.fulfill({json:data}));await touchPage.goto('http://127.0.0.1:8121');
  const touchChart=touchPage.getByRole('img',{name:'Velas com escala de preços e horários de Brasília'});await touchChart.waitFor();await touchChart.scrollIntoViewIfNeeded();
  const touchView=()=>touchChart.evaluate(el=>({start:+el.dataset.timeStart,bars:+el.dataset.visibleBars,min:+el.dataset.priceMin,max:+el.dataset.priceMax,mode:el.dataset.chartMode}));
  const session=await mobile.newCDPSession(touchPage),tb=await touchChart.boundingBox();let tv=await touchView();const sy=tb.y+tb.height*.48;
  await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{id:1,x:tb.x+130,y:sy}]});
  await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{id:1,x:tb.x+185,y:sy+30}]});
  await touchPage.waitForFunction(previous=>+document.querySelector('[data-testid="market-chart"]').dataset.timeStart!==previous,tv.start);
  await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});let ta=await touchView();assert(ta.start!==tv.start&&ta.min!==tv.min);assert.equal(ta.mode,'manual');
  const coords=[{id:1,x:tb.x+105,y:sy-35},{id:2,x:tb.x+225,y:sy+35}];tv=await touchView();
  await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:coords});
  await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{id:1,x:tb.x+80,y:sy-50},{id:2,x:tb.x+250,y:sy+50}]});
  await touchPage.waitForFunction(previous=>+document.querySelector('[data-testid="market-chart"]').dataset.visibleBars<previous,tv.bars);
  ta=await touchView();assert(ta.bars<tv.bars);assert(ta.max-ta.min<tv.max-tv.min);await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  // High-frequency handoff: native active pointers, then controlled events in
  // one task so the second finger releases before React's next paint.
  const fastBefore=await touchView();await touchChart.evaluate(el=>{window._chartTouchIds=[];el.addEventListener('pointerdown',e=>{if(e.pointerType==='touch')window._chartTouchIds.push(e.pointerId)})});
  await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:coords});const ids=await touchPage.evaluate(()=>window._chartTouchIds);assert.equal(ids.length,2);
  await touchChart.evaluate((el,{ids,tb,sy})=>{
   const send=(type,id,x,y)=>el.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',clientX:x,clientY:y,bubbles:true,button:0,buttons:type==='pointerup'?0:1}));
   send('pointermove',ids[1],tb.x+250,sy+50);send('pointermove',ids[0],tb.x+80,sy-50);send('pointerup',ids[1],tb.x+250,sy+50);send('pointermove',ids[0],tb.x+105,sy-35);
  },{ids,tb,sy});
  await touchPage.waitForFunction(previous=>+document.querySelector('[data-testid="market-chart"]').dataset.visibleBars<previous,fastBefore.bars);
  const handoff=await touchView();assert(handoff.max-handoff.min<fastBefore.max-fastBefore.min);await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  console.log('PASS rapid two-to-one finger handoff preserves zoom before next paint');
  const stopped=await touchView();await touchChart.dispatchEvent('pointermove',{pointerId:1,pointerType:'touch',clientX:tb.x+40,clientY:sy});assert.deepEqual(await touchView(),stopped,'Touch gesture remained stuck');
  console.log('PASS mobile touch pan, two-finger pinch and release');
  for(const width of [390,360]){await touchPage.setViewportSize({width,height:844});assert(await touchPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await touchPage.screenshot({path:path.join(out,'chart-mobile-'+width+'.png')})}
  await mobile.close();await page.unroute('**/api/chart?*');
  for(const [tf,interval] of [['M2',120000],['M3',180000],['M5',300000],['M1',60000]]){
   const loaded=page.waitForResponse(r=>r.url().includes('/api/chart?')&&new URL(r.url()).searchParams.get('timeframe')===tf&&r.status()===200);await page.getByRole('button',{name:tf,exact:true}).click();await loaded;await chart.waitFor();
   await page.waitForFunction(()=>document.querySelector('[data-testid="market-chart"]')?.dataset.chartMode==='latest');const vf=await view(),last=+(await chart.getAttribute('data-last-time'));assert.equal(vf.priceMode,'auto');assert.equal(vf.start,last-(vf.bars-3)*interval);
   const times=await chart.locator('[data-candle-time]').evaluateAll(nodes=>nodes.map(e=>+e.dataset.candleTime));assert(times.length>2);assert.equal(times[1]-times[0],interval);
  }
  console.log('PASS M1/M2/M3/M5 spacing and reset after timeframe change');assert.deepEqual(errors,[]);console.log('PASS chart regression: manual navigation, polling, mouse/touch zoom, keyboard, no invented candles, responsive layout and no JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
