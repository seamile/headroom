"""Tests for `headroom wrap kilocode` and `headroom unwrap kilocode`."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from headroom.cli import wrap as wrap_mod
from headroom.cli.main import main
from headroom.copilot_auth import CopilotSubscriptionTokenResolution
from headroom.install.paths import kilo_config_path
from headroom.mcp_registry import KiloRegistrar
from headroom.providers.kilo.config import inject_kilo_provider_config
from headroom.providers.kilo.runtime import build_kilo_launch_env


@pytest.fixture(autouse=True)
def _no_retired_context_tool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADROOM_CONTEXT_TOOL", raising=False)


@pytest.fixture(autouse=True)
def _mock_ensure_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_ensure_proxy(port: int, no_proxy: bool, **kwargs):  # noqa: ANN002, ANN003
        return None, port

    monkeypatch.setattr(wrap_mod, "_ensure_proxy", fake_ensure_proxy)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _set_test_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("KILO_CONFIG", raising=False)
    monkeypatch.delenv("KILO_CONFIG_CONTENT", raising=False)


def _kilo_config_file(tmp_path: Path) -> Path:
    return tmp_path / ".config" / "kilo" / "kilo.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


class _FakeProxy:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


# ---------------------------------------------------------------------------
# Entry resolution
# ---------------------------------------------------------------------------


def test_wrap_kilocode_prefers_kilocode_binary(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    def fake_which(name: str) -> str | None:
        return "/usr/bin/kilo" if name == "kilo" else "/usr/bin/kilocode"

    with (
        patch.object(wrap_mod.shutil, "which", side_effect=fake_which),
        patch.object(wrap_mod, "_launch_tool", side_effect=lambda **kw: captured.update(kw)),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code == 0, result.output
    assert captured["binary"] == "/usr/bin/kilocode"


def test_wrap_kilocode_falls_back_to_kilo_binary(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    def fake_which(name: str) -> str | None:
        if name == "kilo":
            return "/usr/bin/kilo"
        return "/usr/bin/opencode" if name == "opencode" else None

    with (
        patch.object(wrap_mod.shutil, "which", side_effect=fake_which),
        patch.object(wrap_mod, "_launch_tool", side_effect=lambda **kw: captured.update(kw)),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code == 0, result.output
    assert captured["binary"] == "/usr/bin/kilo"
    assert captured["tool_label"] == "KILOCODE"
    assert captured["agent_type"] == "kilo"


def test_wrap_kilocode_missing_binary_does_not_mutate_config(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    with patch.object(wrap_mod.shutil, "which", return_value=None):
        result = runner.invoke(main, ["wrap", "kilocode"])

    assert result.exit_code == 1
    assert "'kilocode' not found in PATH" in result.output
    assert "https://kilo.ai" in result.output
    assert not _kilo_config_file(tmp_path).exists()


def test_wrap_kilocode_passes_unknown_args_through(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    captured: dict[str, object] = {}

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_launch_tool", side_effect=lambda **kw: captured.update(kw)),
    ):
        result = runner.invoke(
            main, ["wrap", "kilocode", "--no-mcp", "--no-serena", "--", "run", "hi"]
        )

    assert result.exit_code == 0, result.output
    assert captured["args"] == ("run", "hi")


# ---------------------------------------------------------------------------
# Proxy lifecycle + provider env
# ---------------------------------------------------------------------------


def test_wrap_kilocode_routes_env_and_shares_proxy(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    monkeypatch.setenv("KILO_KEEP_ME", "1")
    captured: dict[str, object] = {}

    def fake_ensure_proxy(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["ensure"] = kwargs
        return None, 9911

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_ensure_proxy", side_effect=fake_ensure_proxy),
        patch.object(wrap_mod, "_launch_tool", side_effect=lambda **kw: captured.update(kw)),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code == 0, result.output
    assert captured["ensure"]["agent_type"] == "kilo"
    env = captured["env"]
    assert captured["port"] == 9911
    assert env["KILO_KEEP_ME"] == "1"
    content = json.loads(env["KILO_CONFIG_CONTENT"])
    assert content["provider"]["headroom"]["options"]["baseURL"].endswith(":9911/v1")
    assert content["provider"]["anthropic"]["options"]["baseURL"].endswith(":9911/v1")
    assert content["provider"]["openai"]["options"]["baseURL"].endswith(":9911/v1")
    assert "mcp" not in content
    assert captured["tool_label"] == "KILOCODE"


def test_wrap_kilocode_terminates_private_proxy_on_exit(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    proxy = _FakeProxy()

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_ensure_proxy", return_value=(proxy, 8787)),
        patch.object(wrap_mod, "_live_proxy_clients", return_value=[]),
        patch.object(wrap_mod, "_launch_tool", return_value=None),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code == 0, result.output
    assert proxy.terminated


def test_wrap_kilocode_config_write_failure_still_cleans_proxy(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    proxy = _FakeProxy()

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_ensure_proxy", return_value=(proxy, 8787)),
        patch.object(wrap_mod, "_live_proxy_clients", return_value=[]),
        patch.object(
            wrap_mod,
            "inject_kilo_provider_config",
            side_effect=wrap_mod.click.ClickException("boom"),
        ),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code != 0
    assert proxy.terminated


def test_wrap_kilocode_prepare_only_writes_config_without_launch(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    def fail_launch(**kwargs):  # noqa: ANN003
        raise AssertionError("prepare-only must not launch Kilo Code")

    with patch.object(wrap_mod, "_launch_tool", side_effect=fail_launch):
        result = runner.invoke(
            main, ["wrap", "kilocode", "--prepare-only", "--no-mcp", "--no-serena"]
        )

    assert result.exit_code == 0, result.output
    data = _read_json(_kilo_config_file(tmp_path))
    assert "headroom" in data["provider"]


# ---------------------------------------------------------------------------
# MCP / Serena / memory
# ---------------------------------------------------------------------------


def test_wrap_kilocode_registers_headroom_mcp_by_default(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_launch_tool", return_value=None),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-serena"])

    assert result.exit_code == 0, result.output
    data = _read_json(_kilo_config_file(tmp_path))
    assert data["mcp"]["headroom"]["type"] == "local"
    assert data["mcp"]["headroom"]["command"][1:] == ["mcp", "serve"]


def test_wrap_kilocode_no_mcp_skips_registration(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_launch_tool", return_value=None),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code == 0, result.output
    data = _read_json(_kilo_config_file(tmp_path))
    assert "mcp" not in data
    assert "headroom" in data["provider"]


def test_wrap_kilocode_no_serena_disables_ledger_owned_serena(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    calls: list[str] = []

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_launch_tool", return_value=None),
        patch.object(
            wrap_mod,
            "_disable_serena_mcp",
            side_effect=lambda registrar, **kw: calls.append(registrar.name),
        ),
    ):
        result = runner.invoke(main, ["wrap", "kilocode", "--no-mcp", "--no-serena"])

    assert result.exit_code == 0, result.output
    assert calls == ["kilo"]


# ---------------------------------------------------------------------------
# Copilot subscription constraints
# ---------------------------------------------------------------------------


def test_wrap_kilocode_copilot_subscription_rejects_translated_backend(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    result = runner.invoke(
        main, ["wrap", "kilocode", "--copilot-subscription", "--backend", "anyllm", "--no-mcp"]
    )

    assert result.exit_code != 0
    assert "cannot be combined with translated backends" in result.output


def test_wrap_kilocode_copilot_subscription_rejects_no_proxy(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    result = runner.invoke(main, ["wrap", "kilocode", "--copilot-subscription", "--no-proxy"])

    assert result.exit_code != 0
    assert "cannot be combined with --no-proxy" in result.output


def test_wrap_kilocode_copilot_subscription_rejects_prepare_only(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    result = runner.invoke(main, ["wrap", "kilocode", "--copilot-subscription", "--prepare-only"])

    assert result.exit_code != 0
    assert "cannot be combined with --prepare-only" in result.output


def test_wrap_kilocode_copilot_subscription_scrubs_secrets(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_COPILOT_API_TOKEN", "inherited-api-secret")
    captured: dict[str, object] = {}

    resolution = CopilotSubscriptionTokenResolution(
        token="copilot-api-secret",
        source="test",
        confidence="test",
        api_url="https://api.githubcopilot.com",
        token_fingerprint="sha256:test",
        refresh_oauth_token="copilot-refresh-secret",
        api_token_expires_at=123.5,
    )

    with (
        patch.object(wrap_mod.shutil, "which", return_value="/usr/bin/kilocode"),
        patch.object(wrap_mod, "_require_copilot_subscription_resolution", return_value=resolution),
        patch.object(wrap_mod, "_launch_tool", side_effect=lambda **kw: captured.update(kw)),
    ):
        result = runner.invoke(
            main, ["wrap", "kilocode", "--copilot-subscription", "--no-mcp", "--no-serena"]
        )

    assert result.exit_code == 0, result.output
    env = captured["env"]
    assert "GITHUB_COPILOT_API_TOKEN" not in env
    assert "copilot-api-secret" not in env["KILO_CONFIG_CONTENT"]


# ---------------------------------------------------------------------------
# Launch display semantics
# ---------------------------------------------------------------------------


def test_kilo_launch_display_labels_opencode_plugin_as_compatible() -> None:
    env, display = build_kilo_launch_env(8787, environ={})

    assert env["KILO_CONFIG_CONTENT"]
    assert "plugin=headroom-opencode (Kilo Code-compatible transport)" in display


# ---------------------------------------------------------------------------
# Unwrap
# ---------------------------------------------------------------------------


def _write_wrapped_config(tmp_path: Path) -> Path:
    config_file = _kilo_config_file(tmp_path)
    inject_kilo_provider_config(8787)
    return config_file


def test_unwrap_kilocode_restores_backup(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    config_file = _kilo_config_file(tmp_path)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('{"model": "user/model"}\n', encoding="utf-8")
    backup_file = config_file.with_name(config_file.name + ".headroom-backup")
    backup_file.write_text('{"model": "user/model"}\n', encoding="utf-8")
    config_file.write_text('{"provider": {"headroom": {}}}\n', encoding="utf-8")

    result = runner.invoke(main, ["unwrap", "kilocode", "--no-stop-proxy"])

    assert result.exit_code == 0, result.output
    assert _read_json(config_file) == {"model": "user/model"}
    assert not backup_file.exists()
    assert "Restored prior" in result.output


def test_unwrap_kilocode_strips_headroom_block_and_preserves_user_content(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)
    config_file = _kilo_config_file(tmp_path)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('{"model": "user/model"}\n', encoding="utf-8")
    inject_kilo_provider_config(8787)
    # No pre-wrap backup: force the strip-and-preserve path.
    config_file.with_name(config_file.name + ".headroom-backup").unlink()

    result = runner.invoke(main, ["unwrap", "kilocode", "--no-stop-proxy"])

    assert result.exit_code == 0, result.output
    data = _read_json(config_file)
    assert data.get("model") == "user/model"
    assert "provider" not in data or "headroom" not in data.get("provider", {})
    assert "Removed Headroom block" in result.output


def test_unwrap_kilocode_is_idempotent_noop(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    first = runner.invoke(main, ["unwrap", "kilocode", "--no-stop-proxy"])
    second = runner.invoke(main, ["unwrap", "kilocode", "--no-stop-proxy"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "Nothing to undo" in second.output


def test_wrap_kilo_command_is_gone(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _set_test_home(monkeypatch, tmp_path)

    result = runner.invoke(main, ["wrap", "kilo", "--help"])

    assert result.exit_code != 0
    assert "No such command" in result.output


# ---------------------------------------------------------------------------
# Config + registrar units
# ---------------------------------------------------------------------------


def test_kilo_config_path_defaults_to_kilo_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    assert kilo_config_path() == tmp_path / ".config" / "kilo" / "kilo.json"


def test_kilo_config_path_honors_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("KILO_CONFIG", raising=False)
    assert kilo_config_path() == tmp_path / "xdg" / "kilo" / "kilo.json"


def test_kilo_config_path_prefers_existing_legacy_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    home = tmp_path / ".config" / "kilo"
    home.mkdir(parents=True)
    (home / "opencode.json").write_text("{}", encoding="utf-8")
    assert kilo_config_path() == home / "opencode.json"


def test_kilo_config_path_honors_kilo_config_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    explicit = tmp_path / "custom.json"
    monkeypatch.setenv("KILO_CONFIG", str(explicit))
    assert kilo_config_path() == explicit


def test_inject_kilo_provider_config_parses_jsonc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    config_file = _kilo_config_file(tmp_path)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('{\n  // keep me\n  "model": "user/model",\n}\n', encoding="utf-8")

    inject_kilo_provider_config(8787)

    data = _read_json(config_file)
    assert data["model"] == "user/model"
    assert "headroom" in data["provider"]
    backup = config_file.with_name(config_file.name + ".headroom-backup")
    assert backup.exists()


def test_inject_kilo_provider_config_refuses_malformed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    config_file = _kilo_config_file(tmp_path)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("this is not json at all", encoding="utf-8")

    with pytest.raises(Exception, match="refusing to overwrite"):
        inject_kilo_provider_config(8787)
    assert config_file.read_text(encoding="utf-8") == "this is not json at all"


def test_kilo_and_opencode_configs_do_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    inject_kilo_provider_config(8787)

    kilo_file = _kilo_config_file(tmp_path)
    assert kilo_file.exists()
    assert not (tmp_path / ".config" / "opencode").exists()
    assert not (tmp_path / ".config" / "kilo" / "opencode.json").exists()


def test_kilo_registrar_identity_and_detect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_test_home(monkeypatch, tmp_path)
    registrar = KiloRegistrar()
    assert registrar.name == "kilo"
    assert registrar.display_name == "Kilo"
    with patch("headroom.mcp_registry.kilo.shutil.which", return_value="/usr/bin/kilocode"):
        assert registrar.detect() is True
