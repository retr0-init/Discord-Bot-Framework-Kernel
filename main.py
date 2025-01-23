import asyncio
import logging
import os
import pathlib
import shutil
import signal
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Optional, Set, Union
from urllib.parse import urlsplit

import aiofiles
import interactions
import pip
import pygit2
from dotenv import load_dotenv
from interactions.client.errors import (
    EmptyMessageException,
    ExtensionLoadException,
    Forbidden,
    HTTPException,
    MessageException,
    NotFound,
)
from interactions.client.utils import code_block
from interactions.ext.paginators import Paginator

load_dotenv()

BASE_DIR: str = os.path.abspath(os.path.dirname(__file__))
LOG_FILE: str = os.path.join(BASE_DIR, "main.log")
GUILD_ID: int = int(os.environ.get("GUILD_ID", "0"))

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter: logging.Formatter = logging.Formatter(
    "%(asctime)s | %(process)d:%(thread)d | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    "%Y-%m-%d %H:%M:%S.%f %z",
)
file_handler: RotatingFileHandler = RotatingFileHandler(
    LOG_FILE, maxBytes=1 << 20, backupCount=1, encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


async def shutdown(signal: signal.Signals, loop: asyncio.AbstractEventLoop) -> None:
    logger.info(f"Received exit signal {signal.name}")
    tasks: Set[asyncio.Task] = {
        t for t in asyncio.all_tasks() if t is not asyncio.current_task()
    }
    logger.info(f"Cancelling {len(tasks)} outstanding tasks")

    for task in tasks:
        task.cancel()

    try:
        done, pending = await asyncio.wait(tasks, timeout=5.0)
        if pending:
            logger.warning(f"{len(pending)} tasks did not complete within timeout")
    except asyncio.CancelledError:
        logger.debug("Shutdown coroutine cancelled")
    finally:
        loop.stop()


def handle_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    msg = context.get("exception", context["message"])
    logger.error(f"Caught exception: {msg}")
    logger.info("Initiating shutdown sequence")
    asyncio.create_task(shutdown(signal.SIGTERM, loop))


try:
    token = next(v for k, v in os.environ.items() if k == "TOKEN")
except StopIteration:
    logger.critical("TOKEN environment variable not set. Terminating")
    sys.exit(1)

client = interactions.Client(
    token=token,
    activity=interactions.Activity(
        name="with `interactions.py`",
        type=interactions.ActivityType.COMPETING,
        created_at=interactions.Timestamp.now(timezone.utc),
    ),
    debug_scope=GUILD_ID,
    intents=interactions.Intents.ALL,
    disable_dm_commands=True,
    auto_defer=True,
)


async def my_check(ctx: interactions.BaseContext) -> bool:
    is_owner = await interactions.is_owner()(ctx)
    has_role = False
    if role_id := os.environ.get("ROLE_ID"):
        has_role = ctx.author.has_role(role_id)
    return bool(is_owner or has_role)


@interactions.listen()
async def on_startup() -> None:
    await client.synchronise_interactions(delete_commands=True)
    logger.info(f"Logged in as {client.user}")


################ Module ################

up_conv_dict: dict = {
    "_": "_u_",
    "/": "_s_",
    ".": "_d_",
    "-": "_h_",
}


def giturl_parse(url: str) -> tuple[str, str, bool]:
    try:
        u = urlsplit(url)
        uns = u.netloc.split(".")

        if any(
            (
                u.scheme != "https",
                not u.netloc,
                uns[-1] != "com",
                len(uns) < 2,
                not u.path.endswith(".git"),
            )
        ):
            return url, "", False

        netloc = ".".join(x for x in uns if x not in {"www", "com"})

        trans = str.maketrans(up_conv_dict)
        netloc = netloc.translate(trans)

        path = u.path[1:-4]
        path = path.translate(trans)

        return url, f"{netloc}__{path}", True

    except Exception:
        return url, "", False


def gitrepo_clone(url: str) -> tuple[str, bool]:
    return next(
        (
            (reponame, True)
            for _, reponame, validated in [giturl_parse(url)]
            if validated
            and not (
                lambda: pygit2.clone_repository(url, f"extensions/{reponame}") or False
            )()
            and True
        ),
        (giturl_parse(url)[1], False),
    )


def base_gitrepo_pull(repo_path: str) -> int:
    try:
        repo = pygit2.Repository(repo_path)
        origin = repo.remotes["origin"]
        if not origin:
            return 2
        origin.fetch()
        remote_master = repo.lookup_reference("refs/remotes/origin/master").target
        repo.checkout_tree(repo.get(remote_master))
        master = repo.lookup_reference("refs/heads/master")
        master.set_target(remote_master)
        repo.head.set_target(remote_master)
        return 0
    except pygit2.GitError:
        return 2
    except KeyError:
        return 3


def gitrepo_pull(name: str) -> int:
    path = f"{os.getcwd()}/extensions/{name}"
    return (
        lambda p=pygit2.discover_repository(path): (
            1 if p == pygit2.discover_repository(os.getcwd()) else base_gitrepo_pull(p)
        )
    )()


def gitrepo_delete(name: str) -> None:
    path = f"{os.getcwd()}/extensions/{name}"
    if not is_gitrepo(name):
        return
    shutil.rmtree.avoids_symlink_attacks and print(
        "This system is prone to symlink attacks. Be aware!"
    )
    try:
        shutil.rmtree(
            path,
            ignore_errors=False,
            onerror=lambda f, p, e: print(f"Error: {p} - {e}"),
        )
    except OSError:
        pass


def is_gitrepo(name: str) -> bool:
    return not (
        pygit2.discover_repository(f"{(lambda: os.getcwd())()}/extensions/{name}")
        == pygit2.discover_repository(os.getcwd())
    )


if hasattr(pip, "main"):
    pip_main = pip.main
else:
    pip_main = pip._internal.main


def pipmodule_operate(*packages: str, install: bool = True) -> bool:
    args = ["install"] if install else ["uninstall", "-y"]
    args.extend(packages)
    return not bool(pip_main(args))


def piprequirements_operate(file_path: str, install: bool = True) -> bool:
    return not pip_main(
        [*(("install", "-U") if install else ("uninstall", "-y")), "-r", file_path]
    )


@dataclass
class GitRepoInfo:
    modifications: int
    remote_url: str
    current_commit: pygit2.Commit
    remote_head_commit: pygit2.Commit
    CHANGELOG: str

    @staticmethod
    def _get_timestamp(commit: pygit2.Commit) -> float:
        return commit.commit_time + commit.committer.offset * 60

    def get_utc_time(self) -> str:
        return datetime.fromtimestamp(
            self._get_timestamp(self.current_commit), timezone.utc
        ).strftime("%Z %Y-%m-%dT%H:%M:%S.%f")

    def get_remote_utc_time(self) -> str:
        return datetime.fromtimestamp(
            self._get_timestamp(self.remote_head_commit), timezone.utc
        ).strftime("%Z %Y-%m-%dT%H:%M:%S.%f")


def gitrepo_info(name: str) -> tuple[GitRepoInfo, bool]:
    path: str = f"{os.getcwd()}/extensions/{name}"
    repo_path: str = pygit2.discover_repository(path)
    if repo_path == pygit2.discover_repository(os.getcwd()):
        return (
            GitRepoInfo(
                modifications=0,
                remote_url="",
                current_commit=None,
                remote_head_commit=None,
                CHANGELOG="",
            ),
            False,
        )
    repo: pygit2.Repository = pygit2.Repository(repo_path)
    commit: pygit2.Commit = repo[repo.head.target]

    with open(f"{path}/CHANGELOG") as f:
        content: str = f.read()

    return (
        GitRepoInfo(
            repo.diff("origin/master").stats.files_changed,
            repo.remotes["origin"].url,
            commit,
            repo.revparse("origin/master").from_object,
            content,
        ),
        True,
    )


def kernel_gitrepo_info() -> GitRepoInfo:
    repo = pygit2.Repository(pygit2.discover_repository(os.getcwd()))
    return GitRepoInfo(
        repo.diff("origin/master").stats.files_changed,
        repo.remotes["origin"].url,
        repo[repo.head.target],
        repo.revparse("origin/master").from_object,
        "",
    )


def kernel_gitrepo_pull() -> int:
    return base_gitrepo_pull(pygit2.discover_repository(os.getcwd()))


################ Model ################


class EmbedColor(IntEnum):

    OFF = 0x5D5A58
    FATAL = 0xFF4343
    ERROR = 0xE81123
    WARN = 0xFFB900
    INFO = 0x0078D7
    DEBUG = 0x00B7C3
    TRACE = 0x8E8CD8
    ALL = 0x0063B1


################ View ################


async def create_embed(
    bot: interactions.Client,
    title: str,
    description: str = "",
    color: EmbedColor = EmbedColor.INFO,
) -> interactions.Embed:
    guild = await bot.fetch_guild(GUILD_ID)
    return interactions.Embed(
        title=title,
        description=description,
        color=int(color.value),
        timestamp=interactions.Timestamp.now(timezone.utc),
        footer=interactions.EmbedFooter(
            text=guild.name if guild.icon else "鍵政大舞台",
            icon_url=str(guild.icon.url) if guild.icon else None,
        ),
    )


async def send_response(
    bot: interactions.Client,
    ctx: interactions.InteractionContext,
    title: str,
    message: str,
    color: EmbedColor,
) -> None:
    await ctx.send(
        embed=await create_embed(bot, title, message, color),
        ephemeral=True,
    )


async def send_error(
    bot: interactions.Client,
    ctx: interactions.InteractionContext,
    message: str,
) -> None:
    await send_response(bot, ctx, "Error", message, EmbedColor.ERROR)


async def send_success(
    bot: interactions.Client,
    ctx: interactions.InteractionContext,
    message: str,
) -> None:
    await send_response(bot, ctx, "Success", message, EmbedColor.INFO)


################ Kernel functions START ################


kernel_base: interactions.SlashCommand = interactions.SlashCommand(
    name="kernel", description="Bot Framework Kernel Commands"
)


kernel_module: interactions.SlashCommand = kernel_base.group(
    name="module", description="Bot Framework Kernel Module Commands"
)


kernel_review: interactions.SlashCommand = kernel_base.group(
    name="review", description="Bot Framework Kernel Review Commands"
)


dm_messages: dict[str, list[interactions.Message]] = dict()


async def _get_key_members(
    ctx: interactions.SlashContext,
) -> list[Union[interactions.Member, interactions.User]]:
    role: Optional[interactions.Role] = (
        await ctx.guild.fetch_role(os.environ.get("ROLE_ID"))
        if os.environ.get("ROLE_ID")
        else None
    )
    key_members: list[Union[interactions.Member, interactions.User]] = (
        [] if role is None else role.members
    )
    if client.owner not in key_members:
        key_members.append(client.owner)
    return key_members


async def _dm_key_members(
    ctx: interactions.SlashContext,
    msg: Optional[str] = None,
    *,
    embeds: Optional[list[interactions.Embed]] = None,
    components: Optional[list[interactions.ComponentType]] = None,
    custom_id: Optional[str] = None,
) -> None:
    key_members: list[Union[interactions.Member, interactions.User]] = (
        await _get_key_members(ctx)
    )
    dm_msg: list[interactions.Message] = []
    for key_member in key_members:
        try:
            _msg_to_send: interactions.Message = await key_member.send(
                content=msg, embeds=embeds, components=components
            )
        except (EmptyMessageException, NotFound, Forbidden, HTTPException) as e:
            logger.error(f"Failed to send DM: {e}")
        else:
            dm_msg.append(_msg_to_send)
    if custom_id is not None:
        dm_messages[custom_id] = dm_msg


async def _dm_key_members_delete(custom_id: str) -> None:
    if custom_id not in dm_messages:
        logger.error(f"Direct message with custom_id {custom_id} not found.")
        return
    dm_msg: list[interactions.Message] = dm_messages[custom_id]
    for msg in dm_msg:
        try:
            await msg.delete()
        except (MessageException, NotFound, Forbidden) as e:
            logger.error(f"Failed to delete direct message: {e}")


@kernel_review.subcommand("reboot", sub_cmd_description="Reboot the bot")
@interactions.check(my_check)
@interactions.max_concurrency(interactions.Buckets.GUILD, 1)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def cmd_internal_reboot(ctx: interactions.SlashContext) -> None:
    await ctx.defer(ephemeral=True)
    executor: interactions.Member = ctx.author
    embed = await create_embed(
        bot=client,
        title="Bot Reboot Initiated",
        description=f"{executor.mention} has initiated a bot reboot.",
    )
    embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)
    embed.set_footer(text=client.user.display_name, icon_url=client.user.avatar_url)

    await _dm_key_members(ctx, embeds=[embed])
    await send_success(
        client,
        ctx,
        "Bot reboot initiated. The bot will be back online shortly.",
    )

    with open("kernel_flag/reboot", "a", buffering=1) as f:
        f.write(f"Rebooted at {datetime.now(timezone.utc).ctime()}")


