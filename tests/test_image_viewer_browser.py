"""Browser regression tests for the image MCP App under a restrictive iframe CSP."""

from __future__ import annotations

import base64
import html
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from PIL import Image
from playwright._impl._errors import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from gemini_webapi_mcp.image_viewer import IMAGE_VIEWER_HTML

EDGE_PATHS = (
    os.environ.get("GEMINI_VIEWER_TEST_BROWSER"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)


def _image_data(format_name: str, size: tuple[int, int], color: str) -> str:
    image = Image.new("RGB", size, color)
    from io import BytesIO

    output = BytesIO()
    image.save(output, format=format_name)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _sandboxed_html(
    blocks: list[dict], viewer_html: str = IMAGE_VIEWER_HTML, *, include_openai: bool = True
) -> str:
    response = {
        "content": [
            {"type": "image", "mimeType": block["mime"], "data": block["data"]}
            for block in blocks
        ],
        "structuredContent": {
            "artifacts": [
                {"filename": block["filename"], "width": block["width"], "height": block["height"]}
                for block in blocks
            ],
            "prompt": "a test image",
            "conversation_id": ["test-conversation", "test-request"],
        },
    }
    bridge = """<script>
    window.__uploaded = null;
    window.openai = {
      toolOutput: __RESPONSE__,
      uploadFile: async file => {
        const bytes = new Uint8Array(await file.arrayBuffer());
        parent.postMessage({kind:"saved-file", name:file.name, mime:file.type,
          bytes:Array.from(bytes)}, "*");
        return {fileId:"synthetic-test-file"};
      },
      getFileDownloadUrl: async () => ({downloadUrl:"https://example.invalid/test-image"}),
      openExternal: async ({href}) => parent.postMessage({kind:"opened", href}, "*"),
      notifyIntrinsicHeight: height => parent.postMessage({kind:"intrinsic-height", height}, "*")
    };
    </script>""".replace("__RESPONSE__", json.dumps(response)) if include_openai else ""
    csp = (
        "<meta http-equiv=\"Content-Security-Policy\" content=\""
        "default-src 'none'; img-src data:; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; connect-src 'none'; object-src 'none'\">"
    )
    return viewer_html.replace("<head>", f"<head>{csp}{bridge}", 1)


def _launch_browser(playwright):
    executable = next((path for path in EDGE_PATHS if path and Path(path).is_file()), None)
    if executable is None:
        pytest.skip("Install Playwright browser extra and set GEMINI_VIEWER_TEST_BROWSER to Chrome/Edge")
    try:
        return playwright.chromium.launch(headless=True, executable_path=executable)
    except PlaywrightError as exc:
        pytest.skip(f"Configured Chromium browser could not launch: {type(exc).__name__}")


def _open_viewer(
    browser,
    blocks,
    viewport=(390, 844),
    color_scheme="light",
    viewer_html=IMAGE_VIEWER_HTML,
    frame_height=None,
    mcp_host=False,
):
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, color_scheme=color_scheme)
    source = _sandboxed_html(blocks, viewer_html, include_openai=not mcp_host)
    frame_height = frame_height or viewport[1]
    host = ""
    if mcp_host:
        result = {
            "content": [
                {"type": "image", "mimeType": block["mime"], "data": block["data"]}
                for block in blocks
            ],
            "structuredContent": {
                "artifacts": [
                    {"filename": block["filename"], "width": block["width"], "height": block["height"]}
                    for block in blocks
                ],
                "prompt": "a test image",
                "conversation_id": ["test-conversation", "test-request"],
            },
        }
        host = """<script>
          window.__hostMessages=[];window.__hostResult=__RESULT__;
          window.addEventListener('message',event=>{
            const message=event.data;window.__hostMessages.push(message);
            if(message&&message.method==='ui/initialize')event.source.postMessage({jsonrpc:'2.0',id:message.id,result:{
              protocolVersion:'2026-01-26',hostCapabilities:{},hostInfo:{name:'test-host',version:'1'},
              hostContext:{availableDisplayModes:['inline','fullscreen'],displayMode:'inline',theme:'light'}}},'*');
            if(message&&message.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.__hostResult},'*');
            if(message&&message.method==='tools/call')event.source.postMessage({jsonrpc:'2.0',id:message.id,result:window.__hostResult},'*');
            if(message&&message.method==='ui/request-display-mode')event.source.postMessage({jsonrpc:'2.0',id:message.id,result:{mode:message.params.mode}},'*');
            if(message&&message.method==='ui/notifications/size-changed'){
              const frame=document.querySelector('iframe');
              if(frame&&Number(message.params&&message.params.height)>0)frame.style.height=`${message.params.height}px`;
            }
          });
        </script>""".replace("__RESULT__", json.dumps(result))
    page.set_content(
        "<style>html,body{margin:0;width:100%;height:100%;overflow:hidden}iframe{display:block;width:100%;border:0}</style>"
        "<script>window.__downloads=[];window.addEventListener('message',e=>{window.__downloads.push(e.data);"
        "if(e.data&&e.data.kind==='intrinsic-height'){const f=document.querySelector('iframe');"
        "if(f&&Number(e.data.height)>0)f.style.height=`${e.data.height}px`;}})</script>"
        + host
        +
        f"<iframe sandbox='allow-scripts' style='height:{frame_height}px' srcdoc=\""
        + html.escape(source, quote=True)
        + "\"></iframe>"
    )
    return page, page.frame_locator("iframe")


