# cogs/xp_multipliers.py
"""
Système de multiplicateurs XP par salon et par rôle.
Le multiplicateur final est le PRODUIT de tous les multiplicateurs applicables.
Stockage : guild_configs.xp_channel_multipliers et xp_role_multipliers
"""

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config import settings
from utils.database import get_db
from utils.cache import xp_multiplier_cache
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.xp_multipliers")

MIN_MULTIPLIER = 0.1
MAX_MULTIPLIER = 10.0


# ──────────────────────────────────────────────
# Fonction exportée — utilisée par levels.py
# ──────────────────────────────────────────────

async def get_xp_multiplier(guild_id: int, channel_id: int, member: discord.Member) -> float:
    """
    Calcule le multiplicateur XP final pour un membre dans un salon.
    Le résultat est le PRODUIT de tous les multiplicateurs applicables :
    - Multiplicateur du salon (si défini)
    - Multiplicateurs des rôles du membre (si définis)

    Retourne un float clampé entre 0.1 et 10.0.
    Utilise le cache pour éviter les requêtes MongoDB répétées.
    """
    cache_key = f"xp_mult:{guild_id}"

    # 1. Vérifier le cache
    cached = await xp_multiplier_cache.get(cache_key)
    if cached is not None:
        doc = cached
    else:
        # 2. Requête MongoDB (seulement si cache miss)
        db = await get_db()
        doc = await db["guild_configs"].find_one(
            {"guild_id": guild_id},
            {"xp_channel_multipliers": 1, "xp_role_multipliers": 1}
        )

        # 3. Stocker en cache (même si None → cache négatif)
        await xp_multiplier_cache.set(cache_key, doc or {})
        doc = doc or {}

    multiplier = 1.0
    ch_mults = doc.get("xp_channel_multipliers", {})
    r_mults  = doc.get("xp_role_multipliers", {})

    # Multiplicateur de salon
    if str(channel_id) in ch_mults:
        multiplier *= ch_mults[str(channel_id)]

    # Multiplicateurs de rôles
    for role in member.roles:
        if str(role.id) in r_mults:
            multiplier *= r_mults[str(role.id)]

    # Clamp de sécurité [0.1, 10.0]
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, multiplier))


# ──────────────────────────────────────────────
# Utilitaires internes
# ──────────────────────────────────────────────

async def _set_channel_multiplier(guild_id: int, channel_id: int, value: float | None) -> None:
    """Définit ou supprime un multiplicateur de salon."""
    db = await get_db()
    key = f"xp_channel_multipliers.{channel_id}"
    if value is None:
        await db["guild_configs"].update_one(
            {"guild_id": guild_id},
            {"$unset": {key: ""}},
            upsert=True
        )
    else:
        await db["guild_configs"].update_one(
            {"guild_id": guild_id},
            {"$set": {key: value}},
            upsert=True
        )
    # Invalider le cache des multiplicateurs pour ce serveur
    await xp_multiplier_cache.delete(f"xp_mult:{guild_id}")


async def _set_role_multiplier(guild_id: int, role_id: int, value: float | None) -> None:
    """Définit ou supprime un multiplicateur de rôle."""
    db = await get_db()
    key = f"xp_role_multipliers.{role_id}"
    if value is None:
        await db["guild_configs"].update_one(
            {"guild_id": guild_id},
            {"$unset": {key: ""}},
            upsert=True
        )
    else:
        await db["guild_configs"].update_one(
            {"guild_id": guild_id},
            {"$set": {key: value}},
            upsert=True
        )
    # Invalider le cache des multiplicateurs pour ce serveur
    await xp_multiplier_cache.delete(f"xp_mult:{guild_id}")


# ──────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────

