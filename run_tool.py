import shutil
import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import click
import questionary
import sys
import subprocess
import configparser
import zipfile
import json

SF_NAMESPACE_URI = 'http://soap.sforce.com/2006/04/metadata'

def run_command(command: list[str], cwd: Path = None, capture_output=False, check=True):
    command_str = subprocess.list2cmdline(command)
    if not capture_output:
        click.echo(click.style(f"\n> Executing (in shell): {command_str}", fg='yellow'))
    
    try:
        if capture_output:
            result = subprocess.run(command_str, capture_output=True, text=True, shell=True, check=check, cwd=cwd)
            return result.stdout
        
        process = subprocess.Popen(command_str, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=cwd, shell=True)
        for line in iter(process.stdout.readline, ''): print(line, end='')
        process.wait()
        if process.returncode != 0: raise subprocess.CalledProcessError(process.returncode, command)
        
        if not capture_output:
            click.echo(click.style("✓ Command successful.", fg='green'))
        return True

    except subprocess.CalledProcessError as e:
        if not capture_output:
            click.echo(click.style(f"✗ Command failed.", fg='red'))
        return e.stdout if capture_output else False
    except Exception as e:
        click.echo(click.style(f"✗ An unexpected error occurred: {e}", fg='red'))
        return False

def read_config(config_path: Path) -> dict:
    config = configparser.ConfigParser()
    config.read(config_path)
    settings = {}
    settings['target_org_url'] = config.get('Salesforce', 'target_org_url')
    settings['persistent_alias'] = config.get('Salesforce', 'persistent_alias')
    explicit_objects_str = config.get('ToolOptions', 'explicit_custom_objects', fallback='').strip()
    settings['explicit_custom_objects'] = [obj.strip() for obj in explicit_objects_str.split(',') if obj.strip()]
    settings['api_version'] = config.get('ToolOptions', 'api_version', fallback='60.0')
    return settings

def check_auth(alias: str) -> bool:
    click.echo(f"Checking for existing authentication for alias: '{alias}'...")
    try:
        output = run_command(['sf', 'org', 'list', '--json'], capture_output=True, check=False)
        if not output: return False
        
        org_list = json.loads(output)
        all_orgs = org_list.get('result', {}).get('nonScratchOrgs', []) + org_list.get('result', {}).get('scratchOrgs', [])
        for org in all_orgs:
            if alias in org.get('alias', '') or alias in org.get('aliases', []):
                click.echo(click.style("✓ Found active session.", fg='green'))
                return True
    except (json.JSONDecodeError, Exception):
        pass
    click.echo(click.style("No active session found. A new login will be required.", fg='yellow'))
    return False

def generate_download_manifest(manifest_path: Path, api_version: str, explicit_objects: list[str]):
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
    if hasattr(ET, 'indent'): ET.indent(tree, space="    ")
    tree.write(manifest_path, encoding='UTF-8', xml_declaration=True)

def create_sfdx_project_json(project_path: Path, api_version: str):
    project_def = {"packageDirectories": [{"path": "force-app", "default": True}], "name": "SecurityToolProject", "namespace": "", "sfdcLoginUrl": "https://login.salesforce.com", "sourceApiVersion": api_version}
    with open(project_path / 'sfdx-project.json', 'w') as f:
        json.dump(project_def, f, indent=4)

if __name__ == '__main__':
    click.echo(click.style("=== Salesforce Security Tool Launcher ===", bold=True, fg='cyan'))
    
    script_dir = Path(__file__).parent
    config = read_config(script_dir / 'config.ini')
    org_url = config['target_org_url']
    persistent_alias = config['persistent_alias']
    
    projects_dir = script_dir / 'projects'
    project_path = None

    existing_projects = []
    if projects_dir.is_dir():
        existing_projects = sorted([p for p in projects_dir.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)

    menu_choices = ["Create a new project workspace"]
    if existing_projects:
        menu_choices.append("Update an existing project workspace")

    action = questionary.select("Choose an action:", choices=menu_choices).ask()

    if action is None: click.echo("Operation cancelled."); sys.exit(0)

    if action == "Update an existing project workspace":
        project_choices = [p.name for p in existing_projects]
        chosen_project_name = questionary.select("Which project to update?", choices=project_choices).ask()
        if chosen_project_name is None: click.echo("Operation cancelled."); sys.exit(0)
        
        project_path = projects_dir / chosen_project_name
        click.echo(f"\nSelected existing project: {project_path}")
        click.echo(click.style("Refreshing metadata... This will replace the 'force-app' folder.", fg='yellow'))
        
        force_app_path_to_delete = project_path / 'force-app'
        if force_app_path_to_delete.exists():
            shutil.rmtree(force_app_path_to_delete)

    elif action == "Create a new project workspace":
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        project_path = projects_dir / f"{ts}_{persistent_alias}"
        project_path.mkdir(parents=True, exist_ok=True)
        click.echo(f"\nCreated new project directory at: {project_path}")

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
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(mdapi_source_path)
    else:
        click.echo(click.style("Error: Could not find 'unpackaged.zip'.", fg='red')); sys.exit(1)
    
    force_app_path = project_path / 'force-app'
    convert_command = ['sf', 'project', 'convert', 'mdapi', '--root-dir', str(mdapi_source_path), '--output-dir', str(force_app_path)]
    if not run_command(convert_command, cwd=project_path):
        click.echo(click.style("Metadata conversion failed!", fg='red')); sys.exit(1)
    
    shutil.rmtree(temp_retrieve_dir)
    shutil.rmtree(mdapi_source_path)
    
    click.echo("\n" + "="*50)
    click.echo(click.style("Setup complete. Launching the security tool...", bold=True, fg='green'))
    click.echo(f"Working on project: {project_path.name}")
    click.echo("="*50)

    tool_script_path = script_dir / 'fs_tool_v151.py'
    # Make sure to check the actual name of your tool file here
    if not tool_script_path.exists():
        click.echo(click.style(f"Error: The security tool script '{tool_script_path.name}' was not found in this directory.", fg='red'))
        sys.exit(1)
        
    subprocess.run([sys.executable, str(tool_script_path), '--project', str(project_path)])

    click.echo("\n" + "="*50)
    click.echo(click.style("Security tool session finished.", bold=True))
    
    # --- DEPLOYMENT LAUNCHER LOGIC ---
    if questionary.confirm("Do you want to run the deployment script now?", default=False).ask():
        deploy_script_path = script_dir / 'deploy_changes.py'
        if not deploy_script_path.exists():
            click.echo(click.style(f"Error: The deployment script '{deploy_script_path.name}' was not found.", fg='red'))
        else:
            click.echo(click.style("\nLaunching deployment script...", fg='cyan'))
            # Launch deploy_changes.py as a separate process
            subprocess.run([sys.executable, str(deploy_script_path)])
    else:
        click.echo("\nDeployment skipped.")
        click.echo("To deploy your changes later, run:")
        click.echo("python deploy_changes.py")
        
    click.echo("\n" + "="*50)
    click.echo("Tool run complete.")
    click.echo("="*50)