from pydantic import BaseModel

class Config(BaseModel):
    discord_guild_id: str|int
    qq_group_id: str|int
