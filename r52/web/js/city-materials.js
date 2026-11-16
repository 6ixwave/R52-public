import * as THREE from "./vendor/three.module.js";
// Contour stroke adapted from React Bits Topography (David Haz, 2026).
// Full attribution and MIT + Commons Clause are in vendor/REACT-BITS-LICENSE.txt.
export function urbanMaterials() {
  const uniforms = {
    uTime: { value: 0 },
    uColor: { value: new THREE.Color("#822dd8") },
    uHover: { value: new THREE.Vector2(9999, 9999) },
    uPulse: { value: new THREE.Vector3(0, 0, -100) },
  };
  const vertexShader =
    `uniform vec3 uColor;varying vec3 vWorld;varying vec3 vNormalW;varying vec3 vLocal;varying vec3 vTint;void main(){vec4 p=vec4(position,1.0);vec3 n=normal;#ifdef USE_INSTANCING\np=instanceMatrix*p;n=mat3(instanceMatrix)*n;#endif\nvTint=uColor;
#ifdef USE_INSTANCING_COLOR
vTint=instanceColor;
#endif
vec4 world=modelMatrix*p;vWorld=world.xyz;vLocal=position;vNormalW=normalize(mat3(modelMatrix)*n);gl_Position=projectionMatrix*viewMatrix*world;}`
      .replace(/;#if/g, ";\n#if")
      .replace(/;#endif/g, ";\n#endif");
  const common = `uniform float uTime;uniform vec3 uColor;uniform vec2 uHover;uniform vec3 uPulse;varying vec3 vWorld;varying vec3 vNormalW;varying vec3 vLocal;varying vec3 vTint;
float stroke(float dist,float width,float soft,float density,float crowd){float cover=max(0.0,min(min(width,soft),.5*(width+soft)-dist))/soft;return mix(cover,min(width*density,1.0)*.6,crowd);}
float energy(){float hover=exp(-dot(vWorld.xz-uHover,vWorld.xz-uHover)/80.0);float age=uTime-uPulse.z;float ring=exp(-pow((length(vWorld.xz-uPulse.xy)-age*14.0)/1.5,2.0))*step(0.0,age)*(1.0-smoothstep(1.8,4.0,age));return hover*.14+ring*.26;}`;
  const towers = new THREE.ShaderMaterial({
    uniforms,
    vertexShader,
    fragmentShader:
      common +
      `void main(){vec3 n=normalize(vNormalW);float light=.34+.62*max(dot(n,normalize(vec3(-.5,.9,.4))),0.0);float h=vWorld.y*1.6;float density=max(fwidth(h),.0001);float dist=abs(fract(h+.5)-.5)/density;float contour=stroke(dist,.62,1.0,density,smoothstep(.3,.75,density));float wall=1.0-step(.5,abs(n.y));float coord=abs(n.x)>.5?vWorld.z:vWorld.x;vec2 win=fract(vec2(coord*1.25,vWorld.y*.9));float panes=step(.28,win.x)*step(win.x,.7)*step(.3,win.y)*step(win.y,.68)*wall;float seed=fract(sin(dot(floor(vec2(coord*1.25,vWorld.y*.9)),vec2(17.13,91.7)))*4317.3);panes*=step(.48,seed);vec3 color=vTint*(light*.82+.10)+mix(vTint,vec3(1.0),.45)*(contour*.18+panes*.28+energy());color+=vec3(.035,.012,.055)*step(.5,n.y);float fog=1.0-exp(-pow(length(cameraPosition-vWorld)*.0022,2.0));gl_FragColor=vec4(mix(color,vec3(.018,.012,.034),fog),1.0);#include <tonemapping_fragment>\n#include <colorspace_fragment>\n}`.replace(
        /;#include/g,
        ";\n#include",
      ),
  });
  const ground = new THREE.ShaderMaterial({
    uniforms,
    vertexShader,
    fragmentShader:
      common +
      `void main(){vec2 street=abs(mod(vWorld.xz+6.0,12.0)-6.0);float edge=6.0-max(street.x,street.y);float road=1.0-smoothstep(1.05,1.4,edge);vec3 color=mix(vec3(.035,.023,.058),vec3(.012,.011,.021),road);gl_FragColor=vec4(color,1.0);#include <tonemapping_fragment>\n#include <colorspace_fragment>\n}`.replace(
        /;#include/g,
        ";\n#include",
      ),
  });
  return { towers, ground, uniforms };
}
