# NotebookLM capability status

NotebookLM is treated as a separate product and authentication boundary from
Gemini Web. Browser access was observed, but no supported consumer notebook
management, source upload, grounded chat, or Studio generation tools are
registered. `notebooklm_capabilities` reports this boundary; it is not an
operational NotebookLM client.

Google Workspace/NotebookLM Enterprise documentation does not establish that
the corresponding APIs or entitlements are available to a consumer account.
No private request schema is guessed, and the project does not bypass paid
tiers or access controls. Until an authorized, verified adapter exists, use
NotebookLM directly in its official interface. See [feature matrix](feature-matrix.md).
