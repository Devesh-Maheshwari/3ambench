"""Minimal client: `AlertForgeEnv(base_url=...)` over the OpenEnv WebSocket protocol."""

from openenv.core.generic_client import GenericEnvClient


class AlertForgeEnv(GenericEnvClient):
    """Actions are plain dicts, e.g. {"tool": "read_file", "path": "README.md"}."""
