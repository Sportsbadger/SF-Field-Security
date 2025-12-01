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
WORKSPACE_INFO_FILENAME = '.workspace_info.json'


class NavigationInterrupt(Exception):
    """Raised when the user requests to navigate back using Ctrl+C."""


def prompt_with_navigation(prompt):
    """Execute a questionary prompt and translate cancellations into navigation."""

    try:
        answer = prompt.ask()
    except KeyboardInterrupt:
        raise NavigationInterrupt() from None

    if answer is None:
        raise NavigationInterrupt()

    return answer


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
    api_version: str
    active_org_name: str
    available_orgs: list['OrgConfig']
    explicit_custom_objects: list[str]


@dataclass
class OrgConfig:
    """Configuration describing a single Salesforce org target."""

    name: str
    target_org_url: str
    persistent_alias: str
    explicit_custom_objects: list[str]


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
                encoding='utf-8',
                errors='replace',
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
            errors='replace',
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

    parser = configparser.ConfigParser()
    parser.read(config_path)

    legacy_explicit_objects_str = parser.get(
        'ToolOptions', 'explicit_custom_objects', fallback=''
    ).strip()
    legacy_explicit_custom_objects = [
        obj.strip()
        for obj in legacy_explicit_objects_str.split(',')
        if obj.strip()
    ]

    org_sections = [name for name in parser.sections() if name.startswith('Org ')]
    available_orgs: list[OrgConfig] = []
    for section in org_sections:
        org_name = section[4:].strip() or 'default'
        org_explicit_objects_str = parser.get(
            section, 'explicit_custom_objects', fallback=''
        ).strip()
        org_explicit_custom_objects = [
            obj.strip()
            for obj in org_explicit_objects_str.split(',')
            if obj.strip()
        ]
        if not org_explicit_custom_objects:
            org_explicit_custom_objects = legacy_explicit_custom_objects
        available_orgs.append(
            OrgConfig(
                name=org_name,
                target_org_url=parser.get(section, 'target_org_url', fallback='').strip(),
                persistent_alias=parser.get(section, 'persistent_alias', fallback='').strip(),
                explicit_custom_objects=org_explicit_custom_objects,
            )
        )

    # Backwards compatibility with the legacy single-org format.
    if not available_orgs and parser.has_section('Salesforce'):
        available_orgs.append(
            OrgConfig(
                name='default',
                target_org_url=parser.get('Salesforce', 'target_org_url', fallback='').strip(),
                persistent_alias=parser.get('Salesforce', 'persistent_alias', fallback='').strip(),
                explicit_custom_objects=legacy_explicit_custom_objects,
            )
        )

    active_org_name = parser.get('SalesforceOrgs', 'active_org', fallback='').strip()
    active_org: OrgConfig | None = None
    if active_org_name:
        active_org = next((org for org in available_orgs if org.name == active_org_name), None)
        if active_org is None:
            available_names = ', '.join(org.name for org in available_orgs) or 'none found'
            raise click.ClickException(
                f"Active org '{active_org_name}' was not found. Available orgs: {available_names}."
            )
    elif len(available_orgs) == 1:
        active_org = available_orgs[0]
    elif len(available_orgs) > 1:
        raise click.ClickException(
            "Multiple org configurations detected. Set 'SalesforceOrgs.active_org' to choose which org is active."
        )

    if active_org is None:
        raise click.ClickException(
            "No Salesforce org configuration found. Run the configuration setup to create config.ini."
        )

    missing_fields: list[str] = []
    if not active_org.target_org_url:
        missing_fields.append(f"Org {active_org.name}.target_org_url")
    if not active_org.persistent_alias:
        missing_fields.append(f"Org {active_org.name}.persistent_alias")
    if missing_fields:
        raise click.ClickException(
            "Missing required configuration values: " + ', '.join(missing_fields)
        )

    return ConfigSettings(
        target_org_url=active_org.target_org_url,
        persistent_alias=active_org.persistent_alias,
        api_version=parser.get('ToolOptions', 'api_version', fallback='60.0').strip(),
        active_org_name=active_org.name,
        available_orgs=available_orgs,
        explicit_custom_objects=active_org.explicit_custom_objects,
    )


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


def read_workspace_info(project_path: Path) -> dict | None:
    """Return recorded workspace metadata when available."""

    info_path = project_path / WORKSPACE_INFO_FILENAME
    if not info_path.is_file():
        return None

    try:
        with info_path.open('r', encoding='utf-8') as info_file:
            return json.load(info_file)
    except (json.JSONDecodeError, OSError):  # pragma: no cover - best effort
        return None


