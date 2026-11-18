(() => {
  const page = document.querySelector('[data-video-meeting]');
  if (!page) return;

  const meetingId = page.dataset.videoMeeting;
  const apiPath = `/api/video/meetings/${encodeURIComponent(meetingId)}`;
  const csrf = page.querySelector('[data-video-csrf]').value;
  const status = page.querySelector('[data-video-status]');
  const errorBox = page.querySelector('[data-video-error]');
  const remoteLabel = page.querySelector('[data-video-remote-label]');
  const remoteMedia = page.querySelector('[data-video-remote]');
  const localMedia = page.querySelector('[data-video-local]');
  const remoteScreenTile = page.querySelector('[data-video-remote-screen-tile]');
  const remoteScreenMedia = page.querySelector('[data-video-screen-remote]');
  const stage = page.querySelector('[data-video-stage]');
  const localLabel = page.querySelector('[data-video-local-label]');
  const localScreenTile = page.querySelector('[data-video-local-screen-tile]');
  const localScreenMedia = page.querySelector('[data-video-screen-local]');
  const joinButton = page.querySelector('[data-video-join]');
  const micButton = page.querySelector('[data-video-mic]');
  const cameraButton = page.querySelector('[data-video-camera]');
  const screenButton = page.querySelector('[data-video-screen]');
  const audioButton = page.querySelector('[data-video-audio]');
  const leaveButton = page.querySelector('[data-video-leave]');
  const endButton = page.querySelector('[data-video-end]');
  const livekit = window.LivekitClient;

  let room = null;
  let connecting = false;
  let joined = false;
  let ended = page.dataset.videoState === 'ended';
  let historyOpening = false;
  let ending = page.dataset.videoState === 'ending';
  let manualDisconnect = false;
  let hasAttemptedJoin = false;
  let localCameraTrack = null;
  let localScreenTrack = null;
  let endingRequest = false;
  let leaving = false;
  let releasePromise = null;
  const mediaBusy = new Set();
  const requests = new Set();
  const requestTimeoutMs = Math.min(30000, Math.max(1000, Number(page.dataset.videoRequestTimeoutMs) || 12000));
  let disposed = false;
  let joinGeneration = 0;

  const setStatus = (message) => { status.textContent = message; };
  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  };

  function trackEnabled(source) {
    const publication = room?.localParticipant.getTrackPublication(source);
    const track = publication?.track;
    return Boolean(track && !publication.isMuted && track.mediaStreamTrack?.readyState !== 'ended');
  }

  function updateControls() {
    const microphoneOn = Boolean(livekit && trackEnabled(livekit.Track.Source.Microphone));
    const cameraOn = Boolean(livekit && trackEnabled(livekit.Track.Source.Camera));
    const screenOn = Boolean(livekit && trackEnabled(livekit.Track.Source.ScreenShare));
    joinButton.disabled = connecting || joined || leaving || ending || ended;
    micButton.disabled = !joined || ending || ended || mediaBusy.has('microphone');
    cameraButton.disabled = !joined || ending || ended || mediaBusy.has('camera');
    screenButton.disabled = !joined || ending || ended || mediaBusy.has('screen') || !navigator.mediaDevices?.getDisplayMedia;
    leaveButton.disabled = !joined || leaving;
    if (endButton) endButton.disabled = ended || endingRequest;
    micButton.textContent = microphoneOn ? 'Выключить микрофон' : 'Включить микрофон';
    cameraButton.textContent = cameraOn ? 'Выключить камеру' : 'Включить камеру';
    screenButton.textContent = screenOn ? 'Остановить показ экрана' : 'Показать экран';
    micButton.setAttribute('aria-pressed', String(microphoneOn));
    cameraButton.setAttribute('aria-pressed', String(cameraOn));
    screenButton.setAttribute('aria-pressed', String(screenOn));
  }

  function deviceError(kind, error) {
    const names = {
      microphone: ['микрофону', 'Микрофон', 'микрофон'],
      camera: ['камере', 'Камера', 'камеру'],
      screen: ['демонстрации экрана', 'Демонстрация экрана', 'демонстрацию экрана'],
    }[kind];
    if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') {
      return `Доступ к ${names[0]} запрещён браузером. Разрешите его в настройках сайта и попробуйте снова.`;
    }
    if (error?.name === 'NotFoundError' || error?.name === 'DevicesNotFoundError') {
      if (kind === 'screen') return 'Демонстрация экрана недоступна в этом браузере.';
      return `${names[1]} ${kind === 'camera' ? 'недоступна' : 'недоступен'}. Проверьте подключение устройства.`;
    }
    return `Не удалось включить ${names[2]}. Проверьте устройство и попробуйте снова.`;
  }

  async function apiRequest(path, options = {}) {
    const controller = new AbortController();
    requests.add(controller);
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = window.setTimeout(() => {
        controller.abort();
        reject(new DOMException('Request timed out', 'AbortError'));
      }, requestTimeoutMs);
    });
    try {
      const operation = (async () => {
        const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal, ...options });
        const result = await response.json();
        if (disposed || controller.signal.aborted) throw new DOMException('Request cancelled', 'AbortError');
        return { response, result };
      })();
      return await Promise.race([operation, timeout]);
    } catch (error) {
      if (controller.signal.aborted && !disposed) {
        throw Object.assign(new Error('Сеть не ответила вовремя. Проверьте соединение и повторите попытку.'),
          { userMessage: true, timedOut: true });
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
      requests.delete(controller);
    }
  }

  async function apiGet() {
    const { response, result } = await apiRequest(apiPath);
    if (!response.ok) throw Object.assign(new Error(result.detail || 'Не удалось проверить состояние встречи.'), { userMessage: true });
    return result;
  }

  async function apiPost(suffix) {
    const { response, result } = await apiRequest(`${apiPath}/${suffix}`, {
      method: 'POST', credentials: 'same-origin',
      body: new URLSearchParams({ csrf }),
    });
    if (!response.ok) throw Object.assign(new Error(result.detail || 'Операция не выполнена. Повторите попытку.'), { userMessage: true });
    return result;
  }

  function attachRemote(track, publication) {
    const element = track.attach();
    element.autoplay = true;
    if (track.kind === livekit.Track.Kind.Video) element.playsInline = true;
    const isScreen = publication?.source === livekit.Track.Source.ScreenShare;
    (isScreen ? remoteScreenMedia : remoteMedia).append(element);
    if (isScreen) remoteScreenTile.hidden = false;
    syncRemotePresence();
    syncStageLayout();
  }

  function syncStageLayout() {
    if (!stage) return;
    const remoteCameraOn = [...(room?.remoteParticipants?.values() || [])].some(participant =>
      [...(participant.trackPublications?.values() || [])].some(publication =>
        publication.source === livekit.Track.Source.Camera && !publication.isMuted));
    stage.dataset.videoHasRemoteCamera = String(remoteCameraOn && Boolean(remoteMedia.querySelector('video')));
    stage.dataset.videoHasLocalCamera = String(Boolean(localMedia.querySelector('video')));
    stage.dataset.videoPrimary = remoteScreenMedia.querySelector('video') && !remoteScreenTile.hidden
      ? 'remote-screen' : localScreenTrack && !localScreenTile.hidden ? 'local-screen' : 'remote';
    if (localLabel) localLabel.textContent = stage.dataset.videoHasLocalCamera === 'true'
      ? 'Вы' : 'Вы · камера выключена';
  }

  function syncRemotePresence() {
    const participants = room?.remoteParticipants;
    if (!participants?.size) { remoteLabel.textContent = 'Ожидание второго участника'; return; }
    const camera = [...participants.values()].flatMap(participant =>
      [...(participant.trackPublications?.values() || [])]).find(publication => publication.source === livekit.Track.Source.Camera);
    remoteLabel.textContent = !camera ? 'Собеседник подключён без камеры'
      : camera.isMuted ? 'Камера собеседника выключена'
        : 'Собеседник подключён';
  }

  function detachTrack(track) {
    for (const element of track.detach()) element.remove();
  }

  function showLocalCamera() {
    if (localCameraTrack) detachTrack(localCameraTrack);
    localCameraTrack = null;
    localMedia.replaceChildren();
    const track = room?.localParticipant.getTrackPublication(livekit.Track.Source.Camera)?.track;
    if (trackEnabled(livekit.Track.Source.Camera)) {
      localCameraTrack = track;
      const element = track.attach();
      element.muted = true;
      element.autoplay = true;
      element.playsInline = true;
      localMedia.append(element);
    }
    syncStageLayout();
  }

  function showLocalScreen() {
    if (localScreenTrack) detachTrack(localScreenTrack);
    localScreenTrack = null;
    localScreenMedia.replaceChildren();
    const track = room?.localParticipant.getTrackPublication(livekit.Track.Source.ScreenShare)?.track;
    if (trackEnabled(livekit.Track.Source.ScreenShare)) {
      localScreenTrack = track;
      const element = track.attach();
      element.muted = true;
      element.autoplay = true;
      element.playsInline = true;
      localScreenMedia.append(element);
      track.mediaStreamTrack?.addEventListener('ended', () => {
        if (localScreenTrack !== track) return;
        showLocalScreen();
        updateControls();
      }, { once: true });
    }
    localScreenTile.hidden = !localScreenTrack;
    syncStageLayout();
  }

  function registerRoomEvents(activeRoom) {
    activeRoom.on(livekit.RoomEvent.TrackSubscribed, (track, publication) => {
      if (room === activeRoom) attachRemote(track, publication);
    });
    activeRoom.on(livekit.RoomEvent.TrackUnsubscribed, (track, publication) => {
      detachTrack(track);
      if (room === activeRoom && publication?.source === livekit.Track.Source.ScreenShare) {
        remoteScreenTile.hidden = !remoteScreenMedia.querySelector('video');
      }
      if (room === activeRoom) { syncRemotePresence(); syncStageLayout(); }
    });
    activeRoom.on(livekit.RoomEvent.ParticipantConnected, () => {
      if (room !== activeRoom) return;
      syncRemotePresence();
      setStatus('Соединение установлено');
    });
    activeRoom.on(livekit.RoomEvent.ParticipantDisconnected, () => {
      if (room !== activeRoom) return;
      remoteMedia.replaceChildren();
      remoteScreenMedia.replaceChildren();
      remoteScreenTile.hidden = true;
      syncStageLayout();
      remoteLabel.textContent = 'Ожидание второго участника';
      setStatus('Собеседник покинул звонок');
    });
    for (const event of [livekit.RoomEvent.TrackPublished, livekit.RoomEvent.TrackUnpublished,
      livekit.RoomEvent.TrackMuted, livekit.RoomEvent.TrackUnmuted]) {
      if (event) activeRoom.on(event, () => { if (room === activeRoom) { syncRemotePresence(); syncStageLayout(); } });
    }
    activeRoom.on(livekit.RoomEvent.Reconnecting, () => {
      if (room === activeRoom) setStatus('Восстанавливаем соединение…');
    });
    activeRoom.on(livekit.RoomEvent.Reconnected, () => {
      if (room !== activeRoom) return;
      setStatus('Соединение восстановлено');
      syncRemotePresence();
      showLocalCamera();
      showLocalScreen();
      updateControls();
    });
    activeRoom.on(livekit.RoomEvent.AudioPlaybackStatusChanged, () => {
      if (room === activeRoom) audioButton.hidden = activeRoom.canPlaybackAudio;
    });
    activeRoom.on(livekit.RoomEvent.LocalTrackPublished, (publication) => {
      queueMicrotask(() => {
        if (room !== activeRoom) return;
        if (publication.source === livekit.Track.Source.Camera) showLocalCamera();
        if (publication.source === livekit.Track.Source.ScreenShare) showLocalScreen();
        updateControls();
      });
    });
    activeRoom.on(livekit.RoomEvent.LocalTrackUnpublished, (publication) => {
      queueMicrotask(() => {
        if (room !== activeRoom) return;
        if (publication.source === livekit.Track.Source.Camera) showLocalCamera();
        if (publication.source === livekit.Track.Source.ScreenShare) showLocalScreen();
        updateControls();
      });
    });
    const syncLocalMute = (publication, participant) => {
      if (room === activeRoom && participant === activeRoom.localParticipant) {
        if (publication.source === livekit.Track.Source.Camera) showLocalCamera();
        updateControls();
      }
    };
    activeRoom.on(livekit.RoomEvent.TrackMuted, syncLocalMute);
    activeRoom.on(livekit.RoomEvent.TrackUnmuted, syncLocalMute);
    activeRoom.on(livekit.RoomEvent.Disconnected, async () => {
      if (room !== activeRoom || manualDisconnect) return;
      await releaseRoom();
      try {
        const state = await apiGet();
        if (state.status === 'ended' || state.status === 'ending') {
          ended = state.status === 'ended';
          ending = state.status === 'ending';
          page.dataset.videoState = state.status;
          page.dispatchEvent(new Event('video:statechange'));
          setStatus(ended ? 'Встреча завершена' : 'Встреча завершается');
          if (ended) await showHistory();
        } else {
          setStatus('Соединение прервано. Подключитесь снова.');
        }
      } catch {
        setStatus('Соединение прервано. Проверьте сеть и подключитесь снова.');
      }
      updateControls();
    });
  }

  function releaseRoom() {
    if (releasePromise) return releasePromise;
    releasePromise = (async () => {
      leaving = true;
      const activeRoom = room;
      room = null;
      joined = false;
      mediaBusy.clear();
      audioButton.hidden = true;
      if (localCameraTrack) detachTrack(localCameraTrack);
      if (localScreenTrack) detachTrack(localScreenTrack);
      localCameraTrack = null;
      localScreenTrack = null;
      updateControls();
      if (activeRoom) {
        for (const publication of activeRoom.localParticipant?.trackPublications?.values() || []) {
          if (publication.track) detachTrack(publication.track);
        }
        for (const participant of activeRoom.remoteParticipants?.values() || []) {
          for (const publication of participant.trackPublications.values()) {
            if (publication.track) detachTrack(publication.track);
          }
        }
        try { await activeRoom.disconnect(true); } catch { /* connection already closed */ }
      }
      localMedia.replaceChildren();
      localScreenMedia.replaceChildren();
      remoteMedia.replaceChildren();
      remoteScreenMedia.replaceChildren();
      localScreenTile.hidden = true;
      remoteScreenTile.hidden = true;
      remoteLabel.textContent = 'Ожидание второго участника';
      syncStageLayout();
    })().finally(() => {
      leaving = false;
      releasePromise = null;
      updateControls();
    });
    return releasePromise;
  }

  async function showHistory() {
    if (historyOpening || page.dataset.videoState !== 'ended') return;
    historyOpening = true;
    manualDisconnect = true;
    await releaseRoom();
    if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
      window.location.replace(window.location.href);
    }
  }

  async function enableInitialDevices(activeRoom) {
    try {
      await activeRoom.localParticipant.setMicrophoneEnabled(true);
      if (room === activeRoom && !trackEnabled(livekit.Track.Source.Microphone)) {
        showError('Микрофон недоступен. Вы можете участвовать без него.');
      }
    } catch (error) {
      if (room === activeRoom) showError(deviceError('microphone', error));
    }
    if (room !== activeRoom) return;
    try {
      await activeRoom.localParticipant.setCameraEnabled(true);
      if (!trackEnabled(livekit.Track.Source.Camera)) {
        showError('Камера недоступна. Вы можете участвовать без неё.');
      }
      showLocalCamera();
    } catch (error) {
      if (room === activeRoom) showError(deviceError('camera', error));
    }
    updateControls();
  }

  async function join() {
    if (connecting || joined || leaving || ending || ended) return;
    if (!livekit) {
      showError('Клиент видеозвонка не загрузился. Обновите страницу.');
      return;
    }
    connecting = true;
    const generation = ++joinGeneration;
    showError('');
    setStatus('Подключаемся…');
    updateControls();
    try {
      const state = await apiGet();
      if (disposed || generation !== joinGeneration || ended) return;
      if (state.status !== 'active') {
        ended = state.status === 'ended';
        ending = state.status === 'ending';
        page.dataset.videoState = state.status;
        page.dispatchEvent(new Event('video:statechange'));
        setStatus(ended ? 'Встреча завершена' : ending ? 'Встреча завершается' : 'Встреча ещё создаётся');
        if (ended) await showHistory();
        return;
      }
      const endpoint = hasAttemptedJoin ? 'token/refresh' : 'token';
      hasAttemptedJoin = true;
      const credentials = await apiPost(endpoint);
      if (disposed || generation !== joinGeneration || ended) return;
      const activeRoom = new livekit.Room({ adaptiveStream: true, dynacast: true });
      room = activeRoom;
      registerRoomEvents(activeRoom);
      await activeRoom.connect(credentials.url, credentials.token, { autoSubscribe: true });
      if (room !== activeRoom || disposed || ended || generation !== joinGeneration) {
        await activeRoom.disconnect(true);
        return;
      }
      joined = true;
      syncRemotePresence();
      setStatus(activeRoom.remoteParticipants.size ? 'Соединение установлено' : 'Ожидание второго участника');
      updateControls();
      await enableInitialDevices(activeRoom);
      audioButton.hidden = activeRoom.canPlaybackAudio;
    } catch (error) {
      await releaseRoom();
      if (!disposed && !ended) {
        showError(error.userMessage ? error.message : 'Не удалось подключиться. Проверьте сеть и попробуйте снова.');
        setStatus('Не удалось подключиться');
      }
    } finally {
      connecting = false;
      updateControls();
    }
  }

  joinButton.addEventListener('click', join);
  micButton.addEventListener('click', async () => {
    if (!room || !joined || mediaBusy.has('microphone')) return;
    const activeRoom = room;
    const source = livekit.Track.Source.Microphone;
    const enable = !trackEnabled(source);
    mediaBusy.add('microphone');
    updateControls();
    try {
      await activeRoom.localParticipant.setMicrophoneEnabled(enable);
      if (room === activeRoom) {
        if (enable && !trackEnabled(source)) showError('Микрофон недоступен. Проверьте устройство.');
        else showError('');
      }
    } catch (error) { if (room === activeRoom) showError(deviceError('microphone', error)); }
    mediaBusy.delete('microphone');
    updateControls();
  });
  cameraButton.addEventListener('click', async () => {
    if (!room || !joined || mediaBusy.has('camera')) return;
    const activeRoom = room;
    const source = livekit.Track.Source.Camera;
    const enable = !trackEnabled(source);
    mediaBusy.add('camera');
    updateControls();
    try {
      await activeRoom.localParticipant.setCameraEnabled(enable);
      if (room === activeRoom) {
        showLocalCamera();
        if (enable && !trackEnabled(source)) showError('Камера недоступна. Проверьте устройство.');
        else showError('');
      }
    } catch (error) { if (room === activeRoom) showError(deviceError('camera', error)); }
    mediaBusy.delete('camera');
    updateControls();
  });
  screenButton.addEventListener('click', async () => {
    if (!room || !joined || mediaBusy.has('screen')) return;
    const activeRoom = room;
    const source = livekit.Track.Source.ScreenShare;
    const enable = !trackEnabled(source);
    mediaBusy.add('screen');
    updateControls();
    try {
      await activeRoom.localParticipant.setScreenShareEnabled(enable);
      if (room === activeRoom) {
        showLocalScreen();
        if (enable && !trackEnabled(source)) showError('Не удалось начать показ экрана. Попробуйте снова.');
        else showError('');
      }
    } catch (error) { if (room === activeRoom) showError(deviceError('screen', error)); }
    mediaBusy.delete('screen');
    updateControls();
  });
  audioButton.addEventListener('click', async () => {
    if (!room) return;
    try {
      await room.startAudio();
      audioButton.hidden = room.canPlaybackAudio;
    } catch { showError('Браузер заблокировал воспроизведение звука. Нажмите кнопку ещё раз.'); }
  });
  leaveButton.addEventListener('click', async () => {
    if (!joined || leaving) return;
    manualDisconnect = true;
    await releaseRoom();
    manualDisconnect = false;
    if (!ended && !ending) setStatus('Вы покинули звонок');
  });
  if (endButton) endButton.addEventListener('click', async () => {
    if (ended || endingRequest) return;
    if (!window.confirm('Завершить видеозвонок для вас и ученика?')) return;
    endingRequest = true;
    updateControls();
    showError('');
    try {
      const before = await apiGet();
      if (before.status === 'ended') {
        ended = true; page.dataset.videoState = 'ended';
        page.dispatchEvent(new Event('video:statechange'));
        await showHistory();
        return;
      }
      await apiPost('end');
      ended = true;
      ending = false;
      page.dataset.videoState = 'ended';
      page.dispatchEvent(new Event('video:statechange'));
      manualDisconnect = true;
      await releaseRoom();
      manualDisconnect = false;
      setStatus('Встреча завершена для всех');
      await showHistory();
    } catch (error) {
      if (error.timedOut) {
        try {
          const actual = await apiGet();
          if (actual.status === 'ended') {
            ended = true; page.dataset.videoState = 'ended';
            page.dispatchEvent(new Event('video:statechange'));
            await showHistory();
            return;
          }
        } catch { /* The result remains uncertain; the next click checks state again. */ }
      }
      showError(error.userMessage ? error.message : 'Не удалось завершить встречу. Повторите попытку.');
    } finally {
      endingRequest = false;
      updateControls();
    }
  });

  if (ended) setStatus('Встреча завершена');
  else if (ending) setStatus('Встреча завершается');
  else setStatus('Нажмите «Подключиться», чтобы войти в звонок');
  updateControls();

  let polling = false;
  const poll = window.setInterval(async () => {
    if (ended) { window.clearInterval(poll); return; }
    if (polling) return;
    polling = true;
    try {
      const state = await apiGet();
      if (state.status === 'ended' || state.status === 'ending') {
        ended = state.status === 'ended';
        ending = state.status === 'ending';
        page.dataset.videoState = state.status;
        page.dispatchEvent(new Event('video:statechange'));
        manualDisconnect = true;
        await releaseRoom();
        manualDisconnect = false;
        setStatus(state.status === 'ended' ? 'Встреча завершена' : 'Встреча завершается');
        if (ended) await showHistory();
      }
    } catch { /* A temporary state-check failure does not interrupt media. */ }
    finally { polling = false; }
  }, 10000);
  window.addEventListener('pagehide', () => {
    disposed = true;
    joinGeneration++;
    for (const controller of requests) controller.abort();
    window.clearInterval(poll);
    manualDisconnect = true;
    void releaseRoom();
  });
})();
