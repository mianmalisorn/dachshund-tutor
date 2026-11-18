document.querySelectorAll('[data-video-start]').forEach((form) => {
  let startKey = null;
  const requestTimeoutMs = Math.min(30000, Math.max(1000, Number(form.dataset.videoStartTimeoutMs) || 12000));
  async function timedFetch(url, options, timeoutMs = 12000) {
    const controller = new AbortController();
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(() => {
        controller.abort();
        reject(new DOMException('Request timed out', 'AbortError'));
      }, timeoutMs);
    });
    try {
      const operation = (async () => {
        const response = await fetch(url, { ...options, signal: controller.signal });
        const result = await response.json();
        if (controller.signal.aborted) throw new DOMException('Request cancelled', 'AbortError');
        return { response, result };
      })();
      return await Promise.race([operation, timeout]);
    } finally { window.clearTimeout(timer); }
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button');
    const status = form.querySelector('[data-video-start-status]');
    button.disabled = true;
    status.setAttribute('role', 'status');
    status.dataset.state = 'pending';
    status.textContent = 'Создаём встречу…';
    try {
      startKey ||= crypto.randomUUID?.() || Array.from(
        crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')
      ).join('');
      const body = new URLSearchParams({
        csrf: form.querySelector('[name="csrf"]').value,
        student_id: form.dataset.videoStart,
      });
      const { response, result } = await timedFetch('/api/video/meetings', {
        method: 'POST', credentials: 'same-origin', body,
        headers: { 'Idempotency-Key': startKey },
      }, requestTimeoutMs);
      if (!response.ok) {
        throw Object.assign(new Error(result.detail || 'Не удалось начать видеозвонок.'),
          { userMessage: true });
      }
      window.location.assign(`/video/meetings/${encodeURIComponent(result.id)}`);
    } catch (error) {
      if (error.name === 'AbortError') {
        try {
          const { response: check, result: current } = await timedFetch('/api/video/meetings/current',
            { credentials: 'same-origin', cache: 'no-store' }, Math.min(requestTimeoutMs, 8000));
          if (check.ok) {
            const { meeting } = current;
            if (meeting?.student_id === Number(form.dataset.videoStart) && meeting.status === 'active') {
              window.location.assign(`/video/meetings/${encodeURIComponent(meeting.id)}`);
              return;
            }
          }
        } catch { /* The same idempotency key is retained for manual retry. */ }
      }
      status.setAttribute('role', 'alert');
      status.dataset.state = 'error';
      status.textContent = error.name === 'AbortError'
        ? 'Ответ задерживается. Проверьте встречу или повторите попытку; повтор использует тот же ключ.'
        : error.userMessage ? error.message : 'Не удалось начать видеозвонок. Проверьте сеть и повторите попытку.';
      button.disabled = false;
    }
  });
});
