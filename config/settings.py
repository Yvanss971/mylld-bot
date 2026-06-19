# config/settings.py
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    TOKEN:          str = os.getenv("DISCORD_TOKEN", "")
    GUILD_ID:       int = int(os.getenv("GUILD_ID", 0))
    LOG_CHANNEL_ID: int = int(os.getenv("LOG_CHANNEL_ID", 0))
    MONGODB_URI:    str = os.getenv("MONGODB_URI", "")
    BOT_OWNER_ID:   int = int(os.getenv("BOT_OWNER_ID", 0))

    COLOR_PRIMARY:  int = 0x5865F2
    COLOR_SUCCESS:  int = 0x57F287
    COLOR_WARNING:  int = 0xFEE75C
    COLOR_ERROR:    int = 0xED4245
    COLOR_INFO:     int = 0xEB459E
    COLOR_NEUTRAL:  int = 0x2B2D31


settings = Settings()