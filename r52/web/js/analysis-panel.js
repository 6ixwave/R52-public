// Switching tabs only reads saved results. Only «Новый разбор» can make a paid call.
import {h,icon,randomKey,money,dateTime} from './dom.js';
import {get,post,query,describe} from './api.js';
import {selectEl,modal,segmented} from './ui.js';
const NAMES={local:'Локально',mini:'GPT‑5 Mini',sonnet:'Sonnet 4.5'};
const CRITERIA={regular:'Обычная цена',lowest:'Минимальная цена',freshness:'Свежесть',completeness:'Полнота'};
const channel=r=>r.channel||(!(r.llm?.calls)?'local':r.model?.includes('sonnet')?'sonnet':'mini');
const cached=new Map();
function fold(title,body){return h('details',{class:'compact-details'},h('summary',{text:title}),body);}
function evidence(result,refs=null){const facts=(result.facts||[]).filter(f=>!refs||refs.includes(f.id));modal({title:'Основания вывода',width:700,body:h('div',{class:'evidence-list'},facts.map(f=>h('p',{text:f.text})))});}
function snapshots(result){
 if(result.snapshots?.length)return result.snapshots;
 return (result.facts||[]).filter(f=>/^p\d+-identity$/.test(f.id)).map(f=>{
  const id=f.product_ids[0],price=result.facts.find(x=>x.id===`p${id}-price`)?.text||'';
  return {id,title:f.text.replace(/^Товар №\d+:\s*/,''),price_text:price.replace(/^Минимальная показанная цена:\s*/,'').split('. Обычная')[0],fields:{}};
 });
}
function renderReport(result){
 const llm=result.llm||{},shots=snapshots(result),facts=result.facts||[];
 const root=h('div',{class:'report-result'});
 const info=h('div',{class:'report-meta'},h('span',{class:'badge',text:llm.used?NAMES[channel(result)]:'Локальные данные'}),
  result.created_at?h('time',{text:dateTime(result.created_at)}):null,
  llm.calls?h('span',{text:typeof llm.cost_usd==='number'?`$${llm.cost_usd.toFixed(6)}`:'Стоимость неизвестна'}):h('span',{text:'Бесплатно'}));
 root.append(info);
 if(result.kind==='reference')root.append(h('span',{class:'badge warn',text:'Общая справка · не данные товара'}),h('p',{class:'reference-answer',text:result.reference_text||'Справка не получена.'}));
 else {
  if(shots.length)root.append(h('div',{class:'report-metrics'},shots.map(p=>h('a',{class:'report-product',href:`#/products/${p.id}`},h('small',{text:p.title}),h('b',{text:p.price_text||'Цена неизвестна'}),h('span',{text:p.regular_minor!=null?`Обычная: ${money(p.regular_minor,p.currency||'RUB')}`:'Обычная цена не указана'})))));
  const texts=result.comments||[];
  if(llm.used&&texts.length)root.append(h('div',{class:'report-insights'},texts.map((c,i)=>h('article',{class:'report-insight'},h('span',{class:'insight-index',text:['Главное','Обратите внимание','Следующий шаг'][i]||'Вывод'}),h('p',{text:c.text}),h('button',{class:'btn ghost sm',type:'button',onClick:()=>evidence(result,c.fact_ids),text:'Основания'})))));
  else if(facts.length){
   const risks=facts.filter(f=>/-(conditional|jump)$/.test(f.id)||f.id.endsWith('-missing')&&!f.text.includes('нет среди')||f.id.endsWith('-fresh')&&/неизвестно|будущего/.test(f.text));
   const known=shots.reduce((n,p)=>n+Object.keys(p.fields||{}).length,0);
   root.append(h('div',{class:'report-overview'},h('div',{},h('b',{text:String(shots.length)}),h('span',{text:'карточек'})),h('div',{},h('b',{text:String(known)}),h('span',{text:'известных полей'})),h('div',{},h('b',{text:risks.length?'Есть пробелы':'Готово'}),h('span',{text:'качество данных'}))));
   const ranking=facts.filter(f=>/^(ranking-|freshness-order|completeness-order)/.test(f.id));
   if(ranking.length)root.append(h('div',{class:'report-insights'},ranking.map(f=>h('article',{class:'report-insight'},h('span',{class:'insight-index',text:'Результат сравнения'}),h('p',{text:f.text})))));
   if(risks.length)root.append(fold('Что проверить',h('ul',{class:'compact-list'},risks.map(f=>h('li',{text:f.text})))));
  }
 }
 if(llm.rejection_code||llm.error)root.append(h('div',{class:'callout warn',text:'Ответ ИИ не принят. Локальные данные сохранены.'}));
 if(facts.length)root.append(h('button',{class:'btn ghost sm',type:'button',onClick:()=>evidence(result),text:'Все сведения и ограничения'}));
 if(result.note&&(llm.rejection_code||llm.error||!facts.length&&!result.reference_text))root.append(fold('Подробности',h('p',{text:result.note})));
 return root;
}

