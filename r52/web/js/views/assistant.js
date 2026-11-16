// R-52: помощник. Диалоги о товарах и ценах: ответы, карточки товаров, таблицы сравнения и действия с подтверждением.
import { openAnalysis } from "../analysis-panel.js";
import { productImage } from "../media.js";
import { h, icon, money, ago, randomKey } from "../dom.js";
import { get, post, query, describe } from "../api.js";
import { emit } from "../state.js";
import { empty, skel, toast, fail } from "../ui.js";
import { enter, motionOn } from "../motion.js";
import { go, productCard } from "../actions.js";
import { compareTable } from "../flows.js";
import { sourceLabel } from "../meta.js";

const CLOSED = new Set(["DONE", "EXPIRED", "CANCELLED", "FAILED"]);

export async function render(root, ctx) {
  const ask = typeof ctx.params.query.ask === "string" ? ctx.params.query.ask.trim() : "";
  const scopeQuery=String(ctx.params.query.scope||'');
  let contextIds=/^\d+(,\d+){0,11}$/.test(scopeQuery)?scopeQuery.split(',').map(Number).filter(n=>Number.isSafeInteger(n)&&n>0):[];
  const scopeBanner=h('div',{class:'city-chat-scope',hidden:!contextIds.length},h('span',{text:'Выбранные товары'}),h('a',{href:'#/map',text:'Вернуться на карту ↗'}));
  let sessionId = ctx.params.id && ctx.params.id !== "new" ? ctx.params.id : null;
  let sessions = [];
  let sending = false;
  let retryMessage = null;

  const sideList = h("div", { class: "stack" });
  const msgs = h("div", { class: "msgs", role: "log", "aria-live": "polite" });
  const suggest = h("div", { class: "suggest" });
  const input = h("textarea", { class: "textarea", rows: "1", placeholder: "Спросите о товарах и ценах", "aria-label": "Сообщение помощнику" });
  const send = h("button", { class: "btn primary", type: "button", title: "Отправить (Enter)", "aria-label": "Отправить" }, icon("send"));
  root.append(h("div", { class: "chat" },
    h("aside", { class: "card chat-side" },
      h("button", { class: "btn primary", type: "button", onClick: () => go("#/assistant/new") }, icon("plus"), "Новый диалог"),
      h("button", {class:"btn",type:"button",onClick:()=>openAnalysis([],'reference')},icon('assistant'),'Справка о характеристиках'),
      h("span", { class: "section-label", text: "Диалоги" }),
      sideList),
    h("section", { class: "card flush chat-main" }, scopeBanner, msgs, h("details",{class:"chat-commands"},h("summary",{text:"Быстрые команды"}),suggest), h("div", { class: "composer" }, input, send))));

  // Smooth following is opt-out on real user input, never on generated scroll events.
  let pinned=true,scrollFrame=0,revealFrame=0,revealDone=null,lastScrollTime=0;
  const nearBottom=()=>msgs.scrollHeight-msgs.scrollTop-msgs.clientHeight<64;
  const stopFollow=()=>{pinned=false;cancelAnimationFrame(scrollFrame);scrollFrame=0;};
  function follow(){
    if(!pinned||scrollFrame||!ctx.alive())return;
    lastScrollTime=0;
    const tick=(now)=>{
      scrollFrame=0;if(!pinned||!ctx.alive())return;
      const target=Math.max(0,msgs.scrollHeight-msgs.clientHeight),gap=target-msgs.scrollTop;
      const dt=lastScrollTime?Math.min(64,now-lastScrollTime):16;lastScrollTime=now;
      if(!motionOn()||Math.abs(gap)<.8){msgs.scrollTop=target;return;}
      msgs.scrollTop+=gap*(1-Math.exp(-dt/100));scrollFrame=requestAnimationFrame(tick);
    };scrollFrame=requestAnimationFrame(tick);
  }
  const scrollDown=(smooth)=>{pinned=true;if(smooth)follow();else msgs.scrollTop=msgs.scrollHeight;};
  msgs.addEventListener('wheel',e=>{if(e.deltaY<0)stopFollow();},{passive:true});
  msgs.addEventListener('touchstart',stopFollow,{passive:true});
  msgs.addEventListener('pointerdown',stopFollow);
  msgs.addEventListener('keydown',e=>{if(['ArrowUp','PageUp','Home'].includes(e.key))stopFollow();});
  msgs.addEventListener('scroll',()=>{if(!pinned&&msgs.scrollHeight-msgs.scrollTop-msgs.clientHeight<4){pinned=true;follow();}},{passive:true});
  const keepPinned=new ResizeObserver(follow);keepPinned.observe(msgs);
  ctx.cleanup(()=>{keepPinned.disconnect();cancelAnimationFrame(scrollFrame);cancelAnimationFrame(revealFrame);revealDone?.();});
  async function revealReply(message){
    const reply=messageEl(message),content=reply.querySelector('.content'),bubble=content?.querySelector('.bubble');
    if(!bubble){msgs.append(reply);follow();return;}
    const letters=Array.from(bubble.textContent),extras=[...content.children].filter(el=>el!==bubble),steps=[];
    for(const el of extras){
      el.remove();
      if(el.classList.contains('mini-cards')){
        const cards=[...el.children];el.replaceChildren();steps.push(()=>content.append(el));
        for(let i=0;i<cards.length;i+=2)steps.push(()=>{const pair=cards.slice(i,i+2);el.append(...pair);enter(pair,{y:8});});
      }else steps.push(()=>content.append(el));
    }
    bubble.textContent='';reply.setAttribute('aria-busy','true');msgs.append(reply);follow();
    if(!motionOn()){bubble.textContent=letters.join('');for(const step of steps)step();reply.removeAttribute('aria-busy');follow();return;}
    await new Promise(resolve=>{
      let start=0,shown=-1,nextStep=0;const duration=letters.length?Math.max(500,Math.min(4200,letters.length*9)):0;
      const done=()=>{revealFrame=0;revealDone=null;reply.removeAttribute('aria-busy');resolve();};revealDone=done;
      const tick=now=>{
        if(!ctx.alive()){done();return;}if(!start)start=now;
        const elapsed=now-start,count=duration?Math.min(letters.length,Math.floor(letters.length*elapsed/duration)):letters.length;
        if(count!==shown){shown=count;bubble.textContent=letters.slice(0,count).join('');follow();}
        if(elapsed>=duration){const due=Math.min(steps.length,Math.floor((elapsed-duration)/180)+1);while(nextStep<due){steps[nextStep++]();follow();}}
        if(count===letters.length&&nextStep===steps.length){done();return;}
        revealFrame=requestAnimationFrame(tick);
      };revealFrame=requestAnimationFrame(tick);
    });
    follow();
  }
  const autosize = () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  };

  function drawSessions() {
    if (!sessions.length) {
      sideList.replaceChildren(h("p", { class: "faint", text: "Диалогов пока нет." }));
      return;
    }
    sideList.replaceChildren(...sessions.map((s) => h("button", { class: ["sess", s.id === sessionId && "on"], type: "button", onClick: () => go(`#/assistant/${s.id}`) },
      icon("message"),
      h("span", { class: "ellipsis" }, s.title || "Без названия", h("small", { text: ago(s.updated_at || s.created_at) })))));
  }

  async function loadSessions() {
    try {
      const data = await get("/api/assistant/sessions");
      sessions = data.items || [];
    } catch {
      sessions = [];
    }
    if (ctx.alive()) drawSessions();
  }

  function hello() {
    msgs.replaceChildren(h("div", { class: "chat-hello" },
      h("div", { class: "avatar" }, icon("bot")),
      h("h3", { text: "Чем помочь?" }),
      h("p", { class: "muted", text: "Товары, цены, сравнения." })));
  }

  async function openSession(id) {
    msgs.replaceChildren(skel(56), skel(96));
    let data;
    try {
      data = await get(`/api/assistant/sessions/${encodeURIComponent(id)}`);
    } catch (error) {
      if (!ctx.alive()) return;
      if (error.status === 404) {
        sessionId = null;
        drawSessions();
        hello();
        return;
      }
      msgs.replaceChildren(empty({ icon: "warn", title: "Диалог не открылся", text: describe(error), action: { label: "Повторить", icon: "refresh", run: () => openSession(id) } }));
      return;
    }
    if (!ctx.alive()) return;
    const session = data.session || data;
    const messages = data.messages || session.messages || [];
    const scoped=messages.filter(m=>m.role==='user').at(-1)?.data?.context_product_ids;
    if(Array.isArray(scoped)){contextIds=scoped;scopeBanner.hidden=!contextIds.length;}
    ctx.setHeader("Помощник", session.title || "Диалог");
    if (messages.length) msgs.replaceChildren(...messages.map(messageEl));
    else hello();
    scrollDown(false);
  }

  async function sendText(text) {
    const value = String(text || "").trim();
    if (!value || sending) return;
    sending = true;
    send.disabled = true;
    let mine = null;
    try {
      if (!sessionId) {
        const created = await post("/api/assistant/sessions", { title: value.slice(0, 60) });
        sessionId = (created.session && created.session.id) || created.id;
        history.replaceState(null, "", `#/assistant/${sessionId}`);
        ctx.setHeader("Помощник", value.slice(0, 60));
      }
      if (msgs.querySelector(".chat-hello")) msgs.replaceChildren();
      mine = messageEl({ role: "user", text: value });
      const typing = h("div", { class: "msg bot" }, h("div", { class: "avatar" }, icon("bot")),
        h("div", { class: "content" }, h("div", { class: "bubble" }, h("span", { class: "typing", role:"status", "aria-label":"Помощник отвечает" }, h("i"), h("i"), h("i")))));
      msgs.append(mine, typing);
      enter([mine], { y: 8 });
      input.value = "";
      autosize();
      scrollDown(true);
      let result;const waitingAt=performance.now();
      try {
        result = await post(`/api/assistant/sessions/${encodeURIComponent(sessionId)}/messages`, { text: value, idempotency_key: (retryMessage && retryMessage.text === value ? retryMessage.key : (retryMessage = {text: value, key: randomKey()}).key), context_product_ids:contextIds }, { timeout: 90000 });
        if(motionOn())await new Promise(resolve=>setTimeout(resolve,Math.max(0,650-(performance.now()-waitingAt))));
      } finally {
        typing.remove();
      }
      if (!ctx.alive()) return;
      retryMessage = null;
      await revealReply(result.reply);
      loadSessions();
    } catch (error) {
      if (!ctx.alive()) return;
      if (mine) mine.remove();
      if (!input.value) input.value = value;
      fail(error, "Помощник не ответил");
    } finally {
      sending = false;
      send.disabled = false;
      if (ctx.alive()&&pinned) input.focus({preventScroll:true});
    }
  }

  function drawSuggestions(products) {
    const name = (p) => (p.brand && p.model ? `${p.brand} ${p.model}` : String(p.title || "").split(/\s+/).slice(0, 2).join(" "));
    const list = ["что нового", "статус"];
    if (products[0]) {
      const first = name(products[0]);
      list.push(`найди ${first}`, `история цен ${first}`);
      if (products[1]) list.push(`сравни ${first} и ${name(products[1])}`);
      if (products[0].lowest_minor) list.push(`следи за ${first} до ${Math.max(1, Math.round((products[0].lowest_minor * 0.9) / 100))}`);
    }
    suggest.replaceChildren(...list.map((text) => h("button", { class: "chip", type: "button", onClick: () => sendText(text) }, text)));
  }

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendText(input.value);
    }
  });
  send.addEventListener("click", () => sendText(input.value));

  await loadSessions();
  if (!ctx.alive()) return;
  if (!sessionId && ctx.params.id !== "new" && !ask && sessions.length) {
    sessionId = sessions[0].id;
    history.replaceState(null, "", `#/assistant/${sessionId}`);
    drawSessions();
  }
  if (sessionId) {
    await openSession(sessionId);
  } else {
    ctx.setHeader("Помощник", "Новый диалог");
    hello();
  }
  if (!ctx.alive()) return;
  get(`/api/products${query({ limit: 2, sort: "recent" })}`)
    .then((data) => { if (ctx.alive()) drawSuggestions(data.items || []); })
    .catch(() => { if (ctx.alive()) drawSuggestions([]); });
  if (ask) sendText(ask);
  else input.focus();
}

