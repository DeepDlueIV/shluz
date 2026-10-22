// Выполняется в head до восстановления прокрутки и гидратации приложения.
// Только reload главной: обычные якоря, история и маршрут /app не меняются.
export const homepageReloadScrollScript = `(() => {
  if (window.location.pathname !== "/") return;
  const navigation = performance.getEntriesByType("navigation")[0];
  if (!navigation || navigation.type !== "reload") return;

  const previousRestoration = history.scrollRestoration;
  history.scrollRestoration = "manual";
  if (window.location.hash) {
    history.replaceState(history.state, "", window.location.pathname + window.location.search);
  }
  const resetTop = () => window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  resetTop();

  // Поздняя загрузка шрифта не должна перехватывать уже начатое действие пользователя.
  let interacted = false;
  const interactionEvents = ["pointerdown", "wheel", "touchstart", "keydown"];
  const onInteraction = () => { interacted = true; };
  interactionEvents.forEach(type => window.addEventListener(type, onInteraction, { passive: true }));

  const finish = () => {
    if (!interacted) resetTop();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      history.scrollRestoration = previousRestoration;
      interactionEvents.forEach(type => window.removeEventListener(type, onInteraction));
    }));
  };
  window.addEventListener("pageshow", () => {
    // Ждём окончательных размеров текста, а не фиксированной задержки таймера.
    if (document.fonts) document.fonts.ready.then(finish, finish);
    else finish();
  }, { once: true });
})();`;
