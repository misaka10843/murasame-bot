from nonebot import on_command
from nonebot.adapters.qq import Message,MessageEvent
from nonebot.params import CommandArg
ping=on_command("ping",aliases={"乒"})

@ping.handle()
async def _(event:MessageEvent,args:Message = CommandArg()):
    await ping.send("pong! "+args)