# Feature matrix

“Implemented” means a registered workflow exists; it does not guarantee every
Google account entitlement or ChatGPT host behavior. Capability discovery can
report observations without implying operational support.

| Area | Capability | Status |
|---|---|---|
| Gemini | Flash-Lite and Flash chat | Implemented; account/upstream dependent |
| Gemini | Pro routing | Unverified; upstream regression reported |
| Gemini | Model discovery | Implemented; observed/fallback metadata |
| Gemini | Independent thinking levels | Unverified |
| Gemini | Persistent conversations | Implemented |
| Gemini | File upload and URL analysis | Implemented |
| Gemini | Image generation and editing | Implemented; upstream/account dependent |
| Gemini | Multiple image outputs | Supported when upstream returns them; count not guaranteed |
| Viewer | Native inline images and gallery | Implemented; local browser tested, ChatGPT host varies |
| Viewer | Zoom and fullscreen/expanded view | Implemented; host support may vary |
| Viewer | Download | Implemented with host bridge/fallback; host restrictions may apply |
| Viewer | Regenerate/edit follow-up | Wired to supported image workflow; context/account availability applies |
| Viewer | Mobile responsive layout | Local viewport tests pass; ChatGPT Android not user-confirmed |
| Video | Browser-observed generation/playback | Observed for a test account; not an MCP workflow |
| Video | MCP generation/status/cancellation | Unsupported; no job workflow registered |
| Video | Player foundation/media validation | Implemented infrastructure only; no generated artifact delivery workflow |
| NotebookLM | Consumer browser access | Observed; independent product/auth boundary |
| NotebookLM | Capability discovery | Implemented report only |
| NotebookLM | Notebook management/source upload/grounded chat | Unsupported; no operational tools registered |
| NotebookLM | Studio audio/video/study generation | Unsupported; no verified consumer workflow |
| NotebookLM | Enterprise API | Separate entitlement/product; documentation is not proof of consumer access |

For details see [models](models.md), [media viewer](media-viewer.md),
[video](video.md), and [NotebookLM](notebooklm.md).
