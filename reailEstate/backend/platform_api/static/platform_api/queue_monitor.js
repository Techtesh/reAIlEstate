(() => {
  "use strict";
  const root = document.getElementById("queue-monitor");
  if (!root) return;
  const status = document.getElementById("queue-refresh");
  const time = value => value ? new Date(value).toLocaleString() : "";
  const fill = (id, rows, columns) => {
    const body = document.getElementById(id);
    body.replaceChildren();
    if (!rows.length) {
      const row = body.insertRow();
      const cell = row.insertCell();
      cell.colSpan = columns;
      cell.textContent = "None";
    }
    for (const values of rows) {
      const row = body.insertRow();
      for (const value of values) {
        const cell = row.insertCell();
        if (value instanceof Node) cell.append(value);
        else cell.textContent = String(value ?? "");
      }
    }
  };
  const deleteAction = job => {
    if (!job.canDelete) return "";
    const form = document.getElementById("delete-dead-letter-form").content.firstElementChild.cloneNode(true);
    form.elements.namedItem("message_id").value = job.id;
    form.elements.namedItem("version").value = job.deleteVersion;
    form.addEventListener("submit", event => {
      if (!window.confirm(`Delete this dead-letter job for ${job.project}? Project data and audit history will be retained.`)) {
        event.preventDefault();
      }
    });
    return form;
  };
  const stateLabel = job => job.state === "dead_letter" ? "Dead letter" : (
    job.state === "retrying" ? "Failed (retry queued)" : job.state);
  const render = snapshot => {
    for (const counter of root.querySelectorAll("[data-count]")) counter.textContent = snapshot.counts[counter.dataset.count];
    document.getElementById("queue-availability").textContent = `(${snapshot.capacity.available} slots available of ${snapshot.capacity.limit})`;
    fill("queue-workers", snapshot.workers.map(worker => [worker.pid || (worker.state === "queued" ? "Waiting" : "Starting"), worker.group, worker.project, worker.state, time(worker.heartbeatAt)]), 5);
    const pending = snapshot.jobs.filter(job => !["completed", "dead_letter"].includes(job.state));
    fill("queue-pending", pending.map(job => [job.project, `${job.label} (${job.kind})`, stateLabel(job),
      Object.entries(job.progress).map(([key, value]) => `${key}: ${value}`).join(", "), job.attempts,
      [time(job.retryAt), job.error].filter(Boolean).join(" / ")]), 6);
    fill("queue-recent", snapshot.jobs.filter(job => ["completed", "dead_letter"].includes(job.state))
      .map(job => [job.project, `${job.label} (${job.kind})`, stateLabel(job), time(job.updatedAt), job.attempts, job.error, deleteAction(job)]), 7);
    status.textContent = `Updated ${time(snapshot.updatedAt)}`;
  };
  render(JSON.parse(document.getElementById("queue-initial").textContent));
  const refresh = async () => {
    try {
      if (!document.hidden) {
        const response = await fetch(root.dataset.statusUrl, {credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10000)});
        if (!response.ok) throw new Error("Status unavailable");
        render(await response.json());
      }
    } catch {
      status.textContent = "Connection lost. Showing last known status; reconnecting.";
    } finally {
      window.setTimeout(refresh, 3000);
    }
  };
  window.setTimeout(refresh, 3000);
})();