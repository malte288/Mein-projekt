import os, sqlite3, asyncio, time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ui import View, Modal, TextInput
from dotenv import load_dotenv

from vinted import VintedSource

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
DB = os.getenv("DB_PATH", "vinted_sniper.db")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in .env")

con = sqlite3.connect(DB, check_same_thread=False)
con.execute("""
CREATE TABLE IF NOT EXISTS profiles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    channel_id INTEGER,
    query TEXT,
    sizes TEXT,
    max_price REAL,
    condition TEXT,
    active INTEGER DEFAULT 1
)
""")
con.execute("""
CREATE TABLE IF NOT EXISTS seen(
    profile_id INTEGER,
    item_id TEXT,
    first_seen TEXT,
    PRIMARY KEY(profile_id,item_id)
)
""")
con.commit()

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
source = VintedSource()


def get_profile(name):
    return con.execute(
        "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
        "FROM profiles WHERE name=?",
        (name,),
    ).fetchone()


def profile_text(p):
    if not p:
        return "â Profil nicht gefunden."

    pid, name, ch, query, sizes, price, cond, active = p
    price_text = f"{price:g} â¬" if price is not None else "kein Limit"
    return (
        f"**{name}**\n"
        f"ð **Suche:** {query}\n"
        f"ð **GrÃ¶Ãe:** {sizes or 'alle'}\n"
        f"ð° **Preis:** bis {price_text}\n"
        f"â¨ **Zustand:** {cond or 'alle'}\n"
        f"{'ð¢ AKTIV' if active else 'â¸ï¸ PAUSIERT'}"
    )


def is_allowed(interaction):
    # Nur Mitglieder mit "Server verwalten" dÃ¼rfen die Suchparameter Ã¤ndern.
    return (
        interaction.guild is not None
        and interaction.user.guild_permissions.manage_guild
    )


