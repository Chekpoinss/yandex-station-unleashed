const SC_NODES = {
  music: {name:'Наш плеер',icon:'i-music',group:'Звук и свет',def:()=>({action:'resume',track:''})},
  query: { name: 'Запрос Алисе', icon: 'i-mic', group: 'Алиса', def: () => ({ text: '' }) },
  say: { name: 'Алиса скажет', icon: 'i-mic', group: 'Алиса', def: () => ({ text: '' }) },
  ask: { name: 'Спросить ассистента', icon: 'i-mic', group: 'Алиса', def: () => ({ text: '' }), assistant: true },
  assistant: { name: 'Режим ассистента', icon: 'i-mic', group: 'Алиса', def: () => ({ mode: true }), assistant: true },
  vol: { name: 'Громкость', icon: 'i-vol', group: 'Звук и свет', def: () => ({ level: 3, ramp: 0 }) },
  sound: { name: 'Звук', icon: 'i-music', group: 'Звук и свет', def: () => ({ sound: 'sys:common/timer.wav' }) },
  ring: { name: 'Кольцо', icon: 'i-palette', group: 'Звук и свет', def: () => ({ color: ringHex || '#6c47ff', secs: 10 }) },
  anim: { name: 'Анимация', icon: 'i-palette', group: 'Звук и свет', def: () => ({ anim: 'ring:' + ((patterns.ring || []).find(n => n.startsWith('alice_thinking')) || (patterns.ring || [])[0] || ''), secs: 5 }) },
  stop: { name: 'Стоп', icon: 'i-stop', group: 'Звук и свет', def: () => ({}) },
  wait: { name: 'Пауза', icon: 'i-alarm', group: 'Время', def: () => ({ secs: 60 }) },
  wait_until: { name: 'Ждать до', icon: 'i-alarm', group: 'Время', def: () => ({ time: '07:00' }) },
  wait_quiet: { name: 'Ждать тишины', icon: 'i-vol', group: 'Время', def: () => ({ secs: 600 }) },
  if: { name: 'Условие', icon: 'i-shuffle', group: 'Логика', def: () => ({ check: 'time', arg: '22:00-07:00' }), outs: ['да', 'нет'] },
  repeat: { name: 'Повторить', icon: 'i-repeat', group: 'Логика', def: () => ({ count: 3 }), outs: ['ещё', 'готово'] },
  scene: { name: 'Другой сценарий', icon: 'i-flow', group: 'Логика', def: () => ({ target: '' }) },
  note: { name: 'Заметка', icon: 'i-edit', group: 'Логика', def: () => ({ text: '' }), noPorts: true },
};
const SC_CHECKS = {
  time: { name: 'время между', ph: '22:00-07:00' }, days: { name: 'день недели', ph: '67' },
  playing: { name: 'что-то играет' }, online: { name: 'есть интернет' }, vol_above: { name: 'громкость выше', ph: '5' },
  chance: { name: 'случайно, % «да»', ph: '50' }, assistant_mode: { name: 'включён режим ассистента' },
  phase: {name:'состояние общего плеера',ph:'playing / paused / stopped / loading'},
};
const SC_RAMPS = [[0, 'сразу']];
let scHasVoice = false;
let scData = null, scEdit = null, scTimer, scSel = null, scHasAssistant = false;
const durText = s => s >= 3600 ? `${+(s / 3600).toFixed(1)} ч` : s >= 60 ? `${+(s / 60).toFixed(1)} мин` : `${s} с`;
const sndTitle = v => v.startsWith('u_') ? (alUploaded.find(u => u.sound === v)?.sound_name || 'свой звук') : v.replace(/^sys:/, '').split('/').pop().replace(/\.[^.]+$/, '');
function nodeText(n) {
  switch (n.kind) {
    case 'music': return 'общий плеер: ' + n.action;
    case 'query': return `«${n.text}»`;
    case 'say': return `скажет «${n.text}»`;
    case 'ask': return `ассистенту «${n.text}»`;
    case 'assistant': return n.mode ? 'режим ассистента: вкл' : 'режим ассистента: выкл';
    case 'vol': return `громкость ${n.level * 10}%` + (n.ramp ? ` ${SC_RAMPS.find(r => r[0] === n.ramp)?.[1] || durText(n.ramp)}` : '');
    case 'sound': return `звук «${sndTitle(n.sound)}»`;
    case 'ring': return `кольцо ${durText(n.secs)}`;
    case 'wait': return `пауза ${durText(n.secs)}`;
    case 'wait_until': return `ждать до ${n.time}`;
    case 'wait_quiet': return 'ждать тишины';
    case 'if': return `если ${SC_CHECKS[n.check]?.name || ''}${n.arg && SC_CHECKS[n.check]?.ph ? ' ' + n.arg : ''}`;
    case 'repeat': return `повторить ${n.count} раз`;
    case 'scene': return `сценарий «${scData?.scenes.find(s => s.id === n.target)?.name || '?'}»`;
    case 'anim': return `анимация «${animName(n.anim)}»`;
    case 'note': return 'заметка';
    default: return 'стоп';
  }
}
const scNode = (s, id) => s.nodes.find(n => n.id === id);
const animName = v => animTitle(String(v || '').replace(/^\w+:/, ''));
// цепочка от «Запуска» по next; linear — сценарий без ветвлений, все ноды (кроме заметок) в ней
function scChain(s) {
  const out = [], seen = new Set();
  for (let n = scNode(s, s.start); n && !seen.has(n.id); n = scNode(s, n.next)) { seen.add(n.id); out.push(n); }
  const linear = out.every(n => !SC_NODES[n.kind].outs) && s.nodes.every(n => n.kind === 'note' || seen.has(n.id))
    && !(out.length && scNode(s, out[out.length - 1].next)) && !s.nodes.some(n => n.kind === 'note');
  return { chain: out, linear };
}
async function loadScenes() {
  clearTimeout(scTimer);
  try {
    [scData, scHasAssistant] = await Promise.all([api('/api/scenes'), api('/api/assistant').then(a => !!a.installed, () => false)]);
    if (!patterns.ring) patterns = await api('/api/patterns').catch(() => ({}));   // для ноды «Анимация»
  } catch (e) { if (!e.offline) $('#sc-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  renderScenes();
  // пока что-то идёт — обновляем, чтобы кнопка «Остановить» и журнал не отставали
  if (!$('#tab-scenes').hidden) scTimer = setTimeout(loadScenes, scData.running.length ? 2000 : 15000);
}
function renderScenes() {
  const d = scData; if (!d) return;
  if (d.storage_error) {
    $('#sc-list').innerHTML = `<div class="empty">${esc(d.migration_warning || 'Файл сценариев повреждён. Запись отключена, исходник сохранён.')}</div>`;
    return;
  }
  scHasAssistant = !!d.capabilities?.assistant; scHasVoice = !!d.capabilities?.voice;
  const on = d.scenes.filter(s => s.on).length;
  $('#sc-badge').title='Общий плеер требует работающего ПК; автономные сценарии разворачиваются отдельно';
  $('#sc-badge').textContent = !d.scenes.length ? 'нет' : on && !d.daemon ? 'не запущены на колонке — сохраните ещё раз' : `включено ${on} из ${d.scenes.length}`;
  $('#sc-badge').className = 'badge' + (on && d.daemon ? ' ok' : on && !d.daemon ? ' bad' : '');
  $('#sc-list').innerHTML = d.scenes.length ? d.scenes.map(s => {
    const run = d.running.includes(s.id), { chain, linear } = scChain(s);
    return `<div class="al-row${s.on ? '' : ' off'}" data-id="${esc(s.id)}">
      <div class="al-time">${({ boot: icon('i-power', 30), voice: icon('i-mic', 30), manual: icon('i-play', 30) })[s.trigger] || esc(s.time)}</div>
      <div class="al-info"><b>${esc(s.name)}</b>
        <div class="meta">${s.trigger === 'boot' ? 'после включения колонки' : s.trigger === 'manual' ? 'только вручную — кнопкой или из другого сценария' : s.trigger === 'voice' ? esc(`по команде ассистенту: «${s.phrases.join('», «')}»`) : esc(daysText(s.days))}${s.execution==='device'?' · автономно':' · на ПК'}${d.native_pending&&s.execution==='device'?' · изменения не развёрнуты':''}${run ? ' · <span style="color:var(--ok-ink);font-weight:700">выполняется</span>' : ''}</div>
        <div class="sc-flow">${chain.slice(0, 6).map(n => `<span>${esc(nodeText(n))}</span>`).join('')}${chain.length > 6 || !linear ? `<span>${linear ? '…' : 'есть ветвления'}</span>` : ''}</div></div>
      <div class="actions">
        <input type="checkbox" data-a="toggle" ${s.on ? 'checked' : ''} aria-label="Включён" title="Включён">
        ${run ? '<button class="btn sm danger-soft" data-a="stop">Остановить</button>' : '<button class="btn sm" data-a="run" title="Выполнить прямо сейчас">Запустить</button>'}
        <button class="btn sm" data-a="edit">Изменить</button>
      </div></div>`;
  }).join('') : '<div class="empty">Сценариев пока нет. Например: «Ночь» — в 23:00 громкость 20% и «Алиса, включи режим не беспокоить».</div>';
  $('#sc-log').textContent = d.log.length ? d.log.slice().reverse().join('\n') : 'Пока пусто';
}
async function scSave(list) {
  const device=v=>JSON.stringify((v||[]).filter(s=>s.execution==='device'));
  const deploy=device(list)!==device(scData.scenes);
  if (deploy&&!confirm('Сохранить автономные сценарии в /data колонки и обновить alarmd с резервной копией? Расписание будильников и системный init сохраняются. Включённые сценарии смогут запускаться по расписанию.')) throw Error('Изменения отменены');
  let r=await api('/api/scenes',{scenes:list,revision:scData.revision});
  if(deploy){await api('/api/scenes/deploy',{});r=await api('/api/scenes');}
  scData=r;renderScenes();return r;
}
$('#sc-list').onclick = e => {
  const b = e.target.closest('[data-a]'); if (!b) return;
  const id = b.closest('.al-row').dataset.id, s = scData.scenes.find(x => x.id === id);
  if (b.dataset.a === 'edit') return openScene(s);
  if (b.dataset.a === 'run') return act(b, async () => { await api('/api/scenes/run', { id }); setTimeout(loadScenes, 1500); }, `Сценарий «${s.name}» запущен`);
  if (b.dataset.a === 'stop') return act(b, async () => { await api('/api/scenes/stop', { id }); await loadScenes(); }, 'Сценарий остановлен');
};
$('#sc-list').onchange = e => {
  if (e.target.dataset.a !== 'toggle') return;
  const id = e.target.closest('.al-row').dataset.id;
  act(e.target, () => scSave(scData.scenes.map(s => s.id === id ? { ...s, on: e.target.checked } : s)));
};

// ---- редактор: общие части ----
$('#sc-days').innerHTML = DAY_NAMES.map((n, i) => `<button type="button" class="chip" data-d="${i + 1}" aria-pressed="false">${n}</button>`).join('');
$('#sc-days').onclick = e => { const c = e.target.closest('.chip'); if (c) { c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') !== 'true'); scFieldStart(); } };
$('#sc-presets').onclick = e => { const c = e.target.closest('.chip'); if (c) { $$('#sc-days .chip').forEach(x => x.setAttribute('aria-pressed', c.dataset.d.includes(x.dataset.d))); scFieldStart(); } };
const scDays = () => $$('#sc-days .chip').filter(c => c.getAttribute('aria-pressed') === 'true').map(c => c.dataset.d).join('');
function scTriggerUI() {
  const v = $('#sc-trigger').value;
  $$('[data-sc-time]').forEach(x => x.hidden = v === 'boot' || v === 'manual');
  $('#sc-time').closest('label').hidden = v !== 'time';
  $$('[data-sc-voice]').forEach(x => x.hidden = v !== 'voice');
  $('#sc-boot-hint').hidden = v !== 'boot' && v !== 'manual';
  $('#sc-boot-hint').textContent = v === 'manual'
    ? 'Сам не запускается: только кнопкой «Запустить» здесь или нодой «Другой сценарий» из другого сценария.'
    : 'Запустится примерно через минуту после того, как Алиса загрузится, — при каждом включении колонки.';
  $('#sc-days-cap').textContent = v === 'voice' ? 'В какие дни (ни одного — в любой)' : 'Повторять';
  $('#sc-once').hidden = v === 'voice';
  $('#sc-voice-hint').textContent = scHasVoice ? 'Скажите Алисе точную фразу. Её обрабатывает включённый голосовой мост панели на ПК; текст не журналируется.' : 'Включите голосовое управление общим плеером в его настройках. GPT для этих фраз не требуется.';
  scFieldStart();
}
const scPhrases = () => $('#sc-phrases').value.split(',').map(x => x.trim()).filter(Boolean);
$('#sc-trigger').onchange = scTriggerUI;
$('#sc-time').onchange = () => scFieldStart();
$('#sc-phrases').oninput = () => scFieldStart();
const scTrigText = () => ({ boot: 'после включения колонки', manual: 'вручную', voice: `по команде «${scPhrases()[0] || '…'}»` })[$('#sc-trigger').value]
  || `${$('#sc-time').value || '—'} · ${daysText(scDays())}`;
function soundOptions() {
  const groups = {};
  (sounds || []).forEach(n => { const [g, ...r] = n.split('/'); (groups[r.length ? g : ''] ||= []).push(n); });
  return (alUploaded.length ? `<optgroup label="Свои">${alUploaded.map(u => `<option value="${esc(u.sound)}">${esc(u.sound_name)}</option>`).join('')}</optgroup>` : '')
    + Object.entries(groups).map(([g, l]) => `<optgroup label="${esc(g || 'Система')}">${l.map(n => `<option value="sys:${esc(n)}">${esc(n.split('/').pop().replace(/\.[^.]+$/, ''))}</option>`).join('')}</optgroup>`).join('');
}
function nodeFields(n) {
  switch (n.kind) {
    case 'music': return `<select class="field" data-f="action" aria-label="Действие плеера">${[['play','Трек'],['resume','Продолжить'],['pause','Пауза'],['stop','Стоп'],['next','Следующий'],['previous','Предыдущий'],['options','Повтор и перемешивание']].map(([v,t])=>`<option value="${v}" ${n.action===v?'selected':''}>${t}</option>`).join('')}</select>`
      + (n.action==='options' ? `<select class="field" data-f="repeat_mode" aria-label="Повтор">${[['None','Выключен'],['One','Один трек'],['All','Все треки']].map(([v,t])=>`<option value="${v}" ${n.repeat_mode===v?'selected':''}>${t}</option>`).join('')}</select><label>Перемешивать <input type="checkbox" data-f="shuffle" ${n.shuffle?'checked':''}></label>` : n.action==='play' ? `<input class="field" data-f="track" value="${esc(n.track||'')}" placeholder="ID трека" aria-label="ID трека">` : '');
    case 'query': return `<input type="text" class="field" data-f="text" maxlength="200" value="${esc(n.text)}" placeholder="включи режим не беспокоить" aria-label="Что попросить">`;
    case 'say': return `<input type="text" class="field" data-f="text" maxlength="200" value="${esc(n.text)}" placeholder="Пора спать" aria-label="Что сказать">`;
    case 'ask': return `<input type="text" class="field" data-f="text" maxlength="200" value="${esc(n.text)}" placeholder="расскажи сказку на ночь" aria-label="Вопрос ассистенту">`
      + (scHasAssistant ? '' : '<span class="meta">ассистент не установлен — шаг пропустится</span>');
    case 'assistant': return `<select class="field" data-f="mode" aria-label="Режим ассистента"><option value="on" ${n.mode ? 'selected' : ''}>включить</option><option value="off" ${n.mode ? '' : 'selected'}>выключить</option></select>
      <span class="meta">все вопросы к Алисе — своей модели</span>`;
    case 'vol': return `<input type="range" min="0" max="10" data-f="level" value="${n.level}" aria-label="Громкость"><span class="mono meta" style="min-width:38px">${n.level * 10}%</span>
      <select class="field" data-f="ramp" aria-label="Как менять">${SC_RAMPS.map(([v, t]) => `<option value="${v}" ${v === n.ramp ? 'selected' : ''}>${t}</option>`).join('')}</select>`;
    case 'sound': return `<select class="field" data-f="sound" style="flex:1;min-width:140px" aria-label="Звук">${soundOptions()}</select>`;
    case 'ring': return `<input type="color" data-f="color" value="${esc(n.color)}" aria-label="Цвет"><input type="number" class="field mono" data-f="secs" min="1" max="600" value="${n.secs}" aria-label="Секунд"><span class="meta">с</span>`;
    case 'wait': return `<input type="number" class="field mono" data-f="mins" min="0" max="1440" step="any" value="${n.secs / 60}" aria-label="Минут"><span class="meta">мин</span>`;
    case 'wait_until': return `<input type="time" class="field" data-f="time" value="${esc(n.time)}" aria-label="До какого времени">`;
    case 'wait_quiet': return `<span class="meta">пока Алиса и музыка не замолчат, но не дольше</span><input type="number" class="field mono" data-f="mins" min="1" max="120" value="${Math.round(n.secs / 60)}" aria-label="Минут"><span class="meta">мин</span>`;
    case 'scene': return `<select class="field" data-f="target" aria-label="Какой сценарий" style="flex:1;min-width:140px"><option value="">— выберите —</option>${(scData?.scenes || [])
      .filter(s => s.id && s.id !== scEdit.id).map(s => `<option value="${esc(s.id)}" ${s.id === n.target ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>`;
    case 'if': return `<select class="field" data-f="check" aria-label="Условие">${Object.entries(SC_CHECKS).map(([k, c]) => `<option value="${k}" ${k === n.check ? 'selected' : ''}>${c.name}</option>`).join('')}</select>`
      + (SC_CHECKS[n.check]?.ph ? `<input type="text" class="field mono" data-f="arg" value="${esc(n.arg)}" placeholder="${SC_CHECKS[n.check].ph}" aria-label="Значение" style="width:120px">` : '');
    case 'repeat': return `<input type="number" class="field mono" data-f="count" min="1" max="20" value="${n.count}" aria-label="Сколько раз"><span class="meta">раз (до 20), потом «готово»</span>`;
    case 'anim': return `<select class="field" data-f="anim" aria-label="Анимация" style="flex:1;min-width:140px">${[['ring', 'Кольцо'], ['clock', 'Часы']]
      .filter(([k]) => (patterns[k] || []).length).map(([k, g]) => `<optgroup label="${g}">${patterns[k].map(p => `<option value="${k}:${esc(p)}" ${n.anim === k + ':' + p ? 'selected' : ''}>${esc(animName(p))}</option>`).join('')}</optgroup>`).join('')}</select>
      <input type="number" class="field mono" data-f="secs" min="1" max="600" value="${n.secs}" aria-label="Секунд"><span class="meta">с</span>`;
    case 'note': return `<textarea class="field" data-f="text" rows="2" maxlength="200" placeholder="Заметка для себя" aria-label="Заметка">${esc(n.text)}</textarea>`;
    default: return '<span class="meta">остановить музыку и ответ Алисы</span>';
  }
}
function scInput(e) {
  const row = e.target.closest('[data-n]'), f = e.target.dataset.f; if (!row || !f) return;
  const n = scNode(scEdit, row.dataset.n);
  if (f === 'mins') n.secs = clamp(Math.round((+e.target.value || 0) * 60), 0, 86400);
  else if (['level', 'ramp', 'secs', 'count'].includes(f)) n[f] = +e.target.value;
  else if (f === 'mode') n.mode = e.target.value === 'on';
  else if (f === 'shuffle') n.shuffle = e.target.checked;
  else n[f] = e.target.value;
  if (f === 'level') e.target.nextElementSibling.textContent = n.level * 10 + '%';
  if (f === 'check') { n.arg = ''; scRender(); }
  if (f === 'action') { n.repeat_mode ??= 'None'; n.shuffle ??= false; scRender(); }
}
const scNextId = () => String(Math.max(0, ...scEdit.nodes.map(n => +n.id)) + 1);

// ---- список: только для цепочки без ветвлений ----
function renderScList() {
  const { chain, linear } = scChain(scEdit);
  if (!linear) {
    $('#sc-steps').innerHTML = '<div class="empty">В этом сценарии есть условия, повторы или заметки — его можно править только на поле. <button type="button" class="link" data-v="field">Открыть поле</button></div>';
    return;
  }
  $('#sc-steps').innerHTML = chain.length ? chain.map((n, i) => `<div class="sc-step" data-n="${esc(n.id)}">
    <span class="n">${i + 1}</span><span class="k">${icon(SC_NODES[n.kind].icon, 14)}${esc(SC_NODES[n.kind].name)}</span>
    <div class="v">${nodeFields(n)}</div>
    <span class="ctl"><button type="button" class="btn sm icon ghost" data-m="-1" aria-label="Выше" title="Выше">↑</button>
      <button type="button" class="btn sm icon ghost" data-m="1" aria-label="Ниже" title="Ниже">↓</button>
      <button type="button" class="btn sm icon ghost" data-m="del" aria-label="Удалить шаг" title="Удалить шаг">${icon('i-trash', 14)}</button></span></div>`).join('')
    : '<div class="empty">Добавьте первый шаг кнопками ниже</div>';
  scSoundValues($('#sc-steps'));
}
function scSetChain(chain) {   // перестраиваем связи по порядку списка
  scEdit.start = chain[0]?.id || '';
  chain.forEach((n, i) => { n.next = chain[i + 1]?.id || ''; n.else = ''; });
}
$('#sc-steps').oninput = scInput;
$('#sc-steps').onclick = e => {
  if (e.target.closest('[data-v]')) return scView('field');
  const b = e.target.closest('[data-m]'); if (!b) return;
  const chain = scChain(scEdit).chain, i = chain.findIndex(n => n.id === b.closest('[data-n]').dataset.n);
  if (b.dataset.m === 'del') { const [gone] = chain.splice(i, 1); scEdit.nodes = scEdit.nodes.filter(n => n !== gone); }
  else { const j = i + +b.dataset.m; if (j < 0 || j >= chain.length) return; [chain[i], chain[j]] = [chain[j], chain[i]]; }
  scSetChain(chain); scRender();
};
function scSoundValues(box) {
  box.querySelectorAll('[data-f="sound"]').forEach(s => { s.value = scNode(scEdit, s.closest('[data-n]').dataset.n).sound; if (s.selectedIndex < 0) s.value = 'sys:common/timer.wav'; });
}

// ---- поле: ноды, связи и перетаскивание ----
const SC_W = 236;
// Место ноды «Запуск» — в браузере. store хранит только строки: объект — через JSON, иначе вместо места
// сохранится «[object Object]», и всё, что ставится рядом с «Запуском», получит NaN.
const scXY = v => Number.isFinite(v?.x) && Number.isFinite(v?.y);
function scStartPos() {
  if (!scXY(scEdit._start)) {
    let v = null;
    try { v = JSON.parse(store.get('sc.start.' + (scEdit.id || 'new'))); } catch {}
    scEdit._start = scXY(v) ? v : { x: 24, y: 40 };
  }
  return scEdit._start;
}
const scStartSave = (id, p) => store.set('sc.start.' + (id || 'new'), JSON.stringify({ x: p.x, y: p.y }));
function renderScField() {
  if (scEdit.nodes.some(n => !scXY(n))) scLayout();   // место без чисел — пересчитываем, иначе нода застрянет в углу
  const f = $('#sc-field'), s = scEdit, st = scStartPos();
  const port = (key, label) => `<span class="port out" data-port="${key}">${label ? `<i>${label}</i>` : ''}</span>`;
  f.innerHTML = '<div class="sc-canvas"><svg class="sc-wires"></svg>'
    + `<div class="sc-node trig" data-n="start" style="left:${st.x}px;top:${st.y}px"><header>${icon('i-power', 14)}Запуск</header><div class="v"><span class="meta" id="sc-start-text">${esc(scTrigText())}</span></div>${port('start')}</div>`
    + s.nodes.map(n => {
      const def = SC_NODES[n.kind];
      return `<div class="sc-node k-${n.kind}${scSel === n.id ? ' sel' : ''}" data-n="${esc(n.id)}" style="left:${n.x}px;top:${n.y}px">
        <header>${icon(def.icon, 14)}${esc(def.name)}<button type="button" data-m="del" aria-label="Удалить ноду" title="Удалить ноду">×</button></header>
        <div class="v">${nodeFields(n)}</div>
        ${def.noPorts ? '' : '<span class="port in"></span>' + (def.outs ? port('next', def.outs[0]) + port('else', def.outs[1]) : port('next'))}</div>`;
    }).join('') + '</div>';
  scSoundValues(f);
  scApplyPan();
  scWires();
}
const scFieldStart = () => { const t = $('#sc-start-text'); if (t) t.textContent = scTrigText(); };
function scPortXY(el) {
  const r = el.getBoundingClientRect(), c = $('#sc-field .sc-canvas').getBoundingClientRect();
  return { x: r.left + r.width / 2 - c.left, y: r.top + r.height / 2 - c.top };
}
const scCurve = (a, b) => { const dx = Math.max(40, Math.abs(b.x - a.x) / 2); return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`; };
function scWires(tmp) {
  const c = $('#sc-field .sc-canvas'); if (!c) return;
  const nodes = [...c.querySelectorAll('.sc-node')];
  const inOf = id => c.querySelector(`.sc-node[data-n="${CSS.escape(id)}"] .port.in`);
  const link = (from, port, to) => { const a = c.querySelector(`.sc-node[data-n="${CSS.escape(from)}"] [data-port="${port}"]`), b = to && inOf(to); return a && b ? `<path class="w-${port}" d="${scCurve(scPortXY(a), scPortXY(b))}"/>` : ''; };
  let d = link('start', 'start', scEdit.start);
  scEdit.nodes.forEach(n => { d += link(n.id, 'next', n.next); if (SC_NODES[n.kind].outs) d += link(n.id, 'else', n.else); });
  if (tmp) d += `<path class="tmp" d="${scCurve(tmp.a, tmp.b)}"/>`;
  c.querySelector('.sc-wires').innerHTML = d;
}
function scSetLink(from, port, to) {
  if (from === 'start') scEdit.start = to;
  else scNode(scEdit, from)[port === 'else' ? 'else' : 'next'] = to;
}
$('#sc-field').addEventListener('pointerdown', e => {
  const c = $('#sc-field .sc-canvas'), nodeEl = e.target.closest('.sc-node');
  if (!nodeEl && !e.button) return scPan(e);
  if (!nodeEl || e.button) return;
  const id = nodeEl.dataset.n, cr = () => c.getBoundingClientRect();
  const portEl = e.target.closest('.port.out');
  if (portEl) {
    // тянем связь: отпустили над нодой — выход ведёт к ней, на пустом месте — связь убрана
    e.preventDefault();
    const a = scPortXY(portEl);
    let over = null;
    const move = ev => {
      over?.classList.remove('drop');
      over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('#sc-field .sc-node:not(.trig):not(.k-note)');
      over?.classList.add('drop');
      scWires({ a, b: { x: ev.clientX - cr().left, y: ev.clientY - cr().top } });
    };
    const up = () => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', up);
      over?.classList.remove('drop');
      scSetLink(id, portEl.dataset.port, over ? over.dataset.n : '');
      scWires();
    };
    addEventListener('pointermove', move); addEventListener('pointerup', up);
    return;
  }
  if (id !== 'start') { scSel = id; $$('#sc-field .sc-node').forEach(x => x.classList.toggle('sel', x === nodeEl)); }
  if (!e.target.closest('header') || e.target.closest('button')) return;
  // перетаскиваем ноду за заголовок; указатель захватываем — перенос не срывается, даже если мышь обогнала ноду
  e.preventDefault();
  const head = e.target.closest('header');
  const p = id === 'start' ? scStartPos() : scNode(scEdit, id), sx = e.clientX - p.x, sy = e.clientY - p.y;
  try { head.setPointerCapture(e.pointerId); } catch {}
  const move = ev => {
    p.x = Math.round(ev.clientX - sx); p.y = Math.round(ev.clientY - sy);
    nodeEl.style.left = p.x + 'px'; nodeEl.style.top = p.y + 'px';
    scWires();
  };
  const up = () => {
    head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', up); head.removeEventListener('pointercancel', up);
    if (id === 'start') scStartSave(scEdit.id, p);
  };
  head.addEventListener('pointermove', move); head.addEventListener('pointerup', up); head.addEventListener('pointercancel', up);
});
// Поле бесконечное: сдвиг холста хранится в редактируемом сценарии (на время правки), фон-сетка едет вместе с ним
const scPanXY = () => scEdit._pan || (scEdit._pan = { x: 0, y: 0 });
function scApplyPan(glide) {
  const c = $('#sc-field .sc-canvas'), p = scPanXY();
  if (!c) return;
  c.classList.toggle('glide', !!glide);
  c.style.transform = `translate(${p.x}px, ${p.y}px)`;
  $('#sc-field').style.backgroundPosition = `${p.x}px ${p.y}px`;
  if (glide) setTimeout(() => c.classList.remove('glide'), 320);
}
// Тянем фон — двигаем поле (мышью и пальцем), а не выделяем текст
function scPan(e) {
  const f = $('#sc-field'), p = scPanXY(), sx = e.clientX - p.x, sy = e.clientY - p.y;
  e.preventDefault();
  try { f.setPointerCapture(e.pointerId); } catch {}
  f.classList.add('panning');
  const move = ev => { p.x = Math.round(ev.clientX - sx); p.y = Math.round(ev.clientY - sy); scApplyPan(); };
  const up = () => {
    f.classList.remove('panning');
    f.removeEventListener('pointermove', move); f.removeEventListener('pointerup', up); f.removeEventListener('pointercancel', up);
  };
  f.addEventListener('pointermove', move); f.addEventListener('pointerup', up); f.addEventListener('pointercancel', up);
}
// На весь экран колесо и тачпад двигают поле; в обычном виде колесо прокручивает страницу
$('#sc-field').addEventListener('wheel', e => {
  if (!document.body.classList.contains('sc-full')) return;
  e.preventDefault();
  const p = scPanXY();
  p.x -= e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX; p.y -= e.shiftKey && !e.deltaX ? 0 : e.deltaY;
  scApplyPan();
}, { passive: false });
$('#sc-field').oninput = scInput;
$('#sc-field').addEventListener('click', e => {
  const b = e.target.closest('[data-m="del"]'); if (!b) return;
  const id = b.closest('.sc-node').dataset.n;
  scEdit.nodes = scEdit.nodes.filter(n => n.id !== id);
  if (scEdit.start === id) scEdit.start = '';
  scEdit.nodes.forEach(n => { if (n.next === id) n.next = ''; if (n.else === id) n.else = ''; });
  if (scSel === id) scSel = null;
  scRender();
});

// ---- добавление нод ----
function renderScPalette() {
  const field = !$('#sc-field').hidden, groups = {};
  Object.entries(SC_NODES).forEach(([k, v]) => {
    if (v.assistant && !scHasAssistant) return;
    if (!field && (v.outs || v.noPorts)) return;   // ветвления и заметки — только на поле
    (groups[v.group] ||= []).push(`<button type="button" class="btn sm" data-add="${k}">${icon(v.icon, 14)}${esc(v.name)}</button>`);
  });
  $('#sc-add-step').innerHTML = Object.entries(groups).map(([g, b]) => `<div class="grp"><span class="meta">${g}</span>${b.join('')}</div>`).join('')
    + (field ? '' : '<p class="hint">Условия, повторы и заметки — на поле.</p>');
}
$('#sc-add-step').onclick = e => {
  const b = e.target.closest('[data-add]'); if (!b) return;
  const kind = b.dataset.add, n = { id: scNextId(), kind, ...SC_NODES[kind].def(), next: '', else: '', x: 0, y: 0 };
  if ($('#sc-field').hidden) {   // список: в конец цепочки
    const chain = scChain(scEdit).chain;
    scEdit.nodes.push(n); scSetChain([...chain, n]);
  } else {
    // поле: рядом с выбранной нодой и сразу с ней связанная, если у неё свободный выход
    const from = scSel && scNode(scEdit, scSel) || null, f = $('#sc-field');
    const base = (scXY(from) ? from : null) || (scEdit.nodes.length ? null : scStartPos());
    n.x = base ? base.x + (base === scStartPos() ? 230 : SC_W + 40) : 40 - scPanXY().x;
    n.y = base ? base.y : 60 - scPanXY().y + (scEdit.nodes.length % 4) * 40;
    scEdit.nodes.push(n);
    if (n.kind !== 'note') {
      if (from && from.kind !== 'note' && !from.next) from.next = n.id;
      else if (!from && !scEdit.start) scEdit.start = n.id;
    }
    scSel = n.id;
  }
  scRender();
  if (!$('#sc-field').hidden) scReveal(n.id);
  $('#sc-board').classList.remove('pal-open');
};
// Новая нода могла встать за краем видимой части поля — подвигаем поле к ней
function scReveal(id) {
  const f = $('#sc-field'), el = f.querySelector(`.sc-node[data-n="${CSS.escape(id)}"]`);
  if (!el) return;
  const r = el.getBoundingClientRect(), fr = f.getBoundingClientRect();
  if (r.left >= fr.left && r.right <= fr.right && r.top >= fr.top && r.bottom <= fr.bottom) return;
  const p = scPanXY();
  p.x = Math.round(fr.width / 2 - el.offsetLeft - el.offsetWidth / 2);
  p.y = Math.round(fr.height / 3 - el.offsetTop);
  scApplyPan(true);
}

// ---- вид, открытие, сохранение ----
function scRender() {
  if (!scEdit) return;
  const field = !$('#sc-field').hidden;
  if (field) renderScField(); else renderScList();
  renderScPalette();
}
function scView(v) {
  if (v === 'list' && !scChain(scEdit).linear) v = 'field';
  $$('#sc-view button').forEach(b => b.classList.toggle('active', b.dataset.v === v));
  $('#sc-view [data-v="list"]').disabled = !scChain(scEdit).linear;
  $('#sc-view [data-v="list"]').title = scChain(scEdit).linear ? '' : 'В сценарии есть ветвления — только поле';
  store.set('sc.view', v);
  $('#sc-steps').hidden = v === 'field';
  $('#sc-field').hidden = $('#sc-field-hint').hidden = $('#sc-arrange').hidden = v !== 'field';
  $$('.sc-arrange-row [data-sc-center]').forEach(b => b.hidden = v !== 'field');
  $('#sc-board').classList.toggle('fieldmode', v === 'field');
  if (v === 'field') scLayout();   // ноды, добавленные в списке, места на поле ещё не имеют
  scRender();
}
$$('#sc-view button').forEach(b => b.onclick = () => scView(b.dataset.v));
$('#sc-arrange').onclick = $('#sc-arrange-full').onclick = () => { scLayout(true); renderScField(); };
// Поле на весь экран: на телефоне — единственный способ его увидеть, на компьютере — кнопка в углу поля
function scFull(open) {
  if (open) scView('field');
  $('#sc-board').classList.toggle('full-open', open);
  if (!open) $('#sc-board').classList.remove('pal-open');
  document.body.classList.toggle('sc-full', open);
  if (scEdit && (open || $('#sc-field').offsetParent)) renderScField();   // размеры поля изменились — связи пересчитать
}
$('#sc-open-field').onclick = $('#sc-expand').onclick = () => scFull(true);
// «К нодам»: ноды — по центру поля, а если не влезают — от левого верхнего края
function scCenter() {
  const f = $('#sc-field'), nodes = [...f.querySelectorAll('.sc-node')];
  if (!nodes.length) return;
  const x0 = Math.min(...nodes.map(n => n.offsetLeft)), y0 = Math.min(...nodes.map(n => n.offsetTop));
  const x1 = Math.max(...nodes.map(n => n.offsetLeft + n.offsetWidth)), y1 = Math.max(...nodes.map(n => n.offsetTop + n.offsetHeight));
  const at = (a, b, size) => Math.round(b - a + 40 > size ? 20 - a : (size - a - b) / 2);
  Object.assign(scPanXY(), { x: at(x0, x1, f.clientWidth), y: at(y0, y1, f.clientHeight) });
  scApplyPan(true);
}
$$('[data-sc-center]').forEach(b => b.onclick = scCenter);
$('#sc-full-close').onclick = () => scFull(false);
$('#sc-fab').onclick = () => $('#sc-board').classList.toggle('pal-open');
$('#sc-pal-backdrop').onclick = () => $('#sc-board').classList.remove('pal-open');
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || !document.body.classList.contains('sc-full')) return;
  if ($('#sc-board').classList.contains('pal-open')) $('#sc-board').classList.remove('pal-open'); else scFull(false);
});
addEventListener('hashchange', () => { if (document.body.classList.contains('sc-full')) scFull(false); });
// Ноды без места (из списка) — по порядку цепочки змейкой; all — разложить заново все.
// Ноды, оказавшиеся в одной точке, тоже разводим: иначе верхняя закрывает остальные.
function scLayout(all) {
  const { chain } = scChain(scEdit);
  const order = [...chain, ...scEdit.nodes.filter(n => !chain.includes(n))];
  const seen = new Set();
  order.forEach((n, i) => {
    if (all || !scXY(n) || (!n.x && !n.y) || seen.has(n.x + ',' + n.y)) { n.x = 260 + (i % 3) * (SC_W + 14); n.y = 30 + Math.floor(i / 3) * 190 + (i % 2) * 30; }
    seen.add(n.x + ',' + n.y);
  });
}
function openScene(s) {
  scEdit = structuredClone(s || { id: '', name: '', trigger: 'time', time: '23:00', days: '1234567', on: false, start: '', nodes: [] });
  delete scEdit._start; scSel = null;
  scLayout();
  $('#sc-ed-title').textContent = s ? 'Сценарий' : 'Новый сценарий';
  $('#sc-name').value = scEdit.name;
  $('#sc-trigger').value = scEdit.trigger;
  $('#sc-execution').value = scEdit.execution || 'pc';
  $('#sc-time').value = scEdit.time || '23:00';
  $('#sc-phrases').value = (scEdit.phrases || []).join(', ');
  $$('#sc-days .chip').forEach(c => c.setAttribute('aria-pressed', scEdit.days.includes(c.dataset.d)));
  $('#sc-del').hidden = !s;
  $('#sc-trigger [value="voice"]').disabled = !scHasVoice && scEdit.trigger !== 'voice';
  $('#sc-editor').hidden = false;
  scTriggerUI();
  scView(store.get('sc.view') || 'list');
  $('#sc-editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
$('#sc-add').onclick = () => openScene(null);
$('#sc-cancel').onclick = () => { scFull(false); $('#sc-editor').hidden = true; scEdit = null; };
$('#sc-form').onsubmit = e => {
  scFull(false);
  e.preventDefault();
  scLayout();
  const { _start, ...rest } = scEdit;
  const s = { ...rest, execution: $('#sc-execution').value, name: $('#sc-name').value.trim(), trigger: $('#sc-trigger').value, time: $('#sc-time').value, days: scDays(), phrases: scPhrases() };
  if (s.trigger === 'voice' && !s.phrases.length) return toast('Укажите фразу, по которой ассистент запустит сценарий', true);
  if (!s.nodes.some(n => n.kind !== 'note')) return toast('Добавьте хотя бы один шаг', true);
  const list = s.id ? scData.scenes.map(x => x.id === s.id ? s : x) : [...scData.scenes, s];
  act($('#sc-save'), async () => {
    const r = await scSave(list);
    const saved = s.id ? r.scenes.find(x => x.id === s.id) : r.scenes[r.scenes.length - 1];
    if (scXY(_start) && saved) scStartSave(saved.id, _start);
    $('#sc-editor').hidden = true; scEdit = null;
  }, `Сценарий «${s.name}» сохранён`);
};
$('#sc-del').onclick = e => confirm(`Удалить сценарий «${scEdit.name}»?`) &&
  act(e.currentTarget, async () => { await scSave(scData.scenes.filter(x => x.id !== scEdit.id)); $('#sc-editor').hidden = true; scEdit = null; }, 'Сценарий удалён');


$('#sc-deploy').onclick=e=>confirm('Развернуть отмеченные автономные сценарии и обновить alarmd только в /data? Существующие daemon и расписание будут сохранены в резервную копию. Громкость не нарастает; системный init не меняется. Сценарии с включённым расписанием смогут запускаться на колонке. Музыкальным действиям нужен работающий ПК.') && act(e.currentTarget,async()=>{const r=await api('/api/scenes/deploy',{});toast(r.note);await loadScenes();});
