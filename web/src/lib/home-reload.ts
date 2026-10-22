// Выполняется в <head> до гидратации: обновление главной всегда начинается с hero.
// Обычные ссылки с якорем, Back/Forward и /app сохраняют своё поведение.
export const homeReloadScript = String.raw`(() => {
  const navigation = performance.getEntriesByType('navigation')[0];
  if (location.pathname !== '/' || !navigation || navigation.type !== 'reload') return;

  const previousRestoration = history.scrollRestoration;
  history.scrollRestoration = 'manual';
  if (location.hash) history.replaceState(history.state, '', location.pathname + location.search);

  let interacted = false;
  const inputEvents = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
  const noteInteraction = () => { interacted = true; };
  const toTop = () => window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  inputEvents.forEach(name => window.addEventListener(name, noteInteraction, { passive: true }));
  toTop();

  const onPageShow = () => {
    if (!interacted && !location.hash) toTop();
    window.removeEventListener('pageshow', onPageShow);
    window.requestAnimationFrame(() => {
      inputEvents.forEach(name => window.removeEventListener(name, noteInteraction));
      history.scrollRestoration = previousRestoration;
    });
  };
  window.addEventListener('pageshow', onPageShow);
})();`;