@kernel_module.subcommand(
    "load", sub_cmd_description="Load module from Git repo with HTTPS"
)
@interactions.slash_option(
    name="url",
    description="HTTPS URL to module",
    required=True,
    opt_type=interactions.OptionType.STRING,
)
@interactions.check(my_check)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def kernel_module_load(ctx: interactions.SlashContext, url: str) -> None:

    async def _delete_message(mesg: interactions.Message) -> None:
        try:
            await mesg.delete()
        except (MessageException, NotFound, Forbidden) as e:
            logger.warning(f"Failed to delete message: {e}")

    await ctx.defer(ephemeral=True)
    executor: interactions.Member = ctx.author

    embed = await create_embed(
        bot=client,
        title="Module Load",
        description=f"{executor.mention} is attempting to load module from {url}.",
    )
    embed.url = url
    embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)
    embed.set_footer(text=client.user.display_name, icon_url=client.user.avatar_url)

    await _dm_key_members(ctx, embeds=[embed])
    logger.debug("Starting module load process")

    msg: interactions.Message = await ctx.send("Loading new module. Please wait.")
    git_url, parsed, validated = giturl_parse(url)

    if not validated:
        await send_error(
            client,
            ctx,
            "Invalid Git repository URL. The web URL must use HTTPS format (e.g., `https://github.com/user/repo.git`).",
        )
        return await _delete_message(msg)

    if os.path.isdir(os.path.join(os.getcwd(), "extensions", parsed)):
        await send_error(
            client,
            ctx,
            f"Module `{parsed}` is already loaded. Please unload it first using `/kernel module unload` if you want to reload it.",
        )
        return await _delete_message(msg)

    module, clone_validated = gitrepo_clone(git_url)
    if not clone_validated:
        logger.warning(f"Failed to clone module {module}")
        await send_error(
            client,
            ctx,
            f"Failed to clone module `{module}`. Please verify the repository exists and is accessible.",
        )
        return await _delete_message(msg)

    requirements_path = os.path.join(
        os.getcwd(), "extensions", module, "requirements.txt"
    )

    if not os.path.exists(requirements_path):
        gitrepo_delete(module)
        logger.warning(f"Module {module} missing requirements.txt")
        await send_error(
            client,
            ctx,
            f"Module `{module}` is missing required `requirements.txt` file. Please ensure the module follows the correct structure.",
        )
        return await _delete_message(msg)

    if not piprequirements_operate(requirements_path):
        logger.warning(f"Failed to install requirements for module {module}")
        await send_error(
            client,
            ctx,
            f"Failed to install dependencies for module `{module}`. Please check the `requirements.txt` file for errors.",
        )
        return await _delete_message(msg)

    try:
        client.reload_extension(f"extensions.{module}.main")
        logger.info(f"Loaded extension extensions.{module}.main")
        # async with client._sync_lock:
        #     await client.synchronise_interactions(delete_commands=True)
        # await send_success(
        #     client,
        #     ctx,
        #     f"Loaded module `extensions.{module}.main`.",
        # )
        try:
            await msg.edit(content=f"Loaded module `extensions.{module}.main`.")
        except interactions.errors.Forbidden:
            logger.warning("Missing permissions to edit message")
            await send_success(
                client,
                ctx,
                f"Loaded module `extensions.{module}.main`.",
            )
    except Exception as e:
        logger.exception(f"Failed to load extension {module}", exc_info=e)
        await client.synchronise_interactions(delete_commands=True)
        gitrepo_delete(module)
        await send_error(
            client,
            ctx,
            f"Failed to load module `{module}`. The repository has been removed due to loading errors. Error: {str(e)}.",
        )
        await _delete_message(msg)

    logger.debug("Module load process completed")


