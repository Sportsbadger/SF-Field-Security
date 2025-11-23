"""Deploy the latest generated project changes to the target org."""

from pathlib import Path
import sys

import click

from tool_utils import check_auth, read_config, run_command

if __name__ == '__main__':
    click.echo(click.style("=== Step 3: Deploy Changes ===", bold=True, fg='cyan'))

    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    persistent_alias = config.persistent_alias

    if not check_auth(persistent_alias, announce=False):
        click.echo(
            click.style(
                f"Error: Not authenticated to org with alias '{persistent_alias}'.",
                fg='red',
            )
        )
        click.echo("Please run 'setup_project.py' first to log in.")
        sys.exit(1)

    projects_dir = script_dir / 'projects'
    if not projects_dir.is_dir() or not any(projects_dir.iterdir()):
        click.echo(click.style("Error: No project directories found.", fg='red'))
        sys.exit(1)

    latest_project = max(projects_dir.iterdir(), key=lambda p: p.stat().st_ctime)
    click.echo(f"Found latest project to deploy from: {latest_project.name}")

    manifest_path = latest_project / 'force-app' / 'main' / 'default' / 'package.xml'

    if not manifest_path.exists():
        click.echo(
            click.style(
                f"Error: No 'package.xml' found inside '{manifest_path.parent}'.",
                fg='red',
            )
        )
        click.echo(
            "This means the security tool did not generate a deployment package. "
            "No changes to deploy."
        )
        sys.exit(1)

    click.echo(click.style("\nStarting deployment...", bold=True))

    deploy_command = [
        'sf', 'project', 'deploy', 'start',
        '--manifest', str(manifest_path),
        '--target-org', persistent_alias
    ]

    deploy_result = run_command(deploy_command, cwd=latest_project)
    if not deploy_result.success:
        click.echo(click.style("Deployment failed. Please review the output above.", fg='red'))
    else:
        click.echo(click.style("\n✓ Deployment Succeeded.", bold=True, fg='green'))
