# cogs/games.py
# Mini-jeux interactifs pour le serveur Fantoma.

import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from config.settings import settings
from utils.logger import setup_console_logger

logger = setup_console_logger("cogs.games")


# ──────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────

PFC_EMOJIS = {"pierre": "🪨", "feuille": "📄", "ciseaux": "✂️"}
PFC_WINS   = {"pierre": "ciseaux", "feuille": "pierre", "ciseaux": "feuille"}

EIGHT_BALL_RESPONSES = [
    # Positives
    ("C'est certain !", settings.COLOR_SUCCESS),
    ("Sans aucun doute !", settings.COLOR_SUCCESS),
    ("Oui, absolument !", settings.COLOR_SUCCESS),
    ("Tu peux compter dessus.", settings.COLOR_SUCCESS),
    ("Très probablement.", settings.COLOR_SUCCESS),
    ("Les signes pointent vers oui.", settings.COLOR_SUCCESS),
    ("Oui.", settings.COLOR_SUCCESS),
    # Neutres
    ("La réponse est floue, réessaie.", settings.COLOR_WARNING),
    ("Pose la question plus tard.", settings.COLOR_WARNING),
    ("Mieux vaut ne pas te le dire maintenant.", settings.COLOR_WARNING),
    ("Je ne peux pas prédire ça.", settings.COLOR_WARNING),
    ("Concentre-toi et redemande.", settings.COLOR_WARNING),
    # Négatives
    ("N'y compte pas trop.", settings.COLOR_ERROR),
    ("Ma réponse est non.", settings.COLOR_ERROR),
    ("Mes sources disent non.", settings.COLOR_ERROR),
    ("Les perspectives ne sont pas bonnes.", settings.COLOR_ERROR),
    ("Très peu probable.", settings.COLOR_ERROR),
]


# ──────────────────────────────────────────────
# Vue PFC — Boutons interactifs
# ──────────────────────────────────────────────

