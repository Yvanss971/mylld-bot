# cogs/custom_commands.py
# Système de commandes personnalisées créées par les admins.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config.settings import settings
from utils.database import db_get, db_set, db_delete, db_get_all
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.custom_commands")

COLLECTION = "custom_commands"

# Commandes réservées — ne peuvent pas être écrasées
RESERVED_COMMANDS = {
    "help", "ping", "rank", "leaderboard", "xp-add", "xp-reset",
    "reglement", "reglement-preview", "ticket-panel", "ticket",
    "ticket-config", "roles-panel", "autorole", "bienvenue",
    "aurevoir", "retour", "warn", "warns", "clearwarns", "automod",
    "clear", "slowmode", "lock", "unlock", "say", "embed",
    "userinfo", "serverinfo", "avatar", "sondage", "quickpoll",
    "sondage-end", "sondage-list", "giveaway", "de", "pfc",
    "coinflip", "8ball", "chiffre", "roulette", "choix",
    "tictactoe", "cmd-add", "cmd-edit", "cmd-delete", "cmd-list",
    "cmd-info", "rappel", "remind", "remindme", "rappels",
    "reminders", "rappel-annuler", "cancelreminder", "uptime", "up",
    "mute", "timeout", "unmute", "demute", "kick", "ban", "tempban", "unban",
    "sanctions", "history", "logs", "xp-config", "daily", "balance", "bal",
    "coins", "pay", "payer", "shop", "buy", "acheter",
    "shop-add-role", "shop-remove", "anniversaire", "birthday",
    "ship", "pp", "slap", "hug", "calin", "meme",
    "stats-counter", "compteurs", "reaction-role", "rr",
    "schedule", "programmation"
}


# ──────────────────────────────────────────────
# Utilitaires
# ──────────────────────────────────────────────

def format_response(text: str, member: discord.Member) -> str:
    """Remplace les variables dans la réponse."""
    return (
        text
        .replace("{mention}", member.mention)
        .replace("{user}",    member.display_name)
        .replace("{serveur}", member.guild.name)
        .replace("{count}",   str(member.guild.member_count))
        .replace("{date}",    datetime.now(timezone.utc).strftime("%d/%m/%Y"))
    )


