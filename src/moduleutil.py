"""
Module util functions

Copyright (C) 2024  __retr0.init__

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
from dataclasses import dataclass
import datetime
import pygit2
import shutil
import os
from urllib.parse import urlsplit
from threading import Thread
import pip
from icecream import ic

ic.disable()

up_conv_dict: dict = {
    '_': '_u_',
    '/': '_s_',
    '.': '_d_',
    '-': '_h_',
}

'''
Parse the Git https URL into folder name. The URL format will be:
`https://<[www.]xxx-yyy_zzz.cn>.com/<[user-name/]repo_test.txt>.git`
The resulting name will be:
`xxx_h_yyy_u_zzz_d_cn__[user_h_name_s_]repo_u_test_d_txt`

@param url: str         The URL string
@return url: str,       The URL string
        parsed: str,    The parsed folder name
        validated: bool Whether the URL validates
'''
def giturl_parse(url: str) -> tuple[str, str, bool]:
    u = urlsplit(url)
    ic(u)

    # Catch all errors
    uns: list[str] = u.netloc.split('.')
    if u.scheme != 'https':
        ic()
        return url, "", False
    elif u.netloc == '':
        ic()
        return url, "", False
    elif uns[-1] != 'com' or len(uns) < 2:
        ic()
        return url, "", False
    elif u.path.split('.')[-1] != 'git':
        ic()
        return url, "", False

    # Parse the net location
    netloc: str = '.'.join([_ for _ in uns if _ != 'www' or _ != 'com'])
    ic(netloc)
    for _ in up_conv_dict:
        netloc = netloc.replace(_, up_conv_dict[_])
    ic(netloc)

    # Parse the path to git repo
    path: str = u.path[1:-4]
    ic(path)
    for _ in up_conv_dict:
        path = path.replace(_, up_conv_dict[_])
    ic(path)

    return url, f"{netloc}__{path}", True


'''
Clone the git repo from the given url with the format defined by
 `giturl_parse` function

@param url: str         The git repo URL string
@return reponame: str,  The cloned repo name
        validated: bool Whether the cloning and URL validates
'''
def gitrepo_clone(url: str) -> tuple[str, bool]:
    url, reponame, validated = giturl_parse(url)
    if not validated:
        return reponame, False
    try:
        pygit2.clone_repository(url, f"extensions/{reponame}")
    except pygit2.GitError:
        return reponame, False
    return reponame, True


'''
Pull the kernel git repo from remote "master" branch only.
Note that this only pull changes, it does NOT merge the changes
 if there are local changes and commits.
CC-BY-SA-3.0: https://stackoverflow.com/a/27786533

@return err_code: int   Error code (
    0:  Success
    2:  Git fetch failed
    3:  Not found master branch
)
'''
def base_gitrepo_pull(repo_path: str) -> int:
    repo: pygit2.Repository = pygit2.Repository(
        repo_path
    )
    try:
        repo.remotes["origin"].fetch()
    except:
        ic()
        # Remote fetch failed
        return 2
    
    try:
        remote_master_id: str = repo.lookup_reference('refs/remotes/origin/master').target
    except KeyError:
        # Remote Master branch does not exist
        ic()
        return 3
    else:
        repo.checkout_tree(repo.get(remote_master_id))

    try:
        master_ref: pygit2.Reference = repo.lookup_reference('refs/heads/master')
    except KeyError:
        # Local master branch does not exist
        ic()
        return 3
    else:
        master_ref.set_target(remote_master_id)
        repo.head.set_target(remote_master_id)
    # Success
    return 0


'''
Pull the module git repo from remote "master" branch only.
Note that this only pull changes, it does NOT merge the changes
 if there are local changes and commits.
CC-BY-SA-3.0: https://stackoverflow.com/a/27786533

@param name: str        The module name of the repo
@return err_code: int   Error code (
    0:  Success
    1:  It is not a git repo
    2:  Git fetch failed
    3:  Not found master branch
)
'''
def gitrepo_pull(name: str) -> int:
    path: str = f"{os.getcwd()}/extensions/{name}"
    repo_path: str = pygit2.discover_repository(path)
    if repo_path == pygit2.discover_repository(os.getcwd()):
        # Not a git repo
        ic()
        return 1
    ret: int = base_gitrepo_pull(repo_path)
    return ret


'''
Remove the unloaded git repo.

@param name: str    The module of the repo
'''
def gitrepo_delete(name: str) -> None:
    path: str = f"{os.getcwd()}/extensions/{name}"
    ic(path)
    # Check whether the path is a git repo
    if not is_gitrepo(name):
        return
    if shutil.rmtree.avoids_symlink_attacks:
        print("This system is prone to symlink attacks. Be aware!")
    # piprequirements_operate(f"{path}/requirements.txt", install=False)
    try:
        shutil.rmtree(path)
    except OSError as e:
        print(f"Error: {e.filename} - {e.strerror}")

'''
Check whether the folder is a Git repo

@param name: str    The module name
@return is_git: bool
'''
def is_gitrepo(name: str) -> bool:
    path: str = f"{os.getcwd()}/extensions/{name}"
    ic(path)
    if pygit2.discover_repository(path) == pygit2.discover_repository(os.getcwd()):
        return False
    else:
        return True

if hasattr(pip, "main"):
    pip_main = pip.main
else:
    pip_main = pip._internal.main

'''
Pip (un)install packages

@param *packages: str   The python packages to be installed
@param install: bool    (Default: True) Whether to install or uninstall packages
@return success: bool
'''
def pipmodule_operate(*packages: str, install: bool = True) -> bool:
    install_str: tuple[str] = ("install") if install else ("uninstall", "-y")
    ret: int = pip_main([*install_str, *packages])
    return True if ret == 0 else False


'''
Pip (un)install packages from requirements.txt

@param file_path: str   The path to the requirements.txt file
@param install: bool    (Default: True) Whether to install or uninstall packages
@return sucess: bool
'''
def piprequirements_operate(file_path: str, install: bool = True) -> bool:
    install_str: list[str] = ["install", "-U"] if install else ["uninstall", "-y"]
    ret: int = pip_main([*install_str, "-r", file_path])
    return True if ret == 0 else False

'''
Git Repository Information Data Class
'''
@dataclass
class GitRepoInfo:
    __slots__ = ["modifications", "remote_url", "current_commit", "remote_head_commit", "CHANGELOG"]
    modifications: int                  # Number of files modified
    remote_url: str                     # The remote URL of the repo
    current_commit: pygit2.Commit       # Current commit hash of the repo
    remote_head_commit: pygit2.Commit   # Remote Head commit hash of the repo
    CHANGELOG: str                      # The content of the CHANGELOG

    def get_UTC_time(self) -> str:
        dt = datetime.datetime.fromtimestamp(self.current_commit.commit_time + self.current_commit.committer.offset * 60, datetime.timezone.utc)
        return dt.strftime("%Z %Y-%m-%dT%H:%M:%S.%f")
        
    def get_remote_UTC_time(self) -> str:
        dt = datetime.datetime.fromtimestamp(self.remote_head_commit.commit_time + self.remote_head_commit.committer.offset * 60, datetime.timezone.utc)
        return dt.strftime("%Z %Y-%m-%dT%H:%M:%S.%f")

'''
Get the information of the Git repo of the module

@param name: str            The module name
@return info: GitRepoInfo   The Git repository information
        valid: bool         Whether the repo name is valid
'''
def gitrepo_info(name: str) -> tuple[GitRepoInfo, bool]:
    path: str = f"{os.getcwd()}/extensions/{name}"
    repo_path: str = pygit2.discover_repository(path)
    if repo_path == pygit2.discover_repository(os.getcwd()):
        # Not a git repo
        ic()
        return None, False
    repo: pygit2.Repository = pygit2.Repository(
        repo_path
    )
    commit: pygit2.Commit = repo[repo.head.target]
    
    with open(f"{path}/CHANGELOG") as f:
        content: str = f.read()

    return GitRepoInfo(repo.diff("origin/master").stats.files_changed, repo.remotes["origin"].url, commit, repo.revparse("origin/master").from_object, content), True

'''
Get the information of the Kernel Git repo

@return info: GitRepoInfo   The Git repository information
'''
def kernel_gitrepo_info() -> GitRepoInfo:
    repo = pygit2.Repository(
        pygit2.discover_repository(os.getcwd())
    )
    commit: pygit2.Commit = repo[repo.head.target]

    return GitRepoInfo(repo.diff("origin/master").stats.files_changed, repo.remotes["origin"].url, commit, repo.revparse("origin/master").from_object, "")


'''
Pull the kernel git repo from remote "master" branch only.
Note that this only pull changes, it does NOT merge the changes
 if there are local changes and commits.
CC-BY-SA-3.0: https://stackoverflow.com/a/27786533

@return err_code: int   Error code (
    0:  Success
    2:  Git fetch failed
    3:  Not found master branch
)
'''
def kernel_gitrepo_pull() -> int:
    path: str = os.getcwd()
    repo_path: str = pygit2.discover_repository(path)
    ret: int = base_gitrepo_pull(repo_path)
    return ret

