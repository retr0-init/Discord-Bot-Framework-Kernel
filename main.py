"""
Main script to run

This script initializes extensions and starts the bot
"""
import asyncio
import aiofiles
import os
import sys
import pathlib
import tempfile

import interactions
# from interactions.ext import prefixed_commands
# from interactions.ext.prefixed_commands import prefixed_command, PrefixedContext
from dotenv import load_dotenv

'''
The DEV_GUILD must be set to a specific guild_id
'''
from config import DEBUG, DEV_GUILD
from src import logutil, compressutil, moduleutil

from typing import Union

try:
    from icecream import ic
except ImportError:  # Graceful fallback if IceCream isn't installed.
    ic = lambda *a: None if not a else (a[0] if len(a) == 1 else a)  # noqa

ic.disable()

load_dotenv()

# Configure logging for this main.py handler
logger = logutil.init_logger("main.py")
logger.debug(
    "Debug mode is %s; This is not a warning, \
just an indicator. You may safely ignore",
    DEBUG,
)

def compress_temp(filename: str) -> None:
    # tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", prefix="Discord-Bot-Framework_")
    # filename = tmp.name
    # tmp.close()
    compressutil.compress_directory(pathlib.Path(__file__).parent.resolve(), filename)
    # return filename

if not os.environ.get("TOKEN"):
    logger.critical("TOKEN variable not set. Cannot continue")
    sys.exit(1)

client = interactions.Client(
    token=os.environ.get("TOKEN"),
    activity=interactions.Activity(
        name="with interactions", type=interactions.ActivityType.PLAYING
    ),
    debug_scope=DEV_GUILD,
    sync_interactions=True,
    sync_ext=True,
    intents=interactions.Intents.ALL,
)

'''
prefixed_commands.setup(client, default_prefix="!")

@prefixed_command(name="load")
async def cmd_internal_load(ctx: PrefixedContext, module: str):
    client.reload_extension(f"extensions.{module}.main")
    await ctx.reply(f"Loaded extensions.{module}.main")
'''

@interactions.listen()
async def on_startup():
    """Called when the bot starts"""
    await client.synchronise_interactions(delete_commands=True)
    logger.info(f"Logged in as {client.user}")


################ Kernel functions START ################
kernel_base: interactions.SlashCommand = interactions.SlashCommand(name="kernel", description="Bot Framework Kernel Commands")
kernel_module: interactions.SlashCommand = kernel_base.group(name="module", description="Bot Framework Kernel Module Commands")
kernel_review: interactions.SlashCommand = kernel_base.group(name="review", description="Bot Framework Kernel Review Commands")

'''
Load the module from remote HTTPS Git Repository
The scope must be set to a specific guild_id
CC-BY-SA-3.0: https://stackoverflow.com/a/14050282
'''
@kernel_module.subcommand("load", sub_cmd_description="Load module from Git repo with HTTPS")
@interactions.slash_option(
    name = "url",
    description = "HTTPS URL to module",
    required = True,
    opt_type = interactions.OptionType.STRING
)
async def kernel_module_load(ctx: interactions.SlashContext, url: str):
    logger.debug("Kernel module load START")
    ic()
    # Defer the context as the following actions may cost more than 3 seconds
    await ctx.defer(ephemeral = True)
    ic()
    # Parse and validate the Git repository url
    git_url, parsed, validated = moduleutil.giturl_parse(url)
    if not validated:
        ic()
        await ctx.send("The loaded module is not an HTTPS Git Repo!", ephemeral = True)
    else:
        # Check whether the module extension folder exists
        if os.path.isdir(os.path.join(os.getcwd(), "extensions", parsed)):
            ic()
            await ctx.send(f"The module {parsed} has been loaded!", ephemeral = True)
        else:
            # Clone the git repo
            module, clone_validated = moduleutil.gitrepo_clone(git_url)
            if not clone_validated:
                ic()
                logger.warning(f"Module {module} clone failed")
                await ctx.send(f"The module {module} clone failed!", ephemeral = True)
            else:
                requirements_path: str = os.path.join(os.getcwd(), "extensions", module, "requirements.txt")
                ic(requirements_path)
                # Check whether requirements.txt exists in the module repo
                if not os.path.exists(requirements_path):
                    ic()
                    # If not delete the repo
                    moduleutil.gitrepo_delete(module)
                    logger.warning(f"Module {module} requirements.txt does not exist.")
                    await ctx.send(f"The module {module} does not have `requirements.txt`", ephemeral = True)
                else:
                    # pip install -r requirements.txt
                    success: bool = moduleutil.piprequirements_operate(requirements_path)
                    if not success:
                        ic()
                        logger.warning(f"Module {module} requirements.txt install failed")
                        await ctx.send(f"Module {module} `requirements.txt` install fail.", ephemeral = True)
                    else:
                        # Load the module into the kernel
                        try:
                            ic()
                            client.reload_extension(f"extensions.{module}.main")
                            logger.info(f"Loaded extension extensions.{module}.main")
                            await ctx.send(f"Module `extensions.{module}.main` loaded")
                        except interactions.errors.ExtensionLoadException as e:
                            ic()
                            logger.exception(f"Failed to load extension {module}.", exc_info=e)
                            # Delete the repo
                            moduleutil.gitrepo_delete(module)
                            await ctx.send(f"Module {module} load fail! The repo is removed.", ephemeral = True)
    ic()
    logger.debug("Kernel module load END")


