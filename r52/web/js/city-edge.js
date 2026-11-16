// Landscape the existing six-unit ground margin; do not change city bounds or taxonomy.
import * as THREE from './vendor/three.module.js';
export function cityEdge(model,own){
 const group=new THREE.Group(),p=model.root.polygon;if(!p?.length)return group;
 const cx=p.reduce((s,v)=>s+v.x,0)/p.length,cz=p.reduce((s,v)=>s+v.z,0)/p.length;
 const offset=(v,d)=>{const dx=v.x-cx,dz=v.z-cz,length=Math.hypot(dx,dz)||1;return {x:v.x+dx/length*d,z:v.z+dz/length*d};};
 function band(inner,outer,color,y){
  const vertices=[];for(let i=0;i<p.length;i++){const j=(i+1)%p.length,a=offset(p[i],inner),b=offset(p[j],inner),c=offset(p[j],outer),d=offset(p[i],outer);vertices.push(a.x,y,a.z,b.x,y,b.z,c.x,y,c.z,a.x,y,a.z,c.x,y,c.z,d.x,y,d.z);}
  const geo=own(new THREE.BufferGeometry());geo.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));geo.computeVertexNormals();group.add(new THREE.Mesh(geo,own(new THREE.MeshStandardMaterial({color,roughness:.95,side:THREE.DoubleSide}))));
 }
 band(.3,4.5,0x152e2a,.04);band(.5,1.7,0x4b4c60,.07);band(3.9,4.5,0x222139,.08);
 const sites=[];for(let i=0;i<p.length;i++){const a=offset(p[i],2.8),b=offset(p[(i+1)%p.length],2.8),n=Math.max(1,Math.floor(Math.hypot(b.x-a.x,b.z-a.z)/14));for(let j=0;j<n&&sites.length<240;j++){const t=(j+.5)/n;sites.push({x:a.x+(b.x-a.x)*t,z:a.z+(b.z-a.z)*t});}}
 const crown=new THREE.InstancedMesh(own(new THREE.IcosahedronGeometry(1,1)),own(new THREE.MeshStandardMaterial({color:0x31574a,roughness:1})),sites.length);
 const trunk=new THREE.InstancedMesh(own(new THREE.CylinderGeometry(.13,.18,1.3,5)),own(new THREE.MeshStandardMaterial({color:0x3d3340,roughness:1})),sites.length);
 const lights=new THREE.InstancedMesh(own(new THREE.CylinderGeometry(.10,.14,2.2,5)),own(new THREE.MeshStandardMaterial({color:0x9b8dba,emissive:0x57416e,emissiveIntensity:.35})),Math.ceil(sites.length/3));
 const d=new THREE.Object3D();let k=0;sites.forEach((s,i)=>{d.position.set(s.x,.7,s.z);d.scale.set(1,1,1);d.updateMatrix();trunk.setMatrixAt(i,d.matrix);d.position.y=2.1;d.scale.set(1.1,1.3,1.1);d.updateMatrix();crown.setMatrixAt(i,d.matrix);if(i%3===0){d.position.set(s.x+1,1.15,s.z);d.scale.set(1,1,1);d.updateMatrix();lights.setMatrixAt(k++,d.matrix);}});
 group.add(crown,trunk,lights);return group;
}
