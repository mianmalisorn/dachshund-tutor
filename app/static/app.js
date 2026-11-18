/* Общая логика подтверждений, одинаковая для всех ролей. Без приватных шуток. */
(() => {
  const toggle = document.querySelector('[data-mobile-nav-toggle]');
  const nav = document.getElementById('main-nav');
  if (!toggle || !nav) return;
  const mobile = window.matchMedia('(max-width: 800px)');
  const label = toggle.querySelector('[data-mobile-nav-label]');
  function setOpen(open) {
    nav.classList.toggle('mobile-nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    label.textContent = open ? 'Скрыть меню' : 'Меню';
  }
  setOpen(false);
  document.body.classList.add('mobile-nav-ready');
  toggle.addEventListener('click', () => setOpen(!nav.classList.contains('mobile-nav-open')));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mobile.matches && nav.classList.contains('mobile-nav-open')) {
      setOpen(false);
      toggle.focus();
    }
  });
  nav.addEventListener('click', event => {
    if (mobile.matches && event.target.closest('a[href]')) setOpen(false);
  });
  mobile.addEventListener('change', () => setOpen(false));
})();

(() => {
  'use strict';
  document.querySelectorAll('[data-recipient-picker]').forEach(picker => {
    const search = picker.querySelector('[data-recipient-search]');
    const count = picker.querySelector('[data-recipient-count]');
    const selected = picker.querySelector('[data-recipient-selected]');
    const empty = picker.querySelector('[data-recipient-empty]');
    const inputs = [...picker.querySelectorAll('input[name="student_ids"]')];
    const normalize = text => text.trim().toLocaleLowerCase('ru-RU').replace(/\s+/g, ' ');
    picker.querySelector('.recipient-tools').hidden = false;
    function refresh() {
      const query = normalize(search.value);
      let visible = 0;
      selected.replaceChildren();
      for (const input of inputs) {
        const tile = input.closest('label');
        const name = tile.textContent.trim();
        tile.hidden = !normalize(name).includes(query);
        if (!tile.hidden) visible++;
        if (input.checked) {
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.className = 'btn secondary recipient-chip';
          remove.textContent = name + ' ×';
          remove.setAttribute('aria-label', 'Снять выбор: ' + name);
          remove.addEventListener('click', () => {
            input.checked = false;
            refresh();
            search.focus();
          });
          selected.append(remove);
        }
      }
      const total = inputs.filter(input => input.checked).length;
      count.textContent = 'Выбрано: ' + total;
      selected.hidden = total === 0;
      empty.hidden = visible !== 0 || inputs.length === 0;
    }
    search.addEventListener('input', refresh);
    inputs.forEach(input => input.addEventListener('change', refresh));
    refresh();
  });
  const cat = document.getElementById('cat-strip');
  if (cat) {
    const duration = Math.min(25000, Math.max(0, Number(cat.dataset.visibleMs) || 25000));
    window.setTimeout(() => cat.remove(), duration);
  }
  document.querySelectorAll('[data-confirm]').forEach(button => {
    button.addEventListener('click', event => {
      if (!window.confirm(button.dataset.confirm || 'Подтвердить действие?')) {
        event.preventDefault();
      }
    });
  });
})();

