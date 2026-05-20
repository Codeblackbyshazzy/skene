"""Shared CLI plumbing for the journey command (path/LLM/config helpers)."""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import SecretStr

from skene.cli.app import ResolvedConfig, resolve_cli_config
from skene.llm import create_llm_client
from skene.output import error


def resolve_artifact_path(p: Path, default_filename: str) -> Path:
    """Normalise a CLI-supplied output path.

    - relative paths become absolute against ``Path.cwd()``
    - a path that points at an existing directory gets ``default_filename`` appended
    - a path without a file suffix is treated as a directory and gets
      ``default_filename`` appended
    - everything else is resolved as-is
    """
    resolved = p if p.is_absolute() else (Path.cwd() / p).resolve()
    if resolved.exists() and resolved.is_dir():
        return (resolved / default_filename).resolve()
    if not resolved.suffix:
        return (resolved / default_filename).resolve()
    return resolved.resolve()


def resolve_base_path(path: Path | None) -> Path:
    """Resolve the project root path and validate it is an existing directory."""
    base_path = (path if path is not None else Path(".")).resolve()
    if not base_path.exists():
        error(f"Path does not exist: {base_path}")
        raise typer.Exit(1)
    if not base_path.is_dir():
        error(f"Path is not a directory: {base_path}")
        raise typer.Exit(1)
    return base_path


def require_llm_credentials(rc: ResolvedConfig, command_name: str) -> str:
    """Validate rc has credentials for the LLM call; return an effective API key.

    Local providers (lmstudio/ollama/generic) don't need a real key — we fall
    back to the provider name so downstream code has a non-empty string to
    pass around.
    """
    if not rc.api_key and not rc.is_local:
        error(
            f"An API key is required for {command_name}. "
            "Set --api-key, SKENE_API_KEY env var, or add api_key to .skene.config."
        )
        raise typer.Exit(1)
    return rc.api_key or rc.provider


def build_llm(rc: ResolvedConfig, api_key: str, *, no_fallback: bool):
    """Construct an LLMClient from a resolved config."""
    return create_llm_client(
        rc.provider,
        SecretStr(api_key),
        rc.model,
        base_url=rc.base_url,
        debug=rc.debug,
        no_fallback=no_fallback,
    )


__all__ = [
    "ResolvedConfig",
    "build_llm",
    "require_llm_credentials",
    "resolve_artifact_path",
    "resolve_base_path",
    "resolve_cli_config",
]