def kernel_module_option_module() -> Callable:
    return lambda func: (
        interactions.slash_option(
            name="module",
            description="Name of the loaded module (use list command to view available modules)",
            required=True,
            opt_type=interactions.OptionType.STRING,
            autocomplete=True,
        )(func)
    )


@kernel_module.subcommand("unload", sub_cmd_description="Unload module")
@kernel_module_option_module()
@interactions.check(my_check)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def kernel_module_unload(ctx: interactions.SlashContext, module: str) -> None:
    await ctx.defer(ephemeral=True)
    executor: interactions.Member = ctx.author
    info, _ = gitrepo_info(module)

    embed = await create_embed(
        client,
        "Module Unload",
        f"{executor.mention} is unloading module `{module}`. Current commit: `{info.current_commit.id}` from {info.remote_url}.",
        EmbedColor.ERROR,
    )
    embed.url = info.remote_url
    embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)

    await _dm_key_members(ctx, embeds=[embed])

    try:
        client.unload_extension(f"extensions.{module}.main")
        await client.synchronise_interactions(delete_commands=True)
    except Exception as e:
        await send_error(
            client,
            ctx,
            f"Failed to unload module `{module}` cleanly, but proceeding with deletion. Error: {str(e)}.",
        )
    else:
        await send_success(
            client,
            ctx,
            f"Unloaded module `{module}` and cleaned up resources.",
        )
    finally:
        try:
            gitrepo_delete(module)
        except Exception:
            logger.error(f"Failed to delete module directory {module}")


