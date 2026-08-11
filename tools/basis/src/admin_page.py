"""Inline login and administration pages for the Akeda identity vertical slice.

The pages intentionally have no runtime UI dependencies.  They use the same
local brand assets, visual tokens and compact instrument language as Studio.
Authorization remains a server concern; the browser only reflects permissions
returned by the authenticated API.
"""

from __future__ import annotations


LOGIN_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Akeda Studio — вход</title>
  <style>
    :root{--ink:#1a1d21;--mut:#687180;--line:#dfe3e8;--bg:#f4f6f8;--surface:#fff;
      --accent:#3b82f6;--bad:#e5484d;--focus:#245fbf}
    *{box-sizing:border-box}
    html,body{margin:0;min-height:100%;font-family:"Segoe UI",Arial,sans-serif;
      background:var(--bg);color:var(--ink);font-size:13px}
    body{min-height:100vh;display:grid;grid-template-columns:272px minmax(0,1fr)}
    .brand-rail{display:flex;flex-direction:column;min-height:100vh;padding:22px 20px;
      border-right:1px solid var(--line);background:var(--surface)}
    .brand-lockup{display:flex;align-items:center;gap:9px;min-width:0}
    .brand-wordmark{display:block;width:164px;max-width:calc(100% - 68px);height:auto;object-fit:contain}
    .brand-mark{display:block;width:58px;height:auto;object-fit:contain}
    .realm{margin-top:17px;padding-top:14px;border-top:1px solid var(--line)}
    .realm-label{display:block;margin-bottom:3px;color:var(--mut);font-size:10.5px;line-height:14px}
    .realm-name{font-size:12.5px;font-weight:600}
    .boundary{margin-top:auto;padding:12px 0 0 11px;border-top:1px solid var(--line);
      border-left:2px solid var(--accent);color:#4f5967;font-size:11px;line-height:16px}
    main{display:grid;place-items:center;min-width:0;padding:32px}
    .login-sheet{width:min(392px,100%);background:var(--surface);border:1px solid var(--line)}
    .sheet-head{padding:22px 24px 18px;border-bottom:1px solid var(--line)}
    h1{margin:0 0 6px;font-size:18px;line-height:24px;font-weight:650}
    .lead{margin:0;color:var(--mut);font-size:12px;line-height:18px}
    form{display:grid;gap:13px;padding:21px 24px 24px}
    label{display:grid;gap:5px;color:#4f5967;font-size:11.5px;line-height:16px}
    input,select{width:100%;height:34px;padding:6px 9px;border:1px solid #cfd5dc;border-radius:4px;
      background:#fff;color:var(--ink);font:13px/1.3 "Segoe UI",Arial,sans-serif}
    input:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid var(--focus);outline-offset:1px}
    button{height:34px;padding:0 12px;border:1px solid var(--line);border-radius:5px;
      background:#fff;color:#303743;font:600 12.5px/1 "Segoe UI",Arial,sans-serif;cursor:pointer}
    button:hover{background:#eef1f4}
    button.primary{border-color:var(--accent);background:var(--accent);color:#fff}
    button.primary:hover{background:#2f73df}
    button:disabled{opacity:.5;cursor:wait}
    .form-error{min-height:18px;margin:0;color:#a02f34;font-size:11.5px;line-height:17px}
    .session-note{padding:11px 24px;border-top:1px solid var(--line);color:#737c89;
      font-size:10.5px;line-height:15px}
    .status-mark{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;
      background:#7f8996;vertical-align:1px}
    .is-busy .status-mark{background:var(--accent)}
    @media(max-width:720px){
      body{grid-template-columns:1fr}
      .brand-rail{min-height:auto;padding:14px 16px;border-right:0;border-bottom:1px solid var(--line)}
      .brand-lockup{justify-content:flex-start}.brand-wordmark{width:145px}.brand-mark{width:51px}
      .realm{margin-top:11px;padding-top:10px}.boundary{display:none}
      main{padding:22px 14px}.login-sheet{width:min(420px,100%)}
    }
    @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
  </style>
</head>
<body>
  <aside class="brand-rail" aria-label="Продукт">
    <div class="brand-lockup">
      <img class="brand-wordmark" src="/assets/studio/akeda-studio-wordmark.png"
        width="520" height="84" decoding="async" alt="Akeda Studio">
      <img class="brand-mark" src="/assets/studio/akeda-studio-mark.png"
        width="216" height="98" decoding="async" alt="" aria-hidden="true">
    </div>
    <div class="realm">
      <span class="realm-label">Контур доступа</span>
      <span class="realm-name">Управление компаниями и сотрудниками</span>
    </div>
    <div class="boundary">Личная учётная запись определяет компанию, роль и доступные действия.</div>
  </aside>
  <main>
    <section class="login-sheet" aria-labelledby="loginTitle">
      <header class="sheet-head">
        <h1 id="loginTitle">Войти в Akeda</h1>
        <p class="lead">Используйте свою рабочую учётную запись.</p>
      </header>
      <form id="loginForm" novalidate>
        <label for="loginEmail">Рабочая почта
          <input id="loginEmail" name="email" type="email" autocomplete="username"
            inputmode="email" required autofocus>
        </label>
        <label for="loginPassword">Пароль
          <input id="loginPassword" name="password" type="password"
            autocomplete="current-password" required>
        </label>
        <label id="loginOrganizationField" for="loginOrganization" hidden>Компания
          <select id="loginOrganization" name="organization_id"></select>
        </label>
        <p id="loginError" class="form-error" role="alert"></p>
        <button id="loginSubmit" class="primary" type="submit">Войти</button>
      </form>
      <div id="loginStatus" class="session-note" role="status" aria-live="polite">
        <span class="status-mark" aria-hidden="true"></span><span>Проверяю текущую сессию…</span>
      </div>
    </section>
  </main>
  <script>
  (()=>{
    const $=id=>document.getElementById(id);
    const form=$('loginForm'),submit=$('loginSubmit'),error=$('loginError'),status=$('loginStatus'),
      organizationField=$('loginOrganizationField'),organization=$('loginOrganization');
    function setBusy(busy,text=''){
      form.classList.toggle('is-busy',busy);submit.disabled=busy;
      submit.textContent=busy?'Вхожу…':'Войти';
      if(text)status.querySelector('span:last-child').textContent=text;
      status.classList.toggle('is-busy',busy);
    }
    async function readJson(response){
      const text=await response.text();
      if(!text)return {};
      try{return JSON.parse(text);}catch(_error){return {error:text};}
    }
    async function currentSession(){
      try{
        const response=await fetch('/api/auth/me',{credentials:'same-origin',headers:{Accept:'application/json'}});
        if(!response.ok){status.querySelector('span:last-child').textContent='Введите почту и пароль.';return;}
        const data=await readJson(response),user=data.user||(data.email?data:null);
        if(data.authenticated!==false&&user){location.replace('/admin');return;}
      }catch(_error){}
      status.querySelector('span:last-child').textContent='Введите почту и пароль.';
    }
    form.addEventListener('submit',async event=>{
      event.preventDefault();error.textContent='';
      const email=$('loginEmail').value.trim(),password=$('loginPassword').value;
      if(!email||!password){error.textContent='Введите рабочую почту и пароль.';return;}
      setBusy(true,'Проверяю учётные данные…');
      try{
        const payload={email,password};
        if(!organizationField.hidden&&organization.value)payload.organization_id=organization.value;
        const response=await fetch('/api/auth/login',{method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json',Accept:'application/json'},
          body:JSON.stringify(payload)});
        const data=await readJson(response);
        if(response.status===409&&data.code==='organization_selection_required'){
          const options=(data.details&&data.details.organizations)||[];
          organization.replaceChildren(...options.map(item=>{const option=document.createElement('option');
            option.value=String(item.id||'');option.textContent=String(item.name||item.id||'Компания');return option;}));
          organizationField.hidden=false;setBusy(false,'Выберите рабочую компанию и повторите вход.');
          error.textContent='У этой учётной записи несколько компаний.';organization.focus();return;
        }
        if(!response.ok||data.error)throw new Error(data.error||data.message||'Не удалось войти.');
        status.querySelector('span:last-child').textContent='Сессия открыта. Перехожу в управление…';
        location.replace(data.redirect||'/admin');
      }catch(reason){
        error.textContent=reason&&reason.message?reason.message:'Сервис входа недоступен.';
        setBusy(false,'Вход не выполнен. Проверьте данные и повторите.');
        $('loginPassword').select();
      }
    });
    currentSession();
  })();
  </script>
</body>
</html>"""


ACTIVATION_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Akeda Studio — активация доступа</title>
  <style>
    :root{--ink:#1a1d21;--mut:#687180;--line:#dfe3e8;--bg:#f4f6f8;--surface:#fff;
      --accent:#3b82f6;--bad:#e5484d;--ok:#2fa84f;--focus:#245fbf}
    *{box-sizing:border-box}
    html,body{margin:0;min-height:100%;font-family:"Segoe UI",Arial,sans-serif;
      background:var(--bg);color:var(--ink);font-size:13px}
    body{min-height:100vh;display:grid;grid-template-columns:272px minmax(0,1fr)}
    .brand-rail{display:flex;flex-direction:column;min-height:100vh;padding:22px 20px;
      border-right:1px solid var(--line);background:var(--surface)}
    .brand-lockup{display:flex;align-items:center;gap:9px;min-width:0}
    .brand-wordmark{display:block;width:164px;max-width:calc(100% - 68px);height:auto;object-fit:contain}
    .brand-mark{display:block;width:58px;height:auto;object-fit:contain}
    .realm{margin-top:17px;padding-top:14px;border-top:1px solid var(--line)}
    .realm-label{display:block;margin-bottom:3px;color:var(--mut);font-size:10.5px;line-height:14px}
    .realm-name{font-size:12.5px;font-weight:600}
    .boundary{margin-top:auto;padding:12px 0 0 11px;border-top:1px solid var(--line);
      border-left:2px solid var(--accent);color:#4f5967;font-size:11px;line-height:16px}
    main{display:grid;place-items:center;min-width:0;padding:32px}
    .activation-sheet{width:min(430px,100%);background:var(--surface);border:1px solid var(--line)}
    .sheet-head{padding:22px 24px 18px;border-bottom:1px solid var(--line)}
    h1{margin:0 0 6px;font-size:18px;line-height:24px;font-weight:650}
    .lead{margin:0;color:var(--mut);font-size:12px;line-height:18px}
    form{display:grid;gap:13px;padding:21px 24px 24px}
    label{display:grid;gap:5px;color:#4f5967;font-size:11.5px;line-height:16px}
    input{width:100%;height:34px;padding:6px 9px;border:1px solid #cfd5dc;border-radius:4px;
      background:#fff;color:var(--ink);font:13px/1.3 "Segoe UI",Arial,sans-serif}
    input:focus-visible,button:focus-visible{outline:2px solid var(--focus);outline-offset:1px}
    button{height:34px;padding:0 12px;border:1px solid var(--line);border-radius:5px;
      background:#fff;color:#303743;font:600 12.5px/1 "Segoe UI",Arial,sans-serif;cursor:pointer}
    button.primary{border-color:var(--accent);background:var(--accent);color:#fff}
    button.primary:hover{background:#2f73df}button:disabled{opacity:.5;cursor:not-allowed}
    .password-note{margin:-4px 0 0;color:#737c89;font-size:10.5px;line-height:15px}
    .form-error{min-height:18px;margin:0;color:#a02f34;font-size:11.5px;line-height:17px}
    .sheet-status{padding:11px 24px;border-top:1px solid var(--line);color:#737c89;
      font-size:10.5px;line-height:15px}
    .status-mark{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;
      background:#7f8996;vertical-align:1px}.is-busy .status-mark{background:var(--accent)}
    .is-success .status-mark{background:var(--ok)}
    @media(max-width:720px){body{grid-template-columns:1fr}.brand-rail{min-height:auto;padding:14px 16px;
      border-right:0;border-bottom:1px solid var(--line)}.brand-wordmark{width:145px}.brand-mark{width:51px}
      .realm{margin-top:11px;padding-top:10px}.boundary{display:none}main{padding:22px 14px}}
    @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
  </style>
</head>
<body>
  <aside class="brand-rail" aria-label="Продукт">
    <div class="brand-lockup">
      <img class="brand-wordmark" src="/assets/studio/akeda-studio-wordmark.png"
        width="520" height="84" decoding="async" alt="Akeda Studio">
      <img class="brand-mark" src="/assets/studio/akeda-studio-mark.png"
        width="216" height="98" decoding="async" alt="" aria-hidden="true">
    </div>
    <div class="realm"><span class="realm-label">Одноразовый доступ</span>
      <span class="realm-name">Активация рабочей учётной записи</span></div>
    <div class="boundary">Ссылка действует один раз. После активации вход выполняется только с вашим паролем.</div>
  </aside>
  <main>
    <section class="activation-sheet" aria-labelledby="activationPageTitle">
      <header class="sheet-head"><h1 id="activationPageTitle">Активировать доступ</h1>
        <p class="lead">Укажите своё имя и создайте пароль для Akeda.</p></header>
      <form id="activationForm" novalidate>
        <label for="activationName">Имя
          <input id="activationName" name="display_name" type="text" autocomplete="name" maxlength="120" required autofocus>
        </label>
        <label for="activationPassword">Новый пароль
          <input id="activationPassword" name="password" type="password" autocomplete="new-password" minlength="15" required>
        </label>
        <p class="password-note">Не менее 15 символов. Используйте отдельный пароль, которого нет в других сервисах.</p>
        <label for="activationConfirm">Повторите пароль
          <input id="activationConfirm" type="password" autocomplete="new-password" minlength="15" required>
        </label>
        <p id="activationError" class="form-error" role="alert"></p>
        <button id="activationSubmit" class="primary" type="submit">Активировать учётную запись</button>
      </form>
      <div id="activationStatus" class="sheet-status" role="status" aria-live="polite">
        <span class="status-mark" aria-hidden="true"></span><span>Проверяю одноразовую ссылку…</span>
      </div>
    </section>
  </main>
  <script>
  (()=>{
    const $=id=>document.getElementById(id),form=$('activationForm'),submit=$('activationSubmit'),
      error=$('activationError'),status=$('activationStatus');
    const token=new URLSearchParams(location.search).get('token')||'';
    if(token)history.replaceState(null,'','/activate');
    function statusText(text,mode=''){status.querySelector('span:last-child').textContent=text;
      status.classList.toggle('is-busy',mode==='busy');status.classList.toggle('is-success',mode==='success');}
    function setBusy(busy){[...form.elements].forEach(element=>element.disabled=busy);
      submit.textContent=busy?'Активирую…':'Активировать учётную запись';}
    async function readJson(response){const text=await response.text();if(!text)return {};
      try{return JSON.parse(text);}catch(_error){return {error:text};}}
    if(!token){error.textContent='В ссылке нет токена активации.';setBusy(true);
      submit.textContent='Ссылка недействительна';statusText('Запросите новое приглашение у администратора.');}
    else statusText('Ссылка получена. Задайте личный пароль.');
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!token)return;error.textContent='';
      const displayName=$('activationName').value.trim(),password=$('activationPassword').value,
        confirmation=$('activationConfirm').value;
      if(!displayName){error.textContent='Укажите имя.';return;}
      if(password.length<15){error.textContent='Пароль должен содержать не менее 15 символов.';return;}
      if(password!==confirmation){error.textContent='Пароли не совпадают.';$('activationConfirm').select();return;}
      setBusy(true);statusText('Активирую учётную запись…','busy');
      try{
        const response=await fetch('/api/auth/activate',{method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json',Accept:'application/json'},
          body:JSON.stringify({token,display_name:displayName,password})});
        const data=await readJson(response);
        if(!response.ok||data.error)throw new Error(data.error||data.message||'Не удалось активировать учётную запись.');
        statusText('Учётная запись активирована. Перехожу в управление…','success');
        location.replace(data.redirect||'/admin');
      }catch(reason){error.textContent=reason&&reason.message?reason.message:'Сервис активации недоступен.';
        setBusy(false);statusText('Активация не выполнена. Ссылка могла истечь или уже использоваться.');}
    });
  })();
  </script>
</body>
</html>"""


ADMIN_PAGE = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Akeda Studio — управление</title>
  <style>
    :root{--ink:#1a1d21;--mut:#687180;--line:#dfe3e8;--line-soft:#edf0f3;
      --bg:#f4f6f8;--surface:#fff;--accent:#3b82f6;--accent-ink:#245fbf;
      --ok:#2fa84f;--warn:#c78a2b;--bad:#e5484d;--rail:244px;--inspector:344px}
    *{box-sizing:border-box}
    html,body{margin:0;height:100%;font-family:"Segoe UI",Arial,sans-serif;
      background:var(--bg);color:var(--ink);font-size:13px;overflow:hidden}
    button,input,select,textarea{font:inherit}
    button,.button-link{min-height:30px;padding:5px 9px;border:1px solid var(--line);border-radius:5px;
      background:#fff;color:#303743;cursor:pointer}
    button:hover,.button-link:hover{background:#eef1f4}
    button:disabled{opacity:.46;cursor:not-allowed}
    button.primary{border-color:var(--accent);background:var(--accent);color:#fff;font-weight:600}
    button.primary:hover{background:#2f73df}
    button.danger{border-color:#e7b8ba;background:#fff5f4;color:#9f2d2f}
    input,select,textarea{width:100%;min-width:0;border:1px solid #cfd5dc;border-radius:4px;
      background:#fff;color:var(--ink)}
    input,select{height:32px;padding:5px 8px}
    textarea{min-height:86px;padding:7px 8px;resize:vertical;line-height:1.45}
    button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,
      [tabindex]:focus-visible{outline:2px solid var(--accent-ink);outline-offset:1px}
    [hidden]{display:none!important}
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
      clip:rect(0,0,0,0);white-space:nowrap;border:0}
    .ui-icon{width:16px;height:16px;flex:0 0 16px;fill:none;stroke:currentColor;
      stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
    #supportBanner{position:fixed;inset:0 0 auto;z-index:20;display:flex;align-items:center;
      gap:9px;min-height:38px;padding:6px 12px;border-bottom:1px solid #e7c98f;
      background:#fff8eb;color:#68410d}
    #supportBanner[hidden]{display:none}
    #supportBanner .support-mark{width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:var(--warn)}
    #supportBanner strong{font-size:12px;white-space:nowrap}
    #supportBanner .support-copy{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #supportBanner .support-actions{display:flex;gap:5px;margin-left:auto}
    #supportBanner button{min-height:26px;padding:3px 7px;border-color:#dfc188;background:#fffaf1;color:#68410d;font-size:11px}
    #shell{display:grid;grid-template-columns:var(--rail) minmax(0,1fr);height:100%}
    body.has-support #shell{padding-top:38px}
    #shell.has-inspector{grid-template-columns:var(--rail) minmax(0,1fr) var(--inspector)}
    #scopeRail{display:flex;flex-direction:column;min-width:0;min-height:0;padding:14px 10px 10px;
      border-right:1px solid var(--line);background:var(--surface)}
    .brand-lockup{display:flex;align-items:center;gap:8px;min-width:0;padding:0 4px}
    .brand-wordmark{display:block;width:145px;max-width:calc(100% - 61px);height:auto;object-fit:contain}
    .brand-mark{display:block;width:53px;height:auto;object-fit:contain}
    .admin-label{margin:8px 4px 0;color:var(--mut);font-size:10px;line-height:14px}
    #scopeBox{margin:13px 2px 10px;padding:10px 10px 10px 12px;border-top:1px solid var(--line);
      border-left:2px solid var(--accent)}
    #scopeBox.is-support{border-left-color:var(--warn);background:#fffaf1}
    .scope-caption{display:block;margin-bottom:2px;color:#7a8390;font-size:10px;line-height:13px}
    #scopeName{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
      font-size:12px;line-height:17px;font-weight:650}
    #primaryNav{display:grid;gap:2px;margin-top:2px}
    #primaryNav button{display:flex;align-items:center;gap:8px;width:100%;height:34px;padding:0 9px;
      border-color:transparent;border-radius:3px;background:transparent;color:#4f5968;text-align:left}
    #primaryNav button:hover{background:#f0f3f6;color:#252b34}
    #primaryNav button[aria-current="page"]{background:#edf4ff;color:var(--accent-ink);
      box-shadow:inset 2px 0 0 var(--accent);font-weight:600}
    .rail-footer{margin-top:auto;padding:10px 4px 0;border-top:1px solid var(--line)}
    #currentUser{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
      color:#3f4855;font-size:11.5px;font-weight:600}
    #currentUserRole{display:block;margin-top:2px;color:var(--mut);font-size:10.5px}
    #logoutButton{display:flex;align-items:center;gap:6px;margin-top:8px;padding:4px 6px;border-color:transparent;
      background:transparent;color:#596273;font-size:11px}
    #workspace{min-width:0;min-height:0;overflow:auto;background:var(--surface)}
    .workspace-head{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;
      min-height:55px;padding:9px 16px;border-bottom:1px solid var(--line);background:var(--surface)}
    .workspace-title{min-width:0;flex:1}
    .workspace-title h1{margin:0;font-size:16px;line-height:21px;font-weight:650;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .workspace-title p{margin:2px 0 0;color:var(--mut);font-size:10.5px;line-height:14px}
    .head-actions{display:flex;align-items:center;gap:6px}
    .button-link{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;
      font-size:11.5px;line-height:18px;white-space:nowrap}
    .content{min-width:0;padding:0 16px 22px}
    .toolbar{display:flex;align-items:center;gap:7px;min-height:52px;border-bottom:1px solid var(--line-soft)}
    .search-field{position:relative;width:min(380px,55%)}
    .search-field .ui-icon{position:absolute;left:9px;top:8px;color:#7a8390;pointer-events:none}
    .search-field input{padding-left:32px}
    .toolbar select{width:170px}
    .result-count{margin-left:auto;color:var(--mut);font-size:10.5px;font-variant-numeric:tabular-nums}
    .table-wrap{min-width:0;overflow:auto;border-bottom:1px solid var(--line)}
    table{width:100%;min-width:780px;border-collapse:collapse;table-layout:fixed}
    th{position:sticky;top:0;z-index:2;height:34px;padding:6px 8px;border-bottom:1px solid var(--line);
      background:#f8f9fb;color:#5f6977;font-size:10.5px;line-height:14px;font-weight:600;text-align:left}
    td{height:45px;padding:6px 8px;border-bottom:1px solid var(--line-soft);vertical-align:middle;
      font-size:11.5px;line-height:15px}
    tbody tr:hover{background:#f8fafc}
    tbody tr.is-selected{background:#edf4ff;box-shadow:inset 2px 0 0 var(--accent)}
    .entity-button{display:block;width:100%;min-height:0;padding:0;border:0;background:transparent;
      color:#27313d;text-align:left;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .entity-button:hover{background:transparent;color:var(--accent-ink)}
    .entity-sub{display:block;margin-top:1px;color:#7a8390;font:10px/13px Consolas,monospace;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .status{display:inline-flex;align-items:center;gap:6px;min-width:0;color:#4f5967;white-space:nowrap}
    .status::before{content:"";width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:#8a929e}
    .status.active::before,.status.approved::before{background:var(--ok)}
    .status.pending::before{background:var(--warn)}
    .status.disabled::before,.status.suspended::before,.status.rejected::before{background:var(--bad)}
    .status.expired::before,.status.ended::before,.status.revoked::before{background:#9aa3af}
    .mono{font-family:Consolas,monospace;font-variant-numeric:tabular-nums}
    .muted{color:var(--mut)}
    .row-action{min-height:27px;padding:3px 7px;font-size:10.5px}
    .empty-state{display:grid;place-items:center;min-height:310px;text-align:center}
    .empty-state .empty-rule{width:64px;height:1px;margin:0 auto 15px;background:#b9c2cd}
    .empty-state h2{margin:0 0 5px;font-size:14px}.empty-state p{max-width:390px;margin:0 0 13px;
      color:var(--mut);font-size:11.5px;line-height:17px}
    .notice{margin:12px 0;padding:9px 11px;border-left:2px solid #a9b1bc;background:#f7f8fa;
      color:#4f5967;font-size:11.5px;line-height:17px}
    .notice.warning{border-left-color:var(--warn);background:#fffaf1;color:#765018}
    .notice.error{border-left-color:var(--bad);background:#fff5f4;color:#922d31}
    .notice.success{border-left-color:var(--ok);background:#f2fbf4;color:#287d40}
    .company-head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 18px;padding:16px 0 11px}
    .back-button{display:inline-flex;align-items:center;gap:5px;grid-column:1/-1;width:max-content;
      min-height:27px;padding:2px 4px;border-color:transparent;background:transparent;color:#596273;font-size:11px}
    .company-identity h2{margin:0 0 4px;font-size:17px;line-height:22px}
    .company-meta{display:flex;align-items:center;gap:10px;color:var(--mut);font-size:10.5px}
    .company-actions{display:flex;align-items:start;gap:6px}
    .tab-rail{position:sticky;top:55px;z-index:4;display:flex;align-items:end;gap:1px;
      min-height:38px;margin:0 -16px;padding:0 16px;border-bottom:1px solid var(--line);background:var(--surface)}
    .tab-rail button{height:37px;padding:0 11px;border:0;border-bottom:2px solid transparent;
      border-radius:0;background:transparent;color:#5d6775;font-size:11.5px}
    .tab-rail button:hover{background:#f5f7f9;color:#303947}
    .tab-rail button[aria-selected="true"]{border-bottom-color:var(--accent);color:var(--accent-ink);font-weight:600}
    .panel{padding:14px 0 4px}
    .section{padding:0 0 15px;margin:0 0 14px;border-bottom:1px solid var(--line-soft)}
    .section:last-child{border-bottom:0}.section-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}
    .section-head h3{flex:1;margin:0;font-size:12px;line-height:18px;font-weight:650}
    .definition-list{margin:0;max-width:760px}
    .definition-list>div{display:grid;grid-template-columns:190px minmax(0,1fr);gap:12px;
      padding:7px 0;border-top:1px solid var(--line-soft)}
    .definition-list>div:last-child{border-bottom:1px solid var(--line-soft)}
    .definition-list dt{color:var(--mut);font-size:10.5px}.definition-list dd{margin:0;font-size:11.5px}
    .migration-sheet{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;
      padding:11px 12px;border:1px solid #e7d4ad;background:#fffaf1}
    .migration-sheet strong{display:block;margin-bottom:3px;color:#694718;font-size:11.5px}
    .migration-sheet p{margin:0;color:#7a5721;font-size:10.5px;line-height:15px}
    .migration-sheet button{white-space:nowrap}
    .owner-workspace{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;
      gap:14px;max-width:760px;padding:12px 0;border-top:1px solid var(--line-soft);
      border-bottom:1px solid var(--line-soft)}
    .owner-workspace strong{display:block;margin-bottom:3px;font-size:11.5px}
    .owner-workspace p{margin:0;color:var(--mut);font-size:10.5px;line-height:15px}
    .field{display:grid;gap:4px;min-width:0;color:#5f6977;font-size:10.5px;line-height:14px}
    .fixed-value{display:flex;align-items:center;height:32px;padding:0 8px;border:1px solid var(--line);
      border-radius:4px;background:#f7f8fa;color:#3f4855;font-variant-numeric:tabular-nums}
    .audit-list{border-top:1px solid var(--line)}
    .audit-row{display:grid;grid-template-columns:138px minmax(120px,190px) minmax(0,1fr) 90px;
      gap:10px;padding:8px;border-bottom:1px solid var(--line-soft);font-size:11px;line-height:15px}
    .audit-row:hover{background:#f8fafc}.audit-action{font-weight:600}.audit-result{text-align:right}
    #inspector{min-width:0;min-height:0;overflow:auto;border-left:1px solid var(--line);background:var(--surface)}
    .inspector-head{position:sticky;top:0;z-index:3;display:flex;align-items:center;gap:8px;
      min-height:50px;padding:8px 8px 8px 13px;border-bottom:1px solid var(--line);background:var(--surface)}
    .inspector-head h2{min-width:0;flex:1;margin:0;font-size:13px;line-height:18px;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #inspectorClose{display:grid;place-items:center;width:31px;min-height:31px;padding:0;border-color:transparent}
    #inspectorBody{padding:13px}
    .inspector-form{display:grid;gap:11px}.inspector-form .form-actions{display:flex;justify-content:flex-end;
      gap:6px;padding-top:11px;border-top:1px solid var(--line)}
    .form-message{min-height:17px;margin:0;color:#a02f34;font-size:10.5px;line-height:15px}
    .inspector-summary{margin-bottom:13px;padding-bottom:11px;border-bottom:1px solid var(--line)}
    .inspector-summary h3{margin:0 0 3px;font-size:14px}.inspector-summary p{margin:0;color:var(--mut);font-size:10.5px}
    .inspector-links{display:grid;gap:4px}.inspector-links button{display:flex;justify-content:space-between;
      align-items:center;width:100%;border-color:transparent;border-bottom:1px solid var(--line-soft);
      border-radius:0;background:transparent;text-align:left}
    .inspector-primary{display:flex;align-items:center;justify-content:center;width:100%;margin:0 0 10px}
    dialog{width:min(520px,calc(100vw - 28px));padding:0;border:1px solid #bfc7d1;border-radius:6px;
      background:#fff;color:var(--ink)}
    dialog::backdrop{background:rgba(26,29,33,.28)}
    .dialog-head{display:flex;align-items:center;gap:8px;min-height:48px;padding:9px 12px;
      border-bottom:1px solid var(--line)}
    .dialog-head h2{flex:1;margin:0;font-size:14px}.dialog-body{padding:14px}
    .dialog-body p{margin:0 0 10px;color:#4f5967;font-size:11.5px;line-height:17px}
    .credential-list{display:grid;gap:8px;margin-top:12px}
    .credential-row{display:grid;grid-template-columns:80px minmax(0,1fr);align-items:center;gap:8px}
    .credential-row span{color:var(--mut);font-size:10.5px}
    .credential-row input{font:600 12px/1 Consolas,monospace;letter-spacing:.02em}
    .credential-copy{display:flex;justify-content:flex-end;margin-top:10px}
    .dialog-actions{display:flex;justify-content:flex-end;gap:6px;padding:10px 12px;border-top:1px solid var(--line)}
    #toast{position:fixed;left:50%;bottom:22px;z-index:40;max-width:min(560px,86vw);padding:8px 13px;
      transform:translateX(-50%);border-radius:6px;background:#1a1d21;color:#fff;font-size:11.5px;
      opacity:0;pointer-events:none;transition:opacity .18s ease}
    #toast.on{opacity:1}#toast.bad{background:#9f2d2f}
    .loading-line{height:2px;background:var(--accent);transform-origin:left;animation:loading 1.2s ease-in-out infinite}
    @keyframes loading{0%{transform:scaleX(.08)}50%{transform:scaleX(.72)}100%{transform:scaleX(.08)}}
    @media(max-width:1120px){:root{--rail:214px;--inspector:320px}.brand-wordmark{width:128px}.brand-mark{width:48px}}
    @media(max-width:880px){
      :root{--rail:58px}.brand-wordmark,.admin-label,#scopeBox span,#primaryNav button span,
        .rail-footer>span{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0,0,0,0)}
      .brand-lockup{justify-content:center}.brand-mark{width:38px;max-width:38px}
      #scopeBox{height:31px;margin:12px 7px;padding:0;border-top:0;background:#edf4ff}
      #primaryNav button{justify-content:center;padding:0}.rail-footer{display:grid;justify-items:center}
      #logoutButton{width:34px;justify-content:center;padding:0}
      .content{padding-inline:11px}.workspace-head{padding-inline:11px}.tab-rail{margin-inline:-11px;padding-inline:11px}
    }
    @media(max-width:720px){
      #shell.has-inspector{grid-template-columns:var(--rail) minmax(0,1fr)}
      #inspector{position:fixed;right:0;top:0;bottom:0;z-index:18;width:min(360px,calc(100vw - var(--rail)))}
      body.has-support #inspector{top:38px}
      .toolbar{flex-wrap:wrap;padding-block:8px}.search-field{width:100%}
      .toolbar select{width:calc(50% - 4px)}.result-count{margin-left:0}.head-actions .wide-label{display:none}
      .audit-row{grid-template-columns:120px minmax(0,1fr);gap:4px}.audit-detail,.audit-result{grid-column:2;text-align:left}
      .definition-list>div{grid-template-columns:130px minmax(0,1fr)}
    }
    @media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
  </style>
</head>
<body>
  <div id="supportBanner" role="status" aria-live="polite" hidden>
    <span class="support-mark" aria-hidden="true"></span>
    <strong>Просмотр компании</strong>
    <span id="supportBannerText" class="support-copy"></span>
    <div class="support-actions"><button id="supportEndTop" type="button">Вернуться в админку</button></div>
  </div>

  <div id="shell">
    <aside id="scopeRail" aria-label="Навигация управления">
      <div class="brand-lockup">
        <img class="brand-wordmark" src="/assets/studio/akeda-studio-wordmark.png"
          width="520" height="84" decoding="async" alt="Akeda Studio">
        <img class="brand-mark" src="/assets/studio/akeda-studio-mark.png"
          width="216" height="98" decoding="async" alt="" aria-hidden="true">
      </div>
      <div class="admin-label">Управление</div>
      <div id="scopeBox">
        <span class="scope-caption">Область работы</span>
        <strong id="scopeName">Платформа Akeda</strong>
      </div>
      <nav id="primaryNav" aria-label="Разделы"></nav>
      <div class="rail-footer">
        <span id="currentUser"></span><span id="currentUserRole"></span>
        <button id="logoutButton" type="button">
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10"/></svg>
          <span>Выйти</span>
        </button>
      </div>
    </aside>

    <main id="workspace">
      <div class="workspace-head">
        <div class="workspace-title"><h1 id="pageTitle">Компании</h1><p id="pageSubtitle">Загружаю реестр…</p></div>
        <div id="headActions" class="head-actions"></div>
      </div>
      <div id="loadLine" class="loading-line"></div>
      <div id="pageContent" class="content" aria-live="polite"></div>
    </main>

    <aside id="inspector" aria-labelledby="inspectorTitle" hidden>
      <div class="inspector-head"><h2 id="inspectorTitle" tabindex="-1">Детали</h2>
        <button id="inspectorClose" type="button" aria-label="Закрыть инспектор" title="Закрыть">
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
        </button></div>
      <div id="inspectorBody"></div>
    </aside>
  </div>

  <dialog id="activationDialog" aria-labelledby="activationTitle">
    <div class="dialog-head"><h2 id="activationTitle">Доступ создан</h2></div>
    <div class="dialog-body"><p>Передайте логин и пароль сотруднику. Пароль показывается только сейчас; позже можно выпустить новый.</p>
      <div class="credential-list">
        <label class="credential-row" for="credentialLogin"><span>Логин</span><input id="credentialLogin" type="text" readonly></label>
        <label class="credential-row" for="credentialPassword"><span>Пароль</span><input id="credentialPassword" type="text" readonly></label>
      </div><div class="credential-copy"><button id="copyActivation" type="button">Копировать доступ</button></div>
      <p id="activationCopyStatus" role="status" aria-live="polite"></p></div>
    <div class="dialog-actions"><button id="closeActivation" class="primary" type="button">Данные сохранены</button></div>
  </dialog>
  <div id="toast" role="status" aria-live="polite"></div>

  <script>
  (()=>{
    'use strict';
    const $=id=>document.getElementById(id);
    const ICONS={
      companies:'<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V7h10v13M14 11h6v9M7 10h2M7 14h2M7 18h2M17 14h1M17 17h1M2 20h20"/></svg>',
      support:'<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 16 0v5a2 2 0 0 1-2 2h-3M4 12v4h3v-5H4M20 12v4h-3v-5h3M10 20h5"/></svg>',
      audit:'<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4"/></svg>',
      members:'<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="8" r="3"/><path d="M3 20c0-4 2-6 6-6s6 2 6 6M16 5a3 3 0 0 1 0 6M17 14c2.7.3 4 2.2 4 5"/></svg>',
      company:'<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 21V5h16v16M8 9h2M14 9h2M8 13h2M14 13h2M9 21v-4h6v4M2 21h20"/></svg>'
    };
    const ROLE_LABELS={owner:'Владелец',admin:'Администратор',designer:'Проектировщик',
      technologist:'Технолог',reviewer:'Наблюдатель',platform_owner:'Владелец платформы',
      platform_support:'Поддержка Akeda'};
    const STATUS_LABELS={active:'Активна',enabled:'Активен',pending:'Ожидает',invited:'Приглашён',
      suspended:'Приостановлена',disabled:'Отключён',blocked:'Заблокирован',expired:'Истёк',
      approved:'Одобрен',rejected:'Отклонён',revoked:'Отозван',ended:'Завершён',cancelled:'Отменён'};
    const AUDIT_LABELS={'platform.bootstrap':'Создан владелец платформы','auth.login':'Вход в систему',
      'auth.logout':'Выход из системы','organization.created':'Создана компания',
      'organization.status_updated':'Изменён статус компании','member.invited':'Отправлено приглашение',
      'member.invitation_accepted':'Сотрудник активировал доступ','member.provisioned':'Добавлен сотрудник',
      'member.updated':'Изменены права сотрудника','member.access_rotated':'Выпущен новый пароль сотрудника',
      'user.status_updated':'Изменён статус пользователя','support.started':'Администратор Akeda открыл компанию',
      'support.ended':'Администратор Akeda вернулся в админку'};
    const state={me:null,csrf:'',isPlatform:false,page:'companies',companyTab:'overview',
      selectedOrganizationId:null,organizations:[],current:null,globalSupport:[],globalAudit:[],
      search:'',statusFilter:'all',inspector:null,inspectorOpener:null,loading:false};

    const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;',
      '>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const idOf=value=>String(value&&typeof value==='object'?(value.id??value.organization_id??value.membership_id??''):value??'');
    const asArray=value=>Array.isArray(value)?value:[];
    const firstArray=(...values)=>values.find(Array.isArray)||[];
    function pick(obj,...keys){for(const key of keys)if(obj&&obj[key]!==undefined&&obj[key]!==null)return obj[key];return '';}
    function formatDate(value,withTime=false){
      if(!value)return '—';
      const numeric=typeof value==='number'?value:(typeof value==='string'&&/^\d+(?:\.\d+)?$/.test(value)?Number(value):null);
      const date=new Date(numeric!==null&&numeric<1e12?numeric*1000:value);
      if(Number.isNaN(date.getTime()))return String(value);
      return new Intl.DateTimeFormat('ru-RU',withTime?{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}:
        {day:'2-digit',month:'short',year:'numeric'}).format(date);
    }
    function statusKey(value){const key=String(value||'').toLowerCase();
      if(['active','enabled','approved'].includes(key))return key;
      if(['pending','invited','requested'].includes(key))return 'pending';
      if(['suspended','disabled','blocked','rejected'].includes(key))return key;
      if(['expired','ended','revoked','cancelled'].includes(key))return key;return '';}
    function statusHtml(value,label=''){const key=statusKey(value),text=label||STATUS_LABELS[String(value||'').toLowerCase()]||value||'Не задан';
      return `<span class="status ${esc(key)}">${esc(text)}</span>`;}
    function roleLabel(role){return ROLE_LABELS[String(role||'').toLowerCase()]||role||'Не назначена';}
    function auditActionLabel(action){return AUDIT_LABELS[String(action||'')]||action||'Событие';}
    function orgName(org){return pick(org,'name','display_name','organization_name')||'Без названия';}
    function orgId(org){return String(pick(org,'id','organization_id')||'');}
    function orgStatus(org){return pick(org,'status','lifecycle_status')||'active';}
    function orgMode(org){return pick(org,'mode','kind','organization_kind')||'standard';}
    function memberId(member){return String(pick(member,'id','membership_id')||'');}
    function memberName(member){return pick(member,'name','display_name')||pick(member.user||{},'name','display_name')||pick(member,'email')||pick(member.user||{},'email')||'Сотрудник';}
    function memberEmail(member){return pick(member,'email')||pick(member.user||{},'email')||'';}
    function supportId(item){return String(pick(item,'id','support_session_id')||'');}
    function supportOrgName(item){return pick(item,'organization_name')||orgName(state.organizations.find(org=>orgId(org)===String(pick(item,'organization_id')||''))||{});}
    function activeSupport(){
      const fromMe=state.me&&(state.me.support_session||state.me.active_support_session);
      if(fromMe&&['active','approved'].includes(String(fromMe.status||'active').toLowerCase()))return fromMe;
      if(!state.isPlatform)return null;
      const user=state.me&&state.me.user||{},userId=String(pick(user,'id','user_id')||''),userEmail=String(pick(user,'email')||'').toLowerCase();
      return [...asArray(state.current&&state.current.supportSessions),...state.globalSupport]
        .find(item=>String(item.status||'').toLowerCase()==='active'&&(
          item.mine===true||item.actor_is_current===true||
          (userId&&String(pick(item,'actor_id','platform_user_id','requested_by_id')||'')===userId)||
          (userEmail&&String(pick(item,'actor_email','requested_by_email')||'').toLowerCase()===userEmail)))||null;
    }
    function normalizeSnapshot(raw){
      const data=raw&&raw.snapshot?raw.snapshot:(raw||{}),organization=data.organization||data.company||null;
      return {raw:data,organizations:firstArray(data.organizations,data.companies),organization,
        permissions:asArray(data.permissions),
        adminAuthority:String(data.admin_authority||''),
        memberships:firstArray(data.memberships,data.members),invitations:asArray(data.invitations),
        audit:firstArray(data.audit_events,data.audit,data.events),
        supportSessions:firstArray(data.support_sessions,data.support,data.access_sessions)};
    }
    function hasCompanyPermission(permission){return asArray(state.current&&state.current.permissions).includes(permission);}
    function platformManagesCompany(){return !!(state.isPlatform&&state.current&&state.current.adminAuthority==='platform');}
    function canManageMembers(){return platformManagesCompany()||hasCompanyPermission('member.manage');}
    function memberRole(member){return String(pick(member,'role')||'');}
    function memberCanBeManaged(member){return canManageMembers()&&(platformManagesCompany()||memberRole(member)!=='owner');}
    async function readJson(response){const text=await response.text();if(!text)return {};
      try{return JSON.parse(text);}catch(_error){return {error:text};}}
    async function api(path,options={}){
      const method=String(options.method||'GET').toUpperCase(),headers={Accept:'application/json',...(options.headers||{})};
      if(options.body!==undefined)headers['Content-Type']='application/json';
      if(method!=='GET'&&method!=='HEAD'&&state.csrf)headers['X-CSRF-Token']=state.csrf;
      const response=await fetch(path,{...options,method,headers,credentials:'same-origin'}),data=await readJson(response);
      if(response.status===401){location.replace('/login');throw new Error('Сессия завершена.');}
      if(!response.ok||data.error)throw new Error(data.error||data.message||`Запрос не выполнен (${response.status})`);
      return data;
    }
    async function post(path,payload){if(!state.csrf)throw new Error('В сессии нет CSRF-токена. Войдите заново.');
      return api(path,{method:'POST',body:JSON.stringify(payload||{})});}
    function setLoading(loading){state.loading=!!loading;$('loadLine').hidden=!loading;}
    function toast(message,bad=false){const node=$('toast');node.textContent=message;node.classList.toggle('bad',bad);
      node.classList.add('on');clearTimeout(node._timer);node._timer=setTimeout(()=>node.classList.remove('on'),bad?5200:2800);}
    function currentOrg(){return (state.current&&state.current.organization)||
      state.organizations.find(org=>orgId(org)===String(state.selectedOrganizationId||''))||null;}
    function userMembershipOrgId(){
      const item=(state.me&&state.me.membership)||firstArray(state.me&&state.me.memberships)[0]||null;
      const organization=state.me&&state.me.organization||{};
      return String(pick(item||{},'organization_id')||pick(item&&item.organization||{},'id','organization_id')||pick(organization,'id','organization_id')||'');
    }
    function organizationOwner(org){return pick(org,'owner_name','owner_email')||pick(org.owner||{},'name','email')||'—';}
    function organizationMemberCount(org){const value=pick(org,'member_count','members_count','active_members');return value===''?'—':value;}
    async function loadSnapshot(organizationId=null){
      setLoading(true);
      try{
        const suffix=organizationId?`?organization_id=${encodeURIComponent(organizationId)}`:'';
        const data=await api('/api/admin/snapshot'+suffix),normalized=normalizeSnapshot(data);
        if(normalized.organizations.length)state.organizations=normalized.organizations;
        if(organizationId||normalized.organization){state.current=normalized;
          state.selectedOrganizationId=organizationId||orgId(normalized.organization);}
        else{state.globalSupport=normalized.supportSessions;state.globalAudit=normalized.audit;}
        if(!organizationId){state.globalSupport=normalized.supportSessions;state.globalAudit=normalized.audit;}
      }finally{setLoading(false);}
    }
    async function refreshCurrent(){
      if(state.selectedOrganizationId)await loadSnapshot(state.selectedOrganizationId);
      else await loadSnapshot(null);
      render();
    }
    function renderScope(){
      const support=activeSupport(),org=currentOrg();document.body.classList.toggle('has-support',!!support);
      $('supportBanner').hidden=!support;$('scopeBox').classList.toggle('is-support',!!support);
      if(support){
        $('supportBannerText').textContent=`Вы просматриваете компанию «${supportOrgName(support)}» от имени Akeda.`;
        $('scopeName').textContent=supportOrgName(support);
      }else $('scopeName').textContent=state.isPlatform?'Платформа Akeda':orgName(org||{});
    }
    function navButton(id,label,icon,current){return `<button type="button" data-nav="${esc(id)}" ${current?'aria-current="page"':''}>${icon}<span>${esc(label)}</span></button>`;}
    function renderNav(){
      const active=state.page==='company'?state.companyTab:state.page,viewingCompany=state.isPlatform&&state.page==='company'&&!!activeSupport();
      $('primaryNav').innerHTML=state.isPlatform&&!viewingCompany?[
        navButton('companies','Компании',ICONS.companies,active==='companies'),
        navButton('audit','Аудит',ICONS.audit,active==='audit')].join(''):[
        navButton('overview','Компания',ICONS.company,active==='overview'),
        navButton('members','Сотрудники',ICONS.members,active==='members'),
        navButton('audit','Журнал действий',ICONS.audit,active==='audit')].join('');
      $('primaryNav').querySelectorAll('[data-nav]').forEach(button=>button.onclick=async()=>{
        const target=button.dataset.nav;
        if(state.isPlatform&&!viewingCompany&&['companies','audit'].includes(target)){
          state.page=target;state.companyTab='overview';closeInspector();
          if(target!=='companies')await loadSnapshot(null);render();return;
        }
        state.page='company';state.companyTab=target;closeInspector();render();
      });
    }
    function renderHeader(title,subtitle,actions=''){$('pageTitle').textContent=title;$('pageSubtitle').textContent=subtitle||'';$('headActions').innerHTML=actions;}
    function migrationNotice(){return `<div class="migration-sheet"><div><strong>Каталоги и чаты пока не подключены</strong>
      <p>${orgMode(currentOrg()||{})==='demo'?'Для каждого входа будет создана отдельная временная копия демо-каталога и AI-истории. Сейчас подключается её tenant-хранилище.':'После tenant-миграции здесь появятся каталоги, изделия, история AI-чатов и переход в Studio. До переноса данных эти разделы не открываются.'}</p></div>
      <button type="button" disabled title="Станет доступно после переноса проектов по компаниям">Открыть Studio</button></div>`;}

    function renderCompanies(){
      renderHeader('Компании','Платформенный реестр клиентов',`<button id="createOrganization" class="primary" type="button">Создать компанию</button>`);
      const filtered=state.organizations.filter(org=>{
        const haystack=`${orgName(org)} ${orgId(org)} ${organizationOwner(org)} ${orgMode(org)}`.toLowerCase();
        return haystack.includes(state.search.toLowerCase())&&(state.statusFilter==='all'||orgStatus(org)===state.statusFilter);
      });
      $('pageContent').innerHTML=`<div class="toolbar">
        <label class="search-field"><span class="sr-only">Найти компанию</span>
          <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>
          <input id="companySearch" type="search" value="${esc(state.search)}" placeholder="Название, ID или владелец"></label>
        <label class="sr-only" for="companyStatus">Статус компании</label><select id="companyStatus">
          <option value="all">Все статусы</option><option value="active" ${state.statusFilter==='active'?'selected':''}>Активные</option>
          <option value="pending" ${state.statusFilter==='pending'?'selected':''}>Подключение</option>
          <option value="suspended" ${state.statusFilter==='suspended'?'selected':''}>Приостановленные</option></select>
        <span class="result-count">${filtered.length} из ${state.organizations.length}</span></div>
        <div id="companiesTable"></div>`;
      const target=$('companiesTable');
      if(!state.organizations.length){target.innerHTML=`<div class="empty-state"><div><div class="empty-rule"></div><h2>Компаний пока нет</h2>
        <p>Создайте первую компанию и передайте владельцу одноразовую ссылку активации.</p>
        <button id="createOrganizationEmpty" class="primary" type="button">Создать компанию</button></div></div>`;}
      else if(!filtered.length)target.innerHTML=`<div class="empty-state"><div><div class="empty-rule"></div><h2>Компании не найдены</h2>
        <p>Измените запрос или сбросьте фильтр статуса.</p><button id="resetCompanyFilters" type="button">Сбросить фильтры</button></div></div>`;
      else target.innerHTML=`<div class="table-wrap"><table aria-label="Компании"><colgroup><col style="width:30%"><col style="width:14%">
        <col style="width:22%"><col style="width:14%"><col style="width:20%"></colgroup><thead><tr>
        <th scope="col">Компания</th><th scope="col">Статус</th><th scope="col">Владелец</th><th scope="col">Сотрудники</th>
        <th scope="col">Последняя активность</th></tr></thead><tbody>${filtered.map(org=>{
          const id=orgId(org),selected=id===String(state.selectedOrganizationId||'');return `<tr class="${selected?'is-selected':''}">
          <td><button class="entity-button" type="button" data-org="${esc(id)}">${esc(orgName(org))}</button><span class="entity-sub">${esc(id||'ID не выдан')}</span></td>
          <td>${statusHtml(orgStatus(org))}</td><td>${esc(organizationOwner(org))}</td><td class="mono">${esc(organizationMemberCount(org))}</td>
          <td>${esc(formatDate(pick(org,'last_activity_at','updated_at'),true))}</td></tr>`;}).join('')}</tbody></table></div>`;
      $('companySearch').oninput=event=>{state.search=event.target.value;renderCompanies();};
      $('companyStatus').onchange=event=>{state.statusFilter=event.target.value;renderCompanies();};
      $('createOrganization').onclick=()=>openInspector('create-organization',null,$('createOrganization'));
      if($('createOrganizationEmpty'))$('createOrganizationEmpty').onclick=()=>openInspector('create-organization',null,$('createOrganizationEmpty'));
      if($('resetCompanyFilters'))$('resetCompanyFilters').onclick=()=>{state.search='';state.statusFilter='all';renderCompanies();};
      target.querySelectorAll('[data-org]').forEach(button=>button.onclick=()=>selectOrganization(button.dataset.org,button));
    }

    function companyTabs(){const tabs=[['overview','Обзор']];
      if(hasCompanyPermission('member.read'))tabs.push(['members','Сотрудники']);
      if(hasCompanyPermission('audit.read'))tabs.push(['audit','Журнал']);
      if(!tabs.some(([id])=>id===state.companyTab))state.companyTab='overview';
      return `<nav class="tab-rail" role="tablist" aria-label="Разделы компании">${tabs.map(([id,label])=>
        `<button type="button" role="tab" data-company-tab="${id}" aria-selected="${state.companyTab===id}" tabindex="${state.companyTab===id?'0':'-1'}">${label}</button>`).join('')}</nav>`;}
    function renderCompany(){
      const org=currentOrg();if(!org){state.page=state.isPlatform?'companies':'company';
        renderHeader('Компания','Данные компании недоступны');$('pageContent').innerHTML='<div class="notice error">Компания не найдена или недоступна для этой сессии.</div>';return;}
      const viewing=state.isPlatform&&!!activeSupport();
      renderHeader(orgName(org),viewing?'Рабочее пространство компании':state.isPlatform?'Карточка компании':'Управление компанией',
        state.isPlatform?'':'<a class="button-link" data-studio-link href="/index.html">Вернуться в Studio</a>');
      $('pageContent').innerHTML=`<div class="company-head">${state.isPlatform?`<button id="backCompanies" class="back-button" type="button">
        <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>${viewing?'Вернуться в админку':'К реестру'}</button>`:''}
        <div class="company-identity"><h2>${esc(orgName(org))}</h2><div class="company-meta">${statusHtml(orgStatus(org))}<span class="mono">${esc(orgId(org))}</span></div></div>
        <div class="company-actions">${state.isPlatform&&!viewing?'<button id="enterCompanyTop" class="primary" type="button">Открыть компанию</button>':''}<button id="openCompanyInspector" type="button">Сведения</button></div></div>${companyTabs()}<div id="companyPanel"></div>`;
      if($('backCompanies'))$('backCompanies').onclick=viewing?leaveCompany:()=>{state.page='companies';state.companyTab='overview';closeInspector();render();};
      if($('enterCompanyTop'))$('enterCompanyTop').onclick=event=>enterCompany(orgId(org),event.currentTarget);
      $('openCompanyInspector').onclick=event=>openInspector('organization',org,event.currentTarget);
      bindCompanyTabs();renderCompanyPanel();
    }
    function bindCompanyTabs(){const tabs=[...document.querySelectorAll('[data-company-tab]')];tabs.forEach((button,index)=>{
      button.onclick=()=>{state.companyTab=button.dataset.companyTab;render();};
      button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();
        let next=index;if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;if(event.key==='ArrowRight')next=(index+1)%tabs.length;
        if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;state.companyTab=tabs[next].dataset.companyTab;render();
        requestAnimationFrame(()=>document.querySelector(`[data-company-tab="${state.companyTab}"]`)?.focus());};});}
    function renderCompanyPanel(){const panel=$('companyPanel'),org=currentOrg(),snapshot=state.current||normalizeSnapshot({});
      if(state.companyTab==='members'){renderMembers(panel,snapshot);return;}
      if(state.companyTab==='audit'){renderAudit(panel,snapshot.audit,'Журнал компании');return;}
      const members=snapshot.memberships,activeMembers=members.filter(member=>String(pick(member,'status')||'active')==='active').length;
      panel.innerHTML=`<section class="panel"><div class="section"><div class="section-head"><h3>Компания</h3></div>
        <dl class="definition-list"><div><dt>Название</dt><dd>${esc(orgName(org))}</dd></div><div><dt>Идентификатор</dt><dd class="mono">${esc(orgId(org))}</dd></div>
        <div><dt>Статус</dt><dd>${statusHtml(orgStatus(org))}</dd></div><div><dt>Владелец</dt><dd>${esc(organizationOwner(org))}</dd></div>
        <div><dt>Сотрудники</dt><dd>${activeMembers} активных · ${members.length} всего</dd></div>
        <div><dt>Создана</dt><dd>${esc(formatDate(pick(org,'created_at')))}</dd></div></dl></div>
        <div class="section"><div class="section-head"><h3>Команда</h3></div><div class="owner-workspace"><div><strong>Доступы сотрудников</strong>
        <p>${canManageMembers()?'Добавляйте сотрудников, назначайте рабочие роли и отключайте доступ.':'Состав команды доступен только для просмотра.'}</p></div>
        <button id="openMembersFromOverview" type="button">Открыть сотрудников</button></div></div>
        <div class="section"><div class="section-head"><h3>Рабочее пространство</h3></div><div class="owner-workspace"><div><strong>Akeda Studio</strong>
        <p>Каталог, изделия, AI-правки и производственные данные остаются в основном рабочем интерфейсе.</p></div>
        <a class="button-link" data-studio-link href="/index.html">Открыть Studio</a></div></div></section>`;
      bindStudioLinks();
      $('openMembersFromOverview').onclick=()=>{state.companyTab='members';render();};
    }
    function renderMembers(panel,snapshot){
      const members=snapshot.memberships,canManage=canManageMembers();
      panel.innerHTML=`<section class="panel"><div class="section"><div class="section-head"><h3>Сотрудники</h3>
        ${canManage?'<button id="inviteMember" class="primary" type="button">Добавить сотрудника</button>':''}</div><div id="membersTable"></div></div>
        ${snapshot.invitations.length?`<div class="section"><div class="section-head"><h3>Ожидают активации</h3></div>
        <div class="audit-list">${snapshot.invitations.map(inv=>`<div class="audit-row"><span>${esc(formatDate(pick(inv,'created_at'),true))}</span>
          <span>${esc(pick(inv,'email'))}</span><span>Роль: ${esc(roleLabel(pick(inv,'role')))}</span><span>${statusHtml(pick(inv,'status')||'pending')}</span></div>`).join('')}</div></div>`:''}</section>`;
      const target=$('membersTable');
      if(!members.length)target.innerHTML='<div class="notice">Сотрудники ещё не добавлены. В компании всегда должен оставаться хотя бы один владелец.</div>';
      else target.innerHTML=`<div class="table-wrap"><table aria-label="Сотрудники"><colgroup><col style="width:32%"><col style="width:20%"><col style="width:16%"><col style="width:20%"><col style="width:12%"></colgroup>
        <thead><tr><th scope="col">Сотрудник</th><th scope="col">Роль</th><th scope="col">Статус</th><th scope="col">Последний вход</th><th scope="col">${canManage?'Действия':''}</th></tr></thead>
        <tbody>${members.map(member=>{const editable=memberCanBeManaged(member);return `<tr><td><strong>${esc(memberName(member))}</strong><span class="entity-sub">${esc(memberEmail(member))}</span></td>
        <td>${esc(roleLabel(pick(member,'role')))}</td><td>${statusHtml(pick(member,'status')||'active')}</td><td>${esc(formatDate(pick(member,'last_login_at','last_seen_at'),true))}</td>
        <td>${editable?`<button class="row-action" type="button" data-member="${esc(memberId(member))}">Изменить</button>`:memberRole(member)==='owner'?'<span class="muted">Владелец</span>':''}</td></tr>`;}).join('')}</tbody></table></div>`;
      if($('inviteMember'))$('inviteMember').onclick=event=>openInspector('invite-member',null,event.currentTarget);
      if(canManage)target.querySelectorAll('[data-member]').forEach(button=>button.onclick=()=>{
        const member=members.find(item=>memberId(item)===button.dataset.member);openInspector('edit-member',member,button);});
    }
    function renderAudit(container,items,title){
      items=items.map(event=>({...event,action:auditActionLabel(pick(event,'action','event_type'))}));
      if(container===$('pageContent'))renderHeader(title||'Аудит',state.isPlatform?'События платформенного контура':'События компании');
      const body=`<section class="panel"><div class="section"><div class="section-head"><h3>${esc(title||'Журнал действий')}</h3></div>
        ${items.length?`<div class="audit-list">${items.map(event=>`<div class="audit-row"><time class="mono">${esc(formatDate(pick(event,'created_at','timestamp'),true))}</time>
        <span>${esc(pick(event,'actor_name','actor_email')||'Система')}</span><span class="audit-detail"><span class="audit-action">${esc(pick(event,'action','event_type')||'Событие')}</span>
        ${pick(event,'detail','summary')?` · ${esc(pick(event,'detail','summary'))}`:''}</span><span class="audit-result">${esc(pick(event,'outcome','status')||'выполнено')}</span></div>`).join('')}</div>`:
        '<div class="notice">В журнале пока нет событий.</div>'}</div></section>`;
      container.innerHTML=body;
    }
    function renderGlobalAudit(){renderAudit($('pageContent'),state.globalAudit,'Аудит платформы');}

    function render(){renderScope();renderNav();
      if(state.page==='companies'){renderCompanies();return;}
      if(state.page==='audit'&&state.isPlatform){renderGlobalAudit();return;}renderCompany();}

    async function selectOrganization(id,opener){
      try{state.selectedOrganizationId=id;await loadSnapshot(id);render();openInspector('organization',currentOrg(),opener);}
      catch(error){toast(error.message,true);}
    }
    async function openCompany(id,tab='overview'){
      try{state.selectedOrganizationId=id;await loadSnapshot(id);state.page='company';state.companyTab=tab;closeInspector();render();}
      catch(error){toast(error.message,true);}
    }
    function openInspector(mode,payload,opener){state.inspector={mode,payload};state.inspectorOpener=opener||document.activeElement;
      $('inspector').hidden=false;$('shell').classList.add('has-inspector');renderInspector();requestAnimationFrame(()=>$('inspectorTitle').focus());}
    function closeInspector(){if($('inspector').hidden)return;$('inspector').hidden=true;$('shell').classList.remove('has-inspector');
      state.inspector=null;const opener=state.inspectorOpener;state.inspectorOpener=null;if(opener&&opener.isConnected)opener.focus();}
    function roleOptions(selected,includeOwner=true){return Object.entries(ROLE_LABELS).filter(([key])=>
      !key.startsWith('platform_')&&(includeOwner||key!=='owner')).map(([key,label])=>`<option value="${key}" ${selected===key?'selected':''}>${label}</option>`).join('');}
    function renderInspector(){const item=state.inspector;if(!item)return;const org=currentOrg();
      if(item.mode==='create-organization'){
        $('inspectorTitle').textContent='Создать компанию';$('inspectorBody').innerHTML=`<form id="createOrganizationForm" class="inspector-form">
          <label class="field" for="newOrgName">Название компании<input id="newOrgName" required maxlength="120" autocomplete="organization"></label>
          <label class="field" for="newOwnerName">Имя первого владельца<input id="newOwnerName" required maxlength="120" autocomplete="name"></label>
          <label class="field" for="newOwnerEmail">Рабочая почта владельца<input id="newOwnerEmail" type="email" required autocomplete="email"></label>
          <div class="notice">Логином будет почта. Пароль из 6 букв и цифр создастся автоматически и покажется один раз.</div>
          <p id="createOrganizationMessage" class="form-message" role="alert"></p>
          <div class="form-actions"><button type="button" data-close-inspector>Отменить</button><button class="primary" type="submit">Создать и выдать доступ</button></div></form>`;
        $('createOrganizationForm').onsubmit=createOrganization;bindInspectorClose();return;
      }
      if(item.mode==='invite-member'){
        $('inspectorTitle').textContent='Добавить сотрудника';$('inspectorBody').innerHTML=`<form id="inviteMemberForm" class="inspector-form">
          <label class="field" for="inviteName">Имя<input id="inviteName" required maxlength="120" autocomplete="name"></label>
          <label class="field" for="inviteEmail">Рабочая почта<input id="inviteEmail" type="email" required autocomplete="email"></label>
          <label class="field" for="inviteRole">Роль<select id="inviteRole" required><option value="" selected disabled>Выберите роль</option>${roleOptions('',false)}</select></label>
          <div id="inviteRoleHelp" class="notice">Выберите, что сотрудник сможет делать в Studio.</div>
          <p id="inviteMemberMessage" class="form-message" role="alert"></p>
          <div class="form-actions"><button type="button" data-close-inspector>Отменить</button><button class="primary" type="submit">Создать и выдать доступ</button></div></form>`;
        $('inviteRole').onchange=()=>{$('inviteRoleHelp').textContent=rolePermissionSummary($('inviteRole').value);};
        $('inviteMemberForm').onsubmit=inviteMember;bindInspectorClose();return;
      }
      if(item.mode==='edit-member'){
        const member=item.payload||{},role=String(pick(member,'role')||'reviewer'),status=String(pick(member,'status')||'active');
        $('inspectorTitle').textContent='Сотрудник';$('inspectorBody').innerHTML=`<div class="inspector-summary"><h3>${esc(memberName(member))}</h3><p>Логин: ${esc(memberEmail(member))}</p></div>
          <form id="editMemberForm" class="inspector-form">
          <label class="field" for="editMemberName">Имя<input id="editMemberName" required maxlength="120" value="${esc(memberName(member))}"></label>
          <label class="field" for="editMemberEmail">Логин · почта<input id="editMemberEmail" type="email" required value="${esc(memberEmail(member))}"></label>
          <label class="field" for="editMemberRole">Роль<select id="editMemberRole">${roleOptions(role,platformManagesCompany())}</select></label>
          <div id="editRoleHelp" class="notice">${esc(rolePermissionSummary(role))}</div><label class="field" for="editMemberStatus">Состояние<select id="editMemberStatus">
          <option value="active" ${status==='active'?'selected':''}>Активен</option><option value="disabled" ${status==='disabled'?'selected':''}>Отключён</option></select></label>
          <div class="notice">Старый пароль посмотреть нельзя. Если он утерян, выпустите новый — старый сразу перестанет работать.</div>
          <button id="resetMemberPassword" type="button">Выпустить новый пароль</button>
          <div class="notice warning">Роль владельца и передача владения управляются только через Akeda.</div>
          <p id="editMemberMessage" class="form-message" role="alert"></p><div class="form-actions"><button type="button" data-close-inspector>Отменить</button>
          <button class="primary" type="submit">Сохранить</button></div></form>`;
        $('editMemberRole').onchange=()=>{$('editRoleHelp').textContent=rolePermissionSummary($('editMemberRole').value);};
        $('resetMemberPassword').onclick=event=>resetMemberPassword(memberId(member),event.currentTarget);
        $('editMemberForm').onsubmit=event=>updateMember(event,memberId(member));bindInspectorClose();return;
      }
      const selected=item.payload||org||{};$('inspectorTitle').textContent='Карточка компании';
      $('inspectorBody').innerHTML=`<div class="inspector-summary"><h3>${esc(orgName(selected))}</h3><p class="mono">${esc(orgId(selected))}</p></div>
        <dl class="definition-list"><div><dt>Статус</dt><dd>${statusHtml(orgStatus(selected))}</dd></div>
        <div><dt>Владелец</dt><dd>${esc(organizationOwner(selected))}</dd></div><div><dt>Сотрудники</dt><dd class="mono">${esc(organizationMemberCount(selected))}</dd></div></dl>
        ${state.isPlatform?'<button class="primary inspector-primary" type="button" data-enter-company>Открыть компанию</button>':''}
        <div class="notice warning">Каталоги и AI-чаты появятся здесь после tenant-миграции.</div>
        <div class="inspector-links"><button type="button" data-open-company="overview"><span>Открыть карточку</span><span>›</span></button>
          <button type="button" data-open-company="members"><span>Сотрудники и роли</span><span>›</span></button>
          <button type="button" data-open-company="audit"><span>Журнал компании</span><span>›</span></button></div>`;
      const enterButton=$('inspectorBody').querySelector('[data-enter-company]');
      if(enterButton)enterButton.onclick=()=>enterCompany(orgId(selected),enterButton);
      $('inspectorBody').querySelectorAll('[data-open-company]').forEach(button=>button.onclick=()=>openCompany(orgId(selected),button.dataset.openCompany));
    }
    function bindInspectorClose(){$('inspectorBody').querySelectorAll('[data-close-inspector]').forEach(button=>button.onclick=closeInspector);}
    function rolePermissionSummary(role){return ({owner:'Все действия компании, команда и производство.',
      admin:'Проекты, настройки и производство без управления командой.',designer:'Создание и изменение проектов, AI и бесплатные экспорты.',
      technologist:'Производственная проверка, BOM и разрешённые сборки.',reviewer:'Только просмотр и согласование.'})[role]||'Выберите роль и проверьте область доступа.';}
    function mutationBusy(form,busy){[...form.elements].forEach(element=>element.disabled=busy);}
    function showCredentials(data){const credentials=data&&data.credentials||data||{},login=pick(credentials,'login'),password=pick(credentials,'password','starter_password');
      if(!login||!password)return;$('credentialLogin').value=login;$('credentialPassword').value=password;
      $('activationCopyStatus').textContent='';$('activationDialog').showModal();}
    async function createOrganization(event){event.preventDefault();const form=event.currentTarget,message=$('createOrganizationMessage');message.textContent='';mutationBusy(form,true);
      try{const data=await post('/api/admin/organizations/provision',{name:$('newOrgName').value.trim(),owner_name:$('newOwnerName').value.trim(),owner_email:$('newOwnerEmail').value.trim()});
        const created=data.organization||data.company||null;closeInspector();await loadSnapshot(null);render();showCredentials(data.credentials);
        if(created){state.selectedOrganizationId=orgId(created)||state.selectedOrganizationId;}toast('Компания создана.');}
      catch(error){message.textContent=error.message;mutationBusy(form,false);}}
    async function inviteMember(event){event.preventDefault();const form=event.currentTarget,message=$('inviteMemberMessage');message.textContent='';mutationBusy(form,true);
      try{const data=await post('/api/admin/members/provision',{organization_id:state.selectedOrganizationId,name:$('inviteName').value.trim(),
        email:$('inviteEmail').value.trim(),role:$('inviteRole').value});closeInspector();await refreshCurrent();showCredentials(data.credentials);toast('Сотрудник добавлен.');}
      catch(error){message.textContent=error.message;mutationBusy(form,false);}}
    async function updateMember(event,id){event.preventDefault();const form=event.currentTarget,message=$('editMemberMessage');message.textContent='';mutationBusy(form,true);
      try{await post('/api/admin/members/update',{organization_id:state.selectedOrganizationId,membership_id:id,
        name:$('editMemberName').value.trim(),email:$('editMemberEmail').value.trim(),role:$('editMemberRole').value,status:$('editMemberStatus').value});
        closeInspector();await refreshCurrent();toast('Данные сотрудника обновлены.');}
      catch(error){message.textContent=error.message;mutationBusy(form,false);}}
    async function resetMemberPassword(id,button){button.disabled=true;
      try{const data=await post('/api/admin/members/reset-password',{organization_id:state.selectedOrganizationId,membership_id:id});
        closeInspector();showCredentials(data.credentials);toast('Новый пароль выпущен.');}
      catch(error){toast(error.message,true);button.disabled=false;}}
    async function enterCompany(organizationId,button){
      if(!state.isPlatform||!organizationId)return;
      if(button)button.disabled=true;
      try{
        const current=activeSupport(),currentOrgId=String(pick(current||{},'organization_id')||'');
        if(current&&currentOrgId&&currentOrgId!==String(organizationId)){
          await post('/api/admin/company-view/end',{support_session_id:supportId(current)});
          if(state.me){state.me.support_session=null;state.me.active_support_session=null;}
        }
        if(!current||currentOrgId!==String(organizationId)){
          const data=await post('/api/admin/company-view/start',{organization_id:organizationId});
          if(data.support_session)state.me.support_session=data.support_session;
        }
        state.selectedOrganizationId=organizationId;await loadSnapshot(organizationId);state.page='company';state.companyTab='overview';
        closeInspector();render();toast('Компания открыта.');
      }catch(error){toast(error.message,true);if(button)button.disabled=false;}
    }
    async function leaveCompany(){const support=activeSupport();$('supportEndTop').disabled=true;
      try{if(support)await post('/api/admin/company-view/end',{support_session_id:supportId(support)});
        if(state.me){state.me.support_session=null;state.me.active_support_session=null;}
        state.current=null;state.selectedOrganizationId=null;state.page='companies';state.companyTab='overview';closeInspector();
        await loadSnapshot(null);render();toast('Вы вернулись в админку.');}
      catch(error){toast(error.message,true);}finally{$('supportEndTop').disabled=false;}}

    const STUDIO_URL=__STUDIO_URL__;
    function bindStudioLinks(){document.querySelectorAll('[data-studio-link]').forEach(link=>link.href=STUDIO_URL);}
    async function init(){setLoading(true);
      try{
        const raw=await api('/api/auth/me'),user=raw.user||(raw.email?raw:null);
        if(raw.authenticated===false||!user){location.replace('/login');return;}
        state.me={...raw,user};state.csrf=pick(raw,'csrf_token','csrfToken')||pick(user,'csrf_token','csrfToken');
        const platformRole=String(pick(user,'platform_role')||pick(raw,'platform_role')||'');
        state.isPlatform=platformRole.startsWith('platform_');$('currentUser').textContent=pick(user,'name','display_name','email')||'Пользователь';
        const currentMembership=raw.membership||firstArray(raw.memberships)[0]||{};
        $('currentUserRole').textContent=roleLabel(platformRole||pick(raw,'role')||pick(currentMembership,'role'));
        if(state.isPlatform){await loadSnapshot(null);const resumed=activeSupport(),resumedOrgId=String(pick(resumed||{},'organization_id')||'');
          if(resumedOrgId){state.selectedOrganizationId=resumedOrgId;await loadSnapshot(resumedOrgId);state.page='company';}
          else state.page='companies';}
        else{const orgIdValue=userMembershipOrgId();state.selectedOrganizationId=orgIdValue;state.page='company';
          if(orgIdValue)await loadSnapshot(orgIdValue);else state.current=normalizeSnapshot(raw);}
        render();
      }catch(error){$('pageTitle').textContent='Управление недоступно';$('pageSubtitle').textContent='';
        $('pageContent').innerHTML=`<div class="notice error">${esc(error.message||'Не удалось загрузить данные.')}</div>`;
      }finally{setLoading(false);}
    }
    $('inspectorClose').onclick=closeInspector;
    $('logoutButton').onclick=async()=>{try{await post('/api/auth/logout',{});}catch(_error){}location.replace('/login');};
    $('supportEndTop').onclick=leaveCompany;
    $('copyActivation').onclick=async()=>{const value=`Логин: ${$('credentialLogin').value}\nПароль: ${$('credentialPassword').value}`;
      try{await navigator.clipboard.writeText(value);$('activationCopyStatus').textContent='Логин и пароль скопированы.';}
      catch(_error){$('credentialPassword').select();$('activationCopyStatus').textContent='Скопируйте данные вручную.';}};
    $('closeActivation').onclick=()=>{$('activationDialog').close();$('credentialLogin').value='';$('credentialPassword').value='';$('activationCopyStatus').textContent='';};
    $('activationDialog').addEventListener('cancel',event=>{event.preventDefault();toast('Сначала сохраните логин и пароль.',true);});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('inspector').hidden)closeInspector();});
    init();
  })();
  </script>
</body>
</html>"""


def login_page() -> str:
    """Return the self-contained login page."""

    return LOGIN_PAGE


def admin_page() -> str:
    """Return the self-contained identity/admin console page."""

    return ADMIN_PAGE


def activation_page() -> str:
    """Return the self-contained one-time invitation activation page."""

    return ACTIVATION_PAGE


render_login_page = login_page
render_admin_page = admin_page
render_activation_page = activation_page

__all__ = [
    "LOGIN_PAGE",
    "ACTIVATION_PAGE",
    "ADMIN_PAGE",
    "login_page",
    "activation_page",
    "admin_page",
    "render_login_page",
    "render_activation_page",
    "render_admin_page",
]
