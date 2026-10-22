// Выполняется до гидратации. Только обновление главной сбрасывает позицию и старый якорь.
export const homeReloadScript = String.raw`(() => {
  const navigation = performance.getEntriesByType('navigation')[0];
  if (location.pathname !== '/' || !navigation || navigation.type !== 'reload') return;

  const previousRestoration = history.scrollRestoration;
  history.scrollRestoration = 'manual';
  if (location.hash) history.replaceState(history.state, '', location.pathname + location.search);

  let interacted = false;
  let shown = false;
  const inputEvents = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
  const navigationEvents = ['pagehide', 'hashchange', 'popstate'];
  const release = () => {
    inputEvents.forEach(name => window.removeEventListener(name, noteInteraction));
    navigationEvents.forEach(name => window.removeEventListener(name, release));
    window.removeEventListener('pageshow', onPageShow);
    history.scrollRestoration = previousRestoration;
  };
  const noteInteraction = () => {
    interacted = true;
    if (shown) release();
  };
  const toTop = () => window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  const onPageShow = () => {
    shown = true;
    if (!interacted && !location.hash) toTop();
    window.removeEventListener('pageshow', onPageShow);
  };
  // Не возвращаем auto по таймеру: позднее восстановление браузера иначе может победить сброс.
  inputEvents.forEach(name => window.addEventListener(name, noteInteraction, { passive: true }));
  navigationEvents.forEach(name => window.addEventListener(name, release));
  window.addEventListener('pageshow', onPageShow);
  toTop();
})();`;