function messageEl(message) {
  if (!message) return h("div");
  if (message.role === "user") return h("div", { class: "msg user" }, h("div", { class: "bubble", text: message.text }));
  const data = message.data || {};
  const content = h("div", { class: "content" }, h("div", { class: "bubble", text: message.text }));
  if (Array.isArray(data.cards) && data.cards.length) content.append(h("div", { class: "mini-cards" }, data.cards.map(miniCard)));
  if (data.table) content.append(compareTable(data.table));
  if (Array.isArray(data.actions) && data.actions.length) content.append(h("div", { class: "row wrap" }, data.actions.map(actionButton)));
  if (data.llm) {
    const llm = data.llm;
    const label = llm.provider === "openrouter" && llm.used ? "GPT‑5 Mini · OpenRouter" : llm.used ? `Ollama · ${llm.model || "локальная модель"}` : "Ответ по правилам";
    const cost = llm.provider === "openrouter" && llm.calls ? (typeof llm.cost_usd === "number" ? ` · $${llm.cost_usd.toFixed(6)}` : " · стоимость пока неизвестна") : "";
    content.append(h("p", {class: "msg-note", text: label + (llm.coordinator ? " · разбор команды" : "") + cost}));
  }
  if (data.note) content.append(h("p", { class: "msg-note", text: data.note }));
  return h("div", { class: "msg bot" }, h("div", { class: "avatar" }, icon("bot")), content);
}

