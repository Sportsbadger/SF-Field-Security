#!/usr/bin/env python3
"""
Salesforce Field Security & Object Permission Manager

v1.51 Fixes for Modify Object Permissions. 20250611


Profile And Permission Set Version.

A CLI tool to list objects, fields (with types), profiles, and permission sets
in a Salesforce DX project, modify field-level security (for Profiles OR
Permission Sets per run), modify object-level permissions (manual or CSV),
audit field access, audit object permissions, generate necessary metadata for
deployment, and rollback to previous backups.

Enhancements:
- Displays field type alongside field name during selection.
- Bulk Apply FLS: Added option to load fields from a CSV file (ObjectName,FieldName).
- Bulk Apply FLS: Can now target EITHER Profiles OR Permission Sets per run (via manual) or both (via CSV).
- Modify Object Perms: Can now load definitions from a CSV report.
- Audit Report: Includes selected Permission Sets in the analysis and output CSV.
- Backup: Backs up modified Profiles and/or Permission Sets.
- package.xml: Generated based on modified Profiles and/or Permission Sets.
- New: "Audit Field Access by Permission Set (Field-Centric)" for matrix view.
- New: "Inspect Permission Set" to see all object/field/user perms in selected PS.
- New: "Who has access to this field? (Reverse Lookup)" report.
- New: "Generate Object Permissions Report" for matrix view of object CRUD.


Dependencies:
pip install click questionary lxml

Usage:
python fs_tool.py [--project PATH] [--metadata PATH] [--dry-run]

FS Tool Files Directory:
(Reports and Backups are stored here)

"""
import shutil
import datetime
import csv
from pathlib import Path
import xml.etree.ElementTree as ET
import click
import questionary
import sys
from collections import defaultdict
import os

# --- Constants ---
SF_NAMESPACE_URI = 'http://soap.sforce.com/2006/04/metadata'
NS = {'sf': SF_NAMESPACE_URI}
ET.register_namespace('', SF_NAMESPACE_URI)

PROFILE_SUFFIX = '.profile-meta.xml'
PERMISSIONSET_SUFFIX = '.permissionset-meta.xml'
FIELD_META_SUFFIX = '.field-meta.xml'

ALL_CHOICE_VALUE = '[ALL]'
ALL_PROJECT_CHOICE_VALUE = '[ALL_PROJECT]'

ACCESS_RW = "RW"
ACCESS_R_ONLY = "R-"
ACCESS_NONE = "--"

OBJECT_PERM_TAGS = ['allowCreate', 'allowRead', 'allowEdit', 'allowDelete', 'viewAllRecords', 'modifyAllRecords']
OBJECT_PERM_SHORT = ['C', 'R', 'U', 'D', 'VA', 'MA']


# --- Utility Functions (backup_file, find_metadata_base, _list_metadata_components, list_objects, list_fields, list_profiles, list_permission_sets, load_xml) ---
def backup_file(src: Path, backup_dir: Path):
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, backup_dir / src.name)

def find_metadata_base(root: Path, override: str = None) -> Path:
    if override:
        base = Path(override)
        if (base / 'objects').is_dir() and ((base / 'profiles').is_dir() or (base / 'permissionsets').is_dir()):
            click.echo("")
            return base.resolve()
        click.echo(click.style(f"Error: Invalid override path: {base}. 'objects' and ('profiles' or 'permissionsets') missing.", fg='red'))
        sys.exit(1)
    default_paths = [root / 'force-app' / 'main' / 'default', root / 'mdapioutput', root / 'src']
    for default in default_paths:
        if (default / 'objects').is_dir() and ((default / 'profiles').is_dir() or (default / 'permissionsets').is_dir()):
            click.echo("")
            return default.resolve()
    for obj_dir in root.rglob('objects'):
        base = obj_dir.parent
        if (base / 'profiles').is_dir() or (base / 'permissionsets').is_dir():
            click.echo("")
            return base.resolve()
    click.echo(click.style('Error: Metadata folder not found.', fg='red'))
    click.echo("Ensure project has 'objects' and ('profiles' or 'permissionsets') dir, or use --metadata.")
    sys.exit(1)

def _list_metadata_components(meta_path: Path, component_folder: str, suffix: str) -> list[str]:
    comp_path = meta_path / component_folder
    if not comp_path.is_dir(): return []
    return sorted(p.name[:-len(suffix)] for p in comp_path.glob(f'*{suffix}'))

def list_objects(meta: Path) -> list[str]:
    obj_path = meta / 'objects'
    if not obj_path.is_dir(): return []
    return sorted(d.name for d in obj_path.iterdir() if d.is_dir())

def list_fields(meta: Path, obj: str) -> list[tuple[str, str]]:
    """
    Lists eligible fields for an object, including formula fields.
    Formula fields will be marked as such and are read-only by nature.
    """
    fields_dir = meta / 'objects' / obj / 'fields'
    if not fields_dir.is_dir(): return []
    
    result = []
    # These types are still excluded as their visibility is often inherited or not managed by simple FLS.
    excluded_types = {'Lookup', 'MasterDetail', 'MetadataRelationship', 'Summary', 'Hierarchy', 'ExternalLookup', 'AutoNumber'}

    for fpath in fields_dir.glob(f'*{FIELD_META_SUFFIX}'):
        field_name = fpath.name[:-len(FIELD_META_SUFFIX)]
        field_type = 'Unknown'
        is_eligible = True
        is_formula = False

        try:
            tree = ET.parse(fpath)
            root_node = tree.getroot()

            # Check if it's a formula field first, as this is a primary characteristic.
            form_tag = root_node.find('sf:formula', NS)
            if form_tag is not None:
                is_formula = True
                field_type = 'Formula'
                # Formula fields are eligible for visibility changes.
                is_eligible = True
            
            # For non-formula fields, apply the original exclusion logic.
            if not is_formula:
                tp_node = root_node.find('sf:type', NS)
                if tp_node is not None:
                    field_type = tp_node.text
                    if field_type in excluded_types:
                        is_eligible = False
                
                # Required fields are always visible and editable, so no need to manage FLS.
                req_tag = root_node.find('sf:required', NS)
                if req_tag is not None and req_tag.text == 'true':
                    is_eligible = False

        except ET.ParseError as e:
            click.echo(f"Warning: Parsing field {fpath}: {e}. Skipping.")
            is_eligible = False
        except Exception as e:

            click.echo(f"Warning: Error processing field {fpath}: {e}. Skipping.")
            is_eligible = False
        
        if is_eligible:
            result.append((field_name, field_type))
            
    return sorted(result, key=lambda x: x[0])

def list_profiles(meta: Path) -> list[str]: return _list_metadata_components(meta, 'profiles', PROFILE_SUFFIX)
def list_permission_sets(meta: Path) -> list[str]: return _list_metadata_components(meta, 'permissionsets', PERMISSIONSET_SUFFIX)

def load_xml(path: Path) -> tuple[ET.ElementTree | None, ET.Element | None]:
    try:
        tree = ET.parse(path)
        return tree, tree.getroot()
    except ET.ParseError as e: click.echo(f"Error parsing XML {path}: {e}"); return None, None
    except FileNotFoundError: return None, None
    except Exception as e: click.echo(f"Error loading/processing XML {path}: {e}"); return None, None


# --- Permission Getter Functions (get_field_permissions_from_xml_root, ..., get_object_permissions_from_xml_root) ---
def get_field_permissions_from_xml_root(xml_root: ET.Element | None, field_api_name: str) -> tuple[bool, bool]:
    if xml_root is None: return False, False
    fp_node = xml_root.find(f".//sf:fieldPermissions[sf:field='{field_api_name}']", NS)
    if fp_node is None: return False, False
    r_tag = fp_node.find('sf:readable', NS); e_tag = fp_node.find('sf:editable', NS)
    readable = r_tag is not None and r_tag.text == 'true'
    editable = e_tag is not None and e_tag.text == 'true'
    return readable, editable

def check_for_system_overrides(xml_root: ET.Element | None, component_type: str) -> tuple[bool, str | None]:
 #   Checks for 'View All Data' or 'Modify All Data' system permissions.
 #   These permissions can override granular object/field settings.
 #   Returns a tuple: (has_override_permission, permission_name).
    
    if xml_root is None:
        return False, None

    # In Profiles, these are <userPermissions> nodes.
    if component_type == "Profile":
        # Check for ModifyAllData first as it's the most powerful
        if xml_root.find(f".//sf:userPermissions[sf:name='ModifyAllData'][sf:enabled='true']", NS) is not None:
            return True, "Modify All Data"
        if xml_root.find(f".//sf:userPermissions[sf:name='ViewAllData'][sf:enabled='true']", NS) is not None:
            return True, "View All Data"
            
    # In Permission Sets, these are top-level boolean tags.
    elif component_type == "PermissionSet":
        if xml_root.findtext('sf:modifyAllData', default='false', namespaces=NS) == 'true':
            return True, "Modify All Data"
        if xml_root.findtext('sf:viewAllData', default='false', namespaces=NS) == 'true':
            return True, "View All Data"
            
    return False, None



def get_field_permissions_from_profile_root(profile_root: ET.Element | None, field_api_name: str) -> tuple[bool, bool]:
    return get_field_permissions_from_xml_root(profile_root, field_api_name)

def get_field_permissions_from_permissionset_root(permissionset_root: ET.Element | None, field_api_name: str) -> tuple[bool, bool]:
    return get_field_permissions_from_xml_root(permissionset_root, field_api_name)

def get_effective_field_permissions_from_ps_root(
        ps_root: ET.Element | None, object_name: str, full_field_api_name: str
    ) -> tuple[bool, bool]:
    if ps_root is None: return False, False
    r_explicit, e_explicit = get_field_permissions_from_permissionset_root(ps_root, full_field_api_name)
    r_obj_override, e_obj_override = False, False
    op_node = ps_root.find(f".//sf:objectPermissions[sf:object='{object_name}']", NS)
    if op_node is not None:
        if op_node.findtext('sf:modifyAllRecords', namespaces=NS) == 'true':
            r_obj_override, e_obj_override = True, True
        elif op_node.findtext('sf:viewAllRecords', namespaces=NS) == 'true':
            r_obj_override = True
    final_r = r_explicit or r_obj_override
    final_e = e_explicit or e_obj_override
    if final_e: final_r = True
    return final_r, final_e

def get_object_permissions_from_xml_root(xml_root: ET.Element | None, object_api_name: str) -> dict[str, bool]:
    perms = {tag: False for tag in OBJECT_PERM_TAGS}
    if xml_root is None: return perms
    op_node = xml_root.find(f".//sf:objectPermissions[sf:object='{object_api_name}']", NS)
    if op_node is None: return perms
    for tag in OBJECT_PERM_TAGS:
        node = op_node.find(f'sf:{tag}', NS)
        if node is not None and node.text == 'true': perms[tag] = True
    return perms

# --- XML Update Functions (_find_insertion_point, update_permission, update_object_permission) ---

def _find_insertion_point(root: ET.Element, new_element_tag: str) -> tuple[ET.Element, int | None]:
    preferred_order = [
        'applicationVisibilities', 'categoryGroupVisibilities', 'classAccesses', 'customMetadataTypeAccesses',
        'customPermissions', 'customSettingAccesses', 'externalCredentialPrincipalAccesses', 'externalDataSourceAccesses',
        'fieldPermissions', 'flowAccesses', 'layoutAssignments', 'loginFlows', 'loginHours', 'loginIpRanges',
        'objectPermissions', 'pageAccesses', 'profileActionOverrides', 'recordTypeVisibilities',
        'tabSettings', 'tabVisibilities', 'userLicense', 'userPermissions', 'viewAllData', 'modifyAllData'
    ]
    current_tag_index = preferred_order.index(new_element_tag) if new_element_tag in preferred_order else -1
    last_known_element_in_order = None
    insertion_index_in_parent = None
    existing_elements_in_root = list(root)

    for child_idx, child_elem in enumerate(existing_elements_in_root):
        child_tag_name_no_ns = child_elem.tag.split('}')[-1] if '}' in child_elem.tag else child_elem.tag
        try:
            child_tag_order_index = preferred_order.index(child_tag_name_no_ns)
            if child_tag_name_no_ns == new_element_tag or (current_tag_index != -1 and child_tag_order_index < current_tag_index):
                last_known_element_in_order = child_elem
                insertion_index_in_parent = child_idx + 1
        except ValueError: pass

    if last_known_element_in_order:
        return root, insertion_index_in_parent
    else:
        if current_tag_index != -1:
            for child_idx, child_elem in enumerate(existing_elements_in_root):
                child_tag_name_no_ns = child_elem.tag.split('}')[-1] if '}' in child_elem.tag else child_elem.tag
                try:
                    child_tag_order_index = preferred_order.index(child_tag_name_no_ns)
                    if child_tag_order_index > current_tag_index:
                        return root, child_idx
                except ValueError: continue
        return root, None

def update_permission(root: ET.Element, field_api: str, readable: bool, editable: bool, field_type: str = 'Unknown') -> bool:

#    Updates the FLS for a given field. If the field is a Formula, 'editable' is forced to false.

    # Formulas can only be readable, never editable.
    if field_type == 'Formula':
        editable = False

    if editable and not readable:
        readable = True # Maintain logic: if it's editable, it must be readable.
        
    ns_uri = NS['sf']
    fps_existing = root.findall(f".//sf:fieldPermissions[sf:field='{field_api}']", NS)
    fp = None
    if fps_existing:
        fp = fps_existing[0]
        for dup_idx in range(len(fps_existing) - 1, 0, -1):
            dup_node = fps_existing[dup_idx]
            parent_map = {c: p for p in root.iter() for c in p}
            parent_of_dup = parent_map.get(dup_node)
            if parent_of_dup is not None: parent_of_dup.remove(dup_node)
    else:
        fp = ET.Element(f'{{{ns_uri}}}fieldPermissions')
        ET.SubElement(fp, f'{{{ns_uri}}}editable').text = 'false'
        ET.SubElement(fp, f'{{{ns_uri}}}field').text = field_api
        ET.SubElement(fp, f'{{{ns_uri}}}readable').text = 'false'
        parent_to_insert_in, insert_idx = _find_insertion_point(root, 'fieldPermissions')
        if insert_idx is not None: parent_to_insert_in.insert(insert_idx, fp)
        else: parent_to_insert_in.append(fp)

    r_node = fp.find('sf:readable', NS)
    if r_node is None: r_node = ET.SubElement(fp, f'{{{ns_uri}}}readable')
    r_node.text = str(readable).lower()
    
    e_node = fp.find('sf:editable', NS)
    if e_node is None: e_node = ET.SubElement(fp, f'{{{ns_uri}}}editable')
    e_node.text = str(editable).lower()

    # Reorder elements for clean metadata format
    field_el = fp.find('sf:field', NS)
    editable_el = fp.find('sf:editable', NS)
    readable_el = fp.find('sf:readable', NS)
    for child in list(fp): fp.remove(child)
    if editable_el is not None: fp.append(editable_el)
    if field_el is not None: fp.append(field_el)
    if readable_el is not None: fp.append(readable_el)
    
    return True

