import os
import sqlite3
import asyncio
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ui import View
from dotenv import load_dotenv
from vinted import VintedSource

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
DB = os.getenv("DB_PATH", "vinted_sniper.db")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in .env")

# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

con = sqlite3.connect(DB)
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
    PRIMARY KEY(profile_id, item_id)
)
""")
con.commit()

# ------------------------------------------------------------
# DISCORD
# ------------------------------------------------------------

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
source = VintedSource()


def get_profile(name):
    return con.execute(
        """SELECT id,name,channel_id,query,sizes,max_price,condition,active
           FROM profiles WHERE name=?""",
        (name,)
    ).fetchone()


def profile_text(p):
    if not p:
        return "â Profil nicht gefunden."

    pid, name, ch, query, sizes, price, cond, active = p

    return (
        f"**{name}**\n"
        f"ð {query}\n"
        f"ð {sizes or 'alle'}\n"
        f"ð° bis {price if price is not None else 'â'} â¬\n"
        f"â¨ {cond or 'alle'}\n"
        f"{'ð¢ AKTIV' if active else 'â¸ï¸ PAUSIERT'}"
    )


async def send_alert(channel, item, profile_name, detected_at):
    embed = discord.Embed(
        title=f"ð¥ NEUER TREFFER â {profile_name}",
        description=item.get("title", "Vinted-Angebot"),
        url=item.get("url"),
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(
        name="ð° Preis",
        value=f"{item.get('price', 'â')} â¬",
        inline=True,
    )
    embed.add_field(
        name="ð GrÃ¶Ãe",
        value=str(item.get("size", "â")),
        inline=True,
    )
    embed.add_field(
        name="â¨ Zustand",
        value=str(item.get("condition", "â")),
        inline=True,
    )

    if item.get("brand"):
        embed.add_field(
            name="ð·ï¸ Marke",
            value=item["brand"],
            inline=True,
        )

    if item.get("image_url"):
        embed.set_image(url=item["image_url"])

    embed.set_footer(
        text=f"Erkannt nach {detected_at:.2f}s â¢ manuell kaufen"
    )

    await channel.send(embed=embed)


# ------------------------------------------------------------
# BUTTONS
#
# WICHTIG:
# Die Buttons haben feste custom_id-Werte pro Profil.
# Dadurch funktionieren auch bereits vorhandene Discord-
# Nachrichten nach einem Railway-Neustart weiter.
# ------------------------------------------------------------

class ControlView(View):
    def __init__(self, profile_name):
        super().__init__(timeout=None)
        self.profile_name = profile_name

        self.start_button = discord.ui.Button(
            label="â¶ï¸ Start",
            style=discord.ButtonStyle.success,
            custom_id=f"vinted:start:{profile_name}",
        )
        self.pause_button = discord.ui.Button(
            label="â¸ï¸ Pause",
            style=discord.ButtonStyle.secondary,
            custom_id=f"vinted:pause:{profile_name}",
        )
        self.status_button = discord.ui.Button(
            label="ð Status",
            style=discord.ButtonStyle.primary,
            custom_id=f"vinted:status:{profile_name}",
        )

        self.start_button.callback = self.start
        self.pause_button.callback = self.pause
        self.status_button.callback = self.status

        self.add_item(self.start_button)
        self.add_item(self.pause_button)
        self.add_item(self.status_button)

    async def start(self, interaction: discord.Interaction):
        # Discord SOFORT bestÃ¤tigen.
        await interaction.response.defer(ephemeral=True)

        try:
            con.execute(
                "UPDATE profiles SET active=1 WHERE name=?",
                (self.profile_name,),
            )
            con.commit()

            await interaction.followup.send(
                "ð¢ Suche gestartet.",
                ephemeral=True,
            )
        except Exception as e:
            print(f"[BUTTON START] {e}")
            await interaction.followup.send(
                f"â Fehler beim Start: {e}",
                ephemeral=True,
            )

    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            con.execute(
                "UPDATE profiles SET active=0 WHERE name=?",
                (self.profile_name,),
            )
            con.commit()

            await interaction.followup.send(
                "â¸ï¸ Suche pausiert.",
                ephemeral=True,
            )
        except Exception as e:
            print(f"[BUTTON PAUSE] {e}")
            await interaction.followup.send(
                f"â Fehler beim Pausieren: {e}",
                ephemeral=True,
            )

    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            p = get_profile(self.profile_name)

            await interaction.followup.send(
                profile_text(p),
                ephemeral=True,
            )
        except Exception as e:
            print(f"[BUTTON STATUS] {e}")
            await interaction.followup.send(
                f"â Fehler beim Status: {e}",
                ephemeral=True,
            )


# ------------------------------------------------------------
# COMMANDS
# ------------------------------------------------------------

@tree.command(
    name="setup",
    description="Vinted-Suchprofil erstellen",
)
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
    channel = channel or interaction.channel

    try:
        con.execute(
            """
            INSERT INTO profiles(
                name,channel_id,query,sizes,max_price,condition
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                name,
                channel.id,
                query,
                sizes,
                max_price,
                condition,
            ),
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

    except Exception as e:
        print(f"[SETUP] {e}")

        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"â Fehler: {e}",
                ephemeral=True,
            )


