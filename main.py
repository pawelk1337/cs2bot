import nextcord
import logbook 
import settings
import textwrap
import io
import steam.steamid as steamid
import asyncio
import utils
import sys
import json
import traceback
import os

from logbook import Logger, FileHandler, NestedSetup, NullHandler, StreamHandler
from datetime import datetime
from nextcord.ext import commands
from contextlib import redirect_stdout

db = {}

if not os.path.exists("./database.json"):
    db["codes"] = {}
    # db["codes"][0] = {} # required, because it will crash when running

    db["ack"] = {}
    # db["ack"][0] = 0 # required, because it will crash when running

    with open("./database.json", "w") as f:
        json.dump(db, f, indent=4)

    print("Created database")

dbf = open("./database.json", "r")
db = json.load(dbf)
dbf.close()

level = "DEBUG"

log = Logger("CS2Bot")
setup = NestedSetup([
    NullHandler(),
    StreamHandler(sys.stdout, level=level, bubble=True),
    FileHandler('cs2bot.log', level=logbook.TRACE, bubble=True)
]).push_application()


code_rate_limit = {}

bot = commands.Bot(
    command_prefix=settings.prefix,
    intents=nextcord.Intents().all(),
    owner_ids=settings.owner_ids
)

@bot.event
async def on_ready():
    await commit()

    log.info("Ready!")
    
@bot.event
async def on_guild_join(guild: nextcord.Guild):
    await guild.leave()

async def commit():
    with open("./database.json", "w") as f:
        json.dump(db, f, indent=4)
    await asyncio.sleep(0.01)
    with open("./database.json", "r") as f:
        json.load(f)

async def ack(user: int) -> bool:
    try: 
        if db["ack"][user] != None:
            return True
        db["ack"][user] = True
        return False
    except KeyError:
        db["ack"][user] = True
        await commit()
        return False

@bot.event
async def on_voice_state_update(member: nextcord.Member, before: nextcord.VoiceState, after: nextcord.VoiceState):
    if member.bot: log.trace("member is a bot, skipping"); return
    if member.voice is None: log.trace("member is not in a voice channel, skipping", user=member.id); return
    if member.guild.id != settings.guildID: log.trace("wrong guild, skipping", user=member.id); return
    found = False
    for CName in settings.channelNameWhitelist:
        if CName in member.voice.channel.name.lower():
            found = True
            break
    if not found: log.trace("channel name is not in whitelist, skipping", user=member.id); return

    unix_timestamp = (datetime.now() - datetime(1970, 1, 1)).total_seconds()
    CRLtime = 0
    try:
        CRLtime = code_rate_limit[str(member.id)+"+"+str(member.voice.channel.id)]
    except KeyError:
        code_rate_limit[str(member.id)+"+"+str(member.voice.channel.id)] = unix_timestamp

    res = None

    try:
        res = db["codes"][str(member.id)]
    except KeyError:
        log.trace("user has not set their code yet, returning", user=member.id)
        if not await ack(member.id):
            log.trace("user has already seen this message, skipping", user=member.id)
            await member.voice.channel.send(f"## {member.mention} jeszcze nie ustawiłeś swojego kodu znajomego!\nMożesz go ustawić za pomocą `/kod` w kanale <#1101247600499376222>")
        return
    
    res # for some reason it doesn't work sometimes without it

    if (unix_timestamp - code_rate_limit[str(member.id)+"+"+str(member.voice.channel.id)]) > 30*60:
        log.info("member code found, and code was not send in the last 30 minutes in this channel")
        code = res["code"]
        await member.voice.channel.send(f"## Kod użytkownika {member.name} to `{code}`")
    elif CRLtime == 0:
        log.info("member code found, and code was not send in the last 30 minutes in this channel")
        code = res["code"]
        await member.voice.channel.send(f"## Kod użytkownika {member.name} to `{code}`")
    else: log.info("member code was found but the requirements were not met", 
            user=str(member.id)
        ); return


