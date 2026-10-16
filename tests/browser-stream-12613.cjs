// Real application/stream callback with synthetic prices; no market or Telegram.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),url='http://127.0.0.1:8150';
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation','1.26.13','screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_stream_server:app','--host','127.0.0.1','--port','8150'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup')),15000);server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code))});server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'America/Sao_Paulo'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const state=async()=>(await page.request.get(url+'/api/state')).json();
  await page.goto(url+'/configuracoes');await page.getByLabel('Versão do projeto').filter({hasText:'1.26.13'}).waitFor();
  await page.getByText('Estratégias do manual · Price Action para opções binárias',{exact:true}).click();
  await page.getByText(/As entradas durante a vela usam ticks contínuos/).waitFor();
  assert.equal((await(await page.request.get(url+'/_test/streams')).json()).opened,0);
  for(const width of [360,768,1440]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))}
  await page.goto(url);await page.getByRole('button',{name:'Iniciar',exact:true}).click();
  await page.waitForFunction(async()=>{const s=await(await fetch('/_test/streams')).json();return s.opened===1});
  assert.equal((await state()).signals.length,0);
  assert((await page.request.post(url+'/_test/stream_price?price=100.25')).ok());
  await page.getByText('Gatilho observado durante a vela · entrada programada sujeita à revalidação.',{exact:true}).waitFor();
  let s=(await state()).signals[0];assert(s.live_monitor_required&&s.manual_intrabar);
  assert.equal((await(await page.request.post(url+'/_test/confirm_entry')).json()).status,'confirmada');
  assert((await page.request.post(url+'/_test/stream_price?price=99.6')).ok());
  s=(await state()).signals[0];assert.equal(s.state,'cancelado');assert.match(s.cancellation_reason,/invalidação/);
  await page.getByRole('button',{name:'Parar',exact:true}).click();
  const stopped=await(await page.request.get(url+'/_test/streams')).json();assert.equal(stopped.closed,stopped.opened);
  assert.equal((await state()).scan.active,false);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(out,'AURORA-1.26.13-frontend.png'),fullPage:true});
  console.log('PASS native stream has no boot subscription; emits one real-pipeline fixture signal; price loss cancels confirmed entry; Stop closes connection; responsive layouts; no JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
