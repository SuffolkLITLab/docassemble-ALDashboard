import os
import shutil
import subprocess
from docassemble.webapp.users.models import UserModel
from docassemble.webapp.db_object import init_sqlalchemy
from github import Github  # PyGithub

# db is a SQLAlchemy Engine
from sqlalchemy.sql import text
from typing import List, Tuple, Dict, Optional
import docassemble.webapp.worker
from docassemble.webapp.server import (
    user_can_edit_package,
    get_master_branch,
    install_git_package,
    redirect,
    should_run_create,
    flash,
    url_for,
    restart_all,
    install_pip_package,
    get_package_info,
    get_session_variables,
)
from docassemble.base.config import daconfig
from docassemble.webapp.backend import cloud
from docassemble.base.functions import serializable_dict
from docassemble.base.util import (
    log,
    DAFile,
    DAObject,
    DAList,
    word,
    DAFileList,
    get_config,
    user_has_privilege,
)
from docassemble.webapp.server import get_package_info

from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO
import re
import werkzeug
import pkg_resources


db = init_sqlalchemy()

__all__ = [
    "install_from_github_url",
    "reset",
    "speedy_get_users",
    "speedy_get_sessions",
    "get_users_and_name",
    "da_get_config",
    "da_get_config_as_file",
    "da_write_config",
    "ALPackageInstaller",
    "get_package_info",
    "install_from_pypi",
    "install_fonts",
    "list_installed_fonts",
    "dashboard_get_session_variables",
    "nicer_interview_filename",
    "list_question_files_in_package",
    "list_question_files_in_docassemble_packages",
]


def install_from_github_url(url: str, branch: str = "", pat: Optional[str] = None):
    giturl = url.strip().rstrip("/")
    if pat:
        # modify so it looks like https://ghp_...:x-oauth-basic@github.com/johnsmith/docassemble-missouri-familylaw
        giturl = re.sub(r"^https://", f"https://{pat}:x-oauth-basic@", giturl)
    if isinstance(branch, str):
        branch = branch.strip()
    if not branch:
        branch = get_master_branch(giturl)
    packagename = re.sub(r"/*$", "", giturl)
    packagename = re.sub(r"^git+", "", packagename)
    packagename = re.sub(r"#.*", "", packagename)
    packagename = re.sub(r"\.git$", "", packagename)
    packagename = re.sub(r".*/", "", packagename)
    packagename = re.sub(r"^docassemble-", "docassemble.", packagename)
    if user_can_edit_package(giturl=giturl) and user_can_edit_package(
        pkgname=packagename
    ):
        install_git_package(packagename, giturl, branch)
    else:
        flash(word("You do not have permission to install this package."), "error")
    return packagename


def install_from_pypi(packagename: str):
    return install_pip_package(packagename)


def reset(packagename=""):
    result = docassemble.webapp.worker.update_packages.apply_async(
        link=docassemble.webapp.worker.reset_server.s(
            run_create=should_run_create(packagename)
        )
    )
    return redirect(url_for("update_package_wait"))


def da_get_config_as_file():
    yaml = YAML()
    yaml.allow_duplicate_keys = True
    # try:
    with open(daconfig["config file"], "r", encoding="utf-8") as fp:
        content = fp.read()

    the_file = DAFile()
    the_file.initialize(filename="config.yml")
    the_file.write(content)
    return the_file


def da_get_config():
    yaml = YAML()
    yaml.allow_duplicate_keys = True
    # try:
    with open(daconfig["config file"], "r", encoding="utf-8") as fp:
        content = fp.read()
    data = yaml.load(content)
    return data
    # except:
    #  return None


def da_write_config(data: Dict):
    yaml = YAML()
    yaml.allow_duplicate_keys = True
    try:
        # ruamel.yaml has a big rant about why we shouldn't save the YAML output to a string,
        # but it's done upstream here so we will use the provided workaround
        # see https://yaml.readthedocs.io/en/latest/example.html?highlight=StringIO#output-of-dump-as-a-string
        stream = StringIO()
        yaml.dump(data, stream)
        yaml_data = stream.getvalue()
    except:
        log("Invalid configuration provided to da_write_config, skipping.")
        return None
    if cloud is not None:
        key = cloud.get_key("config.yml")
        key.set_contents_from_string(yaml_data)
    with open(daconfig["config file"], "w", encoding="utf-8") as fp:
        fp.write(yaml_data)
    restart_all()
    return True


def speedy_get_users() -> List[Dict[int, str]]:
    """
    Return a list of all users in the database. Possibly faster than get_user_list().
    """
    the_users = UserModel.query.with_entities(UserModel.id, UserModel.email).all()

    return [{user[0]: user[1]} for user in the_users]


