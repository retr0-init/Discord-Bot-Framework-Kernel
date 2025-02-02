import asyncio
import logging
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Optional, Set, Union
from urllib.parse import urlsplit

import aiofiles
import aioshutil
import interactions
import pip
import pygit2
from dotenv import load_dotenv
from interactions.client.errors import (
    BotException,
    CommandException,
    EventLocationNotProvided,
    ExtensionException,
    ForeignWebhookException,
    GatewayNotFound,
    HTTPException,
    InteractionException,
    LoginError,
    MessageException,
    ThreadException,
    TooManyChanges,
    VoiceAlreadyConnected,
    VoiceConnectionTimeout,
    VoiceNotConnected,
    VoiceWebSocketClosed,
    WebSocketClosed,
    WebSocketRestart,
)
from interactions.client.utils import code_block
from interactions.ext.paginators import Paginator

load_dotenv()

BASE_DIR: str = os.path.abspath(os.path.dirname(__file__))
LOG_FILE: str = os.path.join(BASE_DIR, "main.log")
GUILD_ID: int = int(os.environ.get("GUILD_ID", "0"))

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s | %(process)d - %(processName)s | %(thread)d - %(threadName)s | %(taskName)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(pathname)s | %(message)s",
    "%Y-%m-%d %H:%M:%S,%f %z",
)
file_handler: RotatingFileHandler = RotatingFileHandler(
    LOG_FILE, maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


async def graceful_shutdown(
    signal: signal.Signals, loop: asyncio.AbstractEventLoop
) -> None:
    logger.info(f"Received exit signal {signal.name}")
    tasks: Set[asyncio.Task] = {
        t for t in asyncio.all_tasks() if t is not asyncio.current_task()
    }
    logger.info(f"Cancelling {len(tasks)} outstanding tasks")

    [task.cancel() for task in tasks]

    try:
        _, pending = await asyncio.shield(
            asyncio.wait(tasks, timeout=5.0, return_when=asyncio.ALL_COMPLETED)
        )
        if pending:
            [t.cancel() for t in pending]
            logger.warning(f"{len(pending)} tasks forcefully terminated")

    except (asyncio.CancelledError, RuntimeError, OSError) as e:
        logger.error(f"Shutdown error: {type(e).__name__}: {e}")

    finally:
        try:
            for task in tasks:
                if not task.done():
                    task.cancel()

            loop.call_soon_threadsafe(lambda _: loop.stop(), ())

        except Exception as e:
            logger.exception(f"Loop shutdown error: {type(e).__name__}: {e}")
            loop.stop()


def handle_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    msg: str = context.get("exception", context["message"])
    logger.error(f"Caught exception: {msg}")
    logger.info("Initiating shutdown sequence")
    asyncio.create_task(graceful_shutdown(signal.SIGTERM, loop))


try:
    token = next(v for k, v in os.environ.items() if k == "TOKEN")
except (NameError, TypeError, ValueError, StopIteration):
    logger.error("TOKEN environment variable not set or invalid. Terminating")
    sys.exit(1)

try:
    client = interactions.Client(
        token=token,
        activity=interactions.Activity(
            name="with interactions.py",
            type=interactions.ActivityType.COMPETING,
            created_at=interactions.Timestamp.now(timezone.utc),
        ),
        debug_scope=GUILD_ID,
        intents=interactions.Intents.ALL,
        disable_dm_commands=True,
        auto_defer=True,
    )
except Exception as e:
    logger.exception(f"Critical initialization failure: {type(e).__name__}: {e}")
    sys.exit(1)


async def role_check(ctx: interactions.BaseContext) -> bool:
    try:
        return any(
            [
                await interactions.is_owner()(ctx),
                (
                    ctx.author.has_role(os.environ.get("ROLE_ID", ""))
                    if os.environ.get("ROLE_ID")
                    else False
                ),
            ]
        )
    except (AttributeError, TypeError, ValueError) as e:
        logger.error(f"Failed to check roles: {e}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error in role check: {e}")
        return False


@interactions.listen()
async def on_startup() -> None:
    try:
        await client.synchronise_interactions(delete_commands=True)
        logger.info(f"Logged in as {client.user}")
    except Exception as e:
        logger.error(f"Unexpected error during startup: {e}")


################ Module ################


up_conv_dict: dict = {
    "_": "_u_",
    "/": "_s_",
    ".": "_d_",
    "-": "_h_",
}


def parse_git_url(url: str) -> tuple[str, str, bool]:
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
    except (AttributeError, ValueError) as e:
        logger.error(f"Failed to parse git URL: {e}")
        return url, "", False
    except Exception as e:
        logger.exception(f"Unexpected error parsing git URL: {e}")
        return url, "", False


def clone_git_repo(url: str) -> tuple[str, bool]:
    try:
        url_data = parse_git_url(url)
        if not url_data[2]:
            return url_data[1], False

        reponame = url_data[1]
        repo_path = f"extensions/{reponame}"

        pygit2.clone_repository(url, repo_path)
        return reponame, True

    except (ImportError, RuntimeError, OSError) as e:
        logger.error(
            f"{'Git operation' if isinstance(e, (ImportError, RuntimeError)) else 'OS'} error during clone: {e}"
        )
        return "", False
    except Exception as e:
        logger.exception(f"Unexpected error cloning repository: {e}")
        return "", False


def pull_git_repo_base(repo_path: str) -> int:
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


def pull_git_repo(name: str) -> int:
    try:
        path = f"{os.getcwd()}/extensions/{name}"
        repo_path = pygit2.discover_repository(path)
        if repo_path == pygit2.discover_repository(os.getcwd()):
            return 1
        return pull_git_repo_base(repo_path)
    except (ImportError, RuntimeError, OSError) as e:
        logger.error(
            f"{'Git operation' if isinstance(e, (ImportError, RuntimeError)) else 'OS'} error during pull: {e}"
        )
        return 2
    except Exception as e:
        logger.exception(f"Unexpected error pulling repository: {e}")
        return 2


def delete_git_repo(name: str) -> None:
    path = os.path.join(os.getcwd(), "extensions", name)

    if not validate_git_repo(name):
        return

    if shutil.rmtree.avoids_symlink_attacks:
        logger.warning("System vulnerable to symlink attacks")

    def handle_error(_: Callable, path: str, exc_info: tuple) -> None:
        logger.error(f"Delete error at {path}: {exc_info[1]}")

    try:
        shutil.rmtree(path, onerror=handle_error)
    except OSError as e:
        logger.error(f"Failed to delete repository: {e}")
        raise
    except Exception as e:
        logger.exception(f"Unexpected error deleting repository: {e}")
        raise


def validate_git_repo(name: str) -> bool:
    cwd = os.getcwd()
    return pygit2.discover_repository(
        f"{cwd}/extensions/{name}"
    ) != pygit2.discover_repository(cwd)


if hasattr(pip, "main"):
    pip_main = pip.main
else:
    pip_main = pip._internal.main


def execute_pip_operation(*packages: str, install: bool = True) -> bool:
    return not bool(
        pip_main([*(("install",) if install else ("uninstall", "-y")), *packages])
    )


def execute_pip_requirements(file_path: str, install: bool = True) -> bool:
    try:
        return not pip_main(
            [*(("install", "-U") if install else ("uninstall", "-y")), "-r", file_path]
        )
    except (subprocess.CalledProcessError, OSError) as e:
        logger.error(
            f"{'Pip' if isinstance(e, subprocess.CalledProcessError) else 'OS'} error: {e}"
        )
        return False
    except Exception as e:
        logger.exception(
            f"Unexpected error {'installing' if install else 'uninstalling'} requirements: {e}"
        )
        return False


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


def get_git_repo_info(name: str) -> tuple[GitRepoInfo, bool]:
    path: str = f"{os.path.join(os.getcwd(), 'extensions', name)}"
    repo_path: str = pygit2.discover_repository(path)
    cwd_repo: str = pygit2.discover_repository(os.getcwd())

    if repo_path == cwd_repo:
        return GitRepoInfo(0, "", None, None, ""), False

    repo: pygit2.Repository = pygit2.Repository(repo_path)
    origin = repo.remotes["origin"]
    master_ref = repo.revparse("origin/master")

    try:
        content: str = open(os.path.join(path, "CHANGELOG")).read()
    except (IOError, OSError):
        content = ""

    return (
        GitRepoInfo(
            modifications=repo.diff("origin/master").stats.files_changed,
            remote_url=origin.url,
            current_commit=repo[repo.head.target],
            remote_head_commit=master_ref.from_object,
            CHANGELOG=content,
        ),
        True,
    )


def get_kernel_repo_info() -> GitRepoInfo:
    repo: pygit2.Repository = pygit2.Repository(pygit2.discover_repository(os.getcwd()))
    master_ref = repo.revparse("origin/master")
    return GitRepoInfo(
        *(
            repo.diff("origin/master").stats.files_changed,
            repo.remotes.__getitem__("origin").url,
            repo.__getitem__(repo.head.target),
            master_ref.from_object,
            str(),
        )
    )


def pull_kernel_repo() -> int:
    return pull_git_repo_base(next(iter([pygit2.discover_repository(os.getcwd())])))


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


################ View functions ################


async def create_embed(
    bot: interactions.Client,
    title: str,
    description: Optional[str] = None,
    color: Optional[EmbedColor] = EmbedColor.INFO,
) -> interactions.Embed:
    try:
        embed = interactions.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=interactions.Timestamp.now(timezone.utc),
        )

        if bot.user:
            embed.set_author(name=bot.user.display_name, icon_url=bot.user.avatar_url)

        if client.user:
            embed.set_footer(
                text=client.user.display_name, icon_url=client.user.avatar_url
            )

        return embed
    except (AttributeError, ValueError, TypeError) as e:
        logger.error("Failed to create embed: %s", e)
        raise
    except Exception as e:
        logger.exception("Unexpected error creating embed: %s", e)
        raise