function miniCard(card) {
  const id = card.product_id || card.id;
  const price = card.lowest_minor ? money(card.lowest_minor, card.currency || "RUB") : card.price_text || "";
  const sub = [card.source ? sourceLabel(card.source) : null, card.last_seen_at ? `обновлено ${ago(card.last_seen_at)}` : null].filter(Boolean).join(" · ");
  const el=productCard({...card,id,title:card.title||`Товар №${id}`});el.classList.add('assistant-market-card');return el;
}

function actionButton(action) {
  const closed = CLOSED.has(action.status);
  const label = action.status === "DONE" ? "Выполнено" : closed ? "Недоступно" : action.label;
  const button = h("button", { class: ["btn sm", !closed && "primary"], type: "button", disabled: closed }, icon(action.status === "DONE" ? "check" : "wand"), label);
  if (closed) return button;
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.classList.add("busy");
    try {
      const result = await post(`/api/assistant/actions/${encodeURIComponent(action.token)}/confirm`, {});
      toast({ kind: "ok", title: "Готово", text: (result.result && result.result.text) || action.label, timeout: 3600 });
      button.classList.remove("primary");
      button.replaceChildren(icon("check"), "Выполнено");
      emit("data", { kind: /favorite/i.test(action.kind || "") ? "favorites" : "watches" });
    } catch (error) {
      if (error.status === 410 || error.status === 404) {
        button.replaceChildren(icon("clock"), "Срок истёк");
        toast({ kind: "warn", title: "Предложение устарело", text: "Повторите вопрос, чтобы получить новое.", timeout: 3600 });
      } else {
        button.disabled = false;
        fail(error, "Действие не выполнено");
      }
    } finally {
      button.classList.remove("busy");
    }
  });
  return button;
}
