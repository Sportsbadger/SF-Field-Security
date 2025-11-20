"""Command-line entry point for running the Salesforce security tool."""

from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import click
import questionary

from tool_utils import (
    check_auth,
    choose_project_workspace,
    create_sfdx_project_json,
    generate_download_manifest,
    print_post_setup_instructions,
    read_config,
    run_command,
)

if __name__ == '__main__':
    click.echo(click.style("=== Salesforce Security Tool Launcher ===", bold=True, fg='cyan'))

    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    org_url = config['target_org_url']
    persistent_alias = config['persistent_alias']

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
            if not run_command(
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
            ):
                sys.exit(1)

        create_sfdx_project_json(project_path, config['api_version'])
        manifest_path = project_path / 'package.xml'
        generate_download_manifest(manifest_path, config['api_version'], config['explicit_custom_objects'])

        temp_retrieve_dir = project_path / "temp_mdapi_retrieve"
        mdapi_source_path = project_path / "mdapi_source"

        if not run_command(
            [
                'sf',
                'project',
                'retrieve',
                'start',
                '--manifest',
                str(manifest_path),
                '--target-org',
                persistent_alias,
                '--target-metadata-dir',
                str(temp_retrieve_dir),
            ]
        ):
            sys.exit(1)

        zip_path = temp_retrieve_dir / 'unpackaged.zip'
        if zip_path.exists():
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(mdapi_source_path)
        else:
            click.echo(click.style("Error: Could not find 'unpackaged.zip'.", fg='red'))
            sys.exit(1)

        force_app_path = project_path / 'force-app'
        convert_command = [
            'sf',
            'project',
            'convert',
            'mdapi',
            '--root-dir',
            str(mdapi_source_path),
            '--output-dir',
            str(force_app_path),
        ]
        if not run_command(convert_command, cwd=project_path):
            click.echo(click.style("Metadata conversion failed!", fg='red'))
            sys.exit(1)

        shutil.rmtree(temp_retrieve_dir)
        shutil.rmtree(mdapi_source_path)
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
