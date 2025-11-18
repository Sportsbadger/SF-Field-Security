# SF Field Security Tools

This repository packages a launcher (`run_tool.py`) and the interactive Field Security tool (`fs_tool_v151.py`) used to retrieve Salesforce metadata, inspect access, and apply changes to profiles and permission sets.

## Prerequisites
- **Python 3.10+** with the following libraries installed: `click`, `questionary`, and `lxml`. Install them with `pip install click questionary lxml`.
- **Salesforce CLI (`sf`)** installed and available on your `PATH`; it is used for authentication, metadata retrieval, and project conversion steps.
- **Access to the target Salesforce org** with browser-based login capability; the tool launches a web login if no active session exists.

## Installation and Setup
1. Clone this repository and open the project root in your terminal.
2. Install the Python prerequisites:
   ```bash
   pip install click questionary lxml
   ```
3. Update `config.ini` with your org details:
   - `target_org_url` should match the login URL for your environment.
   - `persistent_alias` is the alias that the CLI will use for the authenticated org.
   - `explicit_custom_objects` lists any managed-package objects to include in the retrieval (comma-separated, optional).
   - `api_version` controls the package.xml API version for retrieval.

## Using the Launcher (`run_tool.py`)
The launcher handles authentication, metadata retrieval, and starting the security tool.

1. From the repository root, run:
   ```bash
   python run_tool.py
   ```
2. Choose whether to **create a new project workspace** or **update an existing one**. The launcher stores projects under `projects/` and refreshes metadata when updating.
3. If no active Salesforce CLI session exists for the configured alias, a browser window opens for login.
4. The launcher generates `sfdx-project.json`, builds a `package.xml`, retrieves metadata (profiles, permission sets, and custom objects), converts it to source format, and cleans temporary folders.
5. After setup, it automatically starts `fs_tool_v151.py` against the prepared project folder. When you exit the tool, you can optionally trigger `deploy_changes.py` to push modifications.

## FS Tool Overview and Commands
`fs_tool_v151.py` is an interactive CLI for analyzing and editing profile/permission set access within the retrieved project. You can pass `--project`, `--metadata`, and `--dry-run` flags when launching it directly.

Within the menu you can run:
- **Generate Field Security Report (FLS):** Report field-level access for selected profiles or permission sets.
- **Generate Object Permissions Report:** Matrix view of CRUD/View All/Modify All permissions across chosen objects and profiles/permission sets.
- **Who has access to this field? (Reverse Lookup):** Identify which permission sets or profiles grant access to a specific field.
- **Audit Permission Sets (List Report - FLS, Obj, UserPerms):** Inspect object, field, and user permissions for selected permission sets.
- **Audit Permission Sets (Matrix Report - FLS focused):** Produce a field-centric matrix of permission-set access.
- **Bulk Apply Field Security (FLS):** Apply read/edit updates manually or from a CSV definition to profiles or permission sets, with backups.
- **Modify Object Permissions:** Update CRUD/View All/Modify All settings manually or via CSV input.
- **Rollback From Backup:** Restore profile/permission-set files from backups created by previous runs.

Use `--dry-run` to preview planned bulk changes without writing to disk. Reports and backups are stored in the `FS Tool Files` directory inside the project workspace.
