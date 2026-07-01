"""BAZIS Studio: локальный редактор-предпросмотр единицы мебели (AKD-94…97).

Идея: «функционал БАЗИСа, который нам нужен» уже реализован в конвейере на Python
(генераторы, материалы, присадки, фурнитура, проверки). Studio отдаёт его в наш
3D-вьювер через локальный HTTP-сервер: правки параметров пересчитываются мгновенно
и БЕСПЛАТНО, а платная сборка .b3d через облако — только по явному подтверждению
и только когда все проверки зелёные.

Один источник правды: ничего не портируется в JS — браузер шлёт ParamSpec,
Python возвращает панели/присадки/фурнитуру/проверки/BOM/чертёж.

Запуск: python main.py studio paramspecs/komi_72_tumba_podkatnaya.json
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ payload

def build_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """ParamSpec → всё для редактора: модель, проверки, BOM. Ошибки не бросают."""
    from .paramspec import validate_paramspec

    issues: dict[str, list[str]] = {"schema": [], "consistency": [], "geometry": [],
                                    "cfrn": [], "holes": []}
    issues["schema"] = list(validate_paramspec(spec) or [])
    if issues["schema"]:
        return {"ok": False, "issues": issues}

    from .generators import generate_from_paramspec
    try:
        project = generate_from_paramspec(spec)
    except Exception as e:
        issues["schema"] = [f"генерация: {e}"]
        return {"ok": False, "issues": issues}
    try:
        from .materials import resolve_project_materials
        project["material_refs"] = resolve_project_materials(project)
    except Exception:
        pass

    from .consistency_check import check_consistency
    from .geometry_check import check_placement_geometry
    from .cfrn import check_cfrn_encoding, check_cfrn_holes
    issues["consistency"] = [f"{i.panel}: {i.message}" for i in check_consistency(project)
                             if i.severity == "error"]
    g = check_placement_geometry(project)
    if not g.get("ok", True):
        issues["geometry"] = [str(x) for x in g.get("issues", [])][:20] or ["пересечения панелей"]
    try:
        issues["cfrn"] = check_cfrn_encoding(project)[:20]
        issues["holes"] = check_cfrn_holes(project)[:20]
    except Exception as e:
        issues["cfrn"] = [f"кодирование: {e}"]

    from .webviewer import _COLORS, _hardware, _holes, _panels
    from .delivery import _hardware_bom, spec_summary
    s = spec_summary(project)
    payload = {
        "ok": not any(issues.values()),
        "issues": issues,
        "viewer": {"panels": _panels(project), "colors": _COLORS,
                   "holes": _holes(project), "hardware": _hardware(project)},
        "stats": {"n_panels": s["n_panels"], "n_holes": s["n_holes"],
                  "dims": s["dims"], "decor": s["decor"]},
        "bom": _hardware_bom(project),
    }
    return payload


def techview_svg(spec: dict[str, Any]) -> dict[str, Any]:
    from .generators import generate_from_paramspec
    from .techview import build_techview_svg
    try:
        svg, tv_issues = build_techview_svg(generate_from_paramspec(spec))
        return {"svg": svg, "issues": tv_issues}
    except Exception as e:
        return {"svg": "", "issues": [str(e)]}


# ------------------------------------------------------------------ server

class _Studio:
    def __init__(self, spec_path: Path, out_dir: Path):
        self.spec_path = spec_path
        self.out_dir = out_dir
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))


def make_handler(st: _Studio):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                       # тихий сервер
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                page = PAGE.replace("__SPEC__", json.dumps(st.spec, ensure_ascii=False)
                                    .replace("</", "<\\/"))
                self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"{}")

        def do_POST(self):
            try:
                body = self._body()
                spec = body.get("spec") or {}
                if self.path == "/api/generate":
                    self._json(build_payload(spec))
                elif self.path == "/api/techview":
                    self._json(techview_svg(spec))
                elif self.path == "/api/save":
                    st.spec = spec
                    st.spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
                    from .generators import generate_from_paramspec
                    project = generate_from_paramspec(spec)
                    out = st.out_dir / (st.spec_path.stem + ".project.json")
                    out.write_text(json.dumps(project, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
                    self._json({"ok": True, "spec": str(st.spec_path), "project": str(out)})
                elif self.path == "/api/export-cfrn":
                    from .generators import generate_from_paramspec
                    from .materials import resolve_project_materials
                    from .cfrn import project_to_cfrn_bytes
                    project = generate_from_paramspec(spec)
                    try:
                        project["material_refs"] = resolve_project_materials(project)
                    except Exception:
                        pass
                    out = st.out_dir / (st.spec_path.stem + ".cfrn")
                    out.write_bytes(project_to_cfrn_bytes(project))
                    self._json({"ok": True, "cfrn": str(out)})
                elif self.path == "/api/build-b3d":
                    payload = build_payload(spec)
                    if not payload["ok"]:                # деньги — только на зелёную модель
                        self._json({"ok": False, "error": "проверки не пройдены",
                                    "issues": payload["issues"]}, 409)
                        return
                    from .build_b3d import build_b3d_from_paramspec
                    out = st.out_dir / (st.spec_path.stem + ".b3d")
                    try:
                        rep = build_b3d_from_paramspec(spec, out)
                        self._json({"ok": True, **{k: str(v) for k, v in rep.items()}})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)[:300]}, 502)
                else:
                    self._send(404, b"{}")
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:300]}, 500)

    return Handler


def run_studio(spec_path: str | Path, *, port: int = 8765, out_dir: str | Path | None = None,
               open_browser: bool = True) -> None:
    spec_path = Path(spec_path)
    out = Path(out_dir) if out_dir else spec_path.parent
    st = _Studio(spec_path, out)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(st))
    url = f"http://127.0.0.1:{port}/"
    print(f"BAZIS Studio: {url}  (спека: {spec_path.name}; Ctrl+C — стоп)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


# ------------------------------------------------------------------ страница

PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>BAZIS Studio — предпросмотр и правки</title>
<style>
  :root{--ink:#1a1d21;--mut:#6b7280;--line:#dfe3e8;--bg:#f4f6f8;--card:#fff;
        --ok:#2fa84f;--bad:#e5484d;--accent:#3b82f6}
  *{box-sizing:border-box} html,body{margin:0;height:100%;font-family:Segoe UI,Arial,sans-serif;
    background:var(--bg);color:var(--ink);font-size:13px;overflow:hidden}
  #app{display:grid;grid-template-columns:340px 1fr;height:100%}
  #side{background:var(--card);border-right:1px solid var(--line);overflow-y:auto;padding:12px}
  #side h1{font-size:15px;margin:2px 0 10px}
  fieldset{border:1px solid var(--line);border-radius:8px;margin:0 0 10px;padding:8px 10px}
  legend{font-size:11px;text-transform:uppercase;color:var(--mut);padding:0 4px}
  .row{display:flex;gap:6px;align-items:center;margin:4px 0}
  .row label{flex:0 0 92px;color:var(--mut)}
  input[type=number],input[type=text],select{width:100%;padding:4px 6px;border:1px solid var(--line);
    border-radius:6px;font-size:13px}
  input[type=number]{max-width:86px}
  .sec{border:1px dashed var(--line);border-radius:6px;padding:6px;margin:6px 0}
  .sec .row label{flex-basis:70px}
  .mini{font-size:11px;color:var(--mut)}
  button{cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:6px;
         padding:6px 10px;font-size:12.5px}
  button:hover{background:#eef1f4} button:disabled{opacity:.45;cursor:not-allowed}
  button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .badges{display:flex;flex-wrap:wrap;gap:5px}
  .badge{font-size:11px;padding:2px 8px;border-radius:12px;color:#fff;background:var(--ok)}
  .badge.bad{background:var(--bad)}
  #errors{color:var(--bad);font-size:12px;white-space:pre-wrap;max-height:130px;overflow:auto;margin-top:6px}
  #stats{display:flex;gap:14px;margin:8px 0}
  #stats b{font-size:16px} #stats span{display:block;font-size:11px;color:var(--mut)}
  #main{position:relative}
  #stage{position:absolute;inset:0}
  #tabs{position:absolute;top:10px;left:12px;z-index:5;display:flex;gap:6px}
  #tabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  #draw{position:absolute;inset:48px 12px 12px;z-index:4;background:#fff;border:1px solid var(--line);
        border-radius:8px;overflow:auto;display:none;padding:8px}
  #view3d{position:absolute;inset:0}
  #hud{position:absolute;right:12px;top:10px;z-index:5;background:rgba(255,255,255,.9);
       border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:12px}
  #toast{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);z-index:9;
         background:#1a1d21;color:#fff;padding:7px 14px;border-radius:8px;font-size:12.5px;
         opacity:0;transition:opacity .25s;pointer-events:none;max-width:80%}
  details{margin-top:8px} textarea{width:100%;height:170px;font:11px/1.4 Consolas,monospace}
  #bom table{width:100%;border-collapse:collapse;font-size:11.5px}
  #bom td{border-bottom:1px solid var(--line);padding:3px 4px}
</style></head><body>
<div id="app">
<div id="side">
  <h1>BAZIS Studio <span class="mini">— правки до платной сборки</span></h1>

  <div class="badges" id="badges"></div>
  <div id="errors"></div>
  <div id="stats"></div>

  <fieldset><legend>Габариты, мм</legend>
    <div class="row"><label>Ширина</label><input type="number" id="f_w" step="10"></div>
    <div class="row"><label>Глубина</label><input type="number" id="f_d" step="10"></div>
    <div class="row"><label>Высота</label><input type="number" id="f_h" step="10"></div>
  </fieldset>

  <fieldset><legend>Материал</legend>
    <div class="row"><label>Цвет</label><input type="text" id="f_color"></div>
    <div class="row"><label>Код</label><input type="text" id="f_code"></div>
    <div class="row"><label>Плита, мм</label><input type="number" id="f_t" step="1"></div>
  </fieldset>

  <fieldset><legend>Опоры / зазор</legend>
    <div class="row"><label>Опоры, мм</label><input type="number" id="f_legs" step="1"></div>
    <div class="row"><label>Зазор, мм</label><input type="number" id="f_gap" step="0.5"></div>
  </fieldset>

  <fieldset id="fs_sections"><legend>Секции</legend>
    <div id="sections"></div>
    <button id="addSec">+ секция</button>
  </fieldset>

  <fieldset><legend>Экспорт</legend>
    <div class="row" style="gap:6px">
      <button id="btnSave">Сохранить</button>
      <button id="btnCfrn">.cfrn</button>
      <button id="btnB3d" class="primary">Собрать .b3d (~10₽)</button>
    </div>
    <div class="mini">Платная сборка доступна только при зелёных проверках.</div>
  </fieldset>

  <fieldset id="bom"><legend>BOM (фурнитура)</legend><table></table></fieldset>

  <details><summary class="mini">ParamSpec (raw JSON)</summary>
    <textarea id="rawspec" spellcheck="false"></textarea>
    <button id="applyRaw">Применить JSON</button>
  </details>
</div>

<div id="main">
  <div id="view3d"></div>
  <div id="tabs">
    <button id="tab3d" class="on">3D</button>
    <button id="tabDraw">Чертёж</button>
  </div>
  <div id="hud">
    <label><input type="checkbox" id="cbHoles" checked> присадки</label>
    <label><input type="checkbox" id="cbHw" checked> фурнитура</label>
    <label><input type="checkbox" id="cbXray"> прозрачный</label>
  </div>
  <div id="draw"></div>
  <div id="toast"></div>
</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
let SPEC = __SPEC__;
const $ = id => document.getElementById(id);
const toast = (m,bad)=>{const t=$('toast');t.textContent=m;t.style.background=bad?'#b3261e':'#1a1d21';
  t.style.opacity=1;clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,2600);};

/* ---------- three.js сцена (как webviewer: правосторонняя, Y-вверх) ---------- */
const view=$('view3d');
const scene=new THREE.Scene(); scene.background=new THREE.Color(0xeceff3);
const camera=new THREE.PerspectiveCamera(42, 1, 1, 100000);
const renderer=new THREE.WebGLRenderer({antialias:true});
view.appendChild(renderer.domElement);
const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.enableDamping=true; controls.dampingFactor=0.08;
scene.add(new THREE.AmbientLight(0xffffff,0.72));
const d1=new THREE.DirectionalLight(0xffffff,0.55); d1.position.set(1,2,2); scene.add(d1);
const d2=new THREE.DirectionalLight(0xffffff,0.30); d2.position.set(-2,1,-1); scene.add(d2);
let gPanels=new THREE.Group(), gHoles=new THREE.Group(), gHw=new THREE.Group(), gAux=new THREE.Group();
scene.add(gPanels,gHoles,gHw,gAux);
let panelMats=[], fitted=false;

function resize(){const w=view.clientWidth,h=view.clientHeight;
  camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);}
addEventListener('resize',resize);

function rebuild(v){
  [gPanels,gHoles,gHw,gAux].forEach(g=>{scene.remove(g);});
  gPanels=new THREE.Group();gHoles=new THREE.Group();gHw=new THREE.Group();gAux=new THREE.Group();
  scene.add(gPanels,gHoles,gHw,gAux); panelMats=[];
  const P=v.panels; if(!P.length) return;
  let bb={x0:1e9,x1:-1e9,y0:1e9,y1:-1e9,z0:1e9,z1:-1e9};
  P.forEach(p=>{bb.x0=Math.min(bb.x0,p.x1);bb.x1=Math.max(bb.x1,p.x2);
    bb.y0=Math.min(bb.y0,p.y1);bb.y1=Math.max(bb.y1,p.y2);
    bb.z0=Math.min(bb.z0,p.z1);bb.z1=Math.max(bb.z1,p.z2);});
  const W=bb.x1-bb.x0,H=bb.y1-bb.y0,D=bb.z1-bb.z0,R=Math.max(W,H,D);
  const TX=x=>x-bb.x0, TY=y=>y-bb.y0, TZ=z=>bb.z1-z;
  P.forEach((p,i)=>{
    const w=Math.max(p.x2-p.x1,1),h=Math.max(p.y2-p.y1,1),d=Math.max(p.z2-p.z1,1);
    const geo=new THREE.BoxGeometry(w,h,d);
    const col=new THREE.Color(v.colors[p.type]||'#c9a06a'); col.offsetHSL(0,0,((i%5)-2)*0.009);
    const mat=new THREE.MeshLambertMaterial({color:col,side:THREE.DoubleSide});
    if($('cbXray').checked){mat.transparent=true;mat.opacity=0.2;mat.depthWrite=false;}
    panelMats.push(mat);
    const mesh=new THREE.Mesh(geo,mat);
    mesh.position.set(TX((p.x1+p.x2)/2),TY((p.y1+p.y2)/2),TZ((p.z1+p.z2)/2));
    gPanels.add(mesh);
    const e=new THREE.LineSegments(new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({color:0x5a4326}));
    e.position.copy(mesh.position); gPanels.add(e);
  });
  (v.holes||[]).forEach(hp=>{
    const m=new THREE.Mesh(new THREE.SphereGeometry(Math.max(hp.d/2,3),10,8),
      new THREE.MeshBasicMaterial({color:0x333333}));
    m.position.set(TX(hp.x),TY(hp.y),TZ(hp.z)); gHoles.add(m);});
  (v.hardware||[]).forEach(h=>{
    const w=Math.max(h.x2-h.x1,1),hh=Math.max(h.y2-h.y1,1),d=Math.max(h.z2-h.z1,1);
    const m=new THREE.Mesh(new THREE.BoxGeometry(w,hh,d),
      new THREE.MeshLambertMaterial({color:h.color||'#8f969e'}));
    m.position.set(TX((h.x1+h.x2)/2),TY((h.y1+h.y2)/2),TZ((h.z1+h.z2)/2)); gHw.add(m);});
  const ax=new THREE.AxesHelper(R*1.08); gAux.add(ax);
  const cv=document.createElement('canvas');cv.width=cv.height=256;
  const g2=cv.getContext('2d'),gr=g2.createRadialGradient(128,128,12,128,128,126);
  gr.addColorStop(0,'rgba(0,0,0,0.28)');gr.addColorStop(1,'rgba(0,0,0,0)');
  g2.fillStyle=gr;g2.fillRect(0,0,256,256);
  const sh=new THREE.Mesh(new THREE.PlaneGeometry(W*1.55,D*1.9),
    new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(cv),transparent:true,depthWrite:false}));
  sh.rotation.x=-Math.PI/2; sh.position.set(W/2,0.5,D/2); gAux.add(sh);
  gHoles.visible=$('cbHoles').checked; gHw.visible=$('cbHw').checked;
  if(!fitted){
    const c=new THREE.Vector3(W/2,H/2,D/2);
    const sphere=0.5*Math.sqrt(W*W+H*H+D*D), vfov=42*Math.PI/180;
    const hfov=2*Math.atan(Math.tan(vfov/2)*view.clientWidth/view.clientHeight);
    const dist=sphere/Math.sin(Math.min(vfov,hfov)/2)*1.12;
    const dir=new THREE.Vector3(0.62,0.42,0.92).normalize().multiplyScalar(dist);
    camera.position.copy(c).add(dir); controls.target.copy(c); controls.update(); fitted=true;
  }
}
$('cbHoles').onchange=e=>gHoles.visible=e.target.checked;
$('cbHw').onchange=e=>gHw.visible=e.target.checked;
$('cbXray').onchange=e=>{const on=e.target.checked;
  panelMats.forEach(m=>{m.transparent=on;m.opacity=on?0.2:1;m.depthWrite=!on;m.needsUpdate=true;});};
(function loop(){requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);})();

/* ---------- формы ← spec ---------- */
function fillForm(){
  const d=SPEC.dimensions||{},m=SPEC.materials||{},lg=SPEC.legs||{},gp=SPEC.gaps||{};
  $('f_w').value=d.width??'';$('f_d').value=d.depth??'';$('f_h').value=d.height??'';
  $('f_color').value=m.color??'';$('f_code').value=m.color_code??'';
  $('f_t').value=m.board_thickness??16;$('f_legs').value=lg.height??0;
  $('f_gap').value=gp.default??2;
  $('rawspec').value=JSON.stringify(SPEC,null,2);
  renderSections();
}
function renderSections(){
  const box=$('sections'); box.innerHTML='';
  const secs=SPEC.sections;
  if(!Array.isArray(secs)){$('fs_sections').style.display='none';return;}
  $('fs_sections').style.display='';
  secs.forEach((s,i)=>{
    const div=document.createElement('div'); div.className='sec';
    div.innerHTML=`
      <div class="row"><label>Тип</label>
        <select data-i="${i}" data-k="kind">
          ${['drawers','shelves','door','open'].map(k=>`<option ${s.kind===k?'selected':''}>${k}</option>`).join('')}
        </select>
        <button data-del="${i}" title="удалить">✕</button></div>
      <div class="row"><label>Ящиков</label><input type="number" min="0" data-i="${i}" data-k="drawers" value="${s.drawers??''}"></div>
      <div class="row"><label>Полок</label><input type="number" min="0" data-i="${i}" data-k="shelves" value="${s.shelves??''}"></div>
      <div class="row"><label>Дверей</label><input type="number" min="0" max="2" data-i="${i}" data-k="door" value="${s.door??''}"></div>
      <div class="row"><label>Доля шир.</label><input type="number" step="0.1" data-i="${i}" data-k="width_share" value="${s.width_share??''}"></div>`;
    box.appendChild(div);
  });
}
$('addSec').onclick=()=>{(SPEC.sections=SPEC.sections||[]).push({kind:'shelves',shelves:2});
  fillForm(); apply();};

/* spec ← формы */
function harvest(){
  SPEC.dimensions=SPEC.dimensions||{};SPEC.materials=SPEC.materials||{};
  SPEC.legs=SPEC.legs||{};SPEC.gaps=SPEC.gaps||{};
  const num=v=>v===''?undefined:Number(v);
  SPEC.dimensions.width=num($('f_w').value);SPEC.dimensions.depth=num($('f_d').value);
  SPEC.dimensions.height=num($('f_h').value);
  SPEC.materials.color=$('f_color').value;SPEC.materials.color_code=$('f_code').value;
  SPEC.materials.board_thickness=num($('f_t').value);
  SPEC.legs.height=num($('f_legs').value)||0;
  SPEC.gaps.default=num($('f_gap').value);
  $('rawspec').value=JSON.stringify(SPEC,null,2);
}
document.addEventListener('input',e=>{
  const t=e.target;
  if(t.dataset&&t.dataset.k!==undefined&&t.dataset.i!==undefined){
    const s=SPEC.sections[+t.dataset.i], k=t.dataset.k;
    const v=t.value===''?undefined:(k==='kind'?t.value:Number(t.value));
    if(v===undefined) delete s[k]; else s[k]=v;
    schedule(); return;
  }
  if(t.id&&t.id.startsWith('f_')){harvest();schedule();}
});
document.addEventListener('click',e=>{
  const del=e.target.dataset&&e.target.dataset.del;
  if(del!==undefined&&del!==null&&del!==''){SPEC.sections.splice(+del,1);fillForm();apply();}
});
$('applyRaw').onclick=()=>{try{SPEC=JSON.parse($('rawspec').value);fillForm();apply();}
  catch(err){toast('JSON: '+err.message,true);}};

/* ---------- генерация ---------- */
let timer=null, lastOk=false;
function schedule(){clearTimeout(timer);timer=setTimeout(apply,400);}
async function apply(){
  const r=await fetch('/api/generate',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json(); paint(p);
  if(drawOn) refreshDraw();
}
function paint(p){
  const B=$('badges'); B.innerHTML='';
  const names={schema:'схема',consistency:'встык',geometry:'геометрия',cfrn:'.cfrn',holes:'присадки'};
  let errs=[];
  for(const k of Object.keys(names)){
    const bad=(p.issues[k]||[]).length>0;
    B.insertAdjacentHTML('beforeend',`<span class="badge ${bad?'bad':''}">${names[k]}</span>`);
    if(bad) errs.push(...p.issues[k].slice(0,4).map(x=>`[${names[k]}] ${x}`));
  }
  $('errors').textContent=errs.join('\n');
  lastOk=p.ok; $('btnB3d').disabled=!p.ok;
  if(p.viewer){rebuild(p.viewer);
    const st=p.stats;
    $('stats').innerHTML=`<div><b>${st.n_panels}</b><span>деталей</span></div>
      <div><b>${st.n_holes}</b><span>присадок</span></div>
      <div><b>${st.dims.w}×${st.dims.d}×${st.dims.h}</b><span>${st.decor}</span></div>`;
    const tb=$('bom').querySelector('table');
    tb.innerHTML=(p.bom||[]).map(b=>`<tr><td>${b.slot}</td><td>${b.name}</td><td>${b.art}</td></tr>`).join('');
  }
}

/* ---------- чертёж ---------- */
let drawOn=false;
$('tab3d').onclick=()=>{drawOn=false;$('draw').style.display='none';
  $('tab3d').classList.add('on');$('tabDraw').classList.remove('on');};
$('tabDraw').onclick=()=>{drawOn=true;$('draw').style.display='block';
  $('tabDraw').classList.add('on');$('tab3d').classList.remove('on');refreshDraw();};
async function refreshDraw(){
  const r=await fetch('/api/techview',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  const p=await r.json(); $('draw').innerHTML=p.svg||('<i>'+(p.issues||[]).join('; ')+'</i>');
}

/* ---------- экспорт ---------- */
async function post(url){const r=await fetch(url,{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({spec:SPEC})});
  return await r.json();}
$('btnSave').onclick=async()=>{const p=await post('/api/save');
  toast(p.ok?('Сохранено: '+p.spec):('Ошибка: '+p.error),!p.ok);};
$('btnCfrn').onclick=async()=>{const p=await post('/api/export-cfrn');
  toast(p.ok?('.cfrn: '+p.cfrn):('Ошибка: '+p.error),!p.ok);};
$('btnB3d').onclick=async()=>{
  if(!lastOk){toast('Проверки не пройдены',true);return;}
  if(!confirm('Собрать .b3d через облако БАЗИС? Операция платная (~10₽).'))return;
  toast('Сборка в облаке…');
  const p=await post('/api/build-b3d');
  toast(p.ok?('Готов .b3d: '+p.b3d):('Ошибка: '+(p.error||'')),!p.ok);};

/* старт */
fillForm(); resize(); apply();
</script></body></html>"""