@kernel_module.subcommand("list", sub_cmd_description="List loaded modules")
async def kernel_module_list(ctx: interactions.SlashContext) -> None:
    modules = {
        module
        for module in os.scandir("extensions")
        if module.is_dir() and module.name != "__pycache__" and is_gitrepo(module.name)
    }

    if not modules:
        await send_error(
            client,
            ctx,
            "No modules are currently loaded. Use `/kernel module load` to add new modules.",
        )
        return

    embed = await create_embed(client, "Loaded Modules")

    for module in modules:
        info, _ = gitrepo_info(module.name)
        commit_id = str(info.current_commit.id)
        display_name = (
            module.name.split("_s_")[-1] if "_s_" in module.name else module.name
        )
        embed.add_field(
            name=display_name,
            value=f"- Commit: `{commit_id[:7]}`\n- URL: {info.remote_url}",
            inline=True,
        )

    paginator = Paginator(
        client,
        pages=[embed],
        timeout_interval=180,
        show_callback_button=True,
        show_select_menu=True,
        show_back_button=True,
        show_next_button=True,
        show_first_button=True,
        show_last_button=True,
        wrong_user_message="Only the user who requested this list can control the pagination.",
        hide_buttons_on_stop=True,
    )

    await paginator.send(ctx)


@kernel_module.subcommand("update", sub_cmd_description="Update the module")
@kernel_module_option_module()
@interactions.check(my_check)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def kernel_module_update(ctx: interactions.SlashContext, module: str) -> None:
    await ctx.defer(ephemeral=True)
    executor: interactions.Member = ctx.author
    info, _ = gitrepo_info(module)

    embed = await create_embed(
        client,
        "Module Update",
        f"{executor.mention} is updating module `{module}`.",
        EmbedColor.WARN,
    )
    embed.url = info.remote_url
    embed.add_field(
        name="Current Commit", value=f"`{info.current_commit.id}`", inline=True
    )
    embed.add_field(
        name="Target Commit", value=f"`{info.remote_head_commit.id}`", inline=True
    )
    embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)

    await _dm_key_members(ctx, embeds=[embed])

    if not (module_dir := pathlib.Path(f"extensions/{module}")).is_dir():
        await send_error(
            client,
            ctx,
            f"Module `{module}` does not exist. Use `/kernel module list` to see available modules.",
        )
        return

    if (err := gitrepo_pull(module)) != 0:
        error_reasons = [
            "Not a git repository",
            "Failed to fetch from remote",
            "Master branch not found",
        ]
        error_msg = next(
            (r for i, r in enumerate(error_reasons) if i == err - 1), "Unknown error"
        )
        await send_error(
            client,
            ctx,
            f"Module update failed: {error_msg}. Please try again or contact an administrator.",
        )
        return

    requirements_path = module_dir / "requirements.txt"
    if not requirements_path.exists():
        await send_error(
            client,
            ctx,
            "Missing `requirements.txt`. Update aborted. Please ensure the module has all required files.",
        )
        return

    piprequirements_operate(str(requirements_path))
    client.reload_extension(f"extensions.{module}.main")
    await client.synchronise_interactions(delete_commands=True)

    changelog_path = module_dir / "CHANGELOG"
    cl = "No changelog provided"
    if changelog_path.is_file():
        async with aiofiles.open(changelog_path) as f:
            cl = await f.read()

    result_embed = await create_embed(
        client,
        "Module Update Complete",
        f"Updated module `{module}` to the latest version.",
    )
    result_embed.add_field(
        name="Changelog",
        value=cl[:1000] + "..." if len(cl) > 1000 else cl,
        inline=True,
    )

    paginator = Paginator(
        client,
        pages=[result_embed],
        timeout_interval=180,
        show_callback_button=True,
        show_select_menu=True,
        show_back_button=True,
        show_next_button=True,
        show_first_button=True,
        show_last_button=True,
        wrong_user_message="Only the user who requested this update can control the pagination.",
        hide_buttons_on_stop=True,
    )

    await paginator.send(ctx)


