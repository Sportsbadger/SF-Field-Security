"""Utility helpers for Salesforce project setup and tooling workflows."""

import configparser
import datetime
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

import click
import questionary

SF_NAMESPACE_URI = 'http://soap.sforce.com/2006/04/metadata'


@dataclass
class CommandResult:
    """Outcome of executing a subprocess command."""

    success: bool
    returncode: int | None
    stdout: str | None
    duration_seconds: float


@dataclass
class ConfigSettings:
    """Validated configuration values for project and org settings."""

    target_org_url: str
    persistent_alias: str
    explicit_custom_objects: list[str]
    api_version: str


@dataclass
class MetadataRetrievalPlan:
    """Paths used when retrieving and converting metadata."""

    project_path: Path
    manifest_path: Path
    temp_retrieve_dir: Path
    mdapi_source_path: Path
    force_app_path: Path


def run_command(
    command: list[str],
    cwd: Path = None,
    capture_output: bool = False,
    check: bool = True,
) -> CommandResult:
    """Run a shell command with structured logging and status reporting."""

    command_str = subprocess.list2cmdline(command)
    start = datetime.datetime.now()
    if not capture_output:
        click.echo(
            click.style(
                f"\n[{start:%H:%M:%S}] > Executing (in shell): {command_str}",
                fg='yellow',
            )
        )

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
            duration = (datetime.datetime.now() - start).total_seconds()
            success = result.returncode == 0
            if not success:
                click.echo(
                    click.style(
                        f"✗ Command returned non-zero exit code {result.returncode}.",
                        fg='red',
                    )
                )
            return CommandResult(success, result.returncode, result.stdout, duration)

        process = subprocess.Popen(
            command_str,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            cwd=cwd,
            shell=True,
        )
        stdout_lines: list[str] = []
        for line in iter(process.stdout.readline, ''):
            # Some external tools use carriage returns to redraw progress lines, which can
            # create a flashing effect in the console. Strip carriage returns so output is
            # appended normally without screen blanks.
            sanitized_line = line.replace('\r', '')
            stdout_lines.append(sanitized_line)
            print(sanitized_line, end='')
        process.wait()
        duration = (datetime.datetime.now() - start).total_seconds()
        if process.returncode != 0 and check:
            raise subprocess.CalledProcessError(process.returncode, command)

        success = process.returncode == 0
        if not capture_output:
            click.echo(
                click.style(
                    f"✓ Command successful. (took {duration:.2f}s)", fg='green'
                )
                if success
                else click.style(
                    f"✗ Command returned code {process.returncode} (took {duration:.2f}s)",
                    fg='red',
                )
            )
        return CommandResult(success, process.returncode, ''.join(stdout_lines), duration)

    except subprocess.CalledProcessError as e:
        duration = (datetime.datetime.now() - start).total_seconds()
        if not capture_output:
            click.echo(
                click.style(
                    f"✗ Command failed after {duration:.2f}s (exit {e.returncode}).",
                    fg='red',
                )
            )
        return CommandResult(False, e.returncode, e.stdout, duration)
    except Exception as e:  # pragma: no cover - defensive
        duration = (datetime.datetime.now() - start).total_seconds()
        if not capture_output:
            click.echo(
                click.style(
                    f"✗ An unexpected error occurred after {duration:.2f}s: {e}",
                    fg='red',
                )
            )
        return CommandResult(False, None, None, duration)


def read_config(config_path: Path) -> ConfigSettings:
    """Read INI configuration values used throughout the tool suite."""

    config = configparser.ConfigParser()
    config.read(config_path)
    explicit_objects_str = config.get(
        'ToolOptions', 'explicit_custom_objects', fallback=''
    ).strip()
    settings = ConfigSettings(
        target_org_url=config.get('Salesforce', 'target_org_url', fallback='').strip(),
        persistent_alias=config.get('Salesforce', 'persistent_alias', fallback='').strip(),
        explicit_custom_objects=[
            obj.strip() for obj in explicit_objects_str.split(',') if obj.strip()
        ],
        api_version=config.get('ToolOptions', 'api_version', fallback='60.0').strip(),
    )

    missing_fields: list[str] = []
    if not settings.target_org_url:
        missing_fields.append('Salesforce.target_org_url')
    if not settings.persistent_alias:
        missing_fields.append('Salesforce.persistent_alias')
    if missing_fields:
        raise click.ClickException(
            "Missing required configuration values: " + ', '.join(missing_fields)
        )

    return settings


