// GLSL ES 3.00. Sem fotografias esticadas, downloads ou texturas externas.
const noise = `
float hash31(vec3 p) {
  p = fract(p * .1031); p += dot(p, p.yzx + 33.33);
  return fract((p.x + p.y) * p.z);
}
float noise3(vec3 p) {
  vec3 i = floor(p), f = fract(p); f = f*f*(3.-2.*f);
  return mix(mix(mix(hash31(i),hash31(i+vec3(1,0,0)),f.x),
    mix(hash31(i+vec3(0,1,0)),hash31(i+vec3(1,1,0)),f.x),f.y),
    mix(mix(hash31(i+vec3(0,0,1)),hash31(i+vec3(1,0,1)),f.x),
    mix(hash31(i+vec3(0,1,1)),hash31(i+vec3(1,1,1)),f.x),f.y),f.z);
}
float fbm(vec3 p) {
  float v=0., a=.5;
  for(int i=0;i<5;i++){v+=a*noise3(p);p=p*2.07+vec3(11.4,7.3,9.1);a*=.49;}
  return v;
}
mat2 turn(float a){float c=cos(a),s=sin(a);return mat2(c,-s,s,c);}
`;

export const FULLSCREEN_VERTEX = `#version 300 es
precision highp float;
out vec2 vUV;
void main(){
  vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));
  vUV=p;gl_Position=vec4(p*2.-1.,0,1);
}`;

// A nebulosa é calculada em textura intermediária e interpolada entre duas
// amostras. A tela inteira não repete fBm volumétrico a cada quadro.
export const NEBULA_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUV;
uniform float uTime;
uniform float uAspect;
out vec4 outColor;
${noise}
void main(){
  vec2 p=(vUV-.5)*vec2(uAspect*1.28,1.28);p=turn(-.32)*p;
  vec3 q=vec3(p*2.8,uTime*.004);
  vec3 warp=vec3(fbm(q+3.),fbm(q+17.),fbm(q+31.));
  float gas=fbm(q+warp*2.8), detail=fbm(q*3.5+warp);
  float band=exp(-pow((p.y+.15*sin(p.x*2.))/ .31,2.));
  float density=pow(max(gas-.26,0.)*2.,1.6)*band;
  float lanes=1.-smoothstep(.43,.72,detail)*.86;
  vec3 blue=vec3(.023,.091,.18),violet=vec3(.15,.026,.112);
  vec3 tint=mix(blue,violet,smoothstep(-.7,.8,p.x+warp.x*.6));
  vec3 col=vec3(.0012,.0022,.006)+tint*density*lanes*1.8;
  col+=vec3(.16,.094,.033)*pow(max(gas-.45,0.)*3.,2.)*band*.38;
  // Uma galáxia distante tem braços, núcleo e faixas escuras próprios.
  vec2 g=(vUV-vec2(.22,.72))*vec2(uAspect*.9,3.8);g=turn(-.42)*g;
  float r=length(g),a=atan(g.y,g.x)+uTime*.0015;
  float arms=pow(.5+.5*cos(a*3.-log(max(r,.006))*5.2),6.);
  float disk=exp(-r*12.)*smoothstep(.014,.06,r);
  col+=mix(vec3(.09,.15,.24),vec3(.27,.18,.11),exp(-r*30.))*(arms*.8+.16)*disk;
  col+=vec3(.34,.26,.16)*exp(-r*r*950.);
  outColor=vec4(pow(max(col,vec3(0)),vec3(.78)),1);
}`;

export const SKY_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUV;
uniform sampler2D uSkyA;
uniform sampler2D uSkyB;
uniform float uSkyMix;
uniform float uTime;
uniform vec2 uPointer;
uniform vec2 uViewport;
uniform vec3 uComet;
out vec4 outColor;
void main(){
  vec2 uv=(vUV-.5)*.94+.5+uPointer*.003+vec2(sin(uTime*.006),cos(uTime*.004))*.004;
  vec3 col=mix(texture(uSkyA,uv).rgb,texture(uSkyB,uv).rgb,uSkyMix);
  vec2 sun=vec2(.115,.72),p=(vUV-sun)*vec2(uViewport.x/uViewport.y,1.);
  float r=length(p), angle=atan(p.y,p.x);
  col+=vec3(1.,.72,.35)*(.1*exp(-r*13.)+.7*exp(-r*r*4200.));
  col+=vec3(.52,.65,.7)*exp(-r*34.)*pow(abs(cos(angle*4.+.1*sin(uTime*.04))),28.)*.06;
  // Cauda luminosa breve, com uma origem diferente a cada passagem.
  if(uComet.z>0.){
    vec2 c=(vUV-uComet.xy)*vec2(uViewport.x/uViewport.y,1.);
    vec2 dir=normalize(vec2(1.,-.42));float along=dot(c,dir),side=abs(c.x*dir.y-c.y*dir.x);
    float tail=exp(-side*1900.)*exp(along*34.)*step(along,0.)*step(-.17,along);
    float head=exp(-dot(c,c)*950000.);
    col+=vec3(.62,.86,1.)*(tail*.45+head)*uComet.z;
  }
  float vignette=1.-smoothstep(.3,.95,length((vUV-.5)*vec2(1.05,1.)))*.35;
  outColor=vec4(col*vignette,1);
}`;

