"""Read-only client review surface built around the shared MebelScene engine."""

from __future__ import annotations

import json
from typing import Any, Mapping


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def build_review_page(
    record: Mapping[str, Any],
    *,
    viewer: Mapping[str, Any],
    stats: Mapping[str, Any],
    scene_js: str,
) -> str:
    from .studio_review_pdf import PRESENTATION_JS

    public_review = {
        "project_name": str(record.get("project_name") or "Изделие"),
        "organization_name": str(record.get("organization_name") or ""),
        "revision": str(record.get("revision") or ""),
        "link_mode": str(record.get("link_mode") or "snapshot"),
        "created_at": str(record.get("created_at") or ""),
        "expires_at": str(record.get("expires_at") or ""),
        "status": str(record.get("status") or "pending"),
        "decision": record.get("decision"),
    }
    payload = {"viewer": dict(viewer), "stats": dict(stats)}
    return (
        _PAGE.replace("__SCENE_JS__", scene_js)
        .replace("__PRESENTATION_JS__", PRESENTATION_JS)
        .replace("__REVIEW__", _safe_json(public_review))
        .replace("__PAYLOAD__", _safe_json(payload))
    )


_PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow,noarchive">
<meta name="referrer" content="no-referrer">
<title>Просмотр изделия — Akeda Studio</title>
<style>
  :root{--canvas:#edf0f4;--paper:#fff;--ink:#222a34;--muted:#6b7480;--line:#d9dee5;
    --line-strong:#c7cdd6;--accent:#2f6cdf;--accent-soft:#e9f0ff;--ok:#16834a;
    --warn:#aa6617;--bad:#b7342d;--shadow:0 14px 34px rgba(24,34,47,.12)}
  *{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden}
  body{background:var(--canvas);color:var(--ink);font:13px/1.42 -apple-system,BlinkMacSystemFont,
    "Segoe UI",Arial,sans-serif}
  button,input,textarea{font:inherit}button{color:inherit}
  button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);
    outline-offset:2px}
  .shell{display:grid;grid-template-rows:58px minmax(0,1fr);height:100dvh}
  header{display:flex;align-items:center;gap:20px;padding:0 18px;border-bottom:1px solid var(--line);
    background:rgba(255,255,255,.97);z-index:20}
  .brand{display:flex;align-items:center;gap:9px;flex:0 0 auto}.brand img{display:block;height:27px;width:auto}
  .brand-mark{height:25px!important}.title-block{min-width:0;flex:1;padding-left:18px;
    border-left:1px solid var(--line)}
  .eyebrow{display:block;margin-bottom:1px;color:#747e8b;font-size:9.5px;font-weight:700;
    letter-spacing:.075em;text-transform:uppercase}
  h1{margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:15px;
    line-height:20px;font-weight:700}
  .revision{display:flex;align-items:center;gap:10px;flex:0 0 auto;color:#64707e;font-size:10.5px}
  .revision code{padding:3px 6px;border:1px solid var(--line);border-radius:3px;background:#f6f7f9;
    color:#414b58;font:10px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace}
  .status{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border-radius:3px;
    background:#f5f6f8;color:#596473;font-size:10.5px;font-weight:650}
  .status::before{content:"";width:6px;height:6px;border-radius:50%;background:#c18227}
  .status.approved{background:#e9f6ef;color:#126c3d}.status.approved::before{background:var(--ok)}
  .status.changes_requested{background:#fff3e5;color:#8a5211}.status.changes_requested::before{background:#c57a20}
  .pdf-action{height:31px;padding:0 10px;border:1px solid var(--line-strong);border-radius:4px;
    background:#fff;color:#35404d;font-size:10.5px;font-weight:700;cursor:pointer;white-space:nowrap}
  .pdf-action:hover{background:#f1f4f7}.pdf-action:disabled{opacity:.58;cursor:wait}
  main{display:grid;grid-template-columns:minmax(0,1fr) 330px;min-height:0}
  .stage{position:relative;min-width:0;min-height:0;overflow:hidden;background:#e8ecf1}
  #view3d{position:absolute;inset:0}.stage-tools{position:absolute;z-index:8;display:flex;
    border:1px solid var(--line-strong);border-radius:4px;background:#fff;box-shadow:0 3px 10px rgba(25,34,45,.09)}
  .stage-tools button{display:flex;align-items:center;justify-content:center;height:31px;padding:0 9px;
    border:0;border-right:1px solid #e2e5e9;background:#fff;font-size:10.5px;cursor:pointer}
  .stage-tools button:last-child{border-right:0}.stage-tools button:hover{background:#f1f4f7}
  .stage-tools button.on{background:var(--accent-soft);color:#1f5fc9;font-weight:700}
  #views{left:12px;top:12px}#layers{left:12px;top:53px}.model-actions{left:12px;top:94px}
  #layers button[aria-pressed="true"]{background:var(--accent-soft);color:#1f5fc9;font-weight:700}
  .explode{display:grid;grid-template-columns:auto 84px 29px;align-items:center;gap:6px;
    min-height:31px;padding:0 8px;color:#596473;font-size:10.5px;white-space:nowrap}
  .explode input{width:84px;margin:0;accent-color:var(--accent)}.explode output{text-align:right;
    font:9.5px/1 ui-monospace,SFMono-Regular,Consolas,monospace}
  .hint{position:absolute;left:12px;bottom:11px;z-index:7;padding:6px 8px;border:1px solid rgba(207,213,221,.9);
    border-radius:3px;background:rgba(255,255,255,.88);color:#626d7b;font-size:10px;
    backdrop-filter:blur(8px)}
  aside{display:flex;flex-direction:column;min-width:0;min-height:0;border-left:1px solid var(--line);
    background:var(--paper)}
  .aside-scroll{flex:1;min-height:0;overflow:auto}.panel{padding:14px 16px;border-bottom:1px solid #e4e7eb}
  .panel-label{display:block;margin-bottom:4px;color:#75808d;font-size:9.5px;font-weight:750;
    letter-spacing:.06em;text-transform:uppercase}.panel h2{margin:0;font-size:15px;line-height:20px}
  .overview-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;margin-top:12px}
  .metric{min-width:0}.metric span{display:block;color:#79838f;font-size:9.5px}.metric strong{display:block;
    margin-top:2px;overflow:hidden;text-overflow:ellipsis;color:#303946;font-size:11.5px;
    font-weight:650;white-space:nowrap}.metric.wide{grid-column:1/-1}
  .part-empty{padding:20px 16px;color:#6d7784;font-size:11px;line-height:17px}.part-empty strong{display:block;
    margin-bottom:4px;color:#3c4653;font-size:12px}
  #partPanel[hidden],#partEmpty[hidden]{display:none}.part-head{padding:14px 16px 10px;border-bottom:1px solid #e7eaee}
  .part-head h2{margin:0;font-size:14px;line-height:19px;overflow-wrap:anywhere}.part-type{margin-top:3px;
    color:#75808d;font-size:10px}.part-data{margin:0;padding:11px 16px 15px}.part-row{display:grid;
    grid-template-columns:96px minmax(0,1fr);gap:8px;padding:4px 0;font-size:11px;line-height:15px}
  .part-row dt{color:#75808d}.part-row dd{margin:0;color:#35404d;font-weight:550;overflow-wrap:anywhere}
  .decision{flex:0 0 auto;padding:13px 16px 15px;border-top:1px solid var(--line);background:#fbfcfd}
  .decision h2{margin:0 0 4px;font-size:12.5px}.decision-copy{margin:0 0 9px;color:#6d7784;
    font-size:10.5px;line-height:15px}.decision input,.decision textarea{display:block;width:100%;border:1px solid #ccd2da;
    border-radius:4px;background:#fff;color:#2f3945}.decision input{height:31px;padding:0 8px}.decision textarea{height:62px;
    margin-top:6px;padding:7px 8px;resize:vertical}.decision-actions{display:grid;grid-template-columns:1fr 1fr;
    gap:6px;margin-top:8px}.decision-actions button{height:32px;border:1px solid #cbd1d9;border-radius:4px;
    background:#fff;font-size:10.5px;font-weight:650;cursor:pointer}.decision-actions button:hover{background:#f1f4f7}
  #approve{border-color:#15834a;background:#16834a;color:#fff}#approve:hover{background:#116f3e}
  .attachment-field{margin-top:8px}.attachment-trigger{display:inline-flex;align-items:center;gap:6px;min-height:31px;
    padding:0 9px;border:1px solid #cbd1d9;border-radius:4px;background:#fff;color:#44505d;font-size:10.5px;
    font-weight:650;cursor:pointer}.attachment-trigger:hover{background:#f1f4f7}.attachment-trigger svg{width:14px;height:14px;
    fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
  .attachment-help{margin:5px 0 0;color:#858e9a;font-size:9.5px;line-height:13px}.attachment-list{display:grid;
    gap:4px;margin-top:6px}.attachment-item{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;
    gap:8px;min-height:30px;padding:4px 5px 4px 8px;border:1px solid #dde2e8;border-radius:4px;background:#fff;
    color:#46515e;font-size:10px}.attachment-item span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .attachment-item button{width:24px;height:24px;padding:0;border:0;border-radius:3px;background:#f1f3f6;
    color:#626d79;cursor:pointer}.attachment-item a{color:#245eae;text-decoration:none}.attachment-item a:hover{text-decoration:underline}
  .decision-result{display:none;margin-top:8px;padding:7px 8px;border-radius:3px;background:#edf3ff;
    color:#31547f;font-size:10.5px}.decision-result.on{display:block}.decision button:disabled{opacity:.55;cursor:wait}
  .mobile-dock,.sheet-head,.sheet-scrim{display:none}
  #presentationCapture{position:fixed;left:-10000px;top:0;width:1280px;height:800px;visibility:hidden;
    pointer-events:none;overflow:hidden}
  @media(max-width:900px){main{grid-template-columns:minmax(0,1fr) 286px}.revision span{display:none}}
  @media(max-width:700px){
    .shell{grid-template-rows:52px minmax(0,1fr)}header{padding:0 max(10px,env(safe-area-inset-right)) 0 max(10px,env(safe-area-inset-left));gap:8px}
    .brand{display:none}.revision{display:none}.title-block{padding-left:0;border-left:0}.eyebrow{font-size:8.5px}
    h1{font-size:13px}.status{max-width:118px;padding:5px 7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9.5px}
    main{position:relative;grid-template-columns:1fr}.stage{padding-bottom:58px}
    .stage-tools{left:8px;max-width:calc(100vw - 16px);overflow-x:auto;overscroll-behavior-x:contain;
      scrollbar-width:none;-webkit-overflow-scrolling:touch}.stage-tools::-webkit-scrollbar{display:none}
    .stage-tools button{min-width:46px;height:42px;padding:0 10px;font-size:11px;white-space:nowrap}
    #views{top:8px}#layers{top:56px}.model-actions{top:104px}.model-actions button{min-width:93px}
    .explode{grid-template-columns:auto 90px 31px;min-height:42px;font-size:11px}.explode input{width:90px}.hint{display:none}
    .mobile-dock{position:absolute;display:grid;grid-template-columns:.75fr 1fr 1fr;gap:8px;left:max(10px,env(safe-area-inset-left));
      right:max(10px,env(safe-area-inset-right));bottom:max(9px,env(safe-area-inset-bottom));z-index:11;padding:5px;
      border:1px solid rgba(199,205,214,.96);border-radius:10px;background:rgba(255,255,255,.94);
      box-shadow:0 7px 22px rgba(24,34,47,.16);backdrop-filter:blur(12px)}
    .mobile-dock button{height:42px;border:0;border-radius:6px;background:#f1f3f6;color:#3f4a57;font-size:12px;font-weight:700}
    .mobile-dock #mobileDecision{background:var(--accent);color:#fff}
    .sheet-scrim{position:absolute;display:block;inset:0;z-index:12;background:rgba(25,31,39,.26);opacity:0;
      visibility:hidden;transition:opacity .18s ease,visibility .18s}.sheet-scrim.open{opacity:1;visibility:visible}
    aside{position:absolute;display:block;left:0;right:0;top:auto;bottom:0;z-index:13;width:100%;height:min(70dvh,570px);
      padding-bottom:env(safe-area-inset-bottom);border:0;border-radius:16px 16px 0 0;box-shadow:0 -14px 34px rgba(24,34,47,.18);
      overflow:auto;overscroll-behavior:contain;transform:translateY(102%);transition:transform .2s ease;will-change:transform}
    aside.open{transform:none}.sheet-head{display:flex;align-items:center;min-height:50px;padding:0 12px 0 16px;
      position:sticky;top:0;z-index:2;border-bottom:1px solid var(--line);background:#fff;border-radius:16px 16px 0 0}
    .sheet-head::before{content:"";position:absolute;left:50%;top:7px;width:34px;height:4px;border-radius:3px;
      background:#d6dbe2;transform:translateX(-50%)}.sheet-head strong{margin-top:5px;font-size:12px}
    .sheet-head button{width:40px;height:40px;margin:5px 0 0 auto;border:0;border-radius:6px;background:#f2f4f7;
      font-size:20px;cursor:pointer}.aside-scroll{overflow:visible}.panel{padding:16px}
    .part-empty{padding:20px 16px}.decision{padding:15px 16px 16px}.decision input{height:44px;padding:0 11px}
    .decision textarea{height:82px;padding:10px 11px}.attachment-trigger{min-height:44px;font-size:12px}
    .attachment-item{min-height:44px;font-size:11px}.attachment-item button{width:36px;height:36px}
    .decision-actions{gap:8px}.decision-actions button{height:44px;font-size:12px}
    header>.pdf-action{display:none}
  }
  @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style></head><body>
<div class="shell">
  <header>
    <a class="brand" href="https://akeda.ru" aria-label="Akeda Studio">
      <img src="/assets/studio/akeda-studio-wordmark.png" alt="Akeda Studio">
      <img class="brand-mark" src="/assets/studio/akeda-studio-mark.png" alt="">
    </a>
    <div class="title-block"><span id="reviewContext" class="eyebrow">Версия для согласования</span><h1 id="projectName"></h1></div>
    <div class="revision"><span id="createdAt"></span><code id="revision"></code></div>
    <button id="downloadPdf" class="pdf-action" type="button">Скачать PDF</button>
    <div id="reviewStatus" class="status"></div>
  </header>
  <main>
    <section class="stage" aria-label="Интерактивная 3D-модель изделия">
      <div id="view3d"></div>
      <div id="views" class="stage-tools" role="toolbar" aria-label="Ракурс модели">
        <button type="button" class="on" data-view="axon">Аксон</button><button type="button" data-view="persp">Персп.</button>
        <button type="button" data-view="top">Сверху</button><button type="button" data-view="front">Спереди</button>
        <button type="button" data-view="left">Слева</button>
      </div>
      <div id="layers" class="stage-tools" role="toolbar" aria-label="Отображение модели">
        <button type="button" data-layer="holes" aria-pressed="true">Присадки</button>
        <button type="button" data-layer="hardware" aria-pressed="true">Фурнитура</button>
        <button type="button" data-layer="textures" aria-pressed="true">Текстура</button>
        <button type="button" data-layer="dimensions" aria-pressed="true">Размеры</button>
        <button type="button" data-layer="xray" aria-pressed="false">Прозрачность</button>
      </div>
      <div class="stage-tools model-actions" role="group" aria-label="Открытие и разбор изделия">
        <button id="openAll" type="button">Открыть всё</button><button id="closeAll" type="button">Закрыть всё</button>
        <label class="explode"><span>Разбор</span><input id="explode" type="range" min="0" max="100" value="0">
          <output id="explodeValue">0%</output></label>
      </div>
      <div class="hint">Перетаскивание — вращать · колесо — масштаб · клик — выбрать деталь или открыть фасад</div>
    </section>
    <nav class="mobile-dock" aria-label="Действия с версией">
      <button id="mobilePdf" type="button">PDF</button>
      <button id="mobileDetails" type="button">Об изделии</button>
      <button id="mobileDecision" type="button">Согласовать</button>
    </nav>
    <div id="sheetScrim" class="sheet-scrim"></div>
    <aside id="reviewSheet" aria-label="Сведения об изделии" aria-hidden="false">
      <div class="sheet-head"><strong id="sheetTitle">Об изделии</strong>
        <button id="sheetClose" type="button" aria-label="Закрыть сведения">×</button></div>
      <div class="aside-scroll">
        <section class="panel"><span class="panel-label">Изделие</span><h2 id="overviewName"></h2>
          <div class="overview-grid">
            <div class="metric"><span>Габариты</span><strong id="overviewDims"></strong></div>
            <div class="metric"><span>Деталей</span><strong id="overviewPanels"></strong></div>
            <div class="metric wide"><span>Материал</span><strong id="overviewDecor"></strong></div>
          </div>
        </section>
        <div id="partEmpty" class="part-empty"><strong>Выберите деталь на модели</strong>
          Здесь появятся её размеры, материал, кромка и количество присадок.</div>
        <section id="partPanel" hidden>
          <div class="part-head"><span class="panel-label">Выбранная деталь</span><h2 id="partName"></h2>
            <div id="partType" class="part-type"></div></div>
          <dl class="part-data">
            <div class="part-row"><dt>Габарит X</dt><dd id="partX"></dd></div>
            <div class="part-row"><dt>Габарит Y</dt><dd id="partY"></dd></div>
            <div class="part-row"><dt>Габарит Z</dt><dd id="partZ"></dd></div>
            <div class="part-row"><dt>Толщина</dt><dd id="partThickness"></dd></div>
            <div class="part-row"><dt>Материал</dt><dd id="partMaterial"></dd></div>
            <div class="part-row"><dt>Кромка</dt><dd id="partEdges"></dd></div>
            <div class="part-row"><dt>Присадки</dt><dd id="partHoles"></dd></div>
          </dl>
        </section>
      </div>
      <section class="decision" aria-labelledby="decisionTitle"><h2 id="decisionTitle">Решение по этой версии</h2>
        <p id="decisionCopy" class="decision-copy">Ответ сохранится именно для версии, которую вы сейчас видите.</p>
        <label><span class="panel-label">Ваше имя</span><input id="reviewerName" autocomplete="name"></label>
        <label><span class="panel-label" style="margin-top:7px">Комментарий</span>
          <textarea id="reviewComment" placeholder="Если нужны изменения — опишите их здесь"></textarea></label>
        <div class="attachment-field">
          <input id="reviewFiles" type="file" accept="image/jpeg,image/png,image/webp,application/pdf" multiple hidden>
          <button id="pickFiles" class="attachment-trigger" type="button"><svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 12.5 14.6 5.9a4 4 0 0 1 5.7 5.7l-8.9 8.9a6 6 0 0 1-8.5-8.5l8.4-8.4"/>
          </svg>Прикрепить файлы</button>
          <p class="attachment-help">До 5 файлов · JPG, PNG, WEBP или PDF · до 10 МБ каждый</p>
          <div id="attachmentList" class="attachment-list"></div>
        </div>
        <div class="decision-actions"><button id="requestChanges" type="button">Нужны изменения</button>
          <button id="approve" type="button">Согласовать</button></div>
        <div id="decisionResult" class="decision-result" role="status" aria-live="polite"></div>
      </section>
    </aside>
  </main>
</div>
<div id="presentationCapture" aria-hidden="true"></div>
<script src="/vendor/three.min.js"></script><script src="/vendor/OrbitControls.js"></script>
<script>
__SCENE_JS__
const REVIEW=__REVIEW__,PAYLOAD=__PAYLOAD__,$=id=>document.getElementById(id);
let selectedFiles=[];
const statusLabels={pending:'Ожидает решения',approved:'Согласовано',changes_requested:'Нужны изменения'};
function formatDate(value){const date=new Date(value);return Number.isNaN(date.getTime())?'':
  date.toLocaleString('ru-RU',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).replace(',',' ·');}
function renderReviewStatus(){const element=$('reviewStatus');element.className='status '+REVIEW.status;
  element.textContent=statusLabels[REVIEW.status]||'На согласовании';
  if(REVIEW.decision){$('decisionResult').classList.add('on');$('decisionResult').textContent=
    `${statusLabels[REVIEW.decision.status]} · ${REVIEW.decision.reviewer_name}`+
    (REVIEW.decision.comment?` — ${REVIEW.decision.comment}`:'');renderAttachmentList();}}
$('reviewContext').textContent=(REVIEW.organization_name?REVIEW.organization_name+' · ':'')+
  (REVIEW.link_mode==='live'?'Обновляемый просмотр':'Фиксированная версия');
$('decisionCopy').textContent=REVIEW.link_mode==='live'?
  'Ответ сохранится для версии, которую вы сейчас видите. Если изделие обновится во время просмотра, мы попросим проверить его заново.':
  'Ответ сохранится именно для зафиксированной версии, которую вы сейчас видите.';
$('projectName').textContent=REVIEW.project_name;$('overviewName').textContent=REVIEW.project_name;
$('createdAt').textContent=formatDate(REVIEW.created_at);$('revision').textContent=(REVIEW.revision||'').slice(0,10);
$('overviewDims').textContent=PAYLOAD.stats.dims||'—';$('overviewPanels').textContent=PAYLOAD.stats.n_panels??'—';
$('overviewDecor').textContent=PAYLOAD.stats.decor||'Не указан';renderReviewStatus();
const mobileQuery=matchMedia('(max-width:700px)'),sheet=$('reviewSheet'),sheetScroll=sheet.querySelector('.aside-scroll');
function setSheet(open,target='details'){
  if(!mobileQuery.matches)return;sheet.classList.toggle('open',open);$('sheetScrim').classList.toggle('open',open);
  sheet.setAttribute('aria-hidden',String(!open));if(!open)return;
  $('sheetTitle').textContent=target==='decision'?'Согласование версии':target==='part'?'Параметры детали':'Об изделии';
  requestAnimationFrame(()=>{if(target==='decision')$('decisionTitle').scrollIntoView({block:'start'});else sheetScroll.scrollTop=0;});
}
$('mobileDetails').onclick=()=>setSheet(true,'details');$('mobileDecision').onclick=()=>setSheet(true,'decision');
$('downloadPdf').onclick=event=>downloadPresentationPdf(event.currentTarget);
$('mobilePdf').onclick=event=>downloadPresentationPdf(event.currentTarget);
$('sheetClose').onclick=()=>setSheet(false);$('sheetScrim').onclick=()=>setSheet(false);
function syncMobileState(){if(!mobileQuery.matches){sheet.classList.remove('open');$('sheetScrim').classList.remove('open');
    sheet.setAttribute('aria-hidden','false');}
  else if(!sheet.classList.contains('open'))sheet.setAttribute('aria-hidden','true');}
mobileQuery.addEventListener?.('change',syncMobileState);syncMobileState();
const scene=MebelScene($('view3d'));scene.setPayload(PAYLOAD.viewer);scene.setView('axon');
$('view3d').addEventListener('pointerdown',event=>{if(event.shiftKey){event.preventDefault();event.stopPropagation();}},true);
const layerState={holes:true,hardware:true,textures:true,dimensions:true,xray:false};
function syncLayers(){scene.setHoles(layerState.holes);scene.setHw(layerState.hardware);
  scene.setTextures(layerState.textures);scene.setDims(layerState.dimensions);scene.setXray(layerState.xray);}
document.querySelectorAll('#layers button').forEach(button=>button.onclick=()=>{
  const key=button.dataset.layer;layerState[key]=!layerState[key];button.setAttribute('aria-pressed',String(layerState[key]));syncLayers();});
syncLayers();
$('openAll').onclick=()=>scene.openAll();$('closeAll').onclick=()=>scene.closeAll();
$('explode').oninput=event=>{scene.setExplode(event.target.value/100);$('explodeValue').textContent=event.target.value+'%';};
document.querySelectorAll('#views button').forEach(button=>button.onclick=()=>{scene.setView(button.dataset.view);
  document.querySelectorAll('#views button').forEach(item=>item.classList.toggle('on',item===button));});
function mm(value){const number=Math.abs(Number(value)||0);return `${Math.round(number*10)/10} мм`;}
function formatEdges(value){const raw=String(value||'').trim();if(!raw||raw==='—')return '—';
  const labels={top:'Верхняя',bottom:'Нижняя',left:'Левая',right:'Правая',front:'Передняя',back:'Задняя'};
  const parts=raw.split(/[,;]/).map(item=>item.trim()).filter(Boolean);if(!parts.length)return raw;
  const formatted=parts.map(item=>{const match=item.match(/^([a-z_]+)\s*:\s*(-?\d+(?:\.\d+)?)$/i);
    if(!match)return item;const side=labels[match[1].toLowerCase()]||match[1];
    return `${side} — ${String(Number(match[2])).replace('.',',')} мм`;});return formatted.join('; ');}
scene.onSelect=selection=>{const panel=selection&&selection.panel;$('partEmpty').hidden=!!panel;$('partPanel').hidden=!panel;
  if(!panel)return;$('partName').textContent=panel.name||'Деталь';$('partType').textContent=panel.type||'—';
  $('partX').textContent=mm(panel.x2-panel.x1);$('partY').textContent=mm(panel.y2-panel.y1);
  $('partZ').textContent=mm(panel.z2-panel.z1);$('partThickness').textContent=mm(panel.thickness);
  $('partMaterial').textContent=panel.material||'Не указан';$('partEdges').textContent=formatEdges(panel.edges);
  $('partHoles').textContent=String((PAYLOAD.viewer.holes||[]).filter(hole=>hole.panel===panel.name).length);
  $('mobileDetails').textContent='Параметры детали';};
addEventListener('resize',()=>scene.resize());
const allowedTypes=new Set(['image/jpeg','image/png','image/webp','application/pdf']);
function fileSize(bytes){return bytes<1048576?`${Math.max(1,Math.round(bytes/1024))} КБ`:`${(bytes/1048576).toFixed(1).replace('.',',')} МБ`;}
function renderAttachmentList(){const list=$('attachmentList');list.replaceChildren();
  if(selectedFiles.length){selectedFiles.forEach((entry,index)=>{const row=document.createElement('div');row.className='attachment-item';
    const name=document.createElement('span');name.textContent=`${entry.file.name} · ${fileSize(entry.file.size)}`;row.append(name);
    const remove=document.createElement('button');remove.type='button';remove.setAttribute('aria-label','Убрать '+entry.file.name);
    remove.textContent='×';remove.onclick=()=>{selectedFiles.splice(index,1);renderAttachmentList();};row.append(remove);list.append(row);});return;}
  const attached=REVIEW.decision&&Array.isArray(REVIEW.decision.attachments)?REVIEW.decision.attachments:[];
  attached.forEach(item=>{const row=document.createElement('div');row.className='attachment-item';
    const link=document.createElement('a');link.href=location.pathname+'/attachments/'+encodeURIComponent(item.id);
    link.textContent=`${item.name} · ${fileSize(item.bytes)}`;link.target='_blank';link.rel='noopener';row.append(link);list.append(row);});}
$('pickFiles').onclick=()=>$('reviewFiles').click();$('reviewFiles').onchange=event=>{
  const added=Array.from(event.target.files||[]),next=[...selectedFiles];
  for(const file of added){if(next.length>=5){$('decisionResult').classList.add('on');$('decisionResult').textContent='К одному решению можно приложить до 5 файлов';break;}
    if(!allowedTypes.has(file.type)||file.size<=0||file.size>10*1024*1024){$('decisionResult').classList.add('on');
      $('decisionResult').textContent=`${file.name}: нужен JPG, PNG, WEBP или PDF размером до 10 МБ`;continue;}
    if(next.reduce((sum,item)=>sum+item.file.size,0)+file.size>25*1024*1024){$('decisionResult').classList.add('on');
      $('decisionResult').textContent='Общий размер вложений не должен превышать 25 МБ';continue;}next.push({file,attachmentId:''});}
  selectedFiles=next;event.target.value='';renderAttachmentList();};
async function decide(status){const name=$('reviewerName').value.trim(),comment=$('reviewComment').value.trim();
  if(!name){$('decisionResult').classList.add('on');$('decisionResult').textContent='Укажите ваше имя';return;}
  if(status==='changes_requested'&&!comment){$('decisionResult').classList.add('on');$('decisionResult').textContent='Опишите, что нужно изменить';return;}
  const buttons=[$('approve'),$('requestChanges'),$('pickFiles')];buttons.forEach(button=>button.disabled=true);
  $('decisionResult').classList.add('on');$('decisionResult').textContent=selectedFiles.length?'Загружаем вложения…':'Сохраняем решение…';
  try{for(let index=0;index<selectedFiles.length;index++){const entry=selectedFiles[index];if(entry.attachmentId)continue;
      $('decisionResult').textContent=`Загружаем файл ${index+1} из ${selectedFiles.length}…`;
      const upload=await fetch(location.pathname+'/attachments',{method:'POST',headers:{'Content-Type':entry.file.type,
        'X-Akeda-Filename':encodeURIComponent(entry.file.name),'X-Akeda-Revision':REVIEW.revision},body:entry.file});
      const uploaded=await upload.json();if(!upload.ok)throw new Error(uploaded.error||'Файл не загружен');entry.attachmentId=uploaded.attachment.id;}
    $('decisionResult').textContent='Сохраняем решение…';
    const response=await fetch(location.pathname+'/decision',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({decision:status,reviewer_name:name,comment,revision:REVIEW.revision,
      attachment_ids:selectedFiles.map(item=>item.attachmentId)})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'Ответ не сохранён');
    REVIEW.status=result.status;REVIEW.decision=result.decision;selectedFiles=[];renderReviewStatus();
  }catch(error){$('decisionResult').classList.add('on');$('decisionResult').textContent=error.message;}
  finally{buttons.forEach(button=>button.disabled=false);}}
$('approve').onclick=()=>decide('approved');$('requestChanges').onclick=()=>decide('changes_requested');
__PRESENTATION_JS__
</script></body></html>"""


__all__ = ["build_review_page"]
