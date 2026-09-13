"""Runtime helpers for Kilo integrations."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from headroom.providers.opencode.config import HEADROOM_OPENCODE_PLUGIN
from headroom.providers.opencode.runtime import (
    build_opencode_config_content,
    headroom_opencode_plugin_path,
)


def kilo_plugin_path() -> str | None:
    """Return the absolute path to the OpenCode-compatible transport plugin.

    Kilo is an OpenCode fork and loads ``plugin`` entries from an absolute
    filesystem path (verified in the compiled binary: an absolute ``package``
    is converted to a ``file://`` URL before import). We reuse the same
    transport bundle as OpenCode.
    """
    return headroom_opencode_plugin_path()


def build_kilo_launch_env(
    port: int,
    environ: Mapping[str, str] | None = None,
    project: str | None = None,
    *,
    include_mcp: bool = True,
    include_plugin: bool = True,
) -> tuple[dict[str, str], list[str]]:
    """Build environment variables for launching Kilo through Headroom.

    Kilo renamed the OpenCode payload env var to ``KILO_CONFIG_CONTENT``; the
    JSON shape (``provider``/``mcp``/``plugin``) is identical, so we reuse the
    OpenCode config builder and only swap the env var name.
    """
    env = dict(environ or os.environ)

    config_content = build_opencode_config_content(
        port=port,
        include_mcp=include_mcp,
        include_plugin=include_plugin,
    )
    env["KILO_CONFIG_CONTENT"] = json.dumps(config_content, separators=(",", ":"))

    display = ["KILO_CONFIG_CONTENT={provider: headroom}"]
    if "plugin" in config_content:
        env["HEADROOM_PROXY_URL"] = f"http://127.0.0.1:{port}"
        display.append(f"plugin={HEADROOM_OPENCODE_PLUGIN} (Kilo Code-compatible transport)")

    if project and "HEADROOM_PROJECT" not in env:
        env["HEADROOM_PROJECT"] = project

    return env, display