def save_workspace_info(project_path: Path, org_name: str, persistent_alias: str) -> None:
    """Persist workspace metadata for later filtering and display."""

    info_path = project_path / WORKSPACE_INFO_FILENAME
    info = {
        'org_name': org_name,
        'persistent_alias': persistent_alias,
        'last_updated': datetime.datetime.now().isoformat(),
    }
    with info_path.open('w', encoding='utf-8') as info_file:
        json.dump(info, info_file, indent=2)


def _workspace_matches_alias(project_path: Path, persistent_alias: str) -> bool:
    """Determine whether a workspace belongs to the current org alias."""

    info = read_workspace_info(project_path)
    if info and info.get('persistent_alias') == persistent_alias:
        return True

    return persistent_alias in project_path.name


def list_workspaces_for_alias(projects_dir: Path, persistent_alias: str) -> list[Path]:
    """Return existing workspaces that belong to the specified org alias."""

    if not projects_dir.is_dir():
        return []

    matches = [
        path
        for path in projects_dir.iterdir()
        if path.is_dir() and _workspace_matches_alias(path, persistent_alias)
    ]

    return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)


def _prompt_for_org(
    label_default: str,
    url_example: str,
    alias_default: str | None = None,
    current_url: str | None = None,
    explicit_custom_objects: str = '',
) -> OrgConfig:
    """Collect org configuration details interactively."""

    while True:
        org_label = questionary.text(
            "Enter a label for this org (e.g., sandbox, prod):", default=label_default
        ).ask()
        if org_label is None:
            raise click.ClickException("Configuration cancelled.")
        org_label = org_label.strip()
        if org_label:
            break
        click.echo("Org label cannot be empty. Please provide a name.")

    while True:
        url_prompt = f"Login URL for '{org_label}' (e.g., {url_example})"
        if current_url:
            url_prompt += f" [current: {current_url}]"
        org_url = questionary.text(f"{url_prompt}:").ask()
        if org_url is None:
            raise click.ClickException("Configuration cancelled.")
        org_url = org_url.strip() or (current_url or "")
        if org_url:
            break
        click.echo("Login URL cannot be empty. Please provide a value.")

    while True:
        alias = questionary.text(
            f"Persistent alias for '{org_label}' (Salesforce CLI alias used for authentication and workspace names):",
            default=alias_default or org_label,
        ).ask()
        if alias is None:
            raise click.ClickException("Configuration cancelled.")
        alias = alias.strip()
        if alias:
            break
        click.echo("Alias cannot be empty. Please provide a value.")

    explicit_objects_value = (
        questionary.text(
            f"Comma-separated list of explicit custom objects for '{org_label}' (optional):",
            default=explicit_custom_objects,
        ).ask()
        or ''
    ).strip()
    explicit_custom_objects_list = [
        obj.strip()
        for obj in explicit_objects_value.split(',')
        if obj.strip()
    ]

    return OrgConfig(
        name=org_label,
        target_org_url=org_url,
        persistent_alias=alias,
        explicit_custom_objects=explicit_custom_objects_list,
    )


