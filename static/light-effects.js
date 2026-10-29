/* Room-light controls use the existing single LED worker, not a second daemon. */
(() => {
  const root = document.getElementById('room-light-effects');
  if (!root) return;
  const modes = [['off', 'Алиса'], ['static', 'Цвет'], ['rgb', 'RGB'], ['rainbow', 'Радуга'], ['chase', 'По кругу'], ['breathe', 'Дыхание']];
  root.innerHTML = `<div class="card-head"><h3>Свет комнаты</h3><span class="meta">Эффекты подсветки</span></div>
    <div class="room-modes">${modes.map(([mode, title]) => `<button type="button" class="btn" data-room-effect="${mode}" aria-pressed="false"><span class="room-dots room-${mode}" aria-hidden="true"><i></i><i></i><i></i></span>${title}</button>`).join('')}</div>
    <div class="room-sliders"><label>Яркость <output data-room-output="brightness">35%</output><input type="range" data-room-setting="brightness" min="1" max="100" value="35"></label>
    <label>Скорость <output data-room-output="speed">1.0×</output><input type="range" data-room-setting="speed" min="0.2" max="3" step="0.1" value="1"></label></div>
    <div class="room-duration"><label><input type="checkbox" data-room-forever checked> Пока не выключу</label><label>Длительность <input type="number" class="field" data-room-seconds min="1" max="600" value="30" disabled> с</label></div>
    <p class="hint" data-room-status role="status"></p>`;
  const find = s => root.querySelector(s);
  const status = find('[data-room-status]');
  let state = null, available = false, busy = 0, revision = 0, queue = Promise.resolve();
  let color = [108, 71, 255];
  const controls = window.panelLightEffects = {supported: false, color: rgb => send('static', rgb)};
  function paint(value) {
    state = value;
    available = controls.supported = !!value.supported && Object.prototype.hasOwnProperty.call(value, 'effect');
    root.hidden = !value.supported;
    root.querySelectorAll('button').forEach(b => {
      b.disabled = !available;
      b.setAttribute('aria-pressed', String((value.effect?.mode || 'off') === b.dataset.roomEffect));
    });
    if (!available) { status.textContent = 'Для эффектов перезапустите сервер панели и обновите страницу.'; return; }
    const effect = value.effect;
    if (effect) {
      color = effect.color;
      for (const key of ['brightness', 'speed']) {
        const input = find(`[data-room-setting="${key}"]`);
        if (document.activeElement !== input) input.value = effect[key];
        showValue(input);
      }
      if (document.activeElement !== find('[data-room-seconds]')) find('[data-room-seconds]').value = effect.seconds || 30;
      find('[data-room-forever]').checked = effect.seconds === 0;
      find('[data-room-seconds]').disabled = effect.seconds === 0;
    }
    status.textContent = effect ? (value.reason === 'disconnected' ? 'Ждём безопасного соединения с подсветкой. ' : ['alice', 'alarm', 'volume', 'assistant', 'scene'].includes(value.reason) ? 'Анимация приостановлена: приоритет у Алисы или системной индикации. ' : '')
      + (effect.seconds === 0 ? 'Работает, пока не выключите.' : `Осталось ${Math.ceil(effect.remaining || 0)} с.`)
      + ' Можно закрыть страницу; сервер панели должен работать.'
      : 'Выберите эффект. «Алиса» возвращает штатную подсветку. Светомузыка включается отдельно и заменяет эффект.';
  }
  function showValue(input) {
    const key = input.dataset.roomSetting;
    find(`[data-room-output="${key}"]`).textContent = key === 'brightness' ? input.value + '%' : Number(input.value).toFixed(1) + '×';
  }
  async function refresh() {
    if (busy || document.hidden) return;
    const at = revision;
    try { const value = await api('/api/library/animations'); if (at === revision && !busy) paint(value); }
    catch (e) { status.textContent = e.message; }
  }
  function send(mode, rgb = color) {
    if (!available) return Promise.reject(new Error('Перезапустите сервер панели для включения эффектов'));
    const seconds = find('[data-room-forever]').checked ? 0 : Number(find('[data-room-seconds]').value);
    if (mode !== 'off' && (!Number.isFinite(seconds) || seconds < 0 || seconds > 600 || (!find('[data-room-forever]').checked && seconds < 1))) {
      return Promise.reject(new Error('Длительность: 1–600 секунд или «Пока не выключу»'));
    }
    const data = {action: 'effect', mode, color: [...rgb], seconds,
      brightness: Number(find('[data-room-setting="brightness"]').value), speed: Number(find('[data-room-setting="speed"]').value)};
    revision++; busy++;
    const request = queue.catch(() => {}).then(async () => {
      if (mode === 'off') { await api('/api/ring/reset', {}); paint(await api('/api/library/animations')); }
      else paint(await api('/api/library/animations', data));
    }).finally(() => { busy--; });
    queue = request;
    return request;
  }
  const report = promise => promise.catch(e => { status.textContent = e.message; toast(e.message, true); });
  root.querySelectorAll('[data-room-effect]').forEach(b => b.onclick = () => {
    const hex = document.getElementById('ring-color')?.value;
    const selected = /^#[0-9a-f]{6}$/i.test(hex || '') ? [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)) : color;
    report(send(b.dataset.roomEffect, selected));
  });
  root.querySelectorAll('[data-room-setting]').forEach(input => {
    input.oninput = () => showValue(input);
    input.onchange = () => { if (state?.effect) report(send(state.effect.mode)); };
  });
  find('[data-room-forever]').onchange = e => {
    find('[data-room-seconds]').disabled = e.target.checked;
    if (state?.effect) report(send(state.effect.mode));
  };
  find('[data-room-seconds]').onchange = () => { if (state?.effect) report(send(state.effect.mode)); };
  refresh();
  setInterval(refresh, 2000);
})();
