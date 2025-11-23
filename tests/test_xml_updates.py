import sys
import types
from pathlib import Path
import xml.etree.ElementTree as ET

# Provide lightweight stubs for optional runtime dependencies.
sys.modules.setdefault('questionary', types.SimpleNamespace())

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fs_tool_v151 import (
    OBJECT_PERM_TAGS,
    NS,
    SF_NAMESPACE_URI,
    _find_insertion_point,
    update_object_permission,
    update_permission,
)


def _profile_root():
    return ET.Element(f'{{{SF_NAMESPACE_URI}}}Profile')


def test_find_insertion_point_respects_preferred_order():
    root = _profile_root()
    ET.SubElement(root, f'{{{SF_NAMESPACE_URI}}}categoryGroupVisibilities')
    ET.SubElement(root, f'{{{SF_NAMESPACE_URI}}}layoutAssignments')

    parent, index = _find_insertion_point(root, 'fieldPermissions')

    assert parent is root
    assert index == 1  # after categoryGroupVisibilities, before layoutAssignments


def test_update_permission_creates_nodes_and_orders_children():
    root = _profile_root()

    assert update_permission(root, 'Account.Test__c', readable=False, editable=True)

    fp = root.find('sf:fieldPermissions', NS)
    assert fp is not None
    children = list(fp)
    # editable comes first, then field, then readable
    assert [child.tag.split('}')[-1] for child in children] == [
        'editable',
        'field',
        'readable',
    ]
    assert fp.find('sf:editable', NS).text == 'true'
    assert fp.find('sf:readable', NS).text == 'true'  # editable implies readable

    # Formula fields must never be editable
    assert update_permission(root, 'Account.Formula__c', readable=True, editable=True, field_type='Formula')
    formula_node = root.findall('sf:fieldPermissions', NS)[1]
    assert formula_node.find('sf:editable', NS).text == 'false'


def test_update_object_permission_sets_dependencies_and_order():
    root = _profile_root()
    perms = {
        'allowCreate': True,
        'allowRead': False,  # should be forced to True by other settings
        'viewAllRecords': True,
    }

    assert update_object_permission(root, 'CustomObject__c', perms)

    op = root.find('sf:objectPermissions', NS)
    assert op is not None

    tags_in_order = [child.tag.split('}')[-1] for child in list(op)]
    assert tags_in_order[:-1] == sorted(OBJECT_PERM_TAGS)
    assert tags_in_order[-1] == 'object'

    assert op.find('sf:allowRead', NS).text == 'true'
    assert op.find('sf:viewAllRecords', NS).text == 'true'
