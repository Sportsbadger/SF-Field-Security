import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Provide a lightweight stand-in for the questionary dependency so the helpers
# under test can be imported without pulling the interactive dependency.
sys.modules.setdefault(
    "questionary",
    types.SimpleNamespace(select=None, checkbox=None, confirm=None, Choice=lambda *args, **kwargs: None),
)

from fs_tool_v151 import (
    PROFILE_SUFFIX,
    PERMISSIONSET_SUFFIX,
    _discover_backup_contents,
    _restore_backup_contents,
)


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_discover_backup_contents_lists_expected_files(tmp_path):
    backup_path = tmp_path / "fs_backups" / "20240101_sample"
    _write(backup_path / "profiles" / f"Example{PROFILE_SUFFIX}", "profile")
    _write(backup_path / "permissionsets" / f"Example{PERMISSIONSET_SUFFIX}", "permset")
    _write(backup_path / "package.xml", "<Package/>")

    contents = _discover_backup_contents(backup_path)

    assert [p.name for p in contents["profile_files"]] == [f"Example{PROFILE_SUFFIX}"]
    assert [p.name for p in contents["permset_files"]] == [f"Example{PERMISSIONSET_SUFFIX}"]
    assert contents["package_file"].name == "package.xml"


def test_restore_backup_copies_files_and_creates_safety_backup(tmp_path):
    meta = tmp_path / "meta"
    base_dir = tmp_path / "tool"

    # Existing metadata (should be backed up before overwrite)
    _write(meta / "profiles" / f"Existing{PROFILE_SUFFIX}", "CURRENT_PROFILE")
    _write(meta / "permissionsets" / f"Existing{PERMISSIONSET_SUFFIX}", "CURRENT_PERMSET")
    _write(meta / "package.xml", "<Package>current</Package>")

    # Backup contents to restore
    backup_path = base_dir / "fs_backups" / "20240101_restore"
    _write(backup_path / "profiles" / f"Existing{PROFILE_SUFFIX}", "BACKUP_PROFILE")
    _write(backup_path / "permissionsets" / f"Existing{PERMISSIONSET_SUFFIX}", "BACKUP_PERMSET")
    _write(backup_path / "package.xml", "<Package>backup</Package>")

    contents = _discover_backup_contents(backup_path)
    result = _restore_backup_contents(meta, base_dir, backup_path, contents)

    # Restored content matches backup
    assert (meta / "profiles" / f"Existing{PROFILE_SUFFIX}").read_text() == "BACKUP_PROFILE"
    assert (meta / "permissionsets" / f"Existing{PERMISSIONSET_SUFFIX}").read_text() == "BACKUP_PERMSET"
    assert (meta / "package.xml").read_text() == "<Package>backup</Package>"

    # Summary information reflects restored items
    assert result["restored_profiles"] == ["Existing"]
    assert result["restored_permsets"] == ["Existing"]
    assert result["package_restored"] is True
    assert result["errors"] == []

    # Safety backup captured the previous state
    safety_dir_candidates = [
        p for p in (base_dir / "fs_backups").iterdir()
        if p.is_dir() and p.name.endswith("pre_restore_20240101_restore")
    ]
    assert safety_dir_candidates, "Safety backup not created"
    safety_backup = safety_dir_candidates[0]
    assert (safety_backup / "profiles" / f"Existing{PROFILE_SUFFIX}").read_text() == "CURRENT_PROFILE"
    assert (safety_backup / "permissionsets" / f"Existing{PERMISSIONSET_SUFFIX}").read_text() == "CURRENT_PERMSET"
