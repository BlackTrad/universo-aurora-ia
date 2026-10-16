// @ts-nocheck
// Motor Canvas independente. O mesmo código é incluído no HTML avulso.
// Ajuste estas constantes para personalizar densidade, movimento e cores.
export const COSMOS_CONFIG = Object.freeze({
  seed: 846219, targetFPS: 60, starCounts: [420, 190, 65], starSpeeds: [0.5, 1.5, 3.2],
  starColors: ['213,230,255', '171,221,255', '255,233,189'],
  maxDPR: 2, mobileDPR: 1.5, parallax: [5, 14, 26],
  nebulaSpeed: 0.006, galaxySpeed: 0.003, planetSpeed: 0.009,
  meteorMinSeconds: 8, meteorMaxSeconds: 20, dustCount: 48,
  cyan: '#79deee', gold: '#d9b875', planetRefresh: 1 / 24
});

export function mountCosmos(root, options = {}) {
  const C = COSMOS_CONFIG, TAU = Math.PI * 2;
  const media = window.matchMedia('(prefers-reduced-motion: reduce)');
  const coarse = window.matchMedia('(pointer: coarse)');
  let reduced = media.matches, paused = false, destroyed = false;
  let width = 1, height = 1, dpr = 1, frame = 0, previous = 0, time = 0, paintBudget = 0;
  let pointer = {x: 0, y: 0}, target = {x: 0, y: 0};
  let planet = {x: 0, y: 0, r: 1}, stars = [], meteors = [];
  let nextMeteor = C.meteorMinSeconds, planetAt = -1;
  let lines = options.constellations === true, quality = 1, slowFrames = 0;
  const removers = [], canvases = [], contexts = [];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const mix = (a, b, t) => a + (b - a) * t;
  function random(seed) {let s = seed >>> 0; return () => {s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296;};}
  const rng = random(C.seed);
  function listen(node, event, fn, opts) {node.addEventListener(event, fn, opts); removers.push(() => node.removeEventListener(event, fn, opts));}
  function surface(w, h) {const c = document.createElement('canvas'); c.width = w; c.height = h; return c;}
  try {
  // Cinco superfícies: cenário, três campos estelares e partículas próximas.
  for (let i = 0; i < 5; i++) {
    const c = surface(1, 1); c.className = 'cosmos-layer';
    c.setAttribute('aria-hidden', 'true'); c.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none';
    root.appendChild(c); canvases.push(c); contexts.push(c.getContext('2d', {alpha: true}));
  }
  if (contexts.some(c => !c)) {canvases.forEach(c => c.remove()); return {destroy(){}, setPaused(){}, setConstellations(){}, get reduced(){return true;}, get paused(){return true;}};}

  // Ruído de valor interpolado e fBm: texturas determinísticas, sem imagens.
  function hash(x, y) {let n = Math.imul(x, 374761393) ^ Math.imul(y, 668265263); n = Math.imul(n ^ (n >>> 13), 1274126177); return ((n ^ (n >>> 16)) >>> 0) / 4294967295;}
  function noise(x, y) {
    const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
    const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
    return mix(mix(hash(ix, iy), hash(ix + 1, iy), u), mix(hash(ix, iy + 1), hash(ix + 1, iy + 1), u), v);
  }
  function fbm(x, y) {let s = 0, a = 0.55; for (let k = 0; k < 5; k++) {s += noise(x, y) * a; x = x * 2.03 + 7.9; y = y * 2.03 + 3.2; a *= 0.48;} return s;}
  const nebula = surface(640, 360), nc = nebula.getContext('2d');
  const fog = nc.createImageData(640, 360);
  for (let y = 0; y < 360; y++) for (let x = 0; x < 640; x++) {
    const u = x / 640, v = y / 360;
    const n = fbm(u * 4.8 + 21, v * 4.8 + 50), detail = fbm(u * 10 + 3, v * 10 + 2);
    const band = Math.exp(-Math.pow((v - 0.58 + 0.18 * Math.sin(u * 5)) / 0.25, 2));
    const blue = Math.exp(-((u - 0.24) ** 2 * 6 + (v - 0.48) ** 2 * 5));
    const purple = Math.exp(-((u - 0.66) ** 2 * 9 + (v - 0.55) ** 2 * 4));
    const gas = Math.max(0, n - 0.31) * band * 2.8;
    const lane = 1 - clamp((detail - 0.47) * 3.2, 0, 0.76);
    const i = (y * 640 + x) * 4;
    fog.data[i] = (19 * blue + 66 * purple) * gas * lane;
    fog.data[i + 1] = (66 * blue + 27 * purple) * gas * lane;
    fog.data[i + 2] = (100 * blue + 105 * purple) * gas * lane;
    fog.data[i + 3] = 255;
  }
  nc.putImageData(fog, 0, 0);

  const sprites = C.starColors.map(color => {
    const c = surface(40, 40), ctx = c.getContext('2d');
    const g = ctx.createRadialGradient(20, 20, 0, 20, 20, 20);
    g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.09, `rgba(${color},.9)`);
    g.addColorStop(0.22, `rgba(${color},.25)`); g.addColorStop(1, `rgba(${color},0)`);
    ctx.fillStyle = g; ctx.fillRect(0, 0, 40, 40); return c;
  });
  const galaxy = surface(720, 720), gc = galaxy.getContext('2d'), gr = random(C.seed + 3);
  gc.globalCompositeOperation = 'screen';
  for (let i = 0; i < 11500; i++) {
    const r = Math.pow(gr(), 0.72) * 327, arm = i % 4;
    const angle = arm * TAU / 4 + r * 0.019 + (gr() - 0.5) * (0.21 + r / 720);
    const x = 360 + Math.cos(angle) * r, y = 360 + Math.sin(angle) * r;
    const size = 1.1 + gr() * 2.4, core = 1 - r / 327;
    gc.globalAlpha = 0.12 + core * 0.4;
    gc.drawImage(sprites[r < 80 ? 2 : i % 2], x - size * 2, y - size * 2, size * 4, size * 4);
  }
  gc.globalAlpha = 1;
  const core = gc.createRadialGradient(360, 360, 0, 360, 360, 75);
  core.addColorStop(0, 'rgba(255,247,223,.95)'); core.addColorStop(0.15, 'rgba(240,217,181,.6)'); core.addColorStop(1, 'rgba(239,208,179,0)');
  gc.fillStyle = core; gc.fillRect(280, 280, 160, 160);

  // Mapa equiretangular do planeta; projeção esférica com iluminação fixa.
  const tw = 512, th = 256, texture = new Uint8ClampedArray(tw * th * 3);
  for (let y = 0; y < th; y++) for (let x = 0; x < tw; x++) {
    const lat = y / th * Math.PI, lon = x / tw * TAU;
    // Coordenadas periódicas evitam uma costura na rotação.
    const nx = Math.cos(lon) * Math.sin(lat) * 3, ny = Math.sin(lon) * Math.sin(lat) * 3;
    const land = fbm(nx + 34, ny + 61 + Math.cos(lat) * 3);
    const cloud = Math.pow(clamp((fbm(nx * 1.8 + 76, ny * 1.8 + 19 + Math.cos(lat) * 5) - 0.44) * 5, 0, 1), 1.4);
    const terrain = clamp((land - 0.43) * 9, 0, 1), i = (y * tw + x) * 3;
    texture[i] = mix(mix(10, 52, terrain), 187, cloud * 0.74);
    texture[i + 1] = mix(mix(34, 94, terrain), 208, cloud * 0.74);
    texture[i + 2] = mix(mix(65, 103, terrain), 216, cloud * 0.74);
  }
  const ps = coarse.matches ? 192 : 320, globe = surface(ps, ps), pc = globe.getContext('2d');
  const globeImage = pc.createImageData(ps, ps), samples = [];
  for (let y = 0; y < ps; y++) for (let x = 0; x < ps; x++) {
    const nx = (x + 0.5) / ps * 2 - 1, ny = (y + 0.5) / ps * 2 - 1, r2 = nx * nx + ny * ny;
    if (r2 > 1) continue;
    const nz = Math.sqrt(1 - r2), diffuse = Math.max(0, -nx * 0.64 - ny * 0.48 + nz * 0.36);
    const shade = 0.012 + Math.pow(diffuse, 0.8) * 0.99;
    samples.push({i: (y * ps + x) * 4, u: (Math.atan2(nx, nz) / TAU + 0.5) * tw,
      v: clamp(Math.floor((Math.asin(ny) / Math.PI + 0.5) * th), 0, th - 1), shade,
      rim: Math.pow(1 - nz, 4) * Math.min(1, diffuse * 4) * 0.65});
  }
  function paintPlanet() {
    const shift = time * C.planetSpeed * tw;
    for (const p of samples) {
      const j = (p.v * tw + Math.floor((p.u + shift) % tw)) * 3, i = p.i;
      globeImage.data[i] = texture[j] * p.shade + p.rim * 35;
      globeImage.data[i + 1] = texture[j + 1] * p.shade + p.rim * 125;
      globeImage.data[i + 2] = texture[j + 2] * p.shade + p.rim * 192;
      globeImage.data[i + 3] = 255;
    }
    pc.putImageData(globeImage, 0, 0); planetAt = time;
  }
  const dust = Array.from({length: C.dustCount}, () => ({x: rng(), y: rng(), size: 0.3 + rng() * 0.65, phase: rng() * TAU}));

  function resize() {
    if (destroyed) return;
    width = Math.max(1, root.clientWidth || window.innerWidth); height = Math.max(1, root.clientHeight || window.innerHeight);
    dpr = Math.min(window.devicePixelRatio || 1, coarse.matches ? C.mobileDPR : C.maxDPR);
    for (let i = 0; i < canvases.length; i++) {
      canvases[i].width = Math.round(width * dpr); canvases[i].height = Math.round(height * dpr);
      contexts[i].setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    const density = clamp(width * height / (1440 * 900), 0.34, 1.7);
    const r = random(C.seed + 1);
    stars = C.starCounts.map((n, layer) => Array.from({length: Math.round(n * density)}, () => ({
      x: r(), y: r(), radius: (layer === 0 ? 0.28 : layer === 1 ? 0.48 : 0.85) + r() * (layer + 1) * 0.28,
      alpha: 0.26 + r() * 0.65, phase: r() * TAU, twinkle: 0.25 + r() * 0.85, color: Math.floor(r() * 3)
    })));
    planet = {x: width * (width < 700 ? 0.94 : 0.88), y: height * (width < 700 ? 0.82 : 0.83),
      r: Math.min(width * 0.33, height * 0.43)};
    render();
  }
  function drawWorld(ctx) {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#02030a'; ctx.fillRect(0, 0, width, height);
    const drift = Math.sin(time * C.nebulaSpeed) * 22;
    ctx.drawImage(nebula, -35 + drift + pointer.x * 5, -30 + pointer.y * 4, width + 70, height + 60);
    ctx.save(); ctx.globalCompositeOperation = 'screen'; ctx.globalAlpha = 0.22;
    ctx.drawImage(nebula, -50 - drift, -height * 0.2, width * 1.2, height * 1.4); ctx.restore();
    ctx.save(); ctx.translate(width * 0.21 + pointer.x * 8, height * 0.27 + pointer.y * 6);
    ctx.rotate(-0.42); ctx.scale(1, 0.43); ctx.rotate(time * C.galaxySpeed);
    const size = Math.min(width * 0.5, 680); ctx.globalAlpha = 0.82;
    ctx.drawImage(galaxy, -size / 2, -size / 2, size, size); ctx.restore();
    if (planetAt < 0 || time - planetAt >= C.planetRefresh) paintPlanet();
    const {x, y, r} = planet, px = x + pointer.x * 12, py = y + pointer.y * 9;
    const atmosphere = ctx.createRadialGradient(px, py, r * 0.96, px, py, r * 1.16);
    atmosphere.addColorStop(0, 'rgba(57,139,200,0)'); atmosphere.addColorStop(0.18, 'rgba(89,180,227,.12)'); atmosphere.addColorStop(0.38, 'rgba(48,106,168,.06)'); atmosphere.addColorStop(1, 'rgba(30,74,150,0)');
    ctx.fillStyle = atmosphere; ctx.beginPath(); ctx.arc(px, py, r * 1.16, 0, TAU); ctx.fill();
    ctx.drawImage(globe, px - r, py - r, r * 2, r * 2);
    const vignette = ctx.createRadialGradient(width * 0.48, height * 0.5, height * 0.12, width * 0.48, height * 0.5, Math.max(width, height) * 0.74);
    vignette.addColorStop(0, 'rgba(0,0,0,0)'); vignette.addColorStop(1, 'rgba(0,0,5,.64)');
    ctx.fillStyle = vignette; ctx.fillRect(0, 0, width, height);
  }
  function drawStars(ctx, layer) {
    ctx.clearRect(0, 0, width, height);
    const list = stars[layer], amount = Math.ceil(list.length * quality);
    const px = planet.x + pointer.x * 12, py = planet.y + pointer.y * 9;
    for (let i = 0; i < amount; i++) {
      const s = list[i];
      const x = ((s.x * (width + 80) + time * C.starSpeeds[layer] + pointer.x * C.parallax[layer]) % (width + 80)) - 40;
      const y = ((s.y * (height + 80) + time * C.starSpeeds[layer] * 0.16 + pointer.y * C.parallax[layer]) % (height + 80)) - 40;
      if ((x - px) ** 2 + (y - py) ** 2 < planet.r ** 2) continue;
      const twinkle = 0.79 + 0.21 * Math.sin(time * s.twinkle + s.phase) * Math.sin(time * 0.37 + s.phase);
      ctx.globalAlpha = s.alpha * twinkle;
      if (layer === 0) {ctx.fillStyle = `rgb(${C.starColors[s.color]})`; ctx.fillRect(x, y, s.radius, s.radius);}
      else {const size = s.radius * (layer === 2 ? 10 : 6); ctx.drawImage(sprites[s.color], x - size / 2, y - size / 2, size, size);}
    }
    ctx.globalAlpha = 1;
  }
  function drawEffects(ctx) {
    ctx.clearRect(0, 0, width, height);
    for (const p of dust) {
      const x = ((p.x * (width + 40) + time * 1.4) % (width + 40)) - 20;
      const y = ((p.y * (height + 40) - time * 0.4 + Math.sin(time * 0.06 + p.phase) * 5 + height + 40) % (height + 40)) - 20;
      ctx.globalAlpha = 0.08 + 0.08 * Math.sin(p.phase + time * 0.1); ctx.fillStyle = C.gold;
      ctx.fillRect(x, y, p.size, p.size);
    }
    ctx.globalAlpha = 1;
    if (lines && width > 540) {
      const points = [[.08,.71],[.18,.65],[.28,.72],[.4,.61],[.49,.64]];
      ctx.lineWidth = 0.7; ctx.strokeStyle = 'rgba(121,222,238,.19)'; ctx.beginPath();
      points.forEach(([x,y], i) => {const px=x*width+pointer.x*5,py=y*height+pointer.y*4;i?ctx.lineTo(px,py):ctx.moveTo(px,py);}); ctx.stroke();
      points.forEach(([x,y],i) => {ctx.fillStyle=i%2?C.gold:C.cyan;ctx.globalAlpha=.55;ctx.beginPath();ctx.arc(x*width+pointer.x*5,y*height+pointer.y*4,1.5,0,TAU);ctx.fill();});
      ctx.globalAlpha = 0.38; ctx.fillStyle = C.cyan; ctx.font = '9px ui-monospace, monospace';
      ctx.fillText('BOS', width * 0.18 - 8, height * 0.65 - 13); ctx.fillStyle = C.gold;
      ctx.fillText('CHoCH', width * 0.4 - 13, height * 0.61 - 13); ctx.globalAlpha = 1;
    }
    if (!reduced && !paused && time > nextMeteor) {
      meteors.push({x: width * (0.1 + rng() * 0.7), y: height * rng() * 0.4, birth: time, life: 1.1, speed: 520 + rng() * 200});
      nextMeteor = time + C.meteorMinSeconds + rng() * (C.meteorMaxSeconds - C.meteorMinSeconds);
    }
    meteors = meteors.filter(m => time - m.birth < m.life);
    for (const m of meteors) {
      const age = time - m.birth, x = m.x + age * m.speed, y = m.y + age * m.speed * 0.42;
      const tail = Math.min(150, age * m.speed), opacity = Math.sin(age / m.life * Math.PI) * 0.8;
      const g = ctx.createLinearGradient(x - tail, y - tail * 0.42, x, y);
      g.addColorStop(0, 'rgba(121,222,238,0)'); g.addColorStop(1, `rgba(218,245,255,${opacity})`);
      ctx.strokeStyle = g; ctx.lineWidth = 1.25; ctx.beginPath(); ctx.moveTo(x-tail,y-tail*.42); ctx.lineTo(x,y); ctx.stroke();
      ctx.globalAlpha = opacity; ctx.drawImage(sprites[0], x-5,y-5,10,10); ctx.globalAlpha = 1;
    }
  }
  function render() {if(destroyed)return; drawWorld(contexts[0]); for(let i=0;i<3;i++)drawStars(contexts[i+1],i);drawEffects(contexts[4]);}
  function running() {return !destroyed && !paused && !reduced && !document.hidden;}
  function animate(now) {
    frame = 0; if (!running()) return;
    const raw = previous ? (now - previous) / 1000 : 0; previous = now;
    // Tempo em segundos: mesma velocidade em telas de 60, 90 ou 120 Hz.
    const dt = Math.min(raw, 0.05); time += dt; paintBudget += dt;
    pointer.x = mix(pointer.x, target.x, 1 - Math.exp(-dt * 2.8));
    pointer.y = mix(pointer.y, target.y, 1 - Math.exp(-dt * 2.8));
    if (raw > 0.028) slowFrames++; else slowFrames = Math.max(0, slowFrames - 1);
    if (slowFrames > 70 && quality > 0.55) {quality = Math.max(0.55, quality - 0.15); slowFrames = 0;}
    const period = 1 / C.targetFPS;
    if (paintBudget + 0.000001 >= period) {
      paintBudget = Math.max(0, paintBudget - period) % period;
      render();
    }
    frame = requestAnimationFrame(animate);
  }
  function schedule() {if(running()&&!frame){previous=0;frame=requestAnimationFrame(animate);}}
  function halt() {if(frame)cancelAnimationFrame(frame);frame=0;previous=0;paintBudget=0;}
  function motionChange() {reduced=media.matches;halt();if(reduced){target={x:0,y:0};pointer={x:0,y:0};meteors=[];}render();schedule();options.onMotionChange?.({paused,reduced});}
  listen(window,'resize',resize,{passive:true});
  listen(window,'pointermove',e=>{if(!running()||coarse.matches)return;target.x=clamp(e.clientX/width*2-1,-1,1);target.y=clamp(e.clientY/height*2-1,-1,1);},{passive:true});
  listen(document,'pointerleave',()=>{target={x:0,y:0};});
  listen(document,'visibilitychange',()=>{halt();schedule();});
  listen(media,'change',motionChange);
  resize();schedule();
  return {
    setPaused(value){paused=Boolean(value);halt();render();schedule();options.onMotionChange?.({paused,reduced});},
    setConstellations(value){lines=Boolean(value);render();},
    get reduced(){return reduced;},get paused(){return paused;},
    destroy(){destroyed=true;halt();removers.forEach(fn=>fn());canvases.forEach(c=>c.remove());stars=[];meteors=[];}
  };
  } catch {
    // Um fundo decorativo indisponível nunca deve derrubar o painel de sinais.
    destroyed = true; if (frame) cancelAnimationFrame(frame);
    removers.forEach(fn => fn()); canvases.forEach(c => c.remove());
    return {destroy(){},setPaused(){},setConstellations(){},get reduced(){return true;},get paused(){return true;}};
  }
}
