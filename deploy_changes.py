import datetime
from pathlib import Path
import click
import sys
import subprocess
import configparser
import json

def run_command(command: list[str], cwd: Path = None, capture_output=False):
    command_str = subprocess.list2cmdline(command)
    if not capture_output:
        click.echo(click.style(f"\n> Executing (in shell): {command_str}", fg='yellow'))
    try:
        if capture_output:
            result = subprocess.run(command_str, capture_output=True, text=True, shell=True, check=True, cwd=cwd)
            return result.stdout
        process = subprocess.Popen(command_str, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=cwd, shell=True)
        for line in iter(process.stdout.readline, ''): print(line, end='')
        process.wait()
        if process.returncode != 0: raise subprocess.CalledProcessError(process.returncode, command)
        if not capture_output: click.echo(click.style("✓ Command successful.", fg='green'))
        return True
    except (subprocess.CalledProcessError, Exception) as e:
        if not capture_output: click.echo(click.style(f"✗ Command failed.", fg='red'))
        return e.stdout if capture_output else False

def read_config(config_path: Path) -> dict:
    config = configparser.ConfigParser()
    config.read(config_path)
    return {'persistent_alias': config.get('Salesforce', 'persistent_alias')}

def check_auth(alias: str) -> bool:
    """Checks if the given alias is already authenticated."""
    try:
        output = run_command(['sf', 'org', 'list', '--json'], capture_output=True)
        if not output: return False
        org_list = json.loads(output)
        for org in org_list.get('result', {}).get('nonScratchOrgs', []):
            if org.get('alias') == alias or org.get('username') == alias:
                return True
    except (json.JSONDecodeError, Exception):
        return False
    return False

if __name__ == '__main__':
    click.echo(click.style("=== Step 3: Deploy Changes ===", bold=True, fg='cyan'))
    
    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    persistent_alias = config['persistent_alias']

    if not check_auth(persistent_alias):
        click.echo(click.style(f"Error: Not authenticated to org with alias '{persistent_alias}'.", fg='red'))
        click.echo("Please run 'setup_project.py' first to log in.")
        sys.exit(1)

    projects_dir = script_dir / 'projects'
    if not projects_dir.is_dir() or not any(projects_dir.iterdir()):
        click.echo(click.style("Error: No project directories found.", fg='red')); sys.exit(1)

    latest_project = max(projects_dir.iterdir(), key=lambda p: p.stat().st_ctime)
    click.echo(f"Found latest project to deploy from: {latest_project.name}")

    # The manifest file is now inside the force-app structure because that's where the tool is run
    manifest_path = latest_project / 'force-app' / 'main' / 'default' / 'package.xml'

    if not manifest_path.exists():
        click.echo(click.style(f"Error: No 'package.xml' found inside '{manifest_path.parent}'.", fg='red'))
        click.echo("This means the security tool did not generate a deployment package. No changes to deploy."); sys.exit(1)

    click.echo(click.style("\nStarting deployment...", bold=True))
    
    # --- THE FIX IS HERE ---
    # The CLI needs to run from within the project directory to find all the source files.
    # We provide the manifest, and it uses the sfdx-project.json to know where to look.
    # We DO NOT provide --source-dir.
    deploy_command = [
        'sf', 'project', 'deploy', 'start',
        '--manifest', str(manifest_path),
        '--target-org', persistent_alias
    ]

    # We must set the Current Working Directory (cwd) to the project root for the command to work.
    if not run_command(deploy_command, cwd=latest_project):
         click.echo(click.style("Deployment failed. Please review the output above.", fg='red'))
    else:
         click.echo(click.style("\n✓ Deployment Succeeded.", bold=True, fg='green'))