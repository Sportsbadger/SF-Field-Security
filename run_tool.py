"""Command-line entry point for running the Salesforce security tool."""

from pathlib import Path
import configparser
import subprocess
import sys

import click
import questionary

from tool_utils import (
    build_metadata_plan,
    check_auth,
    choose_project_workspace,
    ConfigSettings,
    ensure_config,
    list_workspaces_for_alias,
    NavigationInterrupt,
    print_post_setup_instructions,
    prompt_with_navigation,
    read_config,
    retrieve_and_convert_metadata,
    run_command,
    save_workspace_info,
)


def ensure_authenticated(org_url: str, persistent_alias: str) -> bool:
    """Authenticate to the org when no valid session exists."""

    if check_auth(persistent_alias):
        return True

    click.echo(
        click.style(
            "\nAction Required: A browser window will open for authentication.",
            bold=True,
        )
    )
    login_result = run_command(
        [
            'sf',
            'org',
            'login',
            'web',
            '--instance-url',
            org_url,
            '--alias',
            persistent_alias,
        ]
    )
    return login_result.success


def switch_active_org(config_path: Path, config) -> ConfigSettings:
    """Allow the user to choose a different active org when multiple exist."""

    if len(config.available_orgs) < 2:
        click.echo("Only one org is configured; no other orgs to switch to.")
        return config

    org_choices = [
        questionary.Choice(title=f"{org.name} ({org.persistent_alias})", value=org.name)
        for org in config.available_orgs
    ]

    selection = prompt_with_navigation(
        questionary.select(
            "Select the org you want to activate:",
            choices=org_choices,
            default=config.active_org_name,
        )
    )
    if selection == config.active_org_name:
        click.echo(f"'{selection}' is already the active org.")
        return config

    parser = configparser.ConfigParser()
    parser.read(config_path)
    if not parser.has_section('SalesforceOrgs'):
        parser.add_section('SalesforceOrgs')
    parser.set('SalesforceOrgs', 'active_org', selection)

    with config_path.open('w', encoding='utf-8') as config_file:
        parser.write(config_file)

    click.echo(click.style(f"Active org set to '{selection}'.", fg='green'))
    return read_config(config_path)


def select_or_create_workspace(script_dir: Path, org_url: str, config) -> None:
    """Select an existing workspace for the org or create a new one."""

    projects_dir = script_dir / 'projects'

    project_path, refresh_metadata = choose_project_workspace(
        projects_dir,
        config.persistent_alias,
        f"Select a workspace for org '{config.active_org_name}' ({config.persistent_alias}):",
        "Create a new project workspace",
        "Use an existing project workspace",
        "Preparing to refresh metadata. This will delete the existing 'force-app' folder.",
        "Removed old 'force-app' directory.",
        allow_use_without_refresh=True,
    )

    if refresh_metadata:
        if not ensure_authenticated(org_url, config.persistent_alias):
            sys.exit(1)

        metadata_plan = build_metadata_plan(project_path)
        if not retrieve_and_convert_metadata(
            metadata_plan,
            config.api_version,
            config.explicit_custom_objects,
            config.persistent_alias,
        ):
            sys.exit(1)

        save_workspace_info(project_path, config.active_org_name, config.persistent_alias)
        print_post_setup_instructions(project_path, launching_tool=False)
        return

    save_workspace_info(project_path, config.active_org_name, config.persistent_alias)
    click.echo("\nWorkspace ready without refreshing metadata.")
    click.echo(f"Using existing project: {project_path}")


