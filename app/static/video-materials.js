(() => {
  const page = document.querySelector('[data-video-meeting]');
  const panel = page?.querySelector('[data-video-materials]');
  if (!panel) return;

  const apiPath = `/api/video/meetings/${encodeURIComponent(page.dataset.videoMeeting)}/materials`;
  const csrf = page.querySelector('[data-video-csrf]').value;
  const status = panel.querySelector('[data-video-materials-status]');
  const list = panel.querySelector('[data-video-materials-list]');
  const fileForm = panel.querySelector('[data-video-materials-file-form]');
  const fileInput = panel.querySelector('[data-video-material-file]');
  const fileSubmit = panel.querySelector('[data-video-material-file-submit]');
  const linkForm = panel.querySelector('[data-video-materials-link-form]');
  const titleInput = panel.querySelector('[data-video-material-title]');
  const urlInput = panel.querySelector('[data-video-material-url]');
  const linkSubmit = panel.querySelector('[data-video-material-link-submit]');
  const readonlyNotice = panel.querySelector('[data-video-materials-readonly]');
  const jump = page.querySelector('[data-video-materials-jump]');
  const badges = [...page.querySelectorAll('[data-video-materials-unread]')];
  const announcement = panel.querySelector('[data-video-materials-announcement]');

  const requests = new Set();
  let loading = false;
  let refreshQueued = false;
  let mutating = false;
  let disposed = false;
  let renderedKey = null;
  let loaded = false;
  let lastFullRefresh = 0;
  let knownMaterials = new Map();
  let queuedFullRefresh = false;
  let unread = 0;

  function viewed() {
    if (document.hidden) return false;
    const rect = panel.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  }

  function updateUnread() {
    for (const badge of badges) {
      badge.hidden = unread === 0;
      badge.textContent = unread ? String(unread) : '';
    }
    jump?.setAttribute('aria-label', unread ? `Материалы: ${unread} новых` : 'Открыть материалы занятия');
  }

  function clearUnread() { if (unread) { unread = 0; updateUnread(); } }

  function setStatus(message) {
    status.textContent = message;
  }

  function canEdit() {
    return page.dataset.videoRole === 'admin' && page.dataset.videoState === 'active';
  }

  function syncWritable() {
    const writable = canEdit();
    if (fileForm) {
      fileForm.hidden = !writable;
      linkForm.hidden = !writable;
      readonlyNotice.hidden = writable;
      readonlyNotice.textContent = page.dataset.videoState === 'ended'
        ? 'Встреча завершена. Материалы доступны для просмотра.'
        : 'Добавлять материалы можно во время активной встречи.';
      fileInput.disabled = !writable || mutating;
      fileSubmit.disabled = !writable || mutating;
      titleInput.disabled = !writable || mutating;
      urlInput.disabled = !writable || mutating;
      linkSubmit.disabled = !writable || mutating;
    }
    for (const button of list.querySelectorAll('[data-video-material-delete]')) {
      button.hidden = !writable;
      button.disabled = !writable || mutating;
    }
  }

  async function request(url, options = {}, timeoutMs = 20000) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    requests.add(controller);
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal, ...options,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw Object.assign(new Error(result.detail || 'Запрос не выполнен. Повторите попытку.'), { userMessage: true });
      }
      return result;
    } finally {
      window.clearTimeout(timeout);
      requests.delete(controller);
    }
  }

  function safeExternalUrl(value) {
    try {
      const url = new URL(value);
      if (url.protocol === 'http:' || url.protocol === 'https:') return url.href;
    } catch { /* An invalid URL is shown as unavailable. */ }
    return null;
  }

  function fileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return '';
    if (bytes < 1024) return `${bytes} Б`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  }

  function materialElement(material) {
    const id = Number(material.id);
    if (!Number.isSafeInteger(id) || id < 1 || !['file', 'link'].includes(material.kind)) return null;
    const item = document.createElement('li');
    item.className = 'video-material';
    item.dataset.materialId = String(id);
    const icon = document.createElement('span');
    icon.className = 'video-material-icon';
    icon.textContent = material.kind === 'file' ? '📄' : '🔗';
    icon.setAttribute('aria-hidden', 'true');
    const info = document.createElement('div');
    info.className = 'video-material-info';
    const title = document.createElement('strong');
    title.textContent = String(material.title || 'Материал');
    const meta = document.createElement('p');
    meta.className = 'video-material-meta';
    const date = new Date(material.created_at);
    const parts = [String(material.author_name || 'Участник')];
    if (!Number.isNaN(date.getTime())) {
      parts.push(date.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }));
    }
    if (material.kind === 'file') {
      const size = fileSize(Number(material.size_bytes));
      if (size) parts.push(size);
    }
    meta.textContent = parts.join(' · ');
    info.append(title, meta);
    const actions = document.createElement('div');
    actions.className = 'video-material-actions';
    const href = material.kind === 'file'
      ? `${apiPath}/${encodeURIComponent(String(id))}/download`
      : safeExternalUrl(material.url);
    if (href) {
      const action = document.createElement('a');
      action.className = 'btn secondary';
      action.href = href;
      action.textContent = material.kind === 'file' ? 'Скачать' : 'Открыть';
      action.setAttribute('aria-label', material.kind === 'file'
        ? `Скачать ${title.textContent}` : `Открыть ссылку ${title.textContent}`);
      if (material.kind === 'link') {
        action.target = '_blank';
        action.rel = 'noopener noreferrer';
      }
      actions.append(action);
    } else {
      const unavailable = document.createElement('span');
      unavailable.textContent = 'Ссылка недоступна';
      actions.append(unavailable);
    }
    if (page.dataset.videoRole === 'admin') {
      const remove = document.createElement('button');
      remove.className = 'btn secondary';
      remove.type = 'button';
      remove.dataset.videoMaterialDelete = String(id);
      remove.textContent = 'Удалить';
      remove.setAttribute('aria-label', `Удалить материал ${title.textContent}`);
      actions.append(remove);
    }
    item.append(icon, info, actions);
    return item;
  }

  function renderMaterials(materials) {
    const signature = JSON.stringify(materials);
    if (signature === renderedKey) return;
    const scrollTop = list.scrollTop;
    const elements = materials.map(materialElement).filter(Boolean);
    list.replaceChildren(...elements);
    list.scrollTop = scrollTop;
    renderedKey = signature;
    syncWritable();
  }

  async function load(forceFull = false) {
    if (disposed || document.hidden) return false;
    if (loading) {
      refreshQueued = true;
      queuedFullRefresh ||= forceFull;
      return false;
    }
    loading = true;
    try {
      const full = forceFull || !loaded || Date.now() - lastFullRefresh >= 60000;
      const materialsById = full ? new Map() : new Map(knownMaterials);
      let cursor = full ? 0 : [...materialsById.keys()].reduce((max, id) => Math.max(max, id), 0);
      let hasMore;
      do {
        const result = await request(`${apiPath}?after_id=${cursor}&limit=100`);
        const pageRows = Array.isArray(result.materials) ? result.materials : [];
        let nextCursor = cursor;
        for (const material of pageRows) {
          const id = Number(material.id);
          if (Number.isSafeInteger(id) && id > cursor) {
            materialsById.set(id, material);
            nextCursor = Math.max(nextCursor, id);
          }
        }
        hasMore = Boolean(result.has_more);
        if (hasMore && nextCursor === cursor) throw new Error('Material page did not advance');
        cursor = nextCursor;
      } while (hasMore && !disposed);
      if (disposed) return false;
      const fresh = loaded ? [...materialsById.values()].filter(material => !knownMaterials.has(Number(material.id))) : [];
      knownMaterials = materialsById;
      loaded = true;
      if (full) lastFullRefresh = Date.now();
      const materials = [...materialsById.values()].sort((a, b) => Number(a.id) - Number(b.id));
      renderMaterials(materials);
      if (fresh.length) {
        if (viewed()) clearUnread();
        else { unread += fresh.length; updateUnread(); }
        if (announcement) announcement.textContent = fresh.length === 1
          ? `Новый материал: ${fresh[0].title}` : `Новых материалов: ${fresh.length}`;
      }
      setStatus(materials.length ? '' : 'Материалов пока нет.');
      return true;
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message : 'Не удалось загрузить материалы. Повторим попытку.');
      return false;
    } finally {
      loading = false;
      if (refreshQueued && !disposed) {
        refreshQueued = false;
        const full = queuedFullRefresh;
        queuedFullRefresh = false;
        void load(full);
      }
    }
  }

  async function uploadFile(event) {
    event.preventDefault();
    if (!canEdit() || mutating || disposed) return;
    const file = fileInput.files?.[0];
    if (!file) {
      setStatus('Выберите файл.');
      return;
    }
    mutating = true;
    syncWritable();
    setStatus('Загружаем файл…');
    try {
      const body = new FormData();
      body.append('csrf', csrf);
      body.append('file', file);
      await request(`${apiPath}/file`, { method: 'POST', body }, 120000);
      fileInput.value = '';
      if (await load() && !disposed) setStatus('Файл добавлен.');
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message
        : 'Не удалось завершить загрузку. Проверьте список перед повторной попыткой.');
    } finally {
      mutating = false;
      syncWritable();
    }
  }

  async function addLink(event) {
    event.preventDefault();
    if (!canEdit() || mutating || disposed) return;
    const title = titleInput.value.trim();
    const url = urlInput.value.trim();
    if (!title || !url || !safeExternalUrl(url)) {
      setStatus('Укажите название и ссылку HTTP или HTTPS.');
      return;
    }
    mutating = true;
    syncWritable();
    setStatus('Добавляем ссылку…');
    try {
      await request(`${apiPath}/link`, {
        method: 'POST', body: new URLSearchParams({ csrf, title, url }),
      });
      titleInput.value = '';
      urlInput.value = '';
      if (await load() && !disposed) setStatus('Ссылка добавлена.');
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message : 'Не удалось добавить ссылку. Текст сохранён.');
    } finally {
      mutating = false;
      syncWritable();
    }
  }

  async function removeMaterial(event) {
    const button = event.target.closest('[data-video-material-delete]');
    if (!button || !canEdit() || mutating || disposed) return;
    const title = button.closest('.video-material')?.querySelector('.video-material-info strong')?.textContent || 'этот материал';
    if (!window.confirm(`Удалить материал «${title}» из видеозанятия?`)) return;
    mutating = true;
    syncWritable();
    setStatus('Удаляем материал…');
    try {
      await request(`${apiPath}/${encodeURIComponent(button.dataset.videoMaterialDelete)}`, {
        method: 'DELETE', body: new URLSearchParams({ csrf }),
      });
      if (await load(true) && !disposed) setStatus('Материал удалён.');
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message : 'Не удалось удалить материал. Повторите попытку.');
    } finally {
      mutating = false;
      syncWritable();
    }
  }

  fileForm?.addEventListener('submit', uploadFile);
  if (!page.dataset.videoLayout) jump?.addEventListener('click', () => {
    panel.scrollIntoView({ block: 'start', behavior: 'smooth' });
    clearUnread();
    panel.querySelector('h2')?.setAttribute('tabindex', '-1');
    panel.querySelector('h2')?.focus();
  });
  if (window.IntersectionObserver) {
    const visibility = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && !document.hidden) clearUnread();
    }, { threshold: 0.2 });
    visibility.observe(panel);
    window.addEventListener('pagehide', () => visibility.disconnect());
  }
  linkForm?.addEventListener('submit', addLink);
  list.addEventListener('click', removeMaterial);
  const observer = new MutationObserver(syncWritable);
  observer.observe(page, { attributes: true, attributeFilter: ['data-video-state'] });
  const interval = page.dataset.videoState === 'ended' ? null : window.setInterval(() => { if (!mutating) void load(); }, 4000);
  page.addEventListener('video:statechange', () => {
    syncWritable();
    if (page.dataset.videoState === 'ended' && interval !== null) window.clearInterval(interval);
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && page.dataset.videoState !== 'ended') void load(); });
  window.addEventListener('pagehide', () => {
    disposed = true;
    window.clearInterval(interval);
    observer.disconnect();
    for (const controller of requests) controller.abort();
  });
  syncWritable();
  updateUnread();
  void load();
})();