def test_image_viewer_renders_gallery_and_expanded_images_under_data_only_csp():
    blocks = [
        {"mime": "image/png", "data": _image_data("PNG", (640, 960), "red"), "filename": "portrait.png", "width": 640, "height": 960},
        {"mime": "image/jpeg", "data": _image_data("JPEG", (1200, 675), "green"), "filename": "landscape.jpg", "width": 1200, "height": 675},
        {"mime": "image/webp", "data": _image_data("WEBP", (800, 800), "blue"), "filename": "square.webp", "width": 800, "height": 800},
    ]
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            for viewport, theme in (((390, 844), "light"), ((1280, 900), "dark")):
                page, viewer = _open_viewer(browser, blocks, viewport, theme)
                main = viewer.locator("#image")
                main.wait_for(state="visible")
                main.evaluate("i => i.decode()")
                assert main.evaluate("i => [i.complete, i.naturalWidth, i.naturalHeight]") == [True, 640, 960]
                assert viewer.locator("#status").inner_text() == "3 generated images"
                assert viewer.locator("#stage").evaluate("e => getComputedStyle(e).backgroundColor") == "rgba(0, 0, 0, 0)"
                assert viewer.locator("body").evaluate("e => e.scrollHeight <= e.clientHeight")

                viewer.get_by_role("button", name="Next image").click()
                main.evaluate("i => i.decode()")
                assert main.evaluate("i => [i.complete, i.naturalWidth, i.naturalHeight]") == [True, 1200, 675]
                viewer.get_by_role("button", name="Next image").click()
                main.evaluate("i => i.decode()")
                assert main.evaluate("i => [i.complete, i.naturalWidth, i.naturalHeight]") == [True, 800, 800]

                main.click()
                expanded = viewer.locator("#expandedImage")
                expanded.evaluate("i => i.decode()")
                assert expanded.evaluate("i => [i.complete, i.naturalWidth, i.naturalHeight]") == [True, 800, 800]
                viewer.get_by_role("button", name="Zoom in").click()
                assert viewer.locator("#expandedImage").evaluate("i => i.style.transform") == "scale(1.25)"
                viewer.locator("#expandedPrevious").click()
                expanded.evaluate("i => i.decode()")
                assert expanded.evaluate("i => [i.complete, i.naturalWidth, i.naturalHeight]") == [True, 1200, 675]
                viewer.get_by_role("button", name="Close expanded image").click()
                assert main.evaluate("i => [i.complete, i.naturalWidth, i.naturalHeight]") == [True, 1200, 675]

                viewer.get_by_role("link", name="Download image").click()
                page.wait_for_function("window.__downloads.some(x => x.kind === 'saved-file')")
                downloaded = page.evaluate("window.__downloads.find(x => x.kind === 'saved-file')")
                assert downloaded["name"] == "landscape.jpg"
                assert downloaded["mime"] == "image/jpeg"
                assert bytes(downloaded["bytes"]) == base64.b64decode(blocks[1]["data"])
                assert main.evaluate("i => i.complete && i.naturalWidth === 1200")
                page.close()
            browser.close()
        finally:
            if browser.is_connected():
                browser.close()


def test_image_viewer_reports_actual_decode_failure():
    invalid = {
        "mime": "image/png",
        "data": base64.b64encode(b"\x89PNG\r\n\x1a\nnot-a-decodable-image").decode("ascii"),
        "filename": "broken.png",
        "width": 1,
        "height": 1,
    }
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            _page, viewer = _open_viewer(browser, [invalid])
            viewer.get_by_text("This image could not be displayed.").wait_for(state="visible")
            assert viewer.locator("#image").evaluate("i => i.complete && i.naturalWidth === 0")
            assert viewer.locator("#status").inner_text() == "Image unavailable"
        finally:
            browser.close()


def test_image_viewer_uses_standard_mcp_apps_host_bridge_without_openai_globals():
    block = {
        "mime": "image/png",
        "data": _image_data("PNG", (960, 540), "purple"),
        "filename": "mcp-app.png",
        "width": 960,
        "height": 540,
    }
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            page, viewer = _open_viewer(browser, [block], mcp_host=True)
            image = viewer.locator("#image")
            image.wait_for(state="visible")
            image.evaluate("item => item.decode()")
            page.wait_for_function("window.__hostMessages.some(m => m.method === 'ui/notifications/initialized')")
            page.wait_for_function("window.__hostMessages.some(m => m.method === 'ui/notifications/size-changed')")
            assert viewer.locator("#image").evaluate("item => [item.naturalWidth,item.naturalHeight]") == [960, 540]

            viewer.get_by_role("button", name="Regenerate").click()
            page.wait_for_function("window.__hostMessages.some(m => m.method === 'tools/call')")
            tool_call = page.evaluate("window.__hostMessages.find(m => m.method === 'tools/call')")
            assert tool_call["params"]["name"] == "gemini_generate_image"
            assert tool_call["params"]["arguments"]["prompt"] == "a test image"

            viewer.locator("#expand").click()
            page.wait_for_function("window.__hostMessages.some(m => m.method === 'ui/request-display-mode')")
            display_request = page.evaluate("window.__hostMessages.find(m => m.method === 'ui/request-display-mode')")
            assert display_request["params"]["mode"] == "fullscreen"
            page.close()
        finally:
            browser.close()


