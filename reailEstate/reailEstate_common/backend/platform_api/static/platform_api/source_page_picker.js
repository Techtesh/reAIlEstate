(() => {
  const root = document.querySelector('[data-page-picker]');
  if (!root) return;
  const preview = root.querySelector('[data-page-preview]');
  const base = preview.getAttribute('src');
  const selector = root.querySelector('[data-page-number]');
  const previous = root.querySelector('[data-page-prev]');
  const next = root.querySelector('[data-page-next]');
  const theater = root.querySelector('[data-preview-theater]');
  const theaterImage = root.querySelector('[data-preview-theater-image]');
  const theaterOpen = root.querySelector('[data-preview-theater-open]');
  const theaterClose = root.querySelector('[data-preview-theater-close]');
  const theaterFullscreen = root.querySelector('[data-preview-theater-fullscreen]');
  let current = 1;
  function show(page) {
    current = Math.max(1, Math.min(selector.options.length, page));
    selector.value = String(current);
    previous.disabled = current === 1;
    next.disabled = current === selector.options.length;
    preview.src = base.replace(/\/1\/$/, `/${current}/`);
    root.querySelector('[data-preview-label]').textContent = `Page ${current}`;
    theaterImage.src = preview.src;
    theaterImage.alt = `Expanded original source page ${current}`;
  }
  selector.addEventListener('change', () => show(Number(selector.value)));
  previous.addEventListener('click', () => show(current - 1));
  next.addEventListener('click', () => show(current + 1));
  root.addEventListener('change', event => {
    if (event.target.name !== 'pages' || !/^\d+$/.test(event.target.value)) return;
    show(Number(event.target.value));
  });
  theaterOpen.addEventListener('click', () => {
    theaterImage.src = preview.src;
    theater.showModal();
  });
  theaterClose.addEventListener('click', async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
    theater.close();
  });
  theaterFullscreen.addEventListener('click', async () => {
    if (theater.requestFullscreen) await theater.requestFullscreen();
  });
  theater.addEventListener('cancel', async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
})();
