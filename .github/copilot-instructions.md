# ALDashboard: docassemble Admin and Configuration Tool

ALDashboard is a Python package built for the docassemble framework - a web application platform for creating guided interviews and legal forms. It provides admin dashboard functionality for managing users, packages, sessions, and various configuration tasks within a docassemble server environment.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Working Effectively

- **Bootstrap and build the package:**
  - `sudo apt-get update && sudo apt-get -y install libcurl4-openssl-dev build-essential python3-dev libldap2-dev libsasl2-dev slapd ldap-utils tox lcov libzbar0 libaugeas0 augeas-lenses` -- installs system dependencies. Takes ~20 seconds. NEVER CANCEL.
  - `python3 -m venv venv && source venv/bin/activate` -- create virtual environment. Takes ~3 seconds.
  - `pip install wheel` -- install wheel package
  - `pip install -e .` -- install package in development mode. Takes ~30 seconds. 
  - `pip install -r docassemble/ALDashboard/requirements.txt` -- install development dependencies. Takes 5-15 minutes. NEVER CANCEL. Set timeout to 20+ minutes.

- **Build the package:**
  - `python setup.py build` -- builds the package. Takes ~0.2 seconds. Very fast.
  - `python setup.py check` -- validates package metadata. Takes ~0.15 seconds.

- **Type checking and linting:**
  - `python3 -m mypy docassemble/ALDashboard --config-file pyproject.toml` -- type checking. Takes ~17 seconds. NEVER CANCEL. Set timeout to 30+ minutes.
  - Note: mypy will show errors for missing docassemble framework dependencies, which is expected when running outside of docassemble environment.

- **Testing:**
  - Tests require the full docassemble framework to run properly.
  - `python3 -m unittest docassemble.ALDashboard.test_validate_docx` -- runs unit tests (will fail without docassemble dependencies installed)
  - The CI uses `SuffolkLITLab/ALActions/pythontests@main` which sets up a full testing environment.

## Project Structure and Navigation

This is a docassemble package with the following key structure:

### Repository Root
```
.
├── README.md                          # Main project documentation
├── setup.py                           # Package configuration and dependencies  
├── pyproject.toml                     # Tool configuration (mypy, black)
├── docassemble/
│   └── ALDashboard/
│       ├── aldashboard.py             # Main Python module with admin functions
│       ├── test_validate_docx.py      # Unit tests
│       ├── data/
│       │   ├── questions/             # Interview YAML files (docassemble UI)
│       │   │   ├── menu.yml          # Main dashboard menu
│       │   │   ├── manage_users.yml  # User management interface
│       │   │   └── *.yml             # Other admin tools
│       │   ├── static/               # CSS, images, HTML templates
│       │   └── templates/            # Document templates
│       └── *.py                      # Additional Python modules
└── .github/
    └── workflows/                    # CI/CD automation
        ├── mypy.yml                  # Type checking workflow
        ├── deploy.yml                # Deployment to dev server
        └── publish.yml               # PyPI publishing
```

### Key Files to Know
- `docassemble/ALDashboard/data/questions/menu.yml` - Main dashboard interface
- `docassemble/ALDashboard/aldashboard.py` - Core Python functionality
- `setup.py` - Package dependencies and metadata
- `pyproject.toml` - Development tool configurations
- `.github/workflows/mypy.yml` - CI type checking configuration

## Validation

- **Cannot run the application standalone** - this package requires a running docassemble server with proper configuration.
- **For testing UI changes:** You must install this package into a docassemble development environment. The package adds administrative interviews accessible via the docassemble web interface.
- **Always run type checking:** `python3 -m mypy docassemble/ALDashboard --config-file pyproject.toml` before committing.
- **Manual testing scenarios:** After making changes, test within docassemble by:
  1. Installing the package in a docassemble instance
  2. Adding the configuration to make ALDashboard available in admin menu
  3. Testing specific workflows like user management, package installation, etc.

## Common Tasks

### Working with Dependencies
The main runtime dependencies are defined in `setup.py`:
- `PyGithub>=2.1.1` - GitHub API access
- `docassemble.ALToolbox>=0.9.2` - AssemblyLine utility functions
- `python-docx>=1.1.1` - Word document processing  
- `openai>=1.0` - AI integration
- `tiktoken` - Token counting for AI
- `pyaml` - YAML processing

Development dependencies in `docassemble/ALDashboard/requirements.txt` include docassemble framework components.

### Code Organization
- **Python modules:** Core business logic in `docassemble/ALDashboard/*.py`
- **Interview files:** User interfaces in `docassemble/ALDashboard/data/questions/*.yml`
- **Static assets:** CSS and images in `docassemble/ALDashboard/data/static/`
- **Templates:** Document templates in `docassemble/ALDashboard/data/templates/`

### Typical Development Workflow
1. Make changes to Python code or YAML interview files
2. Run `python setup.py build` to verify package builds correctly
3. Run type checking: `python3 -m mypy docassemble/ALDashboard --config-file pyproject.toml`
4. For UI changes, test in a live docassemble environment
5. Commit changes - CI will run automated checks

## Important Notes

- **NEVER CANCEL builds or tests** - the docassemble framework has many dependencies and pip installations can take 5-15 minutes.
- **Timeout requirements:** Set timeouts of 20+ minutes for dependency installation, 30+ seconds for type checking.
- **Network dependencies:** Installation may fail due to PyPI timeouts - this is common in restricted environments.
- **Framework dependency:** This package cannot be tested in isolation - it requires the docassemble web framework.
- **Admin privileges required:** The package functionality requires admin/developer privileges within docassemble.

## Configuration Requirements

To use ALDashboard in a docassemble instance, add to configuration:
```yaml
install packages api key: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

administrative interviews:
  - interview: docassemble.ALDashboard:data/questions/menu.yml
    title: Dashboard
    required privileges:
      - admin
      - developer
```

The package provides tools for:
- User management (reset passwords, change permissions)
- Package installation and updates
- Session analysis and debugging
- Document template validation
- Translation file management
- Bootstrap theme compilation