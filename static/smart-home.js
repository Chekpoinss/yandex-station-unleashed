/* No device commands on discovery, refresh or rendering. */
(() => {
  const root = document.getElementById('tab-smarthome');
  if (!root) return;
  const node = id => document.getElementById(id), escape = value => esc(String(value ?? ''));
  const names = {on: 'Питание', brightness: 'Яркость', temperature: 'Температура', temperature_k: 'Температура света',
    humidity: 'Влажность', volume: 'Громкость', open: 'Открытие', fan_speed: 'Скорость вентилятора',
    thermostat: 'Термостат', heat: 'Нагрев', program: 'Программа', backlight: 'Подсветка', mute: 'Без звука',
    pause: 'Пауза', ionization: 'Ионизация', oscillation: 'Поворот', rgb: 'Цвет', scene: 'Цветовая сцена',
    illumination: 'Освещённость', battery_level: 'Заряд', pressure: 'Давление', motion: 'Движение',
    opened: 'Открыто', water_leak: 'Протечка', smoke: 'Дым', co2_level: 'CO₂', power: 'Мощность'};
  const modes = {auto: 'Авто', heat: 'Нагрев', cool: 'Охлаждение', dry: 'Осушение', fan_only: 'Вентиляция',
    low: 'Низкий', medium: 'Средний', high: 'Высокий', min: 'Минимум', max: 'Максимум', eco: 'Эко',
    quiet: 'Тихий', turbo: 'Турбо', normal: 'Обычный', sleep: 'Сон', one: 'Первый', two: 'Второй', three: 'Третий'};
  const units = {'unit.percent': '%', 'unit.temperature.celsius': '°C', 'unit.temperature.kelvin': 'K',
    'unit.illumination.lux': 'лк', 'unit.pressure.mmhg': 'мм рт. ст.', 'unit.power.watt': 'Вт', 'unit.density.ppm': 'ppm'};
  let devices = [], connected = false, loading = false, timer, qrTimer, generation = 0;
  const expanded = new Set(), detailed = new Set(), busy = new Set(), reading = new Set();
  const label = value => names[value] || String(value || '').replaceAll('_', ' ');
  function notice(text, error = false) {
    node('sh-status').textContent = text;
    node('sh-status').classList.toggle('sh-error', error);
  }
  function descriptors(device) {
    const result = [];
    for (const cap of device.capabilities || []) {
      const p = cap.parameters || {}, s = cap.state || {}, instance = p.instance || s.instance || (cap.type === 'devices.capabilities.on_off' ? 'on' : '');
      const base = {type: cap.type, instance, label: label(instance), value: s.value};
      if (['devices.capabilities.on_off', 'devices.capabilities.toggle'].includes(cap.type)) result.push({...base, kind: 'boolean'});
      else if (cap.type === 'devices.capabilities.range') {
        const range = p.range || {};
        const precision = Number.isFinite(range.precision) && range.precision > 0 ? range.precision : 1;
        if (p.random_access === false) result.push({...base, kind: 'relative', step: precision});
        else if (Number.isFinite(range.min) && Number.isFinite(range.max)) result.push({...base, kind: 'range', min: range.min, max: range.max, precision, unit: units[p.unit] || ''});
      } else if (cap.type === 'devices.capabilities.mode' && p.modes?.length) {
        result.push({...base, kind: 'select', options: p.modes.map(m => ({value: m.value, name: modes[m.value] || label(m.value)}))});
      } else if (cap.type === 'devices.capabilities.color_setting') {
        if (p.color_model === 'rgb') result.push({...base, instance: 'rgb', label: 'Цвет', kind: 'color', value: s.instance === 'rgb' ? s.value : null});
        if (Number.isFinite(p.temperature_k?.min) && Number.isFinite(p.temperature_k?.max)) result.push({...base, instance: 'temperature_k', label: 'Температура света', kind: 'range',
          ...p.temperature_k, precision: 1, unit: 'K', value: s.instance === 'temperature_k' ? s.value : null});
        if (p.color_scene?.scenes?.length) result.push({...base, instance: 'scene', label: 'Цветовая сцена', kind: 'select', value: s.instance === 'scene' ? s.value : null,
          options: p.color_scene.scenes.map(m => ({value: m.id, name: m.name || m.id}))});
      }
    }
    return result.filter(d => d.instance);
  }
  function control(d, index, offline) {
    const disabled = offline ? ' disabled' : '', title = escape(d.label);
    if (d.kind === 'boolean') return `<div class="sh-control"><span>${title}</span><div class="seg"><button type="button" data-sh-command="${index}" data-value="true" aria-pressed="${d.value === true}"${disabled}>Вкл</button><button type="button" data-sh-command="${index}" data-value="false" aria-pressed="${d.value === false}"${disabled}>Выкл</button></div></div>`;
    if (d.kind === 'relative') return `<div class="sh-control"><span>${title}</span><div class="row"><button class="btn sm" type="button" data-sh-command="${index}" data-relative="-1" aria-label="Уменьшить: ${title}"${disabled}>−</button><span>${escape(d.value ?? '—')}</span><button class="btn sm" type="button" data-sh-command="${index}" data-relative="1" aria-label="Увеличить: ${title}"${disabled}>+</button></div></div>`;
    let input;
    if (d.kind === 'select') input = `<select class="field" name="value" aria-label="${title}"${disabled}>${d.value == null ? '<option value="" disabled selected>Выберите режим</option>' : ''}${d.options.map(o => `<option value="${escape(o.value)}"${o.value === d.value ? ' selected' : ''}>${escape(o.name)}</option>`).join('')}</select>`;
    else if (d.kind === 'color') input = `<input type="color" name="value" aria-label="${title}" value="#${Number.isInteger(d.value) ? d.value.toString(16).padStart(6, '0') : '6c47ff'}"${disabled}>`;
    else input = `<div class="sh-number"><input class="field" type="number" name="value" min="${d.min}" max="${d.max}" step="${d.precision || 1}" value="${Number.isFinite(d.value) ? d.value : ''}" placeholder="${d.min}–${d.max}" aria-label="${title}" required${disabled}><span>${escape(d.unit)}</span></div>`;
    return `<form class="sh-control" data-sh-form="${index}"><label>${title}</label><div class="sh-input-row">${input}<button class="btn sm" type="submit"${disabled}>Задать</button></div></form>`;
  }
  function readings(device) {
    return (device.properties || []).map(p => {
      const s = p.state || {}, params = p.parameters || {}, instance = params.instance || s.instance;
      if (s.value == null) return '';
      const value = typeof s.value === 'boolean' ? s.value ? 'Да' : 'Нет' : typeof s.value === 'object' ? '—' : s.value;
      return `<div><dt>${escape(label(instance))}</dt><dd>${escape(value)} ${escape(units[params.unit] || '')}</dd></div>`;
    }).join('');
  }
  function render() {
    const query = node('sh-search').value.toLowerCase().trim(), house = node('sh-house').value, room = node('sh-room').value;
    const list = devices.filter(d => (!house || d.household === house) && (!room || d.room === room)
      && `${d.name} ${d.room} ${d.household}`.toLowerCase().includes(query));
    node('sh-devices').innerHTML = list.map(d => {
      const offline = d.state === 'offline', controls = descriptors(d), sensor = readings(d);
      const symbol = /light/.test(d.type) ? '☀' : /socket|switch/.test(d.type) ? '⏻' : /sensor/.test(d.type) ? '◉' : '⌂';
      return `<article class="card sh-device" data-sh-id="${escape(d.id)}" aria-busy="${busy.has(d.id)}"><header><span class="sh-device-icon" aria-hidden="true">${symbol}</span><div><h2>${escape(d.name)}</h2><small>${escape(d.household)} · ${escape(d.room)}</small></div><span class="badge${d.state === 'online' ? ' ok' : offline ? ' bad' : ''}">${offline ? 'Не в сети' : d.state === 'online' ? 'В сети' : 'Статус —'}</span></header>
        ${sensor ? `<dl class="sh-readings">${sensor}</dl>` : ''}
        <details class="sh-detail" data-loaded="${detailed.has(d.id) ? '1' : '0'}"${expanded.has(d.id) ? ' open' : ''}><summary>Управление и показания</summary><div class="sh-device-controls">${controls.map((c, i) => control(c, i, offline || busy.has(d.id))).join('') || `<p class="hint">${detailed.has(d.id) ? sensor ? 'Доступны только показания датчиков.' : 'Управляемые функции этого устройства пока не поддерживаются панелью.' : 'Откройте карточку, чтобы загрузить доступные функции.'}</p>`}</div></details>
        <p class="hint sh-device-status" role="status"></p></article>`;
    }).join('') || `<div class="card empty">${devices.length ? 'По этому запросу устройств нет.' : connected ? 'В этом аккаунте пока нет устройств. Добавьте их в приложении «Дом с Алисой» и нажмите «Обновить».' : 'После входа здесь появятся ваши устройства.'}</div>`;
    root.querySelectorAll('.sh-device').forEach(card => {
      const device = devices.find(d => d.id === card.dataset.shId), controls = descriptors(device), details = card.querySelector('details');
      details.addEventListener('toggle', () => {
        if (details.open) { expanded.add(device.id); if (details.dataset.loaded !== '1') detail(device.id); }
        else expanded.delete(device.id);
      });
      card.querySelectorAll('[data-sh-command]').forEach(b => b.onclick = () => {
        const d = controls[Number(b.dataset.shCommand)];
        execute(device.id, d, d.kind === 'relative' ? Number(b.dataset.relative) * d.step : b.dataset.value === 'true', d.kind === 'relative');
      });
      card.querySelectorAll('[data-sh-form]').forEach(form => form.onsubmit = e => {
        e.preventDefault(); if (!form.reportValidity()) return;
        const d = controls[Number(form.dataset.shForm)], raw = form.elements.value.value;
        if (raw === '') return;
        execute(device.id, d, d.kind === 'range' ? Number(raw) : d.kind === 'color' ? parseInt(raw.slice(1), 16) : raw);
      });
    });
  }
  function filterOptions() {
    for (const [id, key, title] of [['sh-house', 'household', 'Все дома'], ['sh-room', 'room', 'Все комнаты']]) {
      const select = node(id), current = select.value;
      const house = node('sh-house').value;
      const choices = key === 'room' && house ? devices.filter(d => d.household === house) : devices;
      select.innerHTML = `<option value="">${title}</option>` + [...new Set(choices.map(d => d[key]))].sort().map(v => `<option value="${escape(v)}">${escape(v)}</option>`).join('');
      if ([...select.options].some(o => o.value === current)) select.value = current;
    }
  }
  async function detail(id) {
    if (reading.has(id) || busy.has(id)) return;
    reading.add(id); const at = generation;
    try {
      const value = await api('/api/smart-home/device?id=' + encodeURIComponent(id));
      if (at !== generation || busy.has(id)) return;
      devices = devices.map(d => d.id === id ? value : d); detailed.add(id); render();
      const card = [...root.querySelectorAll('.sh-device')].find(c => c.dataset.shId === id);
      if (card && !descriptors(value).length && !readings(value)) card.querySelector('.sh-device-status').textContent = 'Устройство не передаёт поддерживаемые панелью функции.';
    } catch (e) { notice(e.message, true); }
    finally { reading.delete(id); }
  }
  async function execute(id, descriptor, value, relative = false) {
    if (busy.has(id)) return;
    busy.add(id); render();
    try {
      const result = await api('/api/smart-home/action', {id, type: descriptor.type, instance: descriptor.instance, value, relative});
      notice(result.message || 'Команда отправлена.');
      detailed.delete(id);
    } catch (e) { notice(e.message, true); }
    finally { busy.delete(id); render(); await detail(id); }
  }
  async function load(refresh = false) {
    if (loading || root.hidden || document.hidden || busy.size) return;
    loading = true; node('sh-refresh').disabled = true;
    try {
      const result = await api('/api/smart-home' + (refresh ? '?refresh=1' : ''));
      generation++; connected = result.connected; devices = result.devices || []; detailed.clear();
      node('sh-connect').hidden = connected;
      node('sh-filters').hidden = !connected;
      node('sh-token-remove').hidden = result.source !== 'official';
      notice(connected ? `${devices.length} устройств · управление через Яндекс` : result.message || 'Войдите в Яндекс.');
      filterOptions(); render();
    } catch (e) { notice(e.message, true); node('sh-connect').hidden = connected; }
    finally { loading = false; node('sh-refresh').disabled = false; }
  }
  function poll() {
    clearTimeout(timer);
    if (!root.hidden) {
      if (!document.activeElement?.closest('.sh-device')) load();
      timer = setTimeout(poll, 15000);
    }
  }
  node('sh-refresh').onclick = () => load(true);
  node('sh-search').oninput = render; node('sh-room').onchange = render;
  node('sh-house').onchange = () => { filterOptions(); render(); };
  node('sh-token-form').onsubmit = async e => {
    e.preventDefault(); const token = node('sh-token').value; node('sh-token').value = '';
    try { await api('/api/smart-home/auth', {token}); generation++; devices = []; detailed.clear(); render(); await load(true); }
    catch (error) { notice(error.message, true); }
  };
  node('sh-token-remove').onclick = async () => {
    try { await api('/api/smart-home/auth', {disconnect: true}); generation++; devices = []; await load(true); }
    catch (e) { notice(e.message, true); }
  };
  node('sh-login').onclick = async () => {
    clearTimeout(qrTimer); stopQr(); node('sh-login').disabled = true;
    node('sh-qr-status').textContent = 'Получаем код входа…';
    try {
      const result = await api('/api/smart-home/qr', {}), url = new URL(result.link);
      if (url.protocol !== 'https:' || !['passport.yandex.ru', 'passport.yandex.com'].includes(url.hostname)) throw new Error('Яндекс вернул неожиданную ссылку входа.');
      const code = qrcode(0, 'M'); code.addData(result.link); code.make();
      node('sh-qr').innerHTML = code.createSvgTag({cellSize: 4, margin: 16, scalable: true});
      const link = document.createElement('a'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Открыть вход на этом устройстве';
      node('sh-qr').append(link); node('sh-qr').hidden = false;
      node('sh-qr-status').textContent = 'Откройте сканер в приложении «Яндекс — с Алисой» или «Яндекс Старт» с нужным аккаунтом и подтвердите вход. Панель ждёт подтверждения.';
      async function check() {
        try {
          const state = await api('/api/smart-home/qr');
          if (state.state === 'done') { node('sh-qr').hidden = true; await load(true); return; }
          if (state.state === 'failed') { node('sh-qr').hidden = true; node('sh-qr-status').textContent = state.message; return; }
          if (state.state === 'expired' || state.state === 'none') { node('sh-qr-status').textContent = 'Код истёк. Нажмите «Войти по QR-коду» ещё раз.'; return; }
          qrTimer = setTimeout(check, 2000);
        } catch (e) {
          node('sh-qr-status').textContent = e.message + ' Повторяем проверку входа…';
          qrTimer = setTimeout(check, 4000);
        }
      }
      qrTimer = setTimeout(check, 2000);
    } catch (e) { node('sh-qr-status').textContent = e.message; }
    finally { node('sh-login').disabled = false; }
  };
  new MutationObserver(poll).observe(root, {attributes: true, attributeFilter: ['hidden']});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  poll();
})();
