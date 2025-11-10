import os
import shutil
import subprocess
from docassemble.webapp.users.models import UserModel
from docassemble.webapp.db_object import init_sqlalchemy
from github import Github  # PyGithub

# db is a SQLAlchemy Engine
from sqlalchemy.sql import text
from typing import List, Tuple, Dict, Optional, Callable
import math

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
    DACloudStorage,
)
from docassemble.webapp.server import get_package_info

from ruamel.yaml import YAML
from ruamel.yaml.compat import StringIO
import re
import werkzeug
import pkg_resources
from datetime import datetime, timedelta

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
    "dashboard_session_activity",
    "make_usage_rows",
    "compute_heatmap_styles",

    "nicer_interview_filename",
    "list_question_files_in_package",
    "list_question_files_in_docassemble_packages",
    "increment_index_value",
    "get_current_index_value",
    "get_latest_s3_folder",
]


def install_from_github_url(url: str, branch: str = "", pat: Optional[str] = None):
    giturl = url.strip().rstrip("/")
    if pat:
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
    if user_can_edit_package(giturl=giturl) and user_can_edit_package(pkgname=packagename):
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
    fc_list = subprocess.run(["fc-list"], stdout=subprocess.PIPE, text=True)
    output = subprocess.run(
        ["sort"], input=fc_list.stdout, capture_output=True, text=True
    ).stdout
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


def make_usage_rows(
    current_interview_usage: Optional[Dict[int, List[Dict]]],
    nicer_fn: Callable[[str], str] = lambda x: x,
    limit: int = 10,
) -> List[Dict]:
    """Convert the nested `current_interview_usage` structure into a list of rows
    suitable for rendering in the template.

    Args:
        current_interview_usage: mapping of minute -> list of dicts with keys
            including 'filename', 'sessions', 'users'. Typically the output
            of `dashboard_session_activity`.
        nicer_fn: callable that receives a filename and returns a display title.
        limit: maximum number of rows to return (sorted by total recent sessions).

    Returns:
        A list of dicts with keys: filename, title, s_1, s_5, s_10, s_30, s_60,
        s_120, users, total
    """
    if not current_interview_usage:
        return []

    filenames = set()
    for items in current_interview_usage.values():
        for it in items:
            # tolerate malformed items
            fname = it.get("filename") if isinstance(it, dict) else None
            if fname:
                filenames.add(fname)

    rows: List[Dict] = []
    for fname in filenames:
        row = {"filename": fname, "title": nicer_fn(fname)}
        total = 0
        for m in (1, 5, 10, 30, 60, 120):
            found = next(
                (i for i in current_interview_usage.get(m, []) if i.get("filename") == fname),
                None,
            )
            count = 0
            if found:
                try:
                    count = int(found.get("sessions", 0) or 0)
                except Exception:
                    count = 0
            row[f"s_{m}"] = count
            total += count
        row["total"] = total
        found120 = next(
            (i for i in current_interview_usage.get(120, []) if i.get("filename") == fname),
            None,
        )
        users = 0
        if found120:
            try:
                users = int(found120.get("users", 0) or 0)
            except Exception:
                users = 0
        row["users"] = users
        rows.append(row)

    rows = sorted(rows, key=lambda r: r["total"], reverse=True)[: int(limit) if limit else None]
    return rows


def dashboard_session_activity(minutes_list=None, limit: int = 10):
    """
    Return a dict mapping each minutes value to a list of top interviews by session starts
    during the last N minutes. Each list contains dicts with keys: filename, sessions, users, title.

    Example return value:
    {60: [{'filename': 'docassemble.foo:data/questions/x.yml', 'sessions': 12, 'users': 9, 'title': 'foo:x'}, ...], ...}
    """
    if minutes_list is None:
        minutes_list = [1, 5, 10, 30, 60, 120]
    results = {}
    # We'll compute starttime per session key and then count sessions whose starttime >= cutoff
    query = text(
        """
SELECT sub.filename as filename, COUNT(sub.key) AS sessions, COUNT(DISTINCT userdictkeys.user_id) AS users
FROM (
    SELECT key, filename, MIN(modtime) AS starttime
    FROM userdict
    GROUP BY key, filename
) sub
LEFT JOIN userdictkeys ON userdictkeys.key = sub.key
WHERE sub.starttime >= :cutoff
GROUP BY sub.filename
ORDER BY sessions DESC
LIMIT :limit
        """
    )
    with db.connect() as con:
        for m in minutes_list:
            cutoff = datetime.utcnow() - timedelta(minutes=int(m))
            rs = con.execute(query, {"cutoff": cutoff, "limit": int(limit)})
            rows = []
            for row in rs:
                # SQLAlchemy result rows may be mapping-like or tuple-like depending on version/context.
                if hasattr(row, "_mapping"):
                    mapping = row._mapping
                    fname = mapping.get("filename")
                    sessions_count = mapping.get("sessions")
                    users_count = mapping.get("users")
                else:
                    # positional fallback: filename, sessions, users
                    fname = row[0] if len(row) > 0 else None
                    sessions_count = row[1] if len(row) > 1 else None
                    users_count = row[2] if len(row) > 2 else None

                try:
                    sessions_val = int(sessions_count or 0)
                except Exception:
                    sessions_val = 0
                try:
                    users_val = int(users_count or 0)
                except Exception:
                    users_val = 0

                rows.append(
                    {
                        "filename": fname,
                        "sessions": sessions_val,
                        "users": users_val,
                        "title": nicer_interview_filename(fname) if fname else "",
                    }
                )
            results[int(m)] = rows
    return results