export const STAR_VERTEX = `#version 300 es
precision highp float;
layout(location=0) in vec4 aStar;
layout(location=1) in vec2 aLight;
uniform vec2 uPointer;
uniform vec2 uViewport;
uniform float uTime;
uniform float uDPR;
uniform float uDust;
out vec3 vColor;
out float vAlpha;
out float vDust;
void main(){
  float z=aStar.z,phase=aLight.y*6.283185;
  vec2 drift=vec2(sin(uTime*.009+phase),cos(uTime*.007+phase))*.006;
  vec2 p=aStar.xy+uPointer*(.003+z*.021)+drift*(.3+z);
  if(uDust>.5){
    p.x=mod(p.x+uTime*(.0015+z*.002)+1.3,2.6)-1.3;
    p.y+=sin(uTime*.08+phase)*.014;
  }
  gl_Position=vec4(p,0,1);
  float twinkle=.84+.16*sin(uTime*(.4+z)+phase)*sin(uTime*.31+phase);
  gl_PointSize=clamp(aStar.w*uDPR,1.,48.);
  vColor=mix(vec3(.68,.82,1.),vec3(1.,.87,.65),aLight.x);
  vAlpha=(uDust>.5?.13:(.26+z*.58))*twinkle;vDust=uDust;
}`;

export const STAR_FRAGMENT = `#version 300 es
precision highp float;
in vec3 vColor;
in float vAlpha;
in float vDust;
out vec4 outColor;
void main(){
  vec2 p=gl_PointCoord-.5;float r=length(p);
  float glow=exp(-r*r*(vDust>.5?16.:30.));
  float core=exp(-r*r*190.);
  float rays=exp(-min(abs(p.x),abs(p.y))*100.)*exp(-r*11.);
  outColor=vec4(vColor,(glow*.32+core*.75+rays*.14)*vAlpha);
}`;

export const PLANET_VERTEX = `#version 300 es
precision highp float;
uniform vec2 uCenter;
uniform vec2 uViewport;
uniform float uRadius;
uniform float uExtent;
out vec2 vLocal;
void main(){
  vec2 p=vec2(float(gl_VertexID&1),float((gl_VertexID>>1)&1))*2.-1.;
  vLocal=p*uExtent;gl_Position=vec4(uCenter+vLocal*uRadius*2./uViewport,0,1);
}`;