def update_object_permission(root: ET.Element, object_api: str, permissions: dict[str, bool]) -> bool:
    ns_uri = NS['sf']
    op_existing_list = root.findall(f".//sf:objectPermissions[sf:object='{object_api}']", NS)
    op = None
    if permissions.get('modifyAllRecords', False): permissions['viewAllRecords'] = True; permissions['allowRead'] = True
    if permissions.get('viewAllRecords', False): permissions['allowRead'] = True
    if permissions.get('allowEdit', False): permissions['allowRead'] = True
    if permissions.get('allowDelete', False): permissions['allowRead'] = True

    if op_existing_list:
        op = op_existing_list[0]
        for dup_idx in range(len(op_existing_list) -1, 0, -1):
            dup_node = op_existing_list[dup_idx]
            parent_map = {c:p for p in root.iter() for c in p}
            parent_of_dup = parent_map.get(dup_node)
            if parent_of_dup is not None: parent_of_dup.remove(dup_node)
    else:
        op = ET.Element(f'{{{ns_uri}}}objectPermissions')
        for perm_tag in sorted(OBJECT_PERM_TAGS): ET.SubElement(op, f'{{{ns_uri}}}{perm_tag}').text = 'false'
        ET.SubElement(op, f'{{{ns_uri}}}object').text = object_api
        parent_to_insert_in, insert_idx = _find_insertion_point(root, 'objectPermissions')
        if insert_idx is not None: parent_to_insert_in.insert(insert_idx, op)
        else: parent_to_insert_in.append(op)

    for perm_tag in OBJECT_PERM_TAGS:
        node = op.find(f'sf:{perm_tag}', NS)
        if node is None: node = ET.SubElement(op, f'{{{ns_uri}}}{perm_tag}')
        node.text = str(permissions.get(perm_tag, False)).lower()

    object_el = op.find('sf:object', NS)
    perm_elements = [op.find(f'sf:{tag}', NS) for tag in OBJECT_PERM_TAGS]
    for child in list(op): op.remove(child)
    for tag_name in sorted(OBJECT_PERM_TAGS):
        el = next((e for e in perm_elements if e is not None and e.tag == f'{{{ns_uri}}}{tag_name}'), None)
        if el is not None: op.append(el)
    if object_el is not None: op.append(object_el)
    return True

# --- Backup, Package.xml, Formatting (generate_package_xml_for_deployment, create_backup, format_access_display, format_object_perms_display, parse_object_perms_string_to_dict) ---
def generate_package_xml_for_deployment(profiles: list[str], permission_sets: list[str], version: str = '60.0') -> ET.ElementTree | None:
    if not profiles and not permission_sets: return None
    pkg = ET.Element('Package', xmlns=NS['sf'])
    if profiles:
        types_profile = ET.SubElement(pkg, 'types')
        for p in sorted(list(set(profiles))): ET.SubElement(types_profile, 'members').text = p
        ET.SubElement(types_profile, 'name').text = 'Profile'
    if permission_sets:
        types_permset = ET.SubElement(pkg, 'types')
        for ps in sorted(list(set(permission_sets))): ET.SubElement(types_permset, 'members').text = ps
        ET.SubElement(types_permset, 'name').text = 'PermissionSet'
    ET.SubElement(pkg, 'version').text = version
    if hasattr(ET, 'indent'): ET.indent(pkg, space="    ")
    return ET.ElementTree(pkg)

def create_backup(meta: Path, base_dir: Path,
                  profiles_to_backup: list[str],
                  permission_sets_to_backup: list[str],
                  reason: str = "generic_change") -> tuple[Path, list[str], list[str]]:
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_root = base_dir / 'fs_backups' / f"{ts}_{reason}"
    backup_root.mkdir(parents=True, exist_ok=True)
    backed_up_profiles, backed_up_permsets = [], []
    if profiles_to_backup:
        profile_backup_dir = backup_root / 'profiles'; profile_backup_dir.mkdir(parents=True, exist_ok=True)
        for p_name in profiles_to_backup:
            src = meta / 'profiles' / f'{p_name}{PROFILE_SUFFIX}'
            if src.exists(): backup_file(src, profile_backup_dir); backed_up_profiles.append(p_name)
            else: click.echo(f"Warning: Profile not found for backup: {src}")
    if permission_sets_to_backup:
        permset_backup_dir = backup_root / 'permissionsets'; permset_backup_dir.mkdir(parents=True, exist_ok=True)
        for ps_name in permission_sets_to_backup:
            src = meta / 'permissionsets' / f'{ps_name}{PERMISSIONSET_SUFFIX}'
            if src.exists(): backup_file(src, permset_backup_dir); backed_up_permsets.append(ps_name)
            else: click.echo(f"Warning: Permission Set not found for backup: {src}")
    pkg_file = meta / 'package.xml'
    backed_up_pkg = False
    if pkg_file.exists(): backup_file(pkg_file, backup_root); backed_up_pkg = True
    summary = [f"{len(b)} {t}(s)" for b, t in [(backed_up_profiles, "profile"), (backed_up_permsets, "permission set")] if b]
    if backed_up_pkg: summary.append("package.xml")
    click.echo(f"Backup created at {backup_root}" + (f" containing: {', '.join(summary)}." if summary else ", but no items found to back up."))
    return backup_root, backed_up_profiles, backed_up_permsets

def format_access_display(readable: bool, editable: bool) -> str:
    if readable and editable: return ACCESS_RW
    elif readable: return ACCESS_R_ONLY
    else: return ACCESS_NONE

def format_object_perms_display(perms: dict[str, bool]) -> str:
    """Formats object permissions dictionary into a 'c r u d VA MA' string."""
    display_parts = []
    for i in range(len(OBJECT_PERM_TAGS)):
        tag_name = OBJECT_PERM_TAGS[i]
        short_char = OBJECT_PERM_SHORT[i]
        
        permission_is_granted = perms.get(tag_name, False) # Default to False if key is missing
        
        char_to_display = "-" # Default to '-'

        if permission_is_granted:
            if tag_name not in ['viewAllRecords', 'modifyAllRecords']:
                char_to_display = short_char.lower()
            else:
                char_to_display = short_char.upper()
        
        display_parts.append(char_to_display)
            
    return " ".join(display_parts)

def parse_object_perms_string_to_dict(perm_string: str) -> dict[str, bool] | None:
    cleaned_string_parts = perm_string.strip().split()
    parsed_perms = {tag: False for tag in OBJECT_PERM_TAGS}
    if len(cleaned_string_parts) != len(OBJECT_PERM_TAGS):
        click.echo(click.style(f"Error: Invalid object permission string format: '{perm_string}'. Expected {len(OBJECT_PERM_TAGS)} parts. Found {len(cleaned_string_parts)}.", fg='red'))
        return None
    for i, tag_key in enumerate(OBJECT_PERM_TAGS):
        expected_char = OBJECT_PERM_SHORT[i].lower() if tag_key not in ['viewAllRecords', 'modifyAllRecords'] else OBJECT_PERM_SHORT[i].upper()
        if cleaned_string_parts[i] == expected_char:
            parsed_perms[tag_key] = True
        elif cleaned_string_parts[i] != '-':
            click.echo(click.style(f"Warning: Unrecognized token '{cleaned_string_parts[i]}' in perm string '{perm_string}' for {tag_key}. Expected '{expected_char}' or '-'. Treating as false.", fg='yellow'))
    return parsed_perms

# --- Bulk Apply FLS Functions (_get_manual_field_definitions, _get_csv_field_definitions, _handle_field_security_source_selection, _prepare_and_display_planned_fls_changes, _apply_bulk_fls_modifications_to_files, bulk_apply_fls) ---
def _get_manual_field_definitions(meta: Path) -> tuple[list[str], defaultdict, str, list[str]]:
    all_selected_full_field_names = []
    field_new_perms = defaultdict(dict)
    manual_target_type = None
    manual_selected_project_items_names = []
    
    manual_target_type = questionary.select( "Apply manually defined FLS changes to:", choices=["Profiles", "Permission Sets"] ).ask()
    if not manual_target_type: return [], defaultdict(dict), "", []
    
    click.echo(f"Manually targeting: {manual_target_type}")
    click.echo("\n--- Manual Field Selection (for FLS) ---")
    all_objs_list = list_objects(meta)
    if not all_objs_list: click.echo("No objects found in project."); return [], defaultdict(dict), manual_target_type, []
    
    sel_objs = questionary.checkbox('Select objects to define field security for:', choices=all_objs_list).ask()
    if not sel_objs: click.echo("No objects selected."); return [], defaultdict(dict), manual_target_type, []
    
    obj_field_map = {}
    field_type_map = {} # Cache for field types: {'Obj.Field': 'Type'}

    process_all_fields_globally_for_manual = False
    if sel_objs:
        if questionary.confirm(f"For these {len(sel_objs)} object(s): Include ALL their eligible fields in the definition?", default=False).ask():
            process_all_fields_globally_for_manual = True

    if process_all_fields_globally_for_manual:
        for obj_name in sel_objs:
            fields_tuples = list_fields(meta, obj_name)
            if fields_tuples: 
                obj_field_map[obj_name] = [fname for fname, _ in fields_tuples]
                for fname, ftype in fields_tuples: field_type_map[f"{obj_name}.{fname}"] = ftype
    else:
        for obj_name in sel_objs:
            fields_tuples = list_fields(meta, obj_name)
            if not fields_tuples: click.echo(f"Info: No eligible fields found for '{obj_name}'."); continue
            
            for fname, ftype in fields_tuples: field_type_map[f"{obj_name}.{fname}"] = ftype # Populate cache
            
            if questionary.confirm(f"For object '{obj_name}': Include ALL {len(fields_tuples)} eligible fields?", default=False).ask():
                obj_field_map[obj_name] = [fname for fname, _ in fields_tuples]
            else:
                choices = [questionary.Choice(f"{fn} ({ft})", value=fn) for fn, ft in fields_tuples]
                if not choices: continue
                selected_field_names_for_obj = questionary.checkbox(f"Select specific fields for '{obj_name}':", choices=choices).ask()
                if selected_field_names_for_obj: obj_field_map[obj_name] = selected_field_names_for_obj

    if not obj_field_map: click.echo("No fields selected for update."); return [], defaultdict(dict), manual_target_type, []
    
    for obj, fields_list_for_obj in obj_field_map.items():
        if fields_list_for_obj: all_selected_full_field_names.extend(f'{obj}.{f_name}' for f_name in fields_list_for_obj)
    
    if not all_selected_full_field_names: click.echo("No fields effectively selected."); return [], defaultdict(dict), manual_target_type, []

    list_func_manual = list_profiles if manual_target_type == "Profiles" else list_permission_sets
    all_project_items_manual = list_func_manual(meta)
    if not all_project_items_manual: click.echo(f"No {manual_target_type.lower()} found."); return all_selected_full_field_names, defaultdict(dict), manual_target_type, []
    
    item_choices_manual = [questionary.Choice(f'[ALL PROJECT {manual_target_type.upper()}]', value=ALL_PROJECT_CHOICE_VALUE)] + [questionary.Choice(i) for i in all_project_items_manual]
    sel_items_q_manual = questionary.checkbox(f'Select {manual_target_type} to apply FLS changes to:', choices=item_choices_manual).ask()
    if not sel_items_q_manual: click.echo(f'No {manual_target_type.lower()} selected.'); return all_selected_full_field_names, defaultdict(dict), manual_target_type, []
    
    if ALL_PROJECT_CHOICE_VALUE in sel_items_q_manual: manual_selected_project_items_names = all_project_items_manual
    else: manual_selected_project_items_names = [i for i in sel_items_q_manual if i != ALL_PROJECT_CHOICE_VALUE]
    if not manual_selected_project_items_names: click.echo(f"No {manual_target_type.lower()} effectively selected."); return all_selected_full_field_names, defaultdict(dict), manual_target_type, []

    click.echo(f"\n--- Define FLS Permissions for Manually Selected Fields (for {len(manual_selected_project_items_names)} {manual_target_type}) ---")
    
    template = questionary.select(f'FLS Permission template for ALL {len(all_selected_full_field_names)} selected fields:', choices=['Read Only', 'Read & Edit', 'No Access', 'Custom (per field)']).ask()
    if not template: return all_selected_full_field_names, defaultdict(dict), manual_target_type, manual_selected_project_items_names
    
    base_perms_map = {'Read Only': (True, False), 'Read & Edit': (True, True), 'No Access': (False, False)}
    
    if template != 'Custom (per field)':
        r_template, e_template = base_perms_map[template]
        for ffn in all_selected_full_field_names:
            is_formula = field_type_map.get(ffn) == 'Formula'
            r, e = r_template, e_template
            if is_formula: e = False # Force edit to false for formulas
            for target_item_name in manual_selected_project_items_names: field_new_perms[ffn][target_item_name] = (r, e)
    else:
        click.echo(f"--- Define Custom FLS Permissions (will apply to all selected {manual_target_type}) ---")
        for ffn in sorted(all_selected_full_field_names):
            is_formula = field_type_map.get(ffn) == 'Formula'
            
            # Offer different choices for formula fields
            if is_formula:
                choices_for_field = ['Read Only', 'No Access']
                prompt = f"FLS Visibility for formula field {click.style(ffn, bold=True)}:"
            else:
                choices_for_field = ['Read Only', 'Read & Edit', 'No Access']
                prompt = f"FLS Permissions for field {click.style(ffn, bold=True)}:"
            
            choice = questionary.select(prompt, choices=choices_for_field).ask()
            if not choice: click.echo(f"Skipping {ffn}."); continue
            
            r, e = base_perms_map[choice]
            for target_item_name in manual_selected_project_items_names: field_new_perms[ffn][target_item_name] = (r, e)
            
    return all_selected_full_field_names, field_new_perms, manual_target_type, manual_selected_project_items_names
    

