# SF Field Security Tools

Salesforce metadata retrieval, analysis, and deployment workflow focused on field security and object permissions.

This repository provides:
- A launcher (`run_tool.py`) for org/workspace management and end-to-end flow.
- A setup utility (`setup_project.py`) for metadata retrieval only.
- The interactive security tool (`fs_tool_v151.py`) for reports, updates, and rollback.
- A deploy utility (`deploy_changes.py`) to push generated metadata changes.

---

## What changed in this update

The current toolset now supports:
- Multi-org configuration in a single `config.ini` with active-org switching.
- Workspace-per-alias model under `projects/` with recency ordering.
- Metadata refresh that rebuilds `force-app` from a fresh retrieval/conversion cycle.
- Guided first-run config creation when config or workspace is missing.
- Deployment readiness detection from generated `package.xml`.
- Expanded FS tool actions, including user-centric and reverse-lookup reports.

---

## Prerequisites

- Python **3.10+**
- Python packages:
  ```bash
  pip install click questionary lxml
  ```
- Salesforce CLI (`sf`) installed and available in `PATH`
  - Verify:
    ```bash
    sf --version
    ```
- Access to the target Salesforce org(s) for web login

---

## Repository layout

- `run_tool.py` — Main launcher/menu-driven workflow
- `setup_project.py` — Retrieval/conversion workflow without launching FS tool
- `fs_tool_v151.py` — Security analysis and editing CLI
- `deploy_changes.py` — Deploy changes from latest workspace for active alias
- `tool_utils.py` — Shared config/auth/workspace/metadata helpers
- `tests/` — Targeted regression tests
- `projects/` — Generated workspaces (created at runtime)
- `config.ini` — Runtime configuration (created at first run)

---

## Quick start (recommended)

From repository root:

```bash
python run_tool.py
```

Typical flow:
1. Complete guided config creation on first run.
2. Select or create a workspace.
3. Refresh metadata when prompted (auth is requested automatically if needed).
4. Run the File Security Tool.
5. Deploy changes when ready.

---

## Configuration (`config.ini`)

The launcher supports multiple org definitions and one active org.

### Structure

```ini
[SalesforceOrgs]
active_org = sandbox

[Org sandbox]
target_org_url = https://example.sandbox.my.salesforce.com/
persistent_alias = sandbox
explicit_custom_objects = Managed_Object__c,Managed_Object_2__c

[Org production]
target_org_url = https://login.salesforce.com
persistent_alias = prod
explicit_custom_objects =

[ToolOptions]
api_version = 60.0
```

### Keys

- `SalesforceOrgs.active_org`: active org name matching one `[Org <name>]` section
- `Org <name>.target_org_url`: login URL for that org
- `Org <name>.persistent_alias`: `sf` alias used for auth, retrieval, deployment, and workspace filtering
- `Org <name>.explicit_custom_objects`: optional comma-separated managed/custom objects to force into retrieval manifest
- `ToolOptions.api_version`: API version for generated `package.xml`

Notes:
- Legacy single-org format is still supported for backward compatibility.
- If multiple orgs are configured, `active_org` must be set.

---

## Workspace model

Workspaces are created under:

```text
projects/
```

Behavior:
- Workspaces are associated to org alias using `.workspace_info.json`.
- Menus prioritize most recently updated workspace for the active alias.
- Existing workspace can be used without refresh, or refreshed to rebuild `force-app`.
- Refresh deletes/recreates `force-app` in that workspace via retrieval + conversion.

---

## Launcher (`run_tool.py`)

Run:

```bash
python run_tool.py
```

Main menu options:
- `Select or Create Workspace`
- `Switch Active Org` (only shown when 2+ orgs configured)
- `Run the File Security Tool`
- `Deploy Changes`
- `Exit`

Launcher behavior:
- Displays active org, active workspace, and last refresh timestamp.
- Detects pending deploy state when `force-app/main/default/package.xml` exists.
- Uses current active org config for auth/retrieval/deploy.

---

## Setup-only flow (`setup_project.py`)

Use when you want retrieval/conversion only:

```bash
python setup_project.py
```

This script:
- Ensures config exists.
- Prompts for workspace create/select.
- Authenticates if needed.
- Retrieves metadata and converts to source format.
- Saves workspace metadata.

---

## Field Security Tool (`fs_tool_v151.py`)

Direct invocation:

```bash
python fs_tool_v151.py --project <workspace_path> [--metadata <relative_path>] [--dry-run]
```

CLI flags:
- `--project`: project root path (default `.`)
- `--metadata`: optional metadata folder override relative to project root
- `--dry-run`: preview bulk FLS/object updates without modifying files

### Interactive actions

Inside the tool menu:
- `Generate Field Security Report (FLS)`
- `Modify Field Security`
- `Generate Object Permissions Report`
- `Modify Object Permissions`
- `Generate User Field Access Report`
- `Who has access to this field? (Reverse Lookup)`
- `Audit Permission Sets (By Perm Set)`
- `Audit Permission Sets (By Field)`
- `Rollback From Backup`
- `Exit`

### FLS/object modification capabilities

- Modify Profiles or Permission Sets.
- Bulk operations via CSV-driven definitions (where prompted).
- Backup of modified metadata before writes.
- Auto-generation/update of `package.xml` for modified components.
- Dry-run mode creates planning artifacts without applying writes.

### Output location

FS tool writes reports/backups under:

```text
<workspace>/FS Tool Files/
```

---

## Deployment (`deploy_changes.py`)

Run:

```bash
python deploy_changes.py
```

Workflow:
- Reads active org alias from config.
- Uses most recent workspace for that alias.
- Requires generated manifest at:
  - `<workspace>/force-app/main/default/package.xml`
- Deploy command:
  ```bash
  sf project deploy start --manifest <manifest_path> --target-org <alias>
  ```
- On successful deploy, removes the manifest file.

---

## End-to-end workflow examples

### A) Standard interactive run

```bash
python run_tool.py
```
- Create/select workspace
- Refresh metadata
- Run FS tool and make changes
- Deploy from launcher

### B) Scripted-ish split flow

```bash
python setup_project.py
python fs_tool_v151.py --project ./projects/<workspace_name>
python deploy_changes.py
```

### C) Safe preview run

```bash
python fs_tool_v151.py --project ./projects/<workspace_name> --dry-run
```

---

## Troubleshooting

- **Not authenticated**
  - Validate auth/session:
    ```bash
    sf org list --json
    sf org display --target-org <alias>
    ```
- **No workspace for active org**
  - Run launcher and create/select one under `projects/`.
- **No `package.xml` found during deploy**
  - FS tool did not generate deployable changes yet.
- **Managed-package objects missing**
  - Add object API names to `explicit_custom_objects` for that org.
- **CLI/API mismatch issues**
  - Update CLI:
    ```bash
    sf update
    ```

---

## Notes for contributors

- Keep tooling Python-only with minimal dependencies (`click`, `questionary`, `lxml`).
- Preserve workspace metadata semantics (`.workspace_info.json`) when changing project selection logic.
- Preserve manifest-driven deploy flow expected by `deploy_changes.py`.