'''
Kernel module unload / update module option wrapper
'''
def kernel_module_option_module():
    def wrapper(func):
        return interactions.slash_option(
            name = "module",
            description = "The name of the loaded module. Check with the list command.",
            required = True,
            opt_type = interactions.OptionType.STRING,
            autocomplete = True
        )(func)
    return wrapper


'''
Unload the module from kernel
'''
@kernel_module.subcommand("unload", sub_cmd_description="Unload module")
@kernel_module_option_module()
async def kernel_module_unload(ctx: interactions.SlashContext, module: str):
    await ctx.defer(ephemeral=True)
    try:
        client.unload_extension(f"extensions.{module}.main")
        moduleutil.gitrepo_delete(module)
        await client.synchronise_interactions(delete_commands=True)
    except:
        await ctx.send(f"Module {module} either not exists or failed to unload", ephemeral=True)
    else:
        await ctx.send(f"Module {module} unloaded")


'''
List all loaded modules in kernel
'''
@kernel_module.subcommand("list", sub_cmd_description="List loaded modules")
async def kernel_module_list(ctx: interactions.SlashContext):
    modules_o: list[str] = [i for i in os.listdir("extensions/") if os.path.isdir(f"extensions/{i}") and i != "__pycache__"]
    # Check whether the folder is a Git repo
    modules: list[str] = [i for i in modules_o if moduleutil.is_gitrepo(i)]
    # Join the module list if the list is not empty
    if len(modules) > 0:
        modules_str: str = '- ' + '\n- '.join(modules)
        await ctx.send("已加载的模块是\n" + modules_str)
    else:
        # There is no module loaded
        await ctx.send("没有加载的模块")


'''
Update the loaded module in kernel
'''
@kernel_module.subcommand("update", sub_cmd_description="Update the module")
@kernel_module_option_module()
async def kernel_module_update(ctx: interactions.SlashContext, module: str):
    await ctx.defer()
    # Check whether the module exists in the folder
    if not os.path.isdir(f"extensions/{module}"):
        await ctx.send("The extension {module} does not exist!", ephemeral=True)
        return
    # Update the repo
    err: int = moduleutil.gitrepo_pull(module)
    # Return if the module is NOT a Git repo or updating failed
    if err != 0:
        reason: list[str] = [
            "Not a git repo",
            "Remote repo fetch failed",
            "`master` branch does not exist"
        ]
        await ctx.send("Module update failed! The reason is: {}".format(reason[err - 1]), ephemeral=True)
        return
    # Install requirements.txt
    requirements_path: str = f"{os.getcwd()}/extensions/{module}/requirements.txt"
    if not os.path.exists(requirements_path):
        await ctx.send("`requirements.txt` does not exist! No reloading!", ephemeral=True)
        return
    moduleutil.piprequirements_operate(requirements_path)
    # Reload module
    client.reload_extension(f"extensions.{module}.main")
    # Synchronise the slash command
    await client.synchronise_interactions(delete_commands=True)
    # Check CHANGELOG
    changelog_path: str = f"{os.getcwd()}/extensions/{module}/CHANGELOG"
    if os.path.isfile(changelog_path):
        async with aiofiles.open(changelog_path) as f:
            cl: str = await f.read()
    else:
        cL: str = "CHANGELOG not provided!"
    await ctx.send(f"Module `{module}` updated!\nCHANGELOG:\n```\n{cl}\n```", ephemeral=False)


'''
Autocomplete function for the kernel module unloading and update commands
'''
@kernel_module_unload.autocomplete("module")
@kernel_module_update.autocomplete("module")
async def kernel_module_option_module_autocomplete(ctx: interactions.AutocompleteContext):
    module_option_input: str = ctx.input_text
    modules: list[str] = [
        i
        for i in os.listdir("extensions")
        if os.path.isdir(f"extensions/{i}") and i != "__pycache__" and moduleutil.is_gitrepo(i)
    ]
    modules_auto: list[str] = [
        i for i in modules if module_option_input in i
    ]

    await ctx.send(
        choices = [
            {
                "name":     i,
                "value":    i,
            } for i in modules_auto
        ]
    )



'''
Download the running code in tarball (.tar.gz)
'''
@kernel_review.subcommand("download", sub_cmd_description="Download current running code in tarball")
async def kernel_review_download(ctx: interactions.SlashContext):
    await ctx.defer()
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", prefix="Discord-Bot-Framework_") as f:
        compress_temp(f.name)
        await ctx.send("Current code that is running as attached", file=f.name)
################ Kernel functions END ################


# get all python files in "extensions" folder
extensions = [
    f"extensions.{f[:-3]}"
    for f in os.listdir("extensions")
    if f.endswith(".py") and not f.startswith("_")
] + [
    f"extensions.{i}.main"
    for i in os.listdir("extensions")
    if os.path.isdir(f"extensions/{i}") and i != "__pycache__" and moduleutil.is_gitrepo(i)
]

try:
    client.load_extension("interactions.ext.jurigged")
except interactions.errors.ExtensionLoadException as e:
    logger.exception(f"Failed to load extension {extension}.", exc_info=e)

for extension in extensions:
    try:
        client.load_extension(extension)
        logger.info(f"Loaded extension {extension}")
    except interactions.errors.ExtensionLoadException as e:
        logger.exception(f"Failed to load extension {extension}.", exc_info=e)

client.start()
