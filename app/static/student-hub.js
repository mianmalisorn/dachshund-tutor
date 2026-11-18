(() => {
  const hub = document.querySelector('.student-hub[data-student-id]');
  const scroller = document.querySelector('.workspace-scroll');
  if (!hub || !scroller) return;

  const studentId = hub.dataset.studentId;
  const key = `tutor.student-hub.${studentId}`;
  const section = new URLSearchParams(window.location.search).get('section') || 'overview';
  const scrollMode = () => window.matchMedia('(max-width: 800px)').matches ? 'page' : 'workspace';

  const save = () => {
    try {
      const mode = scrollMode();
      sessionStorage.setItem(key, JSON.stringify({
        section, mode, scrollTop: mode === 'page' ? window.scrollY : scroller.scrollTop,
      }));
    } catch (_) {
      // Storage may be disabled; the card remains fully usable without restoration.
    }
  };

  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    sessionStorage.removeItem(key);
    if (saved && saved.section === section && saved.mode === scrollMode()
        && !window.location.hash && Number.isFinite(saved.scrollTop)) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (saved.mode === 'page') window.scrollTo(0, saved.scrollTop);
        else scroller.scrollTop = saved.scrollTop;
      }));
    }
  } catch (_) {
    // Storage may be disabled; the card remains fully usable without restoration.
  }

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (link && link.pathname.startsWith(`/admin/students/${studentId}/`)) save();
  });
  document.addEventListener('submit', event => {
    if (event.target.closest('.student-hub')) save();
  });
})();
