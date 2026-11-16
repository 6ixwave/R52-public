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
    const refresh = number(s["autocollect.refresh_seconds"],60,2592000);
    const search = number(s["autocollect.discovery_seconds"],60,2592000);
    const cap = number(s["autocollect.new_limit"],1,200);
    const run = button("Запустить сейчас",()=>change(()=>post(path+"/run"),"Задача поставлена в очередь"));
    run.disabled = !active.checked;
    content.replaceChildren(h("section",{class:"card auto-card"},label("Автосбор включён",active),
      h("form",{class:"auto-form",onsubmit:e=>{
        e.preventDefault();change(()=>patch("/api/settings",{"autocollect.refresh_seconds":Number(refresh.value),
          "autocollect.discovery_seconds":Number(search.value),"autocollect.new_limit":Number(cap.value)}),"Интервалы сохранены");
      }},label("Обновление цен, с",refresh),label("Поиск, с",search),label("Лимит за цикл",cap),
        h("button",{type:"submit",class:"btn"},"Сохранить")),run,button("Обновить статус",()=>change(async()=>{},"Статус обновлён"))));
    for (const src of data.sources) {
      const toggle = h("input",{type:"checkbox",checked:!!src.enabled,onchange:e=>
        change(()=>patch(path+"/sources/"+src.source,{enabled:e.target.checked}),"Источник обновлён")});
      const r = src.last_result;
      content.append(h("section",{class:"card auto-card"},h("h2",{},names[src.source] || src.source),
        label("Источник включён",toggle), h("p",{class:"muted"},r ?
          new Date(src.last_run).toLocaleString("ru")+" · Найдено "+r.found+" · Обновлено "+r.refreshed+" · Ошибок "+r.errors+(r.reason?" · "+r.reason:"")
          :"Ожидает первого прогона"), ...data.targets.filter(t=>t.source===src.source).map(t=>targetForm(src.source,t)),
        h("details",{},h("summary",{},"Новая цель"),targetForm(src.source,null))));
    }
  }
  await load();
}

