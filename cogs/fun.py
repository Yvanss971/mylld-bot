# cogs/fun.py
# Commandes fun et sociales.

import hashlib
import random
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.fun")

HUG_GIFS = [
    "https://media.giphy.com/media/od5H3PmEG5EVq/giphy.gif",
    "https://media.giphy.com/media/143v0Z4767T15e/giphy.gif",
    "https://media.giphy.com/media/lrr9rHuoJOE0w/giphy.gif",
]

SLAP_GIFS = [
    "https://media.giphy.com/media/Gf3AUz3eBNbTW/giphy.gif",
    "https://media.giphy.com/media/jLeyZWgtwgr2U/giphy.gif",
    "https://media.giphy.com/media/Zau0yrl17uzdK/giphy.gif",
]


def stable_percent(*values: int) -> int:
    raw = ":".join(str(value) for value in sorted(values))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 101


def progress_bar(percent: int, length: int = 10) -> str:
    filled = round((percent / 100) * length)
    return "█" * filled + "░" * (length - filled)


class FunCog(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="ship", description="Calcule la compatibilité entre deux membres.")
    @commands.guild_only()
    @app_commands.describe(membre1="Premier membre", membre2="Deuxième membre")
    async def ship(self, ctx: commands.Context, membre1: discord.Member, membre2: discord.Member = None) -> None:
        first = membre1
        second = membre2 or ctx.author
        if first.id == second.id:
            await ctx.send("💘 Compatibilité avec soi-même : `100%`. C'est solide.", ephemeral=True)
            return

        percent = stable_percent(first.id, second.id)
        embed = discord.Embed(
            title="💘 Ship",
            description=f"{first.mention} + {second.mention}",
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Compatibilité", value=f"`{percent}%`\n{progress_bar(percent)}", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="pp", description="Mesure la pp d'un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé")
    async def pp(self, ctx: commands.Context, membre: discord.Member = None) -> None:
        target = membre or ctx.author
        size = int(hashlib.sha256(str(target.id).encode("utf-8")).hexdigest()[:4], 16) % 26
        shaft = "=" * max(1, size)
        embed = discord.Embed(
            title=f"📏 PP de {target.display_name}",
            description=f"8{shaft}D\n`{size} cm`",
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="slap", description="Met une claque à un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé")
    async def slap(self, ctx: commands.Context, membre: discord.Member) -> None:
        if membre.id == ctx.author.id:
            await ctx.send("Tu viens de te mettre une claque tout seul.", ephemeral=True)
            return

        embed = discord.Embed(
            description=f"👋 {ctx.author.mention} met une claque à {membre.mention}.",
            color=settings.COLOR_WARNING,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=random.choice(SLAP_GIFS))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="hug", aliases=["calin"], description="Fait un câlin à un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé")
    async def hug(self, ctx: commands.Context, membre: discord.Member) -> None:
        embed = discord.Embed(
            description=f"🤗 {ctx.author.mention} fait un câlin à {membre.mention}.",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=random.choice(HUG_GIFS))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="meme", description="Envoie un mème aléatoire depuis Reddit.")
    @commands.guild_only()
    @commands.cooldown(3, 60, commands.BucketType.guild)
    async def meme(self, ctx: commands.Context) -> None:
        await ctx.defer()
        url = "https://www.reddit.com/r/memes/hot.json?limit=50"
        headers = {"User-Agent": "Fantoma Discord Bot/1.0"}

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Reddit HTTP {response.status}")
                    payload = await response.json()
        except Exception as e:
            logger.warning(f"Impossible de récupérer un meme Reddit : {e}")
            await ctx.send("❌ Impossible de récupérer un mème pour le moment.")
            return

        posts = []
        for child in payload.get("data", {}).get("children", []):
            data = child.get("data", {})
            image_url = data.get("url_overridden_by_dest") or data.get("url")
            if data.get("over_18") or not image_url:
                continue
            if not image_url.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
                continue
            posts.append(data)

        if not posts:
            await ctx.send("❌ Aucun mème exploitable trouvé.")
            return

        post = random.choice(posts)
        embed = discord.Embed(
            title=post.get("title", "Mème"),
            url="https://reddit.com" + post.get("permalink", ""),
            color=settings.COLOR_INFO,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_image(url=post.get("url_overridden_by_dest") or post.get("url"))
        embed.set_footer(text=f"👍 {post.get('ups', 0)} • r/memes")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FunCog(bot))
    logger.info("Cog 'Fun' chargé.")
