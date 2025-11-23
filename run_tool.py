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

if __name__ == '__main__':
    click.echo(click.style("=== Salesforce Security Tool Launcher ===", bold=True, fg='cyan'))

    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    org_url = config.target_org_url
    persistent_alias = config.persistent_alias

    projects_dir = script_dir / 'projects'
    project_path, _action, refresh_metadata = choose_project_workspace(
        projects_dir,
        persistent_alias,
        "Choose an action:",
        "Create a new project workspace",
        "Update an existing project workspace",
        "Refreshing metadata... This will replace the 'force-app' folder.",
        allow_use_without_refresh=True,
    )

    if refresh_metadata:
        if not check_auth(persistent_alias):
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
            if not login_result.success:
                sys.exit(1)

        metadata_plan = build_metadata_plan(project_path)
        if not retrieve_and_convert_metadata(
            metadata_plan,
            config.api_version,
            config.explicit_custom_objects,
            persistent_alias,
        ):
            sys.exit(1)
    else:
        click.echo(click.style("Skipping metadata refresh. Using existing project files.", fg='green'))

    print_post_setup_instructions(project_path, launching_tool=True)

    tool_script_path = script_dir / 'fs_tool_v151.py'
    if not tool_script_path.exists():
        click.echo(
            click.style(
                f"Error: The security tool script '{tool_script_path.name}' was not found in this directory.",
                fg='red',
            )
        )
        sys.exit(1)

    subprocess.run([sys.executable, str(tool_script_path), '--project', str(project_path)], check=False)

    click.echo("\n" + "=" * 50)
    click.echo(click.style("Security tool session finished.", bold=True))

    if questionary.confirm("Do you want to run the deployment script now?", default=False).ask():
        deploy_script_path = script_dir / 'deploy_changes.py'
        if not deploy_script_path.exists():
            click.echo(
                click.style(
                    f"Error: The deployment script '{deploy_script_path.name}' was not found.",
                    fg='red',
                )
            )
        else:
            click.echo(click.style("\nLaunching deployment script...", fg='cyan'))
            subprocess.run([sys.executable, str(deploy_script_path)], check=False)
    else:
        click.echo("\nDeployment skipped.")
        click.echo("To deploy your changes later, run:")
        click.echo("python deploy_changes.py")

    click.echo("\n" + "=" * 50)
    click.echo("Tool run complete.")
    click.echo("=" * 50)
