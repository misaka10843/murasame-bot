from nonebot import get_driver, logger, get_bot
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
from typing import Set, Optional
import aiomcrcon   # py-aio-mcrcon 包的 import 名称即为 aiomcrcon
import re

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
ENABLE_MC_SYNC = getattr(config, "enable_mc_sync", True)

LIST_RE = re.compile(r"online:\s*(.*)", re.IGNORECASE)

last_players: Set[str] = set()

_rcon_client: Optional[aiomcrcon.Client] = None

async def get_rcon_client() -> aiomcrcon.Client:
    """获取或初始化持久化 RCON 客户端"""
    global _rcon_client
    if _rcon_client is None:
        _rcon_client = aiomcrcon.Client(RCON_HOST, RCON_PWD, port=RCON_PORT)

    if not hasattr(_rcon_client, "_reader") or _rcon_client._reader is None:
        try:
            await _rcon_client.connect()
            logger.info("[MC] RCON 持久化连接已建立 (aiomcrcon)")
        except Exception as e:
            logger.error(f"[MC] RCON 连接失败: {e}")
            raise e
    return _rcon_client

async def get_online_players() -> Set[str]:
    """通过 RCON 获取在线玩家列表"""
    global _rcon_client
    try:
        client = await get_rcon_client()
        response = await client.command("list")
        
        match = LIST_RE.search(response)
        if match:
            players_str = match.group(1).strip()
            if not players_str:
                return set()
            players = {p.strip() for p in players_str.split(", ")}
            return {re.sub(r"§.", "", p) for p in players if p}
        else:
            return set()
    except Exception as e:
        logger.error(f"[MC] RCON Error: {e}")
        if _rcon_client:
            try:
                await _rcon_client.close()
            except:
                pass
            _rcon_client = None
        return set()

@scheduler.scheduled_job("interval", seconds=SYNC_INTERVAL, id="mc_player_sync")
async def sync_mc_players():
    if not ENABLE_MC_SYNC:
        return
    
    global last_players
    
    current_players = await get_online_players()

    if last_players != current_players:
        logger.info(f"[MC] State changed: {last_players} -> {current_players}")

    # 计算差异
    joined = current_players - last_players
    left = last_players - current_players
    
    if not joined and not left:
        last_players = current_players # 同步状态
        return

    try:
        bot = get_bot()
        if not QQ_GROUP_ID:
            logger.warning("QQ_GROUP_ID is not configured, skipping broadcast.")
            return

        messages = []
        for player in joined:
            messages.append(f"[MC] {player} 进入了服务器")
            logger.info(f"[MC] {player} Joined")
        for player in left:
            messages.append(f"[MC] {player} 离开了服务器")
            logger.info(f"[MC] {player} Left")

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
    if not ENABLE_MC_SYNC:
        logger.info("Minecraft sync is disabled.")
        return

    global last_players
    logger.info("Initializing Minecraft player status cache...")
    last_players = await get_online_players()
    logger.info(f"Current online players: {list(last_players)}")
