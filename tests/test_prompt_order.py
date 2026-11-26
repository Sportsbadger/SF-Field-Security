import sys
import types
from pathlib import Path

sys.modules.setdefault("questionary", types.SimpleNamespace())
sys.path.append(str(Path(__file__).resolve().parent.parent))

import fs_tool_v151 as fs


class _Answer:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def _fake_questionary(answers, asked):
    def prompt(message, *args, **kwargs):
        asked.append(message)
        return _Answer(answers.pop(0))

    return types.SimpleNamespace(
        select=prompt,
        checkbox=prompt,
        confirm=prompt,
        Choice=lambda title, value=None: value if value is not None else title,
    )


def test_modify_object_permissions_asks_objects_before_profiles(monkeypatch, tmp_path):
    for obj in ("Account", "sitetracker__Site__c"):
        (tmp_path / "objects" / obj).mkdir(parents=True)
    (tmp_path / "profiles").mkdir()
    for profile in ("Admin", "Standard"):
        (tmp_path / "profiles" / f"{profile}{fs.PROFILE_SUFFIX}").write_text("<Profile/>")

    asked = []
    answers = [
        "Profiles",  # type
        ["Account"],  # objects
        ["Admin"],  # profiles
        "Read Only",  # template
    ]
    monkeypatch.setattr(fs, "questionary", _fake_questionary(answers, asked))

    objects, profiles, permsets, definitions = fs._get_manual_object_permission_definitions(
        tmp_path
    )

    assert asked[0] == "Modify object permissions for:"
    assert asked[1] == "Select objects to define permissions for:"
    assert asked[2] == "Select Profiles to modify permissions for:"
    assert objects == {"Account"}
    assert profiles == {"Admin"}
    assert permsets == set()
    assert definitions["Account"]["Admin"]["allowRead"] is True


def test_manual_object_permission_definitions_is_defined_once():
    source = (Path(fs.__file__)).read_text(encoding="utf-8")
    assert source.count("def _get_manual_object_permission_definitions(") == 1