def parse_embed_syntax(text: str) -> discord.Embed | None:
    """
    Parse la syntaxe d'embed custom.
    Format : {embed: titre | description | couleur(optionnel)}
    Retourne un Embed ou None si ce n'est pas un embed.
    """
    text = text.strip()
    if not (text.startswith("{embed:") and text.endswith("}")):
        return None

    content = text[7:-1].strip()
    parts   = [p.strip() for p in content.split("|")]

    if len(parts) < 2:
        return None

    title       = parts[0]
    description = parts[1]
    color       = settings.COLOR_PRIMARY

    if len(parts) >= 3:
        color_map = {
            "rouge":  settings.COLOR_ERROR,
            "vert":   settings.COLOR_SUCCESS,
            "jaune":  settings.COLOR_WARNING,
            "bleu":   settings.COLOR_PRIMARY,
            "rose":   settings.COLOR_INFO,
            "gris":   settings.COLOR_NEUTRAL,
        }
        color = color_map.get(parts[2].lower(), settings.COLOR_PRIMARY)

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Fantoma")
    return embed


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class CustomCommandsCog(commands.Cog, name="Commandes Custom"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────
    # Listener — intercepte les messages avec &
    # ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Détecte les commandes custom et répond."""
        if message.author.bot or not message.guild:
            return

        # Vérifie si le message commence par &
        if not message.content.startswith("&"):
            return

        # Extrait le nom de la commande
        parts      = message.content[1:].split()
        if not parts:
            return

        cmd_name   = parts[0].lower()
        cmd_args   = " ".join(parts[1:]) if len(parts) > 1 else ""

        # Ignore si c'est une vraie commande du bot
        if cmd_name in RESERVED_COMMANDS:
            return

        # Cherche la commande dans MongoDB
        cmd = await db_get(COLLECTION, {
            "guild_id": message.guild.id,
            "name":     cmd_name
        })

        if not cmd:
            return

        # Incrémente le compteur d'utilisation
        cmd["uses"] = cmd.get("uses", 0) + 1
        await db_set(COLLECTION, {"guild_id": message.guild.id, "name": cmd_name}, cmd)

        # Formate la réponse
        response = format_response(cmd["response"], message.author)

        # Remplace {args} par les arguments fournis
        response = response.replace("{args}", cmd_args)

        # Vérifie si c'est un embed
        embed = parse_embed_syntax(response)

        try:
            if embed:
                # Applique aussi les variables dans le titre et la description
                embed.title       = format_response(embed.title or "",       message.author)
                embed.description = format_response(embed.description or "", message.author)
                await message.channel.send(embed=embed)
            else:
                await message.channel.send(response)
        except discord.Forbidden:
            pass

        logger.debug(f"Commande custom '&{cmd_name}' utilisée par {message.author}")

    # ──────────────────────────────────────────────
    # /cmd-add & &cmd-add
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="cmd-add",
        description="Crée une commande personnalisée."
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        nom="Nom de la commande (sans &)",
        reponse="Réponse de la commande"
    )
    async def cmd_add(
        self,
        ctx:     commands.Context,
        nom:     str,
        *,
        reponse: str
    ) -> None:
        nom = nom.lower().strip()

        # Vérifie que le nom est valide
        if not nom.replace("-", "").replace("_", "").isalnum():
            await ctx.send(
                "❌ Le nom ne peut contenir que des **lettres, chiffres, - et _**.",
                ephemeral=True
            )
            return

        if nom in RESERVED_COMMANDS:
            await ctx.send(
                f"❌ `{nom}` est une commande réservée du bot.",
                ephemeral=True
            )
            return

        if len(nom) > 32:
            await ctx.send("❌ Le nom ne peut pas dépasser **32 caractères**.", ephemeral=True)
            return

        if len(reponse) > 2000:
            await ctx.send("❌ La réponse ne peut pas dépasser **2000 caractères**.", ephemeral=True)
            return

        # Vérifie si la commande existe déjà
        existing = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "name": nom})
        if existing:
            await ctx.send(
                f"❌ La commande `&{nom}` existe déjà. Utilise `&cmd-edit {nom}` pour la modifier.",
                ephemeral=True
            )
            return

        # Compte le nombre de commandes custom
        all_cmds = await db_get_all(COLLECTION, {"guild_id": ctx.guild.id})
        if len(all_cmds) >= 50:
            await ctx.send("❌ Maximum **50 commandes** custom par serveur.", ephemeral=True)
            return

        # Sauvegarde
        await db_set(COLLECTION, {"guild_id": ctx.guild.id, "name": nom}, {
            "guild_id":   ctx.guild.id,
            "name":       nom,
            "response":   reponse,
            "created_by": ctx.author.id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "uses":       0
        })

        embed = discord.Embed(
            title="✅ Commande créée",
            description=f"La commande `&{nom}` est maintenant disponible !",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="📝 Réponse", value=reponse[:500], inline=False)
        embed.set_footer(text=f"Créée par {ctx.author}")
        await ctx.send(embed=embed)
        logger.info(f"Commande custom '&{nom}' créée par {ctx.author}")

    # ──────────────────────────────────────────────
    # /cmd-edit & &cmd-edit
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="cmd-edit",
        description="Modifie une commande personnalisée."
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        nom="Nom de la commande à modifier",
        reponse="Nouvelle réponse"
    )
    async def cmd_edit(
        self,
        ctx:     commands.Context,
        nom:     str,
        *,
        reponse: str
    ) -> None:
        nom = nom.lower().strip()
        cmd = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "name": nom})

        if not cmd:
            await ctx.send(f"❌ La commande `&{nom}` n'existe pas.", ephemeral=True)
            return

        if len(reponse) > 2000:
            await ctx.send("❌ La réponse ne peut pas dépasser **2000 caractères**.", ephemeral=True)
            return

        cmd["response"]   = reponse
        cmd["edited_by"]  = ctx.author.id
        cmd["edited_at"]  = datetime.now(timezone.utc).isoformat()
        await db_set(COLLECTION, {"guild_id": ctx.guild.id, "name": nom}, cmd)

        embed = discord.Embed(
            title="✅ Commande modifiée",
            description=f"La commande `&{nom}` a été mise à jour.",
            color=settings.COLOR_SUCCESS
        )
        embed.add_field(name="📝 Nouvelle réponse", value=reponse[:500], inline=False)
        await ctx.send(embed=embed)
        logger.info(f"Commande custom '&{nom}' modifiée par {ctx.author}")

    # ──────────────────────────────────────────────
    # /cmd-delete & &cmd-delete
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="cmd-delete",
        aliases=["cmd-del", "cmd-remove"],
        description="Supprime une commande personnalisée."
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(nom="Nom de la commande à supprimer")
    async def cmd_delete(self, ctx: commands.Context, nom: str) -> None:
        nom = nom.lower().strip()
        cmd = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "name": nom})

        if not cmd:
            await ctx.send(f"❌ La commande `&{nom}` n'existe pas.", ephemeral=True)
            return

        await db_delete(COLLECTION, {"guild_id": ctx.guild.id, "name": nom})

        await ctx.send(embed=discord.Embed(
            title="🗑️ Commande supprimée",
            description=f"La commande `&{nom}` a été supprimée.",
            color=settings.COLOR_WARNING
        ))
        logger.info(f"Commande custom '&{nom}' supprimée par {ctx.author}")

    # ──────────────────────────────────────────────
    # /cmd-list & &cmd-list
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="cmd-list",
        description="Liste toutes les commandes personnalisées du serveur."
    )
    @commands.guild_only()
    async def cmd_list(self, ctx: commands.Context) -> None:
        all_cmds = await db_get_all(COLLECTION, {"guild_id": ctx.guild.id})

        if not all_cmds:
            await ctx.send(
                "ℹ️ Aucune commande personnalisée sur ce serveur.\n"
                "Crée-en une avec `&cmd-add <nom> <réponse>` !",
                ephemeral=True
            )
            return

        # Trie par nombre d'utilisations
        all_cmds.sort(key=lambda x: x.get("uses", 0), reverse=True)

        embed = discord.Embed(
            title=f"🤖 Commandes personnalisées ({len(all_cmds)}/50)",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )

        # Groupe par pages de 10
        chunks = [all_cmds[i:i + 10] for i in range(0, len(all_cmds), 10)]

        for chunk in chunks[:3]:  # Max 3 colonnes
            value = ""
            for cmd in chunk:
                uses  = cmd.get("uses", 0)
                value += f"`&{cmd['name']}` — `{uses}` utilisation(s)\n"

            embed.add_field(name="​", value=value, inline=True)

        embed.set_footer(text="Fantoma • Commandes personnalisées")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────
    # /cmd-info & &cmd-info
    # ──────────────────────────────────────────────

    @commands.hybrid_command(
        name="cmd-info",
        description="Affiche les détails d'une commande personnalisée."
    )
    @commands.guild_only()
    @app_commands.describe(nom="Nom de la commande")
    async def cmd_info(self, ctx: commands.Context, nom: str) -> None:
        nom = nom.lower().strip()
        cmd = await db_get(COLLECTION, {"guild_id": ctx.guild.id, "name": nom})

        if not cmd:
            await ctx.send(f"❌ La commande `&{nom}` n'existe pas.", ephemeral=True)
            return

        creator = ctx.guild.get_member(cmd["created_by"])
        created = datetime.fromisoformat(cmd["created_at"])

        embed = discord.Embed(
            title=f"🤖 Commande `&{nom}`",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="📝 Réponse",       value=cmd["response"][:500], inline=False)
        embed.add_field(name="👤 Créée par",      value=creator.mention if creator else "Inconnu", inline=True)
        embed.add_field(name="📅 Créée le",       value=f"<t:{int(created.timestamp())}:F>", inline=True)
        embed.add_field(name="📊 Utilisations",   value=f"`{cmd.get('uses', 0)}`", inline=True)
        embed.set_footer(text="Fantoma • Commandes personnalisées")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomCommandsCog(bot))
    logger.info("Cog 'Commandes Custom' chargé.")