def compute_heatmap_styles(rows, windows: Optional[tuple] = None):
    """Augment each row (dict) in `rows` with per-window inline style and text color
    keys for rendering a logarithmic heatmap.

    Mutates and returns the input list for convenience.
    """

    if not rows:
        return rows

    if windows is None:
        windows = (1, 5, 10, 30, 60, 120)

    def _luminance_channel(v: float) -> float:
        # v is 0-255
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    # determine global max across all windows and users so scaling is consistent
    max_count = 0
    for r in rows:
        for m in windows:
            try:
                c = int(r.get(f"s_{m}", 0) or 0)
            except Exception:
                c = 0
            if c > max_count:
                max_count = c
        try:
            u = int(r.get("users", 0) or 0)
        except Exception:
            u = 0
        if u > max_count:
            max_count = u

    denom = math.log10(max_count + 1) if max_count > 0 else 1.0

    for r in rows:
        # per-cell windows
        for m in windows:
            try:
                c = int(r.get(f"s_{m}", 0) or 0)
            except Exception:
                c = 0
            if max_count <= 0:
                norm = 0.0
            else:
                norm = math.log10(c + 1) / denom
            alpha = 0.15 + 0.8 * norm
            bg = f"background-color: rgba(255,0,0,{alpha:.3f});"

            # compute contrast against white page background composited with our red tint
            try:
                comp_r = 255.0
                comp_g = 255.0 * (1.0 - alpha)
                comp_b = 255.0 * (1.0 - alpha)
                L_comp = (
                    0.2126 * _luminance_channel(comp_r)
                    + 0.7152 * _luminance_channel(comp_g)
                    + 0.0722 * _luminance_channel(comp_b)
                )
                L_black = 0.0
                L_white = 1.0
                contrast_with_black = (L_comp + 0.05) / (L_black + 0.05)
                contrast_with_white = (L_white + 0.05) / (L_comp + 0.05)
                if contrast_with_black >= 4.5 and contrast_with_black >= contrast_with_white:
                    textcol = "black"
                elif contrast_with_white >= 4.5 and contrast_with_white > contrast_with_black:
                    textcol = "white"
                else:
                    textcol = "black" if contrast_with_black >= contrast_with_white else "white"
            except Exception:
                textcol = "black"

            r[f"text_color_{m}"] = textcol
            # single combined style attribute so template doesn't concatenate fragments
            r[f"style_attr_{m}"] = f"{bg} color: {textcol}"

        # users column: scale by same denom
        try:
            u = int(r.get("users", 0) or 0)
        except Exception:
            u = 0
        if max_count <= 0:
            unorm = 0.0
        else:
            unorm = math.log10(u + 1) / denom
        ualpha = 0.15 + 0.8 * unorm
        ubg = f"background-color: rgba(255,0,0,{ualpha:.3f});"

        try:
            comp_r = 255.0
            comp_g = 255.0 * (1.0 - ualpha)
            comp_b = 255.0 * (1.0 - ualpha)
            L_comp = (
                0.2126 * _luminance_channel(comp_r)
                + 0.7152 * _luminance_channel(comp_g)
                + 0.0722 * _luminance_channel(comp_b)
            )
            contrast_with_black = (L_comp + 0.05) / (0.0 + 0.05)
            contrast_with_white = (1.0 + 0.05) / (L_comp + 0.05)
            if contrast_with_black >= 4.5 and contrast_with_black >= contrast_with_white:
                utext = "black"
            elif contrast_with_white >= 4.5 and contrast_with_white > contrast_with_black:
                utext = "white"
            else:
                utext = "black" if contrast_with_black >= contrast_with_white else "white"
        except Exception:
            utext = "black"

        r["text_color_users"] = utext
        r["style_attr_users"] = f"{ubg} color: {utext}"

    return rows


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


def increment_index_value(by: int = 5000, index_name: str = "uploads_indexno_seq"):
    """
    Increment the file index value in the database by a specified amount.

    Args:
        by (int): The amount to increment the file index value by. Defaults to 5000.
        index_name (str): The name of the sequence to increment. Defaults to "uploads_indexno_seq".
    """
    with db.connect() as con:
        con.execute(
            text(f"SELECT setval(:index_name, nextval(:index_name) + :by);"),
            {"by": by, "index_name": index_name},
        )
        con.commit()


def get_current_index_value() -> int:
    """Get the current value of the file index sequence.

    Returns:
        int: The current value of the file index sequence.
    """
    query = db.session.execute(text("SELECT last_value FROM uploads_indexno_seq"))
    return query.fetchone()[0]


def get_latest_s3_folder(prefix: str = "files/") -> Optional[int]:
    """
    Return the highest integer “folder” that exists directly under *prefix*,
    or None if there are no numeric folders at all.

    • Uses the S3 LIST paginator, so it works for any number of prefixes.
    • Ignores non‑numeric folder names (e.g. files/tmp/, files/images/, …).
    • Requires only read permission for ListObjectsV2.

    Example return value: 45237
    """
    cloud = DACloudStorage()  # your Docassemble wrapper
    client, bucket = cloud.client, cloud.bucket_name

    highest = None
    paginator = client.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
        Delimiter="/",  # ask S3 to give us one prefix per “folder”
    ):
        for cp in page.get("CommonPrefixes", []):
            # strip the leading prefix (“files/”) and trailing “/”
            name = cp["Prefix"][len(prefix) : -1]
            if name.isdigit():
                n = int(name)
                if highest is None or n > highest:
                    highest = n

    return highest