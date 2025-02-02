# Discord Bot Framework Kernel

A robust Discord bot framework built with [interactions.py](https://interactions-py.github.io/interactions.py/), featuring modular extension management and secure execution.

## Usage

### Slash Commands

#### Kernel Module

- `/kernel module load <url>` - Load a module from a Git repository (HTTPS URL required; privileged users only)
- `/kernel module unload <module>` - Unload and remove a module (privileged users only)
- `/kernel module update <module>` - Update a module to its latest version (privileged users only)
- `/kernel module info <module>` - Display detailed module information
- `/kernel module list` - Show all loaded modules

#### Kernel Review

- `/kernel review info` - Display kernel information
- `/kernel review update` - Update kernel to latest version (privileged users only)

#### Debug Operations

- `/kernel debug download` - Download current running code as tarball
- `/kernel debug reboot` - Restart the bot (privileged users only)
- `/kernel debug export` - Export files from the extension directory (privileged users only)

### Deployment

1. Install prerequisites:
   1. Python 3.10 or higher required
   2. Install [Firejail](https://github.com/netblue30/firejail)
   3. Install [npm](https://github.com/nodesource/distributions?tab=readme-ov-file#using-debian-as-root) and [PM2](https://pm2.keymetrics.io/)

2. Configure environment:
   1. Copy `dotenv_template.env` as `.env`
   2. Edit `.env` with required values

3. Launch using PM2:

   ```bash
   ./pm2_start.sh
   ```

## Configuration

- `.env` - Environment variables configuration
  - `TOKEN` - Discord bot token
  - `GUILD_ID` - Development guild ID
  - `ROLE_ID` - Admin role ID
- `main.log` - Runtime logs
- `extensions/` - Module directory
  - Each module requires:
    - `requirements.txt`
    - `main.py`
    - `CHANGELOG`

## Acknowledgements

This project incorporates code and ideas from:

- [interactions-py/template](https://github.com/interactions-py/template) (GPL-3.0)
- [retr0-init/Discord-Bot-Framework-Kernel](https://github.com/retr0-init/Discord-Bot-Framework-Kernel) (GPL-3.0)
- Stack Overflow:
  - [Pull the kernel git repo from remote "master" branch only](https://stackoverflow.com/a/27786533) (CC-BY-SA-3.0)
  - [Load the module from remote HTTPS Git Repository](https://stackoverflow.com/a/14050282) (CC-BY-SA-3.0)
