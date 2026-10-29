const AS_STATE = { idle: ['Слушает', 'ok'], thinking: ['Думает…', ''], speaking: ['Отвечает', 'ok'] };
const AS_TTS_HINT = {
  alice: 'Ответ читает сама Алиса («Окей, повторяю: …»). Бесплатно и без ключа, но интонация ровная, без знаков препинания.',
  openai: 'Любой сервер с OpenAI-совместимым /audio/speech: OpenAI, Piper на компьютере через openedai-speech, голоса Edge через openai-edge-tts.',
};
let asTimer = null, asCfg = null;
function asPoll(on) {
  clearTimeout(asTimer);
  if (on) loadAssistant(true);
}
async function loadAssistant(fill) {
  clearTimeout(asTimer);
  let st;
  try { st = await api('/api/assistant'); } catch (e) { return e.offline || toast(e.message, true); }
  $('#as-experimental').hidden = !st.experimental;
  if (st.model_name) $('#as-exp-model').textContent = st.model_name.replace(/^Станция/, 'Станции');
  $('#as-missing').hidden = st.installed;
  $('#as-on').hidden = !st.installed;
  $('#as-update').hidden = !st.outdated;
  const s = st.state || {}, c = st.config || {};
  const [label, cls] = !st.installed ? ['Не установлен', ''] : !st.running ? ['Остановлен', 'bad']
    : !c.enabled ? ['Выключен', ''] : AS_STATE[s.state] || ['—', ''];
  $('#as-badge').textContent = label;
  $('#as-badge').className = 'badge' + (cls ? ' ' + cls : '');
  const err = s.config_error || s.error;
  $('#as-error').hidden = !err;
  $('#as-error').textContent = err ? 'Последняя ошибка: ' + err : '';
  $('#as-hist').innerHTML = (s.history || []).slice().reverse().map(h => `<div><b>${esc(h.question)}</b>`
    + (h.answer ? esc(h.answer) : `<span class="err">${esc(h.error || '')}</span>`)
    + `<small>${new Date(h.at * 1000).toLocaleTimeString()} · ${h.source === 'voice' ? 'голосом' : 'из панели'}`
    + `${h.ms ? ' · ' + (h.ms / 1000).toFixed(1) + ' с' : ''}</small></div>`).join('');
  const until = s.mode_until ? new Date(s.mode_until * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  $('#as-mode-state').textContent = s.mode ? 'включён' + (until ? ` · отключится в ${until}, если молчать` : '') : 'выключен';
  $('#as-mode-btn').textContent = s.mode ? 'Выключить' : 'Включить';
  $('#as-mode-btn').className = 'btn sm' + (s.mode ? '' : ' primary');
  $('#as-mode-btn').dataset.on = s.mode ? '1' : '';
  $('#as-mode-btn').disabled = !st.running || !c.enabled;
  if (s.mode && st.running && c.enabled) { $('#as-badge').textContent = 'Режим ассистента'; $('#as-badge').className = 'badge ok'; }
  if (fill && st.installed) asFill(c);
  if (!$('#tab-assistant').hidden) asTimer = setTimeout(loadAssistant, s.state && s.state !== 'idle' ? 1000 : 4000);
}
function asFill(c) {
  asCfg = c;
  const llm = c.llm || {}, tts = c.tts || {};
  $('#as-enabled').checked = !!c.enabled;
  $('#as-led').checked = c.led?.enabled ?? true;
  $('#as-led-color').value = c.led?.color || '#ff8a00';
  $('#as-triggers').value = (c.triggers || ['спроси gpt', 'спроси джипити', 'спроси чат gpt', 'спроси нейросеть']).join(', ');
  const mode = c.mode || {};
  $('#as-mode-enter').value = (mode.enter || ['переключись на gpt', 'переключись на ассистента', 'включи ассистента']).join(', ');
  $('#as-mode-exit').value = (mode.exit || ['отключись от ассистента', 'выключи ассистента', 'верни алису', 'вернись к алисе']).join(', ');
  $('#as-mode-timeout').value = mode.timeout_min || 10;
  $('#as-mode-follow').checked = mode.follow_up ?? true;
  $('#as-url').value = llm.base_url || '';
  $('#as-key').value = '';
  $('#as-model').value = llm.model || '';
  $('#as-prompt').value = llm.system_prompt || 'Ты эрудированный собеседник и отвечаешь на любые вопросы. Отвечай по-русски и коротко: '
    + 'одно-три предложения разговорной речью. Ответ прочитают вслух, поэтому без списков, разметки, эмодзи, ссылок и формул.';
  $('#as-tokens').value = llm.max_tokens || 500;
  $('#as-history').value = llm.history ?? 6;
  $('#as-proxy').value = llm.proxy || '';
  if (llm.base_url && (c.keys || {})[asKeyId(llm.base_url)]) asModels(true);
  $('#as-tts').value = tts.provider || 'alice';
  $('#as-tts-url').value = tts.base_url || '';
  $('#as-tts-key').value = '';
  $('#as-tts-model').value = tts.model || 'tts-1';
  $('#as-tts-voice').value = tts.voice || '';
  $('#as-tts-speed').value = tts.speed || 1;
  asTtsView();
  asKeysView();
}
// Ключи хранятся по адресу API — как keyID в device/assistant
const asKeyId = url => String(url || '').trim().replace(/\/+$/, '').toLowerCase();
function asProvider(url) {
  const opt = [...$('#as-preset').options].find(o => o.value && asKeyId(o.value) === url);
  return opt ? opt.textContent : url.replace(/^https?:\/\//, '').split('/')[0];
}
function asKeysView() {
  const keys = asCfg?.keys || {};
  [['#as-url', '#as-key'], ['#as-tts-url', '#as-tts-key']].forEach(([u, k]) => {
    const has = keys[asKeyId($(u).value)];
    $(k).placeholder = has ? 'сохранён ' + has : 'не задан';
  });
  $('#as-keys').innerHTML = Object.entries(keys).map(([url, m]) =>
    `<span title="${esc(url)}">${esc(asProvider(url))} ${esc(m)}<button type="button" data-url="${esc(url)}" aria-label="Удалить ключ ${esc(asProvider(url))}">×</button></span>`).join('');
}
['#as-url', '#as-tts-url'].forEach(id => $(id).addEventListener('input', asKeysView));
$('#as-keys').onclick = e => {
  const url = e.target.closest('button')?.dataset.url;
  if (!url || !confirm(`Удалить с колонки ключ для ${asProvider(url)}?`)) return;
  act(null, async () => asFill((await api('/api/assistant', { delete_key: url })).config), 'Ключ удалён');
};
// Панель открыта по HTTP с другого устройства: ключ уйдёт по сети открытым текстом. Не запрещаем — в домашней сети
// это обычный случай, — но предупреждаем и даём ссылку на HTTPS, если он включён.
function asHttpWarning() {
  const plain = location.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
  const port = accessInfo?.ports?.https;
  const url = port && Object.assign(new URL(location.href), { protocol: 'https:', port: String(port) }).href;
  $$('.as-http').forEach(el => {
    if (el.matches('[data-tts]') && $('#as-tts').value !== 'openai') { el.hidden = true; return; }
    el.hidden = !plain;
    el.innerHTML = 'Панель открыта по HTTP: ключ уйдёт до компьютера с панелью открытым текстом. В своей домашней сети риск '
      + 'невелик, в чужой или общей — заметнее. ' + (url ? `Безопаснее <a href="${esc(url)}">открыть панель по HTTPS</a>.`
      : 'Безопаснее включить HTTPS («Настройки» → «Доступ»).');
  });
}
function asTtsView() {
  const v = $('#as-tts').value;
  $$('[data-tts]').forEach(el => el.hidden = v !== 'openai');
  $('#as-tts-hint').textContent = AS_TTS_HINT[v];
  asHttpWarning();
  // что при этом всё равно уходит Яндексу — зависит от способа озвучки
  $('#as-privacy').innerHTML = '<b>Обратите внимание!</b> ' + esc('Яндекс по-прежнему получает голосовые вопросы: «Алиса, спроси GPT, …» распознаёт её облако, '
    + 'звук и текст фразы уходят на серверы Яндекса, как обычный запрос к Алисе. '
    + (v === 'alice' ? 'Ответ модели тоже уходит Яндексу: Алиса озвучивает его запросом «Повтори за мной».'
      : 'Ответ модели Яндекс не получает — его озвучивает выбранный синтезатор.')
    + ' Вопросы из «Проверки» Яндекс не видит.' + (v === 'alice' ? ' Только ответ на них.' : ''));
}
$('#as-tts').onchange = asTtsView;
const asLed = () => act(null, async () => asFill((await api('/api/assistant', { led: { enabled: $('#as-led').checked, color: $('#as-led-color').value } })).config));
$('#as-led').onchange = asLed;
$('#as-led-color').onchange = asLed;
// Модели спрашивает сама колонка (её сеть и прокси); ключ — из поля или уже сохранённый
let asModelsReq = 0;
async function asModels(quiet) {
  const url = $('#as-url').value.trim();
  if (!url) return quiet || toast('Сначала укажите адрес API', true);
  const req = ++asModelsReq, hint = $('#as-models-hint');
  hint.textContent = 'Запрашиваю список моделей…';
  try {
    const r = await api('/api/assistant/models', { base_url: url, api_key: $('#as-key').value, proxy: $('#as-proxy').value.trim() });
    if (req !== asModelsReq) return;
    $('#as-models').innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join('');
    hint.textContent = r.models.length ? `Доступно моделей: ${r.models.length}. Начните вводить название или нажмите на поле.` : 'API не вернул ни одной модели.';
  } catch (e) {
    if (req === asModelsReq) hint.textContent = 'Список не получен: ' + e.message;
  }
}
$('#as-models-btn').onclick = () => asModels();
['#as-url', '#as-key', '#as-proxy'].forEach(id => $(id).addEventListener('change', () => asModels(true)));
$('#as-update').onclick = e => confirm('Обновить программу помощника в /data с резервной копией?') && act(e.currentTarget, async () => { await api('/api/assistant/install', {}); await loadAssistant(); }, 'Программа на колонке обновлена');
$('#as-preset').onchange = e => { if (e.target.value) { $('#as-url').value = e.target.value; asKeysView(); asModels(true); } e.target.value = ''; };
$('#as-install').onclick = e => confirm('Установить выключенный модуль помощника в /data колонки? Настройка модели и включение выполняются отдельно.') && act(e.currentTarget, async () => { await api('/api/assistant/install', {}); await loadAssistant(true); },
  'Помощник установлен. Укажите модель и включите его');
$('#as-mode-btn').onclick = e => {
  const on = !e.currentTarget.dataset.on;
  act(e.currentTarget, async () => { await api('/api/assistant/mode', { on }); setTimeout(loadAssistant, 400); },
    on ? 'Режим ассистента включён — колонка скажет об этом' : 'Режим ассистента выключен');
};
$('#as-mode-follow').onchange = e => act(null, async () => asFill((await api('/api/assistant', { mode: { follow_up: e.target.checked } })).config));
$('#as-restart').onclick = e => act(e.currentTarget, async () => { await api('/api/assistant/start', {}); await loadAssistant(); }, 'Перезапущен');
$('#as-enabled').onchange = e => act(null, async () => { await api('/api/assistant', { enabled: e.target.checked }); await loadAssistant(); },
  e.target.checked ? 'Помощник отвечает на фразу' : 'Помощник выключен');
$('#as-save').onclick = e => act(e.currentTarget, async () => {
  const num = (id, d) => Number($(id).value) || d;
  const st = await api('/api/assistant', {
    triggers: $('#as-triggers').value.split(',').map(s => s.trim()).filter(Boolean),
    mode: { enter: $('#as-mode-enter').value.split(',').map(s => s.trim()).filter(Boolean),
      exit: $('#as-mode-exit').value.split(',').map(s => s.trim()).filter(Boolean),
      timeout_min: Math.max(1, Math.min(240, Number($('#as-mode-timeout').value) || 10)), follow_up: $('#as-mode-follow').checked },
    llm: { base_url: $('#as-url').value.trim(), api_key: $('#as-key').value, model: $('#as-model').value.trim(),
      system_prompt: $('#as-prompt').value.trim(), max_tokens: num('#as-tokens', 500), history: Math.max(0, Number($('#as-history').value) || 0),
      proxy: $('#as-proxy').value.trim() },
    tts: { provider: $('#as-tts').value, base_url: $('#as-tts-url').value.trim(), api_key: $('#as-tts-key').value,
      model: $('#as-tts-model').value.trim(), voice: $('#as-tts-voice').value.trim(), speed: num('#as-tts-speed', 1) },
  });
  asFill(st.config);
}, 'Настройки сохранены');
$('#as-ask').onclick = e => {
  const text = $('#as-question').value.trim();
  if (!text) return;
  act(e.currentTarget, async () => { await api('/api/assistant/ask', { text }); setTimeout(loadAssistant, 500); }, 'Вопрос отправлен — ответ прозвучит на колонке');
};
$('#as-stop').onclick = e => act(e.currentTarget, async () => { await api('/api/assistant/stop', {}); await loadAssistant(); });
$('#as-remove').onclick = e => confirm('Удалить помощника с колонки вместе с настройками и ключами?') &&
  act(e.currentTarget, async () => { await api('/api/assistant/remove', {}); await loadAssistant(true); }, 'Помощник удалён');

