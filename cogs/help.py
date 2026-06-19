"""
cogs/help.py — Fantoma Bot
Système d'aide persistant (survit aux redémarrages)
"""

import discord
from discord.ext import commands

# Couleurs (identiques à config/settings.py, pas de dépendance externe)
COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARNING = 0xFEE75C
COLOR_ERROR   = 0xED4245
COLOR_INFO    = 0xEB459E
COLOR_NEUTRAL = 0x2B2D31

# ══════════════════════════════════════════════════════════════════════════════
# DONNÉES — un dict par système
# ══════════════════════════════════════════════════════════════════════════════

SYSTEMS: dict[str, dict] = {
    "levels": {
        "emoji": "⭐",
        "label": "XP / Niveaux",
        "color": COLOR_PRIMARY,
        "description": "Récompense l'activité de tes membres avec un système d'expérience par salon texte et vocal.",
        "how_it_works": [
            "Chaque message = **15 ± 10 XP** (cooldown 60 s, commandes exclues)",
            "Vocal : **+10 XP toutes les 2 min** (ignore seul / muté / sourd / AFK)",
            "Chaque serveur a son propre classement indépendant",
            "Le niveau monte automatiquement — annonce configurable",
            "**Multiplicateurs XP** : certains salons ou rôles peuvent accélérer ou ralentir le gain d'XP",
        ],
        "commands": [
            ("&rank [@membre]",            "Affiche ton niveau et ton XP"),
            ("&leaderboard",               "Top 10 membres du serveur"),
            ("&xp-add @membre <points>",   "Ajoute de l'XP manuellement **[Admin]**"),
            ("&xp-reset @membre",          "Remet l'XP à zéro **[Admin]**"),
            ("&cache-stats",               "Stats du cache (debug) **[Admin]**"),
        ],
    },

    "xp_multipliers": {
        "emoji": "🔢",
        "label": "Multiplicateurs XP",
        "color": COLOR_INFO,
        "description": "Accélère ou ralentit le gain d'XP par salon ou par rôle. Le multiplicateur final est le produit de tous les applicables.",
        "how_it_works": [
            "Définis un multiplicateur **par salon** (ex: #général 2x)",
            "Définis un multiplicateur **par rôle** (ex: @VIP 1.5x)",
            "Le multiplicateur **final** est le **produit** de tous les applicables",
            "Exemple : salon 2x + rôle VIP 1.5x = **3x total**",
            "Clamp de sécurité : entre **0.1x** et **10x**",
            "Le cache est invalidé automatiquement quand tu modifies un multiplicateur",
        ],
        "commands": [
            ("&xp-multiplier channel #salon <valeur>",      "Définit un multiplicateur de salon **[Admin]**"),
            ("&xp-multiplier channel-remove #salon",         "Supprime le multiplicateur d'un salon **[Admin]**"),
            ("&xp-multiplier role @Rôle <valeur>",           "Définit un multiplicateur de rôle **[Admin]**"),
            ("&xp-multiplier role-remove @Rôle",             "Supprime le multiplicateur d'un rôle **[Admin]**"),
            ("&xp-multiplier list",                          "Liste tous les multiplicateurs actifs **[Admin]**"),
        ],
    },

    "economy": {
        "emoji": "💰",
        "label": "Économie",
        "color": 0xFEE75C,
        "description": "Monnaie virtuelle pour gamifier l'expérience serveur : coins, récompenses quotidiennes et boutique.",
        "how_it_works": [
            "Chaque membre possède un solde de **coins** propre au serveur",
            "Récupère ta récompense quotidienne une fois par 24 h",
            "Transfère des coins à n'importe quel membre",
            "Dépense tes coins en boutique (articles configurables)",
        ],
        "commands": [
            ("&balance [@membre]",         "Affiche ton solde (ou celui d'un membre)"),
            ("&daily",                     "Récompense quotidienne (cooldown 24 h)"),
            ("&pay @membre <montant>",     "Transfère des coins"),
            ("&shop",                      "Boutique du serveur"),
        ],
    },

    "tickets": {
        "emoji": "🎫",
        "label": "Tickets",
        "color": COLOR_SUCCESS,
        "description": "Système de support multi-catégories avec salons privés, rôles staff dédiés et transcripts HTML.",
        "how_it_works": [
            "4 catégories : **Plainte ⚖️ · Candidature 📋 · Owner 👑 · Partenariat 🤝**",
            "Chaque type a son propre rôle staff et message de bienvenue",
            "À la fermeture → transcript HTML généré automatiquement",
            "Le panel est restauré au redémarrage (vue persistante)",
        ],
        "commands": [
            ("&ticket-panel",                       "Affiche le panneau de création **[Admin]**"),
            ("&ticket fermer",                      "Ferme le ticket (avec transcript)"),
            ("&ticket ajouter @membre",             "Ajoute un membre au ticket"),
            ("&ticket retirer @membre",             "Retire un membre du ticket"),
            ("&ticket-config categorie <type> <id>","Configure la catégorie d'un type **[Admin]**"),
            ("&ticket-config logs <#salon>",        "Salon de logs tickets **[Admin]**"),
        ],
    },

    "automod": {
        "emoji": "🔨",
        "label": "AutoMod",
        "color": COLOR_ERROR,
        "description": "Modération automatique avec 6 règles configurables et escalade progressive des sanctions.",
        "how_it_works": [
            "**6 règles** : anti-insultes · anti-liens · anti-mentions · anti-caps · anti-spam · anti-répétition",
            "Les infractions génèrent des **warns** stockés en base",
            "Escalade : `3 warns → mute` · `5 → kick` · `7 → ban`",
            "Salons et rôles exemptables par serveur",
        ],
        "commands": [
            ("&automod toggle <règle>",         "Active / désactive une règle **[Admin]**"),
            ("&automod exempt-salon <#salon>",  "Exempte un salon **[Admin]**"),
            ("&automod logs <#salon>",          "Salon de logs automod **[Admin]**"),
            ("&automod status",                 "État actuel de l'automod"),
            ("&warn @membre <raison>",          "Warn manuel **[Mod]**"),
            ("&warns @membre",                  "Liste les warns d'un membre"),
            ("&clearwarns @membre",             "Supprime tous les warns **[Admin]**"),
        ],
    },

    "antiraid": {
        "emoji": "🚨",
        "label": "Anti-Raid",
        "color": COLOR_ERROR,
        "description": "Détection et neutralisation automatique des raids : flood joins, comptes récents, mass-mentions.",
        "how_it_works": [
            "**Flood joins** : 3 arrivées en 10 s → lockdown + mute automatique",
            "**Comptes récents** : créé depuis < 7 jours → ban automatique",
            "**Mass-mention** : ≥ 10 mentions dans un message → ban",
            "**Lockdown** : permissions sauvegardées avant, restaurées exactement après",
        ],
        "commands": [
            ("&antiraid status",            "État de l'anti-raid"),
            ("&antiraid toggle",            "Active / désactive **[Admin]**"),
            ("&antiraid lockdown",          "Lockdown manuel **[Admin]**"),
            ("&antiraid unlockdown",        "Fin du lockdown **[Admin]**"),
            ("&antiraid set-channel <#>",   "Salon de logs **[Admin]**"),
            ("&antiraid config",            "Configuration détaillée"),
        ],
    },

    "welcome": {
        "emoji": "👋",
        "label": "Bienvenue",
        "color": COLOR_SUCCESS,
        "description": "Messages d'accueil personnalisés avec détection nouveau membre / membre de retour.",
        "how_it_works": [
            "Distingue automatiquement **nouveau membre** vs **membre de retour**",
            "3 embeds configurables : bienvenue · retour · au revoir",
            "Variables : `{mention}` · `{username}` · `{server}` · `{count}` · `{inviter}`",
            "L'inviteur est **affiché automatiquement** dans le message de bienvenue",
            "Bannière URL personnalisable par embed",
        ],
        "commands": [
            ("&bienvenue set <#salon>",     "Salon de bienvenue **[Admin]**"),
            ("&bienvenue toggle",           "Active / désactive **[Admin]**"),
            ("&bienvenue test",             "Message de test"),
            ("&bienvenue banniere <url>",   "Bannière de bienvenue **[Admin]**"),
            ("&aurevoir set <#salon>",      "Salon d'au revoir **[Admin]**"),
            ("&retour set <#salon>",        "Salon de retour **[Admin]**"),
        ],
    },

    "moderation": {
        "emoji": "🔧",
        "label": "Modération",
        "color": COLOR_INFO,
        "description": "Outils de modération manuelle avec historique complet des actions.",
        "how_it_works": [
            "Toutes les actions sont **loggées** dans le salon de logs configuré",
            "Mute temporaire (durée) ou permanent",
            "Historique des sanctions consultable par membre",
        ],
        "commands": [
            ("&mute @membre [durée] [raison]", "Mute **[Mod]**"),
            ("&unmute @membre",                "Unmute **[Mod]**"),
            ("&kick @membre [raison]",         "Expulse **[Mod]**"),
            ("&ban @membre [raison]",          "Banne **[Mod]**"),
            ("&unban <id>",                    "Débanne **[Admin]**"),
            ("&clear <nombre>",                "Supprime des messages **[Mod]**"),
            ("&slowmode <secondes>",           "Slowmode sur le salon **[Mod]**"),
            ("&lock [#salon]",                 "Verrouille un salon **[Mod]**"),
            ("&unlock [#salon]",               "Déverrouille un salon **[Mod]**"),
        ],
    },

    "roles": {
        "emoji": "🎭",
        "label": "Rôles",
        "color": COLOR_PRIMARY,
        "description": "Autorole à l'arrivée, rôles de niveau automatiques et panneau de choix de rôles.",
        "how_it_works": [
            "**Autorole** : rôle attribué automatiquement à chaque nouveau membre",
            "**Rôles de niveau** : attribués automatiquement à certains paliers XP",
            "**Panneau** : boutons persistants pour que les membres choisissent leurs rôles",
        ],
        "commands": [
            ("&roles-panel",            "Affiche le panneau de rôles **[Admin]**"),
            ("&autorole set @rôle",     "Définit le rôle automatique **[Admin]**"),
            ("&autorole remove",        "Supprime l'autorole **[Admin]**"),
            ("&autorole info",          "Affiche l'autorole actuel"),
        ],
    },

    "giveaway": {
        "emoji": "🎁",
        "label": "Giveaways",
        "color": COLOR_INFO,
        "description": "Concours avec tirage automatique, conditions d'accès et reroll.",
        "how_it_works": [
            "Participation par bouton — reclic = retrait de la participation",
            "Nombre de participants mis à jour en **temps réel**",
            "Tirage automatique à l'expiration (vérification toutes les 30 s)",
            "Conditions optionnelles : invitations · niveau XP · coins",
            "Durées : `30m` · `1h` · `2d` · etc.",
        ],
        "commands": [
            ("&giveaway start <durée> <n> <prix>",              "Lance un giveaway **[Admin]**"),
            ("&giveaway start ... min_invites=5",               "Requiert 5 invitations réelles **[Admin]**"),
            ("&giveaway start ... min_level=10",                "Requiert le niveau 10 **[Admin]**"),
            ("&giveaway start ... min_coins=500",               "Requiert 500 coins **[Admin]**"),
            ("&giveaway end <id>",                              "Termine immédiatement **[Admin]**"),
            ("&giveaway reroll <id>",                           "Nouveau gagnant **[Admin]**"),
            ("&giveaway list",                                  "Giveaways actifs"),
        ],
    },

    "polls": {
        "emoji": "📊",
        "label": "Sondages",
        "color": COLOR_PRIMARY,
        "description": "Sondages interactifs avec votes par bouton et pourcentages en temps réel.",
        "how_it_works": [
            "Vote par bouton — **1 vote par membre** maximum",
            "Reclic sur son choix = retrait du vote",
            "Pourcentages mis à jour en **temps réel**",
            "Fermeture automatique + résultats finaux affichés",
        ],
        "commands": [
            ("&sondage <question> <choix1|choix2>", "Crée un sondage **[Mod]**"),
            ("&quickpoll <question>",               "Sondage rapide Oui/Non **[Mod]**"),
            ("&sondage-end <id>",                   "Termine un sondage **[Mod]**"),
            ("&sondage-list",                       "Sondages actifs"),
        ],
    },

    "games": {
        "emoji": "🎮",
        "label": "Mini-jeux",
        "color": COLOR_SUCCESS,
        "description": "Jeux interactifs pour animer le serveur.",
        "commands": [
            ("&de [faces] [nombre]",       "Lance des dés (ex: `&de 6 2`)"),
            ("&pfc [@membre]",             "Pierre-Feuille-Ciseaux"),
            ("&coinflip",                  "Pile ou Face"),
            ("&8ball <question>",          "Boule magique (17 réponses)"),
            ("&chiffre [min] [max]",       "Devine le nombre (modal)"),
            ("&roulette",                  "Roulette russe fun"),
            ("&choix <opt1|opt2|...>",     "Choix aléatoire"),
            ("&tictactoe @membre",         "Morpion 3×3 interactif"),
        ],
    },

    "custom_commands": {
        "emoji": "🤖",
        "label": "Commandes custom",
        "color": COLOR_INFO,
        "description": "Crée tes propres commandes avec texte, embeds et variables dynamiques.",
        "how_it_works": [
            "Réponse texte **ou** embed : `{embed: titre | description | couleur}`",
            "Variables : `{mention}` · `{user}` · `{serveur}` · `{count}` · `{args}` · `{date}`",
            "Maximum **50 commandes** par serveur",
            "Accessible avec `&nom` comme une vraie commande",
        ],
        "commands": [
            ("&cmd-add <nom> <réponse>",   "Crée une commande **[Admin]**"),
            ("&cmd-edit <nom> <réponse>",  "Modifie une commande **[Admin]**"),
            ("&cmd-delete <nom>",          "Supprime une commande **[Admin]**"),
            ("&cmd-list",                  "Liste toutes les commandes custom"),
            ("&cmd-info <nom>",            "Détails d'une commande"),
        ],
    },

    "fun": {
        "emoji": "😄",
        "label": "Fun",
        "color": 0xFEE75C,
        "description": "Commandes légères pour animer les discussions.",
        "commands": [
            ("&ship @membre1 @membre2",    "Compatibilité amoureuse 💘"),
            ("&pp [@membre]",             "... tu sais déjà 😏"),
            ("&slap @membre",             "Gifle quelqu'un"),
            ("&hug @membre",              "Fais un câlin"),
            ("&meme",                     "Mème Reddit aléatoire"),
        ],
    },

    "birthdays": {
        "emoji": "🎂",
        "label": "Anniversaires",
        "color": COLOR_INFO,
        "description": "Annonces automatiques d'anniversaires avec rôle temporaire le jour J.",
        "how_it_works": [
            "Enregistre ta date une seule fois — le bot s'occupe du reste",
            "Annonce automatique le jour de l'anniversaire",
            "Rôle temporaire attribué le jour J",
        ],
        "commands": [
            ("&birthday set <jj/mm/aaaa>", "Enregistre ta date de naissance"),
            ("&birthday next",             "Prochain anniversaire du serveur"),
            ("&birthday list",             "Tous les anniversaires enregistrés"),
        ],
    },

    "stats": {
        "emoji": "📈",
        "label": "Statistiques",
        "color": COLOR_PRIMARY,
        "description": "Salons compteurs mis à jour en temps réel (membres, bots, salons).",
        "how_it_works": [
            "Les salons affichent automatiquement les stats actuelles",
            "Mise à jour en temps réel à chaque changement",
        ],
        "commands": [
            ("&stats set-members <#salon>", "Compteur membres **[Admin]**"),
            ("&stats set-bots <#salon>",    "Compteur bots **[Admin]**"),
            ("&stats set-channels <#salon>","Compteur salons **[Admin]**"),
        ],
    },

    "reaction_roles": {
        "emoji": "🏷️",
        "label": "Reaction Roles",
        "color": COLOR_SUCCESS,
        "description": "Attribution de rôles via réaction emoji sur un message.",
        "how_it_works": [
            "Réagis avec un émoji → rôle attribué automatiquement",
            "Retire la réaction → rôle retiré",
        ],
        "commands": [
            ("&rr add <msg_id> <émoji> @rôle", "Associe émoji ↔ rôle **[Admin]**"),
            ("&rr remove <msg_id> <émoji>",    "Supprime une association **[Admin]**"),
            ("&rr list",                       "Toutes les associations"),
        ],
    },

    "backup": {
        "emoji": "💾",
        "label": "Backup",
        "color": COLOR_NEUTRAL,
        "description": "Sauvegarde automatique de toutes les données MongoDB en ZIP envoyé sur Discord.",
        "how_it_works": [
            "Export ZIP de **toutes** les collections MongoDB",
            "Envoi dans un salon Discord dédié",
            "Backup automatique toutes les **12 h** (configurable)",
            "Restauration avec confirmation par boutons",
        ],
        "commands": [
            ("&backup now",                "Backup immédiat **[Admin]**"),
            ("&backup set-channel <#>",    "Salon de backup **[Admin]**"),
            ("&backup interval <heures>",  "Intervalle auto **[Admin]**"),
            ("&backup toggle",             "Active / désactive le backup auto **[Admin]**"),
            ("&backup status",             "État du backup"),
            ("&backup restore <id>",       "Restaure un backup **[Admin]**"),
        ],
    },

    "blacklist": {
        "emoji": "🚫",
        "label": "Blacklist",
        "color": COLOR_ERROR,
        "description": "Ban automatique dès qu'un utilisateur blacklisté tente de rejoindre.",
        "how_it_works": [
            "Un membre blacklisté reçoit un DM et est banni immédiatement",
            "Fonctionne même si l'utilisateur quitte et revient",
            "Compteur de tentatives d'entrée conservé",
        ],
        "commands": [
            ("&blacklist add <id> <raison>", "Ajoute à la blacklist **[Admin]**"),
            ("&blacklist remove <id>",       "Retire de la blacklist **[Admin]**"),
            ("&blacklist list",              "Liste noire complète **[Admin]**"),
            ("&blacklist check <id>",        "Vérifie un ID"),
        ],
    },

    "invites": {
        "emoji": "📨",
        "label": "Invitations",
        "color": COLOR_PRIMARY,
        "description": "Suivi des invitations : qui a invité qui, classement et conditions pour les giveaways.",
        "how_it_works": [
            "Chaque arrivée est trackée → l'**inviteur est détecté automatiquement**",
            "L'inviteur apparaît dans le **message de bienvenue** et les **logs**",
            "Compteur : **réelles** (total − partis − fakes)",
            "Utilisable comme **condition d'accès** aux giveaways (`min_invites=X`)",
            "Le bot doit avoir la permission **Gérer le serveur** pour lire les invites",
        ],
        "commands": [
            ("&invites [@membre]",     "Affiche ton compteur d'invitations"),
            ("&invites-top",           "Classement des meilleurs inviteurs"),
            ("&invites-reset @membre", "Remet à zéro les invitations **[Admin]**"),
        ],
    },

    "utils": {
        "emoji": "🛠️",
        "label": "Utilitaires",
        "color": COLOR_PRIMARY,
        "description": "Commandes d'information générales et outils de gestion du serveur.",
        "commands": [
            ("&ping",                      "Latence du bot"),
            ("&userinfo [@membre]",        "Infos sur un membre"),
            ("&serverinfo",                "Infos sur le serveur"),
            ("&avatar [@membre]",          "Avatar d'un membre"),
            ("&say <message>",             "Fait parler le bot **[Mod]**"),
            ("&embed",                     "Crée un embed (modal) **[Mod]**"),
            ("&config reset <type>",       "Remet une config par défaut **[Admin]**"),
            ("&config view <type>",        "Affiche la config actuelle **[Admin]**"),
        ],
    },
}

