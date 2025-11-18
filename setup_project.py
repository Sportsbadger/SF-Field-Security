import click
import shutil
import sys
import zipfile
from pathlib import Path

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
    click.echo(click.style("=== Step 1: Project Setup and Metadata Download ===", bold=True, fg='cyan'))

    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    org_url = config['target_org_url']
    persistent_alias = config['persistent_alias']

    projects_dir = script_dir / 'projects'
    project_path, _action = choose_project_workspace(
        projects_dir,
        persistent_alias,
        "Choose an action for your project workspace:",
        "Create a new project workspace",
        "Update an existing project workspace",
        "Preparing to refresh metadata. This will delete the existing 'force-app' folder.",
        "Removed old 'force-app' directory.",
    )

    if not check_auth(persistent_alias):
        click.echo(click.style("\nAction Required: A browser window will open for authentication.", bold=True))
        if not run_command(['sf', 'org', 'login', 'web', '--instance-url', org_url, '--alias', persistent_alias]):
            sys.exit(1)

    create_sfdx_project_json(project_path, config['api_version'])
    manifest_path = project_path / 'package.xml'
    generate_download_manifest(manifest_path, config['api_version'], config['explicit_custom_objects'])
    
    temp_retrieve_dir = project_path / "temp_mdapi_retrieve"
    mdapi_source_path = project_path / "mdapi_source"

    if not run_command(['sf', 'project', 'retrieve', 'start', '--manifest', str(manifest_path), '--target-org', persistent_alias, '--target-metadata-dir', str(temp_retrieve_dir)]):
        sys.exit(1)
        
    zip_path = temp_retrieve_dir / 'unpackaged.zip'
    if zip_path.exists():
        click.echo("Unzipping downloaded MDAPI metadata...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(mdapi_source_path)
        click.echo(click.style("✓ Metadata unzipped.", fg='green'))
    else:
        click.echo(click.style("Error: Could not find 'unpackaged.zip'.", fg='red')); sys.exit(1)
    
    click.echo("\nConverting metadata from MDAPI format to Source format...")
    force_app_path = project_path / 'force-app'
    convert_command = ['sf', 'project', 'convert', 'mdapi', '--root-dir', str(mdapi_source_path), '--output-dir', str(force_app_path)]
    if not run_command(convert_command, cwd=project_path):
        click.echo(click.style("Metadata conversion failed!", fg='red')); sys.exit(1)

    click.echo(click.style("✓ Metadata successfully converted.", fg='green'))
    shutil.rmtree(temp_retrieve_dir)
    shutil.rmtree(mdapi_source_path)

    print_post_setup_instructions(project_path, launching_tool=False)
