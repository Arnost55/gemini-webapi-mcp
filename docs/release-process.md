# Release process

This is a checklist for a future release. The v1.0.0 tag and GHCR publication
already exist for commit `69b205d1a24c7eaaab69b9e2ca1222f78a042952`; do not
recreate or move that tag.

1. Confirm version metadata and changelog against the exact source commit.
2. Run the full test suite, Ruff, package build, Docker build, and documentation
   link/config checks.
3. Review the diff for secrets, private data, unsupported claims, and
   backward-incompatible storage changes.
4. Confirm CI passes and inspect image revision/version metadata and provenance.
5. Complete staging acceptance, including real ChatGPT desktop/mobile checks
   where the release claims those behaviors. Local browser tests are not a
   substitute.
6. Verify backups and a tested rollback image before production deployment.
7. Obtain explicit authorization before creating a new version tag or GitHub
   Release. Never force-push or silently move a tag.
8. Pin deployments to a full immutable image SHA; deploy separately and only
   after approval.

For existing v1.0.0 image references and recovery details, see
[release-1.0.0.md](release-1.0.0.md). This documentation update itself does not
publish an image, create a release, or deploy production.
