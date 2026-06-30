"""Push existing engine + trigger artifacts upstream."""

from pathlib import Path

import typer

from skene.cli.app import app, resolve_cli_config
from skene.config import resolve_upstream_token
from skene.growth_loops.push import publish_bundle
from skene.output import error, success, warning


@app.command()
def push(
    path: Path = typer.Argument(
        ".",
        help="Project root containing the Skene bundle directory",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    upstream: str | None = typer.Option(
        None,
        "--upstream",
        "-u",
        help="Upstream workspace URL (e.g. https://skene.ai/workspace/my-app)",
    ),
    quiet: bool = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Suppress output, show errors only",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Show diagnostic messages and log LLM I/O to ~/.local/state/skene/debug/",
    ),
):
    """
    Push the Skene bundle (YAML, manifests, registry, trigger SQL) to upstream.

    Uploads files from the configured output directory (see SKENE_OUTPUT_DIR /
    ``output_dir`` in config), plus the latest trigger migration under
    ``supabase/migrations/``.
    """
    project_root = path.resolve()
    rc = resolve_cli_config(quiet=quiet, debug=debug, project_root=project_root)

    resolved_upstream = upstream or rc.config.upstream or ""
    user_configured_upstream = bool(resolved_upstream)

    resolved_token = resolve_upstream_token(rc.config)
    if not resolved_token:
        error("No token. Run skene login to authenticate.")
        raise typer.Exit(1)

    try:
        result = publish_bundle(
            project_root,
            rc.config,
            upstream=resolved_upstream,
            token=resolved_token,
            warn=warning,
        )
    except Exception as exc:
        error(f"Deploy failed: {exc}")
        raise typer.Exit(1) from exc

    if result.get("ok"):
        if result.get("status") == "noop":
            success("Nothing new to deploy.")
        else:
            push_id = result.get("push_id", "?")
            updated_paths = result.get("updated_paths") or []
            artifact_count = result.get("artifact_count", len(updated_paths))
            success(f"Pushed {artifact_count} artifact(s); {len(updated_paths)} updated upstream (push_id={push_id})")
            for p in updated_paths:
                success(f"  • {p}")
        return

    msg = result.get("message", "Push failed.")
    err_kind = result.get("error")
    if err_kind == "auth":
        error(msg)
    else:
        warning(msg)
        if user_configured_upstream and err_kind in {"not_found", "network", "server"}:
            warning(
                f"Your configured upstream ({resolved_upstream}) seems wrong. "
                "Verify the workspace URL or remove it to use the default Skene Cloud."
            )
    raise typer.Exit(1)
