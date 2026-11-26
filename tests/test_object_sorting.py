import sys
import types
from pathlib import Path

sys.modules.setdefault("questionary", types.SimpleNamespace())
sys.path.append(str(Path(__file__).resolve().parent.parent))

from fs_tool_v151 import _ci_key, list_objects


def test_list_objects_sorts_case_insensitively(tmp_path):
    names = [
        "sitetracker__Site__c",
        "UKDev__c",
        "PT_Project_Extension__c",
        "PermitCondition__c",
        "Risk__c",
        "Aura_Power_Contract__c",
    ]
    for name in names:
        (tmp_path / "objects" / name).mkdir(parents=True)
    (tmp_path / "objects" / "stray.txt").write_text("not an object")

    assert list_objects(tmp_path) == [
        "Aura_Power_Contract__c",
        "PermitCondition__c",
        "PT_Project_Extension__c",
        "Risk__c",
        "sitetracker__Site__c",
        "UKDev__c",
    ]


def test_ci_key_is_deterministic_for_names_differing_only_by_case():
    assert sorted(["abc", "ABC"], key=_ci_key) == sorted(["ABC", "abc"], key=_ci_key)