class PriceModal(Modal, title="ð° Maximalpreis Ã¤ndern"):
    value = TextInput(
        label="Maximalpreis in â¬",
        placeholder="z. B. 50 oder leer fÃ¼r kein Limit",
        required=False,
        max_length=20,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
                ephemeral=True,
            )
            return

        raw = self.value.value.strip().replace(",", ".")
        try:
            price = None if not raw else float(raw)
            if price is not None and price < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "â Bitte einen gÃ¼ltigen Preis eingeben, z. B. `50`.",
                ephemeral=True,
            )
            return

        con.execute(
            "UPDATE profiles SET max_price=? WHERE name=?",
            (price, self.profile_name),
        )
        con.commit()

        await interaction.response.send_message(
            f"â Preis geÃ¤ndert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class SearchModal(Modal, title="ð Suchbegriff Ã¤ndern"):
    value = TextInput(
        label="Wonach soll gesucht werden?",
        placeholder="z. B. Ralph Lauren Polo",
        required=True,
        max_length=100,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
                ephemeral=True,
            )
            return

        query = self.value.value.strip()
        if not query:
            await interaction.response.send_message(
                "â Der Suchbegriff darf nicht leer sein.",
                ephemeral=True,
            )
            return

        con.execute(
            "UPDATE profiles SET query=? WHERE name=?",
            (query, self.profile_name),
        )
        con.commit()

        await interaction.response.send_message(
            f"â Suchbegriff geÃ¤ndert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class SizeModal(Modal, title="ð GrÃ¶Ãen Ã¤ndern"):
    value = TextInput(
        label="GrÃ¶Ãen",
        placeholder="z. B. M,L,XL oder 'alle'",
        required=True,
        max_length=100,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
                ephemeral=True,
            )
            return

        sizes = self.value.value.strip()
        if sizes.lower() == "alle":
            sizes = ""

        con.execute(
            "UPDATE profiles SET sizes=? WHERE name=?",
            (sizes, self.profile_name),
        )
        con.commit()

        await interaction.response.send_message(
            f"â GrÃ¶Ãen geÃ¤ndert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class ConditionModal(Modal, title="â¨ Zustand Ã¤ndern"):
    value = TextInput(
        label="Zustand",
        placeholder="z. B. Sehr gut, Gut oder 'alle'",
        required=True,
        max_length=100,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
                ephemeral=True,
            )
            return

        condition = self.value.value.strip()
        if condition.lower() == "alle":
            condition = ""

        con.execute(
            "UPDATE profiles SET condition=? WHERE name=?",
            (condition, self.profile_name),
        )
        con.commit()

        await interaction.response.send_message(
            f"â Zustand geÃ¤ndert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class ControlView(View):
    def __init__(self, profile_name):
        super().__init__(timeout=None)
        self.profile_name = profile_name

    async def check_permission(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="â¶ï¸ Start", style=discord.ButtonStyle.success, row=0)
    async def start(self, interaction, button):
        if not await self.check_permission(interaction):
            return

        con.execute(
            "UPDATE profiles SET active=1 WHERE name=?",
            (self.profile_name,),
        )
        con.commit()
        await interaction.response.send_message(
            "ð¢ Suche gestartet.",
            ephemeral=True,
        )

    @discord.ui.button(label="â¸ï¸ Pause", style=discord.ButtonStyle.secondary, row=0)
    async def pause(self, interaction, button):
        if not await self.check_permission(interaction):
            return

        con.execute(
            "UPDATE profiles SET active=0 WHERE name=?",
            (self.profile_name,),
        )
        con.commit()
        await interaction.response.send_message(
            "â¸ï¸ Suche pausiert.",
            ephemeral=True,
        )

    @discord.ui.button(label="ð Status", style=discord.ButtonStyle.primary, row=0)
    async def status(self, interaction, button):
        await interaction.response.send_message(
            profile_text(get_profile(self.profile_name)),
            ephemeral=True,
        )

    @discord.ui.button(label="ð° Preis", style=discord.ButtonStyle.primary, row=1)
    async def price(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(PriceModal(self.profile_name))

    @discord.ui.button(label="ð Suche", style=discord.ButtonStyle.primary, row=1)
    async def search(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(SearchModal(self.profile_name))

    @discord.ui.button(label="ð GrÃ¶Ãe", style=discord.ButtonStyle.primary, row=1)
    async def size(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(SizeModal(self.profile_name))

    @discord.ui.button(label="â¨ Zustand", style=discord.ButtonStyle.primary, row=1)
    async def condition(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(ConditionModal(self.profile_name))


async def send_alert(channel, item, profile_name, detected_at):
    embed = discord.Embed(
        title=f"ð¯ NEUER TREFFER â {profile_name}",
        description=item.get("title", "Vinted-Angebot"),
        url=item.get("url"),
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(
        name="ð° PREIS",
        value=f"{item.get('price', 'â')} â¬",
        inline=True,
    )
    embed.add_field(
        name="ð GRÃSSE",
        value=str(item.get("size", "â")),
        inline=True,
    )
    embed.add_field(
        name="â¨ ZUSTAND",
        value=str(item.get("condition", "â")),
        inline=True,
    )

    if item.get("brand"):
        embed.add_field(
            name="ð·ï¸ MARKE",
            value=item["brand"],
            inline=True,
        )

    if item.get("image_url"):
        embed.set_image(url=item["image_url"])

    embed.set_footer(
        text=f"Erkannt nach {detected_at:.2f}s â¢ manuell kaufen"
    )

    await channel.send(embed=embed)


@tree.command(name="setup", description="Vinted-Suchprofil erstellen")
@app_commands.describe(
    name="Profilname",
    query="Suchbegriff",
    sizes="z.B. M,L",
    max_price="Maximalpreis",
    condition="z.B. Sehr gut",
    channel="Discord-Kanal fÃ¼r Treffer",
)
async def setup(
    interaction: discord.Interaction,
    name: str,
    query: str,
    sizes: str = "M,L",
    max_price: float | None = None,
    condition: str | None = None,
    channel: discord.TextChannel | None = None,
):
    if not is_allowed(interaction):
        await interaction.response.send_message(
            "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
            ephemeral=True,
        )
        return

    channel = channel or interaction.channel

    try:
        con.execute(
            """INSERT INTO profiles
               (name,channel_id,query,sizes,max_price,condition)
               VALUES(?,?,?,?,?,?)""",
            (name, channel.id, query, sizes, max_price, condition),
        )
        con.commit()

        await interaction.response.send_message(
            f"â Profil erstellt.\n\n{profile_text(get_profile(name))}",
            view=ControlView(name),
        )

    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "â Profil existiert bereits.",
            ephemeral=True,
        )


@tree.command(name="profiles", description="Suchprofile anzeigen")
async def profiles(interaction):
    rows = con.execute(
        "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
        "FROM profiles"
    ).fetchall()

    if not rows:
        await interaction.response.send_message("Noch keine Profile.")
        return

    await interaction.response.send_message(
        "\n\n".join(profile_text(r) for r in rows)
    )


@tree.command(name="pause", description="Profil pausieren")
async def pause(interaction, name: str):
    if not is_allowed(interaction):
        await interaction.response.send_message(
            "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
            ephemeral=True,
        )
        return

    con.execute(
        "UPDATE profiles SET active=0 WHERE name=?",
        (name,),
    )
    con.commit()
    await interaction.response.send_message("â¸ï¸ Pausiert.")


@tree.command(name="resume", description="Profil starten")
async def resume(interaction, name: str):
    if not is_allowed(interaction):
        await interaction.response.send_message(
            "â DafÃ¼r brauchst du die Berechtigung **Server verwalten**.",
            ephemeral=True,
        )
        return

    con.execute(
        "UPDATE profiles SET active=1 WHERE name=?",
        (name,),
    )
    con.commit()
    await interaction.response.send_message("ð¢ Gestartet.")


async def monitor():
    await client.wait_until_ready()

    while not client.is_closed():
        profiles = con.execute(
            "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
            "FROM profiles WHERE active=1"
        ).fetchall()

        for p in profiles:
            pid, name, ch_id, query, sizes, max_price, condition, _ = p

            try:
                start = time.monotonic()
                items = await source.search_new(query)

                for item in items:
                    iid = str(item["id"])

                    if con.execute(
                        "SELECT 1 FROM seen WHERE profile_id=? AND item_id=?",
                        (pid, iid),
                    ).fetchone():
                        continue

                    con.execute(
                        "INSERT INTO seen VALUES(?,?,?)",
                        (
                            pid,
                            iid,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    con.commit()

                    if source.matches(
                        item,
                        sizes,
                        max_price,
                        condition,
                    ):
                        ch = client.get_channel(ch_id)

                        if ch:
                            await send_alert(
                                ch,
                                item,
                                name,
                                time.monotonic() - start,
                            )

            except Exception as e:
                print(f"[{name}] {e}")

        await asyncio.sleep(POLL_SECONDS)


@client.event
async def on_ready():
    await tree.sync()
    print(f"ONLINE: {client.user}")

    if not getattr(client, "_monitor_started", False):
        client._monitor_started = True
        asyncio.create_task(monitor())


client.run(TOKEN)
