import { h } from "../dom.js";
import { get, post, put, patch, del, describe } from "../api.js";

const path = "/api/autocollect";
const label = (name, control) => h("label", {class:"auto-field"}, name, control);
const number = (value, min, max) => h("input", {type:"number", min, max, value});
const button = (name, action) => h("button", {type:"button", class:"btn", onclick: action}, name);
const names = {WB:"Wildberries", OZON:"Ozon", AVITO:"Авито", FARPOST:"FarPost", ALIEXPRESS:"AliExpress", ALIEXPRESS_RU:"AliExpress Россия"};

export async function render(root, ctx) {
  const status = h("p", {role:"status", class:"muted"});
  const content = h("div", {class:"auto-grid"});
  root.replaceChildren(status, content);
  let busy = false;
  async function change(action, message) {
    if (busy) return;
    busy = true;
    content.inert = true;
    try {
      await action();
      await load();
      status.textContent = message;
    } catch (error) { status.textContent = describe(error); }
    finally { busy = false; content.inert = false; }
  }
  function targetForm(source, target) {
    const kind = h("select", {value:target?.kind || "query"},
      ...[["query","Запрос"],["category","Категория"],["url","URL категории"]].map(([value,name])=>h("option",{value},name)));
    const value = h("input", {value:target?.value || "", required:true, maxlength:1000});
    const interval = number(target?.interval_seconds || "",60,2592000);
    interval.placeholder = "Общий";
    const limit = number(target?.item_limit ?? 5,1,50);
    const active = h("input",{type:"checkbox",checked:target ? !!target.enabled : true});
    const save = async event => {
      event.preventDefault();
      const data = {source,kind:kind.value,value:value.value,item_limit:Number(limit.value),
        interval_seconds:interval.value ? Number(interval.value) : null, enabled:active.checked};
      await change(()=>target ? put(path+"/targets/"+target.id,data) : post(path+"/targets",data),"Цель сохранена");
    };
    return h("form",{class:"auto-form",onsubmit:save}, label("Тип",kind),label("Что искать",value),
      label("Интервал, с",interval),label("За прогон",limit),label("Активна",active),
      h("button",{class:"btn",type:"submit"},target ? "Сохранить":"Добавить цель"),
      target && button("Удалить",()=>change(()=>del(path+"/targets/"+target.id),"Цель удалена")));
  }
  async function load() {
    const data = await get(path);
    if (!ctx.alive()) return;
    const s = data.settings;
    const active = h("input",{type:"checkbox",checked:s["autocollect.enabled"],
      onchange:e=>change(()=>patch("/api/settings",{"autocollect.enabled":e.target.checked}),"Настройка сохранена")});
    const broad = h("input",{type:"checkbox",checked:s["autocollect.broad_search"],
      onchange:e=>change(()=>patch("/api/settings",{"autocollect.broad_search":e.target.checked}),"Режим поиска сохранён")});
    const refresh = number(s["autocollect.refresh_seconds"],60,2592000);
    const search = number(s["autocollect.discovery_seconds"],60,2592000);
    const cap = number(s["autocollect.new_limit"],1,200);
    const run = button("Запустить сейчас",()=>change(()=>post(path+"/run"),"Задача поставлена в очередь"));
    run.disabled = !active.checked;
    content.replaceChildren(h("section",{class:"card auto-card"},label("Автосбор включён",active),
      label("Широкий поиск без ввода запросов",broad),
      h("form",{class:"auto-form",onsubmit:e=>{
        e.preventDefault();change(()=>patch("/api/settings",{"autocollect.refresh_seconds":Number(refresh.value),
          "autocollect.discovery_seconds":Number(search.value),"autocollect.new_limit":Number(cap.value)}),"Интервалы сохранены");
      }},label("Обновление цен, с",refresh),label("Поиск, с",search),label("Лимит за цикл",cap),
        h("button",{type:"submit",class:"btn"},"Сохранить")),run,button("Обновить статус",()=>change(async()=>{},"Статус обновлён"))));
    for (const src of data.sources) {
      const toggle = h("input",{type:"checkbox",checked:!!src.enabled,onchange:e=>
        change(()=>patch(path+"/sources/"+src.source,{enabled:e.target.checked}),"Источник обновлён")});
      const r = src.last_result;
      const catalog = data.targets.filter(t=>t.source===src.source && t.origin==="CATALOG" && t.enabled)
        .sort((a,b)=>(a.last_run || "").localeCompare(b.last_run || "") || a.id-b.id);
      content.append(h("section",{class:"card auto-card"},h("h2",{},names[src.source] || src.source),
        label("Источник включён",toggle), h("p",{class:"muted"},r ?
          new Date(src.last_run).toLocaleString("ru")+" · Передано на импорт: новых "+r.found+", обновлений "+r.refreshed+" · Ошибок "+r.errors+(r.reason?" · "+r.reason:"")
          :"Ожидает первого прогона"),
        h("p",{class:"muted"},"В базе наблюдений сборщика: "+src.imported_observations+" · Ожидают импорта: "+src.pending_import),
        src.retry_after && h("p",{class:"muted"},"Следующая автоматическая попытка не раньше: "+new Date(src.retry_after).toLocaleString("ru")),
        r?.diagnostic && h("details",{},h("summary",{},"Журнал прогона"),h("p",{},r.diagnostic)),
        s["autocollect.broad_search"] && catalog.length > 0 && h("p",{class:"muted"},
          "Следующий поиск: "+catalog[0].value+" · Страница "+catalog[0].page),
        r?.search && h("p",{class:"muted"},"Последний поиск: "+r.search.query+" · Страница "+r.search.page),
        catalog.length > 0 && h("details",{},h("summary",{},"Категории широкого поиска · "+catalog.length),
          h("p",{},catalog.map(t=>t.value).join(" · "))),
        ...data.targets.filter(t=>t.source===src.source && t.origin!=="CATALOG").map(t=>targetForm(src.source,t)),
        h("details",{},h("summary",{},"Новая цель"),targetForm(src.source,null))));
    }
  }
  await load();
}

