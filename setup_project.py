"""Initial project setup and metadata download workflow."""

from pathlib import Path
import sys

import click

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
    click.echo(click.style("=== Step 1: Project Setup and Metadata Download ===", bold=True, fg='cyan'))

    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    org_url = config.target_org_url
    persistent_alias = config.persistent_alias

    projects_dir = script_dir / 'projects'
    project_path, _action, _refresh_metadata = choose_project_workspace(
        projects_dir,
        persistent_alias,
        "Choose an action for your project workspace:",
        "Create a new project workspace",
        "Update an existing project workspace",
        "Preparing to refresh metadata. This will delete the existing 'force-app' folder.",
        "Removed old 'force-app' directory.",
    )

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

    print_post_setup_instructions(project_path, launching_tool=False)