# Groupes pour l'embed d'accueil
GROUPS = [
    ("⚔️ Modération",      ["moderation", "automod", "antiraid", "blacklist"]),
    ("🎉 Communauté",      ["levels", "xp_multipliers", "economy", "roles", "reaction_roles", "birthdays", "invites"]),
    ("🛡️ Gestion",         ["tickets", "welcome", "backup", "custom_commands"]),
    ("🎮 Divertissement",  ["games", "fun", "giveaway", "polls"]),
    ("📊 Info & Stats",    ["utils", "stats"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def build_home_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📖 Fantoma Bot — Aide",
        description=(
            "Bot complet multi-serveur.\n"
            "Préfixe : **`&`** · Slash : **`/`**\n\n"
            "Sélectionne un système dans le menu pour voir les détails et toutes les commandes."
        ),
        color=COLOR_PRIMARY,
    )
    for group_name, keys in GROUPS:
        lines = []
        for k in keys:
            s = SYSTEMS[k]
            short_desc = s["description"][:55] + "…" if len(s["description"]) > 55 else s["description"]
            lines.append(f"{s['emoji']} **{s['label']}** — {short_desc}")
        embed.add_field(name=group_name, value="\n".join(lines), inline=False)
    embed.set_footer(text="Fantoma Bot • Utilise le menu ci-dessous pour naviguer")
    return embed


def build_system_embed(key: str) -> discord.Embed:
    s = SYSTEMS[key]
    embed = discord.Embed(
        title=f"{s['emoji']} {s['label']}",
        description=s["description"],
        color=s["color"],
    )

    # Fonctionnement
    if "how_it_works" in s:
        embed.add_field(
            name="⚙️ Fonctionnement",
            value="\n".join(f"• {line}" for line in s["how_it_works"]),
            inline=False,
        )

    # Commandes (split si trop long)
    if "commands" in s:
        chunks: list[str] = []
        current = ""
        for cmd, desc in s["commands"]:
            line = f"`{cmd}` — {desc}\n"
            if len(current) + len(line) > 950:
                chunks.append(current.rstrip())
                current = line
            else:
                current += line
        if current:
            chunks.append(current.rstrip())

        for i, chunk in enumerate(chunks):
            field_name = "📋 Commandes" if i == 0 else "📋 Commandes (suite)"
            embed.add_field(name=field_name, value=chunk, inline=False)

    embed.set_footer(text="[ ] = optionnel  •  < > = requis  •  [Admin] = Administrateur  •  [Mod] = Modérateur")
    return embed


# ══════════════════════════════════════════════════════════════════════════════
# VIEWS PERSISTANTES
# ══════════════════════════════════════════════════════════════════════════════

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=v["label"],
                value=k,
                description=(v["description"][:97] + "…" if len(v["description"]) > 97 else v["description"]),
                emoji=v["emoji"],
            )
            for k, v in SYSTEMS.items()
        ]
        super().__init__(
            placeholder="📂  Choisir un système…",
            options=options,
            custom_id="fantoma_help_select_v5",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        embed = build_system_embed(self.values[0])
        await interaction.response.edit_message(embed=embed, view=self.view)


class HomeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Accueil",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id="fantoma_help_home_v5",
        )

    async def callback(self, interaction: discord.Interaction):
        embed = build_home_embed()
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HelpSelect())
        self.add_item(HomeButton())


# ══════════════════════════════════════════════════════════════════════════════
# COG
# ══════════════════════════════════════════════════════════════════════════════

class Help(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="help", aliases=["aide", "h"], description="Affiche l'aide complète du bot")
    async def help_command(self, ctx: commands.Context):
        """Affiche l'aide avec menu de navigation persistant."""
        embed = build_home_embed()
        view = HelpView()
        await ctx.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    # Enregistre la vue persistante → fonctionne après redémarrage
    bot.add_view(HelpView())
    await bot.add_cog(Help(bot))