@tree.command(
    name="profiles",
    description="Suchprofile anzeigen",
)
async def profiles(interaction: discord.Interaction):
    rows = con.execute(
        """SELECT id,name,channel_id,query,sizes,max_price,condition,active
           FROM profiles"""
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "Noch keine Profile."
        )
        return

    await interaction.response.send_message(
        "\n\n".join(profile_text(r) for r in rows)
    )


@tree.command(
    name="pause",
    description="Profil pausieren",
)
async def pause(interaction: discord.Interaction, name: str):
    con.execute(
        "UPDATE profiles SET active=0 WHERE name=?",
        (name,),
    )
    con.commit()

    await interaction.response.send_message(
        "â¸ï¸ Pausiert."
    )


@tree.command(
    name="resume",
    description="Profil starten",
)
async def resume(interaction: discord.Interaction, name: str):
    con.execute(
        "UPDATE profiles SET active=1 WHERE name=?",
        (name,),
    )
    con.commit()

    await interaction.response.send_message(
        "ð¢ Gestartet."
    )


# ------------------------------------------------------------
# VINTED MONITOR
# ------------------------------------------------------------

async def check_profile(p):
    pid, name, ch_id, query, sizes, max_price, condition, _ = p

    try:
        start = time.monotonic()

        # Die Vinted-Suche bleibt async wie im bisherigen Code.
        items = await source.search_new(query)

        for item in items:
            iid = str(item["id"])

            already_seen = con.execute(
                """SELECT 1 FROM seen
                   WHERE profile_id=? AND item_id=?""",
                (pid, iid),
            ).fetchone()

            if already_seen:
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
        print(f"[{name}] {type(e).__name__}: {e}")


async def monitor():
    await client.wait_until_ready()

    while not client.is_closed():
        profiles = con.execute(
            """
            SELECT id,name,channel_id,query,sizes,max_price,condition,active
            FROM profiles
            WHERE active=1
            """
        ).fetchall()

        # Profile getrennt prÃ¼fen, damit ein langsames Profil
        # die anderen nicht unnÃ¶tig aufhÃ¤lt.
        if profiles:
            await asyncio.gather(
                *(check_profile(p) for p in profiles),
                return_exceptions=True,
            )

        await asyncio.sleep(POLL_SECONDS)


# ------------------------------------------------------------
# START / READY
# ------------------------------------------------------------

@client.event
async def on_ready():
    await tree.sync()

    # GANZ WICHTIG:
    # Alte Buttons aus bereits vorhandenen Discord-Nachrichten
    # nach einem Railway-Neustart wieder registrieren.
    if not getattr(client, "_views_registered", False):
        rows = con.execute(
            "SELECT name FROM profiles"
        ).fetchall()

        for (name,) in rows:
            try:
                client.add_view(
                    ControlView(name),
                )
                print(f"BUTTONS REGISTERED: {name}")
            except Exception as e:
                print(f"[VIEW {name}] {e}")

        client._views_registered = True

    print(f"ONLINE: {client.user}")

    if not getattr(client, "_monitor_started", False):
        client._monitor_started = True
        asyncio.create_task(monitor())
        print("MONITOR STARTED")


client.run(TOKEN)
