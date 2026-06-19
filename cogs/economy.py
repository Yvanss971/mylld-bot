# cogs/economy.py
# Monnaie virtuelle Fantoma avec daily, balance, paiements et shop de rôles.

import random
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config.settings import settings
from utils.database import db_delete, db_get, db_get_all, db_set
from utils.guild_config import get_guild_config
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.economy")

COLLECTION = "economy"
SHOP_COLLECTION = "economy_shop"
DAILY_AMOUNT = 250
DAILY_COOLDOWN = timedelta(hours=24)
MESSAGE_COOLDOWN_SECONDS = 60


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def money(amount: int) -> str:
    return f"`{amount:,}` coins Fantoma".replace(",", " ")


class EconomyCog(commands.Cog, name="Économie"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._message_cooldowns: dict[tuple[int, int], datetime] = {}

    async def _get_wallet(self, guild_id: int, user_id: int) -> dict:
        doc = await db_get(COLLECTION, {"guild_id": str(guild_id), "user_id": str(user_id)})
        if doc:
            return doc
        return {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "coins": 0,
            "earned_messages": 0,
            "last_daily": None,
        }

    async def _save_wallet(self, wallet: dict) -> None:
        await db_set(COLLECTION, {
            "guild_id": wallet["guild_id"],
            "user_id": wallet["user_id"],
        }, wallet)

    async def _add_coins(self, guild_id: int, user_id: int, amount: int) -> dict:
        wallet = await self._get_wallet(guild_id, user_id)
        wallet["coins"] = max(0, int(wallet.get("coins", 0)) + amount)
        await self._save_wallet(wallet)
        return wallet

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return

        key = (message.guild.id, message.author.id)
        now = utc_now()
        last = self._message_cooldowns.get(key)
        if last and (now - last).total_seconds() < MESSAGE_COOLDOWN_SECONDS:
            return

        self._message_cooldowns[key] = now
        gain = random.randint(2, 5)
        wallet = await self._add_coins(message.guild.id, message.author.id, gain)
        wallet["earned_messages"] = int(wallet.get("earned_messages", 0)) + gain
        await self._save_wallet(wallet)

    @commands.hybrid_command(name="daily", description="Récupère ta récompense quotidienne.")
    @commands.guild_only()
    async def daily(self, ctx: commands.Context) -> None:
        wallet = await self._get_wallet(ctx.guild.id, ctx.author.id)
        now = utc_now()
        last_daily = parse_iso(wallet.get("last_daily"))

        if last_daily and now - last_daily < DAILY_COOLDOWN:
            next_daily = last_daily + DAILY_COOLDOWN
            await ctx.send(f"⏳ Tu as déjà récupéré ton daily. Reviens <t:{int(next_daily.timestamp())}:R>.", ephemeral=True)
            return

        econ_config = await get_guild_config(ctx.guild.id, "economy")
        daily_amount = int(econ_config.get("daily_amount", DAILY_AMOUNT))

        wallet["coins"] = int(wallet.get("coins", 0)) + daily_amount
        wallet["last_daily"] = now.isoformat()
        await self._save_wallet(wallet)

        embed = discord.Embed(
            title="💰 Daily récupéré",
            description=f"Tu gagnes {money(daily_amount)}.",
            color=settings.COLOR_SUCCESS,
            timestamp=now,
        )
        embed.add_field(name="Solde", value=money(wallet["coins"]), inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="balance", aliases=["bal", "coins"], description="Affiche le solde d'un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre ciblé")
    async def balance(self, ctx: commands.Context, membre: discord.Member = None) -> None:
        target = membre or ctx.author
        wallet = await self._get_wallet(ctx.guild.id, target.id)
        embed = discord.Embed(
            title=f"💰 Solde de {target.display_name}",
            color=settings.COLOR_INFO,
            timestamp=utc_now(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Coins", value=money(int(wallet.get("coins", 0))), inline=True)
        embed.add_field(name="Gagnés en activité", value=money(int(wallet.get("earned_messages", 0))), inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="pay", aliases=["payer"], description="Donne des coins à un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre qui reçoit les coins", montant="Montant à envoyer")
    async def pay(self, ctx: commands.Context, membre: discord.Member, montant: int) -> None:
        if membre.bot or membre.id == ctx.author.id:
            await ctx.send("❌ Membre invalide.", ephemeral=True)
            return
        if montant <= 0:
            await ctx.send("❌ Montant invalide.", ephemeral=True)
            return

        sender = await self._get_wallet(ctx.guild.id, ctx.author.id)
        if int(sender.get("coins", 0)) < montant:
            await ctx.send("❌ Tu n'as pas assez de coins.", ephemeral=True)
            return

        receiver = await self._get_wallet(ctx.guild.id, membre.id)
        sender["coins"] = int(sender.get("coins", 0)) - montant
        receiver["coins"] = int(receiver.get("coins", 0)) + montant
        await self._save_wallet(sender)
        await self._save_wallet(receiver)

        await ctx.send(embed=discord.Embed(
            title="💸 Paiement envoyé",
            description=f"{ctx.author.mention} a envoyé {money(montant)} à {membre.mention}.",
            color=settings.COLOR_SUCCESS,
            timestamp=utc_now(),
        ))

    @commands.hybrid_command(name="shop", description="Affiche le shop des rôles.")
    @commands.guild_only()
    async def shop(self, ctx: commands.Context) -> None:
        items = await db_get_all(SHOP_COLLECTION, {"guild_id": str(ctx.guild.id)})
        items.sort(key=lambda item: int(item.get("price", 0)))

        if not items:
            await ctx.send("ℹ️ Le shop est vide. Un admin peut ajouter un rôle avec `&shop-add-role`.", ephemeral=True)
            return

        embed = discord.Embed(title="🛒 Shop Fantoma", color=settings.COLOR_PRIMARY, timestamp=utc_now())
        for item in items[:15]:
            role = ctx.guild.get_role(int(item["role_id"]))
            role_text = role.mention if role else "Rôle supprimé"
            embed.add_field(
                name=f"`{item['item_id']}` — {item['name']}",
                value=f"{role_text}\nPrix : {money(int(item['price']))}\nAchat : `&buy {item['item_id']}`",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="buy", aliases=["acheter"], description="Achète un objet du shop.")
    @commands.guild_only()
    @app_commands.describe(item_id="ID de l'objet dans le shop")
    async def buy(self, ctx: commands.Context, item_id: str) -> None:
        item = await db_get(SHOP_COLLECTION, {"guild_id": str(ctx.guild.id), "item_id": item_id.lower()})
        if not item:
            await ctx.send("❌ Objet introuvable dans le shop.", ephemeral=True)
            return

        role = ctx.guild.get_role(int(item["role_id"]))
        if not role:
            await ctx.send("❌ Le rôle lié à cet objet n'existe plus.", ephemeral=True)
            return
        if role in ctx.author.roles:
            await ctx.send("ℹ️ Tu as déjà ce rôle.", ephemeral=True)
            return
        if ctx.guild.me and role >= ctx.guild.me.top_role:
            await ctx.send("❌ Mon rôle doit être au-dessus du rôle vendu.", ephemeral=True)
            return

        price = int(item["price"])
        wallet = await self._get_wallet(ctx.guild.id, ctx.author.id)
        if int(wallet.get("coins", 0)) < price:
            await ctx.send("❌ Tu n'as pas assez de coins.", ephemeral=True)
            return

        wallet["coins"] = int(wallet.get("coins", 0)) - price
        await self._save_wallet(wallet)
        await ctx.author.add_roles(role, reason=f"Achat shop Fantoma : {item['name']}")

        await ctx.send(embed=discord.Embed(
            title="✅ Achat réussi",
            description=f"Tu as acheté {role.mention} pour {money(price)}.",
            color=settings.COLOR_SUCCESS,
            timestamp=utc_now(),
        ))

    @commands.hybrid_command(name="shop-add-role", description="[ADMIN] Ajoute un rôle au shop.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(role="Rôle vendu", prix="Prix en coins", nom="Nom affiché dans le shop")
    async def shop_add_role(self, ctx: commands.Context, role: discord.Role, prix: int, *, nom: str = None) -> None:
        if prix <= 0:
            await ctx.send("❌ Prix invalide.", ephemeral=True)
            return
        if ctx.guild.me and role >= ctx.guild.me.top_role:
            await ctx.send("❌ Mon rôle doit être au-dessus du rôle vendu.", ephemeral=True)
            return

        item_id = uuid.uuid4().hex[:6]
        await db_set(SHOP_COLLECTION, {"guild_id": str(ctx.guild.id), "item_id": item_id}, {
            "guild_id": str(ctx.guild.id),
            "item_id": item_id,
            "type": "role",
            "role_id": str(role.id),
            "name": nom or role.name,
            "price": prix,
            "created_by": str(ctx.author.id),
            "created_at": utc_now().isoformat(),
        })
        await ctx.send(f"✅ {role.mention} ajouté au shop pour {money(prix)}. ID : `{item_id}`", ephemeral=True)

    @commands.hybrid_command(name="shop-remove", description="[ADMIN] Retire un objet du shop.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(item_id="ID de l'objet à retirer")
    async def shop_remove(self, ctx: commands.Context, item_id: str) -> None:
        await db_delete(SHOP_COLLECTION, {"guild_id": str(ctx.guild.id), "item_id": item_id.lower()})
        await ctx.send(f"✅ Objet `{item_id}` retiré du shop.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
    logger.info("Cog 'Économie' chargé.")
