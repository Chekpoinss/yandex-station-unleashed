// Choose the visual model from the device; hardware capabilities remain explicit.
import {rendererProfile} from './renderer-profile.js';
async function loadProfile(){
  for(;;){
    const auth=await PanelContext.fetch('/api/panel-auth').then(r=>r.json()).catch(()=>null);
    if(auth&&(!auth.enabled||auth.authed)){
      const response=await PanelContext.fetch('/api/profile');if(response.ok)return response.json();
    }
    await new Promise(resolve=>setTimeout(resolve,2000));
  }
}
const view=document.getElementById('model-view');
const loading=document.createElement('p');loading.className='studio-caption';loading.textContent='Ожидаю данные колонки…';view.append(loading);
const profile=await loadProfile();
let status;
for(;;){
  try{
    const response=await PanelContext.fetch('/api/status');
    if(response.ok){const value=await response.json();if(value.hostname||value.hardware_model){status=value;break;}}
  }catch{}
  await new Promise(resolve=>setTimeout(resolve,2000));
}
const visual=rendererProfile(profile,status);
loading.textContent='Загружаю 3D-модель…';
const gallery = document.getElementById("model-gallery");
if (gallery) gallery.href = PanelContext.url("static/model-preview.html") + "?model=" + encodeURIComponent(visual.id);
window.__deviceColor=status.color||window.__deviceColor;
document.title=visual.name+' — панель';
document.getElementById('model-name').textContent=visual.name;
view.dataset.hardwareProfile=profile.id;
if(visual.id!==profile.id && !(visual.id==='lite' && profile.id==='lite1')){
  const note=document.createElement('p');note.className='hint';note.id='model-profile-note';
  note.textContent=visual.id==='lite'
    ? 'Станция Лайт первого поколения. Для управления пока используется профиль «'+profile.name+'»; совместимость аппаратных функций не подтверждена.'
    : '3D: '+visual.name+'. Профиль управления: '+profile.name+'. Для аппаратных функций выберите --model '+visual.id+' при запуске сервера.';
  view.after(note);
}
const hide=element=>element?.setAttribute('data-profile-hidden','');
if(!profile.clock)hide(document.querySelector('[data-widget="clock"]'));
if(!profile.light)hide(document.querySelector('[data-widget="light"]'));
if(!profile.boot)hide(document.getElementById('privacy-row'));
window.panelProfile=profile;
if(!profile.clock)hide(document.getElementById('boot-card'));
if(!profile.light)hide(document.getElementById('ring-anim-card'));
if(!profile.alarms){hide(document.querySelector('a[href="#alarm"]'));const tab=document.querySelector('#tab-alarm');for(const child of tab.children)hide(child);tab.insertAdjacentHTML('afterbegin','<p class="card">Автозапуск будильника этой модели ещё не поддерживается. Существующие будильники не изменяются.</p>');}
if(profile.id!=='lite2'){
  hide(document.getElementById('pat-play'));
  document.querySelectorAll('[data-k="clock"]').forEach(e=>{if(!profile.clock)hide(e);});
  document.querySelector('[data-widget="light"] .hint').textContent=profile.light_note;
}
// У Лайт первого поколения нет дисплея: только светящийся логотип Алисы — прячем всё про часы.
if(visual.id==='lite'){
  document.querySelector('#tab-boot .page-head .sub').textContent='Анимации логотипа Алисы: кадры, цвета и текст .led. Системная подмена временная, до перезагрузки; оригинал сохраняется.';
  hide(document.querySelector('[data-widget="clock"]'));hide(document.getElementById('pat-play'));
  document.querySelectorAll('[data-k="clock"]').forEach(hide);
}
try {
  // Version the imported renderer too: updating only model.js leaves the browser
  // free to combine a new loader with a cached renderer from before the merge.
  if(visual.id==='lite2')await import('./model-lite2.js?v=020-model3');
  else{const {createModel}=await import('./model-studio.js?v=020-model3');createModel(visual);}
  loading.remove();
} catch(error) {
  loading.textContent='Не удалось загрузить 3D-модель. Обновите страницу с Ctrl+F5.';
  console.error('Загрузка 3D-модели:',error);
}
