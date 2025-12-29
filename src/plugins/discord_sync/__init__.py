import asyncio
import discord
import json
from typing import Dict, List, Optional

from nonebot import get_driver, logger, get_bots, on_command
from nonebot.matcher import Matcher

try:
    from nonebot.adapters.onebot.v11 import Bot as OneBot
except ImportError:
    OneBot = None
    logger.warning("未检测到 OneBot V11 适配器，推送功能可能无法使用")

driver = get_driver()
config = driver.config

DISCORD_TOKEN = getattr(config, "discord_token", None)
DISCORD_PROXY = getattr(config, "discord_proxy", "")
DISCORD_GUILD_ID = int(getattr(config, "discord_guild_id", 0))
QQ_GROUP_ID = getattr(config, "qq_group_id", 0)

LEAVE_DELAY_SECONDS = 60

if not DISCORD_TOKEN and hasattr(config, "discord_bots"):
    try:
        bots_config = json.loads(config.discord_bots)
        DISCORD_TOKEN = bots_config[0]["token"]
    except:
        pass

voice_cache: Dict[str, str] = {}
user_name_cache: Dict[str, str] = {}

pending_leave_tasks: Dict[str, Dict] = {}


class DiscordMonitor(discord.Client):
    async def on_ready(self):
        logger.info(f"[Discord] 监控启动 | Guild: {DISCORD_GUILD_ID}")

        guild = self.get_guild(DISCORD_GUILD_ID)
        if not guild:
            logger.warning(f"[Discord] 无法获取 Guild ID: {DISCORD_GUILD_ID}")
            return

        voice_cache.clear()
        user_name_cache.clear()

        count = 0
        for channel in guild.voice_channels:
            for member in channel.members:
                # 过滤机器人
                if member.bot:
                    continue

                user_id = str(member.id)
                voice_cache[user_id] = channel.name
                user_name_cache[user_id] = member.display_name
                count += 1

        logger.info(f"[Discord] 初始同步完成: 当前 {count} 人(非机器人)在语音频道")

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.guild.id != DISCORD_GUILD_ID:
            return
        if member.bot:
            return
        if before.channel == after.channel:
            return

        user_id = str(member.id)
        name = member.display_name
        user_name_cache[user_id] = name

        # 更新缓存
        if after.channel:
            voice_cache[user_id] = after.channel.name
        else:
            voice_cache.pop(user_id, None)

        if before.channel is not None and after.channel is None:
            # 启动延迟任务，不立即推送
            logger.debug(f"{name} 离开了 {before.channel.name}，启动 {LEAVE_DELAY_SECONDS}s 延迟检查...")
            task = asyncio.create_task(self.wait_and_push_leave(user_id, name, before.channel.name))
            pending_leave_tasks[user_id] = {
                "task": task,
                "channel": before.channel.name
            }
            return

        elif before.channel is None and after.channel is not None:
            # 检查是否有正在等待的离开任务
            if user_id in pending_leave_tasks:
                pending_info = pending_leave_tasks.pop(user_id)
                old_task = pending_info["task"]
                old_channel = pending_info["channel"]
                # 取消之前的离开通知
                old_task.cancel()
                logger.debug(f"{name} 在延迟时间内重新进入，取消离开通知。")
                # 判断：如果是回到了同一个频道 -> 视为掉线重连，什么都不发
                if old_channel == after.channel.name:
                    logger.info(f"[Discord Sync] {name} 掉线重连 (相同频道)，已忽略通知。")
                    return
                else:
                    # 如果回到了不同频道 -> 视为“切换”
                    msg_content = f"{name} 🔄 切换频道: {old_channel} -> {after.channel.name}"
                    logger.info(f"[Discord Sync] {msg_content} (快速重连异频道)")
                    await try_active_push(msg_content)
                    return

            msg_content = f"{name} 🟢 进入了语音频道: {after.channel.name}"
            logger.info(f"[Discord Sync] {msg_content}")
            await try_active_push(msg_content)
            return

        # 这种通常是用户在客户端直接点的切换，通常不需要延迟，直接发
        elif before.channel is not None and after.channel is not None:
            # 如果此时正好有一个 pending 的离开任务（极罕见情况），也清理掉
            if user_id in pending_leave_tasks:
                pending_leave_tasks.pop(user_id)["task"].cancel()

            msg_content = f"{name} 🔄 切换频道: {before.channel.name} -> {after.channel.name}"
            logger.info(f"[Discord Sync] {msg_content}")
            await try_active_push(msg_content)

    async def wait_and_push_leave(self, user_id: str, name: str, channel_name: str):
        """延迟推送离开消息"""
        try:
            await asyncio.sleep(LEAVE_DELAY_SECONDS)

            # 时间到了，任务没被取消，说明用户真的走了
            msg_content = f"{name} 🔴 离开了语音频道: {channel_name}"
            logger.info(f"[Discord Sync] {msg_content} (延迟确认)")
            await try_active_push(msg_content)

            # 清理字典
            if user_id in pending_leave_tasks:
                del pending_leave_tasks[user_id]

        except asyncio.CancelledError:
            # 任务被取消，说明用户回来了，什么都不做
            pass


# --- 初始化 Discord ---
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True

discord_client = DiscordMonitor(intents=intents, proxy=DISCORD_PROXY)


async def try_active_push(content: str):
    """尝试使用 OneBot V11 推送消息"""
    bots = get_bots()
    if not bots:
        return

    try:
        target_group_id = int(QQ_GROUP_ID)
    except (ValueError, TypeError):
        return

    for bot in bots.values():
        if OneBot and isinstance(bot, OneBot):
            try:
                await bot.send_group_msg(group_id=target_group_id, message=content)
                return
            except Exception as e:
                logger.error(f"[Push Error] {e}")


# --- 命令处理 ---
status_cmd = on_command("status", aliases={"voice", "语音状态"})


@status_cmd.handle()
async def handle_status(matcher: Matcher):
    if not voice_cache:
        await matcher.finish("🎧 当前 Discord 语音频道无人。")

    channels: Dict[str, List[str]] = {}
    for uid, c_name in voice_cache.items():
        if c_name not in channels:
            channels[c_name] = []
        channels[c_name].append(user_name_cache.get(uid, uid))

    msg_lines = ["🎧 Discord 语音实时状态:"]
    for c_name, users in channels.items():
        msg_lines.append(f"\n📁 {c_name}:")
        for u in users:
            msg_lines.append(f"  - {u}")

    await matcher.finish("\n".join(msg_lines))


# --- 启动 Hook ---
@driver.on_startup
async def start_discord():
    if DISCORD_TOKEN:
        asyncio.create_task(discord_client.start(DISCORD_TOKEN))
    else:
        logger.error("未配置 DISCORD_TOKEN")