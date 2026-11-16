// Progressive disclosure for explanatory prose, never for errors, prices or user messages.
import {h} from './dom.js';
export function compactInterface(root){
 for(const el of root.querySelectorAll('p.muted,p.faint,.empty p,.view-lead')){
  if(el.dataset.compact||el.closest('details,.analysis-panel,.msgs,.notif,.toast,.screen,[role="alert"],.error,.property-list,.modal')||el.querySelector('input,button,a,select'))continue;
  const text=el.textContent.trim();
  if(text.length<95||/\$|API|ошиб|недоступ|не удалось|истёк|будет удал|безвозврат|резервн|секрет|ключ|оплат/i.test(text))continue;
  el.dataset.compact='1';const details=h('details',{class:'compact-help'},h('summary',{'aria-label':'Дополнительная информация',text:'Подробнее'}));el.replaceWith(details);details.append(el);
 }
}
export function installCompactInterface(root){
 if(!root)return;let pending=false;
 const observer=new MutationObserver(()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;compactInterface(root);});});
 observer.observe(root,{childList:true,subtree:true});compactInterface(root);
}