def _get_csv_field_definitions(meta: Path) -> tuple[list[str], defaultdict, list[str], list[str], dict]:
    all_selected_full_field_names = []
    field_new_perms = defaultdict(dict)
    csv_target_profiles = []
    csv_target_permsets = []
    csv_data_rows_by_field = {}
    click.echo("\n--- Load Field Security from CSV Report ---")
    csv_dir_path_str = questionary.path( "Enter directory path for CSV report (press Enter for current dir):", default=".", only_directories=True, validate=lambda p: Path(p).is_dir() or "Path is not a valid directory." ).ask()
    if csv_dir_path_str is None: return [], defaultdict(dict), [], [], {}
    csv_dir_path = Path(csv_dir_path_str if csv_dir_path_str.strip() else ".").resolve()
    click.echo(f"Looking for CSV files in: {csv_dir_path}")
    csv_files_in_dir = sorted([f.name for f in csv_dir_path.glob('*.csv') if f.is_file()])
    if not csv_files_in_dir: click.echo(f"No CSV files found in directory: {csv_dir_path}"); return [], defaultdict(dict), [], [], {}
    selected_csv_file_name = questionary.select("Select the CSV report file to load:", choices=csv_files_in_dir).ask()
    if not selected_csv_file_name: return [], defaultdict(dict), [], [], {}
    csv_path = csv_dir_path / selected_csv_file_name
    click.echo(f"Reading CSV report: {csv_path}")
    temp_fields_from_csv = set()
    project_profiles_set = set(list_profiles(meta))
    project_permsets_set = set(list_permission_sets(meta))
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as infile:
            reader = csv.DictReader(infile)
            if not reader.fieldnames or not all(k in reader.fieldnames for k in ['ObjectName', 'FieldName']):
                click.echo(click.style("Error: CSV header must contain 'ObjectName' and 'FieldName' columns.", fg='red')); return [], defaultdict(dict), [], [], {}
            for col_name in reader.fieldnames:
                if col_name not in ['ObjectName', 'FieldName', 'Field Type', 'FieldType']:
                    if col_name in project_profiles_set: csv_target_profiles.append(col_name)
                    elif col_name in project_permsets_set: csv_target_permsets.append(col_name)
            if not csv_target_profiles and not csv_target_permsets: click.echo(click.style("Warning: No columns in the CSV header match existing Profile or Permission Set names in your project.", fg='yellow'))
            parsed_perm_count = 0
            for row_num, row_dict in enumerate(reader, 1):
                obj_name = row_dict.get('ObjectName','').strip(); field_name = row_dict.get('FieldName','').strip()
                if not obj_name or not field_name: click.echo(f"Warning: Skipping row {row_num} in CSV due to missing ObjectName or FieldName."); continue
                full_field_name = f"{obj_name}.{field_name}"
                temp_fields_from_csv.add(full_field_name)
                # csv_data_rows_by_field[full_field_name] = row_dict # Store if needed later
                for target_item_name in csv_target_profiles + csv_target_permsets:
                    if target_item_name in row_dict:
                        perm_str = row_dict[target_item_name].upper().strip()
                        r_csv, e_csv = False, False
                        if perm_str == ACCESS_RW: r_csv, e_csv = True, True
                        elif perm_str == ACCESS_R_ONLY: r_csv = True
                        elif perm_str == ACCESS_NONE: r_csv, e_csv = False, False
                        else: click.echo(f"  Warning: Unrecognized CSV permission '{perm_str}' for field '{full_field_name}' on target '{target_item_name}'. Defaulting to No Access.")
                        field_new_perms[full_field_name][target_item_name] = (r_csv, e_csv)
                        parsed_perm_count +=1
        all_selected_full_field_names = sorted(list(temp_fields_from_csv))
        if not all_selected_full_field_names: click.echo("No valid ObjectName.FieldName combinations found in the CSV file."); return [], defaultdict(dict), [], [], {}
        click.echo(f"Loaded {len(all_selected_full_field_names)} unique fields from CSV.")
        if csv_target_profiles: click.echo(f"  Identified {len(set(csv_target_profiles))} matching Profile column(s) in CSV: {', '.join(sorted(list(set(csv_target_profiles))))}")
        if csv_target_permsets: click.echo(f"  Identified {len(set(csv_target_permsets))} matching Permission Set column(s) in CSV: {', '.join(sorted(list(set(csv_target_permsets))))}")
        if parsed_perm_count == 0 and all_selected_full_field_names: click.echo(click.style(f"Warning: No permissions could be derived from the CSV for the loaded fields.", fg='yellow'))
    except FileNotFoundError: click.echo(click.style(f"Error: CSV file not found at {csv_path}", fg='red')); return [], defaultdict(dict), [], [], {}
    except csv.Error as e: click.echo(click.style(f"Error reading or parsing CSV: {e}", fg='red')); return [], defaultdict(dict), [], [], {}
    except Exception as e: click.echo(click.style(f"An unexpected error occurred while processing CSV: {e}", fg='red')); return [], defaultdict(dict), [], [], {}
    return all_selected_full_field_names, field_new_perms, sorted(list(set(csv_target_profiles))), sorted(list(set(csv_target_permsets))), csv_data_rows_by_field

def _handle_field_security_source_selection(meta: Path) -> tuple[list[str], defaultdict, dict]:
    all_selected_full_field_names = []
    field_new_perms = defaultdict(dict)
    source_details_for_planning = {'targets_profiles': [], 'targets_permsets': []} # Ensure these keys exist
    field_source_method = questionary.select( "How to specify FLS changes?", choices=[ "Manual Field Selection & FLS Permission Definition", "Load FLS from CSV Report" ] ).ask()
    if not field_source_method: click.echo("Cancelled."); return [], defaultdict(dict), {}
    source_details_for_planning['field_source_method'] = field_source_method
    if field_source_method == "Manual Field Selection & FLS Permission Definition":
        all_selected_full_field_names, field_new_perms, manual_target_type, manual_selected_project_items_names = _get_manual_field_definitions(meta)
        source_details_for_planning['manual_target_type'] = manual_target_type # Keep for specific logic if needed
        source_details_for_planning['manual_selected_project_items_names'] = manual_selected_project_items_names
        if manual_target_type == "Profiles": source_details_for_planning['targets_profiles'] = manual_selected_project_items_names
        else: source_details_for_planning['targets_permsets'] = manual_selected_project_items_names
    elif field_source_method == "Load FLS from CSV Report":
        all_selected_full_field_names, field_new_perms, csv_target_profiles, csv_target_permsets, _ = _get_csv_field_definitions(meta)
        source_details_for_planning['targets_profiles'] = csv_target_profiles
        source_details_for_planning['targets_permsets'] = csv_target_permsets
    else: return [], defaultdict(dict), {}
    return all_selected_full_field_names, field_new_perms, source_details_for_planning

def _prepare_and_display_planned_fls_changes( meta: Path, all_selected_full_field_names: list[str], field_new_perms: defaultdict, source_details: dict ) -> tuple[list[dict], defaultdict, set[str], set[str], dict]:
    planned_changes_for_backup_csv = []
    items_with_actual_changes_map = defaultdict(list)
    profiles_actually_changed = set()
    permsets_actually_changed = set()
    item_xml_cache = {}
    if not field_new_perms and all_selected_full_field_names: click.echo("No FLS permissions were defined or derived. Aborting."); return [], defaultdict(list), set(), set(), {}
    if not all_selected_full_field_names: click.echo("No fields targeted for FLS update. Aborting."); return [], defaultdict(list), set(), set(), {}
    
    click.echo(f"\n=== PLANNED FLS CHANGES ===")
    all_target_items_from_definitions = set()
    for perms_for_field in field_new_perms.values(): all_target_items_from_definitions.update(perms_for_field.keys())
    explicit_targets = set(source_details.get('targets_profiles', [])) | set(source_details.get('targets_permsets', []))
    all_target_items_to_consider = all_target_items_from_definitions | explicit_targets
    if not all_target_items_to_consider: click.echo("No target Profiles or Permission Sets identified."); return [], defaultdict(list), set(), set(), {}
    
    project_profiles_set = set(list_profiles(meta)); project_permsets_set = set(list_permission_sets(meta))
    for item_name in all_target_items_to_consider:
        if item_name in item_xml_cache: continue
        item_type_for_cache = "Unknown"; item_path_xml = None
        if item_name in project_profiles_set: item_type_for_cache = "Profile"; item_path_xml = meta / 'profiles' / f'{item_name}{PROFILE_SUFFIX}'
        elif item_name in project_permsets_set: item_type_for_cache = "PermissionSet"; item_path_xml = meta / 'permissionsets' / f'{item_name}{PERMISSIONSET_SUFFIX}'
        else:
            click.echo(f"Info (FLS): Target '{item_name}' not found in project. A new file might be created if changes apply.")
            item_type_for_cache = "Profile" if "profile" in item_name.lower() else "PermissionSet"
            new_root = ET.Element(f'{{{NS["sf"]}}}{item_type_for_cache}'); new_root.set('xmlns', NS['sf'])
            if item_type_for_cache == "Profile": ET.SubElement(new_root, f'{{{NS["sf"]}}}userLicense').text = "Salesforce"
            item_xml_cache[item_name] = (new_root, item_type_for_cache)
            continue
        _, root = load_xml(item_path_xml)
        item_xml_cache[item_name] = (root, item_type_for_cache)
        
    max_field_len = max((len(f) for f in all_selected_full_field_names), default=30) if all_selected_full_field_names else 30
    field_col_width = max(max_field_len, len("Object.Field")) + 2
    any_changes_planned_overall = False

    for target_item_name in sorted(list(all_target_items_to_consider)):
        target_specific_root, item_type_str = item_xml_cache.get(target_item_name, (None, "Unknown"))
        if item_type_str == "Unknown" and target_specific_root is None: continue
        
        get_perms_for_target_func = get_field_permissions_from_profile_root if item_type_str == "Profile" else get_field_permissions_from_permissionset_root
        changes_for_this_target_display = []; has_changes_for_this_item = False
        hdr_line_display = f"{'Object.Field':<{field_col_width}}{'Current FLS':<16}{'New FLS':<12}"
        
        fields_to_check_for_this_target = [ffn for ffn in all_selected_full_field_names if target_item_name in field_new_perms.get(ffn, {})]
        
        for ffn in sorted(fields_to_check_for_this_target):
            current_r, current_e = (False, False)
            if target_specific_root: current_r, current_e = get_perms_for_target_func(target_specific_root, ffn)
            new_r, new_e = field_new_perms[ffn][target_item_name]
            if (current_r != new_r or current_e != new_e):
                changes_for_this_target_display.append(f"{ffn:<{field_col_width}}{format_access_display(current_r, current_e):<16}{format_access_display(new_r, new_e):<12}")
                planned_changes_for_backup_csv.append({'TargetItem': target_item_name, 'TargetType': item_type_str, 'Object.Field': ffn, 'Before Read': current_r, 'Before Edit': current_e, 'New Read': new_r, 'New Edit': new_e, 'ChangeType': 'FLS'})
                any_changes_planned_overall = True; has_changes_for_this_item = True
        
        if changes_for_this_target_display:
            click.echo(f"\n--- Target {item_type_str}: {click.style(target_item_name, bold=True)} (FLS Changes) ---")
            has_override, override_name = check_for_system_overrides(target_specific_root, item_type_str)
            if has_override:
                warning_message = (
                    f"Warning: This {item_type_str} has '{override_name}' enabled. "
                    f"The FLS changes below might be overridden (e.g., granting effective Read/Edit to all fields)."
                )
                click.echo(click.style(warning_message, fg='yellow', bold=True))

            
            click.echo(hdr_line_display); click.echo('-' * len(hdr_line_display))
            for line in changes_for_this_target_display: click.echo(line)
            click.echo('-' * len(hdr_line_display))
        elif target_item_name in explicit_targets:
             click.echo(f"\n--- Target {item_type_str}: {click.style(target_item_name, bold=True)} (FLS Changes) ---")
             click.echo("  (No FLS changes planned for this item.)")

    if not any_changes_planned_overall:
        if all_target_items_to_consider: click.echo(click.style("\nNo effective FLS changes were planned.", fg='yellow'))
        return [], defaultdict(list), set(), set(), item_xml_cache
        
    for change in planned_changes_for_backup_csv:
        items_with_actual_changes_map[change['TargetItem']].append(change['Object.Field'])
        if change['TargetType'] == "Profile": profiles_actually_changed.add(change['TargetItem'])
        elif change['TargetType'] == "PermissionSet": permsets_actually_changed.add(change['TargetItem'])
        
    return planned_changes_for_backup_csv, items_with_actual_changes_map, profiles_actually_changed, permsets_actually_changed, item_xml_cache

