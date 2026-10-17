# Backup and restore

Back up the `gemini-state` volume and the external encryption key as separate
protected items, then verify that both backups are readable before relying on
them. The volume includes encrypted registry/OAuth state, browser profiles,
conversation state, handoff metadata, and private artifacts. Treat browser
profiles and generated content as sensitive. Never put backups in Git or
unprotected shared storage.

Use your platform's consistent-volume snapshot/backup procedure while the
service is quiesced as required by that platform. Record the exact immutable
image SHA and matching key version. Do not rotate/delete the key or volume
during an image upgrade. Restore into a private location and validate account
diagnostics and an existing operation before replacing the live service.

For rollback, recreate only the application container with the previous exact
image SHA while reusing the unchanged volume, key, and reviewed environment.
This v1.0.0 viewer release makes no storage migration. Follow the
[release recovery notes](release-1.0.0.md) and
[recovery runbook](recovery-runbook.md); no production deployment is performed
by these instructions.
