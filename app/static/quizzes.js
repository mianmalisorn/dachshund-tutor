(() => {
  const dirty = new Set();
  for (const form of document.querySelectorAll('[data-quiz-dirty]')) {
    form.addEventListener('input', () => dirty.add(form));
    form.addEventListener('submit', () => dirty.delete(form));
  }
  window.addEventListener('beforeunload', event => {
    if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
  });

  const submit = document.querySelector('[data-quiz-submit]');
  const form = document.querySelector('[data-quiz-answer]');
  if (form) {
    const indicator = form.querySelector('[data-quiz-save-status]');
    let generation = 0, timer, sending = false, pending = null, destination = null, conflict = false;
    const schedule = () => { clearTimeout(timer); timer = setTimeout(save, 850); };

    form.addEventListener('input', () => {
      generation++;
      dirty.add(form);
      if (conflict) return;
      indicator.textContent = 'Есть несохранённые изменения.';
      schedule();
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      destination = event.submitter?.value || form.dataset.position;
      clearTimeout(timer);
      save();
    });

    async function save() {
      if (sending || conflict) return;
      sending = true;
      dirty.add(form);
      indicator.textContent = 'Сохранение…';
      try {
        // Keep the exact request after a lost acknowledgement. A retry either
        // commits that request or retrieves its existing result.
        do {
          if (!pending) {
            const data = new FormData(form);
            const bytes = crypto.getRandomValues(new Uint8Array(16));
            data.set('creation_key', Array.from(bytes, n => n.toString(16).padStart(2,'0')).join(''));
            pending = {data, generation, revision:Number(data.get('revision'))};
          }
          const response = await fetch(form.getAttribute('action'), {
            method:'POST', body:pending.data, headers:{'X-Quiz-Save':'yes'}
          });
          if (!response.headers.get('content-type')?.includes('application/json'))
            throw new Error('Проверьте соединение и сеанс.');
          const result = await response.json();
          if (!response.ok) {
            conflict = response.status === 409;
            if (response.status >= 400 && response.status < 500 && !conflict) pending = null;
            throw new Error(result.error || 'Запрос не подтверждён.');
          }
          if (result.state !== 'in_progress') {
            dirty.delete(form);
            location.assign('/student/quizzes/attempts/' + form.elements.id.value);
            return;
          }
          if (result.revision !== pending.revision + 1) {
            conflict = true;
            throw new Error('Ответ изменён в другой вкладке.');
          }
          const firstSave = pending.revision === 1;
          const savedGeneration = pending.generation;
          form.elements.revision.value = result.revision;
          submit.elements.revision.value = result.attempt_revision;
          pending = null;
          if (firstSave) {
            const count = document.querySelector('[data-saved-count]');
            if (count) count.textContent = Number(count.textContent) + 1;
          }
          if (generation === savedGeneration) {
            dirty.delete(form);
            indicator.textContent = 'Сохранено на сервере.';
            clearTimeout(timer);
            if (destination)
              location.assign('/student/quizzes/attempts/' + form.elements.id.value +
                              '?notice=saved&question=' + encodeURIComponent(destination));
            return;
          }
        } while (true);
      } catch (error) {
        clearTimeout(timer);
        destination = null;
        indicator.textContent = 'Ошибка сохранения. ' + error.message +
          (conflict ? ' Скопируйте свой ответ и обновите страницу.' : ' Ваш текст остаётся в форме. Повторите сохранение.');
      } finally {
        sending = false;
      }
    }
    submit.addEventListener('submit', event => {
      if (dirty.size || sending || pending) {
        event.preventDefault();
        indicator.textContent = 'Сначала дождитесь подтверждения сохранения ответа.';
        indicator.scrollIntoView({block:'center'});
      }
    });
  }

  const clock = document.querySelector('[data-quiz-clock]');
  if (clock?.dataset.expires) {
    const end = Date.parse(clock.dataset.expires), server = Date.parse(clock.dataset.server), start = performance.now();
    const tick = () => {
      const left = Math.max(0, Math.ceil((end - server - (performance.now() - start)) / 1000));
      clock.textContent = left ? 'Осталось ' + Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0') :
        'Время истекло. Сервер примет только ранее сохранённые ответы. Обновите страницу, чтобы увидеть состояние.';
    };
    tick(); setInterval(tick, 1000);
  }
})();