@kernel_module.subcommand("info", sub_cmd_description="Show module information")
@kernel_module_option_module()
async def kernel_module_info(ctx: interactions.SlashContext, module: str) -> None:
    await ctx.defer(ephemeral=True)
    info, valid = gitrepo_info(module)
    if not valid:
        await send_error(
            client,
            ctx,
            "Module not found. Please verify the module name and ensure it exists in the extensions directory. Use the `/kernel module list` command to see all available modules.",
        )
        return

    modifications = info.modifications > 0
    result_embed = await create_embed(
        client,
        "Module Information",
        f"Details for module `{module}`",
        EmbedColor.ERROR if modifications else EmbedColor.INFO,
    )
    result_embed.url = info.remote_url

    result_embed.add_field(name="Local Changes Status", value=(
        "Modified - Local changes detected"
        if modifications
        else "Clean - No local modifications"
    ))

    result_embed.add_field(
        name="Current Local Commit",
        value=f"- ID: `{info.current_commit.id}`\n- Timestamp: `{info.get_utc_time()}`",
        inline=True,
    )

    result_embed.add_field(
        name="Latest Remote Commit",
        value=f"- ID: `{info.remote_head_commit.id}`\n- Timestamp: `{info.get_remote_utc_time()}`",
        inline=True,
    )

    changelog_content = (
        info.CHANGELOG[:1000] + "..." if len(info.CHANGELOG) > 1000 else info.CHANGELOG
    )
    if not changelog_content.strip():
        changelog_content = "No changelog information available"

    result_embed.add_field(name="Recent Changes", value=code_block(changelog_content, "py"))

    paginator = Paginator(
        client,
        pages=[result_embed],
        timeout_interval=180,
        show_callback_button=True,
        show_select_menu=True,
        show_back_button=True,
        show_next_button=True,
        show_first_button=True,
        show_last_button=True,
        wrong_user_message="Only the user who requested this information can navigate through these pages.",
        hide_buttons_on_stop=True,
    )

    await paginator.send(ctx)


