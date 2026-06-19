# cogs/giveaway.py
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone

from config.settings import settings
from utils.database import db_get, db_set, db_get_all, db_delete
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.giveaway")

COLLECTION = "giveaways"


# ──────────────────────────────────────────────
# Utilitaires
# ──────────────────────────────────────────────

def parse_duration(duration: str) -> int | None:
    units   = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    total   = 0
    current = ""
    for char in duration.lower():
        if char.isdigit():
            current += char
        elif char in units and current:
            total  += int(current) * units[char]
            current = ""
        else:
            return None
    return total if total > 0 else None


def format_duration(seconds: int) -> str:
    days    = seconds // 86400
    hours   = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs    = seconds % 60
    parts   = []
    if days:    parts.append(f"{days}j")
    if hours:   parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if secs:    parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


def build_giveaway_embed(
    prize:       str,
    winners:     int,
    end_time:    datetime,
    host:        discord.Member | None,
    entries:     int = 0,
    ended:       bool = False,
    winner_ids:  list | None = None,
    min_invites: int = 0,
    min_level:   int = 0,
    min_coins:   int = 0,
) -> discord.Embed:
    color = settings.COLOR_NEUTRAL if ended else settings.COLOR_PRIMARY
    title = "🎁 Giveaway Terminé" if ended else "🎁 Giveaway !"

    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="🎉 Prix",         value=f"**{prize}**",                       inline=False)
    embed.add_field(name="🏆 Gagnants",     value=f"`{winners}`",                       inline=True)
    embed.add_field(name="👥 Participants",  value=f"`{entries}`",                       inline=True)
    embed.add_field(name="👑 Organisé par", value=host.mention if host else "Inconnu",  inline=True)

    reqs = []
    if min_invites > 0: reqs.append(f"📨 **{min_invites}** invitations minimum")
    if min_level   > 0: reqs.append(f"⭐ Niveau **{min_level}** minimum")
    if min_coins   > 0: reqs.append(f"💰 **{min_coins}** coins minimum")
    if reqs:
        embed.add_field(name="📋 Conditions pour participer", value="\n".join(reqs), inline=False)

    if ended:
        if winner_ids:
            embed.add_field(name="🎊 Gagnant(s)", value="\n".join(f"🏆 <@{uid}>" for uid in winner_ids), inline=False)
        else:
            embed.add_field(name="😢 Résultat", value="Pas assez de participants.", inline=False)
        embed.set_footer(text="Giveaway terminé • Fantoma")
    else:
        ts = int(end_time.timestamp())
        embed.add_field(name="⏰ Se termine", value=f"<t:{ts}:R> (<t:{ts}:F>)", inline=False)
        embed.set_footer(text="Clique sur 🎁 pour participer ! • Fantoma")

    return embed


# ──────────────────────────────────────────────
# Vérification des conditions
# ──────────────────────────────────────────────

async def check_requirements(
    interaction: discord.Interaction,
    giveaway:    dict,
) -> tuple[bool, str]:
    user_id  = interaction.user.id
    guild_id = interaction.guild_id

    min_invites = giveaway.get("min_invites", 0)
    min_level   = giveaway.get("min_level",   0)
    min_coins   = giveaway.get("min_coins",   0)

    # Invitations
    if min_invites > 0:
        invites_cog = interaction.client.cogs.get("Invites")
        if invites_cog:
            real = await invites_cog.get_real_invites(guild_id, user_id)
            if real < min_invites:
                return False, (
                    f"❌ Il te faut **{min_invites} invitations réelles** pour participer.\n"
                    f"Tu en as actuellement : `{real}`\n"
                    f"Utilise `&invites` pour voir ton compteur."
                )

    # Niveau XP — user_id en STRING dans la collection levels
    if min_level > 0:
        level_data    = await db_get("levels", {"guild_id": guild_id, "user_id": str(user_id)})
        current_level = level_data.get("level", 0) if level_data else 0
        if current_level < min_level:
            return False, (
                f"❌ Il te faut le **niveau {min_level}** pour participer.\n"
                f"Tu es actuellement niveau : `{current_level}`\n"
                f"Continue d'être actif pour monter en niveau !"
            )

    # Coins — user_id en STRING dans la collection economy
    if min_coins > 0:
        economy_data = await db_get("economy", {"guild_id": guild_id, "user_id": str(user_id)})
        balance      = economy_data.get("balance", 0) if economy_data else 0
        if balance < min_coins:
            return False, (
                f"❌ Il te faut **{min_coins} coins** pour participer.\n"
                f"Ton solde actuel : `{balance}` coins\n"
                f"Utilise `&balance` pour voir ton solde."
            )

    return True, ""


