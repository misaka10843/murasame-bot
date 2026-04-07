import re
from typing import List, Set
from nonebot import get_driver, logger, get_bot
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
from py_aio_mcrcon import MCRconClient

__plugin_meta__ = PluginMetadata(
    name="Minecraft 玩家状态同步",
    description="通过 RCON 同步 MC 玩家进出消息到群聊",
    usage="自动运行，需在 .env 中配置 MC_RCON_* 等信息",
)

# 加载配置
config = get_driver().config
RCON_HOST = getattr(config, "mc_rcon_host", "127.0.0.1")
RCON_PORT = int(getattr(config, "mc_rcon_port", 25575))
RCON_PWD = getattr(config, "mc_rcon_pwd", "your_secure_password")
SYNC_INTERVAL = int(getattr(config, "mc_sync_interval", 30))
QQ_GROUP_ID = getattr(config, "qq_group_id", None)

# 玩家列表缓存
last_players: Set[str] = set()

# 正则表达式用于解析 list 命令结果
# 典型输出: "There are 2 of a max 20 players online: Player1, Player2"
# 或者 "There are 0 of a max 20 players online:"
LIST_RE = re.compile(r"online: (.*)")

async def get_online_players() -> Set[str]:
    """通过 RCON 获取在线玩家列表"""
    try:
        async with MCRconClient(RCON_HOST, RCON_PORT, RCON_PWD) as client:
            response = await client.send_command("list")
            logger.debug(f"MC RCON response: {response}")
            
            match = LIST_RE.search(response)
            if match:
                players_str = match.group(1).strip()
                if not players_str:
                    return set()
                # 逗号分隔并去除空格
                return {p.strip() for p in players_str.split(", ")}
            return set()
    except Exception as e:
        logger.error(f"Failed to connect to MC RCON: {e}")
        return set()

@scheduler.scheduled_job("interval", seconds=SYNC_INTERVAL, id="mc_player_sync")
async def sync_mc_players():
    global last_players
    
    current_players = await get_online_players()
    
    # 如果初次运行且之前没有记录，先同步一次不发消息
    if not last_players and current_players:
        last_players = current_players
        return

    # 计算差异
    joined = current_players - last_players
    left = last_players - current_players
    
    if not joined and not left:
        return

    try:
        bot = get_bot()
        if not QQ_GROUP_ID:
            logger.warning("QQ_GROUP_ID is not configured, skipping broadcast.")
            return

        messages = []
        for player in joined:
            messages.append(f"[MC] {player} 进入了服务器")
        for player in left:
            messages.append(f"[MC] {player} 离开了服务器")

        if messages:
            msg = "\n".join(messages)
            await bot.send_group_msg(group_id=int(QQ_GROUP_ID), message=msg)
            logger.info(f"Broadcasted MC status: {msg}")

    except Exception as e:
        logger.error(f"Error sending group message: {e}")
    finally:
        last_players = current_players

@get_driver().on_startup
async def init_mc_status():
    global last_players
    logger.info("Initializing Minecraft player status cache...")
    last_players = await get_online_players()
    logger.info(f"Current online players: {list(last_players)}")