@bot.slash_command(
    description="Ustaw kod znajomego!"
)
async def kod(interaction: nextcord.Interaction, code: str = nextcord.SlashOption(
        name="kod",
        description="Kod znajomego w cs2",
        required=True
    )
):
    if db == None: log.critical("Database is not configured"); return
    if kod == "":
        log.trace("code is empty, returning")
        await interaction.send(f"## Nie można ustawić kodu!\n### Kod jest pusty!", ephemeral=True); return

    code_parsed = steamid.from_csgo_friend_code(code.replace(" ", "_"))
    try: code_parsed.as_64 
    except AttributeError: 
        log.trace("code is not valid (attr err), returning")
        await interaction.send(f"## Kod **`{code}`** nie jest poprawny!\n## Czy dobrze go wpisałeś?", ephemeral=True)
        return
    
    if code_parsed is None:
        log.trace("code is not valid (code is None), returning")
        await interaction.send(f"## Kod **`{code}`** nie jest poprawny!\n## Czy dobrze go wpisałeś?", ephemeral=True)
        return
    
    if code_parsed.as_64 == 0:
        log.trace("code is not valid (code is 0), returning")
        await interaction.send(f"## Kod **`{code}`** nie jest poprawny!\n## Czy dobrze go wpisałeś?", ephemeral=True)
        return
    
    db["codes"][interaction.user.id] = {
        "code": code_parsed.as_csgo_friend_code,
        "user": interaction.user.id,
        "steam": code_parsed.as_64
    }
    await commit()

    log.info("user has set their code", user=interaction.user.id, code=code)
    await interaction.send(f"## Kod ustawiony **`{code}`**!", ephemeral=True); return

@bot.command()
async def stop(ctx: nextcord.ext.commands.context.Context):
    if not await bot.is_owner(ctx.author._user): return

    await bot.close()
    db.close()
    log.info("Bot stopped")
    log.disable()
    exit(0)

@bot.command(name='eval')
async def not_eval(ctx, *, body):
    if not await bot.is_owner(ctx.author._user): return
    env = {
        'ctx': ctx,
        'channel': ctx.channel,
        'author': ctx.author,
        'guild': ctx.guild,
        'message': ctx.message,
    }

    env.update(globals())

    body = utils.cleanup_code(body)
    stdout = io.StringIO()
    err = out = None

    to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

    def paginate(text: str):
        '''Simple generator that paginates text.'''
        last = 0
        pages = []
        for curr in range(0, len(text)):
            if curr % 1980 == 0:
                pages.append(text[last:curr])
                last = curr
                appd_index = curr
        if appd_index != len(text)-1:
            pages.append(text[last:curr])
        return list(filter(lambda a: a != '', pages))
    
    try:
        exec(to_compile, env)
    except Exception as e:
        err = await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')
        return await ctx.message.add_reaction('\u2049')

    func = env['func']
    try:
        with redirect_stdout(stdout):
            ret = await func()
    except Exception as e:
        value = stdout.getvalue()
        err = await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
    else:
        value = stdout.getvalue()
        if ret is None:
            if value:
                try:
                    
                    out = await ctx.send(f'```py\n{value}\n```')
                except:
                    paginated_text = paginate(value)
                    for page in paginated_text:
                        if page == paginated_text[-1]:
                            out = await ctx.send(f'```py\n{page}\n```')
                            break
                        await ctx.send(f'```py\n{page}\n```')
        else:
            bot._last_result = ret
            try:
                out = await ctx.send(f'```py\n{value}{ret}\n```')
            except:
                paginated_text = paginate(f"{value}{ret}")
                for page in paginated_text:
                    if page == paginated_text[-1]:
                        out = await ctx.send(f'```py\n{page}\n```')
                        break
                    await ctx.send(f'```py\n{page}\n```')

    if out:
        await ctx.message.add_reaction('\u2705')  # tick
    elif err:
        await ctx.message.add_reaction('\u2049')  # x
    else:
        await ctx.message.add_reaction('\u2705')

if __name__ == '__main__':
    try:
        bot.run(settings.token)
    except KeyboardInterrupt:
        sys.exit(0)