class XPMultipliersCog(commands.Cog, name="Multiplicateurs XP"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Groupe de commandes ──

    @commands.hybrid_group(
        name="xp-multiplier",
        description="Gère les multiplicateurs d'XP par salon et par rôle.",
        aliases=["xpmult"]
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def xp_multiplier(self, ctx: commands.Context):
        """Groupe de commandes pour les multiplicateurs XP."""
        if ctx.invoked_subcommand is None:
            await ctx.send(
                "❌ Utilise une sous-commande : `channel`, `channel-remove`, `role`, `role-remove`, ou `list`.\n"
                "Exemple : `&xp-multiplier channel #général 2.0`",
                ephemeral=True
            )

    # ── Salon ──

    @xp_multiplier.command(
        name="channel",
        description="[ADMIN] Définit un multiplicateur XP pour un salon."
    )
    @app_commands.describe(
        salon="Salon ciblé",
        multiplicateur="Valeur du multiplicateur (0.1 à 10.0)"
    )
    async def xp_multiplier_channel(
        self,
        ctx: commands.Context,
        salon: discord.TextChannel,
        multiplicateur: float
    ) -> None:
        if not MIN_MULTIPLIER <= multiplicateur <= MAX_MULTIPLIER:
            await ctx.send(
                f"❌ Le multiplicateur doit être entre `{MIN_MULTIPLIER}` et `{MAX_MULTIPLIER}`.",
                ephemeral=True
            )
            return

        await _set_channel_multiplier(ctx.guild.id, salon.id, multiplicateur)
        logger.info(f"[MULT] Salon {salon.id} → {multiplicateur}x sur {ctx.guild.id}")

        embed = discord.Embed(
            title="✅ Multiplicateur de salon défini",
            description=f"Le salon {salon.mention} a un multiplicateur de **{multiplicateur}x**.",
            color=0x57F287,  # COLOR_SUCCESS hardcodé
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{ctx.guild.name} • Multiplicateurs XP")
        await ctx.send(embed=embed)

    @xp_multiplier.command(
        name="channel-remove",
        description="[ADMIN] Supprime le multiplicateur XP d'un salon."
    )
    @app_commands.describe(salon="Salon à réinitialiser")
    async def xp_multiplier_channel_remove(
        self,
        ctx: commands.Context,
        salon: discord.TextChannel
    ) -> None:
        await _set_channel_multiplier(ctx.guild.id, salon.id, None)
        logger.info(f"[MULT] Salon {salon.id} → supprimé sur {ctx.guild.id}")

        embed = discord.Embed(
            title="🗑️ Multiplicateur supprimé",
            description=f"Le multiplicateur pour {salon.mention} est réinitialisé à **1x**.",
            color=0xFEE75C,  # COLOR_WARNING hardcodé
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{ctx.guild.name} • Multiplicateurs XP")
        await ctx.send(embed=embed)

    # ── Rôle ──

    @xp_multiplier.command(
        name="role",
        description="[ADMIN] Définit un multiplicateur XP pour un rôle."
    )
    @app_commands.describe(
        role="Rôle ciblé",
        multiplicateur="Valeur du multiplicateur (0.1 à 10.0)"
    )
    async def xp_multiplier_role(
        self,
        ctx: commands.Context,
        role: discord.Role,
        multiplicateur: float
    ) -> None:
        if not MIN_MULTIPLIER <= multiplicateur <= MAX_MULTIPLIER:
            await ctx.send(
                f"❌ Le multiplicateur doit être entre `{MIN_MULTIPLIER}` et `{MAX_MULTIPLIER}`.",
                ephemeral=True
            )
            return

        if role >= ctx.guild.me.top_role:
            await ctx.send(
                "❌ Je ne peux pas appliquer de multiplicateur à un rôle supérieur ou égal au mien.",
                ephemeral=True
            )
            return

        await _set_role_multiplier(ctx.guild.id, role.id, multiplicateur)
        logger.info(f"[MULT] Rôle {role.id} → {multiplicateur}x sur {ctx.guild.id}")

        embed = discord.Embed(
            title="✅ Multiplicateur de rôle défini",
            description=f"Le rôle {role.mention} a un multiplicateur de **{multiplicateur}x**.",
            color=0x57F287,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{ctx.guild.name} • Multiplicateurs XP")
        await ctx.send(embed=embed)

    @xp_multiplier.command(
        name="role-remove",
        description="[ADMIN] Supprime le multiplicateur XP d'un rôle."
    )
    @app_commands.describe(role="Rôle à réinitialiser")
    async def xp_multiplier_role_remove(
        self,
        ctx: commands.Context,
        role: discord.Role
    ) -> None:
        await _set_role_multiplier(ctx.guild.id, role.id, None)
        logger.info(f"[MULT] Rôle {role.id} → supprimé sur {ctx.guild.id}")

        embed = discord.Embed(
            title="🗑️ Multiplicateur supprimé",
            description=f"Le multiplicateur pour le rôle {role.mention} est réinitialisé à **1x**.",
            color=0xFEE75C,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{ctx.guild.name} • Multiplicateurs XP")
        await ctx.send(embed=embed)

    # ── Liste ──

    @xp_multiplier.command(
        name="list",
        description="[ADMIN] Affiche tous les multiplicateurs XP actifs du serveur."
    )
    async def xp_multiplier_list(self, ctx: commands.Context) -> None:
        db = await get_db()
        doc = await db["guild_configs"].find_one(
            {"guild_id": ctx.guild.id},
            {"xp_channel_multipliers": 1, "xp_role_multipliers": 1}
        )
        doc = doc or {}

        ch_mults = doc.get("xp_channel_multipliers", {})
        r_mults  = doc.get("xp_role_multipliers", {})

        embed = discord.Embed(
            title="📊 Multiplicateurs XP actifs",
            color=0xEB459E,  # COLOR_INFO hardcodé
            timestamp=datetime.now(timezone.utc)
        )

        # ─ Salons
        if ch_mults:
            lines = []
            for ch_id_str, mult in sorted(ch_mults.items(), key=lambda x: x[1], reverse=True):
                ch = ctx.guild.get_channel(int(ch_id_str))
                name = ch.mention if ch else f"`{ch_id_str}`"
                lines.append(f"{name} → **{mult}x**")
            embed.add_field(name="💬 Salons", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="💬 Salons", value="*Aucun multiplicateur défini.*", inline=False)

        # ─ Rôles
        if r_mults:
            lines = []
            for r_id_str, mult in sorted(r_mults.items(), key=lambda x: x[1], reverse=True):
                r = ctx.guild.get_role(int(r_id_str))
                name = r.mention if r else f"`{r_id_str}`"
                lines.append(f"{name} → **{mult}x**")
            embed.add_field(name="🎭 Rôles", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="🎭 Rôles", value="*Aucun multiplicateur défini.*", inline=False)

        # ─ Info calcul
        embed.add_field(
            name="🧮 Calcul",
            value=(
                "Le multiplicateur final est le **produit** de tous les multiplicateurs applicables.\n"
                "Exemple : salon 2x + rôle VIP 1.5x = **3x total**"
            ),
            inline=False
        )

        embed.set_footer(text=f"{ctx.guild.name} • Multiplicateurs XP")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XPMultipliersCog(bot))
    logger.info("Cog 'Multiplicateurs XP' chargé.")