// Generic, local-only utility: no network requests and no private dialogue text.
(() => {
  const panel = document.getElementById('breathing-panel');
  const opener = document.querySelector('[data-breathing-open]');
  if (!panel || !opener) return;
  const start = panel.querySelector('[data-breathing-start]');
  const stop = panel.querySelector('[data-breathing-stop]');
  const phase = panel.querySelector('.breathing-phase');
  const timer = panel.querySelector('.breathing-timer');
  const orb = panel.querySelector('.breathing-orb');
  let interval = null, began = 0;
  function finish(completed = false) {
    clearInterval(interval); interval = null;
    start.disabled = false; stop.disabled = true;
    orb.removeAttribute('data-phase');
    phase.textContent = completed ? 'Минута для себя. Можно возвращаться.' : 'Можно сделать паузу';
    timer.textContent = completed ? '0' : '60';
  }
  function tick() {
    const elapsed = (performance.now() - began) / 1000;
    if (elapsed >= 60) { finish(true); return; }
    timer.textContent = String(Math.ceil(60 - elapsed));
    const next = elapsed % 10 < 4 ? 'inhale' : 'exhale';
    if (orb.dataset.phase !== next) {
      orb.dataset.phase = next;
      phase.textContent = next === 'inhale' ? 'Вдох' : 'Выдох';
    }
  }
  opener.addEventListener('click', () => panel.showModal());
  panel.querySelector('[data-breathing-close]').addEventListener('click', () => panel.close());
  panel.addEventListener('close', () => { finish(); opener.focus(); });
  start.addEventListener('click', () => { finish(); began = performance.now(); start.disabled = true; stop.disabled = false; tick(); interval = setInterval(tick, 200); });
  stop.addEventListener('click', () => finish());
})();

// Preset text lives only in the authenticated form, never in this public script.
document.querySelectorAll('[data-reminder-preset]').forEach(button => {
 button.addEventListener('click', () => {
  const field = button.closest('form').querySelector('textarea[name="body"]');
  if (field.value.trim() && !window.confirm('Заменить введённый текст выбранным шаблоном?')) return;
  field.value = button.dataset.reminderPreset; field.focus();
 });
});