def get_users_and_name() -> List[Tuple[int, str, str, str]]:
    users = UserModel.query.with_entities(
        UserModel.id, UserModel.email, UserModel.first_name, UserModel.last_name
    )

    return users


def speedy_get_sessions(
    user_id: Optional[int] = None,
    filename: Optional[str] = None,
    filter_step1: bool = True,
    metadata_key_name: str = "metadata",
) -> List[Tuple]:
    """
    Return a list of the most recent 500 sessions, optionally tied to a specific user ID.

    Each session is a tuple with named columns:
    filename,
    user_id,
    modtime,
    key
    """
    get_sessions_query = text(
        """
SELECT 
    userdict.filename as filename,
    num_keys,
    userdictkeys.user_id as user_id,
    mostrecent.modtime as modtime,  -- This retrieves the most recent modification time for each key
    userdict.key as key,
    jsonstorage.data->>'auto_title' as auto_title,
    jsonstorage.data->>'title' as title,
    jsonstorage.data->>'description' as description,
    jsonstorage.data->>'steps' as steps,
    jsonstorage.data->>'progress' as progress
FROM 
    userdict 
NATURAL JOIN 
    (
        SELECT 
            key,
            MAX(modtime) AS modtime,  -- Calculate the most recent modification time for each key
            COUNT(key) AS num_keys
        FROM 
            userdict
        GROUP BY 
            key
        HAVING 
            COUNT(key) > 1 OR :filter_step1 = False
    ) mostrecent
LEFT JOIN 
    userdictkeys ON userdictkeys.key = userdict.key
LEFT JOIN 
    jsonstorage ON jsonstorage.key = userdict.key AND jsonstorage.tags = :metadata
WHERE 
    (userdict.user_id = :user_id OR :user_id is null)
    AND (userdict.filename = :filename OR :filename is null)
ORDER BY 
    modtime DESC 
LIMIT 500;
        """
    )
    if not filename:
        if not user_has_privilege(["admin", "developer"]):
            raise Exception(
                "You must provide a filename to filter sessions unless you are a developer or administrator."
            )
        filename = None  # Explicitly treat empty string as equivalent to None
    if not user_id:
        user_id = None

    # Ensure filter_step1 is a boolean
    filter_step1 = bool(filter_step1)

    with db.connect() as con:
        rs = con.execute(
            get_sessions_query,
            {
                "user_id": user_id,
                "filename": filename,
                "filter_step1": filter_step1,
                "metadata": metadata_key_name,
            },
        )
    sessions = [session for session in rs]

    return sessions


def dashboard_get_session_variables(session_id: str, filename: str):
    """
    Return the variables and steps for a given session ID and YAML filename in serializable dictionary format.
    """
    user_dict = get_session_variables(filename, session_id, secret=None, simplify=False)
    return serializable_dict(user_dict, include_internal=False)


class ALPackageInstaller(DAObject):
    """Methods and state for installing AssemblyLine."""

    def init(self, *pargs, **kwargs):
        super().init(*pargs, **kwargs)
        self.initializeAttribute("errors", ErrorList)

    def get_validated_github_username(self, access_token: str):
        """
        Given a valid GitHub `access_token`, returns the username associated with it.
        Otherwise, adds one or more errors to the installer.
        """
        self.errors.clear()  # Reset
        github = Github(access_token)
        github_user = github.get_user()
        try:
            # Ensure the token has the right permissions
            scopes = str(github_user.raw_headers["x-oauth-scopes"]).split(", ")
            if not "repo" in scopes and not "public_repo" in scopes:
                self.errors.appendObject(
                    template_name="github_permissions_error", scopes=scopes
                )
                return None
            else:
                return github_user.login
        except Exception as error:
            # GitHub doesn't recognize the token
            # github.GithubException.BadCredentialsException (401, 403) (specific exception not working)
            self.errors.appendObject(template_name="github_credentials_error")


class ErrorList(DAList):
    """Contains `ErrorLikeObject`s so they can be recognized by docassemble."""

    def init(self, *pargs, **kwargs):
        super().init(*pargs, **kwargs)
        self.object_type = ErrorLikeObject
        self.gathered = True


class ErrorLikeObject(DAObject):
    """
    An object with a `template_name` that identifies the DALazyTemplate that will
    show its error. It can contain any other attributes so its template can access them
    as needed. DAObject doesn't seem to be enough to allow template definition.
    """

    def init(self, *pargs, **kwargs):
        super().init(*pargs, **kwargs)
        # `unknown_error` can be a default template for unexpected errors to use
        self.template_name = kwargs.get("template_name", "unknown_error")


