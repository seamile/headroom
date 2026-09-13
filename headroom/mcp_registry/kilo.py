"""Kilo MCP registrar.

Kilo keeps OpenCode's ``mcp`` config schema verbatim (verified against the
compiled binary's embedded docs: ``{"mcp": {"name": {"type": "local",
"command": [...], "enabled": true}}}``). It stores that block in its own config
file (``~/.config/kilo/kilo.json``, or ``$KILO_CONFIG``), so this registrar
reuses :class:`OpencodeRegistrar`'s JSON logic with Kilo's path and identity.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from headroom.install.paths import kilo_config_path

from .opencode import OpencodeRegistrar


class KiloRegistrar(OpencodeRegistrar):
    """Register MCP servers with Kilo."""

    name = "kilo"
    display_name = "Kilo"

    def __init__(self, *, config_path: Path | None = None) -> None:
        super().__init__(config_path=config_path or kilo_config_path())

    def detect(self) -> bool:
        if shutil.which("kilo") or shutil.which("kilocode"):
            return True
        return self._config_path.parent.is_dir()