// Server-backed, user-private drafts. Sensitive fields and file inputs never leave the page.
document.querySelectorAll('form[data-draft][data-draft-key]').forEach(form => {
  const kind = form.dataset.draft, key = form.dataset.draftKey;
  const endpoint = `/admin/productivity/drafts/${encodeURIComponent(kind)}/${encodeURIComponent(key)}`;
  const status = document.createElement('p');
  status.className = 'draft-status muted'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  form.prepend(status);
  const removeDraft = document.createElement('button');
  removeDraft.type = 'button'; removeDraft.className = 'btn secondary draft-delete'; removeDraft.textContent = 'Удалить черновик'; removeDraft.hidden = true;
  status.after(removeDraft);
  let revision = 0, timer = null, saving = false, dirty = false, online = navigator.onLine;
  const fields = () => {
    const values = {};
    const checkboxCounts = {};
    for (const control of form.elements) if (control.type === 'checkbox') checkboxCounts[control.name] = (checkboxCounts[control.name] || 0) + 1;
    for (const control of form.elements) {
      if (!control.name || /csrf|password|token|secret|auth|credential|creation_key/i.test(control.name) ||
          ['file','submit','button','reset'].includes(control.type) || control.disabled) continue;
      if (control.type === 'checkbox' || control.type === 'radio') {
        if (control.type === 'radio') { if (control.checked) values[control.name] = control.value; }
        else if (control.name.endsWith('[]') || ['student_ids','students','topics','student_topic_ids'].includes(control.name) || checkboxCounts[control.name] > 1) { (values[control.name] ||= []); if (control.checked) values[control.name].push(control.value); }
        else values[control.name] = control.checked;
      } else if (control.tagName === 'SELECT' && control.multiple) values[control.name] = [...control.selectedOptions].map(option => option.value);
      else values[control.name] = control.value;
    }
    return values;
  };
  const restore = data => {
    for (const control of form.elements) {
      if (!control.name || !(control.name in data) || /csrf|password|token|secret|auth|credential|creation_key/i.test(control.name)) continue;
      const value = data[control.name];
      if (control.type === 'file') continue;
      if (control.type === 'checkbox' || control.type === 'radio') {
        control.checked = Array.isArray(value) ? value.includes(control.value) : typeof value === 'boolean' ? value : String(value) === control.value;
      } else if (control.tagName === 'SELECT' && control.multiple) {
        [...control.options].forEach(option => option.selected = Array.isArray(value) && value.includes(option.value));
      } else if (typeof value === 'string' || typeof value === 'number') control.value = String(value);
    }
    form.dispatchEvent(new Event('input', {bubbles:true}));
    form.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const csrf = form.querySelector('[name="csrf"]')?.value || '';
  async function save() {
    if (!dirty || saving) return;
    if (!online) { status.textContent = 'Нет соединения. Черновик будет сохранён после восстановления связи.'; return; }
    saving = true; status.textContent = 'Сохраняю черновик…';
    const body = new URLSearchParams({csrf, revision: String(revision), payload: JSON.stringify(fields())});
    try {
      const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'}, body, credentials:'same-origin'});
      const result = await response.json();
      if (response.status === 409) { status.textContent = result.detail || 'Черновик изменён в другой вкладке. Обновите страницу и сравните ввод.'; status.classList.add('draft-conflict'); dirty = false; return; }
      if (!response.ok) throw new Error(result.detail || 'Не удалось сохранить черновик. Проверьте соединение и продолжите работу в этой форме.');
      revision = result.revision; dirty = false;
      removeDraft.hidden = false;
      status.textContent = `Черновик сохранён · срок хранения 30 дней · ${new Date(result.updated_at + 'Z').toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'})}`;
    } catch (error) { status.textContent = online ? (error.message || 'Не удалось сохранить черновик. Продолжайте работу в этой форме.') : 'Нет соединения. Черновик будет сохранён после восстановления связи.'; }
    finally { saving = false; }
  }
  fetch(endpoint, {credentials:'same-origin'}).then(response => response.json()).then(result => {
    if (result.draft) removeDraft.hidden = false;
    if (result.draft && window.confirm('Найден сохранённый черновик. Восстановить его в этой форме?')) {
      restore(result.draft); revision = result.revision; status.textContent = `Черновик восстановлен · хранится до 30 дней`;
    } else if (result.draft) revision = result.revision;
  }).catch(() => { status.textContent = 'Не удалось проверить черновик. Можно продолжить заполнение формы.'; });
  form.addEventListener('input', () => { dirty = true; clearTimeout(timer); timer = setTimeout(save, 800); });
  form.addEventListener('change', () => { dirty = true; clearTimeout(timer); timer = setTimeout(save, 800); });
  removeDraft.addEventListener('click', async () => {
    if (!window.confirm('Удалить сохранённый черновик? Это действие нельзя отменить.')) return;
    try {
      const response = await fetch(endpoint, {method:'DELETE', headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'}, body:new URLSearchParams({csrf, revision:String(revision)}), credentials:'same-origin'});
      if (!response.ok) { const result = await response.json(); throw new Error(result.detail || 'Обновите страницу перед удалением.'); }
      revision = 0; dirty = false; removeDraft.hidden = true; status.textContent = 'Черновик удалён. Текущий ввод остаётся в форме.';
    } catch (error) { status.textContent = error.message || 'Не удалось удалить черновик. Проверьте соединение и попробуйте снова.'; }
  });
  window.addEventListener('online', () => { online = true; if (dirty) save(); });
  window.addEventListener('offline', () => { online = false; if (dirty) status.textContent = 'Нет соединения. Ввод сохранён в форме и будет отправлен после восстановления связи.'; });
});

// A keyboard- and touch-friendly command palette. The server applies the same admin role check to every query.
(() => {
  const dialog = document.getElementById('productivity-dialog');
  const opener = document.querySelector('[data-palette-open]');
  const input = document.getElementById('productivity-query');
  const results = document.getElementById('productivity-results');
  if (!dialog || !opener || !input || !results) return;
  const shortcut = opener.querySelector('kbd');
  if (shortcut && /Mac|iPhone|iPad/.test(navigator.platform)) shortcut.textContent = '⌘ K';
  let previousFocus = null, active = -1, timer = null;
  const commands = [
    ['Создать занятие', '/admin/lessons/new'], ['Создать домашнее задание', '/admin/assignments/new'],
    ['Проверка работ', '/admin/reviews'], ['Аналитика', '/admin/analytics']
  ];
  const selectable = () => [...results.querySelectorAll('[role="option"]')];
  function render(items, query) {
    results.replaceChildren(); active = -1;
    if (!query) {
      const heading = document.createElement('p'); heading.className = 'muted'; heading.textContent = 'Начните вводить имя или название. Быстрые команды доступны ниже.'; results.append(heading); return;
    }
    const names = {students:'Ученики', homework:'Домашние задания', tests:'Тесты', topics:'Темы', lessons:'Занятия'};
    let found = 0;
    for (const [group, label] of Object.entries(names)) {
      const rows = items[group] || []; if (!rows.length) continue;
      const title = document.createElement('h3'); title.textContent = label; results.append(title);
      for (const row of rows) {
        const link = document.createElement('a'); link.href = row.url; link.setAttribute('role','option'); link.setAttribute('aria-selected','false');
        const strong = document.createElement('strong'); strong.textContent = row.title;
        const detail = document.createElement('small'); detail.textContent = row.detail;
        link.append(strong, detail); results.append(link); found++;
      }
    }
    if (!found) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = 'Ничего не найдено. Попробуйте часть имени или названия.'; results.append(empty); }
  }
  async function search() {
    const query = input.value.trim();
    if (!query) return render({}, '');
    try {
      const response = await fetch(`/admin/productivity/search?q=${encodeURIComponent(query)}`, {credentials:'same-origin', headers:{'Accept':'application/json'}});
      if (!response.ok) throw new Error();
      const data = await response.json(); render(data.groups, query);
    } catch { results.replaceChildren(); const message = document.createElement('p'); message.className='alert error'; message.textContent='Поиск временно недоступен. Проверьте соединение и повторите запрос.'; results.append(message); }
  }
  function open() { previousFocus = document.activeElement === document.body ? opener : document.activeElement; dialog.showModal(); input.value=''; render({},''); input.focus(); }
  function close() { dialog.close(); if (previousFocus?.isConnected) previousFocus.focus(); }
  opener.addEventListener('click', open);
  dialog.querySelector('[data-palette-close]').addEventListener('click', close);
  dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 160); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(); return; }
    const rows = selectable();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); if (!rows.length) return;
      active = active < 0 ? (event.key === 'ArrowDown' ? 0 : rows.length - 1) : (active + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
      rows.forEach((row, i) => row.setAttribute('aria-selected', String(i === active)));
      rows[active].scrollIntoView({block:'nearest'});
    } else if (event.key === 'Enter' && rows.length) { event.preventDefault(); rows[active < 0 ? 0 : active]?.click(); }
  });
  dialog.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => { window.location.href = button.dataset.command; }));
  dialog.querySelector('[data-next-lesson]').addEventListener('click', async () => {
    try { const response = await fetch('/admin/productivity/next-lesson', {credentials:'same-origin'}); const data = await response.json(); window.location.href = data.url; }
    catch { results.textContent = 'Нет соединения. Повторите попытку после восстановления связи.'; }
  });
  document.addEventListener('keydown', event => {
    if (dialog.open || event.defaultPrevented || event.isComposing || event.altKey || event.shiftKey || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k') return;
    const target = event.target, tag = target?.tagName?.toLowerCase();
    if (target?.isContentEditable || ['input','textarea','select'].includes(tag) || target?.closest('[contenteditable="true"]')) return;
    event.preventDefault(); open();
  });
})();

// Mark an event read only when the user follows its source link.
document.querySelectorAll('[data-notification-key]').forEach(link => link.addEventListener('click', () => {
  const csrf = document.querySelector('#notification-csrf [name="csrf"]')?.value;
  if (!csrf) return;
  const body = new URLSearchParams({csrf, key:link.dataset.notificationKey});
  fetch('/admin/productivity/notifications/read', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body, credentials:'same-origin', keepalive:true});
}));
