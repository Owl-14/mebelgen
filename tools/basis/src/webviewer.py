"""Генерация самодостаточного 3D-просмотра модели (правосторонний, Y-вверх).

Зачем: производственный .cfrn/.b3d кодируется в РОДНОЙ левосторонней конвенции
БАЗИСа (глубина Z уходит ОТ зрителя) — иначе CfrnToB3d соберёт изделие зеркально.
Из-за левосторонности БАЗИС-Просмотр крутит модель «инверсно». Здесь — слой ПОКАЗА:
переводим в правостороннюю систему (three.js) с началом координат в углу модели,
Y-вверх и нормальной орбитой. Файл для станка НЕ трогаем — это только визуализация
(и заготовка веб-доставки клиенту, AKD-15).

Преобразование (левосторонняя БАЗИС → правосторонняя three.js), модель в
положительном октанте, угол в (0,0,0):
    X' = X - Xmin        (ширина, вправо)
    Y' = Y - Ymin        (высота, вверх)
    Z' = Zmax - Z        (глубина на зрителя; фронт модели → +Z)
"""

from __future__ import annotations

import json
from typing import Any

# цвета деталей по типу
_COLORS = {
    "door_front": "#c58a4a", "drawer_front": "#c58a4a", "facade": "#c58a4a",
    "shelf": "#d8b483", "bottom": "#b98a52", "top": "#b98a52", "plinth": "#8a6a40",
    "side_left": "#caa06a", "side_right": "#caa06a", "vertical_partition": "#caa06a",
    "back": "#9c7648",
    "drawer_bottom": "#d8b483", "drawer_side_left": "#caa06a",
    "drawer_side_right": "#caa06a", "drawer_back": "#9c7648",
}


def _panels(project: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in project.get("panels", []):
        pl = p.get("placement")
        if not isinstance(pl, dict):
            continue
        out.append({"name": p.get("name"), "type": p.get("type"),
                    "x1": pl["x1"], "x2": pl["x2"], "y1": pl["y1"],
                    "y2": pl["y2"], "z1": pl["z1"], "z2": pl["z2"]})
    return out


def _holes(project: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from .hardware import compute_drilling
        return [{"x": h["x"], "y": h["y"], "z": h["z"],
                 "d": h["diameter"], "purpose": h["purpose"]} for h in compute_drilling(project)]
    except Exception:
        return []


def project_to_viewer_html(project: dict[str, Any], *, title: str | None = None,
                           include_holes: bool = True) -> str:
    """HTML со встроенным three.js-просмотром модели в правильной (правосторонней) системе."""
    name = title or project.get("project_name") or project.get("furniture_type") or "Модель"
    panels = _panels(project)
    holes = _holes(project) if include_holes else []
    payload = {"panels": panels, "colors": _COLORS, "holes": holes}
    return _TEMPLATE.replace("__NAME__", _esc(name)).replace("__DATA__", json.dumps(payload, ensure_ascii=False))


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__ — 3D (Y вверх, правосторонний)</title>
<style>
  html,body{margin:0;height:100%;background:#eceff3;font-family:Segoe UI,Arial,sans-serif;overflow:hidden}
  #info{position:fixed;left:12px;top:10px;z-index:10;color:#333;font-size:13px;line-height:1.55;
        background:rgba(255,255,255,.85);padding:10px 12px;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.12)}
  #info b{color:#111}
  .k{display:inline-block;width:11px;height:11px;border-radius:2px;vertical-align:-1px;margin-right:5px}
  label{cursor:pointer;user-select:none}
  #hint{position:fixed;right:12px;bottom:10px;z-index:10;color:#666;font-size:12px;
        background:rgba(255,255,255,.8);padding:6px 10px;border-radius:6px}
</style></head><body>
<div id="info">
  <b>__NAME__</b><br>
  Правосторонняя система, <b>Y — вверх</b>. Вращение нормальное.<br>
  <span class="k" style="background:#e5484d"></span>X — ширина
  &nbsp;<span class="k" style="background:#2fa84f"></span>Y — высота
  &nbsp;<span class="k" style="background:#3b82f6"></span>Z — глубина (на зрителя)<br>
  <label><input type="checkbox" id="toggleHoles" checked> показывать присадки</label><br>
  <label><input type="checkbox" id="toggleXray"> прозрачный режим (присадки внутри)</label>
</div>
<div id="hint">ЛКМ — вращать · колесо — зум · ПКМ — панорама</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
const DATA = __DATA__;
const PANELS = DATA.panels, COLORS = DATA.colors, HOLES = DATA.holes || [];

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xeceff3);

let bb = {x0:1e9,x1:-1e9,y0:1e9,y1:-1e9,z0:1e9,z1:-1e9};
PANELS.forEach(p=>{bb.x0=Math.min(bb.x0,p.x1);bb.x1=Math.max(bb.x1,p.x2);
  bb.y0=Math.min(bb.y0,p.y1);bb.y1=Math.max(bb.y1,p.y2);
  bb.z0=Math.min(bb.z0,p.z1);bb.z1=Math.max(bb.z1,p.z2);});
