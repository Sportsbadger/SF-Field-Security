"""Command-line entry point for running the Salesforce security tool."""

from pathlib import Path
import subprocess
import sys

import click
import questionary

from tool_utils import (
    build_metadata_plan,
    check_auth,
    choose_project_workspace,
    print_post_setup_instructions,
    read_config,
    retrieve_and_convert_metadata,
    run_command,
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


def create_or_update_workspace(script_dir: Path, org_url: str, config) -> None:
    """Download or refresh project metadata for a workspace."""

    projects_dir = script_dir / 'projects'
    project_path, _action, _refresh_metadata = choose_project_workspace(
        projects_dir,
        config.persistent_alias,
        "Choose an action for your project workspace:",
        "Create a new project workspace",
        "Update an existing project workspace",
        "Preparing to refresh metadata. This will delete the existing 'force-app' folder.",
        "Removed old 'force-app' directory.",
    )

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

    print_post_setup_instructions(project_path, launching_tool=False)


def run_security_tool(script_dir: Path, org_url: str, config) -> None:
    """Launch the field security tool for a selected project."""

    projects_dir = script_dir / 'projects'
    existing_projects: list[Path] = []
    if projects_dir.is_dir():
        existing_projects = sorted(
            [p for p in projects_dir.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    if not existing_projects:
        click.echo(
            click.style(
                "No project workspaces found. Please create or update a workspace first.",
                fg='yellow',
            )
        )
        return

    project_path = existing_projects[0]
    click.echo(click.style("Using most recently updated workspace:", fg='cyan', bold=True))
    click.echo(f"  {project_path}")

    proceed = questionary.confirm(
        "Proceed with this workspace? (Choose 'No' to return to the main menu)",
        default=True,
    ).ask()

    if not proceed:
        workspace_choices = [
            questionary.Choice(title=p.name, value=p) for p in existing_projects
        ]
        workspace_choices.append(questionary.Choice("Return to main menu", None))

        selected_workspace = questionary.select(
            "Select a workspace to use:", choices=workspace_choices
        ).ask()

        if selected_workspace is None:
            click.echo("Returning to the main menu without launching the tool.")
            return

        project_path = selected_workspace

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
    config = read_config(script_dir / 'config.ini')
    org_url = config.target_org_url

    while True:
        selection = questionary.select(
            "Choose an option:",
            choices=[
                "Create or Update Workspace",
                "Run the File Security Tool",
                "Deploy Changes",
                "Exit",
            ],
        ).ask()

        if selection is None or selection == "Exit":
            click.echo("Goodbye!")
            break

        if selection == "Create or Update Workspace":
            create_or_update_workspace(script_dir, org_url, config)
            click.echo("\nReturning to the main menu...\n")
            continue

        if selection == "Run the File Security Tool":
            run_security_tool(script_dir, org_url, config)
            click.echo("\nReturning to the main menu...\n")
            continue

        if selection == "Deploy Changes":
            deploy_changes(script_dir)
            click.echo("\nReturning to the main menu...\n")

