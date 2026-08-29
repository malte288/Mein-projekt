
import os
import sqlite3
import asyncio
import time
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
DELETE_AFTER_SECONDS = 20 * 60

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
    active INTEGER DEFAULT 1,
    control_message_id INTEGER
)
""")

# Add the new column when upgrading an existing database.
try:
    con.execute("ALTER TABLE profiles ADD COLUMN control_message_id INTEGER")
except sqlite3.OperationalError:
    pass

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
        "SELECT id,name,channel_id,query,sizes,max_price,condition,active,control_message_id "
        "FROM profiles WHERE name=?",
        (name,),
    ).fetchone()


def profile_text(p):
    if not p:
        return "Profil nicht gefunden."

    pid, name, ch, query, sizes, price, cond, active, control_id = p
    price_text = f"{price:g} EUR" if price is not None else "kein Limit"
    return (
        f"**{name}**\n"
        f"**SUCHE:** {query}\n"
        f"**GROESSE:** {sizes or 'alle'}\n"
        f"**PREIS:** bis {price_text}\n"
        f"**ZUSTAND:** {cond or 'alle'}\n"
        f"{'AKTIV' if active else 'PAUSIERT'}"
    )


def is_allowed(interaction):
    return (
        interaction.guild is not None
        and interaction.user.guild_permissions.manage_guild
    )


class PriceModal(Modal, title="Maximalpreis aendern"):
    value = TextInput(
        label="Maximalpreis in EUR",
        placeholder="z. B. 50 oder leer fuer kein Limit",
        required=False,
        max_length=20,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "Dafuer brauchst du die Berechtigung Server verwalten.",
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
                "Bitte einen gueltigen Preis eingeben, z. B. 50.",
                ephemeral=True,
            )
            return

        con.execute(
            "UPDATE profiles SET max_price=? WHERE name=?",
            (price, self.profile_name),
        )
        con.commit()

        await interaction.response.send_message(
            f"Preis geaendert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class SearchModal(Modal, title="Suchbegriff aendern"):
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
                "Dafuer brauchst du die Berechtigung Server verwalten.",
                ephemeral=True,
            )
            return

        query = self.value.value.strip()
        if not query:
            await interaction.response.send_message(
                "Der Suchbegriff darf nicht leer sein.",
                ephemeral=True,
            )
            return

        con.execute(
            "UPDATE profiles SET query=? WHERE name=?",
            (query, self.profile_name),
        )
        con.commit()

        await interaction.response.send_message(
            f"Suchbegriff geaendert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class SizeModal(Modal, title="Groessen aendern"):
    value = TextInput(
        label="Groessen",
        placeholder="z. B. M,L,XL oder alle",
        required=True,
        max_length=100,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "Dafuer brauchst du die Berechtigung Server verwalten.",
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
            f"Groessen geaendert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class ConditionModal(Modal, title="Zustand aendern"):
    value = TextInput(
        label="Zustand",
        placeholder="z. B. Sehr gut, Gut oder alle",
        required=True,
        max_length=100,
    )

    def __init__(self, profile_name):
        super().__init__()
        self.profile_name = profile_name

    async def on_submit(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "Dafuer brauchst du die Berechtigung Server verwalten.",
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
            f"Zustand geaendert.\n\n{profile_text(get_profile(self.profile_name))}",
            ephemeral=True,
        )


class ControlView(View):
    def __init__(self, profile_name):
        super().__init__(timeout=None)
        self.profile_name = profile_name

    async def check_permission(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "Dafuer brauchst du die Berechtigung Server verwalten.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="START", style=discord.ButtonStyle.success, row=0)
    async def start(self, interaction, button):
        if not await self.check_permission(interaction):
            return

        con.execute(
            "UPDATE profiles SET active=1 WHERE name=?",
            (self.profile_name,),
        )
        con.commit()
        await interaction.response.send_message(
            "Suche gestartet.",
            ephemeral=True,
        )

    @discord.ui.button(label="PAUSE", style=discord.ButtonStyle.secondary, row=0)
    async def pause(self, interaction, button):
        if not await self.check_permission(interaction):
            return

        con.execute(
            "UPDATE profiles SET active=0 WHERE name=?",
            (self.profile_name,),
        )
        con.commit()
        await interaction.response.send_message(
            "Suche pausiert.",
            ephemeral=True,
        )

    @discord.ui.button(label="STATUS", style=discord.ButtonStyle.primary, row=0)
    async def status(self, interaction, button):
        await interaction.response.send_message(
            profile_text(get_profile(self.profile_name)),
            ephemeral=True,
        )

    @discord.ui.button(label="PREIS", style=discord.ButtonStyle.primary, row=1)
    async def price(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(PriceModal(self.profile_name))

    @discord.ui.button(label="SUCHE", style=discord.ButtonStyle.primary, row=1)
    async def search(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(SearchModal(self.profile_name))

    @discord.ui.button(label="GROESSE", style=discord.ButtonStyle.primary, row=1)
    async def size(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(SizeModal(self.profile_name))

    @discord.ui.button(label="ZUSTAND", style=discord.ButtonStyle.primary, row=1)
    async def condition(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(ConditionModal(self.profile_name))


class ImageCarouselView(View):
    """Switches images inside the same Discord message."""

    def __init__(self, image_urls, embed_factory):
        super().__init__(timeout=DELETE_AFTER_SECONDS)
        self.image_urls = image_urls or []
        self.index = 0
        self.embed_factory = embed_factory

        self.previous_button.disabled = len(self.image_urls) <= 1
        self.next_button.disabled = len(self.image_urls) <= 1

        # Link button stays in the same message as the carousel.
        # It is added by send_alert when a valid Vinted URL exists.

    async def update_message(self, interaction):
        self.refresh_counter()
        embed = self.embed_factory(self.index)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary, row=0)
    async def previous_button(self, interaction, button):
        if not self.image_urls:
            await interaction.response.defer()
            return
        self.index = (self.index - 1) % len(self.image_urls)
        await self.update_message(interaction)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, row=0)
    async def counter_button(self, interaction, button):
        await interaction.response.defer()

    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction, button):
        if not self.image_urls:
            await interaction.response.defer()
            return
        self.index = (self.index + 1) % len(self.image_urls)
        await self.update_message(interaction)

    def refresh_counter(self):
        self.counter_button.label = (
            f"{self.index + 1} / {len(self.image_urls)}"
            if self.image_urls
            else "1 / 1"
        )


def build_alert_embed(item, profile_name, detected_at, image_index=0):
    embed = discord.Embed(
        title="NEUER TREFFER",
        description=item.get("title", "Vinted-Angebot"),
        url=item.get("url") or discord.Embed.Empty,
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(
        name="PREIS",
        value=f"{item.get('price', '--')} EUR",
        inline=False,
    )
    embed.add_field(
        name="GROESSE",
        value=str(item.get("size", "--")),
        inline=False,
    )
    embed.add_field(
        name="ZUSTAND",
        value=str(item.get("condition", "--")),
        inline=False,
    )

    if item.get("brand"):
        embed.add_field(
            name="MARKE",
            value=str(item["brand"]),
            inline=False,
        )

    image_urls = item.get("image_urls") or []
    if image_urls:
        index = max(0, min(image_index, len(image_urls) - 1))
        embed.set_image(url=image_urls[index])

    created_at = item.get("created_at_ts")
    if created_at:
        online_text = f"<t:{created_at}:R>"
    else:
        online_text = "nicht verfuegbar"

    detected_ts = int(detected_at.timestamp())

    embed.set_footer(
        text=(
            f"Online seit {online_text} | "
            f"Erkannt nach {item.get('detection_seconds', 0):.2f}s | "
            f"Erkannt <t:{detected_ts}:R>"
        )
    )

    return embed


async def move_control_to_bottom(profile_name):
    p = get_profile(profile_name)
    if not p:
        return

    _, name, ch_id, *_rest, control_message_id = p
    channel = client.get_channel(ch_id)
    if channel is None:
        return

    if control_message_id:
        try:
            old_message = await channel.fetch_message(control_message_id)
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    message = await channel.send(
        profile_text(get_profile(profile_name)),
        view=ControlView(profile_name),
    )

    con.execute(
        "UPDATE profiles SET control_message_id=? WHERE name=?",
        (message.id, profile_name),
    )
    con.commit()


async def send_alert(channel, item, profile_name, detected_at):
    image_urls = item.get("image_urls") or []
    view = ImageCarouselView(
        image_urls=image_urls,
        embed_factory=lambda index: build_alert_embed(
            item,
            profile_name,
            detected_at,
            index,
        ),
    )
    view.refresh_counter()

    if item.get("url"):
        view.add_item(
            discord.ui.Button(
                label="VINTED OEFFNEN",
                style=discord.ButtonStyle.link,
                url=item["url"],
                row=1,
            )
        )

    message = await channel.send(
        embed=build_alert_embed(
            item,
            profile_name,
            detected_at,
            0,
        ),
        view=view,
        delete_after=DELETE_AFTER_SECONDS,
    )

    return message


@tree.command(name="setup", description="Vinted-Suchprofil erstellen")
@app_commands.describe(
    name="Profilname",
    query="Suchbegriff",
    sizes="z.B. M,L",
    max_price="Maximalpreis",
    condition="z.B. Sehr gut",
    channel="Discord-Kanal fuer Treffer",
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
            "Dafuer brauchst du die Berechtigung Server verwalten.",
            ephemeral=True,
        )
        return

    channel = channel or interaction.channel

    try:
        con.execute(
            """INSERT INTO profiles
               (name,channel_id,query,sizes,max_price,condition,active)
               VALUES(?,?,?,?,?,?,1)""",
            (name, channel.id, query, sizes, max_price, condition),
        )
        con.commit()

        await interaction.response.send_message(
            "Profil erstellt.",
            ephemeral=True,
        )

        await move_control_to_bottom(name)

    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "Profil existiert bereits.",
            ephemeral=True,
        )


@tree.command(name="profiles", description="Suchprofile anzeigen")
async def profiles(interaction):
    rows = con.execute(
        "SELECT id,name,channel_id,query,sizes,max_price,condition,active,control_message_id "
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
            "Dafuer brauchst du die Berechtigung Server verwalten.",
            ephemeral=True,
        )
        return

    con.execute(
        "UPDATE profiles SET active=0 WHERE name=?",
        (name,),
    )
    con.commit()
    await interaction.response.send_message("Pausiert.")


@tree.command(name="resume", description="Profil starten")
async def resume(interaction, name: str):
    if not is_allowed(interaction):
        await interaction.response.send_message(
            "Dafuer brauchst du die Berechtigung Server verwalten.",
            ephemeral=True,
        )
        return

    con.execute(
        "UPDATE profiles SET active=1 WHERE name=?",
        (name,),
    )
    con.commit()
    await interaction.response.send_message("Gestartet.")


async def monitor():
    await client.wait_until_ready()

    while not client.is_closed():
        profiles_rows = con.execute(
            "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
            "FROM profiles WHERE active=1"
        ).fetchall()

        for p in profiles_rows:
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

                    if not source.matches(
                        item,
                        sizes,
                        max_price,
                        condition,
                    ):
                        continue

                    # Fetch full details/photos only after the item matches.
                    try:
                        item = await source.enrich_item(item)
                    except Exception as detail_error:
                        print(f"[{name}] Detail-Fallback {iid}: {detail_error}")

                    ch = client.get_channel(ch_id)
                    if ch:
                        detected_at = datetime.now(timezone.utc)
                        item["detection_seconds"] = time.monotonic() - start

                        await send_alert(
                            ch,
                            item,
                            name,
                            detected_at,
                        )

                        # Keep the control module directly below the newest offer.
                        await move_control_to_bottom(name)

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
