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

  let refreshLayoutOrder = () => {};
  const explorer = page.querySelector('[data-layout-explorer]');
  if (explorer) {
    const divider = explorer.querySelector('[data-layout-divider]');
    const widthKey = 'layout-sidebar-width';
    let preferredWidth = 260;
    let resizePointer = null;
    try {
      const storedWidth = Number(localStorage.getItem(widthKey));
      if (Number.isFinite(storedWidth) && storedWidth >= 180 && storedWidth <= 480) preferredWidth = storedWidth;
    } catch {}
    const sidebarLimit = () => Math.max(180, Math.min(480, explorer.clientWidth - 328));
    const resizeSidebar = () => {
      const maximum = sidebarLimit();
      const width = Math.max(180, Math.min(maximum, preferredWidth));
      explorer.style.setProperty('--layout-sidebar-width', `${width}px`);
      divider.setAttribute('aria-valuemax', String(maximum));
      divider.setAttribute('aria-valuenow', String(width));
      divider.setAttribute('aria-valuetext', `${width} pixels`);
    };
    const rememberWidth = () => {
      try { localStorage.setItem(widthKey, String(preferredWidth)); } catch {}
    };
    divider.hidden = false;
    divider.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      resizePointer = event.pointerId;
      divider.setPointerCapture(event.pointerId);
      divider.focus({preventScroll: true});
      explorer.classList.add('layout-resizing');
    });
    divider.addEventListener('pointermove', event => {
      if (event.pointerId !== resizePointer) return;
      preferredWidth = Math.round(Math.max(180, Math.min(sidebarLimit(), event.clientX - explorer.getBoundingClientRect().left)));
      resizeSidebar();
    });
    const finishResize = event => {
      if (event.pointerId !== resizePointer) return;
      resizePointer = null;
      explorer.classList.remove('layout-resizing');
      if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
      rememberWidth();
    };
    divider.addEventListener('pointerup', finishResize);
    divider.addEventListener('pointercancel', finishResize);
    divider.addEventListener('lostpointercapture', finishResize);
    divider.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const current = Number(divider.getAttribute('aria-valuenow'));
      preferredWidth = event.key === 'Home' ? 180 : event.key === 'End' ? sidebarLimit() :
        Math.max(180, Math.min(sidebarLimit(), current + (event.key === 'ArrowLeft' ? -10 : 10)));
      resizeSidebar();
      rememberWidth();
    });
    divider.addEventListener('dblclick', () => { preferredWidth = 260; resizeSidebar(); rememberWidth(); });
    new ResizeObserver(resizeSidebar).observe(explorer);
    resizeSidebar();
    const rows = [...explorer.querySelectorAll('[data-layout-row]')];
    const tabs = rows.map(row => row.querySelector('[data-layout-name]'));
    const panels = [...explorer.querySelectorAll('[data-plan-id]')];
    const search = explorer.querySelector('[data-layout-search]');
    const sort = explorer.querySelector('[data-layout-sort]');
    const nameList = explorer.querySelector('.layout-name-list');
    const originalOrder = new Map(rows.map((row, index) => [row, index]));
    const nameOrder = new Intl.Collator(undefined, {numeric: true, sensitivity: 'base'});
    const sortKey = `layout-sort:${window.location.pathname}`;
    const selectionKey = `layout-selection:${window.location.pathname}`;
    let selectedId = '';
    const sortLayouts = () => {
      rows.sort((first, second) => {
        const activity = Number(first.classList.contains('inactive-plan')) -
          Number(second.classList.contains('inactive-plan'));
        if (activity) return activity;
        const original = originalOrder.get(first) - originalOrder.get(second);
        if (sort.value === 'default') return original;
        const compared = nameOrder.compare(first.querySelector('[data-layout-name]').textContent.trim(),
          second.querySelector('[data-layout-name]').textContent.trim());
        return (sort.value === 'name-desc' ? -compared : compared) || original;
      });
      tabs.splice(0, tabs.length, ...rows.map(row => row.querySelector('[data-layout-name]')));
      nameList.append(...rows);
    };
    refreshLayoutOrder = sortLayouts;
    sort.addEventListener('change', () => {
      sortLayouts();
      try { sessionStorage.setItem(sortKey, sort.value); } catch {}
    });
    const selectLayout = (tab, focus = false) => {
      selectedId = tab?.dataset.layoutName || '';
      tabs.forEach(item => {
        const selected = item === tab;
        item.setAttribute('aria-selected', String(selected));
        item.tabIndex = selected ? 0 : -1;
        item.closest('[data-layout-row]').classList.toggle('selected', selected);
      });
      panels.forEach(panel => {
        const wasHidden = panel.hidden;
        panel.hidden = panel.dataset.planId !== selectedId;
        if (!panel.hidden && wasHidden) explorer.querySelector('.layout-details').scrollTop = 0;
      });
      if (tab) {
        try { sessionStorage.setItem(selectionKey, selectedId); } catch {}
        if (focus) {
          tab.focus({preventScroll: true});
          const list = tab.closest('.layout-name-list');
          const tabBounds = tab.getBoundingClientRect();
          const listBounds = list.getBoundingClientRect();
          if (tabBounds.top < listBounds.top) list.scrollTop -= listBounds.top - tabBounds.top;
          if (tabBounds.bottom > listBounds.bottom) list.scrollTop += tabBounds.bottom - listBounds.bottom;
        }
      }
    };
    panels.forEach(panel => {
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', `layout-name-${panel.dataset.planId}`);
      panel.tabIndex = 0;
    });
    tabs.forEach(tab => {
      tab.addEventListener('click', () => selectLayout(tab));
      tab.addEventListener('keydown', event => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const visible = tabs.filter(item => !item.closest('[data-layout-row]').hidden);
        const index = visible.indexOf(tab);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 :
          (index + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length;
        selectLayout(visible[next], true);
      });
    });
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      rows.forEach(row => {
        row.hidden = !row.querySelector('[data-layout-name]').textContent.toLocaleLowerCase().includes(query);
      });
      const visible = tabs.filter(tab => !tab.closest('[data-layout-row]').hidden);
      explorer.querySelector('[data-layout-visible-count]').textContent = visible.length;
      explorer.querySelector('[data-layout-empty]').hidden = visible.length > 0;
      if (!visible.some(tab => tab.dataset.layoutName === selectedId)) selectLayout(visible[0]);
      explorer.dispatchEvent(new Event('layoutfilterchange'));
    });
    let remembered;
    try { remembered = sessionStorage.getItem(selectionKey); } catch {}
    try {
      const savedSort = sessionStorage.getItem(sortKey);
      if (['default', 'name', 'name-desc'].includes(savedSort)) sort.value = savedSort;
    } catch {}
    sortLayouts();
    explorer.classList.add('layout-explorer-ready');
    explorer.querySelector('[data-layout-directory]').hidden = false;
    selectLayout(tabs.find(tab => tab.dataset.layoutName === remembered) || tabs[0]);
  }

  const retryDialog = page.querySelector('#evaluation-retry-dialog');
  if (retryDialog) {
    const guardedActions = new Set(['queue_project_reanalysis', 'queue_failed_reanalysis',
      'run_project_evaluations', 'run_queued_project_evaluations', 'run_layout_evaluation', 'queue_floor_layouts', 'queue_amenity_pages']);
    const cancelRetry = retryDialog.querySelector('[data-retry-cancel]');
    const continueRetry = retryDialog.querySelector('[data-retry-continue]');
    const freshRetry = retryDialog.querySelector('[data-retry-fresh]');
    const retryError = retryDialog.querySelector('[data-retry-error]');
    const pageError = page.querySelector('[data-evaluation-error]');
    let pendingRetry = null;
    let submitting = false;

    const sendEvaluation = async pending => {
      if (submitting) return;
      submitting = true;
      retryError.hidden = true;
      pageError.hidden = true;
      pending.form.setAttribute('aria-busy', 'true');
      cancelRetry.disabled = true;
      continueRetry.disabled = true;
      freshRetry.disabled = true;
      if (pending.submitter) pending.submitter.disabled = true;
      try {
        const response = await fetch(pending.form.getAttribute('action') || window.location.href, {
          method: 'POST', body: pending.data, credentials: 'same-origin',
          headers: {'X-Requested-With': 'XMLHttpRequest'},
        });
        if (!response.headers.get('Content-Type')?.includes('application/json')) {
          throw new Error('Could not submit the evaluation. Reload to check its status before trying again.');
        }
        const result = await response.json();
        if (response.status === 409 && result.requiresConfirmation) {
          pending.data.set('retry_confirmation', result.confirmationToken);
          pending.freshConfirmationToken = result.freshConfirmationToken;
          pendingRetry = pending;
          freshRetry.hidden = !result.freshConfirmationToken;
          retryDialog.querySelector('[data-retry-fresh-help]').hidden = freshRetry.hidden;
          retryDialog.querySelector('[data-retry-project]').textContent = result.project;
          retryDialog.querySelector('[data-retry-failures]').replaceChildren(...result.failures.map(failure => {
            const item = document.createElement('li');
            item.textContent = `${failure.label}: ${failure.error}${failure.attempts == null ? '' : ` (${failure.attempts} attempt${failure.attempts === 1 ? '' : 's'})`}`;
            return item;
          }));
          const overflow = retryDialog.querySelector('[data-retry-overflow]');
          overflow.textContent = `${result.total} failure records in total; showing ${result.failures.length}.`;
          overflow.hidden = result.total <= result.failures.length;
          if (!retryDialog.open) retryDialog.showModal();
        } else if (response.ok && result.redirectUrl) {
          window.location.assign(result.redirectUrl);
        } else {
          throw new Error('Could not submit the evaluation. Reload to check its status before trying again.');
        }
      } catch (error) {
        const target = retryDialog.open ? retryError : pageError;
        target.textContent = error.message || 'Connection lost. Reload to check the evaluation status.';
        target.hidden = false;
      } finally {
        submitting = false;
        pending.form.removeAttribute('aria-busy');
        cancelRetry.disabled = false;
        continueRetry.disabled = false;
        freshRetry.disabled = false;
        if (pending.submitter) pending.submitter.disabled = false;
        if (retryDialog.open) cancelRetry.focus();
      }
    };

    page.addEventListener('submit', event => {
      const form = event.target;
      const data = new FormData(form, event.submitter);
      if (!guardedActions.has(data.get('action'))) return;
      event.preventDefault();
      if (!submitting) sendEvaluation({form, data, submitter: event.submitter});
    });
    cancelRetry.addEventListener('click', () => retryDialog.close());
    retryDialog.addEventListener('cancel', event => {
      if (submitting) event.preventDefault();
    });
    retryDialog.addEventListener('close', () => {
      pendingRetry?.submitter?.focus();
      pendingRetry = null;
    });
    continueRetry.addEventListener('click', () => {
      if (pendingRetry) sendEvaluation(pendingRetry);
    });
    freshRetry.addEventListener('click', () => {
      if (!pendingRetry?.freshConfirmationToken || submitting) return;
      pendingRetry.data.set('fresh_evaluation', 'true');
      pendingRetry.data.set('retry_confirmation', pendingRetry.freshConfirmationToken);
      sendEvaluation(pendingRetry);
    });
  }

  page.querySelectorAll('.alias-editor').forEach(editor => {
    const input = editor.querySelector('[name="display_name"]');
    const cancel = () => {
      input.value = input.defaultValue;
      editor.open = false;
      editor.querySelector('summary').focus();
    };
    editor.addEventListener('toggle', () => {
      if (editor.open) {
        input.focus();
        input.select();
      }
    });
    editor.querySelector('[data-alias-cancel]').addEventListener('click', cancel);
    editor.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        cancel();
      }
    });
  });

  const floorPlanSortForm = page.querySelector('[data-floor-plan-sort-form]');
  const floorPlanGrid = page.querySelector('[data-floor-plan-grid]');
  const floorPlanSort = floorPlanSortForm?.elements.namedItem('sort');
  const floorPlanNameOrder = floorPlanGrid
    ? Array.from(floorPlanGrid.querySelectorAll('[data-plan-id]')).sort((first, second) => {
      const order = first.querySelector('h2').textContent.trim().localeCompare(
        second.querySelector('h2').textContent.trim(), undefined, {numeric: true, sensitivity: 'base'});
      return floorPlanSort?.value === 'name-desc' ? -order : order;
    }) : [];
  floorPlanSortForm?.querySelector('select')?.addEventListener('change', () => floorPlanSortForm.requestSubmit());
  const orderFloorPlans = () => {
    [...floorPlanNameOrder].sort((first, second) =>
      Number(first.classList.contains('inactive-plan')) - Number(second.classList.contains('inactive-plan'))
    ).forEach(card => floorPlanGrid.appendChild(card));
  };

  page.querySelectorAll('[data-active-form]').forEach(form => {
    const checkbox = form.elements.namedItem('active');
    let savedActive = checkbox.checked;
    checkbox.addEventListener('change', () => form.requestSubmit());
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (form.dataset.submitting) return;
      const data = new FormData(form);
      const status = form.querySelector('[data-active-status]');
      const error = form.querySelector('[data-active-error]');
      form.dataset.submitting = 'true';
      form.setAttribute('aria-busy', 'true');
      checkbox.disabled = true;
      status.textContent = 'Saving...';
      error.hidden = true;
      try {
        const response = await fetch(form.getAttribute('action') || window.location.href, {
          method: 'POST', body: data, credentials: 'same-origin',
          headers: {'X-Requested-With': 'XMLHttpRequest'},
        });
        const isJSON = /application\/(?:problem\+)?json/.test(response.headers.get('Content-Type') || '');
        const result = isJSON ? await response.json() : null;
        if (!response.ok || !Array.isArray(result?.plans)) {
          throw new Error(result?.detail || 'Could not confirm the saved state. Reload before trying again.');
        }
        result.plans.forEach(plan => {
          const card = page.querySelector(`[data-plan-id="${CSS.escape(plan.id)}"]`);
          if (card) {
            card.classList.toggle('inactive-plan', plan.inactive);
            card.querySelector('[data-plan-inactive]').hidden = !plan.inactive;
            const priceForm = card.querySelector('[data-layout-price-form]');
            if (priceForm) {
              priceForm.elements.namedItem('expected_updated_at').value = plan.version;
              priceForm.hidden = plan.inactive;
            }
            const activeForm = card.querySelector('[data-active-form]');
            if (activeForm) {
              activeForm.elements.namedItem('version').value = plan.version;
              activeForm.elements.namedItem('active').checked = plan.active;
              activeForm.dispatchEvent(new Event('activestateupdated'));
            }
          }
          const row = page.querySelector(`[data-layout-row="${CSS.escape(plan.id)}"]`);
          if (row) {
            row.classList.toggle('inactive-plan', plan.inactive);
            const tab = row.querySelector('[data-layout-name]');
            tab.title = `${tab.textContent}${plan.inactive ? ' (Inactive)' : ''}`;
          }
        });
        const projectPrice = page.querySelector('[data-project-price]');
        if (projectPrice && typeof result.projectPrice === 'string') projectPrice.textContent = result.projectPrice;
        orderFloorPlans();
        refreshLayoutOrder();
        status.textContent = 'Saved';
      } catch (failure) {
        checkbox.checked = savedActive;
        status.textContent = '';
        error.textContent = failure.message || 'Could not save. Reload before trying again.';
        error.hidden = false;
      } finally {
        delete form.dataset.submitting;
        form.removeAttribute('aria-busy');
        checkbox.disabled = false;
        checkbox.focus({preventScroll: true});
      }
    });
    form.addEventListener('activestateupdated', () => { savedActive = checkbox.checked; });
    window.addEventListener('pageshow', () => {
      delete form.dataset.submitting;
      form.removeAttribute('aria-busy');
      checkbox.disabled = false;
    });
  });

  const draftForm = page.querySelector('[data-draft-cart-form]');
  if (draftForm) {
    const drafts = [...draftForm.querySelectorAll('[name="draft_ids"]')];
    const selectNew = draftForm.querySelector('[data-select-new-drafts]');
    const feedback = draftForm.querySelector('[data-draft-cart-feedback]');
    const showDraftError = message => {
      feedback.textContent = message;
      feedback.hidden = false;
      feedback.focus({preventScroll: true});
    };
    selectNew?.addEventListener('change', () => {
      drafts.filter(draft => draft.dataset.draftKind === 'new').forEach(draft => {
        draft.checked = selectNew.checked;
      });
      feedback.hidden = true;
    });
    drafts.forEach(draft => draft.addEventListener('change', () => {
      const newDrafts = drafts.filter(item => item.dataset.draftKind === 'new');
      if (selectNew) {
        const selected = newDrafts.filter(item => item.checked).length;
        selectNew.checked = newDrafts.length > 0 && selected === newDrafts.length;
        selectNew.indeterminate = selected > 0 && selected < newDrafts.length;
      }
      feedback.hidden = true;
    }));
    draftForm.addEventListener('submit', event => {
      const action = event.submitter?.value;
      const selected = drafts.filter(draft => draft.checked);
      if (!selected.length) {
        event.preventDefault();
        showDraftError('Select at least one draft before choosing an action.');
        return;
      }
      if (action === 'add' && selected.some(draft => draft.dataset.draftKind !== 'new')) {
        event.preventDefault();
        showDraftError('Add selected accepts new layout drafts only. Use Commit for saved pages or crop updates.');
        return;
      }
      if (action === 'commit_saved' && selected.some(draft => draft.dataset.draftKind !== 'saved')) {
        event.preventDefault();
        showDraftError('Commit accepts saved page additions or crop updates only. Use Add selected for new layouts.');
      }
    });
  }

  // Only the current page's eligible flat cards participate, never cart drafts
  // or source floor plans. Each checkbox belongs to the standalone POST form.
  const bulkForm = page.querySelector('#layout-bulk-removal');
  if (!bulkForm) return;
  const selectAll = bulkForm.querySelector('[data-select-layouts]');
  const count = bulkForm.querySelector('[data-layout-selection-count]');
  const layouts = [...page.querySelectorAll('[data-layout-select]')].filter(input =>
    input.form === bulkForm && !input.disabled
  );
  const visibleLayouts = () => layouts.filter(input => !input.closest('[data-layout-row]')?.hidden);
  const updateSelection = () => {
    const selected = layouts.filter(input => input.checked).length;
    const visible = visibleLayouts();
    const visibleSelected = visible.filter(input => input.checked).length;
    if (count) count.textContent = `${selected} selected`;
    if (selectAll) {
      selectAll.checked = visible.length > 0 && visibleSelected === visible.length;
      selectAll.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
      selectAll.disabled = visible.length === 0;
    }
  };
  if (selectAll) selectAll.addEventListener('change', () => {
    visibleLayouts().forEach(input => { input.checked = selectAll.checked; });
    updateSelection();
  });
  explorer?.addEventListener('layoutfilterchange', updateSelection);
  layouts.forEach(input => input.addEventListener('change', updateSelection));
  window.addEventListener('pageshow', updateSelection);
  updateSelection();
})();
