# utils/logger.py
# Gère tous les logs : console colorée + salon Discord.

import logging
import discord
from datetime import datetime, timezone
from config.settings import settings


def setup_console_logger(name: str) -> logging.Logger:
    """
    Crée et configure un logger console avec un format lisible.
    À appeler une seule fois depuis main.py.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class DiscordLogger:
    """
    Envoie des logs structurés dans un salon Discord dédié.
    S'utilise via bot.discord_logger après initialisation.
    """

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.console = logging.getLogger("DiscordLogger")

    async def _send_embed(self, embed: discord.Embed) -> None:
        """Envoie un embed dans le salon de logs configuré."""
        channel = self.bot.get_channel(settings.LOG_CHANNEL_ID)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                self.console.warning(f"Impossible d'envoyer le log Discord : {e}")

    async def log(
        self,
        title: str,
        description: str,
        color: int = settings.COLOR_NEUTRAL,
        fields: list[dict] | None = None,
        user: discord.User | discord.Member | None = None
    ) -> None:
        """
        Envoie un log générique dans le salon Discord.

        Args:
            title: Titre de l'embed
            description: Corps du message
            color: Couleur de la barre latérale
            fields: Liste de dicts {"name": ..., "value": ..., "inline": bool}
            user: Membre ou utilisateur à mentionner dans le footer
        """
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        if fields:
            for field in fields:
                embed.add_field(
                    name=field.get("name", ""),
                    value=field.get("value", ""),
                    inline=field.get("inline", False)
                )

        if user:
            embed.set_footer(
                text=f"Action par {user} ({user.id})",
                icon_url=user.display_avatar.url
            )
        else:
            embed.set_footer(text="Bot Logs")

        self.console.info(f"[LOG] {title} — {description}")
        await self._send_embed(embed)

    async def log_success(self, title: str, description: str, **kwargs) -> None:
        await self.log(title, description, color=settings.COLOR_SUCCESS, **kwargs)

    async def log_error(self, title: str, description: str, **kwargs) -> None:
        await self.log(title, description, color=settings.COLOR_ERROR, **kwargs)

    async def log_warning(self, title: str, description: str, **kwargs) -> None:
        await self.log(title, description, color=settings.COLOR_WARNING, **kwargs)