def _apply_bulk_fls_modifications_to_files( meta: Path, base_dir: Path, planned_changes_for_backup_csv: list[dict], items_with_actual_changes_map: defaultdict, profiles_actually_changed: set[str], permsets_actually_changed: set[str], item_xml_cache: dict, field_new_perms: defaultdict ):
    backup_root, _, _ = create_backup( meta, base_dir, list(profiles_actually_changed), list(permsets_actually_changed), "bulk_apply_fls" )
    if planned_changes_for_backup_csv:
        planned_csv_path = backup_root / 'applied_fls_permissions_summary.csv'
        try:
            with open(planned_csv_path, 'w', newline='', encoding='utf-8') as cf:
                writer = csv.DictWriter(cf, fieldnames=['TargetItem', 'TargetType', 'Object.Field', 'Before Read', 'Before Edit', 'New Read', 'New Edit', 'ChangeType'])
                writer.writeheader(); writer.writerows(planned_changes_for_backup_csv)
            click.echo(f"Applied FLS changes summary saved to {planned_csv_path}")
        except IOError as e_csv: click.echo(click.style(f"Error saving applied FLS changes CSV: {e_csv}", fg='red'))
    
    modified_profiles_for_pkg = set(); modified_permsets_for_pkg = set()
    click.echo("\n--- Applying FLS Changes ---")

    # Create a cache for field types to avoid repeatedly calling list_fields
    field_type_cache = {}
    
    for target_item_name, changed_fields_for_item in items_with_actual_changes_map.items():
        cached_root, item_type_str_from_cache = item_xml_cache.get(target_item_name, (None, "Unknown"))
        current_item_path_part = "profiles" if item_type_str_from_cache == "Profile" else "permissionsets"
        current_item_suffix = PROFILE_SUFFIX if item_type_str_from_cache == "Profile" else PERMISSIONSET_SUFFIX
        item_type_display = item_type_str_from_cache
        item_path_xml = meta / current_item_path_part / f'{target_item_name}{current_item_suffix}'
        tree = None; root_for_write = None
        if item_path_xml.exists():
            tree, root_for_write = load_xml(item_path_xml)
            if tree is None or root_for_write is None: click.echo(f"Error: Could not load XML for {item_type_display} '{target_item_name}'. Skipping FLS update."); continue
        elif cached_root is not None : root_for_write = cached_root; tree = ET.ElementTree(root_for_write)
        else: click.echo(f"Critical Error: No XML root for {target_item_name} and file does not exist. Skipping."); continue
        
        made_change_in_this_item_file = False
        for ffn in changed_fields_for_item:
            if ffn in field_new_perms and target_item_name in field_new_perms[ffn]:
                new_r, new_e = field_new_perms[ffn][target_item_name]
                
                # Get field type from cache or by listing fields
                field_type = field_type_cache.get(ffn)
                if not field_type:
                    obj_name, _ = ffn.split('.', 1)
                    fields_on_obj = list_fields(meta, obj_name)
                    for f_name_obj, f_type_obj in fields_on_obj:
                        field_type_cache[f"{obj_name}.{f_name_obj}"] = f_type_obj
                    field_type = field_type_cache.get(ffn, 'Unknown') # Default to Unknown if not found

                if update_permission(root_for_write, ffn, new_r, new_e, field_type=field_type):
                    made_change_in_this_item_file = True

        if made_change_in_this_item_file:
            try:
                if hasattr(ET, 'indent'): ET.indent(tree, space="    ")
                item_path_xml.parent.mkdir(parents=True, exist_ok=True)
                tree.write(item_path_xml, encoding='UTF-8', xml_declaration=True)
                if item_type_display == "Profile": modified_profiles_for_pkg.add(target_item_name)
                elif item_type_display == "PermissionSet": modified_permsets_for_pkg.add(target_item_name)
                click.echo(f"Successfully applied FLS changes to {item_type_display} {target_item_name}")
            except IOError as e_write: click.echo(click.style(f"Error writing FLS changes to {item_type_display} {target_item_name}: {e_write}", fg='red'))

    if modified_profiles_for_pkg or modified_permsets_for_pkg:
        pkg_tree = generate_package_xml_for_deployment(list(modified_profiles_for_pkg), list(modified_permsets_for_pkg))
        if pkg_tree:
            pkg_file_path = meta / 'package.xml'
            try:
                pkg_tree.write(pkg_file_path, encoding='UTF-8', xml_declaration=True)
                click.echo(f"package.xml updated at {pkg_file_path} with {len(modified_profiles_for_pkg)} Profile(s) and {len(modified_permsets_for_pkg)} Permission Set(s) for FLS.")
            except IOError as e_pkg: click.echo(click.style(f"Error writing package.xml for FLS: {e_pkg}", fg='red'))
        click.echo(click.style(f"Bulk FLS update complete. Remember to deploy the changes.", bold=True))
    else: click.echo(f"Bulk FLS update process finished, but no Profiles or Permission Sets were actually modified.")

def bulk_apply_fls(meta: Path, base_dir: Path, dry_run: bool):
    click.echo("\n--- Modify Field Security (FLS) ---")
    all_selected_full_field_names, field_new_perms, source_details = _handle_field_security_source_selection(meta)
    if not all_selected_full_field_names and not field_new_perms:
        if not source_details: click.echo("FLS application cancelled.")
        return
    planned_changes_for_backup_csv, items_with_actual_changes_map, profiles_actually_changed, permsets_actually_changed, item_xml_cache = _prepare_and_display_planned_fls_changes( meta, all_selected_full_field_names, field_new_perms, source_details )
    if not items_with_actual_changes_map: return
    total_items_with_changes = len(items_with_actual_changes_map)
    if dry_run:
        click.echo(click.style(f"\nDRY RUN (FLS): No files will be modified.", fg='yellow'))
        if total_items_with_changes > 0:
            backup_root_dry_run, _, _ = create_backup( meta, base_dir, list(profiles_actually_changed), list(permsets_actually_changed), "dry_run_bulk_apply_fls" )
            if planned_changes_for_backup_csv:
                dry_run_planned_csv_path = backup_root_dry_run / 'dry_run_planned_fls_summary.csv'
                try:
                    with open(dry_run_planned_csv_path, 'w', newline='', encoding='utf-8') as cf_dry:
                        writer_dry = csv.DictWriter(cf_dry, fieldnames=['TargetItem', 'TargetType', 'Object.Field', 'Before Read', 'Before Edit', 'New Read', 'New Edit', 'ChangeType'])
                        writer_dry.writeheader(); writer_dry.writerows(planned_changes_for_backup_csv)
                    click.echo(f"DRY RUN (FLS): Planned changes summary saved to {dry_run_planned_csv_path}")
                except IOError as e_dry_csv: click.echo(click.style(f"DRY RUN (FLS): Error saving planned changes CSV: {e_dry_csv}", fg='red'))
            pkg_tree_dry = generate_package_xml_for_deployment(list(profiles_actually_changed), list(permsets_actually_changed))
            if pkg_tree_dry: click.echo(f"DRY RUN (FLS): Would generate package.xml for {total_items_with_changes} item(s).")
            else: click.echo(f"DRY RUN (FLS): Package.xml would not be generated (no items with FLS changes).")
        else: click.echo(f"DRY RUN (FLS): No FLS changes planned, so no backup or package.xml would be generated.")
        return
    if not questionary.confirm(f'Apply these planned FLS changes to {total_items_with_changes} item(s)?', default=False).ask():
        click.echo('FLS Operation canceled.'); return
    _apply_bulk_fls_modifications_to_files( meta, base_dir, planned_changes_for_backup_csv, items_with_actual_changes_map, profiles_actually_changed, permsets_actually_changed, item_xml_cache, field_new_perms )


# --- Object Permission Modification Specific Helpers ---

def _get_manual_object_permission_definitions(meta: Path) -> tuple[set[str], set[str], set[str], defaultdict]:
    definitions = defaultdict(lambda: defaultdict(dict))
    profiles_selected = set()
    permsets_selected = set()
    all_selected_objects_set = set()

    target_type_choice = questionary.select("Modify object permissions for:", choices=["Profiles", "Permission Sets"]).ask()
    if not target_type_choice: return set(), set(), set(), definitions

    list_func = list_profiles if target_type_choice == "Profiles" else list_permission_sets
    all_available_items = list_func(meta)
    if not all_available_items:
        click.echo(f"No {target_type_choice.lower()} found in your project."); return set(), set(), set(), definitions

    item_choices = [questionary.Choice(f'[ALL {target_type_choice.upper()}]', value=ALL_CHOICE_VALUE)] + [q_choice(i) for i in all_available_items for q_choice in [questionary.Choice]] # Python 3.8+
    selected_items_q = questionary.checkbox(f'Select {target_type_choice} to modify permissions for:', choices=item_choices).ask()
    if not selected_items_q:
        click.echo(f'No {target_type_choice.lower()} selected.'); return set(), set(), set(), definitions
    targets_to_modify_names = all_available_items if ALL_CHOICE_VALUE in selected_items_q else [i for i in selected_items_q if i != ALL_CHOICE_VALUE]
    if not targets_to_modify_names:
        click.echo(f'No {target_type_choice.lower()} effectively selected.'); return set(), set(), set(), definitions

    if target_type_choice == "Profiles": profiles_selected.update(targets_to_modify_names)
    else: permsets_selected.update(targets_to_modify_names)

    all_project_objects = list_objects(meta)
    if not all_project_objects:
        click.echo("No objects found in project."); return set(), set(), set(), definitions
    selected_objects_for_perm_change = questionary.checkbox("Select objects to define permissions for:", choices=all_project_objects).ask()
    if not selected_objects_for_perm_change:
        click.echo("No objects selected."); return set(), set(), set(), definitions
    all_selected_objects_set.update(selected_objects_for_perm_change)

    click.echo(f"\n--- Define Object Permissions for {len(selected_objects_for_perm_change)} object(s) ---")
    click.echo(f"(These will be applied to all {len(targets_to_modify_names)} selected {target_type_choice.lower()})")

    perm_template_choice = questionary.select(
        f"Permission template for ALL selected objects:",
        choices=["No Access", "Read Only", "Read/Create", "Read/Edit", "Full CRUD", "Full Control", "Custom (per object)"]
    ).ask()
    if not perm_template_choice:
        click.echo("Cancelled."); return set(), set(), set(), definitions

    base_object_perms_templates = {
        "No Access": {tag: False for tag in OBJECT_PERM_TAGS},
        "Read Only": {'allowCreate': False, 'allowRead': True, 'allowEdit': False, 'allowDelete': False, 'viewAllRecords': True, 'modifyAllRecords': False},
        "Read/Create": {'allowCreate': True, 'allowRead': True, 'allowEdit': False, 'allowDelete': False, 'viewAllRecords': True, 'modifyAllRecords': False},
        "Read/Edit": {'allowCreate': False, 'allowRead': True, 'allowEdit': True, 'allowDelete': False, 'viewAllRecords': True, 'modifyAllRecords': False},
        "Full CRUD": {'allowCreate': True, 'allowRead': True, 'allowEdit': True, 'allowDelete': True, 'viewAllRecords': True, 'modifyAllRecords': False},
        "Full Control": {'allowCreate': True, 'allowRead': True, 'allowEdit': True, 'allowDelete': True, 'viewAllRecords': True, 'modifyAllRecords': True},
    }

    object_permission_template_map = {}
    if perm_template_choice != "Custom (per object)":
        perms_to_set = base_object_perms_templates[perm_template_choice]
        for obj_name in selected_objects_for_perm_change:
            object_permission_template_map[obj_name] = perms_to_set.copy()
    else:
        for obj_name in sorted(selected_objects_for_perm_change):
            click.echo(f"\nDefining permissions for object: {click.style(obj_name, bold=True)}")
            custom_perms = {tag: False for tag in OBJECT_PERM_TAGS}
            if questionary.confirm(f"  Allow Create (c)?", default=False).ask(): custom_perms['allowCreate'] = True
            if questionary.confirm(f"  Allow Read (r)?", default=custom_perms['allowCreate']).ask(): custom_perms['allowRead'] = True
            if questionary.confirm(f"  Allow Edit (u)?", default=False).ask(): custom_perms['allowEdit'] = True
            if questionary.confirm(f"  Allow Delete (d)?", default=False).ask(): custom_perms['allowDelete'] = True
            if questionary.confirm(f"  View All Records (VA)?", default=custom_perms['allowRead']).ask(): custom_perms['viewAllRecords'] = True
            if questionary.confirm(f"  Modify All Records (MA)?", default=False).ask(): custom_perms['modifyAllRecords'] = True
            if custom_perms['modifyAllRecords']: custom_perms['viewAllRecords'] = True; custom_perms['allowRead'] = True;
            if custom_perms['viewAllRecords']: custom_perms['allowRead'] = True
            if custom_perms['allowEdit'] or custom_perms['allowDelete']: custom_perms['allowRead'] = True
            object_permission_template_map[obj_name] = custom_perms
            click.echo(f"  Permissions for {obj_name}: {format_object_perms_display(custom_perms)}")

    for obj_name in all_selected_objects_set:
        perms_for_this_object = object_permission_template_map.get(obj_name, {})
        for target_name in targets_to_modify_names:
            definitions[obj_name][target_name] = perms_for_this_object.copy()
    return all_selected_objects_set, profiles_selected, permsets_selected, definitions

