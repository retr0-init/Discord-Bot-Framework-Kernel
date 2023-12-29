"""
Main script to run

This script initializes extensions and starts the bot
"""
import os
import sys
import pathlib
import tempfile

import interactions
from dotenv import load_dotenv

from config import DEBUG, DEV_GUILD
from src import logutil, compressutil

from typing import Union

load_dotenv()

# Configure logging for this main.py handler
logger = logutil.init_logger("main.py")
logger.debug(
    "Debug mode is %s; This is not a warning, \
just an indicator. You may safely ignore",
    DEBUG,
)

def compress_temp() -> Union[str, pathlib.Path]:
    tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", prefix="Discord-Bot-Framework_")
    filename = tmp.name
    tmp.close()
    compressutil.compress_directory(pathlib.Path(__file__).parent.resolve(), filename)
    return filename

if not os.environ.get("TOKEN"):
    logger.critical("TOKEN variable not set. Cannot continue")
    sys.exit(1)

client = interactions.Client(
    token=os.environ.get("TOKEN"),
    activity=interactions.Activity(
        name="with interactions", type=interactions.ActivityType.PLAYING
    ),
    debug_scope=DEV_GUILD,
)


@interactions.listen()
async def on_startup():
    """Called when the bot starts"""
    logger.info(f"Logged in as {client.user}")


# get all python files in "extensions" folder
extensions = [
    f"extensions.{f[:-3]}"
    for f in os.listdir("extensions")
    if f.endswith(".py") and not f.startswith("_")
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
