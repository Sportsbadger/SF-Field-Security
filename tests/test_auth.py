import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("questionary", types.SimpleNamespace())

from tool_utils import (
    CommandResult,
    MetadataRetrievalPlan,
    check_auth,
    has_expired_token_error,
    retrieve_and_convert_metadata,
)


def _command_result(success: bool, stdout: str | None = None) -> CommandResult:
    return CommandResult(
        success=success,
        returncode=0 if success else 1,
        stdout=stdout,
        duration_seconds=0.01,
    )


def test_check_auth_returns_false_when_display_validation_fails(monkeypatch):
    org_list_output = (
        '{"result":{"nonScratchOrgs":[{"alias":"sandbox","aliases":["sandbox"]}]}}'
    )
    responses = [
        _command_result(True, org_list_output),
        _command_result(False, '{"message":"expired access/refresh token"}'),
    ]

    def fake_run_command(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr("tool_utils.run_command", fake_run_command)

    assert check_auth("sandbox", announce=False) is False


def test_check_auth_returns_true_when_org_list_and_display_are_valid(monkeypatch):
    org_list_output = (
        '{"result":{"nonScratchOrgs":[{"alias":"sandbox","aliases":["sandbox"]}]}}'
    )
    responses = [
        _command_result(True, org_list_output),
        _command_result(True, '{"result":{"id":"00D..."}}'),
    ]

    def fake_run_command(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr("tool_utils.run_command", fake_run_command)

    assert check_auth("sandbox", announce=False) is True


def test_has_expired_token_error_matches_expected_patterns():
    assert has_expired_token_error("expired access/refresh token")
    assert has_expired_token_error("INVALID REFRESH TOKEN")
    assert not has_expired_token_error("unexpected deployment failure")


def test_retrieve_reauthenticates_and_retries_once(monkeypatch, tmp_path):
    project_path = tmp_path / "workspace"
    project_path.mkdir()
    plan = MetadataRetrievalPlan(
        project_path=project_path,
        manifest_path=project_path / "package.xml",
        temp_retrieve_dir=project_path / "temp_mdapi_retrieve",
        mdapi_source_path=project_path / "mdapi_source",
        force_app_path=project_path / "force-app",
    )
    plan.temp_retrieve_dir.mkdir(parents=True)
    (plan.temp_retrieve_dir / "unpackaged.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    responses = [
        _command_result(False, '{"message":"expired access/refresh token"}'),
        _command_result(True, ""),
        _command_result(True, ""),
        _command_result(True, '{"status":0}'),
        _command_result(True, ""),
    ]

    def fake_run_command(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr("tool_utils.run_command", fake_run_command)

    assert retrieve_and_convert_metadata(
        plan=plan,
        api_version="60.0",
        explicit_custom_objects=[],
        target_org_alias="sandbox",
        target_org_url="https://login.salesforce.com",
    )