# --- Object Permission Modification Specific Helpers ---
def _get_manual_object_permission_definitions(meta: Path) -> tuple[set[str], set[str], set[str], defaultdict]:
    """Guides the user through manually defining object permission changes for selected Profiles/Permission Sets."""
    definitions = defaultdict(lambda: defaultdict(dict))
    profiles_selected = set()
    permsets_selected = set()
    all_selected_objects_set = set()

    target_type_choice = questionary.select("Modify object permissions for:", choices=["Profiles", "Permission Sets"]).ask()
    if not target_type_choice: return set(), set(), set(), definitions

    list_func = list_profiles if target_type_choice == "Profiles" else list_permission_sets
    all_available_items = list_func(meta)
    if not all_available_items:
        click.echo(f"No {target_type_choice.lower()} found in your project."); return set(), set(), set(), definitions

    item_choices = [questionary.Choice(f'[ALL {target_type_choice.upper()}]', value=ALL_CHOICE_VALUE)] + [questionary.Choice(i) for i in all_available_items]
    selected_items_q = questionary.checkbox(f'Select {target_type_choice} to modify permissions for:', choices=item_choices).ask()
    if not selected_items_q:
        click.echo(f'No {target_type_choice.lower()} selected.'); return set(), set(), set(), definitions
    
    targets_to_modify_names = all_available_items if ALL_CHOICE_VALUE in selected_items_q else [i for i in selected_items_q if i != ALL_CHOICE_VALUE]
    if not targets_to_modify_names:
        click.echo(f'No {target_type_choice.lower()} effectively selected.'); return set(), set(), set(), definitions

    if target_type_choice == "Profiles": profiles_selected.update(targets_to_modify_names)
    else: permsets_selected.update(targets_to_modify_names)

    all_project_objects = list_objects(meta)
    if not all_project_objects:
        click.echo("No objects found in project."); return set(), set(), set(), definitions
    selected_objects_for_perm_change = questionary.checkbox("Select objects to define permissions for:", choices=all_project_objects).ask()
    if not selected_objects_for_perm_change:
        click.echo("No objects selected."); return set(), set(), set(), definitions
    all_selected_objects_set.update(selected_objects_for_perm_change)

    click.echo(f"\n--- Define Object Permissions for {len(selected_objects_for_perm_change)} object(s) ---")
    click.echo(f"(These will be applied to all {len(targets_to_modify_names)} selected {target_type_choice.lower()})")

    perm_template_choice = questionary.select(
        f"Permission template for ALL selected objects:",
        choices=["No Access", "Read Only", "Read/Create", "Read/Edit", "Full CRUD", "Full Control", "Custom (per object)"]
    ).ask()
    if not perm_template_choice:
        click.echo("Cancelled."); return set(), set(), set(), definitions

    base_object_perms_templates = {
        "No Access": {tag: False for tag in OBJECT_PERM_TAGS},
        "Read Only": {'allowCreate': False, 'allowRead': True, 'allowEdit': False, 'allowDelete': False, 'viewAllRecords': False, 'modifyAllRecords': False},
        "Read/Create": {'allowCreate': True, 'allowRead': True, 'allowEdit': False, 'allowDelete': False, 'viewAllRecords': False, 'modifyAllRecords': False},
        "Read/Edit": {'allowCreate': False, 'allowRead': True, 'allowEdit': True, 'allowDelete': False, 'viewAllRecords': False, 'modifyAllRecords': False},
        "Full CRUD": {'allowCreate': True, 'allowRead': True, 'allowEdit': True, 'allowDelete': True, 'viewAllRecords': False, 'modifyAllRecords': False},
        "Full Control": {'allowCreate': True, 'allowRead': True, 'allowEdit': True, 'allowDelete': True, 'viewAllRecords': True, 'modifyAllRecords': True},
    }

    object_permission_template_map = {}
    if perm_template_choice != "Custom (per object)":
        perms_to_set = base_object_perms_templates[perm_template_choice]
        for obj_name in selected_objects_for_perm_change:
            object_permission_template_map[obj_name] = perms_to_set.copy()
    else:
        for obj_name in sorted(selected_objects_for_perm_change):
            click.echo(f"\nDefining permissions for object: {click.style(obj_name, bold=True)}")
            custom_perms = {tag: False for tag in OBJECT_PERM_TAGS}
            if questionary.confirm(f"  Allow Create (c)?", default=False).ask(): custom_perms['allowCreate'] = True
            if questionary.confirm(f"  Allow Read (r)?", default=custom_perms['allowCreate']).ask(): custom_perms['allowRead'] = True
            if questionary.confirm(f"  Allow Edit (u)?", default=False).ask(): custom_perms['allowEdit'] = True
            if questionary.confirm(f"  Allow Delete (d)?", default=False).ask(): custom_perms['allowDelete'] = True
            if questionary.confirm(f"  View All Records (VA)?", default=custom_perms['allowRead']).ask(): custom_perms['viewAllRecords'] = True
            if questionary.confirm(f"  Modify All Records (MA)?", default=False).ask(): custom_perms['modifyAllRecords'] = True
            
            # Enforce dependencies
            if custom_perms['modifyAllRecords']: custom_perms['viewAllRecords'] = True; custom_perms['allowRead'] = True
            if custom_perms['viewAllRecords']: custom_perms['allowRead'] = True
            if custom_perms['allowEdit'] or custom_perms['allowDelete']: custom_perms['allowRead'] = True
            
            object_permission_template_map[obj_name] = custom_perms
            click.echo(f"  Permissions for {obj_name}: {format_object_perms_display(custom_perms)}")

    for obj_name in all_selected_objects_set:
        perms_for_this_object = object_permission_template_map.get(obj_name, {})
        for target_name in targets_to_modify_names:
            definitions[obj_name][target_name] = perms_for_this_object.copy()
    return all_selected_objects_set, profiles_selected, permsets_selected, definitions


def _get_csv_object_permission_definitions(meta: Path) -> tuple[set[str], set[str], set[str], defaultdict]:
    """Loads object permission definitions from a user-selected CSV file."""
    definitions = defaultdict(lambda: defaultdict(dict))
    objects_in_csv = set()
    profiles_in_csv = set()
    permsets_in_csv = set()

    click.echo("\n--- Load Object Permissions from CSV Report ---")
    csv_dir_path_str = questionary.path("Enter directory path for CSV report:", default=".", only_directories=True).ask()
    if csv_dir_path_str is None: return set(), set(), set(), definitions
    csv_dir_path = Path(csv_dir_path_str if csv_dir_path_str.strip() else ".").resolve()
    csv_files = sorted([f.name for f in csv_dir_path.glob('*.csv') if f.is_file()])
    if not csv_files: click.echo(f"No CSV files found in {csv_dir_path}."); return set(), set(), set(), definitions
    selected_csv_file = questionary.select("Select CSV file for object permissions:", choices=csv_files).ask()
    if not selected_csv_file: return set(), set(), set(), definitions
    csv_path = csv_dir_path / selected_csv_file

    project_profiles = set(list_profiles(meta))
    project_permsets = set(list_permission_sets(meta))
    valid_target_columns_from_csv = []

    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as infile:
            reader = csv.DictReader(infile)
            if not reader.fieldnames or 'ObjectName' not in reader.fieldnames:
                click.echo(click.style("Error: CSV must contain 'ObjectName' column.", fg='red')); return set(), set(), set(), definitions
            for col_name in reader.fieldnames:
                if col_name == 'ObjectName': continue
                if col_name in project_profiles: profiles_in_csv.add(col_name); valid_target_columns_from_csv.append(col_name)
                elif col_name in project_permsets: permsets_in_csv.add(col_name); valid_target_columns_from_csv.append(col_name)
            
            if not valid_target_columns_from_csv: click.echo(click.style("Warning: No columns in CSV header match existing Profile or Permission Set names in your project.", fg='yellow'))
            
            for row_num, row_dict in enumerate(reader, 1):
                obj_name = row_dict.get('ObjectName','').strip()
                if not obj_name: click.echo(f"Warning: Skipping row {row_num} due to missing ObjectName."); continue
                objects_in_csv.add(obj_name)
                for target_name in valid_target_columns_from_csv:
                    perm_str = row_dict.get(target_name, '').strip()
                    if perm_str:
                        perms_dict = parse_object_perms_string_to_dict(perm_str)
                        if perms_dict is not None: 
                            definitions[obj_name][target_name] = perms_dict
                        else: 
                            click.echo(f"  Skipping permissions for {obj_name} on {target_name} due to parsing error in CSV row {row_num}.")
    except FileNotFoundError: click.echo(click.style(f"Error: CSV file not found: {csv_path}", fg='red'))
    except Exception as e: click.echo(click.style(f"Error processing CSV: {e}", fg='red'))

    if objects_in_csv: click.echo(f"Loaded object permission definitions for {len(objects_in_csv)} objects from CSV.")
    if profiles_in_csv: click.echo(f"  Identified {len(profiles_in_csv)} Profile(s) from CSV columns: {', '.join(sorted(list(profiles_in_csv)))}")
    if permsets_in_csv: click.echo(f"  Identified {len(permsets_in_csv)} Permission Set(s) from CSV columns: {', '.join(sorted(list(permsets_in_csv)))}")
    return objects_in_csv, profiles_in_csv, permsets_in_csv, definitions


def _prepare_and_display_planned_object_permission_changes(
        meta: Path,
        all_involved_objects: set[str],
        object_new_perms_definitions: defaultdict,
        all_involved_targets: set[str]
    ) -> tuple[list[dict], defaultdict, set[str], set[str], dict]:
    """Loads current permissions, calculates differences, and displays a plan with override warnings."""
    planned_changes_for_csv = []
    items_with_actual_changes_map = defaultdict(list)
    profiles_actually_changed = set()
    permsets_actually_changed = set()
    item_xml_cache = {}

    click.echo(f"\n=== PLANNED OBJECT PERMISSION CHANGES ===")
    project_profiles_set = set(list_profiles(meta))
    project_permsets_set = set(list_permission_sets(meta))

    for target_name in all_involved_targets:
        item_type_for_cache = "Unknown"; item_path_xml = None
        if target_name in project_profiles_set:
            item_type_for_cache = "Profile"
            item_path_xml = meta / 'profiles' / f'{target_name}{PROFILE_SUFFIX}'
        elif target_name in project_permsets_set:
            item_type_for_cache = "PermissionSet"
            item_path_xml = meta / 'permissionsets' / f'{target_name}{PERMISSIONSET_SUFFIX}'
        if not item_path_xml:
            click.echo(click.style(f"Warning: Target '{target_name}' not found in project. Skipping.", fg='yellow'))
            continue
        _, root = load_xml(item_path_xml)
        if root is None and item_path_xml.exists():
            click.echo(click.style(f"Error: Could not parse XML for '{target_name}'. Skipping.", fg='red'))
            continue
        item_xml_cache[target_name] = (root, item_type_for_cache)

    max_obj_len = max((len(o) for o in all_involved_objects), default=20) if all_involved_objects else 20
    obj_col_width = max(max_obj_len, len("Object API Name")) + 2
    example_perms_display = format_object_perms_display({tag: True for tag in OBJECT_PERM_TAGS})
    perm_col_width = len(example_perms_display) + 4
    any_changes_overall = False

    for target_name in sorted(list(all_involved_targets)):
        if target_name not in item_xml_cache: continue
        target_specific_root, item_type_str = item_xml_cache[target_name]
        changes_for_this_target_display = []
        hdr_line_display = f"{'Object API Name':<{obj_col_width}}{'Current Perms':<{perm_col_width}}{'New Perms':<{perm_col_width}}"
        objects_to_check_for_this_target = [obj for obj in all_involved_objects if target_name in object_new_perms_definitions.get(obj, {})]
        
        for obj_name in sorted(objects_to_check_for_this_target):
            current_perms = get_object_permissions_from_xml_root(target_specific_root, obj_name)
            new_perms = object_new_perms_definitions[obj_name][target_name]
            is_different = any(current_perms.get(k, False) != new_perms.get(k, False) for k in OBJECT_PERM_TAGS)
            if is_different:
                current_perms_str = format_object_perms_display(current_perms)
                new_perms_str = format_object_perms_display(new_perms)
                changes_for_this_target_display.append(f"{obj_name:<{obj_col_width}}{current_perms_str:<{perm_col_width}}{new_perms_str:<{perm_col_width}}")
                csv_entry = {'TargetItem': target_name, 'TargetType': item_type_str, 'Object API Name': obj_name, 'ChangeType': 'ObjectPermission'}
                for tag_idx, tag_name_csv in enumerate(OBJECT_PERM_TAGS):
                    csv_entry[f'Before {OBJECT_PERM_SHORT[tag_idx]}'] = current_perms.get(tag_name_csv, False)
                    csv_entry[f'New {OBJECT_PERM_SHORT[tag_idx]}'] = new_perms.get(tag_name_csv, False)
                planned_changes_for_csv.append(csv_entry)
                any_changes_overall = True
                items_with_actual_changes_map[target_name].append(obj_name)
                if item_type_str == "Profile": profiles_actually_changed.add(target_name)
                else: permsets_actually_changed.add(target_name)

        if changes_for_this_target_display:
            click.echo(f"\n--- Target {item_type_str}: {click.style(target_name, bold=True)} (Object Permission Changes) ---")
            
            # <<< NEW WARNING LOGIC >>>
            has_override, override_name = check_for_system_overrides(target_specific_root, item_type_str)
            if has_override:
                warning_message = (
                    f"Warning: This {item_type_str} has '{override_name}' enabled. "
                    f"The object permission changes below may be overridden or ignored by the platform."
                )
                click.echo(click.style(warning_message, fg='yellow', bold=True))
            # <<< END NEW WARNING LOGIC >>>

            click.echo(hdr_line_display)
            click.echo('-' * (obj_col_width + perm_col_width * 2))
            for line in changes_for_this_target_display: click.echo(line)
            click.echo('-' * (obj_col_width + perm_col_width * 2))
        elif objects_to_check_for_this_target:
             click.echo(f"\n--- Target {item_type_str}: {click.style(target_name, bold=True)} ---")
             click.echo("  (No object permission changes planned; permissions may already match.)")
    
    if not any_changes_overall and all_involved_targets:
        click.echo(click.style("\nNo effective object permission changes were planned across any targets.", fg='yellow'))
    
    return planned_changes_for_csv, items_with_actual_changes_map, profiles_actually_changed, permsets_actually_changed, item_xml_cache

