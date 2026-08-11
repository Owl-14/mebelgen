"""Browser-side presentation PDF for one exact client-review revision.

The PDF is assembled from two raster A4 landscape pages in the browser.  Both
3D images are rendered by the existing MebelScene instance contract, so this
feature does not introduce a second renderer or expose ParamSpec/production
data.  The full review revision is printed on every page.
"""

PRESENTATION_JS = r"""
const PRESENTATION_PAGE={width:1754,height:1240,pdfWidth:841.89,pdfHeight:595.28};
let presentationScene=null;

function presentationWaitFrames(count=2){return new Promise(resolve=>{const step=()=>{
  if(--count<=0)resolve();else requestAnimationFrame(step);};requestAnimationFrame(step);});}

function presentationImage(source){return new Promise((resolve,reject)=>{const image=new Image();
  image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('Не удалось подготовить изображение для PDF'));
  image.src=source;});}

function presentationBitmap(image){if(!image)return null;const canvas=document.createElement('canvas');
  canvas.width=image.naturalWidth||image.width;canvas.height=image.naturalHeight||image.height;
  canvas.getContext('2d').drawImage(image,0,0);return canvas;}

async function presentationBrand(){const load=source=>presentationImage(source).catch(()=>null);
  const [wordmark,mark]=await Promise.all([load('/assets/studio/akeda-studio-wordmark.png'),
    load('/assets/studio/akeda-studio-mark.png')]);return {wordmark:presentationBitmap(wordmark),mark:presentationBitmap(mark)};}

async function presentationSnapshots(){let host=$('presentationCapture');
  if(!host){host=document.createElement('div');host.id='presentationCapture';host.setAttribute('aria-hidden','true');
    document.body.append(host);}
  if(!presentationScene){presentationScene=MebelScene(host);presentationScene.setPayload(PAYLOAD.viewer);
    presentationScene.setView('axon');presentationScene.setTextures(true);presentationScene.setHw(true);
    presentationScene.setHoles(false);presentationScene.setXray(false);}
  presentationScene.closeAll();presentationScene.setExplode(0);presentationScene.setDims(true);
  presentationScene.setView('axon');await presentationWaitFrames(3);
  const overview=await presentationImage(presentationScene.snapshot(1280));
  presentationScene.setDims(false);presentationScene.setExplode(.64);await presentationWaitFrames(3);
  const exploded=await presentationImage(presentationScene.snapshot(1280));
  presentationScene.setExplode(0);presentationScene.setDims(true);
  return {overview,exploded};}

function presentationCanvas(){const canvas=document.createElement('canvas');
  canvas.width=PRESENTATION_PAGE.width;canvas.height=PRESENTATION_PAGE.height;return canvas;}

function presentationFont(ctx,size,weight=400,family='Segoe UI,Arial,sans-serif'){
  ctx.font=`${weight} ${size}px ${family}`;}

function presentationFitText(ctx,text,maxWidth,size,weight=600,minSize=18){let current=size;
  while(current>minSize){presentationFont(ctx,current,weight);if(ctx.measureText(text).width<=maxWidth)break;current-=1;}
  return current;}

function presentationWrappedText(ctx,text,x,y,maxWidth,lineHeight,maxLines=3){const words=String(text||'').split(/\s+/);
  const lines=[];let line='';for(const word of words){const next=line?line+' '+word:word;
    if(line&&ctx.measureText(next).width>maxWidth){lines.push(line);line=word;}else line=next;
    if(lines.length===maxLines)break;}if(lines.length<maxLines&&line)lines.push(line);
  lines.slice(0,maxLines).forEach((value,index)=>ctx.fillText(value,x,y+index*lineHeight));return lines.length;}

function presentationContain(ctx,image,x,y,width,height){const scale=Math.min(width/image.width,height/image.height);
  const drawWidth=image.width*scale,drawHeight=image.height*scale;
  ctx.drawImage(image,x+(width-drawWidth)/2,y+(height-drawHeight)/2,drawWidth,drawHeight);}

function presentationHeader(ctx,brand,pageTitle,pageNumber){ctx.fillStyle='#ffffff';ctx.fillRect(0,0,1754,1240);
  ctx.fillStyle='#ffffff';ctx.fillRect(0,0,1754,92);ctx.fillStyle='#2f6cdf';ctx.fillRect(0,92,1754,7);
  if(brand.wordmark)ctx.drawImage(brand.wordmark,42,25,190,39);else{ctx.fillStyle='#202833';
    presentationFont(ctx,30,750);ctx.fillText('Akeda Studio',42,59);}if(brand.mark)ctx.drawImage(brand.mark,244,27,53,35);
  ctx.fillStyle='#5d6876';presentationFont(ctx,16,650);ctx.fillText(pageTitle,326,58);
  ctx.textAlign='right';ctx.fillStyle='#77818e';presentationFont(ctx,14,650);
  ctx.fillText(`${pageNumber} / 2`,1708,57);ctx.textAlign='left';}

function presentationFooter(ctx){ctx.strokeStyle='#d7dde5';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(44,1181);ctx.lineTo(1710,1181);ctx.stroke();
  ctx.fillStyle='#66717f';presentationFont(ctx,13,500);ctx.fillText('Версия изделия',44,1212);
  ctx.fillStyle='#303946';presentationFont(ctx,12,600,'ui-monospace,SFMono-Regular,Consolas,monospace');
  ctx.fillText(REVIEW.revision||'',164,1212);ctx.textAlign='right';ctx.fillStyle='#66717f';
  presentationFont(ctx,13,500);ctx.fillText('Сформировано в Akeda Studio',1710,1212);ctx.textAlign='left';}

function presentationMetaLabel(ctx,label,value,x,y,width){ctx.fillStyle='#77818e';presentationFont(ctx,13,700);
  ctx.fillText(label.toUpperCase(),x,y);ctx.fillStyle='#202833';presentationFont(ctx,23,650);
  presentationWrappedText(ctx,value||'Не указано',x,y+34,width,29,2);}

function presentationOverviewPage(image,brand){const canvas=presentationCanvas(),ctx=canvas.getContext('2d');
  presentationHeader(ctx,brand,'ПРЕЗЕНТАЦИЯ ВЕРСИИ ДЛЯ СОГЛАСОВАНИЯ',1);
  ctx.fillStyle='#edf0f4';ctx.fillRect(44,129,1160,1009);presentationContain(ctx,image,67,153,1114,963);
  ctx.fillStyle='#ffffff';ctx.fillRect(1232,129,478,1009);ctx.strokeStyle='#d7dde5';ctx.strokeRect(1232.5,129.5,477,1008);
  ctx.fillStyle='#2f6cdf';ctx.fillRect(1232,129,10,1009);ctx.fillStyle='#64707e';presentationFont(ctx,14,750);
  ctx.fillText(REVIEW.organization_name||'AKEDA STUDIO',1272,178);
  const title=REVIEW.project_name||'Изделие';ctx.fillStyle='#202833';
  presentationFitText(ctx,title,390,40,760,24);presentationWrappedText(ctx,title,1272,232,390,47,3);
  ctx.fillStyle='#6c7683';presentationFont(ctx,15,500);const type=REVIEW.link_mode==='live'?'Обновляемая версия':'Фиксированная версия';
  ctx.fillText(type,1272,358);ctx.strokeStyle='#d7dde5';ctx.beginPath();ctx.moveTo(1272,390);ctx.lineTo(1668,390);ctx.stroke();
  presentationMetaLabel(ctx,'Габариты',PAYLOAD.stats.dims||'Не указаны',1272,438,390);
  presentationMetaLabel(ctx,'Материал',PAYLOAD.stats.decor||'Не указан',1272,550,390);
  presentationMetaLabel(ctx,'Деталей',String(PAYLOAD.stats.n_panels??'Не указано'),1272,686,390);
  presentationMetaLabel(ctx,'Дата версии',formatDate(REVIEW.created_at)||'Не указана',1272,798,390);
  ctx.fillStyle='#eef4ff';ctx.fillRect(1272,950,396,102);ctx.fillStyle='#2a5fb3';presentationFont(ctx,13,750);
  ctx.fillText('ИДЕНТИФИКАТОР РЕВИЗИИ',1294,982);ctx.fillStyle='#202833';
  presentationFont(ctx,18,700,'ui-monospace,SFMono-Regular,Consolas,monospace');ctx.fillText((REVIEW.revision||'').slice(0,16),1294,1018);
  presentationFooter(ctx);return canvas;}

function presentationPanelSize(panel){const value=axis=>Math.round(Math.abs(Number(panel[axis+'2'])-Number(panel[axis+'1']))||0);
  const sizes=[value('x'),value('y'),value('z')].sort((a,b)=>b-a);return `${sizes[0]} x ${sizes[1]} x ${sizes[2]} мм`;}

function presentationExplodedPage(image,brand){const canvas=presentationCanvas(),ctx=canvas.getContext('2d');
  presentationHeader(ctx,brand,'РАЗОБРАННЫЙ ВИД И ОСНОВНЫЕ ХАРАКТЕРИСТИКИ',2);
  ctx.fillStyle='#edf0f4';ctx.fillRect(44,129,1070,1009);presentationContain(ctx,image,66,151,1026,965);
  ctx.fillStyle='#ffffff';ctx.fillRect(1142,129,568,1009);ctx.strokeStyle='#d7dde5';ctx.strokeRect(1142.5,129.5,567,1008);
  ctx.fillStyle='#202833';presentationFont(ctx,27,740);ctx.fillText('Состав изделия',1182,188);
  ctx.fillStyle='#6f7986';presentationFont(ctx,14,500);ctx.fillText('Основные видимые детали без производственных данных',1182,219);
  const panels=Array.isArray(PAYLOAD.viewer.panels)?PAYLOAD.viewer.panels:[];const rows=panels.slice(0,9);
  let y=265;for(const [index,panel] of rows.entries()){ctx.strokeStyle='#e0e4e9';ctx.beginPath();ctx.moveTo(1182,y+70);ctx.lineTo(1670,y+70);ctx.stroke();
    ctx.fillStyle='#2f6cdf';presentationFont(ctx,13,750);ctx.fillText(String(index+1).padStart(2,'0'),1182,y+21);
    ctx.fillStyle='#202833';presentationFont(ctx,17,650);const name=String(panel.name||panel.type||'Деталь');
    const display=ctx.measureText(name).width>388?name.slice(0,38)+'…':name;ctx.fillText(display,1224,y+21);
    ctx.fillStyle='#77818e';presentationFont(ctx,13,500);ctx.fillText(presentationPanelSize(panel),1224,y+47);y+=82;}
  if(!rows.length){ctx.fillStyle='#77818e';presentationFont(ctx,16,500);ctx.fillText('Состав не указан',1182,285);}
  if(panels.length>rows.length){ctx.fillStyle='#64707e';presentationFont(ctx,14,600);
    ctx.fillText(`И ещё ${panels.length-rows.length} деталей в модели`,1182,1038);}
  ctx.fillStyle='#f4f6f8';ctx.fillRect(1182,1072,488,44);ctx.fillStyle='#5b6673';presentationFont(ctx,13,600);
  ctx.fillText('Инженерные данные и стоимость в документ не включены',1198,1100);
  presentationFooter(ctx);return canvas;}

function presentationBytes(parts){const length=parts.reduce((sum,part)=>sum+part.length,0),result=new Uint8Array(length);
  let offset=0;for(const part of parts){result.set(part,offset);offset+=part.length;}return result;}

async function presentationJpeg(canvas){const blob=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):
  reject(new Error('Не удалось собрать страницу PDF')),'image/jpeg',.93));return new Uint8Array(await blob.arrayBuffer());}

function presentationPdf(jpegs){const encoder=new TextEncoder(),parts=[],offsets=[0],objectCount=2+jpegs.length*3;
  let length=0;const push=part=>{const bytes=typeof part==='string'?encoder.encode(part):part;parts.push(bytes);length+=bytes.length;};
  push('%PDF-1.4\n%AKEDA\n');const addObject=(number,body,binary=null)=>{offsets[number]=length;push(`${number} 0 obj\n${body}`);
    if(binary){push('stream\n');push(binary);push('\nendstream\n');}push('endobj\n');};
  addObject(1,'<< /Type /Catalog /Pages 2 0 R >>\n');
  const kids=jpegs.map((_,index)=>`${3+index*3} 0 R`).join(' ');addObject(2,`<< /Type /Pages /Kids [${kids}] /Count ${jpegs.length} >>\n`);
  jpegs.forEach((jpeg,index)=>{const page=3+index*3,content=page+1,image=page+2;
    addObject(page,`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PRESENTATION_PAGE.pdfWidth} ${PRESENTATION_PAGE.pdfHeight}] `+
      `/Resources << /XObject << /Im0 ${image} 0 R >> >> /Contents ${content} 0 R >>\n`);
    const command=`q\n${PRESENTATION_PAGE.pdfWidth} 0 0 ${PRESENTATION_PAGE.pdfHeight} 0 0 cm\n/Im0 Do\nQ\n`;
    addObject(content,`<< /Length ${encoder.encode(command).length} >>\n`,encoder.encode(command));
    addObject(image,`<< /Type /XObject /Subtype /Image /Width ${PRESENTATION_PAGE.width} /Height ${PRESENTATION_PAGE.height} `+
      `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\n`,jpeg);});
  const xref=length;push(`xref\n0 ${objectCount+1}\n0000000000 65535 f \n`);
  for(let index=1;index<=objectCount;index++)push(`${String(offsets[index]).padStart(10,'0')} 00000 n \n`);
  push(`trailer\n<< /Size ${objectCount+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);return presentationBytes(parts);}

function presentationFilename(){const safe=String(REVIEW.project_name||'izdelie').normalize('NFKC')
  .replace(/[^\p{L}\p{N}._-]+/gu,'-').replace(/^-+|-+$/g,'').slice(0,64)||'izdelie';
  return `${safe}-versiya-${(REVIEW.revision||'').slice(0,10)}.pdf`;}

async function downloadPresentationPdf(button){const targets=[$('downloadPdf'),$('mobilePdf')].filter(Boolean);
  const labels=new Map(targets.map(item=>[item,item.textContent]));targets.forEach(item=>{item.disabled=true;item.textContent='Готовим PDF…';});
  try{const [{overview,exploded},brand]=await Promise.all([presentationSnapshots(),presentationBrand()]);
    const canvases=[presentationOverviewPage(overview,brand),presentationExplodedPage(exploded,brand)];
    const pdf=presentationPdf(await Promise.all(canvases.map(presentationJpeg)));const url=URL.createObjectURL(new Blob([pdf],{type:'application/pdf'}));
    const link=document.createElement('a');link.href=url;link.download=presentationFilename();document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);targets.forEach(item=>item.textContent='PDF скачан');
    setTimeout(()=>targets.forEach(item=>item.textContent=labels.get(item)),1600);
  }catch(error){const result=$('decisionResult');result.classList.add('on');result.textContent=error.message||'PDF не сформирован';
    targets.forEach(item=>item.textContent=labels.get(item));}finally{targets.forEach(item=>item.disabled=false);}}
"""


__all__ = ["PRESENTATION_JS"]
