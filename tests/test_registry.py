import json
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

import pytest

from gemini_webapi_mcp.registry import DynamicAccountRegistry, generate_key


def _register_from_worker(args):
    path, key, subject, account = args
    DynamicAccountRegistry(path, key).register(subject=subject, account=account)
    return account


def test_registry_encrypts_and_reloads(tmp_path):
    path = tmp_path / "registry.json.enc"
    key = generate_key()
    registry = DynamicAccountRegistry(path, key)
    registry.register(subject="chatgpt-user", account="personal", scopes={"chat", "image"})
    raw = path.read_text()
    assert "chatgpt-user" not in raw
    assert "personal" not in raw
    loaded = DynamicAccountRegistry(path, key)
    assert loaded.select("chatgpt-user")["account"] == "personal"
    assert loaded.list_safe("chatgpt-user")[0]["scopes"] == ["chat", "image"]


def test_registry_rejects_wrong_key_and_cross_owner(tmp_path):
    path = tmp_path / "registry.json.enc"
    first = DynamicAccountRegistry(path, generate_key())
    first.register(subject="one", account="shared")
    with pytest.raises(ValueError):
        DynamicAccountRegistry(path, generate_key())
    with pytest.raises(PermissionError):
        first.register(subject="two", account="shared")


def test_registry_requires_explicit_selection_for_multiple_accounts(tmp_path):
    registry = DynamicAccountRegistry(tmp_path / "registry.json.enc", generate_key())
    registry.register(subject="user", account="one")
    registry.register(subject="user", account="two")
    with pytest.raises(PermissionError):
        registry.select("user")
    assert registry.select("user", "two")["profile"] == "two"
    registry.set_state("two", "disabled")
    with pytest.raises(PermissionError):
        registry.select("user", "two")


def test_registry_selection_survives_reload(tmp_path):
    path = tmp_path / "registry.json.enc"
    key = generate_key()
    registry = DynamicAccountRegistry(path, key)
    registry.register(subject="user", account="one")
    registry.register(subject="user", account="two")
    registry.select("user", "two")
    restarted = DynamicAccountRegistry(path, key)
    assert restarted.selected("user")["account"] == "two"


def test_registry_concurrent_workers_preserve_all_accounts(tmp_path):
    path = tmp_path / "registry.json.enc"
    key = generate_key()
    jobs = [(str(path), key, f"subject-{index}", f"account-{index}") for index in range(6)]
    with ProcessPoolExecutor(max_workers=6, mp_context=multiprocessing.get_context("spawn")) as pool:
        assert sorted(pool.map(_register_from_worker, jobs)) == [f"account-{index}" for index in range(6)]
    accounts = DynamicAccountRegistry(path, key).list_safe()
    assert {item["account"] for item in accounts} == {f"account-{index}" for index in range(6)}
