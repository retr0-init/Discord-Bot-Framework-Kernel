from interactions import MISSING
from dotenv import load_dotenv
import os

load_dotenv()

"Enable DEBUG messages for logging"
DEBUG = False

"""The scope for your bot to operate in. This should be a guild ID or list of guild IDs"""
DEV_GUILD = int(os.environ.get("GUILD_ID"))
