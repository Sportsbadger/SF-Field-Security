from pathlib import Path

from run_tool import _find_workspace_metadata_base


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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
