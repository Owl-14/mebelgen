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

from .decor_colors import build_palette, decor_label

# Палитра по умолчанию (когда декор не указан) — «обезличенное дерево»
_COLORS = {
    "door_front": "#c58a4a", "drawer_front": "#c58a4a", "facade": "#c58a4a",
    "shelf": "#d8b483", "bottom": "#b98a52", "top": "#b98a52", "plinth": "#8a6a40",
    "side_left": "#caa06a", "side_right": "#caa06a", "vertical_partition": "#caa06a",
    "back": "#9c7648", "screen": "#c58a4a",
    "drawer_bottom": "#d8b483", "drawer_side_left": "#caa06a",
    "drawer_side_right": "#caa06a", "drawer_back": "#9c7648",
    "_default": "#c9a06a", "_edge": "#5a4326",
}


def _article_decor(article: Any) -> str | None:
    """Артикул позиции базы → метка декора из её имени (для цвета показа)."""
    if not article:
        return None
    try:
        from .materials import by_article
        item = by_article(str(article))
        return decor_label(str(item["name"])) if item else None
    except Exception:
        return None


def _palette(project: dict[str, Any]) -> dict[str, str]:
    """Палитра типов из декоров проекта (корпус + фасады); без декора — дефолт.

    Слоты: materials.color / board_article — корпус; materials.facade_color /
    facade_article — фасады (не задано → как корпус).
    """
    m = project.get("materials") or {}
    carcass = m.get("color") or _article_decor(m.get("board_article"))
    facade = m.get("facade_color") or _article_decor(m.get("facade_article"))
    pal = build_palette(carcass, facade, default=_COLORS)
    return pal if pal is not None else _COLORS