export const PLANET_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vLocal;
uniform float uTime;
uniform float uRotation;
uniform float uCloudRotation;
uniform float uKind;
out vec4 outColor;
${noise}
float continents(vec3 p){return fbm(p*2.7+vec3(8,17,29));}
float clouds(vec3 p){return smoothstep(.47,.67,fbm(p*5.+vec3(41,7,14)));}
void main(){
  vec2 p=vLocal;float r=length(p), aa=max(fwidth(r),.001);
  vec3 light=normalize(vec3(-.78,.48,.53));
  vec3 col=vec3(0);float alpha=0.;
  // O anel tem perspectiva, subdivisões e dois lados em relação ao globo.
  vec2 ring=turn(.32)*p;float rr=length(ring*vec2(1.,3.1));
  float ringAlpha=smoothstep(1.22,1.29,rr)*(1.-smoothstep(2.38,2.5,rr));
  ringAlpha*=.36+.34*noise3(vec3(rr*90.,0,0));
  ringAlpha*=1.-smoothstep(.015,.034,abs(rr-1.84))*.22;
  vec3 ringColor=vec3(.49,.37,.22)*(.7+.12*sin(rr*130.));
  bool giant=uKind>.5&&uKind<1.5;
  if(giant){col=ringColor;alpha=ringAlpha;}
  if(r<1.+aa){
    col=vec3(0);
    vec3 n=vec3(p,sqrt(max(0.,1.-dot(p,p))));
    vec3 surface=n;surface.xz=turn(uTime*uRotation)*surface.xz;
    surface.yz=turn(.17)*surface.yz;
    float lambert=dot(n,light),day=smoothstep(-.13,.22,lambert);
    vec3 base;
    float cloud=0.,ocean=0.;
    if(uKind<.5){
      float land=continents(surface),terrain=fbm(surface*19.+7.);
      ocean=1.-smoothstep(.435,.48,land);
      vec3 water=mix(vec3(.008,.045,.105),vec3(.016,.15,.24),smoothstep(.31,.47,land));
      vec3 ground=mix(vec3(.045,.095,.064),vec3(.18,.16,.095),terrain);
      ground=mix(ground,vec3(.34,.32,.28),smoothstep(.63,.76,land));
      base=mix(ground,water,ocean);
      float ice=smoothstep(.84,.98,abs(surface.y)+terrain*.08);
      base=mix(base,vec3(.67,.77,.8),ice);
      vec3 cn=n;cn.xz=turn(uTime*uCloudRotation+.3)*cn.xz;
      cloud=clouds(cn);
      float shadow=clouds(normalize(cn+light*.045));
      base*=1.-shadow*.22;
      base=mix(base,vec3(.78,.83,.87),cloud*.9);
      // Pontos de luz no lado noturno, agrupados sobre terra próxima à costa.
      float coast=smoothstep(.443,.48,land)*(1.-smoothstep(.56,.66,land));
      float settlements=smoothstep(.52,.72,fbm(surface*12.+81.));
      float lamps=smoothstep(.76,.94,noise3(surface*220.+5.));
      col+=vec3(1.,.53,.18)*lamps*settlements*coast*(1.-day)*2.7*(1.-cloud);
    }else if(giant){
      float turbulence=fbm(surface*8.+13.);
      float stripe=.5+.5*sin(surface.y*36.+turbulence*7.);
      base=mix(vec3(.12,.085,.057),vec3(.51,.37,.22),stripe);
      base+=vec3(.09,.07,.04)*fbm(surface*28.);
    }else{
      float terrain=fbm(surface*7.+vec3(8,4,27));
      float craters=smoothstep(.35,.56,noise3(surface*33.));
      base=mix(vec3(.065,.072,.08),vec3(.24,.25,.27),terrain)*(.65+craters*.35);
    }
    col+=base*(.014+max(0.,lambert)*1.5);
    vec3 halfVector=normalize(light+vec3(0,0,1));
    float spec=pow(max(0.,dot(n,halfVector)),85.);
    col+=vec3(.65,.8,1.)*spec*ocean*day*(1.-cloud)*.65;
    float fresnel=pow(1.-max(n.z,0.),3.8);
    col+=(uKind<.5?vec3(.025,.22,.43):vec3(.065,.06,.055))*fresnel*(.08+day*.8);
    float edge=1.-smoothstep(1.-aa,1.+aa,r);
    alpha=edge;
    if(giant&&ring.y<0.)col=mix(col,ringColor,ringAlpha*.85);
    col=pow(max(col,vec3(0)),vec3(.82));
  }else if(uKind<.5){
    float halo=exp(-(r-1.)*48.)*(1.-smoothstep(1.,1.16,r));
    float sunSide=clamp(dot(normalize(vec3(p,.05)),light)*.8+.2,.05,1.);
    col=vec3(.075,.4,.72)*sunSide;alpha=halo*.46;
  }
  if(alpha<.001)discard;
  outColor=vec4(col,alpha);
}`;
