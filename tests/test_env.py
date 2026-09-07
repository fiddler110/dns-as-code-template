"""Tests for dnsctl_lib/env.py: load_env_file's parsing and
load_cloudflare_env's .env -> environment -> Key Vault fallback order."""
from __future__ import annotations


def test_load_env_file_missing_file_returns_empty(mod, tmp_path):
    assert mod.load_env_file(tmp_path / "does-not-exist.env") == {}


def test_load_env_file_parses_quotes_comments_and_blanks(mod, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment line\n"
        "\n"
        'DOUBLE_QUOTED="value with spaces"\n'
        "SINGLE_QUOTED='also spaces'\n"
        "BARE=nospaces\n"
        "  SPACED_KEY = trims-surrounding-whitespace  \n"
        "# NOT_A_KEY=ignored\n"
        "no_equals_sign_here",
        encoding="utf-8",
    )

    env = mod.load_env_file(env_file)

    assert env == {
        "DOUBLE_QUOTED": "value with spaces",
        "SINGLE_QUOTED": "also spaces",
        "BARE": "nospaces",
        "SPACED_KEY": "trims-surrounding-whitespace",
    }


def test_load_cloudflare_env_prefers_env_file_token_over_vault(mod, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('CLOUDFLARE_API_TOKEN="from-dot-env"\nCLOUDFLARE_KEYVAULT_NAME=some-vault\n', encoding="utf-8")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    def fail_if_called(vault, secret_name):
        raise AssertionError("Key Vault should not be consulted when .env already has a token")

    monkeypatch.setattr(mod.env_lib, "fetch_keyvault_secret", fail_if_called)

    env = mod.load_cloudflare_env(env_file)
    assert env["CLOUDFLARE_API_TOKEN"] == "from-dot-env"


def test_load_cloudflare_env_prefers_process_environment_over_vault(mod, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CLOUDFLARE_KEYVAULT_NAME=some-vault\n", encoding="utf-8")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "from-process-env")

    def fail_if_called(vault, secret_name):
        raise AssertionError("Key Vault should not be consulted when the environment already has a token")

    monkeypatch.setattr(mod.env_lib, "fetch_keyvault_secret", fail_if_called)

    env = mod.load_cloudflare_env(env_file)
    assert "CLOUDFLARE_API_TOKEN" not in env  # not written back to .env's dict, just left to os.environ


def test_load_cloudflare_env_returns_unchanged_with_no_token_and_no_vault(mod, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SOME_OTHER_KEY=value\n", encoding="utf-8")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_KEYVAULT_NAME", raising=False)

    def fail_if_called(vault, secret_name):
        raise AssertionError("Key Vault should not be consulted when no vault is configured")

    monkeypatch.setattr(mod.env_lib, "fetch_keyvault_secret", fail_if_called)

    env = mod.load_cloudflare_env(env_file)
    assert env == {"SOME_OTHER_KEY": "value"}


def test_load_cloudflare_env_fetches_from_vault_when_no_token_present(mod, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CLOUDFLARE_KEYVAULT_NAME=test-vault\n", encoding="utf-8")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    calls = []

    def fake_fetch(vault, secret_name):
        calls.append((vault, secret_name))
        return "fetched-from-vault"

    monkeypatch.setattr(mod.env_lib, "fetch_keyvault_secret", fake_fetch)

    env = mod.load_cloudflare_env(env_file)

    assert env["CLOUDFLARE_API_TOKEN"] == "fetched-from-vault"
    assert calls == [("test-vault", "cloudflare-api-token-readonly")]


def test_load_cloudflare_env_respects_custom_secret_name(mod, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLOUDFLARE_KEYVAULT_NAME=test-vault\n"
        "CLOUDFLARE_KEYVAULT_SECRET_NAME=custom-secret-name\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    calls = []
    monkeypatch.setattr(
        mod.env_lib, "fetch_keyvault_secret",
        lambda vault, secret_name: calls.append((vault, secret_name)) or "token",
    )

    mod.load_cloudflare_env(env_file)
    assert calls == [("test-vault", "custom-secret-name")]


def test_load_cloudflare_env_leaves_token_unset_when_vault_fetch_fails(mod, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CLOUDFLARE_KEYVAULT_NAME=test-vault\n", encoding="utf-8")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setattr(mod.env_lib, "fetch_keyvault_secret", lambda vault, secret_name: None)

    env = mod.load_cloudflare_env(env_file)
    assert "CLOUDFLARE_API_TOKEN" not in env


# --- has_cloudflare_token / require_cloudflare_token (TOOL-12) --------------


def test_has_cloudflare_token_true_from_env_dict(mod, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert mod.has_cloudflare_token({"CLOUDFLARE_API_TOKEN": "x"}) is True


def test_has_cloudflare_token_true_from_process_environment(mod, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    assert mod.has_cloudflare_token({}) is True


def test_has_cloudflare_token_false_when_absent_from_both(mod, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert mod.has_cloudflare_token({}) is False


def test_require_cloudflare_token_true_when_present(mod, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert mod.require_cloudflare_token({"CLOUDFLARE_API_TOKEN": "x"}) is True


def test_require_cloudflare_token_false_prints_standard_error_when_absent(mod, monkeypatch, capsys):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert mod.require_cloudflare_token({}) is False
    err = capsys.readouterr().err
    assert "CLOUDFLARE_API_TOKEN not set in .env, the environment, or Key Vault." in err


def test_require_cloudflare_token_prints_extra_hint_when_given(mod, monkeypatch, capsys):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    assert mod.require_cloudflare_token({}, "       Run: python scripts/dnsctl.py setup") is False
    err = capsys.readouterr().err
    assert "Run: python scripts/dnsctl.py setup" in err
