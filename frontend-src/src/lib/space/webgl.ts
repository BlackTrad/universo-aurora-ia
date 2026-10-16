import {FrameClock, resolution, UNIVERSE, type UniverseController, type MotionState} from './config';
import {FULLSCREEN_VERTEX, NEBULA_FRAGMENT, SKY_FRAGMENT, STAR_VERTEX, STAR_FRAGMENT,
  PLANET_VERTEX, PLANET_FRAGMENT} from './shaders';

type Options = {onMotionChange?: (state: MotionState) => void; onFailure: () => void};
type Program = {handle: WebGLProgram; uniforms: Record<string, WebGLUniformLocation | null>};

export function mountWebGLUniverse(root: HTMLElement, options: Options): UniverseController {
  const canvas = document.createElement('canvas');
  canvas.className = 'universe-canvas'; canvas.setAttribute('aria-hidden', 'true');
  const context = canvas.getContext('webgl2', {alpha: false, antialias: false, depth: false,
    stencil: false, powerPreference: 'low-power', preserveDrawingBuffer: false});
  if (!context) throw new Error('WebGL indisponível');
  const gl = context;
  const programs: WebGLProgram[] = [], shaders: WebGLShader[] = [], buffers: WebGLBuffer[] = [];
  const textures: WebGLTexture[] = [], framebuffers: WebGLFramebuffer[] = [], arrays: WebGLVertexArrayObject[] = [];
  const removers: (() => void)[] = [];
  let destroyed = false, frame = 0, paused = false;
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const coarse = window.matchMedia('(pointer: coarse)');
  let reduced = motion.matches, mobile = coarse.matches || window.innerWidth < 700;
  let width = 1, height = 1, dpr = 1, quality = 0, slowScore = 0;
  let skyFrom = 0, skyTo = 1, skyUpdated = 0, skyWidth = 1, skyHeight = 1;
  let starCount = 0, dustCount = 0, headerBottom = 80;
  const pointer = {x: 0, y: 0}, target = {x: 0, y: 0};
  const clock = new FrameClock();
  const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

  function notify() {options.onMotionChange?.({paused, reduced, renderer: 'webgl'});}
  function listen(node: EventTarget, name: string, fn: EventListener, settings?: AddEventListenerOptions) {
    node.addEventListener(name, fn, settings);removers.push(() => node.removeEventListener(name, fn, settings));
  }
  function halt() {if (frame) cancelAnimationFrame(frame);frame = 0;clock.reset();}
  function release() {
    if (destroyed) return;
    destroyed = true;halt();removers.forEach(fn => fn());
    buffers.forEach(b => gl.deleteBuffer(b));arrays.forEach(a => gl.deleteVertexArray(a));
    textures.forEach(t => gl.deleteTexture(t));framebuffers.forEach(f => gl.deleteFramebuffer(f));
    shaders.forEach(s => gl.deleteShader(s));programs.forEach(p => gl.deleteProgram(p));canvas.remove();
  }
  function allocate<T>(value: T | null, bucket: T[]): T {
    if (!value) throw new Error('Recursos gráficos indisponíveis');bucket.push(value);return value;
  }
  function shader(source: string, kind: number) {
    const s = allocate(gl!.createShader(kind), shaders);gl!.shaderSource(s, source);gl!.compileShader(s);
    if (!gl!.getShaderParameter(s, gl!.COMPILE_STATUS)) throw new Error('Shader incompatível');return s;
  }
  function program(vertex: string, fragment: string, names: string[]): Program {
    const handle = allocate(gl!.createProgram(), programs);
    gl!.attachShader(handle, shader(vertex, gl!.VERTEX_SHADER));
    gl!.attachShader(handle, shader(fragment, gl!.FRAGMENT_SHADER));gl!.linkProgram(handle);
    if (!gl!.getProgramParameter(handle, gl!.LINK_STATUS)) throw new Error('Programa gráfico incompatível');
    return {handle, uniforms: Object.fromEntries(names.map(name => [name, gl!.getUniformLocation(handle, name)]))};
  }
  function use(p: Program) {gl!.useProgram(p.handle);return p.uniforms;}
  function random(seed: number) {let s = seed >>> 0;return () => {s = (Math.imul(s, 1664525) + 1013904223) >>> 0;return s / 4294967296;};}

  try {
    const nebula = program(FULLSCREEN_VERTEX, NEBULA_FRAGMENT, ['uTime', 'uAspect']);
    const sky = program(FULLSCREEN_VERTEX, SKY_FRAGMENT,
      ['uSkyA', 'uSkyB', 'uSkyMix', 'uTime', 'uPointer', 'uViewport', 'uComet']);
    const stars = program(STAR_VERTEX, STAR_FRAGMENT, ['uPointer', 'uViewport', 'uTime', 'uDPR', 'uDust']);
    const planet = program(PLANET_VERTEX, PLANET_FRAGMENT,
      ['uCenter', 'uViewport', 'uRadius', 'uExtent', 'uTime', 'uRotation', 'uCloudRotation', 'uKind']);
    const empty = allocate(gl.createVertexArray(), arrays);
    const skyTextures = [allocate(gl.createTexture(), textures), allocate(gl.createTexture(), textures)];
    const skyFrames = [allocate(gl.createFramebuffer(), framebuffers), allocate(gl.createFramebuffer(), framebuffers)];
    const starArray = allocate(gl.createVertexArray(), arrays), dustArray = allocate(gl.createVertexArray(), arrays);
    const starBuffer = allocate(gl.createBuffer(), buffers), dustBuffer = allocate(gl.createBuffer(), buffers);

    function fillPoints(array: WebGLVertexArrayObject, buffer: WebGLBuffer, count: number, dust: boolean) {
      const r = random(UNIVERSE.seed + (dust ? 31 : 1)), data = new Float32Array(count * 6);
      for (let i = 0; i < count; i++) {
        const k = i * 6, z = r();
        data[k] = r() * 2.4 - 1.2;data[k + 1] = r() * 2.4 - 1.2;data[k + 2] = z;
        data[k + 3] = dust ? 4 + z * 15 : .9 + Math.pow(z, 6) * 7.5;
        data[k + 4] = r();data[k + 5] = r();
      }
      gl!.bindVertexArray(array);gl!.bindBuffer(gl!.ARRAY_BUFFER, buffer);
      gl!.bufferData(gl!.ARRAY_BUFFER, data, gl!.STATIC_DRAW);
      gl!.enableVertexAttribArray(0);gl!.vertexAttribPointer(0, 4, gl!.FLOAT, false, 24, 0);
      gl!.enableVertexAttribArray(1);gl!.vertexAttribPointer(1, 2, gl!.FLOAT, false, 24, 16);
    }
    function paintNebula(index: number, time: number) {
      gl!.bindFramebuffer(gl!.FRAMEBUFFER, skyFrames[index]);gl!.viewport(0, 0, skyWidth, skyHeight);
      gl!.disable(gl!.BLEND);gl!.bindVertexArray(empty);
      const u = use(nebula);gl!.uniform1f(u.uTime, time);gl!.uniform1f(u.uAspect, width / height);gl!.drawArrays(gl!.TRIANGLES, 0, 3);
      gl!.bindFramebuffer(gl!.FRAMEBUFFER, null);
    }
    function setupSky() {
      skyWidth = Math.round((mobile ? UNIVERSE.mobileSkyWidth : UNIVERSE.skyWidth) * Math.pow(.82, quality));
      skyHeight = clamp(Math.round(skyWidth * height / width), 256, 1024);
      for (let i = 0; i < 2; i++) {
        gl!.bindTexture(gl!.TEXTURE_2D, skyTextures[i]);
        gl!.texImage2D(gl!.TEXTURE_2D, 0, gl!.RGBA8, skyWidth, skyHeight, 0, gl!.RGBA, gl!.UNSIGNED_BYTE, null);
        gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_MIN_FILTER, gl!.LINEAR);
        gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_MAG_FILTER, gl!.LINEAR);
        gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_WRAP_S, gl!.CLAMP_TO_EDGE);
        gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_WRAP_T, gl!.CLAMP_TO_EDGE);
        gl!.bindFramebuffer(gl!.FRAMEBUFFER, skyFrames[i]);
        gl!.framebufferTexture2D(gl!.FRAMEBUFFER, gl!.COLOR_ATTACHMENT0, gl!.TEXTURE_2D, skyTextures[i], 0);
        if (gl!.checkFramebufferStatus(gl!.FRAMEBUFFER) !== gl!.FRAMEBUFFER_COMPLETE) throw new Error('Textura gráfica indisponível');
        paintNebula(i, clock.time);
      }
      skyFrom = 0;skyTo = 1;skyUpdated = clock.time;
    }
    function pointLayer(array: WebGLVertexArrayObject, amount: number, isDust: boolean) {
      gl!.bindVertexArray(array);const u = use(stars);
      gl!.uniform2f(u.uPointer, pointer.x, pointer.y);gl!.uniform2f(u.uViewport, width, height);
      gl!.uniform1f(u.uTime, clock.time);gl!.uniform1f(u.uDPR, dpr);gl!.uniform1f(u.uDust, isDust ? 1 : 0);
      gl!.blendFunc(gl!.SRC_ALPHA, gl!.ONE);gl!.drawArrays(gl!.POINTS, 0, amount);
    }
    function drawPlanet(x: number, y: number, radius: number, kind: number, speed: number) {
      const u = use(planet);gl!.bindVertexArray(empty);gl!.blendFunc(gl!.SRC_ALPHA, gl!.ONE_MINUS_SRC_ALPHA);
      gl!.uniform2f(u.uCenter, x / width * 2 - 1, 1 - y / height * 2);
      gl!.uniform2f(u.uViewport, canvas.width, canvas.height);gl!.uniform1f(u.uRadius, radius * dpr);
      gl!.uniform1f(u.uExtent, kind ? 2.6 : 1.18);gl!.uniform1f(u.uKind, kind);
      gl!.uniform1f(u.uTime, clock.time);gl!.uniform1f(u.uRotation, speed);
      gl!.uniform1f(u.uCloudRotation, UNIVERSE.cloudRotation);gl!.drawArrays(gl!.TRIANGLE_STRIP, 0, 4);
    }
    function render() {
      if (destroyed || gl!.isContextLost()) return;
      const time = clock.time;
      if (!reduced && !paused && time - skyUpdated >= UNIVERSE.skyRefreshSeconds) {
        skyFrom = skyTo;skyTo = 1 - skyTo;paintNebula(skyTo, time);skyUpdated = time;
      }
      gl!.bindFramebuffer(gl!.FRAMEBUFFER, null);gl!.viewport(0, 0, canvas.width, canvas.height);
      gl!.disable(gl!.BLEND);gl!.bindVertexArray(empty);
      const u = use(sky);
      gl!.activeTexture(gl!.TEXTURE0);gl!.bindTexture(gl!.TEXTURE_2D, skyTextures[skyFrom]);gl!.uniform1i(u.uSkyA, 0);
      gl!.activeTexture(gl!.TEXTURE1);gl!.bindTexture(gl!.TEXTURE_2D, skyTextures[skyTo]);gl!.uniform1i(u.uSkyB, 1);
      const mix = clamp((time - skyUpdated) / (UNIVERSE.skyRefreshSeconds * .94), 0, 1);
      gl!.uniform1f(u.uSkyMix, mix * mix * (3 - 2 * mix));gl!.uniform1f(u.uTime, time);
      gl!.uniform2f(u.uPointer, pointer.x, pointer.y);gl!.uniform2f(u.uViewport, width, height);
      const age = (time + 16) % UNIVERSE.cometPeriod, cycle = Math.floor((time + 16) / UNIVERSE.cometPeriod);
      const visible = !reduced && age < UNIVERSE.cometDuration;
      gl!.uniform3f(u.uComet, .17 + (cycle % 3) * .14 + age * .21, .87 - age * .09,
        visible ? Math.sin(age / UNIVERSE.cometDuration * Math.PI) : 0);
      gl!.drawArrays(gl!.TRIANGLES, 0, 3);gl!.enable(gl!.BLEND);
      pointLayer(starArray, starCount, false);
      const breathing = reduced ? 0 : Math.sin(time * .11) * 2.5;
      // O planeta aparece no espaço aberto do topo, sem ocultar os controles.
      const centerX = width * (mobile ? .97 : .835) + pointer.x * 16;
      const centerY = headerBottom + (mobile ? 122 : 114) + pointer.y * 9 + breathing;
      const radius = Math.min(width * (mobile ? .31 : .16), mobile ? 116 : 174);
      drawPlanet(width * .12 + pointer.x * 4, height * .79 + breathing, Math.min(width * .075, 78), 1, .013);
      drawPlanet(centerX - radius * 1.58, centerY - radius * .8, radius * .16, 2, -.012);
      drawPlanet(centerX, centerY, radius, 0, UNIVERSE.planetRotation);
      pointLayer(dustArray, dustCount, true);
      gl!.bindVertexArray(null);
    }
    function resize() {
      if (destroyed) return;
      width = Math.max(1, root.clientWidth || window.innerWidth);height = Math.max(1, root.clientHeight || window.innerHeight);
      mobile = coarse.matches || width < 700;
      headerBottom = root.ownerDocument.querySelector('.app-header')?.getBoundingClientRect().bottom || 80;
      const size = resolution(width, height, window.devicePixelRatio, mobile, quality);
      canvas.width = size.width;canvas.height = size.height;dpr = size.ratio;
      starCount = Math.round((mobile ? UNIVERSE.mobileStars : UNIVERSE.stars) * Math.pow(.7, quality));
      dustCount = mobile ? UNIVERSE.mobileDust : UNIVERSE.dust;
      fillPoints(starArray, starBuffer, starCount, false);fillPoints(dustArray, dustBuffer, dustCount, true);
      setupSky();render();
    }
    function running() {return !destroyed && !paused && !reduced && !document.hidden;}
    function animate(now: number) {
      frame = 0;if (!running()) return;
      const step = clock.advance(now);
      slowScore = step.raw > .032 ? slowScore + 1 : Math.max(0, slowScore - .25);
      if (slowScore > 90 && quality < 2) {quality++;slowScore = 0;resize();}
      if (step.paint) {
        const inertia = 1 - Math.exp(-step.dt * 2.6);
        pointer.x += (target.x - pointer.x) * inertia;pointer.y += (target.y - pointer.y) * inertia;
        render();
      }
      frame = requestAnimationFrame(animate);
    }
    function schedule() {if (running() && !frame) frame = requestAnimationFrame(animate);}
    function fail() {if (destroyed) return;release();options.onFailure();}
    listen(canvas, 'webglcontextlost', event => {event.preventDefault();fail();});
    listen(window, 'resize', () => {try {resize();} catch {fail();}}, {passive: true});
    listen(window, 'pointermove', event => {
      const e = event as PointerEvent;if (!running() || mobile || e.buttons || e.pointerType === 'touch') return;
      target.x = clamp(e.clientX / width * 2 - 1, -1, 1);target.y = clamp(e.clientY / height * 2 - 1, -1, 1);
    }, {passive: true});
    listen(document, 'pointerleave', () => {target.x = 0;target.y = 0;});
    listen(document, 'visibilitychange', () => {halt();schedule();});
    listen(motion, 'change', () => {
      reduced = motion.matches;halt();
      if (reduced) {pointer.x = pointer.y = target.x = target.y = 0;}
      render();schedule();notify();
    });
    listen(window, 'pagehide', event => {(event as PageTransitionEvent).persisted ? halt() : release();});
    listen(window, 'pageshow', () => {if (!destroyed) {clock.reset();render();schedule();}});
    const header = root.ownerDocument.querySelector('.app-header');
    if (header && typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => {headerBottom = header.getBoundingClientRect().bottom;render();});
      observer.observe(header);removers.push(() => observer.disconnect());
    }
    root.appendChild(canvas);resize();schedule();notify();
    return {
      get reduced() {return reduced;},get paused() {return paused;},
      setPaused(value) {if (destroyed) return;paused = Boolean(value);halt();render();schedule();notify();},
      destroy: release,
    };
  } catch (error) {release();throw error;}
}
