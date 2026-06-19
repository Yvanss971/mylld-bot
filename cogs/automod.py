# cogs/automod.py
import re
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from config.settings import settings
from utils.database import get_db
from utils.guild_config import get_guild_config, set_guild_config
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.automod")

WARNS_COLLECTION = "warns"
URL_REGEX        = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)


# ──────────────────────────────────────────────
# Utilitaires warns multi-serveur
# ──────────────────────────────────────────────

async def get_member_warns(guild_id: int, user_id: str) -> list:
    db  = await get_db()
    doc = await db[WARNS_COLLECTION].find_one({"guild_id": guild_id, "user_id": user_id})
    return doc.get("warns", []) if doc else []


async def add_warn_db(guild_id: int, user_id: str, reason: str, moderator: str) -> int:
    db    = await get_db()
    doc   = await db[WARNS_COLLECTION].find_one({"guild_id": guild_id, "user_id": user_id})
    warns = doc.get("warns", []) if doc else []
    warns.append({
        "reason":    reason,
        "moderator": moderator,
        "date":      datetime.now(timezone.utc).isoformat()
    })
    await db[WARNS_COLLECTION].update_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"$set": {"guild_id": guild_id, "user_id": user_id, "warns": warns}},
        upsert=True
    )
    return len(warns)


async def clear_warns_db(guild_id: int, user_id: str) -> None:
    db = await get_db()
    await db[WARNS_COLLECTION].update_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"$set": {"warns": []}},
        upsert=True
    )


