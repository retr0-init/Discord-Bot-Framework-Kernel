- [中文](#Discord机器人框架内核)
- [English](#Discord-Bot-Framework-Kernel)

# Discord机器人框架内核
![doc/discord-bot-framework.drawio.png](https://github.com/retr0-init/discord-bot-framework-doc/blob/master/discord-bot-framework.drawio.png)

如上图所示，这是与模块相结合与互动的内核。其中所有的功能应该是必要、极简的，并且也要为了更好地模块化集成提供极可能多的接口。

模块模板在[这里](https://github.com/retr0-init/Discord-Bot-Framework-Module-Template.git)。这是一个模板仓库，可以创建用于模块开发的仓库。为了能让您的模块以最好的方式与内核一起工作，请按照其中的`README.md`中的准则进行开发。

## 如何运行
1. 安装python3. 它的版本应该至少为`3.10`。
2. 安装[firejail](https://github.com/netblue30/firejail)。
3. 安装[npm](https://github.com/nodesource/distributions?tab=readme-ov-file#using-debian-as-root)。
4. 安装[PM2](https://pm2.keymetrics.io/). `sudo npm install pm2@latest -g`。
5. 拷贝[`dotenv_template.env`](dotenv_template.env)到`.env`。填入环境变量。
    1. 如果您要更新`.env`文件，运行`./pm2_delete.sh`终止机器人进程，将第5步重新做一次。
6. 运行`./pm2_start.sh`。

# Discord Bot Framework Kernel
![doc/discord-bot-framework-en.drawio.png](https://github.com/retr0-init/discord-bot-framework-doc/blob/master/discord-bot-framework-en.drawio.png)

As shown above, this is the kernel to interact with modules. All features are meant to be essential, minimal while providing as many ports as possible for greater modularity.

The module template is [here](https://github.com/retr0-init/Discord-Bot-Framework-Module-Template.git). It's a template repository that can create a new repository for module development. Please follow the template guidelines as stated in its `README.md` file to make it properly work with the kernel.

## How to run it
1. Install python3, whose version is `>=3.10`.
2. Install [firejail](https://github.com/netblue30/firejail).
3. Install [npm](https://github.com/nodesource/distributions?tab=readme-ov-file#using-debian-as-root).
4. Install [PM2](https://pm2.keymetrics.io/). `sudo npm install pm2@latest -g`.
5. Copy [`dotenv_template.env`](dotenv_template.env) as `.env`. Fill in the environmental variables.
    1. If you want to update `.env`, execute `./pm2_delete.sh` to shutdown the bot and repeat the Step 5 again.
6. Execute `./pm2_start.sh`.
