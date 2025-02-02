import time

import interactions


class Template(interactions.Extension):

    module_base: interactions.SlashCommand = interactions.SlashCommand(
        name="demo", description="A base command, to expand on"
    )
    module_group_test: interactions.SlashCommand = module_base.group(
        name="test", description="A sub command, to expand on"
    )

    @module_base.subcommand("hello", sub_cmd_description="Say hello")
    async def hello(self, ctx: interactions.SlashContext) -> None:
        await ctx.send("Hello, world! This is a sub command.")

    @module_base.subcommand("ping", sub_cmd_description="Check the bot's latency")
    async def ping(self, ctx: interactions.SlashContext) -> None:
        start_time = time.time()
        message = await ctx.send("Pinging...")
        latency = (time.time() - start_time) * 1000
        await message.edit(content=f"Pong! Latency: {latency:.2f} ms.")

    @module_group_test.subcommand(
        "options", sub_cmd_description="A command with options"
    )
    @interactions.slash_option(
        name="option_str",
        description="A string option",
        opt_type=interactions.OptionType.STRING,
        required=True,
    )
    @interactions.slash_option(
        name="option_int",
        description="An integer option",
        opt_type=interactions.OptionType.INTEGER,
        required=True,
    )
    @interactions.slash_option(
        name="attachment",
        description="An attachment option",
        opt_type=interactions.OptionType.ATTACHMENT,
        required=True,
        argument_name="option_attachment",
    )
    async def options(
        self,
        ctx: interactions.SlashContext,
        option_str: str,
        option_int: int,
        option_attachment: interactions.Attachment,
    ) -> None:
        embed = interactions.Embed(
            "There are a lot of options here",
            description="Maybe too many",
            color=interactions.BrandColors.BLURPLE,
        )
        embed.set_image(url=option_attachment.url)
        embed.add_field("String option", option_str)
        embed.add_field("Integer option", str(option_int))
        await ctx.send(embed=embed)

    @module_group_test.subcommand(
        "components", sub_cmd_description="A command with components"
    )
    async def components(self, ctx: interactions.SlashContext) -> None:
        await ctx.send(
            "Here are some components",
            components=interactions.spread_to_rows(
                interactions.Button(
                    label="Click me!",
                    custom_id="click_me",
                    style=interactions.ButtonStyle.PRIMARY,
                ),
                interactions.StringSelectMenu(
                    interactions.StringSelectOption(
                        label="Select me!", value="select_me_1"
                    ),
                    interactions.StringSelectOption(
                        label="No, select me!", value="select_me_2"
                    ),
                    interactions.StringSelectOption(
                        label="Select me too!", value="select_me_3"
                    ),
                    placeholder="I wonder what this does",
                    max_values=2,
                    custom_id="select_me",
                ),
            ),
        )

    @interactions.component_callback("click_me")
    async def click_me(self, ctx: interactions.ComponentContext) -> None:
        await ctx.send("You clicked me!")

    @interactions.component_callback("select_me")
    async def select_me(self, ctx: interactions.ComponentContext) -> None:
        await ctx.send(f"You selected {' '.join(ctx.values)}.")
