import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("questionary", types.SimpleNamespace())

from tool_utils import CommandResult, check_auth


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
