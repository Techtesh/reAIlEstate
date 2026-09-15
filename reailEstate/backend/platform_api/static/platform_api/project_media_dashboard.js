(() => {
  const initialize = root => {
    const slides = [...root.querySelectorAll('.carousel-slide')];
    const position = root.querySelector('[data-position]');
    const previous = root.querySelector('[data-prev]');
    const next = root.querySelector('[data-next]');
    if (!slides.length || !position || !previous || !next) return;

    let index = Math.max(0, slides.findIndex(slide => slide.classList.contains('active')));
    const show = requestedIndex => {
      slides[index].classList.remove('active');
      index = (requestedIndex + slides.length) % slides.length;
      slides[index].classList.add('active');
      position.textContent = `${index + 1} of ${slides.length}`;
    };

    previous.addEventListener('click', () => show(index - 1));
    next.addEventListener('click', () => show(index + 1));
    if (slides.length === 1) {
      previous.disabled = true;
      next.disabled = true;
    }
  };

  const page = document.querySelector('#content-main.media-tools');
  if (!page) return;
  page.querySelectorAll('[data-carousel]').forEach(initialize);

  // Only the current page's eligible flat cards participate, never cart drafts
  // or source floor plans. Each checkbox belongs to the standalone POST form.
  const bulkForm = page.querySelector('#layout-bulk-removal');
  if (!bulkForm) return;
  const selectAll = bulkForm.querySelector('[data-select-layouts]');
  const count = bulkForm.querySelector('[data-layout-selection-count]');
  const layouts = [...page.querySelectorAll('[data-layout-select]')].filter(input =>
    input.form === bulkForm && !input.disabled
  );
  const updateSelection = () => {
    const selected = layouts.filter(input => input.checked).length;
    if (count) count.textContent = `${selected} selected`;
    if (selectAll) {
      selectAll.checked = layouts.length > 0 && selected === layouts.length;
      selectAll.indeterminate = selected > 0 && selected < layouts.length;
      selectAll.disabled = layouts.length === 0;
    }
  };
  if (selectAll) selectAll.addEventListener('change', () => {
    layouts.forEach(input => { input.checked = selectAll.checked; });
    updateSelection();
  });
  layouts.forEach(input => input.addEventListener('change', updateSelection));
  window.addEventListener('pageshow', updateSelection);
  updateSelection();
})();