def create_config_interactively(
    config_path: Path,
    existing_orgs: list[OrgConfig] | None = None,
    active_org_name: str | None = None,
    api_version: str = '60.0',
) -> None:
    """Guide the user through creating a config.ini file."""

    click.echo(
        click.style(
            "\nStarting configuration setup for config.ini.",
            fg='cyan',
            bold=True,
        )
    )
    click.echo(
        "The org label is a friendly name for menus, while the persistent alias is the Salesforce CLI alias reused for login and workspace naming."
    )

    orgs: list[OrgConfig] = []
    url_default = 'https://login.salesforce.com'
    if existing_orgs:
        click.echo("Existing org entries found. Update the values or press Enter to keep them.")
        for org in existing_orgs:
            orgs.append(
                _prompt_for_org(
                    org.name,
                    url_default,
                    alias_default=org.persistent_alias or org.name,
                    current_url=org.target_org_url,
                    explicit_custom_objects=','.join(org.explicit_custom_objects),
                )
            )
    else:
        orgs.append(_prompt_for_org('sandbox', url_default))

    while questionary.confirm(
        "Would you like to add another org configuration?", default=False
    ).ask():
        orgs.append(_prompt_for_org(f"org{len(orgs) + 1}", url_default))

    if len(orgs) == 1:
        active_org_name = orgs[0].name
    else:
        active_org_name = questionary.select(
            "Which org should be active? (Inactive orgs will be saved for later use)",
            choices=[org.name for org in orgs],
            default=active_org_name or orgs[0].name,
        ).ask()
        if active_org_name is None:
            raise click.ClickException("Configuration cancelled.")

    api_version = (
        questionary.text("API version to use:", default=api_version or '60.0').ask()
        or '60.0'
    ).strip()

    config_lines: list[str] = [
        "# Salesforce Field Security configuration.",
        "# Define multiple [Org <name>] sections. Comment out the ones you do not want to use and set 'SalesforceOrgs.active_org' to the active entry.",
        "",
        "[SalesforceOrgs]",
        f"active_org = {active_org_name}",
        "",
    ]

    for org in orgs:
        config_lines.append(f"[Org {org.name}]")
        config_lines.append(f"target_org_url = {org.target_org_url}")
        config_lines.append(f"persistent_alias = {org.persistent_alias}")
        config_lines.append(
            f"explicit_custom_objects = {','.join(org.explicit_custom_objects)}"
        )
        config_lines.append("")

    config_lines.extend(
        [
            "[ToolOptions]",
            f"api_version = {api_version}",
            "",
        ]
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('\n'.join(config_lines), encoding='utf-8')

    click.echo(click.style(f"Configuration saved to {config_path}.", fg='green'))
    click.echo(
        "Comment out unused org blocks if you only want one available, and update 'active_org' to switch between them."
    )


def ensure_config(config_path: Path, projects_dir: Path) -> None:
    """Create config.ini interactively on first run when needed."""

    config_exists = config_path.exists()
    existing_settings: ConfigSettings | None = None
    if config_exists:
        try:
            existing_settings = read_config(config_path)
        except click.ClickException:
            existing_settings = None

    workspace_exists = projects_dir.is_dir() and any(projects_dir.iterdir())

    if config_exists and workspace_exists:
        return

    reason: str
    if not config_exists and not workspace_exists:
        reason = "No configuration or project workspace detected."
    elif not config_exists:
        reason = "Configuration file not found."
    else:
        reason = "No project workspace found."

    click.echo(click.style(f"{reason} Starting first-run setup...", fg='yellow'))
    create_config_interactively(
        config_path,
        existing_orgs=existing_settings.available_orgs if existing_settings else None,
        active_org_name=existing_settings.active_org_name if existing_settings else None,
        api_version=existing_settings.api_version if existing_settings else '60.0',
    )


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
            '--json',
        ],
        capture_output=True,
        check=False,
    )
    if not retrieve_result.success:
        click.echo(click.style("Metadata retrieval failed.", fg='red'))
        if retrieve_result.stdout:
            click.echo(retrieve_result.stdout)
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
        capture_output=True,
        check=False,
    )
    if not convert_result.success:
        click.echo(click.style("Metadata conversion failed!", fg='red'))
        if convert_result.stdout:
            click.echo(convert_result.stdout)
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
) -> tuple[Path, bool]:
    """Select or create the project workspace directory to operate against."""

    existing_projects = list_workspaces_for_alias(projects_dir, persistent_alias)

    workspace_choices: list[questionary.Choice] = [
        questionary.Choice(title=p.name, value=p) for p in existing_projects
    ]
    workspace_choices.append(questionary.Choice(create_choice_label, 'create_new'))
    workspace_choices.append(questionary.Choice("Return to previous menu", None))

    selection = prompt_with_navigation(
        questionary.select(action_prompt, choices=workspace_choices)
    )

    if selection == 'create_new':
        use_custom_name = prompt_with_navigation(
            questionary.confirm(
                "Would you like to provide a custom workspace name?", default=False
            )
        )
        project_dir_name: str
        if use_custom_name:
            while True:
                custom_name = prompt_with_navigation(
                    questionary.text("Enter a workspace name:", default=persistent_alias)
                )

                project_dir_name = custom_name.strip()
                if not project_dir_name:
                    click.echo(
                        "Workspace name cannot be empty. Please enter a valid name."
                    )
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
        click.echo(f"\nCreated new project directory: {project_path.name}")
        return project_path, True

    project_path: Path = selection
    click.echo(f"\nSelected existing project: {project_path.name}")
    refresh_metadata = True
    if allow_use_without_refresh:
        proceed_choice = prompt_with_navigation(
            questionary.select(
                "How would you like to proceed with this existing project?",
                choices=[
                    "Refresh metadata (replace local files)",
                    "Use existing project without refreshing",
                ],
            )
        )
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

    return project_path, refresh_metadata

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
    click.echo("=" * 50)

