from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_container_bakes_provenance_into_runtime_package():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG VCS_REF=unknown" in dockerfile
    assert "/app/src/gemini_webapi_mcp/_build.json" in dockerfile
    assert '"$VCS_REF" "$BUILD_DATE" "$APP_VERSION"' in dockerfile


def test_publish_workflow_passes_and_verifies_immutable_commit():
    workflow = (ROOT / ".github" / "workflows" / "publish-ghcr.yml").read_text(encoding="utf-8")
    assert "VCS_REF=${{ github.sha }}" in workflow
    assert 'sha-${GITHUB_SHA}' in workflow
    assert "assert p['commit'] == '${GITHUB_SHA}'" in workflow
    assert "'gemini_generate_image' in mcp._tool_manager._tools" in workflow
    assert 'ref=${REGISTRY}/${IMAGE_NAME,,}:sha-${GITHUB_SHA}' in workflow
    assert "--entrypoint /app/.venv/bin/python" in workflow
    assert "Smoke test container startup" in workflow
    assert "http://127.0.0.1:18000/mcp" in workflow
    assert 'branches: [main]' in workflow
    assert 'tags: ["v*"]' in workflow
    assert "type=raw,value=latest,enable={{is_default_branch}}" in workflow
    assert "type=raw,value=1.0,enable={{is_default_branch}}" in workflow
    assert "type=semver,pattern={{version}}" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow
    assert "push: ${{ github.event_name != 'pull_request' }}" in workflow
    assert "load: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "provenance: ${{ github.event_name != 'pull_request' && 'mode=max' || 'false' }}" in workflow
    assert "sbom: ${{ github.event_name != 'pull_request' }}" in workflow
