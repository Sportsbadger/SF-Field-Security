import configparser
import sys
import types
from pathlib import Path

import click
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("questionary", types.SimpleNamespace())

from tool_utils import OrgConfig, add_org_to_config_interactively, read_config


def _write_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "[SalesforceOrgs]",
                "active_org = sandbox",
                "",
                "[Org sandbox]",
                "target_org_url = https://test.salesforce.com",
                "persistent_alias = sandbox",
                "explicit_custom_objects =",
                "",
                "[ToolOptions]",
                "api_version = 60.0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_add_org_to_config_interactively_appends_new_org(monkeypatch, tmp_path):
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    monkeypatch.setattr(
        "tool_utils._prompt_for_org",
        lambda *_args, **_kwargs: OrgConfig(
            name="prod",
            target_org_url="https://login.salesforce.com",
            persistent_alias="prod",
            explicit_custom_objects=["Managed__c"],
        ),
    )

    updated_settings = add_org_to_config_interactively(config_path)

    assert sorted(org.name for org in updated_settings.available_orgs) == [
        "prod",
        "sandbox",
    ]
    assert updated_settings.active_org_name == "sandbox"

    parser = configparser.ConfigParser()
    parser.read(config_path)
    assert parser.get("Org prod", "persistent_alias") == "prod"
    assert parser.get("Org prod", "explicit_custom_objects") == "Managed__c"


def test_add_org_to_config_interactively_rejects_duplicate_name(monkeypatch, tmp_path):
    config_path = tmp_path / "config.ini"
    _write_config(config_path)

    monkeypatch.setattr(
        "tool_utils._prompt_for_org",
        lambda *_args, **_kwargs: OrgConfig(
            name="sandbox",
            target_org_url="https://login.salesforce.com",
            persistent_alias="prod",
            explicit_custom_objects=[],
        ),
    )

    with pytest.raises(click.ClickException):
        add_org_to_config_interactively(config_path)

    settings = read_config(config_path)
    assert [org.name for org in settings.available_orgs] == ["sandbox"]
