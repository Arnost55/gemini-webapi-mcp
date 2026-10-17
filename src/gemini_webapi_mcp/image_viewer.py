"""Private MCP App resources for rendering image-tool results in ChatGPT."""

from __future__ import annotations

import os
from urllib.parse import urlparse

LEGACY_IMAGE_VIEWER_URI = "ui://gemini-webapi-mcp/image-result-v1.html"
IMAGE_VIEWER_URI = "ui://gemini-webapi-mcp/image-result-v2.html"


def image_viewer_resource_meta() -> dict:
    """Return standards-first, least-privilege widget metadata."""
    csp = {"connectDomains": [], "resourceDomains": []}
    meta: dict = {
        "ui": {"prefersBorder": False, "csp": csp},
        "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
        "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"]},
    }
    domain = os.environ.get("GEMINI_WIDGET_DOMAIN", "").rstrip("/")
    parsed = urlparse(domain)
    if domain and parsed.scheme == "https" and parsed.netloc and not parsed.path:
        meta["ui"]["domain"] = domain
        meta["openai/widgetDomain"] = domain
    return meta


IMAGE_VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
:root{color-scheme:light dark;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--line:color-mix(in srgb,CanvasText 15%,transparent);--muted:color-mix(in srgb,CanvasText 65%,transparent);--focus:#0b6bcb}
*{box-sizing:border-box}
html,body{margin:0;min-width:0;background:transparent;color:CanvasText}
body{padding:4px;overflow-x:hidden}
button,a{font:inherit}
button:focus-visible,a:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
#app{position:relative;width:100%}
#status{min-height:20px;margin:5px 4px 9px;color:var(--muted);font-size:13px}
#stage{position:relative;display:grid;place-items:center;width:fit-content;max-width:100%;min-height:0;margin-inline:auto;overflow:hidden;border:1px solid var(--line);border-radius:14px;background:transparent}
#stage.empty{width:100%}
#stage.empty{min-height:clamp(180px,40vw,320px)}
#image{display:block;width:auto;height:auto;max-width:100%;max-height:min(900px,calc(100vh - 160px));max-height:min(900px,calc(100dvh - 160px));object-fit:contain;cursor:zoom-in;user-select:none;-webkit-user-drag:none;transform-origin:center;transition:opacity 160ms ease,transform 140ms ease}
#image[hidden],#error[hidden]{display:none}
#stage.loading:after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(105deg,transparent 25%,color-mix(in srgb,CanvasText 8%,transparent) 45%,transparent 65%);animation:shimmer 1.4s infinite}
@keyframes shimmer{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
#error{padding:28px 18px;text-align:center;color:var(--muted);line-height:1.45}
.controls{position:absolute;inset:10px 10px auto;z-index:2;display:flex;justify-content:space-between;gap:8px;pointer-events:none}
.group{display:flex;gap:6px;pointer-events:auto}
.icon-button,.action{min-width:40px;min-height:40px;border:1px solid color-mix(in srgb,white 22%,transparent);border-radius:999px;background:color-mix(in srgb,#111 76%,transparent);color:white;display:inline-grid;place-items:center;padding:0 12px;cursor:pointer;text-decoration:none;-webkit-tap-highlight-color:transparent;backdrop-filter:blur(12px)}
.icon-button:hover{background:color-mix(in srgb,#111 90%,transparent)}
.icon-button[hidden],.action[hidden]{display:none}
#counter{min-width:54px;font-variant-numeric:tabular-nums}
#galleryNav{display:flex;align-items:center;justify-content:center;gap:8px;padding:10px 4px 2px}
#galleryNav[hidden],#actions[hidden]{display:none}
#thumbs{display:flex;gap:7px;max-width:calc(100% - 104px);overflow-x:auto;scrollbar-width:none}
#thumbs::-webkit-scrollbar{display:none}
.thumb{width:44px;height:44px;flex:0 0 auto;padding:0;overflow:hidden;border:2px solid transparent;border-radius:9px;background:transparent;cursor:pointer}
.thumb[aria-current="true"]{border-color:var(--focus)}
.thumb img{display:block;width:100%;height:100%;object-fit:cover}
#actions{display:flex;flex-wrap:wrap;gap:8px;padding:10px 4px 2px}
#actions .action{position:static;min-height:36px;color:CanvasText;border-color:var(--line);background:transparent;backdrop-filter:none}
#actions .action:hover{background:color-mix(in srgb,CanvasText 6%,transparent)}
#expanded{position:fixed;inset:0;z-index:20;display:grid;grid-template-rows:auto minmax(0,1fr);height:100vh;height:100dvh;min-height:0;overflow:hidden;overscroll-behavior:contain;background:transparent;padding:max(10px,env(safe-area-inset-top)) max(10px,env(safe-area-inset-right)) max(10px,env(safe-area-inset-bottom)) max(10px,env(safe-area-inset-left))}
#expanded[hidden]{display:none}
#expandedBar{display:flex;justify-content:flex-end;gap:7px;padding-bottom:8px}
#expandedImageWrap{min-width:0;min-height:0;overflow:hidden;display:grid;place-items:center;overscroll-behavior:contain;touch-action:pan-x pan-y}
#expandedImageWrap.zoomed{overflow:auto}
#expandedImage{display:block;width:auto;height:auto;max-width:none;max-height:none;object-fit:contain;transform-origin:center}
@media(max-width:480px){body{padding:2px}#stage{border-radius:12px}.controls{inset:8px 8px auto}}
@media(prefers-reduced-motion:reduce){*,*:after{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<main id="app" aria-busy="true">
  <p id="status" role="status" aria-live="polite">Waiting for the generated image result…</p>
  <section id="stage" class="empty loading" aria-label="Generated image">
    <img id="image" alt="Image generated by Gemini" hidden>
    <div id="error" role="status" hidden></div>
    <div class="controls">
      <div class="group"><button id="counter" class="icon-button" type="button" hidden aria-label="Image count"></button></div>
      <div class="group">
        <a id="download" class="icon-button" href="#" hidden aria-label="Download image" title="Download">↓</a>
        <button id="expand" class="icon-button" type="button" hidden aria-label="Expand image" title="Expand">⛶</button>
      </div>
    </div>
  </section>
  <nav id="galleryNav" aria-label="Image gallery" hidden>
    <button id="previous" class="icon-button" type="button" aria-label="Previous image">‹</button>
    <div id="thumbs"></div>
    <button id="next" class="icon-button" type="button" aria-label="Next image">›</button>
  </nav>
  <div id="actions" hidden>
    <button id="regenerate" class="action" type="button">Regenerate</button>
    <button id="edit" class="action" type="button">Edit image</button>
  </div>
</main>
<section id="expanded" hidden aria-modal="true" role="dialog" aria-label="Expanded generated image">
  <div id="expandedBar">
    <button id="expandedPrevious" class="icon-button" type="button" aria-label="Previous image" hidden>‹</button>
    <button id="expandedNext" class="icon-button" type="button" aria-label="Next image" hidden>›</button>
    <button id="zoomOut" class="icon-button" type="button" aria-label="Zoom out">−</button>
    <button id="zoomReset" class="icon-button" type="button" aria-label="Reset zoom">100%</button>
    <button id="zoomIn" class="icon-button" type="button" aria-label="Zoom in">+</button>
    <button id="close" class="icon-button" type="button" aria-label="Close expanded image">×</button>
  </div>
  <div id="expandedImageWrap"><img id="expandedImage" alt="Expanded image generated by Gemini"></div>
</section>
<script>
(()=>{
  const $=id=>document.getElementById(id),app=$("app"),status=$("status"),stage=$("stage"),image=$("image"),error=$("error"),galleryNav=$("galleryNav"),thumbs=$("thumbs"),counter=$("counter"),download=$("download"),expand=$("expand"),actions=$("actions"),expanded=$("expanded"),expandedImage=$("expandedImage"),zoomReset=$("zoomReset");
  const MIME_EXT={"image/png":".png","image/jpeg":".jpg","image/webp":".webp"},allowedBase64=/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/,MAX_IMAGE_BYTES=25*1024*1024;
  let items=[],selected=0,metadata={},lastHeight=0,resizeFrame=0,scale=1;

  function envelope(v){return!v||typeof v!=="object"?null:v.mcp_tool_result||v.mcpToolResult||v.call_tool_result||v.callToolResult||v.result||v}
  function fitInlineImage(){
    if(image.hidden||!items.length)return;
    const bodyStyle=getComputedStyle(document.body),statusStyle=getComputedStyle(status),stageStyle=getComputedStyle(stage);
    const px=value=>Number.parseFloat(value)||0;
    const statusSpace=status.getBoundingClientRect().height+px(statusStyle.marginTop)+px(statusStyle.marginBottom);
    const stageBorders=px(stageStyle.borderTopWidth)+px(stageStyle.borderBottomWidth);
    const gallerySpace=galleryNav.hidden?0:galleryNav.getBoundingClientRect().height;
    const actionSpace=actions.hidden?0:actions.getBoundingClientRect().height;
    const bodySpace=px(bodyStyle.paddingTop)+px(bodyStyle.paddingBottom);
    const available=Math.max(48,Math.floor(window.innerHeight-bodySpace-statusSpace-stageBorders-gallerySpace-actionSpace-8));
    const next=`${available}px`;
    if(image.style.maxHeight!==next)image.style.maxHeight=next;
  }
  function fitExpandedImage(){
    if(expanded.hidden||!expandedImage.complete||!expandedImage.naturalWidth||!expandedImage.naturalHeight)return;
    const width=expandedImage.parentElement.clientWidth,height=expandedImage.parentElement.clientHeight;
    if(!width||!height)return;
    const ratio=expandedImage.naturalWidth/expandedImage.naturalHeight;
    const fitWidth=Math.min(width,height*ratio),fitHeight=fitWidth/ratio;
    expandedImage.style.width=`${fitWidth}px`;expandedImage.style.height=`${fitHeight}px`;
  }
  function reportHeight(){
    resizeFrame=0;
    fitInlineImage();
    fitExpandedImage();
    const style=getComputedStyle(document.body),bottomPadding=parseFloat(style.paddingBottom)||0,rect=app.getBoundingClientRect();
    const height=Math.max(80,Math.min(2400,Math.ceil(rect.bottom+bottomPadding)));
    if(Math.abs(height-lastHeight)<2)return;
    lastHeight=height;
    if(window.openai&&typeof window.openai.notifyIntrinsicHeight==="function")try{window.openai.notifyIntrinsicHeight(height)}catch(_){}
  }
  function scheduleHeight(){if(resizeFrame)return;resizeFrame=requestAnimationFrame(reportHeight)}
  function dimensions(index){const a=Array.isArray(metadata.artifacts)?metadata.artifacts[index]:null,w=Number(a&&a.width),h=Number(a&&a.height);return w>0&&h>0?{w,h}:null}
  function safeFilename(index,mime){
    const artifact=Array.isArray(metadata.artifacts)?metadata.artifacts[index]:null,raw=typeof(artifact&&artifact.filename)==="string"?artifact.filename:"";
    const stem=raw.replace(/\\/g,"/").split("/").pop().replace(/\.[^.]*$/,""),clean=stem.replace(/[^a-zA-Z0-9._-]+/g,"-").replace(/^[-.]+|[-.]+$/g,"").slice(0,80);
    return `${clean||`gemini-image-${String(index+1).padStart(3,"0")}`}${MIME_EXT[mime]}`;
  }
  function validSignature(bytes,mime){
    if(mime==="image/png")return bytes.length>=8&&bytes[0]===137&&bytes[1]===80&&bytes[2]===78&&bytes[3]===71&&bytes[4]===13&&bytes[5]===10&&bytes[6]===26&&bytes[7]===10;
    if(mime==="image/jpeg")return bytes.length>=3&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255;
    if(mime==="image/webp")return bytes.length>=12&&bytes[0]===82&&bytes[1]===73&&bytes[2]===70&&bytes[3]===70&&bytes[8]===87&&bytes[9]===69&&bytes[10]===66&&bytes[11]===80;
    return false;
  }
  function makeItem(block,index){
    const mime=String(block.mimeType||block.mime_type||"").toLowerCase(),data=block.data;
    if(!MIME_EXT[mime]||typeof data!=="string"||!allowedBase64.test(data)||data.length>Math.ceil(MAX_IMAGE_BYTES*4/3)+4)throw new Error("This image has an unsupported format or size.");
    const binary=atob(data),bytes=new Uint8Array(binary.length);
    for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
    if(!bytes.length||bytes.length>MAX_IMAGE_BYTES||!validSignature(bytes,mime))throw new Error("This image could not be validated for display or download.");
    const blob=new Blob([bytes],{type:mime});
    // MCP image blocks are already base64 data. Keep the preview on the data:
    // scheme, which is allowed by constrained MCP App image CSPs; blob: URLs
    // can be blocked inside the host's sandboxed iframe.
    return{blob,mime,src:`data:${mime};base64,${data}`,filename:safeFilename(index,mime),alt:""};
  }
  function clearItems(){items=[];thumbs.replaceChildren()}
  function select(index,focus=false){
    if(!items.length)return;
    selected=(index+items.length)%items.length;
    const item=items[selected],dimsForImage=dimensions(selected);
    app.setAttribute("aria-busy","true");status.textContent="Loading generated image…";
    stage.classList.add("loading");error.hidden=true;image.hidden=false;image.src=item.src;image.alt=item.alt;
    image.dataset.source=item.src;expand.hidden=download.hidden=true;
    image.style.aspectRatio=dimsForImage?`${dimsForImage.w} / ${dimsForImage.h}`:"auto";
    if(dimsForImage){image.width=dimsForImage.w;image.height=dimsForImage.h}else{image.removeAttribute("width");image.removeAttribute("height")}
    if(!expanded.hidden){expandedImage.src=item.src;if(dimsForImage){expandedImage.width=dimsForImage.w;expandedImage.height=dimsForImage.h}else{expandedImage.removeAttribute("width");expandedImage.removeAttribute("height")}scheduleHeight()}
    download.download=item.filename;counter.textContent=`${selected+1} / ${items.length}`;
    [...thumbs.children].forEach((node,j)=>node.setAttribute("aria-current",String(j===selected)));
    if(focus&&thumbs.children[selected])thumbs.children[selected].focus();
    scheduleHeight();
  }
  function showFailure(message){
    app.setAttribute("aria-busy","false");stage.classList.remove("loading");stage.classList.add("empty");error.hidden=false;error.textContent=message;image.hidden=true;expand.hidden=download.hidden=true;scheduleHeight();
  }
  function render(value){
    const result=envelope(value);if(!result||!Array.isArray(result.content))return;
    metadata=result.structuredContent||result.structured_content||{};clearItems();
    const blocks=result.content.filter(item=>item&&item.type==="image"&&typeof item.data==="string"&&MIME_EXT[String(item.mimeType||item.mime_type||"").toLowerCase()]);
    if(!blocks.length){showFailure(result.isError?"Image generation failed. Try again or adjust the prompt.":"The result completed without a viewable image.");status.textContent=result.isError?"Image generation failed":"No image available";return}
    try{items=blocks.map(makeItem)}catch(_){clearItems();showFailure("This image could not be displayed or downloaded safely.");status.textContent="Image unavailable";return}
    items.forEach((item,index)=>{item.alt=items.length===1?"Image generated by Gemini":`Image ${index+1} of ${items.length} generated by Gemini`;const button=document.createElement("button");button.className="thumb";button.type="button";button.setAttribute("aria-label",`Show image ${index+1}`);button.onclick=()=>select(index);const thumb=document.createElement("img");thumb.src=item.src;thumb.alt="";button.appendChild(thumb);thumbs.appendChild(button)});
    app.setAttribute("aria-busy","false");stage.classList.remove("empty");status.textContent=items.length===1?"Generated image":`${items.length} generated images`;
    counter.hidden=galleryNav.hidden=items.length<2;$("expandedPrevious").hidden=$("expandedNext").hidden=items.length<2;expand.hidden=download.hidden=true;
    const canRepeat=typeof metadata.prompt==="string"&&metadata.prompt.trim();actions.hidden=!canRepeat;$("regenerate").hidden=$("edit").hidden=!canRepeat;
    select(0);scheduleHeight();
  }
  async function downloadSelected(){
    const item=items[selected];if(!item)return;
    download.setAttribute("aria-busy","true");download.setAttribute("aria-disabled","true");status.textContent="Preparing image download…";
    try{
      const bridge=window.openai;
      if(typeof File==="function"&&typeof Blob==="function"&&bridge&&typeof bridge.uploadFile==="function"&&typeof bridge.getFileDownloadUrl==="function"){
        const file=new File([item.blob],item.filename,{type:item.mime}),uploaded=await bridge.uploadFile(file);
        if(!uploaded||typeof uploaded.fileId!=="string"||!uploaded.fileId)throw new Error("The host did not return a file reference.");
        const response=await bridge.getFileDownloadUrl({fileId:uploaded.fileId});
        if(!response||typeof response.downloadUrl!=="string"||!/^https:\/\//i.test(response.downloadUrl))throw new Error("The host did not return a secure download link.");
        if(typeof bridge.openExternal==="function")await bridge.openExternal({href:response.downloadUrl,redirectUrl:false});
        else{const link=document.createElement("a");link.href=response.downloadUrl;link.target="_blank";link.rel="noopener noreferrer";link.click()}
      }else{
        const link=document.createElement("a");link.href=item.url;link.download=item.filename;link.rel="noopener";link.style.display="none";document.body.appendChild(link);link.click();link.remove();
      }
      status.textContent=`Download requested: ${item.filename}. If it did not start, use the host’s image save option.`;
    }catch(_){status.textContent="Could not download this image. Try again or use the host’s image save option."}
    finally{download.removeAttribute("aria-busy");download.removeAttribute("aria-disabled");scheduleHeight()}
  }
  function setZoom(value){scale=Math.max(.5,Math.min(4,value));expandedImage.style.transform=`scale(${scale})`;expandedImage.parentElement.classList.toggle("zoomed",scale>1);zoomReset.textContent=scale===1?"Fit":`${Math.round(scale*100)}%`;scheduleHeight()}
  async function callTool(prompt,conversationId){
    if(!prompt||!window.openai||typeof window.openai.callTool!=="function"){status.textContent="Image actions are unavailable in this client.";return}
    const args={prompt};if(conversationId)args.conversation_id=conversationId;
    try{status.textContent="Starting image generation…";await window.openai.callTool("gemini_generate_image",args)}catch(_){status.textContent="Could not start image generation from this client."}scheduleHeight();
  }
  async function openExpanded(){
    if(!items.length||image.hidden||!image.complete||!image.naturalWidth||!image.naturalHeight)return;expandedImage.src=image.currentSrc||items[selected].src;setZoom(1);
    if(window.openai&&typeof window.openai.requestDisplayMode==="function")try{await window.openai.requestDisplayMode({mode:"fullscreen"})}catch(_){}
    expanded.hidden=false;fitExpandedImage();$("close").focus();scheduleHeight();
  }
  async function closeExpanded(){
    expanded.hidden=true;setZoom(1);
    if(window.openai&&typeof window.openai.requestDisplayMode==="function")try{await window.openai.requestDisplayMode({mode:"inline"})}catch(_){}
    expand.focus();scheduleHeight();
  }

  image.addEventListener("load",()=>{
    if(!image.complete||!image.naturalWidth||!image.naturalHeight||image.currentSrc!==image.dataset.source)return;
    stage.classList.remove("loading");error.hidden=true;app.setAttribute("aria-busy","false");
    status.textContent=items.length===1?"Generated image":`${items.length} generated images`;
    expand.hidden=download.hidden=false;scheduleHeight();
  });
  expandedImage.addEventListener("load",scheduleHeight);
  image.addEventListener("error",()=>{
    if(image.currentSrc!==image.dataset.source)return;
    stage.classList.remove("loading");image.hidden=true;error.hidden=false;error.textContent="This image could not be displayed.";
    app.setAttribute("aria-busy","false");status.textContent="Image unavailable";expand.hidden=download.hidden=true;scheduleHeight();
  });
  image.onclick=expand.onclick=openExpanded;download.addEventListener("click",event=>{event.preventDefault();downloadSelected()});
  $("close").onclick=closeExpanded;$("previous").onclick=()=>select(selected-1,true);$("next").onclick=()=>select(selected+1,true);
  $("expandedPrevious").onclick=()=>select(selected-1);$("expandedNext").onclick=()=>select(selected+1);
  $("zoomIn").onclick=()=>setZoom(scale+.25);$("zoomOut").onclick=()=>setZoom(scale-.25);zoomReset.onclick=()=>setZoom(1);
  $("regenerate").onclick=()=>callTool(metadata.prompt);$("edit").onclick=()=>{const prompt=window.prompt("How should Gemini edit this image?");if(prompt&&prompt.trim())callTool(prompt.trim(),metadata.conversation_id)};
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!expanded.hidden)closeExpanded();else if(event.key==="ArrowLeft"&&items.length>1)select(selected-1);else if(event.key==="ArrowRight"&&items.length>1)select(selected+1)});
  render(window.openai&&(window.openai.toolResponseMetadata||window.openai.toolOutput));
  window.addEventListener("openai:set_globals",event=>{const globals=event.detail&&event.detail.globals;if(globals)render(globals.toolResponseMetadata||globals.toolOutput)});
  window.addEventListener("message",event=>{if(event.data&&event.data.method==="ui/notifications/tool-result")render(event.data.params)});
  const observer=new ResizeObserver(scheduleHeight);observer.observe(app);
  window.addEventListener("resize",scheduleHeight,{passive:true});window.addEventListener("orientationchange",scheduleHeight,{passive:true});
  window.addEventListener("pagehide",()=>{observer.disconnect();cancelAnimationFrame(resizeFrame);clearItems()},{once:true});scheduleHeight();
})();
</script>
</body>
</html>"""

# Keep the v1 resource alive for tool descriptors cached by existing chats.
LEGACY_IMAGE_VIEWER_HTML = IMAGE_VIEWER_HTML