def install_fonts(the_font_files: DAFileList):
    """
    Install fonts to the server and restart both supervisor and unoconv.
    """
    # create the /var/www/.fonts directory if it doesn't exist
    if not os.path.exists("/var/www/.fonts"):
        os.makedirs("/var/www/.fonts")

    # save the DAFile to /var/www/.fonts
    for f in the_font_files:
        shutil.copyfile(
            f.path(), "/var/www/.fonts/" + werkzeug.utils.secure_filename(f.filename)
        )

    output = ""
    output += subprocess.run(
        ["fc-cache", "-f", "-v"], capture_output=True, text=True
    ).stdout
    output += subprocess.run(
        ["supervisorctl", "restart", "uwsgi"], capture_output=True, text=True
    ).stdout
    output += subprocess.run(
        ["supervisorctl", "start", "reset"], capture_output=True, text=True
    ).stdout
    if get_config("enable unoconv"):
        output += subprocess.run(
            ["supervisorctl", "-s", "http://localhost:9001", "restart", "unoconv"],
            capture_output=True,
            text=True,
        ).stdout

    return output


def list_installed_fonts():
    """
    List the fonts installed on the server.
    """
    fc_list = subprocess.run(["fc-list"], stdout=subprocess.PIPE)
    output = subprocess.run(
        ["sort"], stdin=fc_list.stdout, capture_output=True, text=True
    ).stdout
    fc_list.stdout.close()
    return output


#  select userdict.filename, num_keys, userdictkeys.user_id, modtime, userdict.key from userdict natural join (select key, max(modtime) as modtime, count(key) as num_keys from userdict group by key) mostrecent left join userdictkeys on userdictkeys.key = userdict.key order by modtime desc;
# db.session.query

# From server.py
#  subq = db.session.query(db.func.max(UserDict.indexno).label('indexno'), UserDict.filename, UserDict.key).group_by(UserDict.filename, UserDict.key).subquery()
#  interview_query = db.session.query(UserDictKeys.user_id, UserDictKeys.temp_user_id, UserDictKeys.filename, UserDictKeys.key, UserDict.dictionary, UserDict.encrypted, UserModel.email).join(subq, and_(subq.c.filename == UserDictKeys.filename, subq.c.key == UserDictKeys.key)).join(UserDict, and_(UserDict.indexno == subq.c.indexno, UserDict.key == UserDictKeys.key, UserDict.filename == UserDictKeys.filename)).join(UserModel, UserModel.id == UserDictKeys.user_id).filter(UserDictKeys.user_id == user_id, UserDictKeys.filename == filename, UserDictKeys.key == session).group_by(UserModel.email, UserDictKeys.user_id, UserDictKeys.temp_user_id, UserDictKeys.filename, UserDictKeys.key, UserDict.dictionary, UserDict.encrypted, UserDictKeys.indexno).order_by(UserDictKeys.indexno)


def nicer_interview_filename(filename: str) -> str:
    """
    Given a filename like docassemble.playground10ALWeaver:data/questions/assembly_line.yml,
    return a less cluttered name like: playground10ALWeaver:assembly_line
    """
    filename_parts = filename.split(":")

    # Fixing the slicing for the first part of the filename
    if filename_parts[0].startswith("docassemble."):
        filename_parts[0] = filename_parts[0][len("docassemble.") :]

    # Check if there are two parts and modify the second part
    if len(filename_parts) > 1:
        if filename_parts[1].startswith("data/questions/"):
            filename_parts[1] = filename_parts[1][len("data/questions/") :]
        return f"{filename_parts[0]}:{filename_parts[1].replace('.yml', '')}"

    return filename_parts[0]


def list_question_files_in_package(package_name: str) -> Optional[List[str]]:
    """
    List all the files in the 'data/questions' directory of a package.

    Args:
        package_name (str): The name of the package to list files from.

    Returns:
        List[str]: A list of filenames in the 'data/questions' directory of the package.
    """
    try:
        # Locate the directory within the package
        directory_path = pkg_resources.resource_filename(package_name, "data/questions")

        # List all files in the directory
        if os.path.isdir(directory_path):
            files = os.listdir(directory_path)
            # Filter out directories, only keep files
            files = [
                f for f in files if os.path.isfile(os.path.join(directory_path, f))
            ]
            return files
        else:
            return []
    except Exception as e:
        log(f"An error occurred with package '{package_name}': {e}")
        return []


def list_question_files_in_docassemble_packages():
    """
    List all the files in the 'data/questions' directory of all docassemble packages.

    Returns:
        Dict[str, List[str]]: A dictionary where the keys are package names and the values are lists of filenames in the 'data/questions' directory of the package.
    """
    packages = get_package_info()[
        0
    ]  # get_package_info returns a tuple, the packages are in index 0

    filtered_packages = [
        pkg for pkg in packages if pkg.package.name.startswith("docassemble.")
    ]

    result = {}

    # Iterate over each filtered package and list files in 'data/questions'
    for package in filtered_packages:
        package_name = package.package.name

        files = list_question_files_in_package(package_name)
        if files:
            result[package_name] = files

    return result
