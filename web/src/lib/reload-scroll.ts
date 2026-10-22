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

  window.addEventListener("pageshow", () => {
    // До первого layout браузер ещё может отложить восстановление прокрутки.
    resetTop();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      history.scrollRestoration = previousRestoration;
    }));
  }, { once: true });
})();`;