# ──────────────────────────────────────────────
# Vue giveaway
# ──────────────────────────────────────────────

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Participer",
        emoji="🎁",
        style=discord.ButtonStyle.primary,
        custom_id="giveaway:enter",
    )
    async def enter_giveaway(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # Defer immédiat → évite le timeout Discord de 3s
        await interaction.response.defer(ephemeral=True)

        try:
            msg_id   = str(interaction.message.id)
            giveaway = await db_get(COLLECTION, {"message_id": msg_id})

            if not giveaway:
                await interaction.followup.send("❌ Ce giveaway est introuvable.", ephemeral=True)
                return

            if giveaway.get("ended"):
                await interaction.followup.send("❌ Ce giveaway est terminé.", ephemeral=True)
                return

            user_id = str(interaction.user.id)
            entries = giveaway.get("entries", [])

            if user_id in entries:
                entries.remove(user_id)
                giveaway["entries"] = entries
                await db_set(COLLECTION, {"message_id": msg_id}, giveaway)
                await interaction.followup.send("✅ Tu as **retiré** ta participation au giveaway.", ephemeral=True)
            else:
                # Vérification des conditions
                ok, reason = await check_requirements(interaction, giveaway)
                if not ok:
                    await interaction.followup.send(reason, ephemeral=True)
                    return

                entries.append(user_id)
                giveaway["entries"] = entries
                await db_set(COLLECTION, {"message_id": msg_id}, giveaway)
                await interaction.followup.send(
                    f"🎉 Tu participes au giveaway **{giveaway['prize']}** ! Bonne chance !",
                    ephemeral=True,
                )

            # Mise à jour de l'embed
            try:
                host     = interaction.guild.get_member(giveaway["host_id"])
                end_time = datetime.fromisoformat(giveaway["end_time"])
                embed    = build_giveaway_embed(
                    prize=giveaway["prize"],
                    winners=giveaway["winners"],
                    end_time=end_time,
                    host=host,
                    entries=len(entries),
                    min_invites=giveaway.get("min_invites", 0),
                    min_level=giveaway.get("min_level", 0),
                    min_coins=giveaway.get("min_coins", 0),
                )
                await interaction.message.edit(embed=embed, view=self)
            except Exception as e:
                logger.error(f"Erreur mise à jour embed giveaway : {e}")

        except Exception as e:
            logger.error(f"Erreur bouton giveaway : {e}")
            try:
                await interaction.followup.send("❌ Erreur interne, réessaie.", ephemeral=True)
            except Exception:
                pass


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class GiveawayCog(commands.Cog, name="Giveaway"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(GiveawayView())
        self._check_task = asyncio.create_task(self._check_giveaways())

    def cog_unload(self) -> None:
        self._check_task.cancel()

    async def _check_giveaways(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                all_giveaways = await db_get_all(COLLECTION, {"ended": False})
                now           = datetime.now(timezone.utc)
                for giveaway in all_giveaways:
                    end_time = datetime.fromisoformat(giveaway["end_time"])
                    if now >= end_time:
                        await self._end_giveaway(giveaway["message_id"], giveaway)
            except Exception as e:
                logger.error(f"Erreur vérification giveaways : {e}")
            await asyncio.sleep(30)

    async def _end_giveaway(self, msg_id: str, giveaway: dict) -> None:
        giveaway["ended"] = True
        await db_set(COLLECTION, {"message_id": msg_id}, giveaway)

        entries    = giveaway.get("entries", [])
        nb_winners = giveaway.get("winners", 1)
        winner_ids = random.sample(entries, min(nb_winners, len(entries))) if entries else []

        giveaway["winner_ids"] = winner_ids
        await db_set(COLLECTION, {"message_id": msg_id}, giveaway)

        channel = self.bot.get_channel(giveaway["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(int(msg_id))
        except (discord.NotFound, discord.HTTPException):
            return

        host     = channel.guild.get_member(giveaway["host_id"])
        end_time = datetime.fromisoformat(giveaway["end_time"])

        embed = build_giveaway_embed(
            prize=giveaway["prize"],
            winners=nb_winners,
            end_time=end_time,
            host=host,
            entries=len(entries),
            ended=True,
            winner_ids=winner_ids,
            min_invites=giveaway.get("min_invites", 0),
            min_level=giveaway.get("min_level", 0),
            min_coins=giveaway.get("min_coins", 0),
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Giveaway terminé", emoji="🔒", style=discord.ButtonStyle.secondary, disabled=True))
        await message.edit(embed=embed, view=view)

        if winner_ids:
            winners_mentions = " ".join(f"<@{uid}>" for uid in winner_ids)
            host_mention     = host.mention if host else "l'organisateur"
            await channel.send(
                f"🎊 Félicitations {winners_mentions} !\n"
                f"Vous avez gagné **{giveaway['prize']}** !\n"
                f"Contactez {host_mention} pour récupérer votre prix. 🎁"
            )
        else:
            await channel.send(f"😢 Le giveaway **{giveaway['prize']}** est terminé mais personne n'a participé...")

        logger.info(f"Giveaway '{giveaway['prize']}' terminé — gagnants : {winner_ids}")

    # ──────────────────────────────────────────────
    # Commandes
    # ──────────────────────────────────────────────

    @commands.hybrid_group(name="giveaway", aliases=["gw"], description="Gère les giveaways.")
    @commands.guild_only()
    async def giveaway_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @giveaway_group.command(name="start", description="Lance un nouveau giveaway.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        duree="Durée (ex: 1h, 30m, 2d)",
        gagnants="Nombre de gagnants",
        prix="Prix à gagner",
        salon="Salon cible (optionnel)",
        min_invites="Invitations réelles minimales (0 = désactivé)",
        min_level="Niveau XP minimal (0 = désactivé)",
        min_coins="Coins minimaux (0 = désactivé)",
    )
    async def giveaway_start(
        self,
        ctx:         commands.Context,
        duree:       str,
        gagnants:    int,
        *,
        prix:        str,
        salon:       discord.TextChannel | None = None,
        min_invites: int = 0,
        min_level:   int = 0,
        min_coins:   int = 0,
    ) -> None:
        seconds = parse_duration(duree)
        if not seconds:
            await ctx.send("❌ Format invalide. Ex: `30m`, `1h`, `2d`", ephemeral=True)
            return
        if seconds < 10:
            await ctx.send("❌ Durée minimum : **10 secondes**.", ephemeral=True)
            return
        if seconds > 86400 * 30:
            await ctx.send("❌ Durée maximum : **30 jours**.", ephemeral=True)
            return
        if not 1 <= gagnants <= 20:
            await ctx.send("❌ Entre **1 et 20** gagnants.", ephemeral=True)
            return

        target   = salon or ctx.channel
        end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        embed = build_giveaway_embed(
            prize=prix, winners=gagnants, end_time=end_time, host=ctx.author,
            entries=0, min_invites=min_invites, min_level=min_level, min_coins=min_coins,
        )
        msg = await target.send(embed=embed, view=GiveawayView())

        await db_set(COLLECTION, {"message_id": str(msg.id)}, {
            "message_id":  str(msg.id),
            "prize":       prix,
            "winners":     gagnants,
            "host_id":     ctx.author.id,
            "channel_id":  target.id,
            "end_time":    end_time.isoformat(),
            "entries":     [],
            "ended":       False,
            "winner_ids":  [],
            "min_invites": min_invites,
            "min_level":   min_level,
            "min_coins":   min_coins,
        })

        await ctx.send(f"✅ Giveaway lancé dans {target.mention} pour **{format_duration(seconds)}** !", ephemeral=True)
        logger.info(f"Giveaway '{prix}' lancé par {ctx.author}")

    @giveaway_group.command(name="end", description="Termine immédiatement un giveaway.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID du message du giveaway")
    async def giveaway_end(self, ctx: commands.Context, message_id: str) -> None:
        giveaway = await db_get(COLLECTION, {"message_id": message_id})
        if not giveaway:
            await ctx.send("❌ Giveaway introuvable.", ephemeral=True)
            return
        if giveaway.get("ended"):
            await ctx.send("❌ Ce giveaway est déjà terminé.", ephemeral=True)
            return
        await self._end_giveaway(message_id, giveaway)
        await ctx.send("✅ Giveaway terminé !", ephemeral=True)

    @giveaway_group.command(name="reroll", description="Relance le tirage d'un giveaway terminé.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(message_id="ID du message du giveaway")
    async def giveaway_reroll(self, ctx: commands.Context, message_id: str) -> None:
        giveaway = await db_get(COLLECTION, {"message_id": message_id})
        if not giveaway:
            await ctx.send("❌ Giveaway introuvable.", ephemeral=True)
            return
        if not giveaway.get("ended"):
            await ctx.send("❌ Ce giveaway n'est pas encore terminé.", ephemeral=True)
            return
        entries = giveaway.get("entries", [])
        if not entries:
            await ctx.send("❌ Aucun participant.", ephemeral=True)
            return
        new_winners            = random.sample(entries, min(giveaway.get("winners", 1), len(entries)))
        giveaway["winner_ids"] = new_winners
        await db_set(COLLECTION, {"message_id": message_id}, giveaway)
        winners_mentions = " ".join(f"<@{uid}>" for uid in new_winners)
        embed = discord.Embed(
            title="🎲 Nouveau tirage !",
            description=f"Nouveaux gagnants de **{giveaway['prize']}** :\n{winners_mentions} 🎊",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Fantoma • Giveaway Reroll")
        await ctx.send(embed=embed)

    @giveaway_group.command(name="list", description="Affiche les giveaways actifs.")
    async def giveaway_list(self, ctx: commands.Context) -> None:
        active = await db_get_all(COLLECTION, {"ended": False})
        if not active:
            await ctx.send("ℹ️ Aucun giveaway actif.", ephemeral=True)
            return
        embed = discord.Embed(title="🎁 Giveaways actifs", color=settings.COLOR_INFO, timestamp=datetime.now(timezone.utc))
        for gw in active[:10]:
            end_time        = datetime.fromisoformat(gw["end_time"])
            channel         = self.bot.get_channel(gw["channel_id"])
            channel_mention = channel.mention if channel else "Salon inconnu"
            reqs            = []
            if gw.get("min_invites"): reqs.append(f"📨 {gw['min_invites']} invitations")
            if gw.get("min_level"):   reqs.append(f"⭐ Niveau {gw['min_level']}")
            if gw.get("min_coins"):   reqs.append(f"💰 {gw['min_coins']} coins")
            embed.add_field(
                name=f"🎉 {gw['prize']}",
                value=(
                    f"Salon : {channel_mention}\n"
                    f"Participants : `{len(gw.get('entries', []))}`\n"
                    f"Gagnants : `{gw['winners']}`\n"
                    f"Se termine : <t:{int(end_time.timestamp())}:R>\n"
                    f"Conditions : {', '.join(reqs) if reqs else 'Aucune'}\n"
                    f"ID : `{gw['message_id']}`"
                ),
                inline=False,
            )
        embed.set_footer(text="Fantoma • Giveaways")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawayCog(bot))
    logger.info("Cog 'Giveaway' chargé.")