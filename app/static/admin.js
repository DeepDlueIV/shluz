/* Локальные улучшения панели. Ничего не отправляется во внешние сервисы. */
(() => {
  "use strict";
  const popup = document.createElement("div");
  popup.id = "admin-help-popup";
  popup.className = "help-popup";
  popup.setAttribute("role", "tooltip");
  popup.hidden = true;
  document.body.append(popup);
  let active = null;
  let pinned = false;
  let timer;

  function closeHelp() {
    clearTimeout(timer);
    if (active) active.removeAttribute("aria-describedby");
    active = null;
    pinned = false;
    popup.hidden = true;
  }
  function positionHelp() {
    if (!active) return;
    const rect = active.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) {
      closeHelp();
      return;
    }
    const width = popup.offsetWidth, height = popup.offsetHeight;
    const left = Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, innerWidth - width - 12));
    let top = rect.bottom + 10;
    if (top + height > innerHeight - 12) top = Math.max(12, rect.top - height - 10);
    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;
  }
  function openHelp(button) {
    clearTimeout(timer);
    if (active !== button) closeHelp();
    active = button;
    popup.textContent = button.dataset.tooltip;
    button.setAttribute("aria-describedby", popup.id);
    popup.hidden = false;
    positionHelp();
  }
  function scheduleClose() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!pinned && active !== document.activeElement && !popup.matches(":hover")) closeHelp();
    }, 160);
  }
  document.querySelectorAll("[data-tooltip]").forEach(button => {
    // title остаётся резервной подсказкой при выключенном JavaScript.
    button.removeAttribute("title");
    button.addEventListener("mouseenter", () => openHelp(button));
    button.addEventListener("mouseleave", scheduleClose);
    button.addEventListener("focus", () => openHelp(button));
    button.addEventListener("blur", () => { pinned = false; scheduleClose(); });
    button.addEventListener("click", event => {
      event.preventDefault();
      if (active === button && pinned) closeHelp();
      else { openHelp(button); pinned = true; }
    });
  });
  popup.addEventListener("mouseenter", () => clearTimeout(timer));
  popup.addEventListener("mouseleave", scheduleClose);
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeHelp(); });
  document.addEventListener("click", event => {
    if (active && !active.contains(event.target) && !popup.contains(event.target)) closeHelp();
  });
  addEventListener("resize", positionHelp);
  document.addEventListener("scroll", positionHelp, true);

  document.querySelectorAll("[data-local-time]").forEach(element => {
    const date = new Date(element.getAttribute("datetime"));
    if (Number.isNaN(date.getTime())) return;
    element.textContent = new Intl.DateTimeFormat("ru-RU", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    }).format(date);
  });
  document.querySelectorAll("[data-timezone]").forEach(element => {
    element.textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
  });

  document.querySelectorAll("[data-copy]").forEach(button => {
    button.addEventListener("click", async () => {
      const value = document.getElementById(button.dataset.copy);
      if (!value) return;
      const status = button.parentElement.querySelector(".copy-status");
      try {
        if (!navigator.clipboard) throw new Error("unavailable");
        await navigator.clipboard.writeText(value.textContent.trim());
        if (status) status.textContent = "Ключ скопирован. Сохраните его в безопасном месте.";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(value);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        if (status) status.textContent = "Ключ выделен. Нажмите Ctrl+C (на Mac — ⌘C).";
      }
    });
  });

  document.querySelectorAll("[data-filter]").forEach(input => {
    const table = document.getElementById(input.dataset.filter);
    if (!table) return;
    const rows = [...table.querySelectorAll("tbody > tr[data-search]")];
    const counter = document.querySelector(`[data-filter-count="${input.dataset.filter}"]`);
    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase("ru-RU");
      let visible = 0;
      rows.forEach(row => {
        row.hidden = !row.dataset.search.toLocaleLowerCase("ru-RU").includes(query);
        if (!row.hidden) visible++;
      });
      if (counter) counter.textContent = query ? `Найдено: ${visible}` : "";
    });
  });
  function revealHash() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    if (!id.startsWith("user-")) return;
    const target = document.getElementById(id);
    if (!target) return;
    target.hidden = false;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
    requestAnimationFrame(() => target.scrollIntoView({block: "start"}));
  }
  addEventListener("hashchange", revealHash);
  revealHash();
})();