const W=bb.x1-bb.x0, H=bb.y1-bb.y0, D=bb.z1-bb.z0, R=Math.max(W,H,D);

// левосторонняя БАЗИС → правосторонняя three.js, угол модели в (0,0,0)
const TX = x => x - bb.x0, TY = y => y - bb.y0, TZ = z => bb.z1 - z;

const panelMats=[];   // материалы деталей — для прозрачного (рентген) режима
PANELS.forEach((p,i)=>{
  const w=Math.max(p.x2-p.x1,1), h=Math.max(p.y2-p.y1,1), d=Math.max(p.z2-p.z1,1);
  const geo=new THREE.BoxGeometry(w,h,d);
  const col=new THREE.Color(COLORS[p.type]||'#c9a06a');
  col.offsetHSL(0,0,((i%5)-2)*0.009);      // лёгкая вариация тона — детали читаются
  const mat=new THREE.MeshLambertMaterial({color:col, side:THREE.DoubleSide});
  panelMats.push(mat);
  const mesh=new THREE.Mesh(geo,mat);
  mesh.position.set(TX((p.x1+p.x2)/2), TY((p.y1+p.y2)/2), TZ((p.z1+p.z2)/2));
  scene.add(mesh);
  const e=new THREE.LineSegments(new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({color:0x5a4326}));
  e.position.copy(mesh.position); scene.add(e);
});
// прозрачный режим: детали полупрозрачны, присадки видно насквозь (в т.ч. внутренние)
document.getElementById('toggleXray').addEventListener('change',e=>{
  const on=e.target.checked;
  panelMats.forEach(m=>{m.transparent=on; m.opacity=on?0.20:1.0; m.depthWrite=!on; m.needsUpdate=true;});
});

// присадки (то, что БАЗИС-Просмотр прячет): маленькие метки в точках сверления
const holeGroup=new THREE.Group();
HOLES.forEach(hp=>{
  const r=Math.max(hp.d/2, 3);
  const m=new THREE.Mesh(new THREE.SphereGeometry(r,10,8),
      new THREE.MeshBasicMaterial({color:0x333333}));
  m.position.set(TX(hp.x), TY(hp.y), TZ(hp.z));
  holeGroup.add(m);
});
scene.add(holeGroup);
document.getElementById('toggleHoles').addEventListener('change',e=>{holeGroup.visible=e.target.checked;});

const ax=new THREE.AxesHelper(R*1.08); scene.add(ax);   // оси из угла модели (0,0,0)

// контактная тень под моделью (AKD-7): мягкое пятно на «полу»
(function(){
  const cv=document.createElement('canvas'); cv.width=cv.height=256;
  const g=cv.getContext('2d'), gr=g.createRadialGradient(128,128,12,128,128,126);
  gr.addColorStop(0,'rgba(0,0,0,0.28)'); gr.addColorStop(1,'rgba(0,0,0,0)');
  g.fillStyle=gr; g.fillRect(0,0,256,256);
  const sh=new THREE.Mesh(new THREE.PlaneGeometry(W*1.55, D*1.9),
      new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(cv), transparent:true, depthWrite:false}));
  sh.rotation.x=-Math.PI/2; sh.position.set(W/2, 0.5, D/2); scene.add(sh);
})();

scene.add(new THREE.AmbientLight(0xffffff,0.72));
const d1=new THREE.DirectionalLight(0xffffff,0.55); d1.position.set(1,2,2); scene.add(d1);
const d2=new THREE.DirectionalLight(0xffffff,0.30); d2.position.set(-2,1,-1); scene.add(d2);

const center=new THREE.Vector3(W/2, H/2, D/2);
const FOV=42;
const camera=new THREE.PerspectiveCamera(FOV, innerWidth/innerHeight, 1, R*50);
// вписать модель: дистанция по габаритной сфере и вертикальному/горизонтальному FOV
const sphere=0.5*Math.sqrt(W*W+H*H+D*D);
const vfov=FOV*Math.PI/180, hfov=2*Math.atan(Math.tan(vfov/2)*innerWidth/innerHeight);
const dist=sphere/Math.sin(Math.min(vfov,hfov)/2)*1.12;
const dir=new THREE.Vector3(0.62,0.42,0.92); dir.normalize().multiplyScalar(dist);
camera.position.copy(center).add(dir);
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth,innerHeight); renderer.setPixelRatio(devicePixelRatio);
document.body.appendChild(renderer.domElement);

const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.target.copy(center); controls.enableDamping=true; controls.dampingFactor=0.08;
controls.update();

addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();
  renderer.setSize(innerWidth,innerHeight);});
(function loop(){requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);})();
</script></body></html>"""
