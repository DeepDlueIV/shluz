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
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });

  window.addEventListener("pageshow", () => {
    // Даём браузеру закончить восстановление этой загрузки; последующую историю не блокируем.
    setTimeout(() => { history.scrollRestoration = previousRestoration; }, 0);
  }, { once: true });
})();`;
