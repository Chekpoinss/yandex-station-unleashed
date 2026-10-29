const RD_ALICE = ['Европа Плюс', 'Русское радио', 'Авторадио', 'Радио Рекорд', 'Наше радио', 'Ретро FM', 'DFM', 'Love Radio',
  'Хит FM', 'Радио Energy', 'Маяк', 'Дорожное радио', 'Радио Шансон', 'Relax FM', 'Монте-Карло', 'Business FM', 'Детское радио'];
let rd = { st: null, timer: null, checked: {} };
// Пометка у станции — только если с потоком беда; нормальные станции без лишних надписей.
function rdCheckText(c) {
  if (!c || c.state === 'ok') return '';
  const where = c.host ? ` Сервер ${c.host}${c.ip ? ' (' + c.ip + ')' : ''}.` : '';
  if (c.state === 'blocked') return `Поток ${c.detail} — похоже на блокировку провайдера.${where} Добавьте его в обход на роутере или укажите прокси ниже.`;
  if (c.state === 'slow') return `Поток идёт медленнее, чем играет (${c.detail.split(' — ')[0]}) — будет прерываться.`;
  return `Станция не работает: ${c.detail}.`;
}
const rdCheckNote = c => { const s = rdCheckText(c); return s ? `<span class="rd-warn" title="${esc(s)}" aria-label="${esc(s)}">⚠</span>` : ''; };
// Проверить станцию (с компьютера с панелью) и сказать, если с ней беда
async function rdCheck(url, quiet) {
  if (rd.checked[url]) return;
  rd.checked[url] = true;
  try {
    const c = await api('/api/radio/check', { url });
    const s = rdCheckText(c);
    if (s && !quiet) toast(s, true);
    await loadRadio();
  } catch {}
}
function rdPoll(on) {
  clearTimeout(rd.timer);
  if (on) loadRadio();
}
async function loadRadio() {
  clearTimeout(rd.timer);
  let st;
  try { st = await api('/api/radio'); } catch (e) { return e.offline || toast(e.message, true); }
  rd.st = st;
  // промах голосом: предлагаем привязать услышанное название к станции
  const miss = st.miss?.phrase && st.stations.length ? st.miss : null;
  $('#rd-miss').hidden = !miss;
  if (miss && rd.missShown !== miss.at) {
    rd.missShown = miss.at;
    $('#rd-miss-phrase').textContent = miss.phrase;
    $('#rd-miss-station').innerHTML = st.stations.map((s, i) => `<option value="${i}">${esc(s.name)}</option>`).join('');
  }
  if (JSON.stringify(st.alice) !== rd.alice) { rd.alice = JSON.stringify(st.alice); rdAliceView(st.alice || []); }
  $('#rd-missing').hidden = st.installed;
  $('#rd-unsupported').hidden = st.supported !== false;
  $('#rd-install').hidden = st.supported === false;
  $('#rd-own').hidden = !st.installed;
  $('#rd-own-off').hidden = !st.own_disabled;
  const now = st.now || {};
  $('#rd-now').hidden = !(now.playing || now.paused || now.retrying || now.error && now.name);
  // подробности проверки — под проигрывателем, где для них есть место
  const note = rdCheckText(st.checks?.[now.url]);
  $('#rd-now-note').hidden = !note || !now.url;
  $('#rd-now-note').textContent = note;
  $('#rd-now-name').textContent = now.name || '—';
  // «souphttpsrc0: Bad Request» → понятная причина: поток отклонил сервер станции, а не колонка
  const err = now.error && (/^souphttpsrc\d*:\s*(.*)$/.exec(now.error)?.[1] || now.error);
  const why = /missing a plug-in/i.test(now.error || '')
    ? 'Формат этой станции колонка не умеет (обычно это OGG Vorbis). Найдите у неё поток MP3 или AAC.'
    : /souphttpsrc/.test(now.error || '') ? `Станция не отдаёт поток (сервер ответил: ${err}). Попробуйте другой адрес потока.` : 'Ошибка: ' + err;
  $('#rd-now-title').textContent = err && !now.retrying ? why
    : now.retrying ? 'поток прервался — переподключаюсь…' : now.paused ? 'пауза, пока вы говорите с Алисой' : now.title || 'играет';
  $('#rd-count').textContent = st.stations.length ? `${st.stations.length}` : '';
  $('#rd-list').innerHTML = st.stations.length ? st.stations.map((s, i) => `<div class="rd-row${now.url === s.url && now.playing ? ' on' : ''}">
      <button class="btn sm icon" data-rd-play="${i}" aria-label="Слушать ${esc(s.name)}"><svg width="14" height="14" aria-hidden="true"><use href="#i-play"/></svg></button>
      <div class="rd-name"><b>${esc(s.name)}</b><small>${s.aliases?.length ? 'голосом: ' + s.aliases.map(esc).join(', ') + ' · ' : ''}${esc(s.url)}</small></div>${rdCheckNote(st.checks?.[s.url])}
      <button class="btn sm icon" data-rd-voice="${i}" aria-label="Как называть голосом ${esc(s.name)}" title="Как называть голосом">🗣</button>
      <button class="btn sm icon" data-rd-ren="${i}" aria-label="Переименовать ${esc(s.name)}" title="Переименовать">✎</button>
      <button class="btn sm icon" data-rd-del="${i}" aria-label="Удалить ${esc(s.name)}">×</button></div>`).join('')
    : '<div class="empty">Добавьте станцию ниже или найдите в каталоге.</div>';
  $('#rd-voice').checked = st.voice;
  if (document.activeElement !== $('#rd-proxy-form').elements.proxy) $('#rd-proxy-form').elements.proxy.value = st.proxy || '';
  // станция играет пару секунд и молчит — проверяем, не блокировка ли
  if (now.url && /почти не отдаёт|souphttpsrc/.test(now.error || '')) rdCheck(now.url, true);
  $('#rd-voice-hint').textContent = `— «Алиса, ${st.play[0]} <название>», «Алиса, ${st.stop[0]}»`;
  if (!$('#tab-radio').hidden) rd.timer = setTimeout(loadRadio, now.playing || now.paused || now.retrying ? 4000 : 10000);
}
// Логотип станции — из каталога radio-browser.info (панель скачивает и хранит его сама); не нашёлся — первые буквы.
const rdKey = s => String(s || '').toLowerCase().replace(/ё/g, 'е').replace(/^радио\s+/, '').replace(/[^a-zа-я0-9]+/g, '');
function rdAliceView(learned = []) {
  const names = [...RD_ALICE, ...learned.map(s => s.title).filter(t => !RD_ALICE.some(n => rdKey(n) === rdKey(t)))];
  $('#rd-alice').innerHTML = names.map(n => {
    const initials = n.replace(/^радио\s+/i, '').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
    const logo = PanelContext.url('/api/radio/logo?name=' + encodeURIComponent(n));
    return `<button class="btn sm" data-alice="${esc(n)}"><span class="rd-logo" aria-hidden="true">${esc(initials)}</span>`
      + `<img src="${esc(logo)}" alt="" hidden>${esc(n)}</button>`;
  }).join('');
  // буквы видны, пока логотип грузится; загрузился — показываем его
  $$('#rd-alice img').forEach(img => img.addEventListener('load', () => { img.hidden = false; img.previousElementSibling.remove(); }, { once: true }));
}
rdAliceView();
const rdAlice = (btn, name) => act(btn, async () => { await api('/api/radio/alice', { name }); setTimeout(loadRadio, 800); }, `Алиса включает «${name}»`);
$('#rd-alice').onclick = e => { const b = e.target.closest('[data-alice]'); if (b) rdAlice(b, b.dataset.alice); };
$('#rd-alice-form').onsubmit = e => { e.preventDefault(); const n = e.target.elements.name.value.trim(); if (n) rdAlice(e.submitter, n); };
const rdPlay = (btn, s) => act(btn, async () => { await api('/api/radio/play', s); setTimeout(loadRadio, 1500); }, `Включаю «${s.name}»`);
const rdSave = (btn, stations, msg) => act(btn, async () => { rd.st = await api('/api/radio', { stations }); await loadRadio(); }, msg);
$('#rd-list').onclick = e => {
  const play = e.target.closest('[data-rd-play]'), del = e.target.closest('[data-rd-del]'), ren = e.target.closest('[data-rd-ren]');
  if (play) rdPlay(play, rd.st.stations[+play.dataset.rdPlay]);
  const voice = e.target.closest('[data-rd-voice]');
  if (voice) {
    const i = +voice.dataset.rdVoice, s = rd.st.stations[i];
    const v = prompt(`Как вы называете «${s.name}» голосом? Несколько вариантов — через запятую:`, (s.aliases || []).join(', '));
    if (v !== null) rdSave(voice, rd.st.stations.map((x, k) => k === i ? { ...x, aliases: v.split(',').map(a => a.trim()).filter(Boolean) } : x), 'Сохранено');
  }
  if (ren) {
    const i = +ren.dataset.rdRen, name = prompt('Название — как вы будете называть станцию голосом:', rd.st.stations[i].name)?.trim();
    if (name) rdSave(ren, rd.st.stations.map((s, k) => k === i ? { ...s, name } : s), 'Название изменено');
  }
  if (del) {
    const s = rd.st.stations[+del.dataset.rdDel];
    if (confirm(`Удалить станцию «${s.name}»?`)) rdSave(del, rd.st.stations.filter((_, i) => i !== +del.dataset.rdDel), 'Станция удалена');
  }
};
$('#rd-add').onsubmit = e => {
  e.preventDefault();
  const f = e.target.elements, s = { name: f.name.value.trim(), url: f.url.value.trim() };
  rdSave(e.submitter, [...(rd.st?.stations || []), s], 'Станция добавлена').then(() => { f.name.value = ''; f.url.value = ''; rdCheck(s.url); });
};
$('#rd-miss-bind').onclick = e => {
  const i = +$('#rd-miss-station').value, phrase = $('#rd-miss-phrase').textContent;
  act(e.currentTarget, async () => {
    await api('/api/radio', { stations: rd.st.stations.map((s, k) => k === i ? { ...s, aliases: [...new Set([...(s.aliases || []), phrase])] } : s) });
    await api('/api/radio/miss-seen', {});
    await loadRadio();
  }, `«${phrase}» теперь включает «${rd.st.stations[i].name}»`);
};
$('#rd-miss-skip').onclick = e => act(e.currentTarget, async () => { await api('/api/radio/miss-seen', {}); await loadRadio(); });
$('#rd-proxy-form').onsubmit = e => {
  e.preventDefault();
  act(e.submitter, async () => { await api('/api/radio', { proxy: e.target.elements.proxy.value.trim() }); rd.checked = {}; await loadRadio(); },
    e.target.elements.proxy.value.trim() ? 'Прокси сохранён — станции пойдут через него' : 'Прокси выключен');
};
$('#rd-stop').onclick = e => act(e.currentTarget, async () => { await api('/api/radio/stop', {}); setTimeout(loadRadio, 500); });
$('#rd-voice').onchange = e => act(null, async () => { await api('/api/radio', { voice: e.target.checked }); await loadRadio(); });
$('#rd-install').onclick = e => act(e.currentTarget, async () => { await api('/api/assistant/install', {}); await loadRadio(); }, 'Программа установлена');
let rdFound = [];
$('#rd-search').onsubmit = e => {
  e.preventDefault();
  const f = e.target.elements;
  act(e.submitter, async () => {
    const r = await api(`/api/radio/search?q=${encodeURIComponent(f.q.value.trim())}&country=${f.country.value}`);
    rdFound = r.stations;
    $('#rd-results').innerHTML = rdFound.length ? rdFound.map((s, i) => `<div class="rd-row">
        <button class="btn sm icon" data-rd-try="${i}" aria-label="Слушать ${esc(s.name)}"><svg width="14" height="14" aria-hidden="true"><use href="#i-play"/></svg></button>
        <div class="rd-name"><b>${esc(s.name)}</b><small>${[s.codec, s.bitrate ? s.bitrate + ' кбит/с' : '', s.country, s.tags].filter(Boolean).map(esc).join(' · ')}</small></div>
        <button class="btn sm" data-rd-add="${i}">Добавить</button></div>`).join('') : '<div class="empty">Ничего не нашлось.</div>';
  });
};
$('#rd-results').onclick = e => {
  const t = e.target.closest('[data-rd-try]'), a = e.target.closest('[data-rd-add]');
  if (t) { if (!rd.st?.installed) return toast('Сначала установите программу на колонку', true); rdPlay(t, rdFound[+t.dataset.rdTry]); }
  if (a) {
    if (!rd.st?.installed) return toast('Сначала установите программу на колонку', true);
    const s = rdFound[+a.dataset.rdAdd];
    if (rd.st.stations.some(x => x.url === s.url)) return toast('Эта станция уже в списке');
    rdSave(a, [...rd.st.stations, { name: s.name, url: s.url }], `«${s.name}» добавлена. Для голоса задайте название, как вы его произносите (✎)`)
      .then(() => rdCheck(s.url));
  }
};