def test_image_viewer_fits_images_without_iframe_scrolling_across_viewports():
    viewports = ((360, 640), (430, 932), (768, 1024), (1280, 800))
    dimensions_list = ((896, 1200), (1376, 768), (1024, 1024))
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        try:
            for viewport in viewports:
                for dimensions in dimensions_list:
                    width, height = dimensions
                    block = {
                        "mime": "image/png",
                        "data": _image_data("PNG", dimensions, "teal"),
                        "filename": "geometry.png",
                        "width": width,
                        "height": height,
                    }
                    page, viewer = _open_viewer(
                        browser,
                        [block],
                        viewport=viewport,
                        # Reproduce hosts that initially allocate a tiny iframe.
                        frame_height=80,
                    )
                    main = viewer.locator("#image")
                    main.wait_for(state="visible")
                    main.evaluate("image => image.decode()")
                    page.wait_for_function(
                        "document.querySelector('iframe').getBoundingClientRect().height > 100"
                    )
                    viewer.locator("body").evaluate(
                        "body => new Promise(resolve => { const settled = () => "
                        "body.clientHeight >= body.scrollHeight - 1 ? resolve() : "
                        "requestAnimationFrame(settled); settled(); })"
                    )
                    inline_geometry = viewer.locator("body").evaluate(
                        "body => ({client:body.clientHeight, scroll:body.scrollHeight, "
                        "image:document.querySelector('#image').getBoundingClientRect().toJSON(), "
                        "stage:document.querySelector('#stage').getBoundingClientRect().toJSON(), "
                        "toolbar:document.querySelector('.controls').getBoundingClientRect().toJSON()})"
                    )
                    assert inline_geometry["scroll"] <= inline_geometry["client"] + 1, (
                        f"inline overflow at viewport={viewport}, image={dimensions}: {inline_geometry}"
                    )
                    image_box, stage_box = inline_geometry["image"], inline_geometry["stage"]
                    toolbar_box = inline_geometry["toolbar"]
                    expected_width = min(width, viewport[0] - 8, 1200)
                    assert image_box["width"] >= expected_width * 0.95, (
                        f"collapsed image at viewport={viewport}, image={dimensions}: {inline_geometry}"
                    )
                    assert image_box["height"] > 64
                    assert image_box["top"] >= stage_box["top"] - 1
                    assert image_box["bottom"] <= stage_box["bottom"] + 1
                    assert toolbar_box["bottom"] <= image_box["top"] + 1
                    assert abs(image_box["width"] / image_box["height"] - width / height) < 0.01

                    viewer.locator("#expand").click()
                    expanded_geometry = viewer.locator("#expandedImageWrap").evaluate(
                        "wrap => ({client:wrap.clientHeight, scroll:wrap.scrollHeight, "
                        "image:document.querySelector('#expandedImage').getBoundingClientRect().toJSON(), "
                        "wrapBox:wrap.getBoundingClientRect().toJSON()})"
                    )
                    assert expanded_geometry["scroll"] <= expanded_geometry["client"] + 1, (
                        f"expanded overflow at viewport={viewport}, image={dimensions}: {expanded_geometry}"
                    )
                    image_box, wrap_box = expanded_geometry["image"], expanded_geometry["wrapBox"]
                    assert image_box["top"] >= wrap_box["top"] - 1
                    assert image_box["bottom"] <= wrap_box["bottom"] + 1
                    assert abs(image_box["width"] / image_box["height"] - width / height) < 0.01
                    assert abs((image_box["left"] + image_box["right"] - wrap_box["left"] - wrap_box["right"]) / 2) < 2
                    assert viewer.locator("#expandedBar").evaluate(
                        "bar => { const r=bar.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight }"
                    )
                    viewer.get_by_role("button", name="Zoom in").click()
                    assert viewer.locator("#expandedImageWrap").evaluate(
                        "e => getComputedStyle(e).overflow"
                    ) == "auto"
                    viewer.get_by_role("button", name="Reset zoom").click()
                    assert viewer.get_by_role("button", name="Reset zoom").inner_text() == "Fit"
                    assert viewer.locator("#expandedImageWrap").evaluate(
                        "e => e.scrollHeight <= e.clientHeight + 1"
                    )
                    viewer.get_by_role("button", name="Close expanded image").click()
                    assert main.evaluate("image => image.naturalHeight") == height
                    page.close()
            browser.close()
        finally:
            if browser.is_connected():
                browser.close()