def _apply_bulk_object_permission_modifications_to_files(
        meta: Path, base_dir: Path, planned_obj_perm_changes_csv: list[dict],
        items_with_obj_perm_changes_map: defaultdict, profiles_with_obj_perm_changes: set[str],
        permsets_with_obj_perm_changes: set[str], item_xml_cache: dict,
        object_new_perms_definitions: defaultdict
    ):
    """Applies the planned object permission changes to the actual XML files."""
    backup_root, _, _ = create_backup( meta, base_dir, list(profiles_with_obj_perm_changes), list(permsets_with_obj_perm_changes), "bulk_apply_object_perms" )
    
    if planned_obj_perm_changes_csv:
        csv_path = backup_root / 'applied_object_permissions_summary.csv'
        try:
            fieldnames = ['TargetItem', 'TargetType', 'Object API Name', 'ChangeType'] + [f'Before {ps}' for ps in OBJECT_PERM_SHORT] + [f'New {ps}' for ps in OBJECT_PERM_SHORT]
            with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
                writer = csv.DictWriter(cf, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(planned_obj_perm_changes_csv)
            click.echo(f"Applied object permission changes summary saved to {csv_path}")
        except IOError as e_csv: click.echo(click.style(f"Error saving applied object permission changes CSV: {e_csv}", fg='red'))
    
    modified_profiles_for_pkg = set()
    modified_permsets_for_pkg = set()
    click.echo("\n--- Applying Object Permission Changes ---")

    for target_item_name, changed_objects_for_item in items_with_obj_perm_changes_map.items():
        cached_root, item_type_str = item_xml_cache.get(target_item_name)
        
        item_path_part = "profiles" if item_type_str == "Profile" else "permissionsets"
        item_suffix = PROFILE_SUFFIX if item_type_str == "Profile" else PERMISSIONSET_SUFFIX
        item_xml_path = meta / item_path_part / f"{target_item_name}{item_suffix}"
        
        tree, root_for_write = load_xml(item_xml_path)
        if not tree or not root_for_write:
            click.echo(f"Error: Could not re-load XML for {item_type_str} '{target_item_name}'. Skipping apply step.")
            continue
        
        made_change_in_this_file = False
        for obj_name in changed_objects_for_item:
            perms_to_apply = object_new_perms_definitions[obj_name][target_item_name]
            if update_object_permission(root_for_write, obj_name, perms_to_apply):
                made_change_in_this_file = True

        if made_change_in_this_file:
            try:
                if hasattr(ET, 'indent'): ET.indent(tree, space="    ")
                item_xml_path.parent.mkdir(parents=True, exist_ok=True)
                tree.write(item_xml_path, encoding='UTF-8', xml_declaration=True)
                if item_type_str == "Profile": modified_profiles_for_pkg.add(target_item_name)
                else: modified_permsets_for_pkg.add(target_item_name)
                click.echo(f"Successfully applied object permission changes to {item_type_str} {target_item_name}")
            except IOError as e_w: click.echo(click.style(f"Error writing object permission changes to {item_xml_path}: {e_w}", fg='red'))
            
    if modified_profiles_for_pkg or modified_permsets_for_pkg:
        pkg_tree = generate_package_xml_for_deployment(list(modified_profiles_for_pkg), list(modified_permsets_for_pkg))
        if pkg_tree:
            pkg_file_path = meta / 'package.xml'
            try: 
                pkg_tree.write(pkg_file_path, encoding='UTF-8', xml_declaration=True)
                click.echo(f"package.xml updated at {pkg_file_path} for object permissions.")
            except IOError as e_pkg: click.echo(click.style(f"Error writing package.xml for object permissions: {e_pkg}", fg='red'))
        click.echo(click.style("Bulk object permission update complete. Remember to deploy the changes.", bold=True))
    else: click.echo("Bulk object permission update process finished, but no files were actually modified.")


def modify_object_permissions(meta: Path, base_dir: Path, dry_run: bool):
    """Main function to orchestrate modifying object permissions."""
    click.echo("\n--- Modify Object Permissions (CRUD, ViewAll, ModifyAll) ---")
    source_choice = questionary.select("How to specify object permission changes?", choices=["Manual Definition", "Load from CSV Report"]).ask()
    if not source_choice: click.echo("Cancelled."); return
    
    object_new_perms_definitions = defaultdict(lambda: defaultdict(dict))
    all_involved_objects, all_involved_profiles, all_involved_permsets = set(), set(), set()

    if source_choice == "Manual Definition":
        all_involved_objects, all_involved_profiles, all_involved_permsets, object_new_perms_definitions = _get_manual_object_permission_definitions(meta)
    elif source_choice == "Load from CSV Report":
        all_involved_objects, all_involved_profiles, all_involved_permsets, object_new_perms_definitions = _get_csv_object_permission_definitions(meta)
    
    if not all_involved_objects or not object_new_perms_definitions:
        click.echo("No object permission definitions were provided or parsed. Aborting."); return
    if not all_involved_profiles and not all_involved_permsets:
        click.echo("No target Profiles or Permission Sets identified from definitions. Aborting."); return

    all_involved_targets_combined = all_involved_profiles.union(all_involved_permsets)
    
    planned_csv, items_with_changes_map, profs_changed_actual, psets_changed_actual, item_xml_cache = _prepare_and_display_planned_object_permission_changes( meta, all_involved_objects, object_new_perms_definitions, all_involved_targets_combined )
    
    if not items_with_changes_map:
        return # Planning step already printed a message.

    total_items_with_changes_count = len(items_with_changes_map)
    if dry_run:
        click.echo(click.style(f"\nDRY RUN (Object Perms): No files will be modified.", fg='yellow'))
        if total_items_with_changes_count > 0:
            backup_root_dry, _, _ = create_backup(meta, base_dir, list(profs_changed_actual), list(psets_changed_actual), "dry_run_object_perms")
            if planned_csv:
                dry_run_csv_path = backup_root_dry / 'dry_run_object_permissions_summary.csv'
                try:
                    fieldnames_dry = ['TargetItem', 'TargetType', 'Object API Name', 'ChangeType'] + [f'Before {ps}' for ps in OBJECT_PERM_SHORT] + [f'New {ps}' for ps in OBJECT_PERM_SHORT]
                    with open(dry_run_csv_path, 'w', newline='', encoding='utf-8') as cf_dry:
                        writer_dry = csv.DictWriter(cf_dry, fieldnames=fieldnames_dry)
                        writer_dry.writeheader()
                        writer_dry.writerows(planned_csv)
                    click.echo(f"DRY RUN: Planned object permission changes summary saved to {dry_run_csv_path}")
                except Exception as e_dry_csv: click.echo(click.style(f"DRY RUN: Error saving planned changes CSV: {e_dry_csv}", fg='red'))
            
            pkg_tree_dry = generate_package_xml_for_deployment(list(profs_changed_actual), list(psets_changed_actual))
            if pkg_tree_dry: click.echo(f"DRY RUN: Would generate package.xml for {len(profs_changed_actual)} Profile(s) and {len(psets_changed_actual)} PS(s).")
        return

    if not questionary.confirm(f'Apply these object permission changes to {total_items_with_changes_count} item(s)?', default=False).ask():
        click.echo('Operation canceled.'); return
        
    _apply_bulk_object_permission_modifications_to_files( meta, base_dir, planned_csv, items_with_changes_map, profs_changed_actual, psets_changed_actual, item_xml_cache, object_new_perms_definitions )



# --- Report Functions (generate_field_security_report, inspect_permission_set_access, etc.) ---

def _select_objects_and_fields_for_report_interactive(meta: Path, purpose_str: str = "the report") -> tuple[list[str], dict[str, list[str]]]:
    objects_to_process = []
    field_selection_map = {} # {'ObjectName': ['FieldName1', 'FieldName2']}
    all_objs_list = list_objects(meta)
    if not all_objs_list: click.echo("No objects found."); return [], {}
    obj_target_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + [questionary.Choice(o) for o in all_objs_list]
    sel_objs_q = questionary.checkbox(f'Select objects to include in {purpose_str}:', choices=obj_target_choices).ask()
    if not sel_objs_q: click.echo(f'No objects selected for {purpose_str}.'); return [], {}
    objects_to_process = all_objs_list if ALL_CHOICE_VALUE in sel_objs_q else [o for o in sel_objs_q if o != ALL_CHOICE_VALUE]
    if not objects_to_process: click.echo(f'No objects effectively selected for {purpose_str}.'); return [], {}
    process_all_fields_globally = False
    if objects_to_process:
        if questionary.confirm(f"Include ALL eligible fields for the {len(objects_to_process)} selected object(s) for {purpose_str}?", default=False).ask():
            process_all_fields_globally = True
    if process_all_fields_globally:
        click.echo("Gathering all eligible fields for selected objects...")
        for obj_name in objects_to_process:
            fields_for_obj = list_fields(meta, obj_name)
            if fields_for_obj: field_selection_map[obj_name] = [fname for fname, ftype in fields_for_obj]
            else: click.echo(f"Info: No eligible fields for '{obj_name}' (when selecting all).")
    else:
        click.echo("Proceeding with per-object field selection...")
        for obj_name in objects_to_process:
            fields_for_obj = list_fields(meta, obj_name)
            if not fields_for_obj: click.echo(f"Info: No eligible fields found for '{obj_name}', skipping."); continue
            if questionary.confirm(f"For object '{obj_name}': Include ALL {len(fields_for_obj)} eligible fields for {purpose_str}?", default=False).ask():
                field_selection_map[obj_name] = [fname for fname, ftype in fields_for_obj]
                click.echo(f"  Added all {len(fields_for_obj)} eligible fields for '{obj_name}'.")
            else:
                field_choices = [questionary.Choice(f"{fname} ({ftype})", value=fname) for fname, ftype in fields_for_obj]
                if not field_choices: click.echo(f"Info: No individual fields available for selection for '{obj_name}'. Skipping."); continue
                chosen_specific_fields = questionary.checkbox(f"Select specific fields for '{obj_name}' for {purpose_str}:", choices=field_choices).ask()
                if chosen_specific_fields:
                    field_selection_map[obj_name] = chosen_specific_fields
                    click.echo(f"  Added {len(chosen_specific_fields)} specific field(s) for '{obj_name}'.")
                else: click.echo(f"No specific fields selected for '{obj_name}'. Skipping this object for field selection.")
    return objects_to_process, field_selection_map

# generate_field_security_report (from v1.2)
def generate_field_security_report(meta: Path, base_dir: Path):
    """Generates a CSV report detailing field-level security for selected objects/fields across specified profiles and auto-discovered relevant permission sets."""
    click.echo("\n--- Generate Field Security Report (Profiles & Auto-PS) ---")

    all_profs = list_profiles(meta); profiles_to_audit = []
    if all_profs:
        profile_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + \
                          [questionary.Choice(p) for p in all_profs]
        sel_profs_q = questionary.checkbox('Select profiles to include (optional, leave blank for none):', choices=profile_choices).ask()
        if sel_profs_q:
            if ALL_CHOICE_VALUE in sel_profs_q: profiles_to_audit = all_profs
            else: profiles_to_audit = [p for p in sel_profs_q if p != ALL_CHOICE_VALUE]

    _, field_selection_map = _select_objects_and_fields_for_report_interactive(meta, "the FLS report")

    if not field_selection_map:
        click.echo("No fields selected for report."); return

    selected_full_field_api_names = {f"{obj_name}.{f_name}" for obj_name, fields in field_selection_map.items() for f_name in fields}
    if not selected_full_field_api_names:
        click.echo("No fields targeted."); return

    click.echo("\nDiscovering relevant permission sets...")
    all_available_permsets = list_permission_sets(meta)
    permsets_to_include_in_report = []; permset_xml_cache = {}
    for ps_name in all_available_permsets:
        _, ps_root = load_xml(meta / 'permissionsets' / f'{ps_name}{PERMISSIONSET_SUFFIX}')
        if ps_root is None: continue
        permset_xml_cache[ps_name] = ps_root # Cache root for later use
        is_relevant = False
        for field_api in selected_full_field_api_names:
            obj_name_from_field = field_api.split('.')[0]
            r_eff, e_eff = get_effective_field_permissions_from_ps_root(ps_root, obj_name_from_field, field_api)
            if r_eff or e_eff:
                is_relevant = True; break
        if is_relevant and ps_name not in permsets_to_include_in_report:
            permsets_to_include_in_report.append(ps_name)

    permsets_to_include_in_report.sort()
    if permsets_to_include_in_report: click.echo(f"Found {len(permsets_to_include_in_report)} relevant PS: {', '.join(permsets_to_include_in_report)}")
    else: click.echo("No relevant PS found for selected fields.")

    if not profiles_to_audit and not permsets_to_include_in_report:
        click.echo("Nothing to report (no profiles selected and no relevant permission sets found)."); return

    ts_formatted = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    output_filename = base_dir / f'Field_Security_Report_{ts_formatted}.csv'
    click.echo("\nGenerating report...")
    profile_xml_cache = {}
    summary_parts = [] # To track if any data was actually written
    total_fields_processed_for_report = 0
    try:
        with open(output_filename, 'w', newline='', encoding='utf-8') as cf:
            header = ['ObjectName', 'FieldName', 'Field Type'] + sorted(profiles_to_audit) + sorted(permsets_to_include_in_report)
            writer = csv.writer(cf); writer.writerow(header)

            for obj_name in sorted(field_selection_map.keys()):
                fields_for_report = sorted(field_selection_map[obj_name])
                if not fields_for_report: continue
                if len(header) - 3 < 15 or total_fields_processed_for_report % 50 == 0 : # reduce noise for many columns
                    click.echo(f"Processing object: {obj_name} ({len(fields_for_report)} fields selected for this object)")

                field_type_map_for_obj = {fname: ftype for fname, ftype in list_fields(meta, obj_name)} # Get types once per object
                for f_name in fields_for_report:
                    full_field_name = f"{obj_name}.{f_name}"; field_type = field_type_map_for_obj.get(f_name, 'Unknown')
                    csv_row = [obj_name, f_name, field_type]
                    for p_name in sorted(profiles_to_audit):
                        if p_name not in profile_xml_cache:
                            _, prof_root = load_xml(meta/'profiles'/f'{p_name}{PROFILE_SUFFIX}')
                            profile_xml_cache[p_name] = prof_root
                        r, e = get_field_permissions_from_profile_root(profile_xml_cache.get(p_name), full_field_name)
                        csv_row.append(format_access_display(r, e))

                    for ps_name in sorted(permsets_to_include_in_report):
                        ps_root_iter = permset_xml_cache.get(ps_name) # Already cached
                        r_ps, e_ps = get_effective_field_permissions_from_ps_root(ps_root_iter, obj_name, full_field_name)
                        csv_row.append(format_access_display(r_ps, e_ps))
                    writer.writerow(csv_row); total_fields_processed_for_report += 1
        # Summary parts updated only if processing happened
        if profiles_to_audit: summary_parts.append(f"{len(profiles_to_audit)} profile(s)")
        if permsets_to_include_in_report: summary_parts.append(f"{len(permsets_to_include_in_report)} auto-discovered PS")

        if total_fields_processed_for_report > 0:
            click.echo(f"\nReport for {total_fields_processed_for_report} field(s) generated.")
            if summary_parts: click.echo(f"Included: {', '.join(summary_parts)}.")
            click.echo(f"Saved to: {output_filename}")
        else:
            click.echo("\nNo data rows were generated for the report (e.g. selected fields had no relevant profiles/PS).")
            if output_filename.exists(): # Remove empty file
                try:
                    output_filename.unlink()
                except OSError:
                    # Optionally, you can log a warning if you want to know if deletion failed
                    click.echo(click.style(f"Warning: Could not remove empty report file {output_filename}", fg='yellow'))
                    pass # Continue if deletion fails

    except IOError as e:
        click.echo(click.style(f"\nError generating report: {e}", fg='red'))
    except Exception as e_generic:
        click.echo(click.style(f"\nAn unexpected error occurred generating report: {e_generic}", fg='red'))
    finally:
        # This finally block might be redundant now if the above logic handles empty file deletion.
        # If 'output_filename' was defined and the file exists AND total_fields_processed_for_report is 0,
        # it means an empty or partially written file might exist if an error occurred mid-processing.
        if 'output_filename' in locals() and output_filename.exists() and total_fields_processed_for_report == 0:
             try:
                 output_filename.unlink()
                 click.echo(f"Cleaned up empty report file: {output_filename}")
             except OSError:
                 click.echo(click.style(f"Warn: Could not remove potentially empty report {output_filename}", fg='yellow'))

# inspect_permission_set_access (from v1.2)
def inspect_permission_set_access(meta_base: Path, fs_tool_files_dir: Path):
    click.echo("\n--- Inspect Permission Set (Objects & Fields) ---")
    all_permsets = list_permission_sets(meta_base)
    if not all_permsets: click.echo("No permission sets found in the metadata."); return
    permset_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + [questionary.Choice(ps) for ps in all_permsets]
    selected_permset_q = questionary.checkbox( "Select permission sets to inspect:", choices=permset_choices ).ask()
    if not selected_permset_q: click.echo("No permission sets selected."); return
    selected_permset_names = sorted(all_permsets) if ALL_CHOICE_VALUE in selected_permset_q else sorted([ps for ps in selected_permset_q if ps != ALL_CHOICE_VALUE])
    if not selected_permset_names: click.echo("No permission sets effectively selected for inspection."); return
    all_permset_data_for_inspection = {}
    click.echo("\nProcessing selected permission sets for inspection...")
    for ps_name in selected_permset_names:
        click.echo(f"  Analysing: {click.style(ps_name, bold=True)}")
        permset_path = meta_base / 'permissionsets' / f'{ps_name}{PERMISSIONSET_SUFFIX}'
        _, root = load_xml(permset_path)
        if root is None: click.echo(click.style(f"    Could not load or parse {ps_name}. Skipping.", fg='yellow')); continue
        current_ps_details = {"objects": defaultdict(dict), "fields": defaultdict(dict), "user_permissions": []}
        obj_perms_nodes = root.findall('.//sf:objectPermissions', NS)
        if not obj_perms_nodes: click.echo("    No specific object permissions found in this permission set.")
        for op_node in obj_perms_nodes:
            obj_api_el = op_node.find('sf:object', NS)
            if obj_api_el is not None and obj_api_el.text:
                obj_name = obj_api_el.text
                current_ps_details["objects"][obj_name] = {tag: op_node.findtext(f'sf:{tag}', default='false', namespaces=NS) == 'true' for tag in OBJECT_PERM_TAGS}
        field_perms_nodes = root.findall('.//sf:fieldPermissions', NS)
        if not field_perms_nodes and obj_perms_nodes : click.echo("    No specific field permissions found in this permission set.")
        for fp_node in field_perms_nodes:
            field_api_el = fp_node.find('sf:field', NS)
            if field_api_el is not None and field_api_el.text:
                full_field_name = field_api_el.text
                readable = fp_node.findtext('sf:readable', default='false', namespaces=NS) == 'true'
                editable = fp_node.findtext('sf:editable', default='false', namespaces=NS) == 'true'
                current_ps_details["fields"][full_field_name] = {"R": readable, "E": editable}
        user_perms_nodes = root.findall('.//sf:userPermissions', NS)
        for up_node in user_perms_nodes:
            name_el_text = up_node.findtext('sf:name', namespaces=NS)
            if name_el_text and up_node.findtext('sf:enabled', default='false', namespaces=NS) == 'true':
                current_ps_details["user_permissions"].append(name_el_text)
        current_ps_details["user_permissions"].sort()
        all_permset_data_for_inspection[ps_name] = current_ps_details
    click.echo(click.style("\n=== CONSOLE OUTPUT ===", bold=True, underline=True))
    object_column_width = 50; field_column_width = 50
    if not all_permset_data_for_inspection: click.echo("No data to display.")
    else:
        for ps_name, data in all_permset_data_for_inspection.items():
            click.echo(f"\n{'-'*10} Access for Permission Set: {click.style(ps_name, fg='cyan', bold=True)} {'-'*10}")
            if data["objects"]:
                click.echo(click.style("\n  Object Permissions:", underline=True))
                example_true_perms_header = format_object_perms_display({tag: True for tag in OBJECT_PERM_TAGS})
                obj_header = f"    {'Object':<{object_column_width}} {example_true_perms_header}"
                click.echo(obj_header)
                click.echo(f"    {'-'*(object_column_width-1)} {' '.join(['-'*len(s) for s in example_true_perms_header.split()])}")
                for obj, perms in sorted(data["objects"].items()): click.echo(f"    {obj:<{object_column_width}} {format_object_perms_display(perms)}")
            else: click.echo("  No explicit object permissions defined.")
            if data["fields"]:
                click.echo(click.style("\n  Field Permissions:", underline=True))
                fields_by_object = defaultdict(list)
                for field_full_name, perms_f in sorted(data["fields"].items()):
                    obj_name, field_name_only = field_full_name.split('.', 1) if '.' in field_full_name else ("_UNKNOWN_OBJECT_?", field_full_name)
                    fields_by_object[obj_name].append((field_name_only, perms_f))
                for obj_name, field_list in sorted(fields_by_object.items()):
                    click.echo(f"    Object: {click.style(obj_name, bold=True)}")
                    field_header = f"      {'Field':<{field_column_width}} {'Access':<8}"; click.echo(field_header)
                    click.echo(f"      {'-'*(field_column_width-1)} {'-'*7}")
                    for field_name_only, field_perms_val in field_list: click.echo(f"      {field_name_only:<{field_column_width}} {format_access_display(field_perms_val['R'], field_perms_val['E']):<8}")
            elif not data["objects"]: click.echo("  No explicit field permissions defined.")
            if data["user_permissions"]:
                click.echo(click.style("\n  Enabled User Permissions:", underline=True))
                for up_name in data["user_permissions"]: click.echo(f"    - {up_name}")
        click.echo(click.style("\n=== END CONSOLE OUTPUT ===", bold=True, underline=True))
    if not all_permset_data_for_inspection: click.echo("\nNo data to write to CSV file."); return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = fs_tool_files_dir / f"Inspect_PermissionSet_Access_{ts}.csv"
    try:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['PermissionSetName', 'PermissionType', 'ObjectName', 'FieldName', 'FieldPermissions', 'ObjCreate', 'ObjRead', 'ObjUpdate', 'ObjDelete', 'ObjViewAll', 'ObjModifyAll', 'UserPermissionName']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames); writer.writeheader()
            object_perm_field_map = {
                'allowCreate': 'ObjCreate',
                'allowRead': 'ObjRead',
                'allowEdit': 'ObjUpdate',
                'allowDelete': 'ObjDelete',
                'viewAllRecords': 'ObjViewAll',
                'modifyAllRecords': 'ObjModifyAll',
            }
            for ps_name, data in all_permset_data_for_inspection.items():
                for obj_name, obj_perms_val in data["objects"].items():
                    mapped_obj_perms = {object_perm_field_map[k]: v for k, v in obj_perms_val.items() if k in object_perm_field_map}
                    writer.writerow({'PermissionSetName': ps_name, 'PermissionType': 'Object', 'ObjectName': obj_name, **mapped_obj_perms})
                for field_full_name, field_perms_val in data["fields"].items():
                    obj_part, field_part = field_full_name.split('.', 1) if '.' in field_full_name else (field_full_name, '')
                    writer.writerow({'PermissionSetName': ps_name, 'PermissionType': 'Field', 'ObjectName': obj_part, 'FieldName': field_part, 'FieldPermissions': format_access_display(field_perms_val['R'], field_perms_val['E'])})
                for up_name in data["user_permissions"]: writer.writerow({'PermissionSetName': ps_name, 'PermissionType': 'UserPermission', 'UserPermissionName': up_name})
        click.echo(f"\nPermission Set inspection CSV report saved to: {csv_filename}")
    except IOError as e: click.echo(click.style(f"Error writing CSV report: {e}", fg='red'))