export function analysisPanel({ids=[],kind='card'}={}){
 let tab=kind==='reference'?'mini':'local',items=[],total=0,selected=null,busy=false,seq=0;
 const mode=selectEl(kind==='reference'?[['reference','Справка']]:[['card','Разбор товара'],['compare','Сравнение'],['quality','Проверка данных'],['reference','Справка']],kind,{'aria-label':'Задача'});
 const criterion=selectEl(Object.entries(CRITERIA),'regular',{'aria-label':'Критерий'});
 const question=h('textarea',{class:'textarea',rows:'2',maxlength:'500',placeholder:'Что для вас важно?','aria-label':'Вопрос'});
 const optional=fold('Вопрос и приоритеты',question);
 const output=h('div',{class:'analysis-output',role:'status','aria-live':'polite'});
 const history=selectEl([['','Нет сохранённых результатов']],'',{'aria-label':'Сохранённые разборы'});
 const more=h('button',{class:'btn ghost sm',type:'button',text:'Ранее',hidden:true,onClick:()=>loadHistory(true)});
 const create=h('button',{class:'btn primary',type:'button'});
 const tabs=segmented(Object.entries(NAMES).map(([value,label])=>({value,label})),tab,value=>{tab=value;selected=null;update();},{label:'Сохранённые результаты'});
 function titleItem(x){return `${dateTime(x.created_at)}${x.criterion?' · '+(CRITERIA[x.criterion]||''):''}${x.cost_usd!=null?' · $'+x.cost_usd.toFixed(6):''}`;}
 function update(){
  const available=items.filter(x=>x.channel===tab);if(!available.some(x=>x.key===selected))selected=available[0]?.key||null;
  history.replaceChildren(...(available.length?available.map(x=>h('option',{value:x.key,text:titleItem(x)})):[h('option',{value:'',text:'Пока нет разборов'})]));history.value=selected||'';history.disabled=!available.length;
  create.textContent=tab==='local'?'Обновить бесплатно':`Новый разбор · ${NAMES[tab]}`;create.disabled=busy;
  criterion.hidden=mode.value!=='compare';
  if(mode.value==='reference'){optional.open=true;question.placeholder='Какой термин объяснить?';}
  if(selected)showSaved(selected);else output.replaceChildren(h('div',{class:'analysis-empty'},icon('assistant'),h('span',{text:tab==='local'?'Сводка по сохранённым сведениям':`Сохранённый ответ ${NAMES[tab]} появится здесь`})));
 }
 async function showSaved(key){
  const ticket=++seq;
  if(cached.has(key)){output.replaceChildren(renderReport(cached.get(key)));return;}
  output.replaceChildren(h('span',{class:'muted',text:'Открываю сохранённый разбор…'}));
  try{const result=await get('/api/assistant/analysis/history/'+encodeURIComponent(key));cached.set(key,result);if(ticket===seq)output.replaceChildren(renderReport(result));}
  catch(e){if(ticket===seq)output.replaceChildren(h('p',{class:'error',text:describe(e)}));}
 }
 async function loadHistory(append=false){
  const ticket=++seq;
  try{const data=await get('/api/assistant/analysis/history'+query({product_ids:(mode.value==='reference'?[]:ids).join(','),kind:mode.value,limit:100,offset:append?items.length:0}));
   if(ticket!==seq)return;
   items=append?[...items,...data.items]:data.items;total=data.total;more.hidden=items.length>=total;update();
  }catch(e){if(ticket===seq)output.replaceChildren(h('p',{class:'error',text:'Не удалось открыть историю: '+describe(e)}));}
 }
 async function run(){
  if(busy)return;
  const k=mode.value,useAI=tab!=='local';
  if(k==='card'&&ids.length!==1||k==='compare'&&ids.length<2||k==='quality'&&!ids.length){output.replaceChildren(h('p',{text:k==='compare'?'Выберите не менее двух товаров.':'Откройте подходящую карточку.'}));return;}
  if(k==='reference'&&!question.value.trim()){optional.open=true;question.focus();return;}
  if(k==='reference'&&!useAI){output.replaceChildren(h('p',{text:'Для справки выберите модель.'}));return;}
  const body={kind:k,product_ids:k==='reference'?[]:ids,criterion:criterion.value,question:question.value.trim(),advanced:tab==='sonnet',use_ai:useAI,paid_confirmed:useAI};
  if(useAI&&!window.confirm(`Создать НОВЫЙ платный разбор ${NAMES[tab]}? Сохранённые ответы можно читать бесплатно. Выбранные сведения и вопрос будут отправлены OpenRouter.${tab==='sonnet'?' Локальный резерв $0.50 — не цена и не потолок списания.':''}`))return;
  const signature=JSON.stringify(body);const storageKey='r52-analysis-pending:'+signature;
  let key;try{key=localStorage.getItem(storageKey)||randomKey();localStorage.setItem(storageKey,key);}catch{key=randomKey();}
  body.idempotency_key=key;busy=true;create.disabled=true;for(const b of tabs.querySelectorAll('button'))b.disabled=true;mode.disabled=true;output.replaceChildren(h('p',{text:useAI?'Разбор выполняется…':'Проверяю сведения…'}));
  try{const result=await post('/api/assistant/analysis',body,{timeout:90000});try{localStorage.removeItem(storageKey);}catch{}
   result.channel=tab;cached.set(key,result);selected=key;
   await loadHistory();
  }catch(e){output.replaceChildren(h('div',{class:'callout warn',text:describe(e)+' Повторное списание по этому запросу заблокировано.'}));}
  finally{busy=false;create.disabled=false;for(const b of tabs.querySelectorAll('button'))b.disabled=false;mode.disabled=false;}
 }
 history.addEventListener('change',()=>{selected=history.value;showSaved(selected);});create.addEventListener('click',run);
 mode.addEventListener('change',()=>{selected=null;items=[];loadHistory();});
 const root=h('section',{class:'card analysis-panel'},h('div',{class:'analysis-head'},h('h3',{},icon('assistant'),' Разбор'),mode),tabs,
  h('div',{class:'analysis-toolbar'},history,more,criterion),optional,output,h('div',{class:'analysis-bottom'},h('span',{class:'save-hint',text:'История сохраняется · просмотр бесплатный'}),create));
 loadHistory();return root;
}
export function openAnalysis(ids,kind='compare'){modal({title:kind==='quality'?'Проверка данных':'Разбор',width:900,body:analysisPanel({ids,kind})});}
