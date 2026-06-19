# cogs/polls.py
# Système de sondages interactifs avec boutons et résultats en temps réel.

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from config.settings import settings
from utils.database import db_get, db_set, db_get_all
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.polls")

COLLECTION = "polls"


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ──────────────────────────────────────────────
# Utilitaires
# ──────────────────────────────────────────────

def build_progress_bar(count: int, total: int, length: int = 10) -> str:
    """Génère une barre de progression pour un choix."""
    if total == 0:
        filled = 0
    else:
        filled = int((count / total) * length)
    percent = int((count / total) * 100) if total > 0 else 0
    return "█" * filled + "░" * (length - filled) + f" **{percent}%** (`{count}`)"


def build_poll_embed(
    question:   str,
    choices:    list[str],
    votes:      dict,
    author:     discord.Member | None,
    ended:      bool = False,
    end_time:   datetime | None = None
) -> discord.Embed:
    """Construit l'embed du sondage."""
    total_votes = sum(len(v) for v in votes.values())

    if ended:
        color = settings.COLOR_NEUTRAL
        title = "📊 Sondage Terminé"
    else:
        color = settings.COLOR_PRIMARY
        title = "📊 Sondage"

    embed = discord.Embed(
        title=title,
        description=f"**{question}**",
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

    # Résultats pour chaque choix
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, choice in enumerate(choices):
        count = len(votes.get(str(i), []))
        bar   = build_progress_bar(count, total_votes)
        embed.add_field(
            name=f"{emojis[i]} {choice}",
            value=bar,
            inline=False
        )

    embed.add_field(
        name="👥 Total des votes",
        value=f"`{total_votes}` vote(s)",
        inline=True
    )

    if author:
        embed.add_field(
            name="👑 Créé par",
            value=author.mention,
            inline=True
        )

    if end_time and not ended:
        embed.add_field(
            name="⏰ Se termine",
            value=f"<t:{int(end_time.timestamp())}:R>",
            inline=True
        )

    if ended and total_votes > 0:
        # Trouve le/les gagnant(s)
        max_votes = max(len(v) for v in votes.values())
        winners   = [
            choices[int(i)] for i, v in votes.items()
            if len(v) == max_votes
        ]
        embed.add_field(
            name="🏆 Résultat",
            value=" • ".join(f"**{w}**" for w in winners),
            inline=False
        )

    footer = "Fantoma • Sondage terminé" if ended else "Fantoma • Clique pour voter"
    embed.set_footer(text=footer)
    return embed


# ──────────────────────────────────────────────
# Vue sondage
# ──────────────────────────────────────────────

class PollButton(discord.ui.Button):
    """Bouton de vote pour un choix."""

    def __init__(self, choice_index: int, choice_label: str):
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        super().__init__(
            label=choice_label[:80],
            emoji=emojis[choice_index],
            style=discord.ButtonStyle.secondary,
            custom_id=f"poll:vote:{choice_index}"
        )
        self.choice_index = choice_index

    async def callback(self, interaction: discord.Interaction) -> None:
        msg_id = str(interaction.message.id)
        poll   = await db_get(COLLECTION, {"message_id": msg_id})

        if not poll:
            await interaction.response.send_message("❌ Sondage introuvable.", ephemeral=True)
            return

        if poll.get("ended"):
            await interaction.response.send_message("❌ Ce sondage est terminé.", ephemeral=True)
            return

        user_id     = str(interaction.user.id)
        choice_key  = str(self.choice_index)
        votes       = poll.get("votes", {})
        choices     = poll.get("choices", [])

        # Vérifie si l'utilisateur a déjà voté pour CE choix
        already_voted_this = user_id in votes.get(choice_key, [])

        # Retire le vote précédent de tous les choix
        for key in votes:
            if user_id in votes[key]:
                votes[key].remove(user_id)

        if already_voted_this:
            # Si re-clic sur le même bouton → retire le vote
            response_msg = "✅ Ton vote a été **retiré**."
        else:
            # Ajoute le nouveau vote
            if choice_key not in votes:
                votes[choice_key] = []
            votes[choice_key].append(user_id)
            response_msg = f"✅ Tu as voté pour **{choices[self.choice_index]}** !"

        poll["votes"] = votes
        await db_set(COLLECTION, {"message_id": msg_id}, poll)

        # Met à jour l'embed
        try:
            author   = interaction.guild.get_member(poll["author_id"])
            end_time = parse_dt(poll["end_time"]) if poll.get("end_time") else None
            embed    = build_poll_embed(
                question=poll["question"],
                choices=choices,
                votes=votes,
                author=author,
                end_time=end_time
            )
            await interaction.response.edit_message(embed=embed, view=self.view)
        except Exception as e:
            logger.error(f"Erreur mise à jour embed sondage : {e}")
            await interaction.response.send_message(response_msg, ephemeral=True)
            return

        await interaction.followup.send(response_msg, ephemeral=True)


class PollView(discord.ui.View):
    """Vue persistante avec les boutons de vote."""

    def __init__(self, choices: list[str]):
        super().__init__(timeout=None)
        for i, choice in enumerate(choices[:10]):
            self.add_item(PollButton(i, choice))


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class PollsCog(commands.Cog, name="Sondages"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._check_task = asyncio.create_task(self._check_polls())
        # Réenregistre les vues persistantes au démarrage
        self._restore_views()

    def _restore_views(self) -> None:
        """Lance la restauration des vues en arrière-plan."""
        asyncio.create_task(self._async_restore_views())

    async def _async_restore_views(self) -> None:
        """Réenregistre les vraies vues depuis MongoDB au démarrage."""
        await self.bot.wait_until_ready()
        try:
            active_polls = await db_get_all(COLLECTION, {"ended": False})
            for poll in active_polls:
                choices = poll.get("choices", [])
                if choices:
                    self.bot.add_view(PollView(choices))
            logger.info(f"{len(active_polls)} vue(s) de sondage restaurée(s).")
        except Exception as e:
            logger.error(f"Erreur restauration vues sondages : {e}")

    def cog_unload(self) -> None:
        self._check_task.cancel()

    # ──────────────────────────────────────────────
    # Vérification automatique des sondages expirés
    # ──────────────────────────────────────────────

    async def _check_polls(self) -> None:
        """Vérifie toutes les 30 secondes si des sondages sont expirés."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                active_polls = await db_get_all(COLLECTION, {"ended": False})
                now          = datetime.now(timezone.utc)

                for poll in active_polls:
                    if not poll.get("end_time"):
                        continue
                    end_time = parse_dt(poll["end_time"])
                    if now >= end_time:
                        await self._end_poll(poll["message_id"], poll)

            except Exception as e:
                logger.error(f"Erreur vérification sondages : {e}")

            await asyncio.sleep(30)

    async def _end_poll(self, msg_id: str, poll: dict) -> None:
        """Termine un sondage et affiche les résultats finaux."""
        poll["ended"] = True
        await db_set(COLLECTION, {"message_id": msg_id}, poll)

        channel = self.bot.get_channel(poll["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(int(msg_id))
        except (discord.NotFound, discord.HTTPException):
            return

        author  = channel.guild.get_member(poll["author_id"])
        embed   = build_poll_embed(
            question=poll["question"],
            choices=poll["choices"],
            votes=poll.get("votes", {}),
            author=author,
            ended=True
        )

        # Désactive tous les boutons
        view = discord.ui.View()
        for i, choice in enumerate(poll["choices"]):
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            btn    = discord.ui.Button(
                label=choice[:80],
                emoji=emojis[i],
                style=discord.ButtonStyle.secondary,
                disabled=True
            )
            view.add_item(btn)

        await message.edit(embed=embed, view=view)
        logger.info(f"Sondage '{poll['question']}' terminé.")

    # ──────────────────────────────────────────────
    # /sondage & &sondage
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="sondage",
        description="Crée un sondage avec plusieurs choix."
    )
    @commands.guild_only()
    @app_commands.describe(
        question="La question du sondage",
        choix="Les choix séparés par | (ex: Oui|Non|Peut-être)",
        duree="Durée optionnelle (ex: 1h, 30m, 2d)"
    )
    async def sondage(
        self,
        ctx:      commands.Context,
        question: str,
        choix:    str,
        duree:    str | None = None
    ) -> None:
        choices = [c.strip() for c in choix.split("|") if c.strip()]

        if len(choices) < 2:
            await ctx.send(
                "❌ Donne au moins **2 choix** séparés par `|`.\n"
                "Ex: `&sondage \"Ta question ?\" \"Choix 1|Choix 2|Choix 3\"`",
                ephemeral=True
            )
            return

        if len(choices) > 10:
            await ctx.send("❌ Maximum **10 choix** par sondage.", ephemeral=True)
            return

        # Parse la durée
        end_time = None
        if duree:
            from cogs.giveaway import parse_duration
            seconds = parse_duration(duree)
            if seconds:
                end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        # Initialise les votes
        votes = {str(i): [] for i in range(len(choices))}

        embed = build_poll_embed(
            question=question,
            choices=choices,
            votes=votes,
            author=ctx.author,
            end_time=end_time
        )

        view = PollView(choices)
        msg  = await ctx.send(embed=embed, view=view)

        # Sauvegarde dans MongoDB
        await db_set(COLLECTION, {"message_id": str(msg.id)}, {
            "message_id": str(msg.id),
            "question":   question,
            "choices":    choices,
            "votes":      votes,
            "author_id":  ctx.author.id,
            "channel_id": ctx.channel.id,
            "end_time":   end_time.isoformat() if end_time else None,
            "ended":      False
        })

        logger.info(f"Sondage '{question}' créé par {ctx.author}")

    # ──────────────────────────────────────────────
    # /quickpoll & &quickpoll
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="quickpoll",
        aliases=["qp"],
        description="Crée un sondage rapide Oui/Non."
    )
    @commands.guild_only()
    @app_commands.describe(question="La question du sondage")
    async def quickpoll(self, ctx: commands.Context, *, question: str) -> None:
        choices  = ["✅ Oui", "❌ Non", "🤷 Peut-être"]
        votes    = {str(i): [] for i in range(len(choices))}
        end_time = datetime.now(timezone.utc) + timedelta(hours=24)

        embed = build_poll_embed(
            question=question,
            choices=choices,
            votes=votes,
            author=ctx.author,
            end_time=end_time
        )

        view = PollView(choices)
        msg  = await ctx.send(embed=embed, view=view)

        await db_set(COLLECTION, {"message_id": str(msg.id)}, {
            "message_id": str(msg.id),
            "question":   question,
            "choices":    choices,
            "votes":      votes,
            "author_id":  ctx.author.id,
            "channel_id": ctx.channel.id,
            "end_time":   end_time.isoformat(),
            "ended":      False
        })

        logger.info(f"QuickPoll '{question}' créé par {ctx.author}")

    # ──────────────────────────────────────────────
    # /sondage-end & &sondage-end
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="sondage-end",
        description="Termine immédiatement un sondage."
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID du message du sondage")
    async def sondage_end(self, ctx: commands.Context, message_id: str) -> None:
        poll = await db_get(COLLECTION, {"message_id": message_id})

        if not poll:
            await ctx.send("❌ Sondage introuvable.", ephemeral=True)
            return

        if poll.get("ended"):
            await ctx.send("❌ Ce sondage est déjà terminé.", ephemeral=True)
            return

        await self._end_poll(message_id, poll)
        await ctx.send("✅ Sondage terminé !", ephemeral=True)

    # ──────────────────────────────────────────────
    # /sondage-list & &sondage-list
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="sondage-list",
        description="Affiche les sondages actifs."
    )
    @commands.guild_only()
    async def sondage_list(self, ctx: commands.Context) -> None:
        active = await db_get_all(COLLECTION, {"ended": False})

        if not active:
            await ctx.send("ℹ️ Aucun sondage actif en ce moment.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📊 Sondages actifs",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )

        for poll in active[:10]:
            total_votes = sum(len(v) for v in poll.get("votes", {}).values())
            end_time    = parse_dt(poll["end_time"]) if poll.get("end_time") else None
            end_str     = f"<t:{int(end_time.timestamp())}:R>" if end_time else "Pas de limite"

            embed.add_field(
                name=f"❓ {poll['question'][:50]}",
                value=(
                    f"Votes : `{total_votes}`\n"
                    f"Choix : `{len(poll['choices'])}`\n"
                    f"Se termine : {end_str}\n"
                    f"ID : `{poll['message_id']}`"
                ),
                inline=False
            )

        embed.set_footer(text="Fantoma • Sondages")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PollsCog(bot))
    logger.info("Cog 'Sondages' chargé.")