def audit_all_fields_by_selected_permission_sets(meta: Path, base_dir: Path):
    click.echo("\n--- Audit Field Access by Selected Permission Sets (Field-Centric Matrix) ---")
    all_available_permsets = list_permission_sets(meta)
    if not all_available_permsets: click.echo("No permission sets found in metadata."); return
    permset_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + [questionary.Choice(ps) for ps in all_available_permsets]
    selected_permset_q = questionary.checkbox("Select Permission Sets to include as columns:", choices=permset_choices).ask()
    if not selected_permset_q: click.echo("No permission sets selected."); return
    permsets_for_report_columns = sorted(all_available_permsets) if ALL_CHOICE_VALUE in selected_permset_q else sorted([ps for ps in selected_permset_q if ps != ALL_CHOICE_VALUE])
    if not permsets_for_report_columns: click.echo("No permission sets effectively selected."); return
    click.echo(f"\nAuditing fields against {len(permsets_for_report_columns)} PS: {', '.join(permsets_for_report_columns)}")
    permset_xml_cache = {ps_name: load_xml(meta/'permissionsets'/f'{ps_name}{PERMISSIONSET_SUFFIX}')[1] for ps_name in permsets_for_report_columns}
    click.echo("\nIdentifying fields accessible by the selected permission sets...")
    fields_to_include_in_report = []
    field_permission_cache = {}
    all_objects_in_project = list_objects(meta)
    total_eligible_fields_scanned = 0
    for obj_name in all_objects_in_project:
        for fname, ftype in list_fields(meta, obj_name):
            total_eligible_fields_scanned += 1
            full_field_api_name = f"{obj_name}.{fname}"
            for ps_name in permsets_for_report_columns:
                ps_root = permset_xml_cache.get(ps_name)
                if ps_root is None: continue
                cached_perms = field_permission_cache.get((ps_name, full_field_api_name))
                if cached_perms is None:
                    cached_perms = get_effective_field_permissions_from_ps_root(ps_root, obj_name, full_field_api_name)
                    field_permission_cache[(ps_name, full_field_api_name)] = cached_perms
                if cached_perms[0] or cached_perms[1]:
                    fields_to_include_in_report.append((obj_name, fname, ftype))
                    break
            if total_eligible_fields_scanned > 0 and total_eligible_fields_scanned % 500 == 0: click.echo(f"  Scanned {total_eligible_fields_scanned} eligible fields... Found {len(fields_to_include_in_report)} relevant so far.")
    if total_eligible_fields_scanned == 0: click.echo("No eligible fields found to scan."); return
    if not fields_to_include_in_report: click.echo("No fields found accessible by selected permission sets."); return
    fields_to_include_in_report.sort()
    click.echo(f"\nFound {len(fields_to_include_in_report)} fields accessible by at least one selected PS.")
    ts_formatted = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    output_filename = base_dir / f'Accessible_Fields_By_Selected_PS_Report_{ts_formatted}.csv'
    click.echo(f"\nGenerating report: {output_filename}")
    processed_count_for_csv = 0
    try:
        with open(output_filename, 'w', newline='', encoding='utf-8') as cf:
            writer = csv.writer(cf); writer.writerow(['ObjectName', 'FieldName', 'FieldType'] + permsets_for_report_columns)
            for obj_name, field_name, field_type in fields_to_include_in_report:
                full_field_api_name = f"{obj_name}.{field_name}"
                csv_row = [obj_name, field_name, field_type]
                for ps_name in permsets_for_report_columns:
                    cached_perms = field_permission_cache.get((ps_name, full_field_api_name))
                    if cached_perms is None:
                        cached_perms = get_effective_field_permissions_from_ps_root(permset_xml_cache.get(ps_name), obj_name, full_field_api_name)
                        field_permission_cache[(ps_name, full_field_api_name)] = cached_perms
                    r_ps, e_ps = cached_perms
                    csv_row.append(format_access_display(r_ps, e_ps))
                writer.writerow(csv_row); processed_count_for_csv += 1
                if processed_count_for_csv % 100 == 0: click.echo(f"  Written {processed_count_for_csv}/{len(fields_to_include_in_report)} fields to CSV...")
        click.echo(f"\nField Access by PS (Field-Centric) report generated for {len(fields_to_include_in_report)} fields.")
        click.echo(f"Report saved to: {output_filename}")
    except IOError as e: click.echo(click.style(f"\nError generating report: {e}", fg='red'))
    finally:
        if 'output_filename' in locals() and output_filename.exists() and processed_count_for_csv == 0:
             try: output_filename.unlink(); click.echo(f"Cleaned up empty report file: {output_filename}")
             except OSError: pass

def rollback_changes(meta: Path, base_dir: Path):
    click.echo("\n--- Rollback Changes from Backup ---")
    backups_dir = base_dir / 'fs_backups'
    if not backups_dir.is_dir() or not any(backups_dir.iterdir()): click.echo('No backups found.'); return
    choices = sorted([d.name for d in backups_dir.iterdir() if d.is_dir()], reverse=True)
    if not choices: click.echo('No valid backup directories found.'); return
    sel_backup_name = questionary.select('Select backup to restore:', choices=choices).ask()
    if not sel_backup_name: click.echo('Cancelled.'); return
    backup_path = backups_dir / sel_backup_name
    backup_profiles_dir = backup_path / 'profiles'; target_profiles_dir = meta / 'profiles'
    backup_permsets_dir = backup_path / 'permissionsets'; target_permsets_dir = meta / 'permissionsets'
    backup_pkg_file = backup_path / 'package.xml'; target_pkg_file = meta / 'package.xml'
    has_profiles = backup_profiles_dir.is_dir() and any(backup_profiles_dir.glob(f'*{PROFILE_SUFFIX}'))
    has_permsets = backup_permsets_dir.is_dir() and any(backup_permsets_dir.glob(f'*{PERMISSIONSET_SUFFIX}'))
    has_pkg = backup_pkg_file.is_file()
    if not has_profiles and not has_permsets and not has_pkg: click.echo(f"Backup '{sel_backup_name}' contains no items to restore."); return
    confirm_parts = [f"Restore from backup '{sel_backup_name}':"]
    if has_profiles: confirm_parts.append(f"  - Profiles -> {target_profiles_dir}")
    if has_permsets: confirm_parts.append(f"  - Permission Sets -> {target_permsets_dir}")
    if has_pkg: confirm_parts.append(f"  - package.xml -> {target_pkg_file}")
    confirm_parts.append("\nOverwrite current files? (CANNOT BE UNDONE)")
    if not questionary.confirm('\n'.join(confirm_parts), default=False).ask(): click.echo('Rollback cancelled.'); return
    click.echo(f"Starting rollback from: {sel_backup_name}")
    restored_p_count, restored_ps_count, err_count = 0, 0, 0; pkg_restored = False
    try:
        if has_profiles:
            target_profiles_dir.mkdir(parents=True, exist_ok=True)
            for f_to_restore in backup_profiles_dir.glob(f'*{PROFILE_SUFFIX}'): shutil.copy2(f_to_restore, target_profiles_dir / f_to_restore.name); restored_p_count += 1
        if has_permsets:
            target_permsets_dir.mkdir(parents=True, exist_ok=True)
            for f_to_restore in backup_permsets_dir.glob(f'*{PERMISSIONSET_SUFFIX}'): shutil.copy2(f_to_restore, target_permsets_dir / f_to_restore.name); restored_ps_count += 1
        if has_pkg: shutil.copy2(backup_pkg_file, target_pkg_file); pkg_restored = True
    except Exception as e: click.echo(f"Error during rollback: {e}"); err_count += 1
    summary = []
    if restored_p_count > 0: summary.append(f"{restored_p_count} profile(s)")
    if restored_ps_count > 0: summary.append(f"{restored_ps_count} permission set(s)")
    if pkg_restored: summary.append("package.xml")
    if err_count > 0: click.echo(click.style(f"\nRollback finished with {err_count} errors.", fg='red'))
    elif not summary: click.echo(f"\nRollback finished, but no items were actually restored.")
    else: click.echo(click.style(f"\nRollback complete.", fg='green'))
    if summary: click.echo(f"Restored: {', '.join(summary)}.\nRemember to deploy the restored metadata.");

