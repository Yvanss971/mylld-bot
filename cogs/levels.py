# cogs/levels.py
"""
Système de niveaux avec :
- Cache TTL pour les configs et données utilisateur
- Aggregation MongoDB pour les classements (plus rapide)
- Multiplicateurs XP intégrés via import depuis xp_multipliers.py
- Logs enrichis avec multiplicateur appliqué
"""

import json
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from pathlib import Path
from datetime import datetime, timedelta, timezone

from config import settings
from utils.database import get_db
from utils.guild_config import get_guild_config
from utils.cache import levels_cache, guild_config_cache
from utils.logger import setup_console_logger
from cogs.xp_multipliers import get_xp_multiplier

logger = setup_console_logger("cogs.levels")

_guild_locks: dict[int, asyncio.Lock] = {}


def _get_guild_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _guild_locks:
        _guild_locks[guild_id] = asyncio.Lock()
    return _guild_locks[guild_id]


# ──────────────────────────────────────────────
# Utilitaires MongoDB multi-serveur — optimisés
# ──────────────────────────────────────────────

async def get_member_data(guild_id: int, user_id: str) -> dict:
    """
    Récupère les données d'un membre avec cache.
    """
    cache_key = f"levels:{guild_id}:{user_id}"

    # 1. Vérifier le cache
    cached = await levels_cache.get(cache_key)
    if cached is not None:
        return cached

    # 2. Requête MongoDB
    db  = await get_db()
    doc = await db["levels"].find_one({"guild_id": guild_id, "user_id": user_id})

    if not doc:
        doc = {
            "guild_id": guild_id,
            "user_id": user_id,
            "xp": 0,
            "level": 1,
            "total_messages": 0,
            "voice_minutes": 0
        }

    # 3. Stocker en cache (30s TTL)
    await levels_cache.set(cache_key, doc)
    return doc


async def save_member_data(data: dict) -> None:
    """
    Sauvegarde les données d'un membre et invalide son cache.
    """
    db = await get_db()
    await db["levels"].update_one(
        {"guild_id": data["guild_id"], "user_id": data["user_id"]},
        {"$set": data},
        upsert=True
    )

    # Invalider le cache de cet utilisateur
    await levels_cache.delete(f"levels:{data['guild_id']}:{data['user_id']}")


async def get_rank(guild_id: int, user_id: str) -> int:
    """
    Calcule le rang d'un membre via aggregation MongoDB (O(1) vs O(n) avant).

    Avant : chargeait TOUT en RAM + tri Python
    Après : count_documents avec $gt (indexé)
    """
    db = await get_db()

    # Récupérer l'XP de l'utilisateur
    user_doc = await db["levels"].find_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"xp": 1}
    )

    if not user_doc:
        # L'utilisateur n'existe pas encore → dernier rang
        total = await db["levels"].count_documents({"guild_id": guild_id})
        return total + 1

    user_xp = user_doc.get("xp", 0)

    # Compter combien ont PLUS d'XP (plus rapide que trier tout)
    higher_count = await db["levels"].count_documents({
        "guild_id": guild_id,
        "xp": {"$gt": user_xp}
    })

    return higher_count + 1


async def get_leaderboard(guild_id: int, limit: int = 10) -> list:
    """
    Récupère le top N du classement via aggregation MongoDB.
    Utilise l'index sur (guild_id, xp) pour un tri côté DB.
    """
    db = await get_db()

    pipeline = [
        {"$match": {"guild_id": guild_id}},
        {"$sort": {"xp": -1}},
        {"$limit": limit},
        {"$project": {
            "user_id": 1,
            "xp": 1,
            "level": 1,
            "total_messages": 1,
            "voice_minutes": 1
        }}
    ]

    cursor = db["levels"].aggregate(pipeline)
    return await cursor.to_list(length=limit)


# ──────────────────────────────────────────────
# Fonctions de calcul de niveau
# ──────────────────────────────────────────────

def calculate_level(xp: int, levels: list) -> int:
    current = 1
    for entry in levels:
        if xp >= entry["xp_required"]:
            current = entry["level"]
    return current


def get_level_from_xp(xp: int, levels: list) -> int:
    """Calcule le niveau réel depuis l'XP (source de vérité)."""
    current = 1
    for entry in sorted(levels, key=lambda x: x["level"]):
        if xp >= entry["xp_required"]:
            current = entry["level"]
        else:
            break
    return current