@kernel_module_unload.autocomplete("module")
@kernel_module_update.autocomplete("module")
@kernel_module_info.autocomplete("module")
async def kernel_module_option_module_autocomplete(
    ctx: interactions.AutocompleteContext,
):
    module_option_input: str = ctx.input_text
    modules: list[str] = [
        i
        for i in os.listdir("extensions")
        if os.path.isdir(f"extensions/{i}") and i != "__pycache__" and is_gitrepo(i)
    ]
    modules_auto: list[str] = [i for i in modules if module_option_input in i]

    await ctx.send(choices=[{"name": i, "value": i} for i in modules_auto][:25])


gDownloading: bool = False


@kernel_review.subcommand(
    "download", sub_cmd_description="Download current running code in tarball"
)
@interactions.max_concurrency(interactions.Buckets.GUILD, 2)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def kernel_review_download(ctx: interactions.SlashContext):
    global gDownloading
    if gDownloading:
        return await ctx.send(
            "There is already a download task running! Please run it later :)",
            ephemeral=True,
        )
    gDownloading = True
    await ctx.defer(ephemeral=True)
    try:

        def compress_temp(filename: str) -> None:
            path = pathlib.Path(__file__).parent.resolve()
            excluded = {".", "venv", "__pycache__"}
            with tarfile.open(filename, "w:gz", compresslevel=9) as tar:
                for fn in os.scandir(path):
                    if not any(part in excluded for part in fn.path.split(os.sep)):
                        tar.add(
                            fn.path,
                            arcname=fn.name,
                            filter=lambda ti: (
                                ti
                                if not any(
                                    part in excluded for part in ti.name.split("/")
                                )
                                else None
                            ),
                        )

        async with aiofiles.tempfile.NamedTemporaryFile(mode="wb", suffix=".tar.gz", prefix="Discord-Bot-Framework_") as tmp:
            compress_temp(tmp.name)
            await ctx.send("Current code that is running as attached", file=tmp.name)
    finally:
        gDownloading = False


