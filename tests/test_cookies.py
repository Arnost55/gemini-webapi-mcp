"""_resolve_cookies must take __Secure-1PSID from the exact `.google.com` domain.

browser_cookie3 matches `domain_name` as a substring, so a jar holding cookies
for `.google.com.bd` (or any other ccTLD) also comes back — and a "last wins"
loop would hand Gemini a foreign-domain session (regression: 2026-09-21, every
request answered 1096 / "Are you signed in?").
"""
import os
import sys
from http.cookiejar import Cookie, CookieJar
import hashlib
import sqlite3
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from gemini_webapi_mcp import server as S  # noqa: E402


def _cookie(name, value, domain):
    return Cookie(0, name, value, None, False, domain, True, domain.startswith("."),
                  "/", True, True, None, False, None, None, {})


def test_resolve_cookies_prefers_exact_google_com_domain(monkeypatch):
    import browser_cookie3

    jar = CookieJar()
    jar.set_cookie(_cookie("__Secure-1PSID", "good-psid", ".google.com"))
    jar.set_cookie(_cookie("__Secure-1PSIDTS", "good-ts", ".google.com"))
    jar.set_cookie(_cookie("__Secure-1PSID", "bd-psid", ".google.com.bd"))
    jar.set_cookie(_cookie("__Secure-1PSIDTS", "bd-ts", ".google.com.bd"))
    monkeypatch.setattr(browser_cookie3, "chrome", lambda **kw: jar)
    monkeypatch.delenv("GEMINI_PSID", raising=False)

    assert S._resolve_cookies() == ("good-psid", "good-ts")


def test_basic_chromium_cookie_reader_handles_v10(tmp_path):
    from gemini_webapi_mcp.server import _read_basic_chromium_cookies

    db = tmp_path / "Cookies"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB, value TEXT)")
    key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, dklen=16)
    iv = b" " * 16
    value = b"psid-test"
    pad = 16 - len(value) % 16
    encrypted = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    blob = encrypted.update(value + bytes([pad]) * pad) + encrypted.finalize()
    con.execute("INSERT INTO cookies VALUES (?, ?, ?, ?)", (".google.com", "__Secure-1PSID", b"v10" + blob, ""))
    con.commit(); con.close()

    assert _read_basic_chromium_cookies(db) == [(".google.com", "__Secure-1PSID", "psid-test")]


def test_account_client_requires_both_session_cookies(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").touch()
    (profile / "Local State").touch()
    monkeypatch.delenv("GEMINI_ACCOUNT_COOKIE_READER", raising=False)
    monkeypatch.setattr(
        S, "_read_basic_chromium_cookies",
        lambda path: [(".google.com", "__Secure-1PSID", "secret-not-returned")],
    )

    psid, psidts = S._AccountClientManager._cookie_values(profile)
    assert psid == "secret-not-returned"
    assert psidts is None
    with pytest.raises(RuntimeError, match="__Secure-1PSIDTS"):
        S._AccountClientManager._cookies(profile)
