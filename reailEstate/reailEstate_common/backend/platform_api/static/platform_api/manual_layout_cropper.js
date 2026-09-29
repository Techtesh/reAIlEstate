(() => {
  const root = document.querySelector('[data-manual-cropper]');
  if (!root) return;
  const image = root.querySelector('[data-crop-source]');
  const canvas = root.querySelector('[data-crop-canvas]');
  const context = canvas.getContext('2d');
  const list = root.querySelector('[data-crop-list]');
  const input = root.querySelector('input[name="regions_json"]');
  const rotationInput = root.querySelector('input[name="rotation_degrees"]');
  const rotationLabel = root.querySelector('[data-rotation-label]');
  const workspace = root.querySelector('.crop-workspace');
  const stage = root.querySelector('[data-crop-stage]');
  const theater = root.querySelector('[data-crop-theater]');
  const fullscreen = root.querySelector('[data-crop-fullscreen]');
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  list.after(status);
  let regions = [];
  try { regions = JSON.parse(input.value || '[]'); } catch { /* Empty form. */ }
  if (!Array.isArray(regions)) regions = [];
  let rotation = Number(rotationInput.value) || 0;
  let start = null;
  let draft = null;
  canvas.style.touchAction = 'none';

  function point(event) {
    const bounds = canvas.getBoundingClientRect();
    const clamp = v => Math.max(0, Math.min(1, v));
    return {x: clamp((event.clientX - bounds.left) / bounds.width),
      y: clamp((event.clientY - bounds.top) / bounds.height)};
  }

  function paint() {
    if (!image.naturalWidth) return;
    const quarter = Math.abs(rotation) % 180 === 90;
    canvas.width = quarter ? image.naturalHeight : image.naturalWidth;
    canvas.height = quarter ? image.naturalWidth : image.naturalHeight;
    context.save();
    context.translate(canvas.width / 2, canvas.height / 2);
    context.rotate(-rotation * Math.PI / 180);
    context.drawImage(image, -image.naturalWidth / 2, -image.naturalHeight / 2);
    context.restore();
    for (const region of [...regions, ...(draft ? [draft] : [])]) {
      const x = region.x * canvas.width, y = region.y * canvas.height;
      const w = region.width * canvas.width, h = region.height * canvas.height;
      context.fillStyle = 'rgba(255, 140, 0, .12)';
      context.fillRect(x, y, w, h);
      context.strokeStyle = '#d97706';
      context.lineWidth = Math.max(3, canvas.width / 300);
      context.strokeRect(x, y, w, h);
    }
    rotationInput.value = rotation;
    rotationLabel.textContent = `Rotation: ${rotation}°`;
  }

  function sync() {
    list.replaceChildren();
    regions.forEach((region, index) => {
      const item = document.createElement('li');
      const name = document.createElement('input');
      name.value = region.label; name.maxLength = 64;
      name.setAttribute('aria-label', `Crop ${index + 1} name`);
      name.addEventListener('input', () => {
        region.label = name.value || `Unit ${String(index + 1).padStart(2, '0')}`;
        input.value = JSON.stringify(regions);
      });
      const remove = document.createElement('button');
      remove.type = 'button'; remove.textContent = 'Remove'; remove.className = 'button';
      remove.addEventListener('click', () => { regions.splice(index, 1); sync(); });
      item.append(name, ' ', remove); list.appendChild(item);
    });
    input.value = JSON.stringify(regions);
    paint();
  }

  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !image.naturalWidth) return;
    start = point(event); canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (!start) return;
    const end = point(event);
    draft = {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y)};
    paint();
  });
  canvas.addEventListener('pointerup', event => {
    if (!start) return;
    const end = point(event);
    const region = {x: Math.min(start.x, end.x), y: Math.min(start.y, end.y),
      width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y)};
    start = null; draft = null;
    const area = region.width * region.height;
    if (area < .01 || area > .7 || regions.length >= 20) {
      status.textContent = 'Select an individual dwelling (1–70% of the page), at most 20 draft crops.';
    } else {
      regions.push({...region, label: `Unit ${String(regions.length + 1).padStart(2, '0')}`,
        confidence: 0, evidence: 'reviewer_selected_boundary', floors: []});
      status.textContent = `${regions.length} crop(s) selected.`;
    }
    sync();
  });
  canvas.addEventListener('pointercancel', () => { start = null; draft = null; paint(); });
  function rotate(delta) {
    if (regions.length && !window.confirm('Rotating clears the current crop selections. Continue?')) return;
    rotation = ((rotation + delta + 540) % 360) - 180;
    regions = []; start = null; draft = null; sync();
  }
  root.querySelector('[data-rotate-left]').addEventListener('click', () => rotate(90));
  root.querySelector('[data-rotate-right]').addEventListener('click', () => rotate(-90));
  root.querySelector('[data-clear-crops]').addEventListener('click', () => { regions = []; sync(); });
  root.querySelector('form').addEventListener('submit', event => {
    if (!regions.length) { event.preventDefault(); status.textContent = 'Draw a crop before saving.'; }
  });
  image.addEventListener('load', paint);
  image.addEventListener('error', () => { status.textContent = 'Source image could not be loaded.'; });
  sync();
  theater.addEventListener('click', () => {
    const active = workspace.classList.toggle('is-theater');
    theater.setAttribute('aria-pressed', String(active));
    theater.setAttribute('title', active ? 'Exit theater mode' : 'Theater mode');
  });
  fullscreen.addEventListener('click', async () => {
    if (document.fullscreenElement === stage) {
      await document.exitFullscreen();
    } else if (stage.requestFullscreen) {
      await stage.requestFullscreen();
    }
  });
  document.addEventListener('fullscreenchange', () => {
    const active = document.fullscreenElement === stage;
    fullscreen.setAttribute('aria-pressed', String(active));
    fullscreen.setAttribute('title', active ? 'Exit full screen' : 'Full screen');
  });
})();
