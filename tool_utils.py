"""Utility helpers for Salesforce project setup and tooling workflows."""

import configparser
import datetime
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import click
import questionary

SF_NAMESPACE_URI = 'http://soap.sforce.com/2006/04/metadata'


def run_command(command: list[str], cwd: Path = None, capture_output: bool = False, check: bool = True):
    """Run a shell command, streaming output unless capture_output is True."""
    command_str = subprocess.list2cmdline(command)
    if not capture_output:
        click.echo(click.style(f"\n> Executing (in shell): {command_str}", fg='yellow'))

    try:
        if capture_output:
            result = subprocess.run(
                command_str,
                capture_output=True,
                text=True,
                shell=True,
                check=check,
                cwd=cwd,
            )
            return result.stdout

        process = subprocess.Popen(
            command_str,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            cwd=cwd,
            shell=True,
        )
        for line in iter(process.stdout.readline, ''):
            print(line, end='')
        process.wait()
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, command)

        if not capture_output:
            click.echo(click.style("✓ Command successful.", fg='green'))
        return True

    except subprocess.CalledProcessError as e:
        if not capture_output:
            click.echo(click.style("✗ Command failed.", fg='red'))
        return e.stdout if capture_output else False
    except Exception as e:
        if not capture_output:
            click.echo(click.style(f"✗ An unexpected error occurred: {e}", fg='red'))
        return False


def read_config(config_path: Path) -> dict:
    """Read INI configuration values used throughout the tool suite."""
    config = configparser.ConfigParser()
    config.read(config_path)
    explicit_objects_str = config.get(
        'ToolOptions', 'explicit_custom_objects', fallback=''
    ).strip()
    return {
        'target_org_url': config.get('Salesforce', 'target_org_url', fallback=''),
        'persistent_alias': config.get('Salesforce', 'persistent_alias', fallback=''),
        'explicit_custom_objects': [obj.strip() for obj in explicit_objects_str.split(',') if obj.strip()],
        'api_version': config.get('ToolOptions', 'api_version', fallback='60.0'),
    }


def check_auth(alias: str, announce: bool = True) -> bool:
    """Return True when the provided alias has an active Salesforce session."""
    if announce:
        click.echo(f"Checking for existing authentication for alias: '{alias}'...")

    try:
        output = run_command(['sf', 'org', 'list', '--json'], capture_output=True, check=False)
        if not output:
            return False

        org_list = json.loads(output)
        all_orgs = (
            org_list.get('result', {}).get('nonScratchOrgs', [])
            + org_list.get('result', {}).get('scratchOrgs', [])
        )
        for org in all_orgs:
            aliases = []
            alias_value = org.get('alias')
            if alias_value:
                aliases.append(alias_value)
            aliases.extend(org.get('aliases', []))
            if alias in aliases or org.get('username') == alias:
                if announce:
                    click.echo(click.style("✓ Found active session.", fg='green'))
                return True
    except (json.JSONDecodeError, Exception):
        pass

    if announce:
        click.echo(click.style("No active session found. A new login will be required.", fg='yellow'))
    return False


def generate_download_manifest(manifest_path: Path, api_version: str, explicit_objects: list[str]):
    """Create a package.xml manifest for the requested metadata components."""
    package = ET.Element('Package', xmlns=SF_NAMESPACE_URI)
    ET.register_namespace('', SF_NAMESPACE_URI)
    types = {'Profile': '*', 'PermissionSet': '*', 'CustomObject': '*'}
    for name, members in types.items():
        types_elem = ET.SubElement(package, 'types')
        ET.SubElement(types_elem, 'members').text = members
        ET.SubElement(types_elem, 'name').text = name
    if explicit_objects:
        types_explicit_objects = ET.SubElement(package, 'types')
        for obj in sorted(explicit_objects):
            ET.SubElement(types_explicit_objects, 'members').text = obj
        ET.SubElement(types_explicit_objects, 'name').text = 'CustomObject'
    ET.SubElement(package, 'version').text = api_version
    tree = ET.ElementTree(package)
    if hasattr(ET, 'indent'):
        ET.indent(tree, space="    ")
    tree.write(manifest_path, encoding='UTF-8', xml_declaration=True)


def create_sfdx_project_json(project_path: Path, api_version: str):
    """Write the sfdx-project.json file configured for the provided API version."""
    project_def = {
        "packageDirectories": [{"path": "force-app", "default": True}],
        "name": "SecurityToolProject",
        "namespace": "",
        "sfdcLoginUrl": "https://login.salesforce.com",
        "sourceApiVersion": api_version,
    }
    with open(project_path / 'sfdx-project.json', 'w', encoding='utf-8') as f:
        json.dump(project_def, f, indent=4)


def choose_project_workspace(
    projects_dir: Path,
    persistent_alias: str,
    action_prompt: str,
    create_choice_label: str,
    update_choice_label: str,
    update_warning: str,
    post_delete_message: str | None = None,
    allow_use_without_refresh: bool = False,
) -> tuple[Path, str, bool]:
    """Select or create the project workspace directory to operate against."""
    existing_projects: list[Path] = []
    if projects_dir.is_dir():
        existing_projects = sorted(
            [p for p in projects_dir.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    menu_choices = [create_choice_label]
    if existing_projects:
        menu_choices.append(update_choice_label)

    action = questionary.select(action_prompt, choices=menu_choices).ask()
    if action is None:
        click.echo("Operation cancelled.")
        sys.exit(0)

    if action == update_choice_label:
        project_choices = [p.name for p in existing_projects]
        chosen_project_name = questionary.select(
            "Which project to update?"
            if action_prompt.endswith('workspace')
            else "Which project workspace would you like to update?",
            choices=project_choices,
        ).ask()
        if chosen_project_name is None:
            click.echo("Operation cancelled.")
            sys.exit(0)

        project_path = projects_dir / chosen_project_name
        click.echo(f"\nSelected existing project: {project_path}")
        refresh_metadata = True
        if allow_use_without_refresh:
            proceed_choice = questionary.select(
                "How would you like to proceed with this existing project?",
                choices=[
                    "Refresh metadata (replace local files)",
                    "Use existing project without refreshing",
                ],
            ).ask()
            if proceed_choice is None:
                click.echo("Operation cancelled.")
                sys.exit(0)
            refresh_metadata = proceed_choice.startswith("Refresh metadata")

        if refresh_metadata:
            click.echo(click.style(update_warning, fg='yellow'))
            force_app_path_to_delete = project_path / 'force-app'
            if force_app_path_to_delete.exists():
                shutil.rmtree(force_app_path_to_delete)
                if post_delete_message:
                    click.echo(post_delete_message)
        else:
            click.echo("Using existing project without refreshing metadata.")

        return project_path, action, refresh_metadata

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    project_path = projects_dir / f"{ts}_{persistent_alias}"
    project_path.mkdir(parents=True, exist_ok=True)
    click.echo(f"\nCreated new project directory at: {project_path}")
    return project_path, action, True


def print_post_setup_instructions(project_path: Path, launching_tool: bool):
    """Display next steps after setup or tool launch completes."""
    click.echo("\n" + "=" * 50)
    if launching_tool:
        click.echo(click.style("Setup complete. Launching the security tool...", bold=True, fg='green'))
        click.echo(f"Working on project: {project_path.name}")
    else:
        click.echo(click.style("Setup Complete! Now, run the security tool.", bold=True, fg='green'))
        click.echo(f"1. Change into the project directory: cd \"{project_path}\"")
        click.echo("2. Run the tool: python ../fs_tool_v151.py")
    click.echo("=" * 50)

