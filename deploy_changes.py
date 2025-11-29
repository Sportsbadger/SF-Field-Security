"""Deploy the latest generated project changes to the target org."""

from pathlib import Path
import sys
import json

import click
import questionary

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

    click.echo(click.style("\nChecking for deployable changes...", bold=True))

    preview_json_result = run_command(
        [
            'sf', 'project', 'deploy', 'preview',
            '--manifest', str(manifest_path),
            '--target-org', persistent_alias,
            '--json',
        ],
        cwd=latest_project,
        capture_output=True,
        check=False,
    )

    planned_changes: list = []
    preview_payload: dict = {}
    result_payload: dict = {}

    try:
        preview_payload = json.loads(preview_json_result.stdout or '{}')
        result_payload = preview_payload.get('result', {}) if isinstance(preview_payload, dict) else {}
        planned_changes = (
            result_payload.get('deployedSource')
            or result_payload.get('outboundFiles')
            or []
        )
    except json.JSONDecodeError:
        if not preview_json_result.success:
            click.echo(
                click.style(
                    "Deployment preview failed and returned non-JSON output.",
                    fg='red',
                )
            )
            if preview_json_result.stdout:
                click.echo(preview_json_result.stdout)
            sys.exit(preview_json_result.returncode or 1)

        click.echo(
            click.style(
                "Could not parse deployment preview output. Review the preview details before proceeding.",
                fg='red',
            )
        )
        if preview_json_result.stdout:
            click.echo(preview_json_result.stdout)
        sys.exit(1)

    if not preview_json_result.success:
        if preview_json_result.stdout:
            click.echo(click.style("Deployment preview output:", bold=True))
            click.echo(preview_json_result.stdout)
        click.echo(
            click.style(
                "Deployment preview failed. Please resolve the issues above before deploying.",
                fg='red',
            )
        )
        sys.exit(preview_json_result.returncode or 1)

    if not planned_changes:
        click.echo(
            click.style(
                "No deployable changes were detected for the latest project. Returning to the menu.",
                fg='yellow',
            )
        )
        sys.exit(0)

    click.echo(click.style("\nPlanned deployment (before/after):", bold=True))
    run_command(
        [
            'sf', 'project', 'deploy', 'preview',
            '--manifest', str(manifest_path),
            '--target-org', persistent_alias,
        ],
        cwd=latest_project,
        check=False,
    )

    if not questionary.confirm(
        f"Deploy these changes to org alias '{persistent_alias}'?",
        default=False,
    ).ask():
        click.echo("Deployment cancelled. Returning to the menu without changes.")
        sys.exit(0)

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