def _panels(project: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in project.get("panels", []):
        pl = p.get("placement")
        if not isinstance(pl, dict):
            continue
        eb = p.get("edge_banding") or {}
        out.append({"name": p.get("name"), "type": p.get("type"),
                    "x1": pl["x1"], "x2": pl["x2"], "y1": pl["y1"],
                    "y2": pl["y2"], "z1": pl["z1"], "z2": pl["z2"],
                    "thickness": p.get("thickness"), "material": p.get("material"),
                    **({"swing": p["swing"]} if p.get("swing") else {}),
                    "edges": ", ".join(f"{k}:{v}" for k, v in eb.items() if v) or "—"})
    return out


def _holes(project: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from .hardware import compute_drilling
        return [{"x": h["x"], "y": h["y"], "z": h["z"], "panel": h.get("panel"),
                 "d": h["diameter"], "purpose": h["purpose"],
                 "depth": h.get("depth"), "axis": h.get("axis"), "dir": h.get("dir")}
                for h in compute_drilling(project)]
    except Exception:
        return []


def _hardware(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Видимые детали механизмов: направляющие, петли (hardware_geometry)."""
    try:
        from .hardware_geometry import compute_hardware_geometry
        return compute_hardware_geometry(project)
    except Exception:
        return []


def _openables(project: dict[str, Any], panels: list[dict[str, Any]],
               hardware: list[dict[str, Any]],
               holes: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Открывающиеся узлы для анимации: ящики (фасад+короб+полоз ящика, выезд по Z)
    и двери (поворот вокруг оси петель). Индексы — в массивы panels/hardware/holes.

    AKD-189: ручки и присадки, принадлежащие панелям узла, едут вместе с ним."""
    groups: list[dict[str, Any]] = []
    holes = holes or []
    box_types = ("drawer_bottom", "drawer_side_left", "drawer_side_right", "drawer_back")

    def _own_holes(panel_idxs: list[int]) -> list[int]:
        names = {str(panels[i].get("name")) for i in panel_idxs}
        return [k for k, h in enumerate(holes) if str(h.get("panel")) in names]

    for d in project.get("drawers", []):
        pos, dim = d.get("position") or {}, d.get("dimensions") or {}
        if not pos or not dim:
            continue
        bx1, bx2 = pos["x"] - 17, pos["x"] + dim["width"] + 17
        by1, by2 = pos["y"] - 2, pos["y"] + dim["height"] + 2
        bz1, bz2 = pos["z"] - 1, pos["z"] + dim["depth"] + 17
        idxs: list[int] = []
        facade = None
        for i, p in enumerate(panels):
            cx, cy, cz = (p["x1"] + p["x2"]) / 2, (p["y1"] + p["y2"]) / 2, (p["z1"] + p["z2"]) / 2
            if p["type"] in box_types and bx1 <= cx <= bx2 and by1 <= cy <= by2 and bz1 <= cz <= bz2:
                idxs.append(i)
            elif p["type"] == "drawer_front" and facade is None \
                    and p["x1"] - 20 <= pos["x"] <= p["x2"] + 20 and p["y1"] <= pos["y"] <= p["y2"]:
                facade = i
        if facade is None:
            continue
        fname = str(panels[facade].get("name"))
        hw = [j for j, h in enumerate(hardware)
              if (h["kind"] == "guide_drawer" and str(d.get("id")) in str(h.get("name")))
              or (h["kind"] == "handle" and str(h.get("name", "")).startswith(fname))]
        gpanels = sorted(set(idxs + [facade]))
        groups.append({"kind": "drawer", "id": str(d.get("id")),
                       "panels": gpanels, "hardware": hw, "holes": _own_holes(gpanels),
                       "travel": round(min(float(dim["depth"]) * 0.75, 380), 1)})

    from .hardware import door_hinge_side
    model_w = max((q["x2"] for q in panels), default=0)
    for i, p in enumerate(panels):
        if p["type"] != "door_front":
            continue
        # сторона петель — общий хелпер (положение секции/swing), AKD-223/224
        hinge = door_hinge_side(
            {"name": p.get("name"), "swing": p.get("swing"),
             "placement": {"x1": p["x1"], "x2": p["x2"]}}, model_w)
        hw = [j for j, h in enumerate(hardware)
              if h["kind"] in ("hinge_cup", "handle")
              and str(h.get("name", "")).startswith(str(p.get("name")))]
        g = {"kind": "door", "id": str(p.get("name") or f"door{i}"),
             "panels": [i], "hardware": hw, "holes": _own_holes([i]),
             "hinge": hinge, "az": max(p["z1"], p["z2"]), "swing": 100}
        if hinge in ("up", "down"):                    # откидная: ось X по кромке
            g["ay"] = p["y2"] if hinge == "up" else p["y1"]
            g["ax"] = 0
        else:
            g["ax"] = p["x1"] if hinge == "left" else p["x2"]
        groups.append(g)
    return groups


def project_to_viewer_html(project: dict[str, Any], *, title: str | None = None,
                           include_holes: bool = True) -> str:
    """HTML со встроенным three.js-просмотром модели в правильной (правосторонней) системе."""
    name = title or project.get("project_name") or project.get("furniture_type") or "Модель"
    payload = viewer_payload(project, include_holes=include_holes)
    return (_TEMPLATE
            .replace("__NAME__", _esc(name))
            .replace("__SCENE_JS__", SCENE_JS)
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False)))


def viewer_payload(project: dict[str, Any], *, include_holes: bool = True) -> dict[str, Any]:
    """Данные сцены: панели + присадки + фурнитура + открывающиеся узлы."""
    panels = _panels(project)
    holes = _holes(project) if include_holes else []
    hardware = _hardware(project)
    return {"panels": panels, "colors": _palette(project), "holes": holes,
            "hardware": hardware,
            "openables": _openables(project, panels, hardware, holes)}


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ движок сцены
# Общий для standalone-вьювера и Studio. Правосторонняя система (Y-вверх),
# анимация открытия ящиков/дверей (клик по узлу), направляющие — C-профиль
# с кареткой, чашки петель — цилиндры.
SCENE_JS = r"""
function MebelScene(container){
  const scene=new THREE.Scene(); scene.background=new THREE.Color(0xeceff3);
  const FOV=42;
  const camera=new THREE.PerspectiveCamera(FOV,1,1,100000);
  const renderer=new THREE.WebGLRenderer({antialias:true});
  renderer.setPixelRatio(devicePixelRatio); container.appendChild(renderer.domElement);
  const controls=new THREE.OrbitControls(camera,renderer.domElement);
  controls.enableDamping=true; controls.dampingFactor=0.08;
  scene.add(new THREE.AmbientLight(0xffffff,0.72));
  const L1=new THREE.DirectionalLight(0xffffff,0.55); L1.position.set(1,2,2); scene.add(L1);
  const L2=new THREE.DirectionalLight(0xffffff,0.30); L2.position.set(-2,1,-1); scene.add(L2);

  let world=null, holeGroup=null, hwVisible=true, fitted=false;
  let panelMats=[], groups=[];      // groups: {node,kind,travel,sign,swing,t,target}
  let panelMeshes=[], selectedPi=null, PANELS_REF=[];   // выбор детали (AKD-120)
  let dimGroup=null, dimsOn=true;                       // размерные линии (AKD-125)

  function size(){const w=container.clientWidth||innerWidth,h=container.clientHeight||innerHeight;
    camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);}
  addEventListener('resize',size);

  function edge(mesh,color){const e=new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry),
      new THREE.LineBasicMaterial({color:color||0x5a4326}));
    e.position.copy(mesh.position); e.rotation.copy(mesh.rotation); return e;}

  function box(w,h,d,color){return new THREE.Mesh(new THREE.BoxGeometry(Math.max(w,0.8),
      Math.max(h,0.8),Math.max(d,0.8)), new THREE.MeshLambertMaterial({color:color}));}

  // --- процедурная текстура декора (AKD-124): волокна от базового цвета ---
  let texOn=true; const texCache=new Map();
  function woodTexture(hex, vertical){
    const key=hex+(vertical?'|v':'|h');
    if(texCache.has(key)) return texCache.get(key);
    const cv=document.createElement('canvas'); cv.width=cv.height=256;
    const ctx=cv.getContext('2d');
    ctx.fillStyle=hex; ctx.fillRect(0,0,256,256);
    const c=new THREE.Color(hex), hsl={}; c.getHSL(hsl);
    let seed=0; for(const ch of hex) seed=(seed*31+ch.charCodeAt(0))>>>0;
    const rand=()=>{seed=(seed*1664525+1013904223)>>>0; return seed/4294967296;};
    if(hsl.s>0.09){                                   // древесные — волокна
      for(let i=0;i<70;i++){
        const y=rand()*256, w=0.8+rand()*3.2, dl=(rand()-0.5)*0.22;
        const col=new THREE.Color().setHSL(hsl.h,
          Math.min(hsl.s*(0.85+rand()*0.3),1),
          Math.min(Math.max(hsl.l+dl,0.03),0.97));
        ctx.strokeStyle='#'+col.getHexString(); ctx.globalAlpha=0.5; ctx.lineWidth=w;
        ctx.beginPath(); ctx.moveTo(-4,y);
        for(let x=0;x<=260;x+=12)
          ctx.lineTo(x,y+Math.sin(x*0.018+i*1.7)*2.0+(rand()-0.5)*1.2);
        ctx.stroke();
      }
      for(let i=0;i<2;i++){                           // редкие «сучки», деликатно
        const x=rand()*256,y=rand()*256,r=1.5+rand()*2;
        ctx.globalAlpha=0.12; ctx.fillStyle='#000';
        ctx.beginPath(); ctx.ellipse(x,y,r*2.2,r,0,0,7); ctx.fill();
      }
    }
    ctx.globalAlpha=0.045;                            // лёгкий шум для всех
    for(let i=0;i<900;i++){ctx.fillStyle=(i%2)?'#000':'#fff';
      ctx.fillRect(rand()*256,rand()*256,1.5,1.5);}
    ctx.globalAlpha=1;
    const tex=new THREE.CanvasTexture(cv);
    tex.wrapS=tex.wrapT=THREE.RepeatWrapping;
    if(vertical){tex.center.set(0.5,0.5); tex.rotation=Math.PI/2;}
    texCache.set(key,tex); return tex;
  }
  // --- 3D-метизы по присадкам (AKD-147): как в веб-вьювере БАЗИС ---
  const _MTL={steel:0x9aa0a6, dark:0x646a71, chrome:0xd2d6da, brass:0xc9b37e,
              wood:0xd8b483, hole:0x2b2620};
  const _mtlCache={};
  function fMtl(c){return _mtlCache[c]||(_mtlCache[c]=new THREE.MeshLambertMaterial({color:c}));}
  function cyl(d,len,color){
    return new THREE.Mesh(new THREE.CylinderGeometry(d/2,d/2,Math.max(len,0.6),12),fMtl(color));}
  function orient(m,axis){                       // цилиндр Y → вдоль оси сверления
    if(axis==='x') m.rotation.z=Math.PI/2;
    else if(axis==='z') m.rotation.x=Math.PI/2;
    return m;}
  function alongAxis(v,axis,k){                  // сдвиг вдоль оси на k
    if(axis==='x') v.x+=k; else if(axis==='z') v.z+=k; else v.y+=k; return v;}
  function fastenerGroup(hp){
    // hp: {d, depth, axis, dir, purpose}; сцена: мировой Z инвертирован (TZ)
    const g=new THREE.Group();
    const axis=hp.axis||'z', dir=(axis==='z'?-1:1)*(hp.dir||1);
    const depth=hp.depth||12, pu=hp.purpose||'';
    const add=(mesh,off)=>{orient(mesh,axis);
      alongAxis(mesh.position,axis,off); g.add(mesh);};
    // отверстие: тёмный цилиндр Ø×глубина, утопленный вглубь
    add(cyl(hp.d,depth,_MTL.hole),dir*depth/2);
    if(pu.includes('конфирмат')){
      add(cyl(7,50,_MTL.steel),dir*25);
      add(cyl(10,3.5,_MTL.steel),dir*1.2);                 // головка
    }else if(pu.includes('саморез')||pu.includes('планка')){
      add(cyl(3.5,16,_MTL.dark),dir*8);
      add(cyl(7,2,_MTL.dark),dir*0.8);
    }else if(pu.includes('гвоздь')){
      add(cyl(1.8,25,_MTL.steel),dir*12);
      add(cyl(3.5,1,_MTL.steel),dir*0.4);
    }else if(pu.includes('шкант')){
      if(pu.includes('торец')) add(cyl(8,30,_MTL.wood),dir*10);  // одно тело на пару отверстий
    }else if(pu.includes('чашка Ø15')){
      add(cyl(15,13,_MTL.brass),dir*6.5);
    }else if(pu.includes('шток')){
      add(cyl(7,depth,_MTL.steel),dir*depth/2);
    }else if(pu.includes('полкодержатель')){
      add(cyl(5,10,_MTL.chrome),dir*5);
      add(cyl(7,5,_MTL.chrome),dir*-2.5);                  // опорный носик наружу
    }else if(pu.includes('ручка (винт)')){
      add(cyl(4,25,_MTL.chrome),dir*12);
    }else if(pu.includes('направляющая')){
      add(cyl(3.5,12,_MTL.dark),dir*6);
    }else if(pu.includes('замок')){
      add(cyl(18,depth,_MTL.chrome),dir*depth/2);
      add(cyl(22,2,_MTL.chrome),dir*1);
    }
    return g;
  }

  // --- разнесённый вид (AKD-126): смещение деталей по типу ---
  const _EXPL={door_front:[0,0,1],drawer_front:[0,0,1],facade:[0,0,1],screen:[0,0,1],
    back:[0,0,-1],drawer_back:[0,0,-.6],top:[0,1,0],bottom:[0,-1,0],
    shelf:[0,.45,0],plinth:[0,-.5,0],side_left:[-1,0,0],side_right:[1,0,0],
    drawer_side_left:[-.55,0,0],drawer_side_right:[.55,0,0],drawer_bottom:[0,-.45,0]};
  let explodeT=0, explodeR=600;
  function setExplode(t){
    explodeT=Math.min(Math.max(t,0),1);
    const k=explodeT*explodeR*0.45;
    panelMeshes.forEach((m,i)=>{
      if(!m||!m.userData.basePos) return;
      const d=_EXPL[(PANELS_REF[i]||{}).type]||[0,0,0];
      m.position.set(m.userData.basePos.x+d[0]*k,
                     m.userData.basePos.y+d[1]*k,
                     m.userData.basePos.z+d[2]*k);   // фронт модели уже +Z сцены
      const e=m.userData.edgeObj; if(e) e.position.copy(m.position);
    });
  }

  // --- размерные линии W×D×H (AKD-125) ---
  function dimLabel(text,R){
    const cv=document.createElement('canvas'); cv.width=256; cv.height=72;
    const g=cv.getContext('2d');
    g.font='600 44px Segoe UI,Arial'; g.textAlign='center'; g.textBaseline='middle';
    g.fillStyle='rgba(236,239,243,0.92)';
    const w=g.measureText(text).width+26;
    g.fillRect(128-w/2,8,w,56);
    g.fillStyle='#1a4fa0'; g.fillText(text,128,38);
    const sp=new THREE.Sprite(new THREE.SpriteMaterial(
      {map:new THREE.CanvasTexture(cv),depthTest:false,transparent:true}));
    sp.scale.set(R*0.20,R*0.056,1); sp.renderOrder=9; return sp;
  }
  function dimLine(a,b,R){
    const g=new THREE.Group();
    const mat=new THREE.LineBasicMaterial({color:0x1a4fa0});
    const v=new THREE.Vector3().subVectors(b,a), n=v.clone().normalize();
    // перпендикуляр для засечек
    const up=Math.abs(n.y)>0.9?new THREE.Vector3(1,0,0):new THREE.Vector3(0,1,0);
    const t=new THREE.Vector3().crossVectors(n,up).normalize().multiplyScalar(R*0.014);
    const pts=[a,b];
    g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),mat));
    for(const p of [a,b]){
      g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(
        [p.clone().add(t),p.clone().sub(t)]),mat));
    }
    const label=dimLabel(`${Math.round(v.length())}`,R);
    label.position.copy(a).add(v.multiplyScalar(0.5)).add(t.clone().multiplyScalar(3));
    g.add(label);
    return g;
  }
  function buildDims(W,H,D,R){
    const g=new THREE.Group();
    const o=Math.max(R*0.055,28);
    g.add(dimLine(new THREE.Vector3(0,0,D+o),new THREE.Vector3(W,0,D+o),R));      // ширина
    g.add(dimLine(new THREE.Vector3(W+o,0,D),new THREE.Vector3(W+o,0,0),R));      // глубина
    g.add(dimLine(new THREE.Vector3(W+o,0,D+o),new THREE.Vector3(W+o,H,D+o),R));  // высота
    g.visible=dimsOn;
    return g;
  }

  function applyTexture(mesh){
    const m=mesh.material, u=mesh.userData;
    if(texOn&&u.baseHex){
      const base=woodTexture(u.baseHex,!!u.grainV);
      const t=base.clone(); t.needsUpdate=true;        // общая канва, свой repeat
      const k=Math.max(1,(u.longDim||400)/700);        // масштаб волокон под деталь
      t.repeat.set(k,k);
      m.map=t; m.color.set('#ffffff');
    }else{
      m.map=null;
      if(u.baseColor) m.color.copy(u.baseColor);
    }
    m.needsUpdate=true;
  }

  // Направляющая как направляющая: C-профиль (стенка+полки) + каретка + стопор.
  function buildGuide(h,TX,TY,TZ,parent,tagGi){
    const gx1=h.x1,gx2=h.x2,gy1=h.y1,gy2=h.y2,gz1=h.z1,gz2=h.z2;
    const W=gx2-gx1,H=gy2-gy1,L=gz2-gz1;
    const left=/left/.test(h.name||'');
    const isCorpus=h.kind==='guide_corpus';
    // стенка профиля: у корпуса — на грани корпуса, у ящика — на грани короба
    const webAtX1 = isCorpus ? left : !left;
    const cx=(gx1+gx2)/2, cy=(gy1+gy2)/2, czBack=TZ((gz1+gz2)/2);
    const add=(m)=>{m.userData.gi=tagGi; m.userData.hwpart=true; parent.add(m);
      const e=edge(m,0x4a5057); e.userData.hwpart=true; parent.add(e);};
    const web=box(1.7,H,L,h.color); web.position.set(TX(webAtX1?gx1+0.85:gx2-0.85),TY(cy),czBack); add(web);
    const fl=Math.min(3.4,H/4);
    const ft=box(W,fl,L,h.color); ft.position.set(TX(cx),TY(gy2-fl/2),czBack); add(ft);
    const fb=box(W,fl,L,h.color); fb.position.set(TX(cx),TY(gy1+fl/2),czBack); add(fb);
    if(isCorpus){ // каретка (шариковая обойма) внутри профиля
      const car=box(W*0.55,H*0.5,L*0.62,'#6d747c');
      car.position.set(TX(cx),TY(cy),TZ(gz1+L*0.42)); add(car);
    }
    const cap=box(W,H,2.6,'#2f3338');   // пластиковый стопор на фронте
    cap.position.set(TX(cx),TY(cy),TZ(gz1+1.3)); add(cap);
  }

  function buildHw(h,TX,TY,TZ,parent,tagGi){
    if(h.kind==='guide_corpus'||h.kind==='guide_drawer'){buildGuide(h,TX,TY,TZ,parent,tagGi);return;}
    let m;
    if(h.kind==='hinge_cup'){ // чашка Ø35 — цилиндр
      const r=(h.x2-h.x1)/2;
      m=new THREE.Mesh(new THREE.CylinderGeometry(r,r,h.z2-h.z1,22),
        new THREE.MeshLambertMaterial({color:h.color||'#666d75'}));
      m.rotation.x=Math.PI/2;
    } else {
      m=box(h.x2-h.x1,h.y2-h.y1,h.z2-h.z1,h.color||'#8f969e');
    }
    m.position.set(TX((h.x1+h.x2)/2),TY((h.y1+h.y2)/2),TZ((h.z1+h.z2)/2));
    m.userData.gi=tagGi; m.userData.hwpart=true; parent.add(m);
    if(h.kind!=='hinge_cup'){const e=edge(m,0x4a5057); e.userData.hwpart=true; parent.add(e);}
  }

  function setPayload(DATA,opts){
    opts=opts||{};
    if(world) scene.remove(world);
    world=new THREE.Group(); scene.add(world);
    panelMats=[]; groups=[]; panelMeshes=[]; selectedPi=null;
    PANELS_REF=DATA.panels||[];
    const PANELS=DATA.panels||[], COLORS=DATA.colors||{}, HOLES=DATA.holes||[];
    const HW=DATA.hardware||[], OPEN=DATA.openables||[];
    if(!PANELS.length){renderer.render(scene,camera);return;}
    let bb={x0:1e9,x1:-1e9,y0:1e9,y1:-1e9,z0:1e9,z1:-1e9};
    PANELS.forEach(p=>{bb.x0=Math.min(bb.x0,p.x1);bb.x1=Math.max(bb.x1,p.x2);
      bb.y0=Math.min(bb.y0,p.y1);bb.y1=Math.max(bb.y1,p.y2);
      bb.z0=Math.min(bb.z0,p.z1);bb.z1=Math.max(bb.z1,p.z2);});
    const W=bb.x1-bb.x0,H=bb.y1-bb.y0,D=bb.z1-bb.z0,R=Math.max(W,H,D);
    // левосторонняя БАЗИС → правосторонняя three.js, угол модели в (0,0,0)
    const TX=x=>x-bb.x0, TY=y=>y-bb.y0, TZ=z=>bb.z1 - z;

    // владельцы: панель/фурнитура/присадка → индекс открывающегося узла
    const ownP={}, ownH={}, ownHole={};
    OPEN.forEach((o,gi)=>{(o.panels||[]).forEach(i=>ownP[i]=gi);
                          (o.hardware||[]).forEach(j=>ownH[j]=gi);
                          (o.holes||[]).forEach(k=>ownHole[k]=gi);});
    // группы-узлы: ящик — трансляция по Z; дверь — поворот вокруг оси петель
    // (вертикальной left/right или горизонтальной up/down — AKD-224)
    OPEN.forEach(o=>{
      const node=new THREE.Group();
      const vert=(o.hinge==='up'||o.hinge==='down');
      if(o.kind==='door'){
        if(vert) node.position.set(0,TY(o.ay||0),TZ(o.az));
        else     node.position.set(TX(o.ax),0,TZ(o.az));
      }
      world.add(node);
      groups.push({node,kind:o.kind,travel:o.travel||300,axis:vert?'x':'y',
                   sign:(o.hinge==='right'||o.hinge==='down')?1:-1,
                   swing:(o.swing||100)*Math.PI/180,
                   t:0,target:0,
                   pivot:{x:TX(o.ax||0),y:TY(o.ay||0),z:TZ(o.az||0),vert}});
    });
    const holder=gi=>gi===undefined?world:groups[gi].node;
    const place=(m,gi)=>{ if(gi!==undefined&&groups[gi].kind==='door'){
        const pv=groups[gi].pivot;
        if(pv.vert){m.position.y-=pv.y; m.position.z-=pv.z;}
        else{m.position.x-=pv.x; m.position.z-=pv.z;}} };

    PANELS.forEach((p,i)=>{
      const gi=ownP[i];
      const w=Math.max(p.x2-p.x1,1),h=Math.max(p.y2-p.y1,1),d=Math.max(p.z2-p.z1,1);
      const col=new THREE.Color(COLORS[p.type]||COLORS._default||'#c9a06a'); col.offsetHSL(0,0,((i%5)-2)*0.009);
      const mat=new THREE.MeshLambertMaterial({color:col,side:THREE.DoubleSide});
      panelMats.push(mat);
      const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat);
      mesh.position.set(TX((p.x1+p.x2)/2),TY((p.y1+p.y2)/2),TZ((p.z1+p.z2)/2));
      if(gi!==undefined) mesh.userData.gi=gi;
      mesh.userData.pi=i; panelMeshes[i]=mesh;
      // текстура декора: базовый цвет типа + направление волокон по ориентации
      mesh.userData.baseHex='#'+col.getHexString();
      mesh.userData.baseColor=col.clone();
      mesh.userData.grainV=(w<=h&&w<=d)||(d<=w&&d<=h);   // боковины/фасады — вертикально
      mesh.userData.longDim=Math.max(w,h,d);
      applyTexture(mesh);
      place(mesh,gi); holder(gi).add(mesh);
      const e=edge(mesh,COLORS._edge); holder(gi).add(e);
      mesh.userData.edgeObj=e;
      mesh.userData.basePos=mesh.position.clone();
    });
    HW.forEach((h,j)=>{
      const gi=ownH[j];
      // buildHw кладёт метки gi на меши; для двери — сдвиг в систему узла-петли
      const tmp=new THREE.Group();
      buildHw(h,TX,TY,TZ,tmp,gi);
      tmp.children.forEach(m=>{ if(gi!==undefined&&groups[gi].kind==='door'){
          const pv=groups[gi].pivot;
          if(pv.vert){m.position.y-=pv.y; m.position.z-=pv.z;}
          else{m.position.x-=pv.x; m.position.z-=pv.z;}} });
      while(tmp.children.length) holder(gi).add(tmp.children[0]);
    });
    holeGroup=new THREE.Group();
    HOLES.forEach((hp,k)=>{
      const g=fastenerGroup(hp);                  // отверстие с глубиной + метиз
      g.position.set(TX(hp.x),TY(hp.y),TZ(hp.z));
      g.userData.holepart=1;
      const gi=ownHole[k];                        // присадки узла едут с ним (AKD-189)
      if(gi!==undefined){
        if(groups[gi].kind==='door'){
          const pv=groups[gi].pivot;
          if(pv.vert){g.position.y-=pv.y; g.position.z-=pv.z;}
          else{g.position.x-=pv.x; g.position.z-=pv.z;}
        }
        groups[gi].node.add(g);
      } else holeGroup.add(g);});
    world.add(holeGroup);
    world.add(new THREE.AxesHelper(R*1.08));
    dimGroup=buildDims(W,H,D,R); world.add(dimGroup);
    explodeR=R; if(explodeT>0) setExplode(explodeT);
    const cv=document.createElement('canvas');cv.width=cv.height=256;
    const g2=cv.getContext('2d'),gr=g2.createRadialGradient(128,128,12,128,128,126);
    gr.addColorStop(0,'rgba(0,0,0,0.28)');gr.addColorStop(1,'rgba(0,0,0,0)');
    g2.fillStyle=gr;g2.fillRect(0,0,256,256);
    const sh=new THREE.Mesh(new THREE.PlaneGeometry(W*1.55,D*1.9),
      new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(cv),transparent:true,depthWrite:false}));
    sh.rotation.x=-Math.PI/2; sh.position.set(W/2,0.5,D/2); world.add(sh);
    api.setHw(hwVisible);
    if(!fitted||opts.refit){
      size();
      const c=new THREE.Vector3(W/2,H/2,D/2);
      const sphere=0.5*Math.sqrt(W*W+H*H+D*D), vfov=FOV*Math.PI/180;
      const hfov=2*Math.atan(Math.tan(vfov/2)*(container.clientWidth||innerWidth)/(container.clientHeight||innerHeight));
      const dist=sphere/Math.sin(Math.min(vfov,hfov)/2)*1.12;
      const dir=new THREE.Vector3(0.62,0.42,0.92).normalize().multiplyScalar(dist);
      camera.position.copy(c).add(dir); controls.target.copy(c); controls.update(); fitted=true;
    }
  }

  // клик по узлу — открыть/закрыть (отличаем от вращения по сдвигу мыши)
  const ray=new THREE.Raycaster(), mv=new THREE.Vector2();
  let downAt=null;

  // --- Shift+drag: перемещение детали (AKD-121) ---
  let dragState=null;
  const SNAP_MM=8;    // радиус магнита к граням соседних деталей

  // Магнит: по каждой движущейся оси прилипаем к ближайшей грани соседа —
  // торец-к-пласти или заподлицо. Работает в МИРОВЫХ осях БАЗИС
  // (сцена Z инвертирована), правит d НА МЕСТЕ (d — сценовый вектор).
  function magnetSnap(pi,d){
    const p=PANELS_REF[pi]; if(!p) return;
    const dw={x:d.x,y:d.y,z:-d.z};                   // сцена → мир
    const axes=['x','y','z'];
    for(const a of axes){
      if(!dw[a]) continue;
      const [t1,t2]=axes.filter(t=>t!==a);
      const lo=p[a+'1']+dw[a], hi=p[a+'2']+dw[a];
      let best=null;
      for(const q of PANELS_REF){
        if(!q||q===p) continue;
        // сосед должен пересекаться с деталью по обеим поперечным осям (≥5 мм)
        const ok=[t1,t2].every(t=>{
          const l=Math.min(p[t+'2']+ (dw[t]||0), q[t+'2'])
                - Math.max(p[t+'1']+ (dw[t]||0), q[t+'1']);
          return l>=5;});
        if(!ok) continue;
        for(const g of [q[a+'1'],q[a+'2']]){
          for(const e of [lo,hi]){
            const adj=g-e;
            if(Math.abs(adj)<=SNAP_MM&&(best===null||Math.abs(adj)<Math.abs(best)))
              best=adj;
          }
        }
      }
      if(best!==null) dw[a]=Math.round(dw[a]+best);
    }
    d.set(dw.x,dw.y,-dw.z);                          // мир → сцена
  }
  function _setMv(e){
    const r=renderer.domElement.getBoundingClientRect();
    mv.x=((e.clientX-r.left)/r.width)*2-1; mv.y=-((e.clientY-r.top)/r.height)*2+1;
  }
  renderer.domElement.addEventListener('pointerdown',e=>{
    if(!e.shiftKey) return;
    _setMv(e); ray.setFromCamera(mv,camera);
    for(const h of ray.intersectObjects(scene.children,true)){
      let o=h.object, pi;
      while(o){ if(o.userData&&o.userData.pi!==undefined){pi=o.userData.pi;break;} o=o.parent; }
      if(pi===undefined) continue;
      const plane=new THREE.Plane();
      plane.setFromNormalAndCoplanarPoint(
        camera.getWorldDirection(new THREE.Vector3()), h.point);
      dragState={pi, plane, start:h.point.clone(), mesh:panelMeshes[pi],
                 basePos:panelMeshes[pi].position.clone(), delta:new THREE.Vector3()};
      controls.enabled=false;
      selectPanel(pi);
      e.stopImmediatePropagation(); e.preventDefault();
      break;
    }
  }, true);
  renderer.domElement.addEventListener('pointermove',e=>{
    if(!dragState) return;
    _setMv(e); ray.setFromCamera(mv,camera);
    const p=new THREE.Vector3();
    if(ray.ray.intersectPlane(dragState.plane,p)){
      const d=p.sub(dragState.start);
      d.set(Math.round(d.x),Math.round(d.y),Math.round(d.z));   // шаг 1 мм
      // снап к доминантной оси: слабые компоненты (<35% максимума) убираем,
      // чтобы «тащу вверх» не давало паразитных сдвигов по X/Z
      const m=Math.max(Math.abs(d.x),Math.abs(d.y),Math.abs(d.z));
      if(m>0){['x','y','z'].forEach(a=>{if(Math.abs(d[a])<m*0.35) d[a]=0;});}
      magnetSnap(dragState.pi,d);                    // магнит к граням соседей
      dragState.delta.copy(d);
      dragState.mesh.position.copy(dragState.basePos).add(d);
      const eo=dragState.mesh.userData.edgeObj;
      if(eo) eo.position.copy(dragState.mesh.position);
    }
  });
  addEventListener('pointerup',()=>{
    if(!dragState) return;
    controls.enabled=true;
    const st=dragState; dragState=null;
    const d=st.delta, panel=PANELS_REF[st.pi];
    if(panel&&(d.x||d.y||d.z)&&api.onTransform){
      api.onTransform(panel.name,[d.x,d.y,-d.z]);   // сцена→мир БАЗИС: Z инвертирован
    }else{
      st.mesh.position.copy(st.basePos);
      const eo=st.mesh.userData.edgeObj; if(eo) eo.position.copy(st.basePos);
    }
  });
  renderer.domElement.addEventListener('pointerdown',e=>{downAt=[e.clientX,e.clientY,Date.now()];});
  renderer.domElement.addEventListener('pointerup',e=>{
    if(!downAt) return;
    const dx=e.clientX-downAt[0],dy=e.clientY-downAt[1],dt=Date.now()-downAt[2]; downAt=null;
    if(Math.hypot(dx,dy)>5||dt>400) return;
    const r=renderer.domElement.getBoundingClientRect();
    mv.x=((e.clientX-r.left)/r.width)*2-1; mv.y=-((e.clientY-r.top)/r.height)*2+1;
    ray.setFromCamera(mv,camera);
    for(const hit of ray.intersectObjects(scene.children,true)){
      let o=hit.object, gi, pi;
      while(o){
        if(gi===undefined&&o.userData&&o.userData.gi!==undefined) gi=o.userData.gi;
        if(pi===undefined&&o.userData&&o.userData.pi!==undefined) pi=o.userData.pi;
        o=o.parent;
      }
      if(pi!==undefined) selectPanel(pi===selectedPi?null:pi);   // повторный клик — снять
      if(gi!==undefined){const g=groups[gi]; g.target=g.target>0.5?0:1;}
      if(pi!==undefined||gi!==undefined) return;
      if(hit.object.type==='Mesh') return;   // клик по фурнитуре/прочему — ничего
    }
    selectPanel(null);                        // клик в пустоту — снять выбор
  });

  function selectPanel(pi){
    if(selectedPi!==null&&panelMeshes[selectedPi])
      panelMeshes[selectedPi].material.emissive.setHex(0x000000);
    selectedPi=pi;
    if(pi!==null&&panelMeshes[pi])
      panelMeshes[pi].material.emissive.setHex(0x2b62c4);
    if(api.onSelect) api.onSelect(pi===null?null:{index:pi,panel:PANELS_REF[pi]});
  }

  (function loop(){
    requestAnimationFrame(loop);
    groups.forEach(g=>{
      g.t+=(g.target-g.t)*0.14;
      if(Math.abs(g.target-g.t)<0.002) g.t=g.target;
      if(g.kind==='drawer'){g.node.position.z=g.t*g.travel;}
      else if(g.axis==='x'){g.node.rotation.x=g.sign*g.t*g.swing;}
      else{g.node.rotation.y=g.sign*g.t*g.swing;}
    });
    controls.update(); renderer.render(scene,camera);
  })();

  const api={
    setPayload,
    setXray(on){panelMats.forEach(m=>{m.transparent=on;m.opacity=on?0.2:1;m.depthWrite=!on;m.needsUpdate=true;});},
    setHoles(on){if(holeGroup)holeGroup.visible=on;
      world&&world.traverse(o=>{ if(o.userData&&o.userData.holepart) o.visible=on; });},
    setHw(on){hwVisible=on;
      world&&world.traverse(o=>{ if(o.userData&&o.userData.hwpart) o.visible=on; });},
    openAll(){groups.forEach(g=>g.target=1);},
    closeAll(){groups.forEach(g=>g.target=0);},
    select:selectPanel,
    getSelected(){return selectedPi;},
    onSelect:null,                     // колбэк ({index,panel}|null)
    onTransform:null,                  // колбэк (name, [dx,dy,dz] в мире БАЗИС)
    setTextures(on){texOn=on; panelMeshes.forEach(m=>m&&applyTexture(m));},
    setDims(on){dimsOn=on; if(dimGroup) dimGroup.visible=on;},
    setExplode,
    resize:size,
  };
  size();
  return api;
}
"""


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
  <label><input type="checkbox" id="toggleHw" checked> фурнитура (направляющие, петли)</label><br>
  <label><input type="checkbox" id="toggleXray"> прозрачный режим (механизмы внутри)</label><br>
  <button id="btnOpen" style="margin-top:6px">Открыть всё</button>
  <button id="btnClose">Закрыть всё</button>
</div>
<div id="hint">клик по фасаду/ящику — открыть · ЛКМ — вращать · колесо — зум · ПКМ — панорама</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
__SCENE_JS__
const DATA = __DATA__;
const stage = document.createElement('div');
stage.style.cssText='position:fixed;inset:0';
document.body.insertBefore(stage, document.body.firstChild);
const scene3d = MebelScene(stage);
scene3d.setPayload(DATA);
document.getElementById('toggleHoles').addEventListener('change',e=>scene3d.setHoles(e.target.checked));
document.getElementById('toggleHw').addEventListener('change',e=>scene3d.setHw(e.target.checked));
document.getElementById('toggleXray').addEventListener('change',e=>scene3d.setXray(e.target.checked));
document.getElementById('btnOpen').addEventListener('click',()=>scene3d.openAll());
document.getElementById('btnClose').addEventListener('click',()=>scene3d.closeAll());
</script></body></html>"""
