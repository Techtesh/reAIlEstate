/* One selection on the exact signed source; never navigate to a source FloorPlan. */
(() => {
  "use strict";
  const form = document.getElementById("crop-update-form");
  if (!form) return;
  const config = Object.freeze(JSON.parse(document.getElementById("crop-update-config").textContent));
  const canvas = document.getElementById("crop-canvas");
  const ctx = canvas.getContext("2d");
  const status = document.getElementById("crop-status");
  const save = document.getElementById("crop-save");
  const undo = document.getElementById("crop-undo");
  const clear = document.getElementById("crop-clear");
  const image = new Image();
  const names = ["x", "y", "width", "height"];
  let rotation = 0, selection = null, drag = null, loaded = false, submitting = false;
  const history = [];
  const field = name => form.elements.namedItem(name);
  const valid = r => r && names.every(k => Number.isFinite(r[k])) && r.x >= 0 && r.y >= 0 &&
    r.width > 0 && r.height > 0 && r.x + r.width <= 1 && r.y + r.height <= 1;

  function draw() {
    if (!loaded) return;
    const sideways = rotation % 180 !== 0;
    let width = sideways ? image.naturalHeight : image.naturalWidth;
    let height = sideways ? image.naturalWidth : image.naturalHeight;
    const radians = rotation * Math.PI / 180;
    if (rotation % 90) {
      const cosine = Math.abs(Number(Math.cos(radians).toFixed(15)));
      const sine = Math.abs(Number(Math.sin(radians).toFixed(15)));
      const halfWidth = (image.naturalWidth * cosine + image.naturalHeight * sine) / 2;
      const halfHeight = (image.naturalWidth * sine + image.naturalHeight * cosine) / 2;
      width = Math.ceil(image.naturalWidth / 2 + halfWidth) - Math.floor(image.naturalWidth / 2 - halfWidth);
      height = Math.ceil(image.naturalHeight / 2 + halfHeight) - Math.floor(image.naturalHeight / 2 - halfHeight);
    }
    const scale = Math.min(1, 1200 / Math.max(width, height));
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingQuality = "high";
    ctx.save();
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.scale(canvas.width / width, canvas.height / height);
    ctx.rotate(radians);
    ctx.drawImage(image, -image.naturalWidth / 2, -image.naturalHeight / 2);
    ctx.restore();
    if (selection) {
      const {x, y, width: w, height: h} = selection;
      ctx.fillStyle = "rgba(0, 90, 220, 0.18)";
      ctx.strokeStyle = "#006fe6";
      ctx.lineWidth = 3;
      ctx.fillRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height);
      ctx.strokeRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height);
    }
  }

  function sync() {
    field("regions").value = JSON.stringify(valid(selection) ? [selection] : []);
    field("rotation_degrees").value = String(rotation);
    save.disabled = !loaded || !valid(selection) || !field("confirm").checked || !!drag;
    undo.disabled = !history.length;
    clear.disabled = !selection;
    if (selection) names.forEach(k => {
      document.getElementById(`crop-${k}`).value = String(Number((selection[k] * 100).toFixed(6)));
    });
    status.textContent = selection ? `One rectangle selected. Rotation: ${rotation}° clockwise.` :
      `No rectangle selected. Rotation: ${rotation}° clockwise. Draw or apply bounds deliberately.`;
    draw();
  }

  function choose(rectangle) {
    history.push(selection ? {...selection} : null);
    selection = rectangle;
    sync();
  }

  function point(event) {
    const box = canvas.getBoundingClientRect();
    return {x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
      y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))};
  }
  function dragRectangle(event) {
    const end = point(event);
    return {x: Math.min(drag.start.x, end.x), y: Math.min(drag.start.y, end.y),
      width: Math.abs(end.x - drag.start.x), height: Math.abs(end.y - drag.start.y)};
  }
  canvas.addEventListener("pointerdown", event => {
    if (!loaded || drag || event.button !== 0) return;
    drag = {id: event.pointerId, start: point(event), previous: selection};
    canvas.setPointerCapture(event.pointerId);
    save.disabled = true;
  });
  canvas.addEventListener("pointermove", event => {
    if (!drag || event.pointerId !== drag.id) return;
    selection = dragRectangle(event);
    draw();
  });
  canvas.addEventListener("pointerup", event => {
    if (!drag || event.pointerId !== drag.id) return;
    const rectangle = dragRectangle(event);
    selection = drag.previous;
    drag = null;
    canvas.releasePointerCapture(event.pointerId);
    if (valid(rectangle)) choose(rectangle); else sync();
  });
  function cancelDrag() {
    if (!drag) return;
    selection = drag.previous;
    drag = null;
    sync();
  }
  canvas.addEventListener("pointercancel", cancelDrag);
  canvas.addEventListener("lostpointercapture", cancelDrag);
  canvas.addEventListener("keydown", event => { if (event.key === "Escape") cancelDrag(); });
  document.getElementById("crop-apply").addEventListener("click", () => {
    if (!loaded) return;
    const rectangle = Object.fromEntries(names.map(k => [k, document.getElementById(`crop-${k}`).valueAsNumber / 100]));
    if (!valid(rectangle)) {
      status.textContent = "Enter finite, positive bounds entirely inside the image.";
      return;
    }
    choose(rectangle);
  });
  undo.addEventListener("click", () => { if (history.length) { selection = history.pop(); sync(); } });
  clear.addEventListener("click", () => choose(null));
  function rotate(degrees) {
    if (!loaded) return;
    cancelDrag();
    rotation = (rotation + degrees + 360) % 360;
    selection = null;
    history.length = 0;
    field("confirm").checked = false;
    sync();
  }
  document.getElementById("crop-rotate-left").addEventListener("click", () => rotate(-5));
  document.getElementById("crop-rotate-right").addEventListener("click", () => rotate(5));
  field("confirm").addEventListener("change", sync);
  form.addEventListener("submit", event => {
    if (!loaded || !valid(selection) || drag || !field("confirm").checked) {
      event.preventDefault();
      status.textContent = "Select one rectangle and explicitly confirm this layout-only edit.";
      return;
    }
    // No source parameters are taken from the canvas, query string or a card.
    field("source_mode").value = config.sourceMode;
    submitting = true;
    save.disabled = true;
  });
  window.addEventListener("beforeunload", event => {
    if (!submitting && (selection || rotation)) { event.preventDefault(); event.returnValue = ""; }
  });
  document.querySelectorAll("[data-crop-navigate]").forEach(link => {
    link.addEventListener("click", event => {
      if ((selection || rotation) && !window.confirm("Discard the unsaved crop selection?")) event.preventDefault();
      else submitting = true;
    });
  });
  image.onload = () => {
    if (image.naturalWidth !== config.width || image.naturalHeight !== config.height) {
      status.textContent = "Preview dimensions changed. Reload the crop editor.";
      return;
    }
    loaded = true;
    sync();
  };
  image.onerror = () => { status.textContent = "Authenticated preview unavailable or expired. Reload the crop editor."; };
  image.src = config.previewUrl;
})();