# ALDashboard: a docassemble Admin and Configuration Tool

[![PyPI version](https://badge.fury.io/py/docassemble.ALDashboard.svg)](https://badge.fury.io/py/docassemble.ALDashboard)

A single tool and interview to centralize some tedious Docassemble admin configuration tasks.

![A screenshot of the ALDashboard menu with choices: "Admin only - manage users", "Admin only - stats", "Install assembly line", "Verify API Keys", "Install packages", "update packages", "Package scanner", "View Answer files", "generate review screen draft", "validate docx template", "validation translation files", "prepare translation files", "validate an attachment fields block", "PDF tools", and "Compile Bootstrap theme"](https://github.com/SuffolkLITLab/docassemble-ALDashboard/assets/6252212/29539eec-3891-476b-b248-dd3db986d899)

1. Install the Document Assembly Line packages (support files for [Court Forms Online](https://courtformsonline.org))
1. Searchable user management - reset passwords and change privileges.
1. Installing or updating several packages at once.
1. Listing and viewing the contents of an (unencrypted) interview to facilitate debugging errors on production servers.
1. View analytics/stats captured with `store_variable_snapshot`.
1. List the files inside a particular package installed on the server.
1. Gather files from a user who left the organization/unknown username and password.
1. Review screen generator
1. validate DOCX Jinja2 templates
1. Generate a [custom bootstrap theme](https://suffolklitlab.org/docassemble-AssemblyLine-documentation/docs/customization/overview#creating-a-custom-theme-from-source-instead-of-with-a-theme-generator) for your interviews.

Ideas:
1. Add a link to the dispatch directive for an existing file in an existing package.
1. Generate translation files [TBD].

## Use

To use, you must create a docassemble API key and add it to your
configuration, like this:

`install packages api key: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

If you want the ALDashboard to be a dropdown option for admins and developers, add the following to the configuration before your `install packages api key`: 

    administrative interviews:
      - interview: docassemble.ALDashboard:data/questions/menu.yml
        title: Dashboard
        required privileges:
          - admin
          - developer

## ALDashboard API

When installed on a docassemble server, ALDashboard exposes a Flask API at:

- `POST /al/api/v1/dashboard/translation`
- `POST /al/api/v1/dashboard/docx/auto-label`
- `POST /al/api/v1/dashboard/docx/runs`
- `POST /al/api/v1/dashboard/docx/relabel`
- `POST /al/api/v1/dashboard/bootstrap/compile`
- `POST /al/api/v1/dashboard/translation/validate`
- `POST /al/api/v1/dashboard/review-screen/draft`
- `POST /al/api/v1/dashboard/docx/validate`
- `POST /al/api/v1/dashboard/yaml/check`
- `POST /al/api/v1/dashboard/yaml/reformat`
- `POST /al/api/v1/dashboard/pdf/label-fields`
- `POST /al/api/v1/dashboard/pdf/fields/detect`
- `POST /al/api/v1/dashboard/pdf/fields/relabel`
- `GET /al/api/v1/dashboard/jobs/{job_id}`
- `GET /al/api/v1/dashboard/jobs/{job_id}/download`
- `DELETE /al/api/v1/dashboard/jobs/{job_id}`
- `GET /al/api/v1/dashboard/openapi.json`
- `GET /al/api/v1/dashboard/docs`

The API uses docassemble API key authentication via `api_verify()`. Endpoints default to synchronous execution and support `mode=async` (or `async=true`) for Celery-backed processing.

To enable async mode, add this module to your docassemble configuration:

```yaml
celery modules:
  - docassemble.ALDashboard.api_dashboard_worker
```

### Endpoint Notes

- `POST /al/api/v1/dashboard/translation`
  - Input: `interview_path`, one or more target languages (`tr_langs`), optional GPT settings.
  - Output: translation XLSX metadata and optional base64 file content.
- `POST /al/api/v1/dashboard/docx/auto-label`
  - Input: DOCX file upload, optional `custom_people_names`.
  - Uses `docassemble.ALToolbox.llms` for OpenAI configuration.
  - Optional per-request overrides: `openai_api`, `openai_base_url`, `openai_model`.
  - Prompt customization: `custom_prompt`, `additional_instructions`.
  - Optional output budget override: `max_output_tokens`.
  - Output: `results` array by default; include `include_labeled_docx_base64=true` to also get updated DOCX bytes.
- `POST /al/api/v1/dashboard/docx/runs`
  - Input: DOCX file upload (or base64 content).
  - Output: parsed run list as `results`, each entry `[paragraph_index, run_index, run_text]`.
  - Traversal includes body paragraphs, tables, headers, and footers.
- `POST /al/api/v1/dashboard/docx/relabel`
  - Input: existing `results` from first-pass label run and/or DOCX upload.
  - Supports index-based edits: `replace_labels_by_index`, `skip_label_indexes`.
  - Supports explicit additions: `add_labels`.
  - Supports range-based rule additions: `add_label_rules` (paragraph range + match conditions).
  - Output: edited `results` array by default; include `include_labeled_docx_base64=true` to also get updated DOCX bytes.
  - In async mode, download binary file output from `GET /al/api/v1/dashboard/jobs/{job_id}/download`.
- `POST /al/api/v1/dashboard/bootstrap/compile`
  - Input: SCSS upload or `scss_text`.
  - Output: compiled CSS text or base64.
  - Operational notes:
    - Requires `node` and `npm` available on server `PATH`.
    - First run downloads Bootstrap source into `/tmp` and runs `npm install`/`npm run css-compile`, so it may be noticeably slower.
    - Requires outbound HTTPS access to fetch Bootstrap and npm dependencies.
    - Writes temporary build artifacts under `/tmp`; ensure adequate disk space and cleanup policies.
- `POST /al/api/v1/dashboard/translation/validate`
  - Input: translation XLSX.
  - Output: structured errors/warnings/empty rows.
- `POST /al/api/v1/dashboard/review-screen/draft`
  - Input: one or more YAML files.
  - Output: generated review-screen YAML draft.
- `POST /al/api/v1/dashboard/docx/validate`
  - Input: one or more DOCX templates.
  - Output: per-file Jinja rendering errors.
- `POST /al/api/v1/dashboard/yaml/check`
  - Input: `yaml_text` (or `yaml_content`) and optional `filename`.
  - Output: structured DAYamlChecker issues with `errors`, `warnings`, and `valid`.
- `POST /al/api/v1/dashboard/yaml/reformat`
  - Input: `yaml_text` (or `yaml_content`), optional `line_length` and `convert_indent_4_to_2`.
  - Output: reformatted YAML in `formatted_yaml` and `changed` boolean.
- `POST /al/api/v1/dashboard/pdf/label-fields`
  - Input: PDF upload.
  - Output: PDF with fields detected and optionally relabeled (backward-compatible alias of `/pdf/fields/detect`).
- `POST /al/api/v1/dashboard/pdf/fields/detect`
  - Input: PDF upload.
  - Optional flags: `relabel_with_ai`, `include_pdf_base64`, `include_parse_stats`.
  - Optional exact-name list: `target_field_names` (ordered list to apply after detection).
  - Output: PDF with detected fields added, plus optional AI/target-name relabeling.
- `POST /al/api/v1/dashboard/pdf/fields/relabel`
  - Input: PDF with existing fields.
  - Relabel modes: `field_name_mapping` (exact old->new map), ordered `target_field_names`, or AI (`relabel_with_ai=true`).
  - Output: Relabeled PDF and resulting field names; optional parse stats/base64 output.
- `GET /al/api/v1/dashboard/jobs/{job_id}/download`
  - Streams the first available file artifact from a completed async job.
  - Optional query parameters:
    - `index` (0-based artifact index)
    - `field` (exact artifact field path from JSON result)

Live docs:

- `GET /al/api/v1/dashboard/openapi.json`
- `GET /al/api/v1/dashboard/docs`

## MCP Bridge API

ALDashboard also exposes a lightweight MCP-style discovery layer over HTTP:

- `POST /al/api/v1/mcp` (JSON-RPC 2.0 endpoint)
- `GET /al/api/v1/mcp` (endpoint metadata)
- `GET /al/api/v1/mcp/tools` (convenience tool listing)
- `GET /al/api/v1/mcp/docs` (human-readable docs)

Supported JSON-RPC methods:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`

`tools/list` discovers tools generated from:

- ALDashboard REST OpenAPI paths (`/al/api/v1/dashboard/...`)
- ALWeaver REST paths (`/al/api/v1/weaver...`) only when `docassemble.ALWeaver` is installed.

For development-only fallback discovery from a local checkout, set:

```bash
export ALDASHBOARD_MCP_DEV_MODE=true
export ALWEAVER_REPO_PATH=~/docassemble-ALWeaver
```

Example:

```bash
curl -X POST "https://YOURSERVER/al/api/v1/mcp" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Tool execution example:

```bash
curl -X POST "https://YOURSERVER/al/api/v1/mcp" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"aldashboard.get_al_api_v1_dashboard_openapi_json","arguments":{}}}'
```

`tools/call` securely reuses the same authenticated request context (for example `X-API-Key` or `Authorization`) and does not require storing a separate API key in MCP configuration.

### DOCX Modes and End-to-End Workflow

Purpose of each mode:

1. `POST /docx/runs`: inspection mode
   - Returns `[paragraph_index, run_index, run_text]`.
   - Use this to understand document coordinates before deterministic edits.
2. `POST /docx/auto-label`: draft generation mode
   - Generates initial label suggestions (`results`).
3. `POST /docx/relabel`: editing/apply mode
   - Edits draft labels (`replace_labels_by_index`, `skip_label_indexes`, `add_labels`, `add_label_rules`).
   - If DOCX content is provided and `include_labeled_docx_base64=true`, returns an updated DOCX.
4. `GET /jobs/{job_id}/download`: async file download mode
   - Streams final binary output from completed async jobs.

Full workflow: upload DOCX -> draft labels -> manual edits (change, delete, add) -> download final DOCX

Step 1. Create draft labels (async)

```bash
curl -X POST "https://YOURSERVER/al/api/v1/dashboard/docx/auto-label" \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "mode=async" \
  -F "file=@/path/to/input.docx" \
  -F "openai_base_url=https://YOURRESOURCE.openai.azure.com/openai/v1/" \
  -F "openai_api=YOUR_AZURE_OPENAI_KEY" \
  -F "openai_model=gpt-5-mini"
```

Step 2. Poll job until `status=succeeded`, then read `data.results`

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://YOURSERVER/al/api/v1/dashboard/jobs/JOB_ID_FROM_STEP_1"
```

Step 3. Edit labels manually (change one, delete one, add one) and request final DOCX (async)

```bash
curl -X POST "https://YOURSERVER/al/api/v1/dashboard/docx/relabel" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "mode": "async",
    "filename": "input.docx",
    "file_content_base64": "BASE64_DOCX_HERE",
    "results": [[1,0,"{{ letter_date }}",0],[2,0,"{{ old_name }}",0],[3,0,"{{ keep_me }}",0]],
    "replace_labels_by_index": {"0":"{{ edited_letter_date }}"},
    "skip_label_indexes": [1],
    "add_labels": [[0,0,"{{ added_new_label }}",0]],
    "include_labeled_docx_base64": true
  }'
```

Step 4. Poll relabel job, then download final DOCX

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://YOURSERVER/al/api/v1/dashboard/jobs/JOB_ID_FROM_STEP_3"

curl -L -o final_labeled.docx \
  -H "X-API-Key: YOUR_API_KEY" \
  "https://YOURSERVER/al/api/v1/dashboard/jobs/JOB_ID_FROM_STEP_3/download"
```

## Some screenshots

### Main page
![A screenshot of the ALDashboard menu with choices: "Admin only - manage users", "Admin only - stats", "Install assembly line", "Verify API Keys", "Install packages", "update packages", "Package scanner", "View Answer files", "generate review screen draft", "validate docx template", "validation translation files", "prepare translation files", "validate an attachment fields block", "PDF tools", and "Compile Bootstrap theme"](https://github.com/SuffolkLITLab/docassemble-ALDashboard/assets/6252212/29539eec-3891-476b-b248-dd3db986d899)

### Manage users

![A screenshot that says "Manage users" with the fields "User", "What do you want want to do? Reset password or Change user permissions", "New Password", and "Verify new Password"](https://user-images.githubusercontent.com/7645641/123702231-e069ec00-d830-11eb-94dc-5ec0abb86bc9.png)

### Bulk install packages from GitHub

![A screenshot that says "What packages do you want to install?" The fields are for "Github URL", "YAML filename", and "Short name or alias (no spaces)"](https://user-images.githubusercontent.com/7645641/123702290-efe93500-d830-11eb-9fdf-a5935ff4078e.png)

### Bulk update packages

![A screenshot that says "What packages do you want to update?" followed by a list of packages. For example, "docassemble.209aPlaintiffMotionToModify", "docassemble.ALAffidavitOfIndigency", and more.](https://user-images.githubusercontent.com/7645641/123702362-068f8c00-d831-11eb-9ce4-df7a67ffcfeb.png)

### View answer files

View / search sessions by user and interview name

![A screenshot that says "What interview do you want to view sessions for?" The fields are "File name" and "User (leave blank to view all sessions)"](https://user-images.githubusercontent.com/7645641/123702422-1d35e300-d831-11eb-84d5-5e7385deb901.png)

![A screenshot that says "Recently generated sessions for docassemble.MA209AProtectiveOrder:data/questions/209a_package.yml" with 5 sessions below.](https://user-images.githubusercontent.com/7645641/123702464-2cb52c00-d831-11eb-80fc-f2291e824eae.png)

### View interview stats captured with `store_variables_snapshot()`

![A screenshot with the title "Stats for Eviction Moratorium: 9". Below is the text "Total submissions: 9", "Group by: zip | state | modtime", and "Excel Download" followed by a map that can be filtered by state or by date.](https://user-images.githubusercontent.com/7645641/123702623-5e2df780-d831-11eb-8937-6625df74ab22.png)

### Generate a bootstrap theme

![A screenshot with the title "Your file is compiled!", below is the text "You can view and copy your file, or download it directly by right clicking the link to save it as a CSS file". Below that are examples of Bootstrap components like buttons and nav bars.](https://github.com/SuffolkLITLab/docassemble-ALDashboard/assets/6252212/079e428d-4cae-4f75-8b1b-227c28f32a44)
