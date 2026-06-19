# cogs/antiraid.py
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from config.settings import settings
from utils.guild_config import get_guild_config, set_guild_config
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.antiraid")

# Mots trop communs à ignorer absolument (évite faux positifs)
_IGNORE_CONTENTS = {
    "lol", "mdr", "xd", "😂", "ok", "oui", "non", "gg", "np",
    "haha", "lmao", "rip", "omg", "wsh", "sah", "frr", "ouais",
    "non", "okok", "ahh", "ohh", "wow", "ptdr", "skl", "nrv",
}


class AntiRaidCog(commands.Cog, name="AntiRaid"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._join_cache:  dict[int, list]  = defaultdict(list)
        # Structure unifiée : {guild_id: {content: [(user_id, datetime), ...]}}
        # user_id et timestamp ensemble → nettoyage parfaitement synchronisé
        self._spam_cache:  dict[int, dict]  = defaultdict(lambda: defaultdict(list))
        self._img_cache:   dict[int, list]  = defaultdict(list)   # [(user_id, datetime)]
        self._processing:  set              = set()
        self._saved_permissions: dict[int, dict] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────────

    async def _send_alert(self, guild, title, description, color=None, fields=None) -> None:
        if color is None:
            color = settings.COLOR_ERROR
        config     = await get_guild_config(guild.id, "antiraid")
        channel_id = config.get("alert_channel_id")
        channel    = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=f"🚨 {title}", description=description,
            color=color, timestamp=datetime.now(timezone.utc)
        )
        if fields:
            for f in fields:
                embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
        embed.set_footer(text=f"{guild.name} • AntiRaid System")
        staff_role_id = config.get("staff_role_id")
        content = f"<@&{staff_role_id}>" if staff_role_id else None
        try:
            await channel.send(content=content, embed=embed)
        except discord.Forbidden:
            pass

    async def _apply_lockdown(self, guild: discord.Guild) -> int:
        config = await get_guild_config(guild.id, "antiraid")
        config["lockdown_active"] = True
        await set_guild_config(guild.id, "antiraid", config)
        self._saved_permissions[guild.id] = {}
        locked = 0
        for channel in guild.text_channels:
            try:
                perm = channel.overwrites_for(guild.default_role)
                self._saved_permissions[guild.id][channel.id] = perm.send_messages
                if perm.send_messages is not False:
                    await channel.set_permissions(guild.default_role, send_messages=False)
                    locked += 1
            except discord.Forbidden:
                pass
        logger.info(f"Lockdown activé — {locked} salons verrouillés sur {guild.name}")
        return locked

    async def _lift_lockdown(self, guild: discord.Guild) -> int:
        config = await get_guild_config(guild.id, "antiraid")
        config["lockdown_active"] = False
        await set_guild_config(guild.id, "antiraid", config)
        unlocked    = 0
        saved_perms = self._saved_permissions.get(guild.id, {})
        for channel in guild.text_channels:
            try:
                saved_value = saved_perms.get(channel.id, None)
                perm        = channel.overwrites_for(guild.default_role)
                if perm.send_messages is False:
                    await channel.set_permissions(guild.default_role, send_messages=saved_value)
                    unlocked += 1
            except discord.Forbidden:
                pass
        self._saved_permissions.pop(guild.id, None)
        logger.info(f"Lockdown levé — {unlocked} salons restaurés sur {guild.name}")
        return unlocked

    async def _mute_member(self, member, reason, duration_minutes=10):
        try:
            until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
            await member.timeout(until, reason=reason)
            return True
        except discord.Forbidden:
            return False

    async def _kick_member(self, member, reason):
        try:
            await member.kick(reason=reason)
            return True
        except discord.Forbidden:
            return False

    async def _ban_member(self, member, reason):
        try:
            await member.ban(reason=reason, delete_message_days=1)
            return True
        except discord.Forbidden:
            return False

    def _member_has_roles(self, member: discord.Member) -> bool:
        """Membre avec au moins 1 rôle autre que @everyone → pas un raider."""
        return len(member.roles) > 1

    # ──────────────────────────────────────────────────────────────────────────
    # ON_MEMBER_JOIN
    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        config = await get_guild_config(member.guild.id, "antiraid")
        if not config.get("enabled"):
            return

        guild = member.guild
        now   = datetime.now(timezone.utc)

        # Compte récent
        new_account_cfg = config["thresholds"].get("new_account", {})
        if new_account_cfg.get("enabled"):
            min_age_days = new_account_cfg.get("min_age_days", 7)
            account_age  = (now - member.created_at.replace(tzinfo=None)).days
            if account_age < min_age_days:
                action = new_account_cfg.get("action", "ban")
                reason = f"AntiRaid — Compte trop récent ({account_age}j)"
                if action == "kick":   await self._kick_member(member, reason)
                elif action == "ban":  await self._ban_member(member, reason)
                elif action == "mute": await self._mute_member(member, reason)
                await self._send_alert(guild, "Compte suspect détecté",
                    f"{member.mention} a été **{action}** automatiquement.",
                    fields=[
                        {"name": "👤 Membre",        "value": str(member),          "inline": True},
                        {"name": "📅 Âge du compte", "value": f"`{account_age}` j", "inline": True},
                        {"name": "⚡ Action",         "value": f"`{action}`",        "inline": True},
                    ])
                return

        # Flood de joins
        join_cfg = config["thresholds"].get("join_flood", {})
        if not join_cfg.get("enabled"):
            return

        max_joins = join_cfg.get("max_joins", 5)        # défaut relevé à 5
        interval  = join_cfg.get("interval_seconds", 10)

        self._join_cache[guild.id] = [
            t for t in self._join_cache[guild.id]
            if (now - t).total_seconds() < interval
        ]
        self._join_cache[guild.id].append(now)

        if len(self._join_cache[guild.id]) >= max_joins:
            self._join_cache[guild.id].clear()
            action = join_cfg.get("action", "lockdown_and_mute")
            locked = muted = banned = 0

            if "lockdown" in action:
                locked = await self._apply_lockdown(guild)

            for m in list(guild.members):
                if m.bot or not m.joined_at:
                    continue
                if (now - m.joined_at.replace(tzinfo=None)).total_seconds() > 60:
                    continue
                reason      = "AntiRaid — Flood de joins détecté"
                account_age = (now - m.created_at.replace(tzinfo=None)).days
                min_age     = config["thresholds"].get("new_account", {}).get("min_age_days", 7)
                if account_age < min_age:
                    if await self._ban_member(m, reason + " (compte suspect)"):
                        banned += 1
                elif "mute" in action:
                    if await self._mute_member(m, reason):
                        muted += 1

            await self._send_alert(guild, "🚨 RAID DÉTECTÉ — Flood de joins",
                f"`{max_joins}` membres ont rejoint en moins de `{interval}` secondes !",
                fields=[
                    {"name": "🔒 Salons verrouillés", "value": f"`{locked}`",             "inline": True},
                    {"name": "🔇 Membres mutés",      "value": f"`{muted}`",               "inline": True},
                    {"name": "🚫 Bannis",              "value": f"`{banned}`",              "inline": True},
                    {"name": "💡 Pour lever",          "value": "`&antiraid unlockdown`",   "inline": True},
                ])

    # ──────────────────────────────────────────────────────────────────────────
    # ON_MESSAGE — VERSION CORRIGÉE
    # ──────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        config = await get_guild_config(message.guild.id, "antiraid")
        if not config.get("enabled"):
            return

        # Membres avec des rôles = membres établis, on les ignore
        if self._member_has_roles(message.author):
            return

        guild   = message.guild
        now     = datetime.now(timezone.utc)
        user_id = message.author.id

        # ── Spam d'images ──────────────────────────────────────────────────────
        spam_cfg = config["thresholds"].get("mass_spam", {})
        if message.attachments and spam_cfg.get("enabled"):
            image_ext = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4")
            if any(a.filename.lower().endswith(image_ext) for a in message.attachments):
                max_same = spam_cfg.get("max_same_messages", 5)
                interval = spam_cfg.get("interval_seconds", 10)

                # Nettoyage synchronisé (user_id + timestamp ensemble)
                self._img_cache[guild.id] = [
                    (uid, t) for uid, t in self._img_cache[guild.id]
                    if (now - t).total_seconds() < interval
                ]
                # 1 entrée par utilisateur unique dans la fenêtre
                if user_id not in {uid for uid, _ in self._img_cache[guild.id]}:
                    self._img_cache[guild.id].append((user_id, now))

                if len(self._img_cache[guild.id]) >= max_same:
                    action      = spam_cfg.get("action", "ban")
                    spammer_ids = [uid for uid, _ in self._img_cache[guild.id]]
                    self._img_cache[guild.id] = []
                    banned = 0
                    for sid in spammer_ids:
                        m = guild.get_member(sid)
                        if m and not m.bot:
                            try:
                                await message.delete()
                            except discord.NotFound:
                                pass
                            if action == "ban" and await self._ban_member(m, "AntiRaid — Spam images"):
                                banned += 1
                    await self._send_alert(guild, "🖼️ Spam d'images détecté",
                        f"`{len(spammer_ids)}` membres spammaient des images.",
                        fields=[
                            {"name": "👥 Sanctionnés", "value": f"`{banned}`", "inline": True},
                            {"name": "⚡ Action",       "value": f"`{action}`", "inline": True},
                        ])

        # ── Mass mentions ──────────────────────────────────────────────────────
        mention_cfg = config["thresholds"].get("mass_mention", {})
        if mention_cfg.get("enabled"):
            total_mentions = len(message.mentions) + len(message.role_mentions)
            if total_mentions >= mention_cfg.get("max_mentions", 10):
                action = mention_cfg.get("action", "ban")
                reason = f"AntiRaid — Mass mention ({total_mentions} mentions)"
                if action == "ban":    await self._ban_member(message.author, reason)
                elif action == "kick": await self._kick_member(message.author, reason)
                elif action == "mute": await self._mute_member(message.author, reason)
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                await self._send_alert(guild, "Mass Mention détectée",
                    f"{message.author.mention} → **{action}** automatique.",
                    fields=[
                        {"name": "👤 Membre",   "value": str(message.author),  "inline": True},
                        {"name": "📢 Mentions", "value": f"`{total_mentions}`", "inline": True},
                    ])
                return

        # ── Spam massif texte ──────────────────────────────────────────────────
        if not spam_cfg.get("enabled"):
            return

        content = message.content.lower().strip()

        # GARDE 1 : contenu trop court (min 15 chars, pas 3)
        if not content or len(content) < 15:
            return

        # GARDE 2 : mots communs explicitement ignorés
        if content in _IGNORE_CONTENTS:
            return

        max_same = spam_cfg.get("max_same_messages", 5)
        interval = spam_cfg.get("interval_seconds", 10)

        # FIX PRINCIPAL : nettoyage synchronisé — user_id + timestamp ensemble
        self._spam_cache[guild.id][content] = [
            (uid, t) for uid, t in self._spam_cache[guild.id][content]
            if (now - t).total_seconds() < interval
        ]

        # 1 entrée par utilisateur unique dans la fenêtre temporelle
        existing_users = {uid for uid, _ in self._spam_cache[guild.id][content]}
        if user_id not in existing_users:
            self._spam_cache[guild.id][content].append((user_id, now))

        if len(self._spam_cache[guild.id][content]) >= max_same:
            action      = spam_cfg.get("action", "ban")
            spammer_ids = [uid for uid, _ in self._spam_cache[guild.id][content]]
            self._spam_cache[guild.id][content] = []   # reset immédiat

            banned = 0
            for sid in spammer_ids:
                if sid in self._processing:
                    continue
                self._processing.add(sid)
                try:
                    m = guild.get_member(sid)
                    if m and not m.bot:
                        reason = "AntiRaid — Spam massif coordonné"
                        if action == "ban"  and await self._ban_member(m, reason):   banned += 1
                        elif action == "kick" and await self._kick_member(m, reason): banned += 1
                        elif action == "mute" and await self._mute_member(m, reason): banned += 1
                finally:
                    self._processing.discard(sid)

            await self._send_alert(guild, "🚨 Spam Massif Coordonné",
                f"`{len(spammer_ids)}` membres ont envoyé le même message dans la même fenêtre !",
                fields=[
                    {"name": "👥 Sanctionnés", "value": f"`{banned}`",  "inline": True},
                    {"name": "⚡ Action",       "value": f"`{action}`", "inline": True},
                    {"name": "📝 Message",      "value": f"`{content[:80]}`", "inline": False},
                ])

    # ──────────────────────────────────────────────────────────────────────────
    # COMMANDES
    # ──────────────────────────────────────────────────────────────────────────

    @commands.hybrid_group(name="antiraid", aliases=["ar"], description="Gère le système anti-raid.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antiraid_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @antiraid_group.command(name="status", description="Affiche l'état du système anti-raid.")
    async def antiraid_status(self, ctx: commands.Context) -> None:
        config     = await get_guild_config(ctx.guild.id, "antiraid")
        thresholds = config.get("thresholds", {})
        embed = discord.Embed(title="🛡️ AntiRaid — Status", color=settings.COLOR_INFO, timestamp=datetime.now(timezone.utc))
        embed.add_field(name="⚙️ Système",  value="✅ Activé" if config.get("enabled") else "❌ Désactivé", inline=True)
        embed.add_field(name="🔒 Lockdown", value="🔴 Actif" if config.get("lockdown_active") else "🟢 Inactif", inline=True)
        rules_text = "\n".join(f"{'✅' if v.get('enabled') else '❌'} **{k}**" for k, v in thresholds.items())
        embed.add_field(name="📋 Règles", value=rules_text or "Aucune", inline=False)
        channel = self.bot.get_channel(config.get("alert_channel_id"))
        embed.add_field(name="📢 Salon d'alertes", value=channel.mention if channel else "Non configuré", inline=True)
        embed.set_footer(text=f"{ctx.guild.name} • AntiRaid")
        await ctx.send(embed=embed, ephemeral=True)

    @antiraid_group.command(name="toggle", description="Active ou désactive l'anti-raid.")
    async def antiraid_toggle(self, ctx: commands.Context) -> None:
        config  = await get_guild_config(ctx.guild.id, "antiraid")
        current = config.get("enabled", True)
        config["enabled"] = not current
        await set_guild_config(ctx.guild.id, "antiraid", config)
        state = "✅ Activé" if not current else "❌ Désactivé"
        color = settings.COLOR_SUCCESS if not current else settings.COLOR_WARNING
        await ctx.send(embed=discord.Embed(title=f"AntiRaid — {state}", color=color))

    @antiraid_group.command(name="lockdown", description="Active le lockdown manuellement.")
    @app_commands.describe(raison="Raison du lockdown (optionnel)")
    async def antiraid_lockdown(self, ctx: commands.Context, *, raison: str = "Lockdown manuel") -> None:
        await ctx.defer()
        locked = await self._apply_lockdown(ctx.guild)
        embed = discord.Embed(
            title="🔒 Lockdown activé",
            description=f"`{locked}` salons verrouillés.\n**Raison :** {raison}",
            color=settings.COLOR_ERROR, timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="💡 Pour lever", value="`&antiraid unlockdown`", inline=False)
        embed.set_footer(text=f"Activé par {ctx.author}")
        await ctx.send(embed=embed)

    @antiraid_group.command(name="unlockdown", description="Lève le lockdown.")
    async def antiraid_unlockdown(self, ctx: commands.Context) -> None:
        await ctx.defer()
        unlocked = await self._lift_lockdown(ctx.guild)
        embed = discord.Embed(
            title="🔓 Lockdown levé",
            description=f"`{unlocked}` salons déverrouillés.",
            color=settings.COLOR_SUCCESS, timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"Levé par {ctx.author}")
        await ctx.send(embed=embed)

    @antiraid_group.command(name="set-channel", description="Définit le salon d'alertes anti-raid.")
    @app_commands.describe(salon="Salon des alertes")
    async def antiraid_set_channel(self, ctx: commands.Context, salon: discord.TextChannel) -> None:
        config = await get_guild_config(ctx.guild.id, "antiraid")
        config["alert_channel_id"] = salon.id
        await set_guild_config(ctx.guild.id, "antiraid", config)
        await ctx.send(embed=discord.Embed(description=f"✅ Salon d'alertes défini : {salon.mention}", color=settings.COLOR_SUCCESS))

    @antiraid_group.command(name="set-staff", description="Définit le rôle staff à mentionner lors d'un raid.")
    @app_commands.describe(role="Rôle staff")
    async def antiraid_set_staff(self, ctx: commands.Context, role: discord.Role) -> None:
        config = await get_guild_config(ctx.guild.id, "antiraid")
        config["staff_role_id"] = role.id
        await set_guild_config(ctx.guild.id, "antiraid", config)
        await ctx.send(embed=discord.Embed(description=f"✅ Rôle staff défini : {role.mention}", color=settings.COLOR_SUCCESS))

    @antiraid_group.command(name="config", description="Configure les seuils de détection.")
    @app_commands.describe(regle="Règle à configurer", parametre="Paramètre à modifier", valeur="Nouvelle valeur")
    @app_commands.choices(regle=[
        app_commands.Choice(name="Flood de joins", value="join_flood"),
        app_commands.Choice(name="Compte récent",  value="new_account"),
        app_commands.Choice(name="Mass mention",   value="mass_mention"),
        app_commands.Choice(name="Spam massif",    value="mass_spam"),
    ])
    async def antiraid_config(self, ctx: commands.Context, regle: str, parametre: str, valeur: str) -> None:
        config = await get_guild_config(ctx.guild.id, "antiraid")
        if regle not in config["thresholds"]:
            await ctx.send(f"❌ Règle `{regle}` introuvable.", ephemeral=True)
            return
        if valeur.isdigit():
            valeur = int(valeur)
        elif valeur.lower() in ("true", "false"):
            valeur = valeur.lower() == "true"
        config["thresholds"][regle][parametre] = valeur
        await set_guild_config(ctx.guild.id, "antiraid", config)
        await ctx.send(embed=discord.Embed(description=f"✅ `{regle}.{parametre}` = `{valeur}`", color=settings.COLOR_SUCCESS))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaidCog(bot))
    logger.info("Cog 'AntiRaid' chargé.")
