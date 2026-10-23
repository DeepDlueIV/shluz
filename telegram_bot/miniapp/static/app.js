/* Управление остаётся в панели. sendData и answerWebAppQuery не используются. */
(() => {
  "use strict";
  const tg = window.Telegram?.WebApp;
  const content = document.querySelector("#content");
  const tabs = document.querySelector("#tabs");
  const modal = document.querySelector("#modal");
  let session = "", state = null, page = "home", working = false, toastTimer, pollTimer;
  const pages = [["home","⌂","Главная"],["dialogs","▤","Диалоги"],["models","◇","Модели"],["account","◎","Аккаунт"],["settings","⚙","Настройки"]];
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const add = (parent, ...nodes) => { nodes.forEach(n => parent.append(n)); return parent; };
  const button = (text, cls, action) => { const n = el("button", cls, text); n.type = "button"; n.addEventListener("click", action); return n; };
  const heading = (title, sub) => add(el("div","page-title"), el("h1","",title), el("p","",sub));
  const note = text => el("p","note",text);
  const money = value => value == null ? "Уточняется" : new Intl.NumberFormat("ru-RU", {style:"currency",currency:"USD",maximumFractionDigits:6}).format(Number(value));
  const fmt = value => value == null ? "—" : new Intl.NumberFormat("ru-RU").format(value);
  const reqId = () => crypto.randomUUID();
  function toast(text, error = false) { const box = document.querySelector("#status"); box.textContent = text; box.className = "visible" + (error ? " error" : ""); clearTimeout(toastTimer); toastTimer = setTimeout(() => box.className = "", error ? 9000 : 3500); }
  async function api(path, data) {
    const abort = new AbortController(); const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch("/miniapp/api/" + path, {method:data ? "POST":"GET", headers:{"Content-Type":"application/json", ...(session ? {Authorization:"Bearer " + session}: {})}, body:data ? JSON.stringify(data):undefined, signal:abort.signal, cache:"no-store", credentials:"omit"});
      const result = await response.json();
      if (!response.ok) { const error = new Error(typeof result.detail === "string" ? result.detail : "Действие не выполнено"); error.status = response.status; throw error; }
      return result;
    } catch (e) { if(e.name === "AbortError") throw new Error("Нет подтверждения. Обновите панель перед повторным действием."); throw e; }
    finally { clearTimeout(timeout); }
  }
  function fatal(message) { clearTimeout(pollTimer); session = ""; tabs.hidden = true; content.replaceChildren(add(el("div","loader"),el("h1","","Откройте меню в Telegram"),el("p","",message),button("Закрыть панель","secondary",close))); }
  function error(e) { if(e.status === 401) fatal(e.message); else toast(e.message || "Не удалось выполнить действие",true); }
  function close() { if(tg?.initData) tg.close(); else toast("Вернитесь в личный чат с ботом и нажмите «Меню»."); }
  document.querySelector("#close").onclick = close;
  document.querySelector(".brand").onclick = e => { e.preventDefault(); go("home"); };
  function busyCard() {
    if(!state.generation?.busy) return null;
    const card = add(el("div","card busy"),el("span","loader-dot"),el("p","","Модель готовит ответ"),button("Стоп","danger-button",() => mutate("stop",{target:state.generation.request_id},"Остановка запрошена")));
    return card;
  }
  function controlsDisabled(node) { if(state.generation?.busy) { node.disabled = true; node.title = "Сначала дождитесь ответа или нажмите Стоп"; } return node; }
  async function mutate(action, fields = {}, success = "Сохранено") {
    if(working) return false;
    working = true;
    try { state = await api("action",{action,request_id:reqId(),...fields}); render(); toast(success); return true; }
    catch(e) { error(e); return false; } finally { working = false; }
  }
  async function refresh() { try { state = await api("state"); render(); } catch(e) { error(e); } }
  function ask(title, message, onConfirm, initial) {
    document.querySelector("#modal-title").textContent = title;
    document.querySelector("#modal-text").textContent = message;
    const label = document.querySelector("#name-label"), input = document.querySelector("#name-input");
    label.hidden = initial === undefined; input.value = initial ?? "";
    const ok = document.querySelector("#confirm-button"); ok.textContent = initial === undefined ? "Подтвердить" : "Сохранить"; ok.disabled = false;
    modal.querySelector("form").onsubmit = async e => {
      e.preventDefault(); if(e.submitter?.value !== "ok") { modal.close(); return; }
      ok.disabled = true; const saved = await onConfirm(input.value); ok.disabled = false; if(saved) modal.close();
    };
    modal.showModal(); if(initial !== undefined) { input.focus(); input.select(); }
  }
  async function destructive(action, target = "") {
    try {
      const confirmation = await api("confirm",{action,target});
      const all = action === "forget";
      ask(all ? "Удалить все диалоги?" : action === "clear" ? "Очистить историю?" : "Удалить диалог?",
        "Действие нельзя отменить. Баланс и тариф сохранятся. Сообщения в самом Telegram останутся.",
        () => mutate(action,{target,confirmation:confirmation.confirmation},all ? "Все диалоги удалены" : "Готово"));
    } catch(e) { error(e); }
  }
  function row(title, sub, action, selected = false) {
    const n = button("","list-button"+(selected?" selected":""),action);
    add(n,add(el("span","label"),el("span","title",title),el("small","",sub)),el("span",selected?"check":"chevron",selected?"✓":"›")); return n;
  }
  function kv(title,value,css="") { return add(el("div","kv"),el("span","",title),el("strong",css,String(value))); }
  function renderHome() {
    const root = el("div","stack");
    add(root,heading("Ваше пространство","Общение — в чате. Всё остальное — здесь."));
    const current = add(el("section","card current"),el("div","eyebrow","Сейчас открыт"),el("h2","",state.active_dialog?.title || "Новый диалог"),add(el("div","pill"),el("span","dot"),el("span","",state.model || "Модель выберется при первом запросе")),button("Вернуться в чат  ↗","primary full",close));
    add(root,current,controlsDisabled(button("＋  Новый диалог","secondary full",()=>mutate("new",{},"Открыт новый диалог. Можно возвращаться в чат."))));
    const shortcuts = el("div","grid");
    for(const [symbol,title,sub,dest] of [["▤","Мои диалоги",`${state.dialogs.length} в истории`,"dialogs"],["◇","Выбор модели","Доступные вашему тарифу","models"]]) {
      const b=button("","card shortcut",()=>go(dest)); add(b,el("span","symbol",symbol),add(el("div"),el("h3","",title),el("small","",sub)));shortcuts.append(b);
    }
    root.append(shortcuts);
    const remaining = state.allowance?.requests?.remaining;
    const card=add(el("section","card stat-row"),add(el("div"),el("p","","Ваш тариф"),el("h3","",state.plan?.name || "Не назначен")),add(el("div"),el("strong","",state.plan?.billing_mode === "payg" ? money(state.wallet?.available_usd) : remaining == null ? "—" : fmt(remaining)),el("p","",state.plan?.billing_mode === "payg" ? "доступно" : "запросов осталось")));
    root.append(card); return root;
  }
  function renderDialogs() {
    const root=add(el("div"),heading("Диалоги","Выберите контекст для следующего сообщения."));
    add(root,controlsDisabled(button("＋ Новый диалог","primary full",()=>mutate("new",{},"Новый диалог открыт"))));
    const list=el("div","list section-gap");
    state.dialogs.forEach(d=>list.append(controlsDisabled(row(d.title,new Date(d.updated_at*1000).toLocaleDateString("ru-RU"),()=>mutate("activate",{target:d.id},"Диалог выбран. Продолжайте в чате."),d.id===state.active_dialog.id))));
    add(root,list,note("Переключение меняет контекст модели, но не перемещает сообщения в Telegram."),controlsDisabled(button("Удалить все диалоги","danger-button full section-gap",()=>destructive("forget")))); return root;
  }
  async function renderModels(root) {
    root.append(heading("Модели","Только модели вашего тарифа в Telegram."));
    const search=el("input"); search.type="search"; search.placeholder="Найти модель"; search.setAttribute("aria-label","Поиск модели");
    const list=el("div","list"); add(root,search,list);
    try {
      const data=await api("models");
      const draw=()=>{ list.replaceChildren(); const models=data.models.filter(id=>id.toLowerCase().includes(search.value.toLowerCase()));
        models.forEach(id=>list.append(controlsDisabled(row(id,id==="mock-chat"?"Тестовое эхо, не нейросеть":"Контекст текущего диалога сохранится",()=>mutate("model",{value:id},"Модель выбрана"),id===state.model))));
        if(!models.length) list.append(el("p","empty",data.models.length ? "Ничего не найдено" : "Для вашего тарифа пока нет доступных моделей."));
      }; search.oninput=draw; draw();
    } catch(e) { list.append(note("Не удалось загрузить список.")); error(e); }
  }
  async function renderAccount(root) {
    root.append(heading("Аккаунт","Лимиты и расходы без лишних деталей."));
    const card=add(el("section","card"),kv("Ваш ID",state.account.id,"id"),kv("Тариф",state.plan?.name || "Не назначен"));
    if(state.plan?.billing_mode === "payg") {
      add(card,kv("Баланс",money(state.wallet?.balance_usd)),kv("В резерве",money(state.wallet?.reserved_usd)),kv("Доступно",money(state.wallet?.available_usd)));
      if(state.wallet?.display_unit === "credits") card.append(kv("В кредитах",fmt(state.wallet.available_credits)));
    } else { const q=state.allowance?.requests; if(q) add(card,kv("Использовано запросов",fmt(q.used)),kv("Осталось",q.limit == null ? "Лимит не задан" : fmt(q.remaining))); }
    root.append(card);
    try {
      const data=await api("account"), sum=data.summary;
      add(root,add(el("div","section-head"),el("h2","","Расход за месяц · UTC")),add(el("section","card"),kv("Входные токены",fmt(sum.prompt_tokens)),kv("Выходные токены",fmt(sum.completion_tokens)),kv("Списано",money(sum.charged_usd))));
      if(sum.usage_unknown) root.append(note("Есть запросы, расход которых ещё уточняется."));
      root.append(add(el("div","section-head"),el("h2","","Тарифы")));
      const list=el("div","stack");
      data.plans.forEach(p=> { const card=add(el("section","card"),el("h3","",p.name),note(p.billing_mode==="payg" ? "По использованию" : `${money(p.monthly_price_usd)} / месяц`)); const pay=button("Оплата скоро","secondary full section-gap",()=>{});pay.disabled=true;add(card,pay);list.append(card); });
      if(!data.plans.length) list.append(note("Тарифы появятся после настройки."));
      add(root,list,note("Оплата ещё не подключена. Просмотр тарифа не меняет ваш доступ."));
    } catch(e) { error(e); }
  }
  async function exportDialog() {
    try { const data=await api("action",{action:"export",target:state.active_dialog.id,request_id:reqId()});
      const url=URL.createObjectURL(new Blob([data.text],{type:"text/plain;charset=utf-8"}));const a=el("a");a.href=url;a.download="NoClip-dialog.txt";a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
      const root=add(el("div"),heading("Экспорт диалога","Скачивание началось. Если Telegram его не поддержал, скопируйте текст ниже."));const text=el("textarea","export-text");text.value=data.text;text.readOnly=true;text.setAttribute("aria-label","Текст экспорта");add(root,text,button("Скопировать текст","primary full section-gap",async()=>{try{await navigator.clipboard.writeText(data.text);toast("Скопировано");}catch{text.focus();text.select();toast("Выделите и скопируйте текст средствами устройства.");}}),button("← К настройкам","secondary full section-gap",()=>go("settings")));content.replaceChildren(root);
    } catch(e) { error(e); }
  }
  function renderSettings() {
    const root=add(el("div","stack"),heading("Настройки диалога",`История: ${state.retention_days} дней.`));
    add(root,controlsDisabled(row("Название",state.active_dialog.title,()=>ask("Название диалога","До 60 символов. Название не отправляется модели.",name=>mutate("rename",{target:state.active_dialog.id,value:name},"Название сохранено"),state.active_dialog.title))));
    const card=add(el("section","card"),el("h3","","Стиль ответа"),note("Применяется к следующим сообщениям; модель и история сохраняются."));const styles=el("div","list section-gap");
    [["balanced","Обычный"],["short","Краткий"],["detailed","Подробный"]].forEach(([id,label])=>styles.append(controlsDisabled(row(label,"",()=>mutate("style",{value:id},"Стиль сохранён"),state.style===id))));add(card,styles);root.append(card);
    add(root,row("Экспорт диалога","Скачать историю текстовым файлом",exportDialog),controlsDisabled(row("Очистить историю","Оставить диалог, начать новый контекст",()=>destructive("clear",state.active_dialog.id))),controlsDisabled(button("Удалить этот диалог","danger-button full",()=>destructive("delete",state.active_dialog.id))),row("FAQ и поддержка","Ответы и информация о приватности",()=>go("faq")));return root;
  }
  function renderFAQ() {
    return add(el("div","stack privacy"),heading("FAQ и поддержка","Вопросы и ответы будут дополняться."),add(el("section","card"),el("h3","","Как продолжить разговор?"),el("p","","Закройте панель и напишите сообщение в обычном чате Telegram. Кнопка «Меню» всегда рядом с полем ввода.")),add(el("section","card"),el("h3","","Что происходит при смене диалога?"),el("p","","Модель получает контекст выбранного диалога. Сообщения в Telegram не перемещаются и не удаляются. Историю можно экспортировать.")),add(el("section","card"),el("h3","","Поддержка"),el("p","",state.support),el("small","id",`Ваш ID: ${state.account.id}`)),add(el("section","card"),el("h3","","Конфиденциальность"),el("p","",`История бота хранится до ${state.retention_days} дней для продолжения диалога. Контекст передаётся выбранной модели через шлюз. Сквозное шифрование в обычном чате с ботом не предоставляется.`),el("p","","Удаление в панели очищает контекст бота, но не сообщения в Telegram и не финансовый учёт. Срок удаления резервных копий определяется отдельно.")));
  }
  function render() {
    if(!state)return;
    const root=el("div");content.replaceChildren(root);content.setAttribute("aria-busy","false");
    if(state.generation?.busy) { const card=busyCard(); card.style.marginBottom="16px";root.append(card); }
    if(page==="home")root.append(renderHome());
    else if(page==="dialogs")root.append(renderDialogs());
    else if(page==="models")renderModels(root);
    else if(page==="account")renderAccount(root);
    else if(page==="settings")root.append(renderSettings());
    else root.append(renderFAQ());
    tabs.hidden=false;tabs.className="tabs";tabs.replaceChildren();
    pages.forEach(([id,icon,label])=>{const b=button("","",()=>go(id));b.setAttribute("aria-label",label);b.setAttribute("aria-current",page===id?"page":"false");add(b,el("span","tab-symbol",icon),el("span","",label));tabs.append(b);});
    if(page==="home")tg?.BackButton?.hide();else tg?.BackButton?.show();
    clearTimeout(pollTimer); if(state.generation?.busy) pollTimer=setTimeout(()=>{if(!document.hidden&&!modal.open)refresh();},5000);
  }
  function go(next) { if(!state)return;page=next;render();window.scrollTo({top:0,behavior:"instant"}); }
  function theme() {
    document.body.dataset.theme="dark";
    // Палитра NoClip не зависит от темы чата Telegram.
    try { tg?.setHeaderColor?.("#0c0d0f"); tg?.setBackgroundColor?.("#0c0d0f"); tg?.setBottomBarColor?.("#0c0d0f"); } catch (_) { /* Старый Telegram сохраняет собственную рамку. */ }
  }
  async function start() {
    theme();tg?.ready();tg?.expand();tg?.onEvent?.("themeChanged",theme);tg?.BackButton?.onClick(()=>go("home"));
    if(!tg?.initData) {fatal("Управление доступно только из кнопки «Меню» вашего бота. Здесь не нужно вводить пароли или ключи.");return;}
    try {const auth=await api("session",{init_data:tg.initData});session=auth.session;await refresh();}catch(e){fatal(e.message);}
  }
  document.addEventListener("visibilitychange",()=>{if(!document.hidden&&session&&!modal.open)refresh();});
  start();
})();
