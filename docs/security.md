# Security and operations

The account registry and durable OAuth state use AES-256-GCM encryption.
Google cookies remain in isolated Chromium profiles and are not returned in
MCP results. Account selection and media artifacts enforce ownership checks;
private storage paths are not public URLs. Browser handoff links are short-lived
capabilities and must be kept out of logs and referrers. Upload and media
validation apply type/size boundaries; never use URL import as permission to
fetch private-network resources.

The main threats are compromise of the service host or encryption key, exposure
of a handoff capability, overly broad MCP caller authorization, unsafe public
port exposure, malicious uploaded content, and upstream account/session
compromise. Mitigate with least-privilege service users, HTTPS, token/scope
checks, per-account profiles, private storage and backups, host firewalling,
bounded uploads, reviewed egress controls, and sanitized diagnostics.

Keep keys separate from source control and back them up with the encrypted
state they unlock. Do not expose raw MCP/VNC ports, enable privileged
containers, disable seccomp as a blanket workaround, or delete volumes to
troubleshoot. See [threat model](security-threat-model.md) and
[backup guidance](backup-and-restore.md).
