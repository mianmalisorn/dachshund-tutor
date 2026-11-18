(() => {
  const page = document.querySelector('[data-video-meeting]');
  const chat = page?.querySelector('[data-video-chat]');
  if (!chat) return;

  const apiPath = `/api/video/meetings/${encodeURIComponent(page.dataset.videoMeeting)}/messages`;
  const csrf = page.querySelector('[data-video-csrf]').value;
  const toggle = chat.querySelector('[data-video-chat-toggle]');
  const body = chat.querySelector('[data-video-chat-body]');
  const status = chat.querySelector('[data-video-chat-status]');
  const olderButton = chat.querySelector('[data-video-chat-older]');
  const list = chat.querySelector('[data-video-chat-messages]');
  const form = chat.querySelector('[data-video-chat-form]');
  const input = chat.querySelector('[data-video-chat-text]');
  const sendButton = chat.querySelector('[data-video-chat-send]');
  const hint = chat.querySelector('[data-video-chat-hint]');
  const jump = page.querySelector('[data-video-chat-jump]');
  const badges = [...page.querySelectorAll('[data-video-chat-unread]')];
  const announcement = chat.querySelector('[data-video-chat-announcement]');
  const ownId = Number(page.dataset.videoUserId);

  const seen = new Set();
  const requests = new Set();
  let pollCursor = 0;
  let oldestId = null;
  let historyLoaded = false;
  let historyBusy = false;
  let polling = false;
  let sending = false;
  let disposed = false;
  let retryKey = null;
  let retryText = null;
  let unread = 0;

  function viewed() {
    if (document.hidden || body.hidden) return false;
    const rect = chat.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0;
  }

  function updateUnread() {
    for (const badge of badges) {
      badge.hidden = unread === 0;
      badge.textContent = unread ? String(unread) : '';
    }
    jump?.setAttribute('aria-label', unread ? `Чат: ${unread} новых сообщений` : 'Открыть чат занятия');
  }

  function clearUnread() { if (unread) { unread = 0; updateUnread(); } }

  function setStatus(message) {
    status.textContent = message;
  }

  function syncWritable() {
    const active = page.dataset.videoState === 'active' && page.dataset.videoChatWritable === 'true';
    input.disabled = !active || sending;
    sendButton.disabled = !active || sending;
    hint.textContent = active
      ? 'Enter — отправить, Shift+Enter — новая строка'
      : page.dataset.videoState === 'ended'
        ? 'Встреча завершена. Историю можно читать.'
        : 'Отправка сообщений недоступна. Историю можно читать.';
  }

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    requests.add(controller);
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal, ...options,
      });
      const result = await response.json();
      if (!response.ok) {
        throw Object.assign(new Error(result.detail || 'Не удалось выполнить запрос.'), { userMessage: true });
      }
      return result;
    } finally {
      window.clearTimeout(timeout);
      requests.delete(controller);
    }
  }

  function messageElement(message) {
    const item = document.createElement('li');
    item.className = 'video-chat-message';
    item.dataset.messageId = String(message.id);
    const meta = document.createElement('div');
    meta.className = 'video-chat-meta';
    const author = document.createElement('strong');
    author.textContent = message.author_name;
    const time = document.createElement('time');
    time.dateTime = message.created_at;
    const date = new Date(message.created_at);
    time.textContent = Number.isNaN(date.getTime())
      ? 'Время неизвестно'
      : date.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
    const text = document.createElement('p');
    text.textContent = message.text;
    meta.append(author, time);
    item.append(meta, text);
    return item;
  }

  function addMessage(message, prepend = false, initial = false) {
    if (seen.has(message.id)) return;
    seen.add(message.id);
    oldestId = oldestId === null ? message.id : Math.min(oldestId, message.id);
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 70;
    const element = messageElement(message);
    const next = [...list.children].find((item) => Number(item.dataset.messageId) > message.id);
    if (next) list.insertBefore(element, next);
    else list.append(element);
    if (!prepend && nearBottom) list.scrollTop = list.scrollHeight;
    if (!initial && !prepend && Number(message.author_id) !== ownId) {
      if (viewed()) clearUnread();
      else { unread++; updateUnread(); }
      if (announcement) announcement.textContent = `Новое сообщение от ${message.author_name || 'участника'}`;
    }
  }

  async function loadHistory() {
    if (historyBusy || disposed) return;
    historyBusy = true;
    olderButton.disabled = true;
    try {
      const older = historyLoaded && oldestId !== null;
      const query = new URLSearchParams({ limit: '30' });
      if (older) query.set('before_id', String(oldestId));
      const result = await request(`${apiPath}?${query}`);
      const messages = [...result.messages].sort((a, b) => a.id - b.id);
      const previousHeight = list.scrollHeight;
      if (older) {
        for (const message of messages) addMessage(message, true);
        list.scrollTop += list.scrollHeight - previousHeight;
      } else {
        for (const message of messages) addMessage(message, false, true);
        if (messages.length) pollCursor = Math.max(pollCursor, messages[messages.length - 1].id);
      }
      historyLoaded = true;
      olderButton.hidden = !result.has_more;
      setStatus(seen.size ? '' : 'Сообщений пока нет.');
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message : 'Не удалось загрузить чат. Повторим попытку.');
    } finally {
      historyBusy = false;
      olderButton.disabled = false;
    }
  }

  async function poll() {
    if (polling || disposed || document.hidden || page.dataset.videoState === 'ended') return;
    polling = true;
    try {
      if (!historyLoaded) {
        await loadHistory();
        return;
      }
      const query = new URLSearchParams({ after_id: String(pollCursor), limit: '100' });
      const result = await request(`${apiPath}?${query}`);
      for (const message of result.messages) {
        addMessage(message);
        pollCursor = Math.max(pollCursor, message.id);
      }
      setStatus(seen.size ? '' : 'Сообщений пока нет.');
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message : 'Связь с чатом временно потеряна. Повторим попытку.');
    } finally {
      polling = false;
    }
  }

  function newClientKey() {
    return crypto.randomUUID?.() || Array.from(
      crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')
    ).join('');
  }

  async function send(event) {
    event.preventDefault();
    if (sending || disposed || page.dataset.videoState !== 'active'
        || page.dataset.videoChatWritable !== 'true') return;
    const text = input.value.trim();
    if (!text) {
      setStatus('Введите сообщение.');
      return;
    }
    if (text.length > 2000) {
      setStatus('Сообщение должно содержать не больше 2000 символов.');
      return;
    }
    if (retryText !== text) {
      retryText = text;
      retryKey = newClientKey();
    }
    sending = true;
    syncWritable();
    try {
      const message = await request(apiPath, {
        method: 'POST',
        body: new URLSearchParams({ csrf, text, client_key: retryKey }),
      });
      addMessage(message);
      input.value = '';
      retryKey = null;
      retryText = null;
      setStatus('Сообщение отправлено.');
    } catch (error) {
      if (!disposed) setStatus(error.userMessage ? error.message : 'Не удалось отправить сообщение. Текст сохранён, попробуйте ещё раз.');
    } finally {
      sending = false;
      syncWritable();
    }
  }

  toggle.addEventListener('click', () => {
    body.hidden = !body.hidden;
    toggle.setAttribute('aria-expanded', String(!body.hidden));
    toggle.textContent = body.hidden ? 'Открыть чат' : 'Скрыть чат';
    if (!body.hidden) clearUnread();
  });
  if (!page.dataset.videoLayout) jump?.addEventListener('click', () => {
    body.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    toggle.textContent = 'Скрыть чат';
    chat.scrollIntoView({ block: 'start', behavior: 'smooth' });
    clearUnread();
    toggle.focus();
  });
  if (window.IntersectionObserver) {
    const visibility = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && !body.hidden && !document.hidden) clearUnread();
    }, { threshold: 0.2 });
    visibility.observe(chat);
    window.addEventListener('pagehide', () => visibility.disconnect());
  }
  olderButton.addEventListener('click', loadHistory);
  form.addEventListener('submit', send);
  input.addEventListener('input', () => {
    if (retryText !== input.value.trim()) {
      retryKey = null;
      retryText = null;
    }
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  const observer = new MutationObserver(syncWritable);
  observer.observe(page, { attributes: true, attributeFilter: ['data-video-state'] });
  const interval = page.dataset.videoState === 'ended' ? null : window.setInterval(poll, 4000);
  page.addEventListener('video:statechange', () => {
    syncWritable();
    if (page.dataset.videoState === 'ended' && interval !== null) window.clearInterval(interval);
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && page.dataset.videoState !== 'ended') void poll(); });
  window.addEventListener('pagehide', () => {
    disposed = true;
    window.clearInterval(interval);
    observer.disconnect();
    for (const controller of requests) controller.abort();
  });
  syncWritable();
  updateUnread();
  void loadHistory();
})();
