(() => {
  const root = document.querySelector('[data-page-cropper]');
  if (!root) return;
  const saveForm = root.querySelector('[data-crop-save]');
  const documentForm = root.querySelector('[data-document-navigation]');
  const pageForm = root.querySelector('[data-page-navigation]');
  let dirty = false;
  let leaving = false;
  const confirmLeave = () => !dirty || window.confirm('This page has unsaved crops. Leave and discard those selections? Saved cart drafts are kept.');
  window.addEventListener('beforeunload', event => {
    if (dirty && !leaving) { event.preventDefault(); event.returnValue = ''; }
  });
  for (const form of [documentForm, pageForm].filter(Boolean)) {
    form.addEventListener('submit', event => {
      if (!confirmLeave()) event.preventDefault(); else leaving = true;
    });
  }
  root.querySelectorAll('a:not([target="_blank"])').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!confirmLeave()) event.preventDefault(); else leaving = true;
  }));
  const documentSelector = documentForm.querySelector('select');
  const loadedDocument = documentSelector.value;
  documentSelector.addEventListener('change', () => {
    documentForm.requestSubmit();
    if (!leaving) documentSelector.value = loadedDocument;
  });
  if (!saveForm) return;
  const pageSelector = pageForm.querySelector('select');
  const loadedPage = pageSelector.value;
  const navigatePage = value => {
    pageSelector.value = String(value);
    pageForm.requestSubmit();
    if (!leaving) pageSelector.value = loadedPage;
  };
  pageSelector.addEventListener('change', () => navigatePage(pageSelector.value));
  pageForm.querySelector('[data-page-prev]').addEventListener('click', () => navigatePage(Number(loadedPage) - 1));
  pageForm.querySelector('[data-page-next]').addEventListener('click', () => navigatePage(Number(loadedPage) + 1));

  const image = root.querySelector('[data-crop-source]');
  const canvas = root.querySelector('[data-crop-canvas]');
  const context = canvas.getContext('2d');
  const input = saveForm.elements.regions_json;
  const rotationInput = saveForm.elements.rotation_degrees;
  const list = root.querySelector('[data-crop-list]');
  const status = root.querySelector('[data-crop-status]');
  const saveButton = root.querySelector('[data-save-crops]');
  const limit = Number(root.dataset.cartRemaining);
  let regions = [];
  try { regions = JSON.parse(input.value || '[]'); } catch { /* Invalid server form input. */ }
  if (!Array.isArray(regions)) regions = [];
  regions = regions.filter(region => region && typeof region === 'object');
  let rotation = Number(rotationInput.value) || 0;
  let start = null;
  let draft = null;
  let ready = false;
  dirty = regions.length > 0;

  function point(event) {
    const box = canvas.getBoundingClientRect();
    const clamp = value => Math.max(0, Math.min(1, value));
    return {x: clamp((event.clientX - box.left) / box.width), y: clamp((event.clientY - box.top) / box.height)};
  }
  function rectangle(end) {
    return {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y)};
  }
  function paint() {
    rotationInput.value = rotation;
    root.querySelector('[data-rotation-label]').textContent = `Rotation: ${rotation}°`;
    saveButton.disabled = !ready || !regions.length || regions.length > limit;
    if (!ready) return;
    const quarter = Math.abs(rotation) % 180 === 90;
    canvas.width = quarter ? image.naturalHeight : image.naturalWidth;
    canvas.height = quarter ? image.naturalWidth : image.naturalHeight;
    context.save(); context.translate(canvas.width / 2, canvas.height / 2);
    context.rotate(-rotation * Math.PI / 180);
    context.drawImage(image, -image.naturalWidth / 2, -image.naturalHeight / 2); context.restore();
    [...regions, ...(draft ? [draft] : [])].forEach((region, index) => {
      const x = region.x * canvas.width, y = region.y * canvas.height;
      const w = region.width * canvas.width, h = region.height * canvas.height;
      context.fillStyle = 'rgba(255,140,0,.16)'; context.fillRect(x, y, w, h);
      context.strokeStyle = '#d97706'; context.lineWidth = Math.max(3, canvas.width / 300);
      context.strokeRect(x, y, w, h);
      context.font = `${Math.max(14, canvas.width / 65)}px sans-serif`;
      context.fillStyle = '#111'; context.fillText(String(index + 1), x + 6, y + Math.max(20, canvas.width / 55));
    });
  }
  function sync() {
    input.value = JSON.stringify(regions);
    list.replaceChildren();
    regions.forEach((region, index) => {
      const item = document.createElement('li');
      const label = document.createElement('input'); label.value = region.label || `Unit ${index + 1}`;
      label.maxLength = 64; label.setAttribute('aria-label', `Crop ${index + 1} label`);
      label.addEventListener('input', () => {
        region.label = label.value; dirty = true; input.value = JSON.stringify(regions);
      });
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button';
      remove.textContent = 'Remove'; remove.addEventListener('click', () => {
        regions.splice(index, 1); dirty = regions.length > 0; sync();
      });
      item.append(label, ' ', remove); list.appendChild(item);
    });
    paint();
  }
  canvas.addEventListener('pointerdown', event => {
    if (!ready || event.button !== 0 || start) return;
    start = point(event); canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => { if (start) { draft = rectangle(point(event)); paint(); } });
  canvas.addEventListener('pointerup', event => {
    if (!start) return;
    const region = rectangle(point(event)); start = null; draft = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    const area = region.width * region.height;
    if (area < .01 || area > .7 || regions.length >= limit) {
      status.textContent = `Choose an individual layout (1–70% of the page). ${limit} cart slot(s) available.`;
    } else {
      regions.push({...region, label: `Unit ${String(regions.length + 1).padStart(2, '0')}`, floors: []});
      dirty = true; status.textContent = `${regions.length} crop(s) selected. Save before changing pages.`;
    }
    sync();
  });
  canvas.addEventListener('pointercancel', () => { start = null; draft = null; paint(); });
  function clear() { regions = []; start = null; draft = null; dirty = false; sync(); }
  function rotate(delta) {
    if (regions.length && !window.confirm('Rotating clears the unsaved crop selections on this page. Continue?')) return;
    rotation = ((rotation + delta + 540) % 360) - 180; clear();
  }
  root.querySelector('[data-rotate-left]').addEventListener('click', () => rotate(90));
  root.querySelector('[data-rotate-right]').addEventListener('click', () => rotate(-90));
  root.querySelector('[data-clear-crops]').addEventListener('click', () => {
    if (!regions.length || window.confirm('Discard the unsaved crops on this page?')) clear();
  });
  saveForm.addEventListener('submit', event => {
    if (!ready || !regions.length || regions.length > limit) {
      event.preventDefault(); status.textContent = 'Load the source page and select crops within the remaining cart limit.';
    } else { leaving = true; saveButton.disabled = true; }
  });
  function loaded() {
    ready = image.naturalWidth > 0;
    status.textContent = ready ? 'Drag a rectangle around each layout, then edit its label.' : 'Source image unavailable; reload the document.';
    paint();
  }
  image.addEventListener('load', loaded);
  image.addEventListener('error', () => { ready = false; status.textContent = 'Source preview is stale or unavailable. Reload the document before cropping.'; paint(); });
  if (image.complete) loaded();
  sync();
})();