def run_security_tool(script_dir: Path, org_url: str, config) -> None:
    """Launch the field security tool for a selected project."""

    projects_dir = script_dir / 'projects'
    existing_projects = list_workspaces_for_alias(projects_dir, config.persistent_alias)

    if not existing_projects:
        click.echo(
            click.style(
                "No project workspaces found. Please select or create a workspace first.",
                fg='yellow',
            )
        )
        return

    project_path = existing_projects[0]
    click.echo(click.style("Using most recently updated workspace:", fg='cyan', bold=True))
    click.echo(f"  {project_path}")

    proceed = prompt_with_navigation(
        questionary.confirm(
            "Proceed with this workspace? (Choose 'No' to return to the main menu)",
            default=True,
        )
    )

    if not proceed:
        workspace_choices = [
            questionary.Choice(title=p.name, value=p) for p in existing_projects
        ]
        workspace_choices.append(questionary.Choice("Return to main menu", None))

        project_path = prompt_with_navigation(
            questionary.select("Select a workspace to use:", choices=workspace_choices)
        )

    save_workspace_info(project_path, config.active_org_name, config.persistent_alias)
    print_post_setup_instructions(project_path, launching_tool=True)

    tool_script_path = script_dir / 'fs_tool_v151.py'
    if not tool_script_path.exists():
        click.echo(
            click.style(
                f"Error: The security tool script '{tool_script_path.name}' was not found in this directory.",
                fg='red',
            )
        )
        return

    subprocess.run([sys.executable, str(tool_script_path), '--project', str(project_path)], check=False)

    click.echo("\n" + "=" * 50)
    click.echo(click.style("Security tool session finished.", bold=True))


def deploy_changes(script_dir: Path) -> None:
    """Launch deployment workflow when available."""

    deploy_script_path = script_dir / 'deploy_changes.py'
    if not deploy_script_path.exists():
        click.echo(
            click.style(
                f"Error: The deployment script '{deploy_script_path.name}' was not found.",
                fg='red',
            )
        )
        return

    click.echo(click.style("\nLaunching deployment script...", fg='cyan'))
    subprocess.run([sys.executable, str(deploy_script_path)], check=False)


if __name__ == '__main__':
    click.echo(click.style("=== Salesforce Security Tool Launcher ===", bold=True, fg='cyan'))

    script_dir = Path(__file__).parent
    config_path = script_dir / 'config.ini'
    projects_dir = script_dir / 'projects'
    ensure_config(config_path, projects_dir)
    config = read_config(config_path)

    active_org_display = f"{config.active_org_name} ({config.persistent_alias})"
    click.echo(click.style(f"Active org: {active_org_display}", fg='cyan'))
    org_workspaces = list_workspaces_for_alias(projects_dir, config.persistent_alias)
    if org_workspaces:
        click.echo(
            click.style(
                f"Most recent workspace for this org: {org_workspaces[0].name}",
                fg='cyan',
            )
        )
    else:
        click.echo(click.style("No workspaces found for this org yet.", fg='yellow'))

    while True:
        menu_choices = ["Select or Create Workspace"]
        if len(config.available_orgs) > 1:
            menu_choices.append("Switch Active Org")
        menu_choices.extend(
            ["Run the File Security Tool", "Deploy Changes", "Exit"]
        )

        try:
            selection = prompt_with_navigation(
                questionary.select(
                    "Choose an option:",
                    choices=menu_choices,
                )
            )
        except NavigationInterrupt:
            click.echo("Goodbye!")
            break

        if selection == "Exit":
            click.echo("Goodbye!")
            break

        if selection == "Select or Create Workspace":
            try:
                select_or_create_workspace(script_dir, config.target_org_url, config)
            except NavigationInterrupt:
                click.echo("\nReturning to the main menu...\n")
                continue
            click.echo("\nReturning to the main menu...\n")
            continue

        if selection == "Run the File Security Tool":
            try:
                run_security_tool(script_dir, config.target_org_url, config)
            except NavigationInterrupt:
                click.echo("\nReturning to the main menu...\n")
                continue
            click.echo("\nReturning to the main menu...\n")
            continue

        if selection == "Deploy Changes":
            deploy_changes(script_dir)
            click.echo("\nReturning to the main menu...\n")
            continue

        if selection == "Switch Active Org":
            try:
                config = switch_active_org(config_path, config)
            except NavigationInterrupt:
                click.echo("\nReturning to the main menu...\n")
                continue
            click.echo("\nReturning to the main menu...\n")

