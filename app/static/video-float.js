/* Move the existing remote tile. LiveKit stays connected through video-call.js. */
(() => {
  const page = document.querySelector('[data-video-meeting]');
  const tile = page?.querySelector('.video-remote');
  const toggle = page?.querySelector('[data-video-float-toggle]');
  const handle = page?.querySelector('[data-video-float-drag]');
  if (!page || !tile || !toggle || !handle) return;

  const margin = 12;
  let floating = false;
  let dragging = null;
  const clamp = (value, min, max) => Math.min(Math.max(value, min), Math.max(min, max));

  function place(left, top) {
    const width = tile.getBoundingClientRect().width;
    const height = tile.getBoundingClientRect().height;
    tile.style.left = `${clamp(left, margin, innerWidth - width - margin)}px`;
    tile.style.top = `${clamp(top, margin, innerHeight - height - margin)}px`;
  }

  function restore() {
    if (!floating) return;
    floating = false;
    dragging = null;
    tile.classList.remove('is-floating');
    tile.style.left = '';
    tile.style.top = '';
    toggle.textContent = 'Открепить видео';
    toggle.setAttribute('aria-pressed', 'false');
    handle.hidden = true;
  }

  toggle.addEventListener('click', () => {
    if (floating) { restore(); return; }
    if (page.dataset.videoLayout === 'compact' || page.dataset.videoState === 'ended') return;
    const origin = tile.getBoundingClientRect();
    floating = true;
    tile.classList.add('is-floating');
    toggle.textContent = 'Вернуть в сетку';
    toggle.setAttribute('aria-pressed', 'true');
    handle.hidden = false;
    place(origin.right - tile.getBoundingClientRect().width, origin.top);
  });

  handle.addEventListener('pointerdown', (event) => {
    if (!floating || event.button !== 0) return;
    const rect = tile.getBoundingClientRect();
    dragging = {x:event.clientX, y:event.clientY, left:rect.left, top:rect.top};
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  handle.addEventListener('pointermove', (event) => {
    if (dragging) place(dragging.left + event.clientX - dragging.x,
                         dragging.top + event.clientY - dragging.y);
  });
  const stopDragging = () => { dragging = null; };
  handle.addEventListener('pointerup', stopDragging);
  handle.addEventListener('pointercancel', stopDragging);
  handle.addEventListener('keydown', (event) => {
    if (!floating || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
    const rect = tile.getBoundingClientRect();
    const step = event.shiftKey ? 40 : 12;
    place(rect.left + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0),
          rect.top + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0));
    event.preventDefault();
  });
  window.addEventListener('resize', () => {
    if (floating) {
      const rect = tile.getBoundingClientRect();
      place(rect.left, rect.top);
    }
  });
  const observer = new MutationObserver(() => {
    if (page.dataset.videoLayout === 'compact' || page.dataset.videoState === 'ended') restore();
  });
  observer.observe(page, {attributes:true, attributeFilter:['data-video-layout','data-video-state']});
  window.addEventListener('pagehide', () => observer.disconnect(), {once:true});
})();