def get_xp_for_next_level(current_level: int, levels: list) -> int | None:
    """Retourne l'XP requis pour le prochain niveau supérieur au niveau actuel."""
    sorted_levels = sorted(levels, key=lambda x: x["level"])
    for entry in sorted_levels:
        if entry["level"] > current_level:
            return entry["xp_required"]
    return None


def get_xp_for_level(level: int, levels: list) -> int:
    """Retourne l'XP requis pour atteindre ce niveau (ou le palier le plus proche en dessous)."""
    sorted_levels = sorted(levels, key=lambda x: x["level"])
    xp = 0
    for entry in sorted_levels:
        if entry["level"] <= level:
            xp = entry["xp_required"]
        else:
            break
    return xp


def build_progress_bar(current: int, total: int, length: int = 15) -> str:
    if total == 0:
        return "█" * length + " **100%**"
    filled  = int((current / total) * length)
    percent = int((current / total) * 100)
    return "█" * filled + "░" * (length - filled) + f" **{percent}%**"


# ──────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────

class LevelsCog(commands.Cog, name="Niveaux"):

    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self._cooldowns:   dict[str, datetime] = {}
        self._voice_cache: dict[str, datetime] = {}
        self.voice_xp_loop.start()

    def cog_unload(self):
        self.voice_xp_loop.cancel()

    @tasks.loop(seconds=60)
    async def voice_xp_loop(self) -> None:
        now = datetime.now(timezone.utc)

        for guild in self.bot.guilds:
            config    = await get_guild_config(guild.id, "levels")
            voice_cfg = config.get("voice_xp", {})

            if not voice_cfg.get("enabled"):
                continue

            interval = voice_cfg.get("interval_seconds", 120)
            xp_gain  = voice_cfg.get("xp_per_interval", 10)
            afk_id   = guild.afk_channel.id if guild.afk_channel else None

            for vc in guild.voice_channels:
                if voice_cfg.get("ignore_afk") and vc.id == afk_id:
                    continue

                real_members = [
                    m for m in vc.members
                    if not m.bot
                    and not (voice_cfg.get("ignore_muted")    and m.voice.self_mute)
                    and not (voice_cfg.get("ignore_deafened") and m.voice.self_deaf)
                ]

                if voice_cfg.get("ignore_alone") and len(real_members) < 2:
                    continue

                for member in real_members:
                    cache_key = f"{guild.id}:{member.id}"
                    last      = self._voice_cache.get(cache_key)

                    if last and (now - last).total_seconds() < interval:
                        continue

                    self._voice_cache[cache_key] = now

                    async with _get_guild_lock(guild.id):
                        md        = await get_member_data(guild.id, str(member.id))
                        old_level = md["level"]
                        levels    = config.get("levels", [])

                        # ── Multiplicateurs XP ──
                        mult = await get_xp_multiplier(guild.id, vc.id, member)
                        final_xp_gain = int(xp_gain * mult)

                        md["xp"]            += final_xp_gain
                        md["voice_minutes"]  = md.get("voice_minutes", 0) + max(1, interval // 60)
                        md["level"]          = calculate_level(md["xp"], levels)
                        await save_member_data(md)

                    logger.info(
                        f"[VOCAL] +{final_xp_gain} XP (x{mult}) → {member} sur {guild.name}"
                    )

                    if md["level"] > old_level:
                        await self._notify_level_up(member, md["level"], config=config, via_voice=True)

    @voice_xp_loop.before_loop
    async def before_voice_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        user_id   = str(message.author.id)
        guild_id  = message.guild.id
        cache_key = f"{guild_id}:{user_id}"
        now       = datetime.now(timezone.utc)

        config = await get_guild_config(guild_id, "levels")

        if cache_key in self._cooldowns:
            elapsed = (now - self._cooldowns[cache_key]).total_seconds()
            if elapsed < config.get("xp_cooldown_seconds", 60):
                return

        self._cooldowns[cache_key] = now

        async with _get_guild_lock(guild_id):
            md        = await get_member_data(guild_id, user_id)
            old_level = md["level"]
            levels    = config.get("levels", [])

            base_xp_gain = config.get("xp_per_message", 15) + random.randint(0, config.get("xp_randomness", 10))

            # ── Multiplicateurs XP ──
            mult = await get_xp_multiplier(guild_id, message.channel.id, message.author)
            xp_gain = int(base_xp_gain * mult)

            md["xp"]            += xp_gain
            md["total_messages"] += 1
            md["level"]          = calculate_level(md["xp"], levels)
            await save_member_data(md)

        logger.debug(
            f"[MSG] +{xp_gain} XP (x{mult}) → {message.author} sur {message.guild.name} "
            f"(total: {md['xp']})"
        )

        if md["level"] > old_level:
            await self._notify_level_up(message.author, md["level"], config=config, message=message)

    async def _notify_level_up(
        self,
        member:    discord.Member,
        new_level: int,
        config:    dict,
        message:   discord.Message | None = None,
        via_voice: bool = False
    ) -> None:
        source = "🔊 Vocal" if via_voice else "💬 Messages"

        embed = discord.Embed(
            title="⭐ Level Up !",
            description=(
                f"Félicitations {member.mention} !\n"
                f"Tu passes au **niveau {new_level}** ! 🎉\n"
                f"*(via {source})*"
            ),
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"{member.guild.name} • Système de niveaux")

        channel_id = config.get("level_up_channel_id")
        target     = self.bot.get_channel(channel_id) if channel_id else (message.channel if message else None)

        if config.get("level_up_dm"):
            try:
                await member.send(embed=embed)
                self.bot.dispatch("level_up", member, new_level)
                return
            except discord.Forbidden:
                pass

        if target:
            await target.send(embed=embed)

        self.bot.dispatch("level_up", member, new_level)
        logger.info(f"⭐ {member} → niveau {new_level} sur {member.guild.name}")

    @commands.hybrid_command(name="rank", description="Affiche ton niveau et ton XP.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé (optionnel)")
    async def rank(self, ctx: commands.Context, membre: discord.Member = None) -> None:
        await ctx.defer()

        target   = membre or ctx.author
        guild_id = ctx.guild.id
        user_id  = str(target.id)
        config   = await get_guild_config(guild_id, "levels")
        levels   = config.get("levels", [])

        md            = await get_member_data(guild_id, user_id)
        xp            = md.get("xp", 0)
        level         = get_level_from_xp(xp, levels)  # calculé depuis XP, pas stocké
        messages      = md.get("total_messages", 0)
        voice_minutes = md.get("voice_minutes", 0)
        rank          = await get_rank(guild_id, user_id)
        xp_next       = get_xp_for_next_level(level, levels)
        xp_current    = get_xp_for_level(level, levels)

        if xp_next:
            xp_in_level = xp - xp_current
            xp_needed   = xp_next - xp_current
            bar         = build_progress_bar(max(0, xp_in_level), xp_needed)
            xp_text     = f"`{max(0, xp_in_level):,}` / `{xp_needed:,}` XP"
        else:
            bar     = "█" * 15 + " **MAX** 👑"
            xp_text = f"`{xp:,}` XP — Niveau maximum !"

        embed = discord.Embed(
            title=f"⭐ {target.display_name}",
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏅 Niveau",    value=f"```{level}```",             inline=True)
        embed.add_field(name="🏆 Rang",      value=f"```#{rank}```",             inline=True)
        embed.add_field(name="✨ XP Total",  value=f"```{xp:,}```",              inline=True)
        embed.add_field(name="💬 Messages",  value=f"```{messages:,}```",        inline=True)
        embed.add_field(name="🔊 Vocal",     value=f"```{voice_minutes} min```", inline=True)
        if xp_next:
            embed.add_field(name="🎯 Prochain niveau", value=xp_text,            inline=True)
        embed.add_field(name="📊 Progression", value=bar,                        inline=False)
        embed.set_footer(text=f"{ctx.guild.name} • Système de niveaux")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard", description="Affiche le top 10 des membres.")
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context) -> None:
        await ctx.defer()

        # Utilise l'aggregation MongoDB optimisée
        top_members = await get_leaderboard(ctx.guild.id, limit=10)

        if not top_members:
            await ctx.send("❌ Aucune donnée disponible.", ephemeral=True)
            return

        medals      = ["🥇", "🥈", "🥉"]
        description = ""

        for i, md in enumerate(top_members):
            medal  = medals[i] if i < 3 else f"`#{i + 1}`"
            member = ctx.guild.get_member(int(md["user_id"]))
            name   = member.display_name if member else "Membre inconnu"
            description += f"{medal} **{name}**\n╰ Niveau `{md.get('level', 1)}` • `{md.get('xp', 0):,} XP`\n"

        embed = discord.Embed(
            title=f"🏆 Classement {ctx.guild.name}",
            description=description,
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{ctx.guild.name} • Top 10")

        # Position de l'auteur (seulement s'il n'est pas dans le top 10)
        uid_str = str(ctx.author.id)
        author_in_top = any(m["user_id"] == uid_str for m in top_members)

        if not author_in_top:
            rank = await get_rank(ctx.guild.id, uid_str)
            md = await get_member_data(ctx.guild.id, uid_str)
            embed.add_field(
                name="📍 Ta position",
                value=f"Rang `#{rank}` • Niveau `{md.get('level', 1)}` • `{md.get('xp', 0):,} XP`",
                inline=False
            )

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="xp-add", description="[ADMIN] Ajoute de l'XP à un membre.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(membre="Membre ciblé", quantite="XP à ajouter")
    async def xp_add(self, ctx: commands.Context, membre: discord.Member, quantite: int) -> None:
        if quantite <= 0:
            await ctx.send("❌ Quantité invalide.", ephemeral=True)
            return

        config = await get_guild_config(ctx.guild.id, "levels")
        levels = config.get("levels", [])

        async with _get_guild_lock(ctx.guild.id):
            md        = await get_member_data(ctx.guild.id, str(membre.id))
            old_level = md.get("level", 1)
            md["xp"]  = md.get("xp", 0) + quantite
            md["level"] = calculate_level(md["xp"], levels)
            await save_member_data(md)

        embed = discord.Embed(
            title="✅ XP Ajouté",
            description=f"`+{quantite:,} XP` → {membre.mention}",
            color=settings.COLOR_SUCCESS
        )
        embed.add_field(name="XP Total", value=f"`{md['xp']:,}`",  inline=True)
        embed.add_field(name="Niveau",   value=f"`{md['level']}`", inline=True)
        if md["level"] > old_level:
            embed.add_field(name="⭐ Level Up !", value=f"Niveau `{old_level}` → `{md['level']}`", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="xp-reset", description="[ADMIN] Remet l'XP d'un membre à zéro.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(membre="Membre à remettre à zéro")
    async def xp_reset(self, ctx: commands.Context, membre: discord.Member) -> None:
        await save_member_data({
            "guild_id": ctx.guild.id,
            "user_id":  str(membre.id),
            "xp": 0, "level": 1, "total_messages": 0, "voice_minutes": 0
        })
        await ctx.send(embed=discord.Embed(
            title="🔄 XP Réinitialisé",
            description=f"L'XP de {membre.mention} a été remis à zéro.",
            color=settings.COLOR_WARNING
        ))

    # ── Commandes admin cache ──

    @commands.hybrid_command(name="cache-stats", description="[ADMIN] Stats du cache niveaux.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def cache_stats(self, ctx: commands.Context) -> None:
        """Affiche les statistiques des caches pour debug."""
        gc_stats = await guild_config_cache.stats()
        lvl_stats = await levels_cache.stats()
        mult_stats = await xp_multiplier_cache.stats()

        embed = discord.Embed(
            title="📊 Stats Cache",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="⚙️ Guild Config",
            value=f"Size: `{gc_stats['size']}` | Expired: `{gc_stats['expired']}` | TTL: `{gc_stats['ttl_seconds']}s`",
            inline=False
        )
        embed.add_field(
            name="⭐ Levels",
            value=f"Size: `{lvl_stats['size']}` | Expired: `{lvl_stats['expired']}` | TTL: `{lvl_stats['ttl_seconds']}s`",
            inline=False
        )
        embed.add_field(
            name="🔢 XP Multipliers",
            value=f"Size: `{mult_stats['size']}` | Expired: `{mult_stats['expired']}` | TTL: `{mult_stats['ttl_seconds']}s`",
            inline=False
        )
        embed.set_footer(text=f"{ctx.guild.name} • Cache Stats")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LevelsCog(bot))
    logger.info("Cog 'Niveaux' chargé.")