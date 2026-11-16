// One aggregate marker and a paged catalogue. Never thousands of DOM offer cards.
import {h,money,icon} from './dom.js';
import {productImage} from './media.js';
import {cityPanelBounds} from './city-layout.js';
export function createCityPanels(host,onSelect,onHover){
 const layer=h('div',{class:'city-screen-overlay'}),cluster=h('button',{class:'btn sm city-products-button',type:'button',hidden:true}),
 search=h('input',{class:'input',type:'search',placeholder:'Найти в этой области','aria-label':'Поиск товаров области'}),
 list=h('div',{class:'city-catalog-list'}),counter=h('span',{'aria-live':'polite'}),
 prev=h('button',{class:'icon-btn',type:'button',text:'‹','aria-label':'Предыдущая страница'}),next=h('button',{class:'icon-btn',type:'button',text:'›','aria-label':'Следующая страница'}),
 close=h('button',{class:'icon-btn',type:'button',text:'×','aria-label':'Закрыть список'}),
 drawer=h('section',{class:'city-catalog',hidden:true,'aria-label':'Товары выбранной области'},h('header',{},h('b',{text:'Товары'}),close),search,list,h('footer',{},prev,counter,next));
 layer.append(drawer);host.append(layer);
 const workspace=host.closest('.city-workspace');
 const tools=workspace?.querySelector('.city-header-tools');
 (tools||layer).append(cluster);cluster.textContent='Товары · 0';
 let modelRef=null,scope=null,lastKey='',page=0,filtered=[],gallery=null,open=false,stats={cards:[],areas:[]},lastRect=null,clusterRect=null,lastTotal=-1;
 const PAGE=30;
 function fill(){
  filtered=(scope?.products||[]).filter(p=>(p.title||'').toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase()));
  page=Math.min(page,Math.max(0,Math.ceil(filtered.length/PAGE)-1));
  const shown=filtered.slice(page*PAGE,(page+1)*PAGE);
  list.replaceChildren(...shown.map(p=>h('button',{class:'city-catalog-product',type:'button',onClick:()=>onSelect?.(modelRef.byKey.get('product:'+p.id))},
   (p.photo_count||p.image_url?productImage(p,{className:'city-catalog-photo'}):h('span',{class:'city-catalog-empty','aria-hidden':'true'},icon('products'))),h('span',{},h('b',{text:p.title}),h('strong',{text:p.lowest_minor==null?'Цена неизвестна':money(p.lowest_minor,p.currency||'RUB')})))));
  if(!shown.length)list.append(h('p',{class:'muted',text:'Нет товаров'}));
  counter.textContent=filtered.length?`${page*PAGE+1}–${Math.min((page+1)*PAGE,filtered.length)} / ${filtered.length}`:'0';
  prev.disabled=page===0;next.disabled=(page+1)*PAGE>=filtered.length;
 }
 function toggle(value){open=value;if(open)workspace?.dispatchEvent(new CustomEvent('city-panel-request',{detail:{kind:'products'}}));drawer.hidden=!open;if(gallery)gallery.wrapper.hidden=open;cluster.setAttribute('aria-expanded',String(open));if(open){fill();search.focus({preventScroll:true});}}
 cluster.addEventListener('click',()=>toggle(!open));close.addEventListener('click',()=>toggle(false));
 search.addEventListener('input',()=>{page=0;fill();});prev.addEventListener('click',()=>{page--;fill();});next.addEventListener('click',()=>{page++;fill();});
 for(const el of [cluster,drawer])for(const event of ['pointerdown','pointerup','wheel','dblclick'])el.addEventListener(event,e=>e.stopPropagation());
 function bounds(){return cityPanelBounds(workspace,host);}
 const requested=event=>{
  if(event.detail?.kind!=='products')toggle(false);
  if(gallery&&event.detail?.kind!=='gallery')gallery.wrapper.hidden=true;
 };
 workspace?.addEventListener('city-panel-request',requested);
 function clear(){gallery?.wrapper.remove();gallery=null;toggle(false);scope=null;lastKey='';lastTotal=-1;cluster.hidden=true;lastRect=null;list.replaceChildren();stats={cards:[],areas:[]};}
 function removeGallery(){gallery?.wrapper.remove();gallery=null;}
 function setGallery(el){removeGallery();toggle(false);gallery={el,wrapper:h('div',{class:'city-marker city-gallery-marker'},el)};workspace?.dispatchEvent(new CustomEvent('city-panel-request',{detail:{kind:'gallery'}}));layer.append(gallery.wrapper);}
 function update({model,camera,selection,quiet}){
  if(!model)return;const w=host.clientWidth,height=host.clientHeight,b=bounds(quiet);
  if(model!==modelRef){clear();modelRef=model;}
  const current=selection?.kind==='product'?selection.parent:selection||model.root;
  if(current?.key!==lastKey){scope=current;lastKey=scope.key;page=0;search.value='';lastTotal=-1;if(open)fill();}
  if(lastTotal!==scope.products.length){lastTotal=scope.products.length;cluster.textContent=`Товары · ${lastTotal.toLocaleString('ru-RU')}`;}
  stats={cards:[],areas:[],total:lastTotal,page,pages:Math.ceil(lastTotal/PAGE),capacity:PAGE,mode:'cluster',gallery:null};
  clusterRect=null;cluster.hidden=false;cluster.disabled=!lastTotal;
  drawer.style.top=b.y+'px';drawer.style.bottom=Math.max(12,height-b.y-b.height)+'px';
  lastRect=open?{x:Math.max(12,w-Math.min(340,w-24)-12),y:b.y,width:Math.min(340,w-24),height:b.height}:null;
  if(gallery){
   const width=Math.min(w<700?w-24:370,b.width),hgt=Math.min(590,b.height),rect={x:w<700?12:w-width-16,y:b.y,width,height:hgt};
   const el=gallery.wrapper;Object.assign(el.style,{width:width+'px',height:hgt+'px',transform:`translate3d(${rect.x}px,${rect.y}px,0)`});stats.gallery=rect;
  }
 }
 return {setData(model){clear();modelRef=model;},update,setGallery,removeGallery,clear,stats:()=>stats,
 reserve(viewport){return open&&host.clientWidth>900?{...viewport,width:Math.max(240,viewport.width-360)}:viewport;},
 avoid:()=>({scope:scope?.children?.length?null:scope?.key,rects:[lastRect,clusterRect].filter(Boolean)}),
 destroy(){workspace?.removeEventListener('city-panel-request',requested);clear();cluster.remove();layer.remove();}};
}
