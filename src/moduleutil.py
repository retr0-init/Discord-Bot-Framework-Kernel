import pygit2
import shutil
import os
from urllib.parse import urlsplit
from threading import Thread
import pip


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

    # Catch all errors
    uns: list[str] = u.netloc.split('.')
    if u.scheme != 'https':
        return url, "", False
    elif u.netloc == '':
        return url, "", False
    elif uns[-1] != 'com' or len(uns) < 2:
        return url, "", False
    elif u.path.split('.')[-1] != 'git':
        return url, "", False

    # Parse the net location
    netloc: str = '.'.join([_ for _ in uns if _ != 'www' or _ != 'com'])
    for _ in up_conv_dict:
        netloc = netloc.replace(_, up_conv_dict[_])

    # Parse the path to git repo
    path: str = u.path[1:-4]
    for _ in up_conv_dict:
        path = path.replace(_, up_conv_dict[_])

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
        pygit2.clone_repository(url, reponame)
    except pygit2.GitError:
        return reponame, False
    return reponame, True


'''
Pull the git repo from remote "master" branch only.
Note that this only pull changes, it does NOT merge the changes
 if there are local changes and commits.
CC-BY-SA-3.0: https://stackoverflow.com/a/27786533

@param name: str    The module name of the repo
'''
def gitrepo_pull(name: str) -> None:
    path: str = f"{os.getcwd()}/extensions/{name}"
    repo: pygit2.Repository = pygit2.Repository(
        pygit2.discover_repository(path)
    )
    repo.remotes["origin"].fetch()
    remote_master_id: str = repo.lookup_reference('refs/remotes/origin/master').target
    repo.checkout_tree(repo.get(remote_master_id))
    master_ref: pygit2.Reference = repo.lookup_reference('refs/heads/master')
    master_ref.set_target(remote_master_id)
    repo.head.set_target(remote_master_id)


'''
Remove the unloaded git repo.

@param name: str    The module of the repo
'''
def gitrepo_delete(name: str) -> None:
    path: str = f"{os.getcwd()}/extensions/{name}"
    # Check whether the path is a git repo
    if pygit2.discover_repository(path) == "":
        return
    if shutil.rmtree.avoids_symlink_attacks:
        print("This system is prone to symlink attacks. Be aware!")
    try:
        shutil.rmtree(path)
    except OSError as e:
        print(f"Error: {e.filename} - {e.strerror}")


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
def piprequirements_operate(file_path: str, install: bool = True):
    install_str: tuple[str] = ("install") if install else ("uninstall", "-y")
    ret: int = pip_main([*install_str, "-r", file_path])
    return True if ret == 0 else False