@kernel_review.subcommand("info", sub_cmd_description="Show the Kernel information")
async def kernel_review_info(ctx: interactions.SlashContext):
    await ctx.defer(ephemeral=True)
    info = kernel_gitrepo_info()

    modifications = info.modifications > 0
    result_embed = await create_embed(
        client,
        "Kernel Information",
        "Details for Discord-Bot-Framework-Kernel",
        EmbedColor.ERROR if modifications else EmbedColor.INFO,
    )
    result_embed.url = info.remote_url

    result_embed.add_field(name="Local Changes Status", value=(
        "Modified - Local changes detected"
        if modifications
        else "Clean - No local modifications"
    ))

    result_embed.add_field(
        name="Current Local Commit",
        value=f"- ID: `{info.current_commit.id}`\n- Timestamp: `{info.get_utc_time()}`",
        inline=True,
    )

    result_embed.add_field(
        name="Latest Remote Commit",
        value=f"- ID: `{info.remote_head_commit.id}`\n- Timestamp: `{info.get_remote_utc_time()}`",
        inline=True,
    )

    paginator = Paginator(
        client,
        pages=[result_embed],
        timeout_interval=180,
        show_callback_button=True,
        show_select_menu=True,
        show_back_button=True,
        show_next_button=True,
        show_first_button=True,
        show_last_button=True,
        wrong_user_message="Only the user who requested this information can navigate through these pages.",
        hide_buttons_on_stop=True,
    )

    await paginator.send(ctx)


