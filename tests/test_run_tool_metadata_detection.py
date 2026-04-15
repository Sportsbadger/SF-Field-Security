from pathlib import Path

import run_tool
from tool_utils import ConfigSettings, OrgConfig

from run_tool import (
    _build_main_menu_choices,
    _find_workspace_metadata_base,
    _has_pending_deploy,
    _prompt_deploy_after_tool_run,
)


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _config() -> ConfigSettings:
    org = OrgConfig(
        name="sandbox",
        target_org_url="https://test.salesforce.com",
        persistent_alias="sandbox",
        explicit_custom_objects=[],
    )
    return ConfigSettings(
        target_org_url=org.target_org_url,
        persistent_alias=org.persistent_alias,
        api_version="60.0",
        active_org_name=org.name,
        available_orgs=[org],
        explicit_custom_objects=[],
    )


def test_find_workspace_metadata_base_prefers_default_force_app(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _mkdir(workspace / "force-app" / "main" / "default" / "objects")
    _mkdir(workspace / "force-app" / "main" / "default" / "profiles")

    detected = _find_workspace_metadata_base(workspace)

    assert detected == workspace / "force-app" / "main" / "default"


def test_find_workspace_metadata_base_finds_nested_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "metadata" / "custom" / "pkg"
    _mkdir(nested / "objects")
    _mkdir(nested / "permissionsets")

    detected = _find_workspace_metadata_base(workspace)

    assert detected == nested


def test_find_workspace_metadata_base_returns_none_when_invalid(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _mkdir(workspace / "force-app" / "main" / "default" / "objects")

    detected = _find_workspace_metadata_base(workspace)

    assert detected is None


def test_has_pending_deploy_detects_manifest(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    workspace = projects_dir / "workspace"
    _mkdir(workspace / "force-app" / "main" / "default")
    (workspace / ".workspace_info.json").write_text(
        '{"persistent_alias": "sandbox", "last_updated": "2026-01-01T00:00:00"}',
        encoding="utf-8",
    )
    (workspace / "force-app" / "main" / "default" / "package.xml").write_text(
        "<Package />", encoding="utf-8"
    )

    assert _has_pending_deploy(projects_dir, "sandbox") is True


def test_build_main_menu_choices_marks_pending_deploy() -> None:
    choices = _build_main_menu_choices(_config(), has_pending_deploy=True)

    assert "Deploy Changes (Pending)" in choices
    assert "Deploy Changes" not in choices


def test_prompt_deploy_after_tool_run_launches_deploy(monkeypatch, tmp_path: Path) -> None:
    script_dir = tmp_path
    workspace = tmp_path / "workspace"
    _mkdir(workspace / "force-app" / "main" / "default")
    (workspace / "force-app" / "main" / "default" / "package.xml").write_text(
        "<Package />", encoding="utf-8"
    )

    state = {"saved": False, "deployed": False}

    monkeypatch.setattr(
        run_tool,
        "save_workspace_info",
        lambda *args, **kwargs: state.__setitem__("saved", True),
    )
    monkeypatch.setattr(run_tool, "prompt_with_navigation", lambda _prompt: True)
    monkeypatch.setattr(
        run_tool,
        "deploy_changes",
        lambda _script_dir: state.__setitem__("deployed", True),
    )
    monkeypatch.setattr(
        run_tool.questionary,
        "confirm",
        lambda *_args, **_kwargs: object(),
        raising=False,
    )

    _prompt_deploy_after_tool_run(script_dir, workspace, _config())

    assert state["saved"] is True
    assert state["deployed"] is True
