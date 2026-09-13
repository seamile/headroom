"""Kilo config file helpers for wrap and persistent install.

Kilo keeps OpenCode's ``mcp``/``provider`` config schema but renamed the
process-level config payload to ``KILO_CONFIG_CONTENT`` (there is no
``OPENCODE_CONFIG_CONTENT`` in the Kilo binary). The global config directory is
``$XDG_CONFIG_HOME/kilo`` (default ``~/.config/kilo``); a ``KILO_CONFIG`` env var
overrides the target file. Kilo still reads the legacy ``opencode.json`` /
``opencode.jsonc`` / ``config.json`` names from *its own* directory.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import click

from headroom import fsutil
from headroom.install.paths import kilo_config_path
from headroom.providers.opencode.config import (
    _MCP_MARKER_START,
    _PROVIDER_MARKER_START,
    _inject_key_into_json,
    _parse_json_loose,
    headroom_provider_entry,
    strip_opencode_headroom_blocks,
)


def kilo_config_paths() -> tuple[Path, Path]:
    """Return ``(config_file, backup_file)`` for Kilo.

    The backup name is Kilo-specific (``kilo.json.headroom-backup``), so
    ``wrap kilo`` can never collide with an OpenCode backup.
    """
    config_file = kilo_config_path()
    backup_file = config_file.with_name(config_file.name + ".headroom-backup")
    return config_file, backup_file


def snapshot_kilo_config_if_unwrapped(config_file: Path, backup_file: Path) -> None:
    """Snapshot the Kilo config to ``backup_file`` before the first injection."""
    if backup_file.exists() or not config_file.exists():
        return
    try:
        content = fsutil.read_text(config_file)
    except OSError:
        return
    if _PROVIDER_MARKER_START in content or _MCP_MARKER_START in content:
        return
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_file, backup_file)


_JSONC_TRAILING_COMMA_RE = re.compile(r",(\s*(?://[^\n]*\n\s*)*[}\]])")


def _parse_kilo_config_for_write(content: str) -> dict:
    """Parse Kilo config text, refusing to clobber a config we can't read.

    Kilo's config files are JSONC: they may contain ``//`` comments and
    trailing commas. We drop trailing commas (including ones followed by a
    comment) and delegate comments/loose parsing to the shared parser, then
    abort rather than overwrite a config we could not understand.
    """
    normalized = _JSONC_TRAILING_COMMA_RE.sub(r"\1", content)
    data = _parse_json_loose(normalized)
    if not data and content.strip() not in ("", "{}"):
        raise click.ClickException(
            "existing Kilo config is not valid JSON/JSONC; refusing to overwrite it"
        )
    return data


def kilo_config_has_headroom(content: str) -> bool:
    """Return True when ``content`` contains any Headroom-managed Kilo config."""
    if _PROVIDER_MARKER_START in content or _MCP_MARKER_START in content:
        return True
    data = _parse_json_loose(content)
    provider = data.get("provider")
    if isinstance(provider, dict) and "headroom" in provider:
        return True
    mcp = data.get("mcp")
    return isinstance(mcp, dict) and "headroom" in mcp


def strip_kilo_headroom_config(content: str, *, remove_mcp: bool = True) -> str:
    """Remove all Headroom-managed content from a Kilo config.

    ``unwrap kilo`` cannot rely on the pre-wrap backup alone: when the user had
    no config before wrapping there is nothing to restore. Unlike OpenCode's
    marker-comment blocks, our injected provider is a plain JSON key, so this
    strips both the marker blocks and the ``provider.headroom`` / ``mcp.headroom``
    keys. Returns ``""`` when only Headroom content remained.
    """
    content = strip_opencode_headroom_blocks(content, remove_mcp=remove_mcp)
    data = _parse_json_loose(content)
    if not data:
        return ""

    provider = data.get("provider")
    if isinstance(provider, dict):
        provider.pop("headroom", None)
        if not provider:
            data.pop("provider", None)

    if remove_mcp:
        mcp = data.get("mcp")
        if isinstance(mcp, dict):
            mcp.pop("headroom", None)
            if not mcp:
                data.pop("mcp", None)

    if not data:
        return ""
    return json.dumps(data, indent=2) + "\n"


def inject_kilo_provider_config(port: int) -> None:
    """Inject a Headroom model provider into Kilo's config file.

    Mirrors ``inject_opencode_provider_config`` but targets Kilo's config path
    and backup namespace. Safe to call repeatedly — the injected provider is
    fully replaced each time.
    """
    config_file, backup_file = kilo_config_paths()

    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        snapshot_kilo_config_if_unwrapped(config_file, backup_file)

        content = fsutil.read_text(config_file) if config_file.exists() else ""

        if _PROVIDER_MARKER_START in content or _MCP_MARKER_START in content:
            content = strip_opencode_headroom_blocks(content)

        data = _parse_kilo_config_for_write(content)
        data = _inject_key_into_json(data, "provider", {"headroom": headroom_provider_entry(port)})

        config_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"could not write Kilo config at {config_file}: {exc}") from exc
