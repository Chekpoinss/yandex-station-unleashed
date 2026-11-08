/* Additions to the existing panel; no parallel player or separate interface. */
(() => {
  document.querySelector('#say-form').onsubmit = e => {
    e.preventDefault();
    const text = document.querySelector('#say-text').value.trim();
    if (text) act(e.submitter, () => api('/api/say', {text, voice: document.querySelector('#say-voice').value}), 'Запрос речи отправлен Алисе');
  };
  document.querySelector('#say-voice').onchange = e => { store.set('say.voice', e.target.value); document.querySelector('#say-hint').textContent = SAY_HINT[e.target.value]; };
})();
document.querySelectorAll('[data-report]').forEach(b => b.onclick = () => act(b, async () => {
  const box=document.getElementById(b.dataset.report);
  box.value=(await api('/api/report')).text;box.hidden=false;box.focus();box.select();
}, 'Отчёт готов — проверьте его перед отправкой'));
// A refreshed page must not suggest that an old running backend has loaded new code.
(async () => {
  let ready = false;
  try { ready = (await api('/api/extensions')).revision >= 3; } catch {}
  if (ready) return;
  const banner = document.createElement('p'); banner.className = 'card hint'; banner.setAttribute('role','status');
  banner.textContent = 'Новый интерфейс загружен, но новые функции сервера ещё не доступны. Вручную закройте прежнее окно панели и запустите open-my-panel.cmd, затем обновите страницу. Перезапуск колонки не нужен.';
  document.querySelector('#tab-overview > .page-head').after(banner);
  const disable = () => document.querySelectorAll(':is([data-panel-extension], #tab-scenes, #tab-assistant, #tab-radio, #tab-boot) :is(button,input,select,textarea)').forEach(el => { if (!el.disabled) el.disabled = true; });
  disable();
  new MutationObserver(disable).observe(document.querySelector('body'), {childList:true,subtree:true});
})();
