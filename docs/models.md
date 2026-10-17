# Models and capability discovery

`gemini_list_models` reports discovered model metadata and confidence or
routability information. `gemini_video_capabilities` and
`notebooklm_capabilities` report separate capability boundaries; discovery is
not proof that an operation is available to every account.

| Capability | Status |
|---|---|
| Gemini Flash-Lite chat | Implemented; availability depends on account/upstream. |
| Gemini Flash chat | Implemented; availability depends on account/upstream. |
| Gemini Pro routing | Not verified as reliable after upstream protocol regression. |
| Independent thinking levels | Mapping remains unverified; do not assume a setting is honored. |
| Model discovery | Implemented with observed/fallback metadata. |

Use diagnostics and returned capability metadata rather than assuming a model
name is routable. If upstream rejects a request, preserve the error state; do
not claim successful generation. Model availability and account entitlements
can change independently of this package.