def reverse_lookup_field_access(meta: Path, base_dir: Path):
    click.echo("\n--- Who has access to this field? (Reverse Lookup) ---")
    _, field_selection_map = _select_objects_and_fields_for_report_interactive(meta, "this reverse lookup")
    if not field_selection_map: click.echo("No fields selected for reverse lookup."); return
    selected_fields_for_lookup = [(obj, f_name) for obj, fields in field_selection_map.items() for f_name in fields]
    if not selected_fields_for_lookup: click.echo("No fields effectively selected for lookup."); return
    click.echo(f"\nPerforming reverse lookup for {len(selected_fields_for_lookup)} field(s)...")
    report_data, console_output_data = [], defaultdict(list)
    profile_xml_roots = {p: load_xml(meta/'profiles'/f"{p}{PROFILE_SUFFIX}")[1] for p in list_profiles(meta)}
    permset_xml_roots = {ps: load_xml(meta/'permissionsets'/f"{ps}{PERMISSIONSET_SUFFIX}")[1] for ps in list_permission_sets(meta)}
    field_type_cache = {}
    for obj_name, field_name in selected_fields_for_lookup:
        full_field_api_name = f"{obj_name}.{field_name}"
        field_type = field_type_cache.get(full_field_api_name)
        if field_type is None:
            for fname_obj, ftype_obj in list_fields(meta, obj_name): field_type_cache[f"{obj_name}.{fname_obj}"] = ftype_obj
            field_type = field_type_cache.get(full_field_api_name, "Unknown")
        for p_name, p_root in profile_xml_roots.items():
            if not p_root: continue
            r, e = get_field_permissions_from_profile_root(p_root, full_field_api_name)
            if r or e:
                acc_str = format_access_display(r,e); report_data.append({'Object Name':obj_name, 'Field Name':field_name, 'Field Type':field_type, 'Component Type':'Profile', 'Component Name':p_name, 'Access Level':acc_str, 'Access Via':'Direct FLS'})
                console_output_data[full_field_api_name].append((p_name, "Profile", acc_str, "Direct FLS"))
        for ps_name, ps_root in permset_xml_roots.items():
            if not ps_root: continue
            r_expl, e_expl = get_field_permissions_from_permissionset_root(ps_root, full_field_api_name)
            r_eff, e_eff = get_effective_field_permissions_from_ps_root(ps_root, obj_name, full_field_api_name)
            note = ""
            if r_eff or e_eff:
                if r_expl or e_expl: note = "Direct FLS"
                op_node = ps_root.find(f".//sf:objectPermissions[sf:object='{obj_name}']", NS)
                if op_node:
                    if op_node.findtext('sf:modifyAllRecords',NS)=='true' and ( (r_eff and not r_expl) or (e_eff and not e_expl) ): note = "via ModifyAllRecords" if not note else note + " (+ModifyAllRecords)"
                    elif op_node.findtext('sf:viewAllRecords',NS)=='true' and ( (r_eff and not r_expl) and not e_eff ): note = "via ViewAllRecords" if not note else note + " (+ViewAllRecords)"
                if not note and (r_eff or e_eff): note = "Unknown effective source" # Fallback
                acc_str=format_access_display(r_eff,e_eff); report_data.append({'Object Name':obj_name, 'Field Name':field_name, 'Field Type':field_type, 'Component Type':'PermissionSet', 'Component Name':ps_name, 'Access Level':acc_str, 'Access Via':note})
                console_output_data[full_field_api_name].append((ps_name, "PermissionSet", acc_str, note))
    click.echo(click.style("\n--- Access Report (Console) ---", bold=True))
    if not console_output_data: click.echo("No access found for the selected field(s).")
    else:
        for full_field_name, access_list in sorted(console_output_data.items()):
            click.echo(f"\nField: {click.style(full_field_name, fg='cyan', bold=True)} (Type: {field_type_cache.get(full_field_name, 'Unknown')})")
            for item_name, item_type, access, note in sorted(access_list, key=lambda x: (x[1], x[0])): click.echo(f"  - {item_type:<14} {item_name:<40} Access: {access:<4} ({note})")
    if not report_data: click.echo("\nNo data to write to CSV for reverse lookup."); return
    ts = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    outfile = base_dir / f'Field_Access_Reverse_Lookup_{ts}.csv'
    try:
        with open(outfile, 'w', newline='', encoding='utf-8') as cf:
            writer = csv.DictWriter(cf, fieldnames=['Object Name', 'Field Name', 'Field Type', 'Component Type', 'Component Name', 'Access Level', 'Access Via'])
            writer.writeheader(); writer.writerows(sorted(report_data, key=lambda x: (x['Object Name'], x['Field Name'], x['Component Type'], x['Component Name'])))
        click.echo(f"\nReverse lookup report saved to: {outfile}")
    except IOError as e: click.echo(click.style(f"\nError generating reverse lookup CSV: {e}", fg='red'))

def generate_object_permissions_report(meta: Path, base_dir: Path):
    click.echo("\n--- Generate Object Permissions Report ---")
    all_objs_list = list_objects(meta)
    if not all_objs_list: click.echo("No objects found in project."); return
    obj_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + [questionary.Choice(o) for o in all_objs_list]
    selected_objects_q = questionary.checkbox('Select objects to include in the report:', choices=obj_choices).ask()
    if not selected_objects_q: click.echo("No objects selected for the report."); return
    objects_for_report = all_objs_list if ALL_CHOICE_VALUE in selected_objects_q else sorted([o for o in selected_objects_q if o != ALL_CHOICE_VALUE])
    if not objects_for_report: click.echo("No objects effectively selected for the report."); return
    profiles_for_report = []
    all_profs = list_profiles(meta)
    if all_profs:
        profile_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + [questionary.Choice(p) for p in all_profs]
        sel_profs_q = questionary.checkbox('Select profiles to include (optional):', choices=profile_choices).ask()
        if sel_profs_q: profiles_for_report = all_profs if ALL_CHOICE_VALUE in sel_profs_q else sorted([p for p in sel_profs_q if p != ALL_CHOICE_VALUE])
    permsets_for_report = []
    all_permsets = list_permission_sets(meta)
    if all_permsets:
        permset_choices = [questionary.Choice(ALL_CHOICE_VALUE, value=ALL_CHOICE_VALUE)] + [questionary.Choice(ps) for ps in all_permsets]
        sel_permsets_q = questionary.checkbox('Select permission sets to include (optional):', choices=permset_choices).ask()
        if sel_permsets_q: permsets_for_report = all_permsets if ALL_CHOICE_VALUE in sel_permsets_q else sorted([ps for ps in sel_permsets_q if ps != ALL_CHOICE_VALUE])
    if not profiles_for_report and not permsets_for_report: click.echo("No profiles or permission sets selected for the report."); return
    click.echo("\nGenerating object permissions report...")
    profile_xml_cache = {p_name: load_xml(meta / 'profiles' / f"{p_name}{PROFILE_SUFFIX}")[1] for p_name in profiles_for_report}
    permset_xml_cache = {ps_name: load_xml(meta / 'permissionsets' / f"{ps_name}{PERMISSIONSET_SUFFIX}")[1] for ps_name in permsets_for_report}
    report_data_rows, console_output_lines = [], []
    max_obj_name_len = max(len(o) for o in objects_for_report) if objects_for_report else 20
    all_targets_sorted = sorted(list(set(profiles_for_report) | set(permsets_for_report))) # Unique sorted targets for columns
    target_col_width = max((max(len(t) for t in all_targets_sorted) if all_targets_sorted else 0), 20) + 2
    perms_display_width = len(format_object_perms_display({tag: True for tag in OBJECT_PERM_TAGS})) + 2
    header_console = f"{'Object Name':<{max_obj_name_len + 2}}" + "".join([f"{target_name:<{target_col_width}}" for target_name in all_targets_sorted])
    console_output_lines.append(header_console); console_output_lines.append("-" * len(header_console))
    for obj_name in objects_for_report:
        csv_row_dict = {'ObjectName': obj_name}
        console_row_str_parts = {target_name: " " * target_col_width for target_name in all_targets_sorted} # Pre-fill with spaces
        for p_name in profiles_for_report:
            perms = get_object_permissions_from_xml_root(profile_xml_cache.get(p_name), obj_name)
            perms_str = format_object_perms_display(perms)
            csv_row_dict[p_name] = perms_str
            console_row_str_parts[p_name] = f"{perms_str:<{target_col_width}}"
        for ps_name in permsets_for_report:
            perms = get_object_permissions_from_xml_root(permset_xml_cache.get(ps_name), obj_name)
            perms_str = format_object_perms_display(perms)
            csv_row_dict[ps_name] = perms_str # Dict key uniqueness handles if name in both lists
            console_row_str_parts[ps_name] = f"{perms_str:<{target_col_width}}" # Dict key uniqueness handles if name in both lists
        console_row_str = f"{obj_name:<{max_obj_name_len + 2}}" + "".join(console_row_str_parts[target_name] for target_name in all_targets_sorted)
        report_data_rows.append(csv_row_dict); console_output_lines.append(console_row_str)
    click.echo(click.style("\n--- Object Permissions Report (Console) ---", bold=True))
    for line in console_output_lines: click.echo(line)
    ts_formatted = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    output_filename = base_dir / f'Object_Permissions_Report_{ts_formatted}.csv'
    header_csv = ['ObjectName'] + all_targets_sorted # Use the same sorted unique target list for CSV header
    try:
        with open(output_filename, 'w', newline='', encoding='utf-8') as cf:
            writer = csv.DictWriter(cf, fieldnames=header_csv, extrasaction='ignore')
            writer.writeheader(); writer.writerows(report_data_rows)
        click.echo(f"\nObject permissions report generated for {len(objects_for_report)} object(s). Saved to: {output_filename}")
    except IOError as e: click.echo(click.style(f"\nError generating object permissions report CSV: {e}", fg='red'))


# --- Main CLI ---
@click.command()
@click.option('--project', default='.', help='SFDX project root path.')
@click.option('--metadata', default=None, help='Override metadata folder relative to project root.')
@click.option('--dry-run', is_flag=True, help='Preview bulk changes without modifying files.')
def main(project, metadata, dry_run):
    """Salesforce Field Security & Object Permission Manager."""
    try:
        project_root = Path(project).resolve(strict=True)
        click.echo("")
    except FileNotFoundError:
        click.echo(click.style(f"Error: Project directory not found: {Path(project).resolve()}", fg='red')); sys.exit(1)
    except Exception as e:
        click.echo(click.style(f"Error resolving project path '{project}': {e}", fg='red')); sys.exit(1)

    if dry_run:
        click.echo(click.style("DRY RUN MODE ENABLED", fg='yellow', bold=True))

    try:
        meta_override_path = project_root / metadata if metadata else None
        meta_base = find_metadata_base(project_root, str(meta_override_path) if meta_override_path else None)
        click.echo("")
    except SystemExit: sys.exit(1)
    except Exception as e: click.echo(click.style(f"Critical Error finding metadata base: {e}", fg='red')); sys.exit(1)

    fs_tool_dir = project_root / 'FS Tool Files'
    try:
        fs_tool_dir.mkdir(parents=True, exist_ok=True)
        click.echo("")
    except OSError as e: click.echo(click.style(f"Error creating tool directory '{fs_tool_dir}': {e}.", fg='red'))

    while True:
        main_choice = questionary.select(
            'Choose action:',
            choices=[
                'Generate Field Security Report (FLS)',
                'Modify Field Security',
                'Generate Object Permissions Report',
                'Modify Object Permissions',
                'Who has access to this field? (Reverse Lookup)',
                'Audit Permission Sets (By Perm Set)',
                'Audit Permission Sets (By Field)',
                'Rollback From Backup',
                'Exit'
            ], qmark='>', pointer='->'
        ).ask()
        click.echo("-" * 20)

        if main_choice == 'Generate Field Security Report (FLS)':
            generate_field_security_report(meta_base, fs_tool_dir)
        elif main_choice == 'Modify Field Security':
            bulk_apply_fls(meta_base, fs_tool_dir, dry_run)
        elif main_choice == 'Generate Object Permissions Report':
            generate_object_permissions_report(meta_base, fs_tool_dir)
        elif main_choice == 'Modify Object Permissions':
            modify_object_permissions(meta_base, fs_tool_dir, dry_run)
        elif main_choice == 'Who has access to this field? (Reverse Lookup)':
            reverse_lookup_field_access(meta_base, fs_tool_dir)
        elif main_choice == 'Audit Permission Sets (By Perm Set)':
            inspect_permission_set_access(meta_base, fs_tool_dir)
        elif main_choice == 'Audit Permission Sets (By Field)':
            audit_all_fields_by_selected_permission_sets(meta_base, fs_tool_dir)
        elif main_choice == 'Rollback From Backup':
            if dry_run: click.echo(click.style("DRY RUN: Rollback operation skipped.", fg='yellow'))
            else: rollback_changes(meta_base, fs_tool_dir)
        elif main_choice == 'Exit' or main_choice is None:
            click.echo('Exiting. Goodbye!'); break
        else: click.echo("Invalid choice. Please try again.")
        click.echo("\n" + "="*30 + "\n")

if __name__ == '__main__':

    main()
