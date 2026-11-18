(() => {
  const page = document.querySelector('[data-video-meeting]');
  if (!page?.querySelector('[data-video-extras]')) return;
  if (!page || page.dataset.videoState === 'ended') return;

  const chat = page.querySelector('[data-video-chat]');
  const materials = page.querySelector('[data-video-materials]');
  const extras = page.querySelector('[data-video-extras]');
  const openers = {
    chat: page.querySelector('[data-video-chat-jump]'),
    materials: page.querySelector('[data-video-materials-jump]'),
  };
  const panels = { chat, materials };
  const tabs = [...page.querySelectorAll('[data-video-side-tab]')];
  let compact = false;
  let selected = null;

  function render() {
    page.dataset.videoLayout = compact ? 'compact' : 'wide';
    extras.hidden = compact && !selected;
    for (const name of ['chat', 'materials']) {
      const visible = selected === name;
      panels[name].hidden = !visible;
      openers[name].setAttribute('aria-expanded', String(visible));
    }
    for (const tab of tabs) tab.setAttribute('aria-pressed', String(!compact && selected === tab.dataset.videoSideTab));
  }

  function select(name, focusPanel = true) {
    if (compact && selected === name) {
      selected = null;
      render();
      openers[name].focus();
      return;
    }
    selected = name;
    render();
    if (name === 'chat') {
      const body = chat.querySelector('[data-video-chat-body]');
      if (body.hidden) chat.querySelector('[data-video-chat-toggle]').click();
    }
    if (compact) panels[name].scrollIntoView({ block: 'start', behavior: 'smooth' });
    if (focusPanel) {
      const heading = panels[name].querySelector('h2');
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }

  for (const name of ['chat', 'materials']) {
    openers[name].addEventListener('click', () => select(name));
    panels[name].querySelector(`[data-video-panel-close="${name}"]`)
      .addEventListener('click', () => {
        selected = null;
        render();
        openers[name].focus();
      });
  }
  for (const tab of tabs) tab.addEventListener('click', () => select(tab.dataset.videoSideTab, false));
  page.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !compact || !selected) return;
    if (!panels[selected].contains(document.activeElement)) return;
    const previous = selected;
    selected = null;
    render();
    openers[previous].focus();
    event.preventDefault();
  });

  const checkLayout = () => {
    const next = page.getBoundingClientRect().width < 900;
    if (next !== compact) {
      if (next) {
        const focusedPanel = Object.entries(panels).find(([, panel]) => panel.contains(document.activeElement));
        if (focusedPanel) openers[focusedPanel[0]].focus();
        selected = null;
      }
      compact = next;
      if (!compact && !selected) selected = 'chat';
      render();
    }
  };
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(checkLayout);
    observer.observe(page);
    window.addEventListener('pagehide', () => observer.disconnect());
  } else {
    window.addEventListener('resize', checkLayout);
    window.addEventListener('pagehide', () => window.removeEventListener('resize', checkLayout));
  }
  compact = page.getBoundingClientRect().width < 900;
  selected = compact ? null : 'chat';
  render();
})();