def check_auth(alias: str, announce: bool = True) -> bool:
    """Return True when the provided alias has an active Salesforce session."""
    if announce:
        click.echo(f"Checking for existing authentication for alias: '{alias}'...")

    try:
        result = run_command(['sf', 'org', 'list', '--json'], capture_output=True, check=False)
        output = result.stdout or ''
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
    except json.JSONDecodeError as exc:
        click.echo(
            click.style(
                f"Unable to parse Salesforce org list output: {exc}", fg='red'
            )
        )
    except Exception as exc:  # pragma: no cover - defensive
        click.echo(
            click.style(
                f"Unexpected error while checking authentication: {exc}", fg='red'
            )
        )

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


def build_metadata_plan(project_path: Path) -> MetadataRetrievalPlan:
    """Construct a plan containing all metadata-related directories for a project."""

    return MetadataRetrievalPlan(
        project_path=project_path,
        manifest_path=project_path / 'package.xml',
        temp_retrieve_dir=project_path / 'temp_mdapi_retrieve',
        mdapi_source_path=project_path / 'mdapi_source',
        force_app_path=project_path / 'force-app',
    )


def retrieve_and_convert_metadata(
    plan: MetadataRetrievalPlan,
    api_version: str,
    explicit_custom_objects: list[str],
    target_org_alias: str,
) -> bool:
    """Download metadata and convert it into source format based on a plan."""

    create_sfdx_project_json(plan.project_path, api_version)
    generate_download_manifest(plan.manifest_path, api_version, explicit_custom_objects)

    retrieve_result = run_command(
        [
            'sf',
            'project',
            'retrieve',
            'start',
            '--manifest',
            str(plan.manifest_path),
            '--target-org',
            target_org_alias,
            '--target-metadata-dir',
            str(plan.temp_retrieve_dir),
        ]
    )
    if not retrieve_result.success:
        return False

    zip_path = plan.temp_retrieve_dir / 'unpackaged.zip'
    if zip_path.exists():
        click.echo("Unzipping downloaded MDAPI metadata...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(plan.mdapi_source_path)
        click.echo(click.style("✓ Metadata unzipped.", fg='green'))
    else:
        click.echo(click.style("Error: Could not find 'unpackaged.zip'.", fg='red'))
        return False

    click.echo("\nConverting metadata from MDAPI format to Source format...")
    convert_result = run_command(
        [
            'sf',
            'project',
            'convert',
            'mdapi',
            '--root-dir',
            str(plan.mdapi_source_path),
            '--output-dir',
            str(plan.force_app_path),
        ],
        cwd=plan.project_path,
    )
    if not convert_result.success:
        click.echo(click.style("Metadata conversion failed!", fg='red'))
        return False

    cleanup_paths = [plan.temp_retrieve_dir, plan.mdapi_source_path]
    for path in cleanup_paths:
        shutil.rmtree(path, ignore_errors=True)

    click.echo(click.style("✓ Metadata successfully converted.", fg='green'))
    return True


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

    use_custom_name = questionary.confirm(
        "Would you like to provide a custom workspace name?", default=False
    ).ask()
    project_dir_name: str
    if use_custom_name:
        while True:
            custom_name = questionary.text(
                "Enter a workspace name:", default=persistent_alias
            ).ask()
            if custom_name is None:
                click.echo("Operation cancelled.")
                sys.exit(0)

            project_dir_name = custom_name.strip()
            if not project_dir_name:
                click.echo("Workspace name cannot be empty. Please enter a valid name.")
                continue

            candidate_path = projects_dir / project_dir_name
            if candidate_path.exists():
                click.echo(
                    click.style(
                        f"A workspace named '{project_dir_name}' already exists. Please choose a different name.",
                        fg='yellow',
                    )
                )
                continue

            break
    else:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        project_dir_name = f"{ts}_{persistent_alias}"

    project_path = projects_dir / project_dir_name
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
        click.echo(
            click.style(
                f"{project_path.name} has been created. Now run the Security Tool.",
                bold=True,
                fg='green',
            )
        )
        click.echo(f"1. Change into the project directory: cd \"{project_path}\"")
        click.echo("2. Run the tool: python ../fs_tool_v151.py")
    click.echo("=" * 50)