@kernel_review.subcommand("update", sub_cmd_description="Update the kernel")
@interactions.max_concurrency(interactions.Buckets.GUILD, 1)
@interactions.check(my_check)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def kernel_review_update(ctx: interactions.SlashContext) -> None:
    await ctx.defer(ephemeral=True)
    executor: interactions.Member = ctx.author
    info = kernel_gitrepo_info()

    embed = await create_embed(
        client,
        "Kernel Update",
        f"{executor.mention} is updating the kernel.",
        EmbedColor.WARN,
    )
    embed.url = info.remote_url
    embed.add_field(
        name="Current Commit", value=f"`{info.current_commit.id}`", inline=True
    )
    embed.add_field(
        name="Target Commit", value=f"`{info.remote_head_commit.id}`", inline=True
    )
    embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)

    await _dm_key_members(ctx, embeds=[embed])

    if (err := kernel_gitrepo_pull()) != 0:
        error_reasons = [
            "Not a git repository",
            "Failed to fetch from remote",
            "Master branch not found",
        ]
        error_msg = next(
            (r for i, r in enumerate(error_reasons) if i == err - 1), "Unknown error"
        )
        await send_error(
            client,
            ctx,
            f"Kernel update failed: {error_msg}. Please try again or contact an administrator.",
        )
        return

    requirements_path = pathlib.Path("requirements.txt")
    if not requirements_path.exists():
        await send_error(
            client,
            ctx,
            "Missing `requirements.txt`. Update aborted. Please ensure all required files exist.",
        )
        return

    piprequirements_operate(str(requirements_path))

    result_embed = await create_embed(
        client,
        "Kernel Update Complete",
        "Updated kernel to the latest version.",
    )

    paginator = Paginator(
        client,
        pages=[result_embed],
        timeout_interval=180,
        show_callback_button=True,
        show_select_menu=True,
        show_back_button=True,
        show_next_button=True,
        show_first_button=True,
        show_last_button=True,
        wrong_user_message="Only the user who requested this update can control the pagination.",
        hide_buttons_on_stop=True,
    )

    await paginator.send(ctx)


################ Kernel functions END ################


async def main_main():
    extensions = {
        *map(
            lambda f: f"extensions.{f[:-3]}",
            filter(
                lambda x: x.endswith(".py") and not x.startswith("_"),
                os.listdir("extensions"),
            ),
        ),
        *(
            f"extensions.{d}.main"
            for d in filter(
                lambda x: os.path.isdir(f"extensions/{x}")
                and x != "__pycache__"
                and is_gitrepo(x),
                os.listdir("extensions"),
            )
        ),
    }

    try:
        client.load_extension("interactions.ext.jurigged")
    except ExtensionLoadException as e:
        logger.exception("Failed to load jurigged extension", exc_info=e)

    for ext in extensions:
        try:
            client.load_extension(ext)
            logger.info(f"Loaded extension {ext}")
        except ExtensionLoadException as e:
            logger.exception(f"Failed to load extension {ext}", exc_info=e)

    await client.astart()


if __name__ == "__main__":
    asyncio.run(main_main())
