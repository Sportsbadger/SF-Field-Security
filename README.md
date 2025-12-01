# SF Field Security Tools

This repository packages a launcher (`run_tool.py`), setup utilities, and the interactive Field Security tool (`fs_tool_v151.py`) used to retrieve Salesforce metadata, inspect access, and apply changes to profiles and permission sets.

## What's new
- **Workspace management:** Create, reuse, and refresh named workspaces under `projects/`, with recent workspaces automatically suggested per org.
- **Multi-org awareness:** Store multiple org definitions in `config.ini`, select an active org from the menu, and keep separate workspaces per alias.
- **Guided config creator:** First-run setup now builds `config.ini` interactively, including multiple orgs and API version selection.
- **Root/branch rewrite:** Metadata retrieval now rebuilds the `force-app` branch of each workspace from a clean root each time you refresh.

## Prerequisites
- **Python 3.10+** with the following libraries installed: `click`, `questionary`, and `lxml`. Install them with `pip install click questionary lxml`.
- **Salesforce CLI (`sf`)** installed and available on your `PATH`; it is used for authentication, metadata retrieval, and project conversion steps. Download the CLI from [Salesforce's installation page](https://developer.salesforce.com/tools/salesforcecli) and follow the installer or package-manager instructions for your operating system. After installation, ensure the `sf` binary is on your shell `PATH` so the launcher scripts can invoke it:
  - **macOS/Linux:** if you installed with a package manager (e.g., `brew install sf` or `npm install --global @salesforce/cli`), the binary is typically placed in `/usr/local/bin` or your Node global bin folder. Confirm by running `which sf`; if the command is not found, add the install directory to your `PATH` in `~/.bashrc`, `~/.zshrc`, etc., for example `export PATH="$PATH:/usr/local/bin"`.
  - **Windows:** the installer adds the CLI to the system `PATH` automatically. If you used a ZIP download, add the folder containing `sf.cmd` to `PATH` via **System Properties → Environment Variables** so that `sf --version` works in a new Command Prompt or PowerShell window.
  - Verify with `sf --version`; if it prints the CLI version, the tool will be able to authenticate and retrieve metadata.
- **Access to the target Salesforce org** with browser-based login capability; the tool launches a web login if no active session exists.

## Installation and Setup
1. Clone this repository and open the project root in your terminal.
2. Install the Python prerequisites:
   ```bash
   pip install click questionary lxml
   ```
3. Run the launcher once to create `config.ini` and your initial workspace:
   ```bash
   python run_tool.py
   ```
   - If `config.ini` or a workspace is missing, the guided config creator launches. You can define multiple orgs, pick which org is active, and set the API version. The tool writes `[Org <name>]` sections under `[SalesforceOrgs]` and remembers the active org.
   - When refreshing metadata, the launcher rebuilds the workspace’s `force-app` folder from a clean root using a fresh manifest and conversion process.

## Configuration reference
- `config.ini` is organized into multiple `[Org <name>]` sections so you can store sandbox, prod, and other targets. The `[SalesforceOrgs]` section controls which org is active, and `[ToolOptions]` stores the `api_version`.
- Each `[Org <name>]` entry supports:
  - `target_org_url` – Login URL for that org.
  - `persistent_alias` – Salesforce CLI alias reused for authentication and workspace naming.
  - `explicit_custom_objects` – Comma-separated managed-package objects to include in retrieval (optional).
- Switch the active org from the launcher menu (**Switch Active Org**). The tool automatically aligns the active workspace to the selected org.

## Workspace management
- Workspaces live under `projects/` and are filtered per org alias. The launcher suggests the most recently updated workspace, but you can select any existing one or create a new folder (with a timestamp or custom name).
- When choosing an existing workspace, you can **refresh** (deletes and recreates `force-app` from the retrieved metadata) or **use without refreshing** to preserve current files.
- Workspace metadata is stored in `.workspace_info.json` so the tool can display the active org, alias, and last refreshed time.

## Quick start workflow
Use the launcher to authenticate, retrieve metadata, and open the interactive security tool in one flow.

1. From the repository root, run:
   ```bash
   python run_tool.py
   ```
2. Review the active org and workspace shown at the top of the menu.
3. Choose **Select or Create Workspace** to build or refresh a project. Authentication prompts appear automatically when no active `sf` session exists for the configured alias.
4. After metadata retrieval, the launcher converts MDAPI output into `force-app` source format and cleans temporary folders, rebuilding the branch from the workspace root.
5. Select **Run the File Security Tool** to start `fs_tool_v151.py` against the prepared project. When you exit the tool, you can optionally trigger **Deploy Changes**.

## FS Tool overview and commands
`fs_tool_v151.py` is an interactive CLI for analyzing and editing profile/permission set access within the retrieved project. You can pass `--project`, `--metadata`, and `--dry-run` flags when launching it directly.

Within the menu you can run:
- **Generate Field Security Report (FLS):** Report field-level access for selected profiles or permission sets.
- **Modify Field Security (FLS):** Apply read/edit updates manually or from a CSV definition to profiles or permission sets, with backups.
- **Generate Object Permissions Report:** Matrix view of CRUD/View All/Modify All permissions across chosen objects and profiles/permission sets.
- **Modify Object Permissions:** Update CRUD/View All/Modify All settings manually or via CSV input.
- **Who has access to this field? (Reverse Lookup):** Identify which permission sets or profiles grant access to a specific field.
- **Audit Permission Sets (List Report - FLS, Obj, UserPerms):** Inspect object, field, and user permissions for selected permission sets.
- **Audit Permission Sets (Matrix Report - FLS focused):** Produce a field-centric matrix of permission-set access.
- **Rollback From Backup:** Restore profile/permission-set files from backups created by previous runs.

Use `--dry-run` to preview planned bulk changes without writing to disk. Reports and backups are stored in the `FS Tool Files` directory inside the project workspace.

## Additional scripts
- **`setup_project.py`** – Prepares a project folder without launching the interactive tool (useful for automated retrievals). It mirrors the metadata download steps from `run_tool.py` and can be pointed at a specific projects directory with `--projects-dir`.
- **`deploy_changes.py`** – Deploys updates from a previously prepared workspace. Run it from the repository root to push metadata changes created by the tool.
- **`tool_utils.py`** – Shared helpers used by the launcher and setup script (authentication checks, manifest generation, workspace metadata, config creation, and CLI command wrappers).

## Tips and troubleshooting
- Confirm the Salesforce CLI is authenticated with the configured alias using `sf org display --target-org <alias>` if retrieval fails.
- If you use managed packages, populate the `explicit_custom_objects` entry for each org in `config.ini` so their objects are included in that org's package manifest.
- Keep the CLI version updated (`sf update`) to avoid API incompatibilities when retrieving or deploying metadata.
