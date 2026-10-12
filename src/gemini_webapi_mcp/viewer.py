"""Minimal embedded RFB viewer for isolated Gemini login handoffs.

The page is deliberately a browser viewport, not a noVNC desktop.  Google is
opened only by the isolated server-side Chromium process; this page never
frames or submits a Google login form.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit


# The npm package publishes its transpiled bundle under ``lib/``.  The
# browser-native module lives in the official noVNC source tree, which
# jsDelivr serves from GitHub with its relative core imports intact.
DEFAULT_RFB_URL = "https://cdn.jsdelivr.net/gh/novnc/noVNC@v1.5.0/core/rfb.js"


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":")).replace("<", "\\u003c")


def _source_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http", "wss", "ws"} or not parsed.netloc:
        raise ValueError("viewer URLs must be absolute HTTP(S) or WS(S) URLs")
    return f"{parsed.scheme}://{parsed.netloc}"


def content_security_policy(rfb_url: str, ws_url: str, frame_ancestors: list[str]) -> str:
    """Build a restrictive response CSP from administrator-supplied origins."""
    rfb_origin = _source_origin(rfb_url).replace("wss://", "https://").replace("ws://", "http://")
    ws_origin = _source_origin(ws_url)
    ancestors = " ".join(frame_ancestors or ["'self'"])
    return (
        "default-src 'none'; "
        "base-uri 'none'; form-action 'none'; object-src 'none'; "
        f"script-src 'unsafe-inline' {rfb_origin}; "
        f"connect-src 'self' {ws_origin}; "
        "style-src 'unsafe-inline'; img-src data:; "
        f"frame-ancestors {ancestors}"
    )


def viewer_page(*, sid: str, ws_url: str, expires_at: float, complete_url: str = "", rfb_url: str = DEFAULT_RFB_URL) -> str:
    """Render the responsive viewer with an opaque, account-bound capability."""
    config = _json({"sid": sid, "wsUrl": ws_url, "expiresAt": expires_at, "completeUrl": complete_url, "rfbUrl": rfb_url})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer"><title>Connect Gemini account</title>
<style>
:root{{color-scheme:light dark;font:15px system-ui,sans-serif;background:#0c1220;color:#edf2ff}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100dvh;background:#0c1220}}
main{{min-height:100dvh;display:flex;flex-direction:column}}
header{{padding:12px max(14px,env(safe-area-inset-left));display:flex;gap:10px;align-items:center;border-bottom:1px solid #273149;background:#121a2b}}
h1{{font-size:17px;margin:0;flex:1}}button{{font:inherit;border:1px solid #435273;border-radius:9px;background:#1d2a45;color:inherit;padding:8px 11px;cursor:pointer}}
button:focus-visible{{outline:2px solid #9ec5ff;outline-offset:2px}}#status{{font-size:13px;color:#b9c8e8}}
#notice{{padding:10px 14px;color:#c4d0e8;background:#172239;font-size:13px;line-height:1.4}}
#surface-wrap{{position:relative;flex:1;min-height:240px;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#05070c}}
#surface{{width:100%;height:100%;min-height:240px;touch-action:none;outline:none;overflow:hidden}}
#surface.touch{{cursor:none}}#surface canvas{{display:block;max-width:100%;max-height:100%;margin:auto}}
#message{{position:absolute;inset:0;display:grid;place-items:center;padding:24px;text-align:center;background:#05070ce8;color:#eef3ff}}
#message[hidden]{{display:none}}#message p{{max-width:34rem;line-height:1.5}}#countdown{{font-variant-numeric:tabular-nums}}
footer{{padding:10px 14px max(10px,env(safe-area-inset-bottom));display:flex;gap:8px;flex-wrap:wrap;background:#121a2b;border-top:1px solid #273149}}
@media (prefers-color-scheme:light){{:root,body{{background:#f7f9fc;color:#152039}}header,footer{{background:#fff;border-color:#dbe2ef}}#notice{{background:#eef4ff;color:#304568}}#surface-wrap{{background:#e9eef7}}#message{{background:#e9eef7e8;color:#152039}}button{{background:#f4f7fb;color:#152039;border-color:#b8c6dc}}}}
</style></head><body><main>
<header><h1>Connect Gemini account</h1><span id="status" role="status">Starting…</span><span id="countdown"></span><button id="fullscreen" type="button">Fullscreen</button></header>
<div id="notice">Google sign-in runs inside an isolated Chromium browser hosted by your MCP server. This page does not collect your Google password.</div>
<section id="surface-wrap"><div id="surface" tabindex="0" aria-label="Remote Gemini sign-in browser"></div><div id="message"><p id="message-text">Loading secure browser…</p></div></section>
<footer><button id="mode" type="button">Mode: Auto</button><button id="verify" type="button">I’m signed in</button><button id="focus" type="button">Focus keyboard</button><button id="cancel" type="button">Cancel</button></footer>
</main><script type="application/json" id="viewer-config">{config}</script>
<script type="module">
const cfg=JSON.parse(document.getElementById('viewer-config').textContent);const surface=document.getElementById('surface');
const message=document.getElementById('message'),text=document.getElementById('message-text'),status=document.getElementById('status'),count=document.getElementById('countdown');
let rfb=null,mode='auto',connected=false,closed=false,lastTouch=null,longPress=null,retryTimer=null,touches=new Map(),pinchStart=0,pinchScale=1,lastPointerTap=0,readyCountdownStarted=false,readyCountdownTimer=null;
const setMessage=(value,show=true)=>{{text.textContent=value;message.hidden=!show}};
const touchMode=()=>mode==='touch'||(mode==='auto'&&matchMedia('(pointer:coarse)').matches&&!matchMedia('(hover:hover)').matches);\nconst revealAfterCountdown=()=>{{if(readyCountdownStarted)return;readyCountdownStarted=true;let seconds=10;const tick=()=>{{if(closed)return;if(seconds<=0){{message.hidden=true;return}}setMessage('Chromium is ready. Complete Google sign-in in the browser above. Viewer unlocks in '+seconds+'s.',true);seconds-=1;readyCountdownTimer=setTimeout(tick,1000)}};tick()}};
const applyMode=()=>{{surface.classList.toggle('touch',touchMode());document.getElementById('mode').textContent='Mode: '+(mode[0].toUpperCase()+mode.slice(1));}};
document.getElementById('mode').onclick=()=>{{mode=mode==='auto'?'touch':mode==='touch'?'mouse':'auto';applyMode();}};applyMode();
const point=e=>{{const r=surface.getBoundingClientRect();const c=surface.querySelector('canvas');const sx=(c?.width||r.width)/r.width,sy=(c?.height||r.height)/r.height;return [Math.max(0,Math.round((e.clientX-r.left)*sx)),Math.max(0,Math.round((e.clientY-r.top)*sy))]}};
const wheel=(x,y,dy)=>rfb?.sendPointerEvent(x,y,dy>0?8:16);
function touchDown(e){{if(!touchMode()||!rfb)return;e.preventDefault();surface.setPointerCapture(e.pointerId);touches.set(e.pointerId,e);if(touches.size===2){{clearTimeout(longPress);const a=[...touches.values()];pinchStart=Math.hypot(a[0].clientX-a[1].clientX,a[0].clientY-a[1].clientY);return}}lastTouch={{x:e.clientX,y:e.clientY,moved:false}};longPress=setTimeout(()=>{{if(lastTouch&&!lastTouch.moved){{rfb.sendPointerEvent(x,y,4);rfb.sendPointerEvent(x,y,0)}}}},550)}}
function touchMove(e){{if(!touchMode())return;e.preventDefault();touches.set(e.pointerId,e);if(touches.size>=2){{const a=[...touches.values()];const distance=Math.hypot(a[0].clientX-a[1].clientX,a[0].clientY-a[1].clientY);if(pinchStart>0){{pinchScale=Math.max(.8,Math.min(2.2,pinchScale*distance/pinchStart));surface.style.transform='scale('+pinchScale+')';surface.style.transformOrigin='center center';}}if(lastTouch)lastTouch.moved=true;return}}if(!lastTouch||!rfb)return;const dx=e.clientX-lastTouch.x,dy=e.clientY-lastTouch.y;if(Math.abs(dx)+Math.abs(dy)>8){{lastTouch.moved=true;clearTimeout(longPress);const [x,y]=point(e);wheel(x,y,dy);lastTouch={{x:e.clientX,y:e.clientY,moved:true}}}}}}
function touchUp(e){{if(touchMode())e.preventDefault();touches.delete(e.pointerId);if(touches.size===0){{pinchStart=0;clearTimeout(longPress);if(surface.style.transform)surface.style.transform='';if(lastTouch&&!lastTouch.moved&&rfb){{const [x,y]=point(e);rfb.sendPointerEvent(x,y,1);rfb.sendPointerEvent(x,y,0);lastPointerTap=Date.now()}}lastTouch=null}}}}
surface.addEventListener('pointerdown',touchDown,{{capture:true}});surface.addEventListener('pointermove',touchMove,{{capture:true,passive:false}});surface.addEventListener('pointerup',touchUp,{{capture:true}});surface.addEventListener('pointercancel',e=>{{touches.delete(e.pointerId);clearTimeout(longPress);lastTouch=null}},{{capture:true}});surface.addEventListener('click',e=>{{if(!touchMode()||!rfb||Date.now()-lastPointerTap<500)return;e.preventDefault();const [x,y]=point(e);rfb.sendPointerEvent(x,y,1);rfb.sendPointerEvent(x,y,0)}},{{capture:true}});
document.getElementById('verify').onclick=async()=>{{const button=document.getElementById('verify');button.disabled=true;status.textContent='Confirmed';setMessage('Sign-in confirmed. Continuing authorization…',true);try{{await fetch('/auth/browser/verify?sid='+encodeURIComponent(cfg.sid),{{method:'POST',credentials:'same-origin'}})}}catch{{}}if(cfg.completeUrl){{window.location.assign(cfg.completeUrl)}}else{{button.disabled=false}}}};document.getElementById('focus').onclick=()=>{{surface.focus();rfb?.focus()}};document.getElementById('fullscreen').onclick=()=>document.documentElement.requestFullscreen?.();
document.getElementById('cancel').onclick=async()=>{{closed=true;await fetch('/auth/browser/cancel?sid='+encodeURIComponent(cfg.sid),{{method:'POST',credentials:'same-origin'}}).catch(()=>{{}});setMessage('Authentication cancelled. You can close this tab.')}};
function connect(){{if(closed||Date.now()/1000>=cfg.expiresAt)return;clearTimeout(retryTimer);status.textContent='Connecting…';import(cfg.rfbUrl).then(({{default:RFB}})=>{{if(closed)return;const ws=cfg.wsUrl+(cfg.wsUrl.includes('?')?'&':'?')+'token='+encodeURIComponent(cfg.sid);rfb=new RFB(surface,ws,{{scaleViewport:true,resizeSession:false,clipViewport:false,showDotCursor:false}});rfb.addEventListener('connect',()=>{{connected=true;status.textContent='Connected';setMessage('Connected to the browser viewer. Checking browser status…');rfb.showDotCursor=false;surface.focus()}});rfb.addEventListener('disconnect',()=>{{connected=false;if(!closed){{status.textContent='Reconnecting…';setMessage('The secure browser connection was interrupted. Retrying…');retryTimer=setTimeout(connect,1200)}}}})}}).catch(()=>{{status.textContent='Unavailable';setMessage('The embedded browser viewer could not load. Check the server reverse proxy and try again.')}})}}
async function poll(){{if(closed)return;const left=Math.max(0,Math.ceil(cfg.expiresAt-Date.now()/1000));count.textContent=left?('Expires in '+left+'s'):'Expired';if(!left){{closed=true;setMessage('This login session expired. Start a new connection.');rfb?.disconnect();return}}try{{const r=await fetch('/auth/browser/status?sid='+encodeURIComponent(cfg.sid),{{credentials:'same-origin'}});const d=await r.json();if(d.state==='expired'){{closed=true;setMessage('This login session expired. Start a new connection.');rfb?.disconnect()}}else if(d.state==='error'){{status.textContent='Browser unavailable';const detail=d.error==='browser_process_exited'?'The server browser stopped before the page could be displayed. Start a new login.':d.error==='browser_start_failed'?'Chromium failed to start on the server. Check the configured browser and container logs.':'Google rejected this automated browser session. Configure a supported installed Chrome or Edge browser, then start a new login. Do not retry with spoofed browser flags.';setMessage(detail,true)}}else if(!d.ready&&(d.browser_ready||(d.components&&Object.values(d.components).length===4&&Object.values(d.components).every(value=>value==='running')))){{status.textContent='Chromium ready';revealAfterCountdown()}}else if(d.ready){{status.textContent='Gemini login detected';setMessage('Login detected. Return to the authorization page and continue.',true)}}}}catch{{}}setTimeout(poll,2000)}}
connect();poll();
</script></body></html>"""
