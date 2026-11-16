// Shared HUD geometry: side panels occupy one slot between the real header and footer.
export function cityPanelBounds(workspace,host=workspace){
 const origin=host.getBoundingClientRect(),w=host.clientWidth,h=host.clientHeight,quiet=workspace.classList.contains('city-quiet');
 let top=12,bottom=h-12;
 if(!quiet){
  for(const selector of ['.city-header','.city-toolbar']){
   const el=workspace.querySelector(selector);if(!el||el.hidden||getComputedStyle(el).display==='none')continue;
   top=Math.max(top,el.getBoundingClientRect().bottom-origin.top+12);
  }
  const footer=workspace.querySelector('.city-context');
  if(footer&&!footer.hidden&&getComputedStyle(footer).display!=='none')bottom=Math.min(bottom,footer.getBoundingClientRect().top-origin.top-12);
 }
 top=Math.max(8,Math.min(top,h-48));bottom=Math.max(top+24,Math.min(h-8,bottom));
 return {x:12,y:top,width:Math.max(0,w-24),height:Math.max(24,bottom-top),bottom:Math.max(8,h-bottom)};
}
export function installCityLayout(workspace){
 let frame=0,stopped=false;
 const layout=()=>{frame=0;if(stopped)return;const header=workspace.querySelector('.city-header'),toolbar=workspace.querySelector('.city-toolbar');if(header&&toolbar&&!workspace.classList.contains('city-quiet'))toolbar.style.top=(header.getBoundingClientRect().bottom-workspace.getBoundingClientRect().top+12)+'px';const b=cityPanelBounds(workspace);
  workspace.style.setProperty('--city-panel-top',b.y+'px');workspace.style.setProperty('--city-panel-bottom',b.bottom+'px');
 };
 const schedule=()=>{if(!frame&&!stopped)frame=requestAnimationFrame(layout);};
 const size=new ResizeObserver(schedule);for(const el of [workspace,...workspace.querySelectorAll('.city-header,.city-toolbar,.city-context')])size.observe(el);
 const changes=new MutationObserver(schedule);changes.observe(workspace,{subtree:true,attributes:true,attributeFilter:['hidden','class','data-selection'],childList:true});
 window.addEventListener('resize',schedule);window.visualViewport?.addEventListener('resize',schedule);layout();
 return ()=>{stopped=true;cancelAnimationFrame(frame);size.disconnect();changes.disconnect();window.removeEventListener('resize',schedule);window.visualViewport?.removeEventListener('resize',schedule);};
}
