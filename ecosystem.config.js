module.exports = {
  apps : [{
    name   : "Discord-Bot-Framework",
    script : "./run.sh",
    watch  : ["./kernel_flag/"],
    watch_delay: 1,
    logfile: "/tmp/pm2-discord-bot-framework.log",
    time   : true,
  }]
}