class AutoModCog(commands.Cog, name="AutoMod"):

    def __init__(self, bot: commands.Bot):
        self.bot            = bot
        self._spam_cache:   dict[str, list] = defaultdict(list)
        self._repeat_cache: dict[str, list] = defaultdict(list)

    def _is_exempt(self, message: discord.Message, config: dict) -> bool:
        if message.author.bot:
            return True
        if message.channel.id in config.get("exempt_channels", []):
            return True
        if any(r.id in config.get("exempt_roles", []) for r in message.author.roles):
            return True
        return False

    async def _delete_and_warn(self, message, reason, rule, auto_warn=True):
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            return
        if auto_warn:
            config = await get_guild_config(message.guild.id, "automod")
            await self._process_warn(message.author, reason, "AutoMod", message.guild, message.channel, config)
        logger.info(f"[{rule}] {message.author} — {reason}")

    async def _process_warn(self, member, reason, moderator, guild, notify_channel=None, config=None):
        if config is None:
            config = await get_guild_config(guild.id, "automod")

        total = await add_warn_db(guild.id, str(member.id), reason, moderator)

        try:
            dm_embed = discord.Embed(
                title="⚠️ Avertissement reçu",
                description=f"Serveur : **{guild.name}**\nRaison : {reason}\nTotal : `{total}`",
                color=settings.COLOR_WARNING,
                timestamp=datetime.now(timezone.utc)
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        if notify_channel:
            notif = discord.Embed(
                description=f"⚠️ {member.mention} — {reason} (warn `{total}`)",
                color=settings.COLOR_WARNING
            )
            msg = await notify_channel.send(embed=notif)
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except discord.NotFound:
                pass

        await self._send_log("⚠️ Avertissement", f"{member.mention} averti.", [
            {"name": "Raison", "value": reason,     "inline": True},
            {"name": "Total",  "value": str(total), "inline": True},
            {"name": "Par",    "value": moderator,  "inline": True},
        ], settings.COLOR_WARNING, config)

        await self._apply_thresholds(member, total, guild, config)

    async def _apply_thresholds(self, member, warn_count, guild, config):
        action = config.get("warn_thresholds", {}).get(str(warn_count))
        if not action:
            return
        reason = f"Seuil automatique ({warn_count} warns)"
        try:
            if action == "mute":
                duration = config["rules"]["anti_spam"].get("mute_duration_seconds", 300)
                await member.timeout(datetime.now(timezone.utc) + timedelta(seconds=duration), reason=reason)
            elif action == "kick":
                await member.kick(reason=reason)
            elif action == "ban":
                await member.ban(reason=reason, delete_message_days=1)
        except discord.Forbidden:
            pass

    async def _send_log(self, title, description, fields, color, config):
        channel = self.bot.get_channel(config.get("log_channel_id"))
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
        for f in fields:
            embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
        embed.set_footer(text="AutoMod")
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        config = await get_guild_config(message.guild.id, "automod")
        if not config.get("enabled") or self._is_exempt(message, config):
            return
        rules = config.get("rules", {})
        checks = [
            (rules.get("anti_insults",  {}), self._check_insults),
            (rules.get("anti_links",    {}), self._check_links),
            (rules.get("anti_mentions", {}), self._check_mentions),
            (rules.get("anti_caps",     {}), self._check_caps),
            (rules.get("anti_spam",     {}), self._check_spam),
            (rules.get("anti_repeat",   {}), self._check_repeat),
        ]
        for rule_config, check_fn in checks:
            if rule_config.get("enabled"):
                if await check_fn(message, rule_config):
                    return

    async def _check_insults(self, message, rule):
        for word in rule.get("words", []):
            if word.lower() in message.content.lower():
                await self._delete_and_warn(message, "Mot interdit détecté", "anti_insults")
                return True
        return False

    async def _check_links(self, message, rule):
        if not URL_REGEX.search(message.content):
            return False
        allowed = rule.get("allowed_domains", [])
        if allowed:
            for url in URL_REGEX.findall(message.content):
                if not any(d in url for d in allowed):
                    await self._delete_and_warn(message, "Lien non autorisé", "anti_links")
                    return True
        else:
            await self._delete_and_warn(message, "Les liens sont interdits", "anti_links")
            return True
        return False

    async def _check_mentions(self, message, rule):
        total = len(message.mentions) + len(message.role_mentions)
        if total >= rule.get("max_mentions", 5):
            await self._delete_and_warn(message, f"Trop de mentions ({total})", "anti_mentions")
            return True
        return False

    async def _check_caps(self, message, rule):
        content = message.content
        if len(content) < rule.get("min_length", 10):
            return False
        letters = [c for c in content if c.isalpha()]
        if not letters:
            return False
        caps_pct = (sum(1 for c in letters if c.isupper()) / len(letters)) * 100
        if caps_pct >= rule.get("max_percent", 70):
            await self._delete_and_warn(message, f"Trop de majuscules ({int(caps_pct)}%)", "anti_caps")
            return True
        return False

    async def _check_spam(self, message, rule):
        key      = f"{message.guild.id}:{message.author.id}"
        now      = datetime.now(timezone.utc)
        interval = rule.get("interval_seconds", 5)
        max_msgs = rule.get("max_messages", 5)
        self._spam_cache[key] = [t for t in self._spam_cache[key] if (now - t).total_seconds() < interval]
        self._spam_cache[key].append(now)
        if len(self._spam_cache[key]) >= max_msgs:
            self._spam_cache[key].clear()
            try:
                await message.author.timeout(now + timedelta(seconds=rule.get("mute_duration_seconds", 300)), reason="Spam")
            except discord.Forbidden:
                pass
            await self._delete_and_warn(message, f"Spam ({max_msgs} msgs/{interval}s)", "anti_spam", auto_warn=False)
            return True
        return False

    async def _check_repeat(self, message, rule):
        key      = f"{message.guild.id}:{message.author.id}"
        now      = datetime.now(timezone.utc)
        interval = rule.get("interval_seconds", 10)
        content  = message.content.lower().strip()
        if not content:
            return False
        self._repeat_cache[key] = [(t, c) for t, c in self._repeat_cache[key] if (now - t).total_seconds() < interval]
        count = sum(1 for _, c in self._repeat_cache[key] if c == content)
        self._repeat_cache[key].append((now, content))
        if count >= rule.get("max_repeats", 3):
            await self._delete_and_warn(message, "Message répété trop de fois", "anti_repeat")
            return True
        return False

    @commands.hybrid_group(name="automod", description="Configure l'auto-modération.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def automod_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @automod_group.command(name="toggle", description="Active/désactive une règle.")
    @app_commands.describe(regle="Règle à modifier", actif="Activer ou désactiver")
    @app_commands.choices(regle=[
        app_commands.Choice(name="Anti-spam",       value="anti_spam"),
        app_commands.Choice(name="Anti-liens",      value="anti_links"),
        app_commands.Choice(name="Anti-mentions",   value="anti_mentions"),
        app_commands.Choice(name="Anti-majuscules", value="anti_caps"),
        app_commands.Choice(name="Anti-répétition", value="anti_repeat"),
        app_commands.Choice(name="Anti-insultes",   value="anti_insults"),
    ])
    async def automod_toggle(self, ctx: commands.Context, regle: str, actif: bool) -> None:
        config = await get_guild_config(ctx.guild.id, "automod")
        config["rules"][regle]["enabled"] = actif
        await set_guild_config(ctx.guild.id, "automod", config)
        state = "✅ Activée" if actif else "❌ Désactivée"
        await ctx.send(embed=discord.Embed(
            title=f"AutoMod — {regle}",
            description=f"Règle **{regle}** : {state}",
            color=settings.COLOR_SUCCESS if actif else settings.COLOR_WARNING
        ))

    @automod_group.command(name="exempt-salon", description="Ajoute ou retire un salon des exemptions.")
    @app_commands.describe(salon="Salon à exempter", action="Ajouter ou retirer")
    @app_commands.choices(action=[
        app_commands.Choice(name="Ajouter",  value="add"),
        app_commands.Choice(name="Retirer",  value="remove"),
    ])
    async def automod_exempt(self, ctx: commands.Context, salon: discord.TextChannel, action: str) -> None:
        config  = await get_guild_config(ctx.guild.id, "automod")
        exempts = config.get("exempt_channels", [])
        if action == "add":
            if salon.id in exempts:
                await ctx.send(f"ℹ️ {salon.mention} est déjà exempté.", ephemeral=True)
                return
            exempts.append(salon.id)
            msg = f"✅ {salon.mention} ajouté aux exemptions."
        else:
            if salon.id not in exempts:
                await ctx.send(f"ℹ️ {salon.mention} n'est pas exempté.", ephemeral=True)
                return
            exempts.remove(salon.id)
            msg = f"✅ {salon.mention} retiré des exemptions."
        config["exempt_channels"] = exempts
        await set_guild_config(ctx.guild.id, "automod", config)
        await ctx.send(embed=discord.Embed(description=msg, color=settings.COLOR_SUCCESS))

    @automod_group.command(name="logs", description="Définit le salon de logs de l'automod.")
    @app_commands.describe(salon="Salon de logs")
    async def automod_logs(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await get_guild_config(ctx.guild.id, "automod")
        config["log_channel_id"] = salon.id
        await set_guild_config(ctx.guild.id, "automod", config)
        await ctx.send(f"✅ Logs AutoMod définis dans {salon.mention}.", ephemeral=True)

    @automod_group.command(name="status", description="Affiche l'état de toutes les règles.")
    async def automod_status(self, ctx: commands.Context) -> None:
        config     = await get_guild_config(ctx.guild.id, "automod")
        rules      = config.get("rules", {})
        exempts    = config.get("exempt_channels", [])
        status_map = {True: "✅", False: "❌"}
        rules_text  = "\n".join(f"{status_map[v.get('enabled', False)]} **{k}**" for k, v in rules.items())
        exempts_text = " ".join(f"<#{ch}>" for ch in exempts) if exempts else "Aucun"
        embed = discord.Embed(title="🔨 AutoMod — Status", color=settings.COLOR_INFO, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="📋 Règles",          value=rules_text,    inline=True)
        embed.add_field(name="🔕 Salons exemptés", value=exempts_text,  inline=False)
        embed.add_field(name="⚙️ Système",         value="✅ Activé" if config.get("enabled") else "❌ Désactivé", inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="warn", description="Avertit manuellement un membre.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(membre="Membre à avertir", raison="Raison")
    async def warn(self, ctx: commands.Context, membre: discord.Member, *, raison: str) -> None:
        if membre.bot:
            await ctx.send("❌ Tu ne peux pas avertir un bot.", ephemeral=True)
            return
        config = await get_guild_config(ctx.guild.id, "automod")
        await self._process_warn(membre, raison, str(ctx.author), ctx.guild, config=config)
        warns = await get_member_warns(ctx.guild.id, str(membre.id))
        embed = discord.Embed(title="⚠️ Avertissement", color=settings.COLOR_WARNING, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Membre", value=membre.mention,  inline=True)
        embed.add_field(name="Raison", value=raison,          inline=True)
        embed.add_field(name="Total",  value=f"`{len(warns)}`", inline=True)
        embed.set_footer(text=f"Par {ctx.author}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="warns", description="Affiche les avertissements d'un membre.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(membre="Membre ciblé")
    async def warns(self, ctx: commands.Context, membre: discord.Member) -> None:
        warn_list = await get_member_warns(ctx.guild.id, str(membre.id))
        if not warn_list:
            await ctx.send(f"✅ {membre.mention} n'a aucun avertissement.", ephemeral=True)
            return
        description = ""
        for i, w in enumerate(warn_list, 1):
            description += f"**{i}.** {w['reason']}\n   ↳ Par `{w['moderator']}` le `{w['date'][:10]}`\n"
        embed = discord.Embed(
            title=f"⚠️ Avertissements de {membre.display_name}",
            description=description,
            color=settings.COLOR_WARNING,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=membre.display_avatar.url)
        embed.set_footer(text=f"Total : {len(warn_list)}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="clearwarns", description="Remet les warns d'un membre à zéro.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(membre="Membre ciblé")
    async def clearwarns(self, ctx: commands.Context, membre: discord.Member) -> None:
        await clear_warns_db(ctx.guild.id, str(membre.id))
        await ctx.send(embed=discord.Embed(
            description=f"✅ Warns de {membre.mention} réinitialisés.",
            color=settings.COLOR_SUCCESS
        ))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoModCog(bot))
    logger.info("Cog 'AutoMod' chargé.")