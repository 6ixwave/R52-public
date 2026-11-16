// Original local Canvas interpretation of the Topography/Sonar visual reference.
// Not the upstream React Bits shader. No runtime downloads or dependencies.
import {motionOn} from "./motion.js";
export function topography(host){return radar(host);}
export function radar(host){
 const canvas=document.createElement('canvas');canvas.className='terrain-canvas radar-canvas';canvas.setAttribute('aria-hidden','true');host.prepend(canvas);const c=canvas.getContext('2d');let w=0,h=0,frame=0,last=0,active=true,visible=true;
 function paint(t){if(!w||!h)return;const x=w/2,y=h*.48,r=Math.min(w*.43,h*.46);c.clearRect(0,0,w,h);const glow=c.createRadialGradient(x,y,0,x,y,r*1.35);glow.addColorStop(0,'#123c432b');glow.addColorStop(.8,'#0c253018');glow.addColorStop(1,'#08101b00');c.fillStyle=glow;c.fillRect(0,0,w,h);
 c.strokeStyle='#71e6e422';c.lineWidth=.7;for(let i=1;i<=6;i++){c.beginPath();c.arc(x,y,r*i/6,0,Math.PI*2);c.stroke();}for(let a=0;a<Math.PI*2;a+=Math.PI/6){c.beginPath();c.moveTo(x,y);c.lineTo(x+Math.cos(a)*r,y+Math.sin(a)*r);c.stroke();}
 for(let i=0;i<120;i++){const a=i/120*Math.PI*2;const outer=i%10===0;const k=r+(outer?8:3);c.strokeStyle=outer?'#75dedd75':'#5ac3d135';c.beginPath();c.moveTo(x+Math.cos(a)*r,y+Math.sin(a)*r);c.lineTo(x+Math.cos(a)*k,y+Math.sin(a)*k);c.stroke();}
 const a=t*.24-Math.PI/2;for(let i=0;i<45;i++){const start=a-i*.012;c.fillStyle=`rgba(57,222,201,${.065*(1-i/45)})`;c.beginPath();c.moveTo(x,y);c.arc(x,y,r,start-.014,start);c.closePath();c.fill();}c.strokeStyle='#6bf5d8a6';c.lineWidth=1;c.beginPath();c.moveTo(x,y);c.lineTo(x+Math.cos(a)*r,y+Math.sin(a)*r);c.stroke();
 const pulse=(t*.14)%1;c.beginPath();c.arc(x,y,r*pulse,0,Math.PI*2);c.strokeStyle=`rgba(80,218,209,${.25*(1-pulse)})`;c.stroke();c.fillStyle='#8bf9e3';c.beginPath();c.arc(x,y,3,0,Math.PI*2);c.fill();c.font='10px monospace';c.textAlign='center';c.fillStyle='#8ab7be88';c.fillText('N / 000',x,y-r-18);c.fillText('R52 · LOCAL SCAN',x,y+r+28);
 }
 function size(){const box=host.getBoundingClientRect();w=box.width;h=box.height;const d=Math.min(devicePixelRatio||1,1.5);canvas.width=Math.round(w*d);canvas.height=Math.round(h*d);c.setTransform(d,0,0,d,0,0);paint(0);}
 function tick(t){if(!active)return;if(motionOn()&&!document.hidden&&visible&&t-last>40){paint(t/1000);last=t;}frame=requestAnimationFrame(tick);}
 const ro=new ResizeObserver(size);ro.observe(host);const io=new IntersectionObserver(es=>visible=es[0].isIntersecting);io.observe(host);size();frame=requestAnimationFrame(tick);return {destroy(){active=false;cancelAnimationFrame(frame);ro.disconnect();io.disconnect();canvas.remove();}};
}
export function magneticBackground(host){if(!host)return;const canvas=document.createElement("canvas");canvas.className="magnetic-dots";host.append(canvas);const c=canvas.getContext("2d");let dots=[],w=0,h=0,last=0,mouse={x:-1000,y:-1000};
 function paint(){c.clearRect(0,0,w,h);for(const p of dots){const dx=mouse.x-p.x,dy=mouse.y-p.y,f=motionOn()?Math.max(0,1-Math.hypot(dx,dy)/165):0;p.px+=(p.x+dx*f*.26-p.px)*.12;p.py+=(p.y+dy*f*.26-p.py)*.12;c.fillStyle=`rgba(133,174,218,${.11+f*.35})`;c.beginPath();c.arc(p.px,p.py,1+f*.5,0,Math.PI*2);c.fill();}}
 function size(){w=innerWidth;h=innerHeight;const d=Math.min(devicePixelRatio||1,1.5);canvas.width=w*d;canvas.height=h*d;c.setTransform(d,0,0,d,0,0);dots=[];const gap=Math.max(32,Math.sqrt(w*h/1250));for(let y=gap/2;y<h;y+=gap)for(let x=gap/2;x<w;x+=gap)dots.push({x,y,px:x,py:y});paint();}
 function tick(t){if(!document.hidden&&t-last>32){paint();last=t;}requestAnimationFrame(tick);}window.addEventListener("resize",size);window.addEventListener("pointermove",e=>mouse={x:e.clientX,y:e.clientY},{passive:true});document.addEventListener("pointerleave",()=>mouse={x:-1000,y:-1000});size();requestAnimationFrame(tick);
}