class PFCView(discord.ui.View):
    """Vue pour le jeu Pierre-Feuille-Ciseaux."""

    def __init__(self, challenger: discord.Member, opponent: discord.Member | None, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.challenger      = challenger
        self.opponent        = opponent
        self.challenger_choice: str | None = None
        self.opponent_choice:   str | None = None
        self.message: discord.Message | None = None

    async def _process_choice(self, interaction: discord.Interaction, choice: str) -> None:
        """Traite le choix d'un joueur."""
        user = interaction.user

        # Vérifie que c'est bien un des joueurs
        if user != self.challenger and (self.opponent and user != self.opponent):
            await interaction.response.send_message(
                "❌ Tu ne fais pas partie de cette partie !", ephemeral=True
            )
            return

        # Enregistre le choix
        if user == self.challenger and not self.challenger_choice:
            self.challenger_choice = choice
            await interaction.response.send_message(
                f"✅ Tu as choisi **{PFC_EMOJIS[choice]} {choice}** !", ephemeral=True
            )
        elif self.opponent and user == self.opponent and not self.opponent_choice:
            self.opponent_choice = choice
            await interaction.response.send_message(
                f"✅ Tu as choisi **{PFC_EMOJIS[choice]} {choice}** !", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "ℹ️ Tu as déjà fait ton choix !", ephemeral=True
            )
            return

        # Si c'est contre le bot
        if not self.opponent:
            self.opponent_choice = random.choice(list(PFC_EMOJIS.keys()))
            await self._show_result(interaction)
            return

        # Si les deux joueurs ont choisi
        if self.challenger_choice and self.opponent_choice:
            await self._show_result(interaction)

    async def _show_result(self, interaction: discord.Interaction) -> None:
        """Affiche le résultat de la partie."""
        self.stop()

        c_choice = self.challenger_choice
        o_choice = self.opponent_choice
        opponent_name = self.opponent.display_name if self.opponent else "Fantoma Bot"

        # Détermine le gagnant
        if c_choice == o_choice:
            result     = "🤝 **Égalité !**"
            color      = settings.COLOR_WARNING
            winner_txt = "Personne ne gagne !"
        elif PFC_WINS[c_choice] == o_choice:
            result     = f"🏆 **{self.challenger.display_name} gagne !**"
            color      = settings.COLOR_SUCCESS
            winner_txt = f"{self.challenger.mention} remporte la partie !"
        else:
            result     = f"🏆 **{opponent_name} gagne !**"
            color      = settings.COLOR_ERROR
            winner_txt = f"{'Le bot' if not self.opponent else self.opponent.mention} remporte la partie !"

        embed = discord.Embed(
            title="🪨📄✂️ Pierre-Feuille-Ciseaux — Résultat",
            description=result,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name=f"{self.challenger.display_name}",
            value=f"{PFC_EMOJIS[c_choice]} **{c_choice}**",
            inline=True
        )
        embed.add_field(name="VS", value="⚔️", inline=True)
        embed.add_field(
            name=opponent_name,
            value=f"{PFC_EMOJIS[o_choice]} **{o_choice}**",
            inline=True
        )
        embed.add_field(name="🎯 Verdict", value=winner_txt, inline=False)
        embed.set_footer(text="Fantoma • Mini-jeux")

        # Désactive les boutons
        for item in self.children:
            item.disabled = True

        if self.message:
            await self.message.edit(embed=embed, view=self)

    async def on_timeout(self) -> None:
        if self.message:
            embed = discord.Embed(
                title="⏰ Temps écoulé !",
                description="La partie a expiré car un joueur n'a pas répondu.",
                color=settings.COLOR_ERROR
            )
            for item in self.children:
                item.disabled = True
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(label="🪨 Pierre",   style=discord.ButtonStyle.secondary, custom_id="pfc:pierre")
    async def pierre(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._process_choice(interaction, "pierre")

    @discord.ui.button(label="📄 Feuille",  style=discord.ButtonStyle.secondary, custom_id="pfc:feuille")
    async def feuille(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._process_choice(interaction, "feuille")

    @discord.ui.button(label="✂️ Ciseaux", style=discord.ButtonStyle.secondary, custom_id="pfc:ciseaux")
    async def ciseaux(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._process_choice(interaction, "ciseaux")


# ──────────────────────────────────────────────
# Vue Devine un nombre
# ──────────────────────────────────────────────

class GuessModal(discord.ui.Modal, title="Devine le nombre !"):
    """Modal pour entrer une réponse au jeu Devine un nombre."""

    guess = discord.ui.TextInput(
        label="Ton nombre",
        placeholder="Entre un nombre...",
        min_length=1,
        max_length=4
    )

    def __init__(self, secret: int, min_val: int, max_val: int, attempts: list, message: discord.Message):
        super().__init__()
        self.secret   = secret
        self.min_val  = min_val
        self.max_val  = max_val
        self.attempts = attempts
        self.message  = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            number = int(self.guess.value)
        except ValueError:
            await interaction.response.send_message(
                "❌ Entre un nombre valide !", ephemeral=True
            )
            return

        self.attempts.append(number)
        nb_attempts = len(self.attempts)

        if number == self.secret:
            embed = discord.Embed(
                title="🎉 Bravo !",
                description=(
                    f"Tu as trouvé le nombre **{self.secret}** "
                    f"en **{nb_attempts}** essai(s) ! 🏆"
                ),
                color=settings.COLOR_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Fantoma • Mini-jeux")
            await interaction.response.edit_message(embed=embed, view=None)
            return

        hint = "📈 **Trop petit !**" if number < self.secret else "📉 **Trop grand !**"

        embed = discord.Embed(
            title="🔢 Devine le nombre !",
            description=(
                f"Entre `{self.min_val}` et `{self.max_val}`\n\n"
                f"{hint}\n"
                f"Essais : `{nb_attempts}` | Dernière tentative : `{number}`"
            ),
            color=settings.COLOR_WARNING,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Fantoma • Mini-jeux")

        view = GuessView(self.secret, self.min_val, self.max_val, self.attempts, self.message)
        await interaction.response.edit_message(embed=embed, view=view)


class GuessView(discord.ui.View):
    """Vue pour le jeu Devine un nombre."""

    def __init__(self, secret: int, min_val: int, max_val: int, attempts: list, message: discord.Message = None):
        super().__init__(timeout=120)
        self.secret   = secret
        self.min_val  = min_val
        self.max_val  = max_val
        self.attempts = attempts
        self.message  = message

    @discord.ui.button(label="🎯 Proposer un nombre", style=discord.ButtonStyle.primary)
    async def guess_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            GuessModal(self.secret, self.min_val, self.max_val, self.attempts, self.message)
        )

    @discord.ui.button(label="🏳️ Abandonner", style=discord.ButtonStyle.danger)
    async def abandon_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="🏳️ Partie abandonnée",
            description=f"Le nombre était **{self.secret}**. Dommage !",
            color=settings.COLOR_ERROR
        )
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class GamesCog(commands.Cog, name="Mini-jeux"):
    """Cog contenant les mini-jeux du serveur."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────
    # /dé & &dé
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="de", aliases=["dé", "dice", "roll"], description="Lance un ou plusieurs dés.")
    @commands.guild_only()
    @app_commands.describe(
        faces="Nombre de faces du dé (défaut : 6)",
        nombre="Nombre de dés à lancer (défaut : 1)"
    )
    async def de(
        self,
        ctx: commands.Context,
        faces:  int = 6,
        nombre: int = 1
    ) -> None:
        if not 2 <= faces <= 1000:
            await ctx.send("❌ Le dé doit avoir entre **2 et 1000 faces**.", ephemeral=True)
            return
        if not 1 <= nombre <= 20:
            await ctx.send("❌ Tu peux lancer entre **1 et 20 dés**.", ephemeral=True)
            return

        results = [random.randint(1, faces) for _ in range(nombre)]
        total   = sum(results)

        embed = discord.Embed(
            title=f"🎲 Lancer de dé{'s' if nombre > 1 else ''}",
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )

        if nombre == 1:
            embed.description = f"# **{results[0]}**\nsur un d{faces}"
        else:
            results_str = " • ".join(f"`{r}`" for r in results)
            embed.description = f"{results_str}"
            embed.add_field(name="🎯 Total",   value=f"**{total}**",            inline=True)
            embed.add_field(name="📊 Moyenne", value=f"**{total/nombre:.1f}**", inline=True)
            embed.add_field(name="🎲 Dés",     value=f"`{nombre}d{faces}`",     inline=True)

        embed.set_footer(text=f"{ctx.author.display_name} • Fantoma Mini-jeux")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────
    # /pfc & &pfc
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="pfc", description="Joue à Pierre-Feuille-Ciseaux.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre adversaire (optionnel — joue contre le bot si absent)")
    async def pfc(
        self,
        ctx:    commands.Context,
        membre: discord.Member | None = None
    ) -> None:
        # Vérifie que le membre n'est pas un bot
        if membre and membre.bot:
            await ctx.send("❌ Tu ne peux pas défier un bot !", ephemeral=True)
            return
        if membre and membre == ctx.author:
            await ctx.send("❌ Tu ne peux pas jouer contre toi-même !", ephemeral=True)
            return

        view = PFCView(challenger=ctx.author, opponent=membre)

        opponent_name = membre.mention if membre else "**Fantoma Bot** 🤖"
        embed = discord.Embed(
            title="🪨📄✂️ Pierre-Feuille-Ciseaux",
            description=(
                f"**{ctx.author.mention}** défie {opponent_name} !\n\n"
                f"{'Les deux joueurs choisissent en privé.' if membre else 'Choisis ton arme !'}\n"
                f"⏰ Temps limite : **30 secondes**"
            ),
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Fantoma • Mini-jeux")

        msg        = await ctx.send(embed=embed, view=view)
        view.message = msg

    # ──────────────────────────────────────────────
    # /coinflip & &coinflip
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="coinflip", aliases=["cf", "pile", "face"], description="Lance une pièce — Pile ou Face.")
    @commands.guild_only()
    async def coinflip(self, ctx: commands.Context) -> None:
        result = random.choice(["Pile", "Face"])
        emoji  = "🪙" if result == "Pile" else "🟡"
        color  = settings.COLOR_PRIMARY

        embed = discord.Embed(
            title="🪙 Pile ou Face !",
            description=f"# {emoji} **{result} !**",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"{ctx.author.display_name} • Fantoma Mini-jeux")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────
    # /8ball & &8ball
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="8ball", aliases=["boule", "magic"], description="Pose une question à la boule magique.")
    @commands.guild_only()
    @app_commands.describe(question="Ta question pour la boule magique")
    async def eight_ball(self, ctx: commands.Context, *, question: str) -> None:
        response, color = random.choice(EIGHT_BALL_RESPONSES)

        embed = discord.Embed(
            title="🎱 Boule Magique",
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="❓ Question", value=f"*{question}*",  inline=False)
        embed.add_field(name="🎱 Réponse",  value=f"**{response}**", inline=False)
        embed.set_footer(text=f"{ctx.author.display_name} • Fantoma Mini-jeux")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────
    # /chiffre & &chiffre
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="chiffre", aliases=["guess", "devine"], description="Devine un nombre mystère.")
    @commands.guild_only()
    @app_commands.describe(
        minimum="Valeur minimale (défaut : 1)",
        maximum="Valeur maximale (défaut : 100)"
    )
    async def chiffre(
        self,
        ctx:     commands.Context,
        minimum: int = 1,
        maximum: int = 100
    ) -> None:
        if minimum >= maximum:
            await ctx.send("❌ Le minimum doit être inférieur au maximum.", ephemeral=True)
            return
        if maximum - minimum > 10000:
            await ctx.send("❌ L'intervalle ne peut pas dépasser 10 000.", ephemeral=True)
            return

        secret   = random.randint(minimum, maximum)
        attempts = []

        embed = discord.Embed(
            title="🔢 Devine le nombre !",
            description=(
                f"J'ai choisi un nombre entre `{minimum}` et `{maximum}`.\n"
                f"Tu as autant d'essais que tu veux — bonne chance ! 🎯"
            ),
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Fantoma • Mini-jeux")

        view = GuessView(secret, minimum, maximum, attempts)
        msg  = await ctx.send(embed=embed, view=view)
        view.message = msg

    # ──────────────────────────────────────────────
    # /roulette & &roulette
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="roulette", description="Joue à la roulette russe. 🔫")
    @commands.guild_only()
    async def roulette(self, ctx: commands.Context) -> None:
        # 1 chance sur 6
        survived = random.randint(1, 6) != 1

        if survived:
            embed = discord.Embed(
                title="🔫 Roulette Russe",
                description=(
                    f"*{ctx.author.mention} appuie sur la gâchette...*\n\n"
                    f"**CLIC !** 😮‍💨\n"
                    f"Tu as survécu ! Cette fois-ci... 😅"
                ),
                color=settings.COLOR_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
        else:
            embed = discord.Embed(
                title="🔫 Roulette Russe",
                description=(
                    f"*{ctx.author.mention} appuie sur la gâchette...*\n\n"
                    f"**BANG ! 💥**\n"
                    f"Tu n'as pas eu de chance... RIP 😵"
                ),
                color=settings.COLOR_ERROR,
                timestamp=datetime.now(timezone.utc)
            )

        embed.set_footer(text="Fantoma • Mini-jeux — C'est pour rire !")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────
    # /choix & &choix
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="choix", aliases=["choose", "choisir"], description="Choisit aléatoirement parmi plusieurs options.")
    @commands.guild_only()
    @app_commands.describe(options="Options séparées par des virgules (ex: pizza, sushi, burger)")
    async def choix(self, ctx: commands.Context, *, options: str) -> None:
        choices = [o.strip() for o in options.split(",") if o.strip()]

        if len(choices) < 2:
            await ctx.send(
                "❌ Donne au moins **2 options** séparées par des virgules.\n"
                "Ex: `&choix pizza, sushi, burger`",
                ephemeral=True
            )
            return
        if len(choices) > 20:
            await ctx.send("❌ Maximum **20 options** à la fois.", ephemeral=True)
            return

        winner = random.choice(choices)

        embed = discord.Embed(
            title="🎯 Le bot a choisi !",
            color=settings.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="🗳️ Options",
            value="\n".join(f"{'➡️' if c == winner else '▪️'} {c}" for c in choices),
            inline=False
        )
        embed.add_field(name="✅ Choix", value=f"**{winner}**", inline=False)
        embed.set_footer(text=f"{ctx.author.display_name} • Fantoma Mini-jeux")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────
    # /tictactoe & &tictactoe
    # ──────────────────────────────────────────────

    @commands.hybrid_command(name="tictactoe", aliases=["ttt", "morpion"], description="Joue au morpion contre un membre.")
    @commands.guild_only()
    @app_commands.describe(membre="Membre adversaire")
    async def tictactoe(self, ctx: commands.Context, membre: discord.Member) -> None:
        if membre.bot or membre == ctx.author:
            await ctx.send("❌ Choisis un vrai membre différent de toi !", ephemeral=True)
            return

        view = TicTacToeView(ctx.author, membre)
        embed = discord.Embed(
            title="⭕❌ Morpion",
            description=(
                f"**{ctx.author.mention}** (❌) vs **{membre.mention}** (⭕)\n\n"
                f"C'est au tour de **{ctx.author.display_name}** ❌"
            ),
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Fantoma • Mini-jeux")
        msg        = await ctx.send(embed=embed, view=view)
        view.message = msg


# ──────────────────────────────────────────────
# Morpion (TicTacToe)
# ──────────────────────────────────────────────

class TicTacToeButton(discord.ui.Button):
    def __init__(self, row: int, col: int):
        super().__init__(
            label="‎",  # Caractère invisible
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=f"ttt:{row}:{col}"
        )
        self.row_pos = row
        self.col_pos = col

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TicTacToeView = self.view

        if interaction.user != view.current_player:
            await interaction.response.send_message(
                f"❌ C'est au tour de **{view.current_player.display_name}** !",
                ephemeral=True
            )
            return

        # Place le symbole
        symbol = "❌" if view.current_player == view.player1 else "⭕"
        self.label    = symbol
        self.disabled = True
        self.style    = discord.ButtonStyle.danger if symbol == "❌" else discord.ButtonStyle.success

        view.board[self.row_pos][self.col_pos] = symbol

        # Vérifie si quelqu'un a gagné
        winner = view.check_winner()

        if winner:
            embed = discord.Embed(
                title="⭕❌ Morpion — Résultat",
                description=f"🏆 **{view.current_player.mention} gagne !** Félicitations !",
                color=settings.COLOR_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Fantoma • Mini-jeux")
            for item in view.children:
                item.disabled = True
            view.stop()
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # Vérifie si c'est un match nul
        if all(view.board[r][c] != "" for r in range(3) for c in range(3)):
            embed = discord.Embed(
                title="⭕❌ Morpion — Match nul !",
                description="🤝 Égalité ! Bien joué à tous les deux.",
                color=settings.COLOR_WARNING,
                timestamp=datetime.now(timezone.utc)
            )
            view.stop()
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # Change de joueur
        view.current_player = (
            view.player2 if view.current_player == view.player1 else view.player1
        )
        next_symbol = "❌" if view.current_player == view.player1 else "⭕"

        embed = discord.Embed(
            title="⭕❌ Morpion",
            description=(
                f"**{view.player1.mention}** (❌) vs **{view.player2.mention}** (⭕)\n\n"
                f"C'est au tour de **{view.current_player.display_name}** {next_symbol}"
            ),
            color=settings.COLOR_PRIMARY,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Fantoma • Mini-jeux")
        await interaction.response.edit_message(embed=embed, view=view)


class TicTacToeView(discord.ui.View):
    def __init__(self, player1: discord.Member, player2: discord.Member):
        super().__init__(timeout=120)
        self.player1        = player1
        self.player2        = player2
        self.current_player = player1
        self.board          = [[""] * 3 for _ in range(3)]
        self.message: discord.Message | None = None

        # Crée la grille 3x3
        for row in range(3):
            for col in range(3):
                self.add_item(TicTacToeButton(row, col))

    def check_winner(self) -> bool:
        b = self.board
        # Lignes et colonnes
        for i in range(3):
            if b[i][0] == b[i][1] == b[i][2] != "":
                return True
            if b[0][i] == b[1][i] == b[2][i] != "":
                return True
        # Diagonales
        if b[0][0] == b[1][1] == b[2][2] != "":
            return True
        if b[0][2] == b[1][1] == b[2][0] != "":
            return True
        return False

    async def on_timeout(self) -> None:
        if self.message:
            embed = discord.Embed(
                title="⏰ Partie expirée",
                description="La partie a expiré faute d'activité.",
                color=settings.COLOR_ERROR
            )
            for item in self.children:
                item.disabled = True
            await self.message.edit(embed=embed, view=self)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamesCog(bot))
    logger.info("Cog 'Mini-jeux' chargé.")