async def send_response(
    bot: interactions.Client,
    ctx: interactions.InteractionContext,
    title: str,
    message: str,
    color: EmbedColor,
) -> None:
    embed = await create_embed(bot, title, message, color)
    await ctx.send(embed=embed, ephemeral=True)


async def send_error(
    bot: interactions.Client,
    ctx: interactions.SlashContext,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    try:
        embed = await create_embed(bot, "Error", message, EmbedColor.ERROR)
        await ctx.send(embeds=[embed], ephemeral=ephemeral)
    except InteractionException as e:
        logger.error(f"Failed to send error message: {str(e)}")
    except (AttributeError, ValueError, TypeError) as e:
        logger.error(f"Failed to create error embed: {str(e)}")
    except Exception as e:
        logger.exception(f"Unexpected error sending error message: {str(e)}")


async def send_success(
    bot: interactions.Client,
    ctx: interactions.SlashContext,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    try:
        embed = await create_embed(bot, "Success", message)
        await ctx.send(embeds=[embed], ephemeral=ephemeral)
    except InteractionException as e:
        logger.error(f"Failed to send success message: {str(e)}")
    except (AttributeError, ValueError, TypeError) as e:
        logger.error(f"Failed to create success embed: {str(e)}")
    except Exception as e:
        logger.exception(f"Unexpected error sending success message: {str(e)}")


################ Kernel functions ################


kernel_base: interactions.SlashCommand = interactions.SlashCommand(
    name="kernel", description="Bot Framework Kernel Commands"
)
kernel_module: interactions.SlashCommand = kernel_base.group(
    name="module", description="Module Commands"
)
kernel_review: interactions.SlashCommand = kernel_base.group(
    name="review", description="Review Commands"
)
kernel_debug: interactions.SlashCommand = kernel_base.group(
    name="debug", description="Debug commands"
)


dm_messages: dict[str, list[interactions.Message]] = dict()


async def get_members(
    ctx: interactions.SlashContext,
) -> list[Union[interactions.Member, interactions.User]]:
    role_id: str | None = os.environ.get("ROLE_ID")
    if not role_id:
        return [client.owner] if hasattr(client, "owner") else []

    try:
        role: interactions.Role = await ctx.guild.fetch_role(role_id)
        if not role:
            return [client.owner] if hasattr(client, "owner") else []

        members: list[Union[interactions.Member, interactions.User]] = list(
            role.members
        )
        if hasattr(client, "owner") and client.owner not in members:
            members.append(client.owner)
        return members

    except (InteractionException, AttributeError) as e:
        logger.error("Failed to fetch role members: %s", e)
    except Exception as e:
        logger.exception("Unexpected error getting key members: %s", e)

    return [client.owner] if hasattr(client, "owner") else []


async def dm_members(
    ctx: interactions.SlashContext,
    msg: Optional[str] = None,
    *,
    embeds: Optional[list[interactions.Embed]] = None,
    components: Optional[list[interactions.ComponentType]] = None,
    custom_id: Optional[str] = None,
) -> None:
    try:
        key_members = await get_members(ctx)
    except (AttributeError, TypeError, ValueError) as e:
        logger.error("Failed to fetch role members: %s", e)
        return
    except Exception as e:
        logger.exception("Unexpected error getting key members: %s", e)
        return

    dm_msg: list[interactions.Message] = []
    dm_msg_append = dm_msg.append

    for key_member in (m for m in key_members if m is not None):
        try:
            msg_to_send = await key_member.send(
                content=msg, embeds=embeds or None, components=components or None
            )
            dm_msg_append(msg_to_send)
        except (MessageException, HTTPException, InteractionException, OSError) as e:
            logger.error("Failed to send DM: {str(e)}")
        except Exception as e:
            logger.exception("Unexpected error sending DM: %s", e)

    if custom_id is not None:
        dm_messages[custom_id] = dm_msg


################ Delete files ################


@kernel_debug.subcommand(
    "delete", sub_cmd_description="Delete files from the extension directory"
)
@interactions.slash_option(
    name="type",
    description="Type of files to delete",
    required=True,
    opt_type=interactions.OptionType.STRING,
    autocomplete=True,
    argument_name="file_type",
)
@interactions.check(interactions.has_id(1268909926458064991))
async def cmd_delete(ctx: interactions.SlashContext, file_type: str) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error("Failed to defer interaction: %s", e)
        return
    except Exception as e:
        logger.exception("Unexpected error deferring interaction: %s", e)
        return

    if not os.path.exists(BASE_DIR):
        await send_error(client, ctx, "Extension directory does not exist.")
        return

    if file_type == "all":
        await send_error(
            client, ctx, "Cannot delete all files at once for safety reasons."
        )
        return

    file_path: str = os.path.join(BASE_DIR, file_type)
    if not os.path.isfile(file_path):
        await send_error(
            client,
            ctx,
            f"File `{file_type}` does not exist in the extension directory.",
        )
        return

    try:
        os.remove(file_path)
    except (PermissionError, OSError) as e:
        logger.error(
            "%s error while deleting %s: %s",
            "Permission denied" if isinstance(e, PermissionError) else "OS",
            file_type,
            e,
        )
        await send_error(
            client,
            ctx,
            (
                "Permission denied while deleting file."
                if isinstance(e, PermissionError)
                else "Failed to delete file."
            ),
        )
        return
    except Exception as e:
        logger.exception("Unexpected error deleting %s: %s", file_type, e)
        await send_error(client, ctx, f"An unexpected error occurred: {e}")
        return

    await send_success(client, ctx, f"Successfully deleted file `{file_type}`.")
    logger.info("Deleted file %s from extension directory", file_type)


@cmd_delete.autocomplete("type")
async def delete_type_autocomplete(ctx: interactions.AutocompleteContext) -> None:
    choices: list[dict[str, str]] = []

    try:
        if os.path.exists(BASE_DIR):
            files: list[str] = [
                f
                for f in os.listdir(BASE_DIR)
                if os.path.isfile(os.path.join(BASE_DIR, f)) and not f.startswith(".")
            ]
            choices = [{"name": file, "value": file} for file in sorted(files)]
    except (PermissionError, OSError) as e:
        logger.error(
            "%s error while listing files: %s",
            "Permission denied" if isinstance(e, PermissionError) else "OS",
            e,
        )
        choices = [
            {
                "name": (
                    "Error: Permission denied"
                    if isinstance(e, PermissionError)
                    else f"Error: {e}"
                ),
                "value": "error",
            }
        ]
    except Exception as e:
        logger.exception("Unexpected error listing files: %s", e)
        choices = [{"name": f"Error: {e}", "value": "error"}]

    try:
        await ctx.send(choices[:25])
    except InteractionException as e:
        logger.error("Failed to send autocomplete choices: %s", e)
    except Exception as e:
        logger.exception("Unexpected error sending autocomplete choices: %s", e)


################ Export files ################


@kernel_debug.subcommand(
    "export", sub_cmd_description="Export files from the extension directory"
)
@interactions.slash_option(
    name="type",
    description="Type of files to export",
    required=True,
    opt_type=interactions.OptionType.STRING,
    autocomplete=True,
    argument_name="file_type",
)
@interactions.slash_default_member_permission(interactions.Permissions.ADMINISTRATOR)
async def cmd_export(ctx: interactions.SlashContext, file_type: str) -> None:
    await ctx.defer(ephemeral=True)
    filename: str = ""

    if not os.path.exists(BASE_DIR):
        return await send_error(client, ctx, "Extension directory does not exist.")

    file_path = os.path.join(BASE_DIR, file_type)
    if file_type != "all" and not os.path.isfile(file_path):
        return await send_error(
            client,
            ctx,
            f"File `{file_type}` does not exist in the extension directory.",
        )

    try:
        async with aiofiles.tempfile.NamedTemporaryFile(
            prefix="export_", suffix=".tar.gz", mode="wb+", delete=False
        ) as temp_file:
            filename = temp_file.name
            base_name = filename.removesuffix(".tar.gz")

            await aioshutil.make_archive(
                base_name,
                "gztar",
                BASE_DIR,
                "." if file_type == "all" else file_type,
            )

            if not os.path.exists(filename):
                return await send_error(client, ctx, "Failed to create archive file.")

            message = (
                "All extension files attached."
                if file_type == "all"
                else f"File `{file_type}` attached."
            )
            await ctx.send(
                message,
                files=[interactions.File(filename)],
            )

    except PermissionError:
        logger.error("Permission denied while exporting %s", file_type)
        await send_error(client, ctx, "Permission denied while accessing files.")
    except Exception as e:
        logger.exception("Error exporting %s: %s", file_type, e)
        await send_error(
            client, ctx, f"An error occurred while exporting {file_type}: {str(e)}"
        )
    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logger.exception("Error cleaning up temp file: %s", e)


@cmd_export.autocomplete("type")
async def export_type_autocomplete(ctx: interactions.AutocompleteContext) -> None:
    choices: list[dict[str, str]] = [{"name": "All Files", "value": "all"}]

    try:
        if os.path.exists(BASE_DIR):
            files = [
                f
                for f in os.listdir(BASE_DIR)
                if os.path.isfile(os.path.join(BASE_DIR, f)) and not f.startswith(".")
            ]

            choices.extend({"name": file, "value": file} for file in sorted(files))
    except PermissionError:
        logger.error("Permission denied while listing files")
        choices = [{"name": "Error: Permission denied", "value": "error"}]
    except Exception as e:
        logger.exception(f"Error listing files: {e}")
        choices = [{"name": f"Error: {str(e)}", "value": "error"}]

    await ctx.send(choices[:25])


@kernel_review.subcommand("reboot", sub_cmd_description="Reboot the bot")
@interactions.check(role_check)
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

    await dm_members(ctx, embeds=[embed])
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
@interactions.check(role_check)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def cmd_module_load(ctx: interactions.SlashContext, url: str) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        return

    executor: interactions.Member = ctx.author

    try:
        embed = await create_embed(
            bot=client,
            title="Module Load",
            description=f"{executor.mention} is attempting to load module from {url}.",
        )
        embed.url = url
        embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)
        embed.set_footer(text=client.user.display_name, icon_url=client.user.avatar_url)

        await dm_members(ctx, embeds=[embed])
    except (AttributeError, ValueError) as e:
        logger.error(f"Error creating embed: {e}")
        await send_error(client, ctx, "Failed to create notification embed")
        return
    except Exception as e:
        logger.exception(f"Unexpected error in notification: {e}")
        await send_error(client, ctx, "Failed to send notifications")
        return

    logger.debug("Starting module load process")

    try:
        msg = await ctx.send("Loading new module. Please wait.")
    except InteractionException as e:
        logger.error(f"Failed to send loading message: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error sending loading message: {e}")
        return

    git_url, parsed, validated = parse_git_url(url)

    if not validated:
        await send_error(
            client,
            ctx,
            "Invalid Git repository URL. The web URL must use HTTPS format (e.g., `https://github.com/user/repo.git`).",
        )
        try:
            await msg.delete()
        except MessageException as e:
            logger.error(f"Failed to delete message: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error deleting message: {e}")
        return

    try:
        if os.path.isdir(os.path.join(os.getcwd(), "extensions", parsed)):
            await send_error(
                client,
                ctx,
                f"Module `{parsed}` is already loaded. Please unload it first using `/kernel module unload` if you want to reload it.",
            )
            try:
                await msg.delete()
            except MessageException as e:
                logger.error(f"Failed to delete message: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error deleting message: {e}")
            return
    except OSError as e:
        logger.error(f"OS error checking directory: {e}")
        await send_error(client, ctx, "Failed to check module directory")
        return
    except Exception as e:
        logger.exception(f"Unexpected error checking directory: {e}")
        await send_error(client, ctx, "Failed to verify module status")
        return

    try:
        module, clone_validated = clone_git_repo(git_url)
        if not clone_validated:
            logger.warning(f"Failed to clone module {module}")
            await send_error(
                client,
                ctx,
                f"Failed to clone module `{module}`. Please verify the repository exists and is accessible.",
            )
            try:
                await msg.delete()
            except MessageException as e:
                logger.error(f"Failed to delete message: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error deleting message: {e}")
            return
    except (ImportError, RuntimeError) as e:
        logger.error(f"Error cloning repository: {e}")
        await send_error(client, ctx, "Failed to clone repository.")
        return
    except OSError as e:
        logger.error(f"OS error cloning repository: {e}")
        await send_error(client, ctx, "Failed to access repository.")
        return
    except Exception as e:
        logger.exception(f"Unexpected error cloning repository: {e}")
        await send_error(client, ctx, "Failed to clone repository")
        return

    requirements_path = os.path.join(
        os.getcwd(), "extensions", module, "requirements.txt"
    )

    if not os.path.exists(requirements_path):
        try:
            delete_git_repo(module)
        except OSError as e:
            logger.error(f"Failed to cleanup module directory: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error during cleanup: {e}")
        logger.warning(f"Module {module} missing requirements.txt")
        await send_error(
            client,
            ctx,
            f"Module `{module}` is missing required `requirements.txt` file. Please ensure the module follows the correct structure.",
        )
        try:
            await msg.delete()
        except MessageException as e:
            logger.error(f"Failed to delete message: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error deleting message: {e}")
        return

    try:
        if not execute_pip_requirements(requirements_path):
            logger.warning(f"Failed to install requirements for module {module}")
            await send_error(
                client,
                ctx,
                f"Failed to install dependencies for module `{module}`. Please check the `requirements.txt` file for errors.",
            )
            try:
                await msg.delete()
            except MessageException as e:
                logger.error(f"Failed to delete message: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error deleting message: {e}")
            return
    except (ImportError, RuntimeError) as e:
        logger.error(f"Error installing requirements: {e}")
        await send_error(client, ctx, f"Failed to install requirements: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error installing requirements: {e}")
        await send_error(client, ctx, "Failed to install module requirements")
        return

    try:
        client.reload_extension(f"extensions.{module}.main")
        logger.info(f"Loaded extension extensions.{module}.main")
        try:
            await msg.edit(content=f"Loaded module `extensions.{module}.main`.")
        except InteractionException as e:
            logger.warning(f"Failed to edit message: {e}")
            await send_success(
                client,
                ctx,
                f"Loaded module `extensions.{module}.main`.",
            )
    except ExtensionException as e:
        logger.exception(f"Failed to load extension {module}", exc_info=e)
        try:
            await client.synchronise_interactions(delete_commands=True)
        except InteractionException as e:
            logger.error(f"Failed to synchronize commands: {e}")
        try:
            delete_git_repo(module)
        except OSError as e:
            logger.error(f"Failed to cleanup module directory: {e}")
        await send_error(
            client,
            ctx,
            f"Failed to load module `{module}`. The repository has been removed due to loading errors.",
        )
        try:
            await msg.delete()
        except MessageException as e:
            logger.error(f"Failed to delete message: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error deleting message: {e}")


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
@interactions.check(role_check)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def cmd_module_unload(ctx: interactions.SlashContext, module: str) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        return

    executor: interactions.Member = ctx.author

    try:
        info, valid = get_git_repo_info(module)
        if not valid:
            await send_error(
                client,
                ctx,
                f"Module `{module}` not found or is not a valid git repository.",
            )
            return
    except (ImportError, RuntimeError) as e:
        logger.error(f"Error getting repository info: {e}")
        await send_error(client, ctx, f"Failed to get module information: {e}")
        return
    except OSError as e:
        logger.error(f"OS error accessing repository: {e}")
        await send_error(client, ctx, f"Failed to access module: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error getting repo info: {e}")
        await send_error(client, ctx, "Failed to get module information")
        return

    try:
        embed = await create_embed(
            client,
            "Module Unload",
            f"{executor.mention} is unloading module `{module}`. Current commit: `{info.current_commit.id}` from {info.remote_url}.",
            EmbedColor.ERROR,
        )
        embed.url = info.remote_url
        embed.set_author(name=executor.display_name, icon_url=executor.avatar_url)

        await dm_members(ctx, embeds=[embed])
    except (AttributeError, ValueError) as e:
        logger.error(f"Error creating embed: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error sending notification: {e}")

    try:
        client.unload_extension(f"extensions.{module}.main")
        try:
            await client.synchronise_interactions(delete_commands=True)
        except InteractionException as e:
            logger.error(f"Failed to synchronize commands: {e}")
    except ImportError as e:
        logger.exception(f"Import error while unloading {module}: {e}")
        await send_error(
            client,
            ctx,
            f"Failed to unload module `{module}` due to import error. Error: {str(e)}",
        )
    except ExtensionException as e:
        logger.exception(f"Extension error while unloading {module}: {e}")
        await send_error(
            client,
            ctx,
            f"Failed to unload module `{module}`. Error: {str(e)}",
        )
    except Exception as e:
        logger.exception(f"Unexpected error while unloading {module}: {e}")
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
            delete_git_repo(module)
        except OSError as e:
            logger.error(f"Failed to delete module directory: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error during cleanup: {e}")


@kernel_module.subcommand("list", sub_cmd_description="List loaded modules")
async def cmd_module_list(ctx: interactions.SlashContext) -> None:
    try:
        modules = {
            module
            for module in os.scandir("extensions")
            if module.is_dir()
            and module.name != "__pycache__"
            and validate_git_repo(module.name)
        }
    except OSError as e:
        logger.error(f"Failed to scan extensions directory: {e}")
        await send_error(client, ctx, "Failed to access modules directory")
        return
    except Exception as e:
        logger.exception(f"Unexpected error scanning modules: {e}")
        await send_error(client, ctx, "Failed to list modules")
        return

    if not modules:
        await send_error(
            client,
            ctx,
            "No modules are currently loaded. Use `/kernel module load` to add new modules.",
        )
        return

    try:
        embed = await create_embed(client, "Loaded Modules")
    except (AttributeError, ValueError) as e:
        logger.error(f"Failed to create embed: {e}")
        await send_error(client, ctx, "Failed to create module list")
        return
    except Exception as e:
        logger.exception(f"Unexpected error creating embed: {e}")
        await send_error(client, ctx, "Failed to display module list")
        return

    for module in modules:
        try:
            info, _ = get_git_repo_info(module.name)
            commit_id = str(info.current_commit.id)
            display_name = (
                module.name.split("_s_")[-1] if "_s_" in module.name else module.name
            )
            embed.add_field(
                name=display_name,
                value=f"- Commit: `{commit_id[:7]}`\n- URL: {info.remote_url}",
                inline=True,
            )
        except (AttributeError, ValueError) as e:
            logger.error(f"Error adding module {module.name} to list: {e}")
            continue
        except Exception as e:
            logger.exception(f"Unexpected error processing module {module.name}: {e}")
            continue

    try:
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
    except InteractionException as e:
        logger.error(f"Failed to send paginated response: {e}")
        await send_error(client, ctx, "Failed to display module list")
    except Exception as e:
        logger.exception(f"Unexpected error sending paginated response: {e}")
        await send_error(client, ctx, "Failed to show module list")


@kernel_module.subcommand("update", sub_cmd_description="Update the module")
@kernel_module_option_module()
@interactions.check(role_check)
@interactions.max_concurrency(interactions.Buckets.GUILD, 1)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def cmd_module_update(ctx: interactions.SlashContext, module: str) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        return

    executor: interactions.Member = ctx.author

    try:
        info, _ = get_git_repo_info(module)
    except (ImportError, RuntimeError) as e:
        logger.error(f"Failed to get module info: {e}")
        await send_error(client, ctx, f"Failed to access module information: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error getting module info: {e}")
        await send_error(client, ctx, "Failed to get module information")
        return

    try:
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

        await dm_members(ctx, embeds=[embed])
    except (AttributeError, ValueError) as e:
        logger.error(f"Failed to create notification embed: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error sending notification: {e}")

    module_dir = pathlib.Path(f"extensions/{module}")
    if not module_dir.is_dir():
        await send_error(
            client,
            ctx,
            f"Module `{module}` does not exist. Use `/kernel module list` to see available modules.",
        )
        return

    try:
        err = pull_git_repo(module)
        if err != 0:
            error_reasons = [
                "Not a git repository",
                "Failed to fetch from remote",
                "Master branch not found",
            ]
            error_msg = next(
                (r for i, r in enumerate(error_reasons) if i == err - 1),
                "Unknown error",
            )
            await send_error(
                client,
                ctx,
                f"Module update failed: {error_msg}. Please try again or contact an administrator.",
            )
            return
    except Exception as e:
        logger.exception(f"Failed to pull repository updates: {e}")
        await send_error(client, ctx, f"Failed to update module: {e}")
        return

    requirements_path = module_dir / "requirements.txt"
    if not requirements_path.exists():
        await send_error(
            client,
            ctx,
            "Missing `requirements.txt`. Update aborted. Please ensure the module has all required files.",
        )
        return

    try:
        execute_pip_requirements(str(requirements_path))
    except (ImportError, RuntimeError) as e:
        logger.error(f"Failed to update requirements: {e}")
        await send_error(client, ctx, f"Failed to update module dependencies: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error updating requirements: {e}")
        await send_error(client, ctx, "Failed to update module dependencies")
        return

    try:
        client.reload_extension(f"extensions.{module}.main")
        await client.synchronise_interactions(delete_commands=True)
    except ExtensionException as e:
        logger.error(f"Failed to reload module: {e}")
        await send_error(client, ctx, f"Failed to reload module: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error reloading module: {e}")
        await send_error(client, ctx, "Failed to reload module")
        return

    try:
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
    except (OSError, IOError) as e:
        logger.error(f"Failed to read changelog: {e}")
        await send_error(client, ctx, "Failed to read update information")
    except InteractionException as e:
        logger.error(f"Failed to send update result: {e}")
        await send_error(client, ctx, "Failed to display update results")
    except Exception as e:
        logger.exception(f"Unexpected error displaying update results: {e}")
        await send_error(client, ctx, "Failed to complete update process")


@kernel_module.subcommand("info", sub_cmd_description="Show module information")
@kernel_module_option_module()
async def cmd_module_info(ctx: interactions.SlashContext, module: str) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        return

    try:
        info, valid = get_git_repo_info(module)
        if not valid:
            await send_error(
                client,
                ctx,
                "Module not found. Please verify the module name and ensure it exists in the extensions directory. Use the `/kernel module list` command to see all available modules.",
            )
            return
    except (ImportError, RuntimeError) as e:
        logger.error(f"Failed to get module info: {e}")
        await send_error(client, ctx, f"Failed to access module information: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error getting module info: {e}")
        await send_error(client, ctx, "Failed to get module information")
        return

    try:
        result_embed = await create_embed(
            client,
            "Module Information",
            f"Details for module `{module}`",
            (
                EmbedColor.ERROR
                if (modifications := info.modifications > 0)
                else EmbedColor.INFO
            ),
        )
        result_embed.url = info.remote_url

        result_embed.add_field(
            name="Local Changes Status",
            value=(
                "Modified - Local changes detected"
                if modifications
                else "Clean - No local modifications"
            ),
        )

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
            "No changelog information available"
            if not info.CHANGELOG.strip()
            else (
                info.CHANGELOG[:1000] + "..."
                if len(info.CHANGELOG) > 1000
                else info.CHANGELOG
            )
        )

        result_embed.add_field(
            name="Recent Changes", value=code_block(changelog_content, "py")
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
    except (AttributeError, ValueError) as e:
        logger.error(f"Failed to create info embed: {e}")
        await send_error(client, ctx, "Failed to display module information")
    except InteractionException as e:
        logger.error(f"Failed to send module info: {e}")
        await send_error(client, ctx, "Failed to display module information")
    except Exception as e:
        logger.exception(f"Unexpected error displaying module info: {e}")
        await send_error(client, ctx, "Failed to show module information")


@cmd_module_unload.autocomplete("module")
@cmd_module_update.autocomplete("module")
@cmd_module_info.autocomplete("module")
async def module_module_autocomplete(ctx: interactions.AutocompleteContext) -> None:
    try:
        module_option_input: str = ctx.input_text
        modules: list[str] = [
            i
            for i in os.listdir("extensions")
            if os.path.isdir(f"extensions/{i}")
            and i != "__pycache__"
            and validate_git_repo(i)
        ]
        modules_auto: list[str] = [i for i in modules if module_option_input in i]

        await ctx.send(choices=[{"name": i, "value": i} for i in modules_auto][:25])
    except OSError as e:
        logger.error(f"Failed to list modules directory: {e}")
        await ctx.send(choices=[{"name": "Error listing modules", "value": "error"}])
    except InteractionException as e:
        logger.error(f"Failed to send autocomplete choices: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error in module autocomplete: {e}")
        await ctx.send(choices=[{"name": "Error occurred", "value": "error"}])


is_download_in_progress: bool = False


@kernel_review.subcommand(
    "download", sub_cmd_description="Download current running code in tarball"
)
@interactions.max_concurrency(interactions.Buckets.GUILD, 1)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def cmd_review_download(ctx: interactions.SlashContext) -> None:
    global is_download_in_progress
    if is_download_in_progress:
        try:
            await ctx.send(
                "There is already a download task running! Please run it later :)",
                ephemeral=True,
            )
        except InteractionException as e:
            logger.error(f"Failed to send busy message: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error sending busy message: {e}")
        return

    is_download_in_progress = True
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        is_download_in_progress = False
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        is_download_in_progress = False
        return

    try:

        def compress_temp(filename: str) -> None:
            try:
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
            except OSError as e:
                logger.error(f"Failed to compress files: {e}")
                raise
            except Exception as e:
                logger.exception(f"Unexpected error during compression: {e}")
                raise

        async with aiofiles.tempfile.NamedTemporaryFile(
            mode="wb", suffix=".tar.gz", prefix="Discord-Bot-Framework_"
        ) as tmp:
            try:
                compress_temp(tmp.name)
                await ctx.send(
                    "Current code that is running as attached", file=tmp.name
                )
            except InteractionException as e:
                logger.error(f"Failed to send file: {e}")
                await send_error(client, ctx, "Failed to send compressed files")
            except OSError as e:
                logger.error(f"Failed to create temporary file: {e}")
                await send_error(client, ctx, "Failed to create download file")
            except Exception as e:
                logger.exception(f"Unexpected error sending download: {e}")
                await send_error(client, ctx, "Failed to process download request")
    finally:
        is_download_in_progress = False


@kernel_review.subcommand("info", sub_cmd_description="Show the Kernel information")
async def cmd_review_info(ctx: interactions.SlashContext) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        return

    try:
        info = get_kernel_repo_info()
    except (ImportError, RuntimeError, OSError) as e:
        error_msg = {
            ImportError: "Failed to access kernel information",
            RuntimeError: "Failed to access kernel information",
            OSError: "Failed to read kernel information",
        }.get(type(e), "Failed to retrieve kernel information")
        logger.error(f"{error_msg}: {e}")
        await send_error(client, ctx, f"{error_msg}: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error getting kernel info: {e}")
        await send_error(client, ctx, "Failed to retrieve kernel information")
        return

    try:
        modifications = bool(info.modifications)
        embed_color = EmbedColor.ERROR if modifications else EmbedColor.INFO
        result_embed = await create_embed(
            client,
            "Kernel Information",
            "Details for Discord-Bot-Framework-Kernel",
            embed_color,
        )
        result_embed.url = info.remote_url

        result_embed.add_field(
            name="Local Changes Status",
            value=(
                "Modified - Local changes detected"
                if modifications
                else "Clean - No local modifications"
            ),
        )

        commit_info = (
            ("Current Local Commit", info.current_commit.id, info.get_utc_time()),
            (
                "Latest Remote Commit",
                info.remote_head_commit.id,
                info.get_remote_utc_time(),
            ),
        )

        for name, commit_id, timestamp in commit_info:
            result_embed.add_field(
                name=name,
                value=f"- ID: `{commit_id}`\n- Timestamp: `{timestamp}`",
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
    except (AttributeError, ValueError, InteractionException) as e:
        logger.error(
            f"Failed to {'create info embed' if isinstance(e, (AttributeError, ValueError)) else 'send kernel info'}: {e}"
        )
        await send_error(client, ctx, "Failed to display kernel information")
    except Exception as e:
        logger.exception(f"Unexpected error displaying kernel info: {e}")
        await send_error(client, ctx, "Failed to show kernel information")


################ Update the kernel ################


@kernel_review.subcommand("update", sub_cmd_description="Update the kernel")
@interactions.check(role_check)
@interactions.max_concurrency(interactions.Buckets.GUILD, 1)
@interactions.cooldown(interactions.Buckets.GUILD, 2, 60)
async def cmd_review_update(ctx: interactions.SlashContext) -> None:
    try:
        await ctx.defer(ephemeral=True)
    except InteractionException as e:
        logger.error(f"Failed to defer interaction: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error deferring interaction: {e}")
        return

    executor: interactions.Member = ctx.author

    try:
        info = get_kernel_repo_info()
    except (ImportError, RuntimeError) as e:
        logger.error(f"Failed to get kernel info: {e}")
        await send_error(client, ctx, f"Failed to access kernel information: {e}")
        return
    except OSError as e:
        logger.error(f"OS error accessing kernel info: {e}")
        await send_error(client, ctx, f"Failed to read kernel information: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error getting kernel info: {e}")
        await send_error(client, ctx, "Failed to retrieve kernel information")
        return

    try:
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

        await dm_members(ctx, embeds=[embed])
    except (AttributeError, ValueError) as e:
        logger.error(f"Failed to create notification embed: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error sending notification: {e}")

    try:
        if (err := pull_kernel_repo()) != 0:
            await send_error(
                client,
                ctx,
                f"Kernel update failed: {next((r for i, r in enumerate(['Not a git repository', 'Failed to fetch from remote', 'Master branch not found']) if i == err - 1), 'Unknown error')}. Please try again or contact an administrator.",
            )
            return
    except Exception as e:
        logger.exception(f"Failed to pull repository updates: {e}")
        await send_error(client, ctx, f"Failed to update kernel: {e}")
        return

    requirements_path = pathlib.Path("requirements.txt")
    if not requirements_path.exists():
        await send_error(
            client,
            ctx,
            "Missing `requirements.txt`. Update aborted. Please ensure all required files exist.",
        )
        return

    try:
        execute_pip_requirements(str(requirements_path))
    except (ImportError, RuntimeError) as e:
        logger.error(f"Failed to update requirements: {e}")
        await send_error(client, ctx, f"Failed to update kernel dependencies: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error updating requirements: {e}")
        await send_error(client, ctx, "Failed to update kernel dependencies")
        return

    try:
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
    except (AttributeError, ValueError) as e:
        logger.error(f"Failed to create completion embed: {e}")
        await send_error(client, ctx, "Failed to display update completion")
    except InteractionException as e:
        logger.error(f"Failed to send update result: {e}")
        await send_error(client, ctx, "Failed to display update results")
    except Exception as e:
        logger.exception(f"Unexpected error displaying update results: {e}")
        await send_error(client, ctx, "Failed to complete update process")


################ Kernel functions END ################


async def main() -> None:
    try:
        extensions = {
            *(
                f"extensions.{name[:-3]}"
                for name in os.listdir("extensions")
                if name.endswith(".py") and not name.startswith("_")
            ),
            *(
                f"extensions.{dirname}.main"
                for dirname in os.listdir("extensions")
                if all(
                    (
                        os.path.isdir(f"extensions/{dirname}"),
                        dirname != "__pycache__",
                        validate_git_repo(dirname),
                    )
                )
            ),
        }
    except OSError as e:
        logger.error(f"Failed to list extensions directory: {e}")
        return
    except Exception as e:
        logger.exception(f"Unexpected error listing extensions: {e}")
        return

    try:
        client.load_extension("interactions.ext.jurigged")
    except (ExtensionException, ImportError) as e:
        logger.error("Error loading jurigged extension", exc_info=e)
    except Exception as e:
        logger.exception("Unexpected error loading jurigged extension", exc_info=e)

    for ext in sorted(extensions):
        try:
            client.load_extension(ext)
            logger.info(f"Loaded extension {ext}")
        except (ExtensionException, ImportError) as e:
            logger.error(f"Error loading {ext}", exc_info=e)
        except Exception as e:
            logger.exception(f"Unexpected error loading {ext}", exc_info=e)

    try:
        await client.astart()
    except LoginError as e:
        logger.critical(f"Failed to start client: {e}")
    except Exception as e:
        logger.critical(f"Unexpected error starting client: {e}")


asyncio.run(main())
