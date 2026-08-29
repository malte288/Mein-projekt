import os
import sqlite3
import asyncio
import time
from datetime import datetime, timezone
from io import BytesIO

import aiohttp
import discord
from discord import app_commands
from discord.ui import View, Modal, TextInput
from dotenv import load_dotenv

from vinted import VintedSource


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
DB = os.getenv("DB_PATH", "vinted_sniper.db")

# Treffer-Nachrichten nach 20 Minuten loeschen
DELETE_AFTER = 20 * 60

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in .env")


# ============================================================
# DATABASE
# ============================================================

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


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
source = VintedSource()

# Aktuelle Steuerungsnachricht pro Profil
control_messages = {}


# ============================================================
# PROFILE
# ============================================================

def get_profile(name):
    return con.execute(
        "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
        "FROM profiles WHERE name=?",
        (name,),
    ).fetchone()


def profile_text(p):
    if not p:
        return "Profil nicht gefunden."

    pid, name, ch, query, sizes, price, cond, active = p

    price_text = f"{price:g} EUR" if price is not None else "kein Limit"

    return (
        f"**{name}**\n"
        f"SUCHBEGRIFF: **{query}**\n"
        f"GROESSE: **{sizes or 'alle'}**\n"
        f"PREIS: **bis {price_text}**\n"
        f"ZUSTAND: **{cond or 'alle'}**\n"
        f"STATUS: **{'AKTIV' if active else 'PAUSIERT'}**"
    )


def is_allowed(interaction):
    return (
        interaction.guild is not None
        and interaction.user.guild_permissions.manage_guild
    )


# ============================================================
# MODALS
# ============================================================

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
                "Dafuer brauchst du die Berechtigung **Server verwalten**.",
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
                "Bitte einen gueltigen Preis eingeben, z. B. `50`.",
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
                "Dafuer brauchst du die Berechtigung **Server verwalten**.",
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
                "Dafuer brauchst du die Berechtigung **Server verwalten**.",
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
                "Dafuer brauchst du die Berechtigung **Server verwalten**.",
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


# ============================================================
# CONTROL PANEL
# ============================================================

class ControlView(View):
    def __init__(self, profile_name):
        super().__init__(timeout=None)
        self.profile_name = profile_name

    async def check_permission(self, interaction):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "Dafuer brauchst du die Berechtigung **Server verwalten**.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, row=0)
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

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, row=0)
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

    @discord.ui.button(label="Status", style=discord.ButtonStyle.primary, row=0)
    async def status(self, interaction, button):
        await interaction.response.send_message(
            profile_text(get_profile(self.profile_name)),
            ephemeral=True,
        )

    @discord.ui.button(label="Preis", style=discord.ButtonStyle.primary, row=1)
    async def price(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(PriceModal(self.profile_name))

    @discord.ui.button(label="Suche", style=discord.ButtonStyle.primary, row=1)
    async def search(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(SearchModal(self.profile_name))

    @discord.ui.button(label="Groesse", style=discord.ButtonStyle.primary, row=1)
    async def size(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(SizeModal(self.profile_name))

    @discord.ui.button(label="Zustand", style=discord.ButtonStyle.primary, row=1)
    async def condition(self, interaction, button):
        if not await self.check_permission(interaction):
            return
        await interaction.response.send_modal(ConditionModal(self.profile_name))


def create_control_embed(profile_name):
    embed = discord.Embed(
        title="VINTED SNIPER",
        description=profile_text(get_profile(profile_name)),
        color=discord.Color.blurple(),
    )

    embed.set_footer(
        text="Einstellungen direkt ueber die Buttons aendern"
    )

    return embed


async def refresh_control_panel(channel, profile_name):
    old_message = control_messages.get(profile_name)

    if old_message:
        try:
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass
        except Exception:
            pass

    message = await channel.send(
        embed=create_control_embed(profile_name),
        view=ControlView(profile_name),
    )

    control_messages[profile_name] = message


# ============================================================
# VINTED IMAGE GALLERY
# ============================================================

def get_image_urls(item):
    """
    Unterstuetzt mehrere moegliche Felder aus verschiedenen
    Vinted-Quellen.

    Wenn VintedSource bereits mehrere Bilder liefert, werden
    diese hier automatisch als eine Discord-Galerie verwendet.
    """

    urls = []

    # Mehrere Bilder
    for key in (
        "image_urls",
        "images",
        "photos",
        "photo_urls",
        "pictures",
    ):
        value = item.get(key)

        if isinstance(value, (list, tuple)):
            for entry in value:
                if isinstance(entry, str):
                    url = entry
                elif isinstance(entry, dict):
                    url = (
                        entry.get("url")
                        or entry.get("image_url")
                        or entry.get("photo_url")
                        or entry.get("full_size_url")
                    )
                else:
                    url = None

                if url:
                    urls.append(url)

    # Einzelbild als Fallback
    for key in (
        "image_url",
        "photo_url",
        "photo",
        "image",
        "thumbnail",
    ):
        value = item.get(key)

        if isinstance(value, str) and value:
            urls.append(value)

    # Doppelte entfernen, Reihenfolge behalten
    result = []
    seen = set()

    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)

    # Discord erlaubt max. 10 Attachments pro Nachricht
    return result[:10]


async def download_images(urls):
    """
    Laedt die Bilder herunter, damit Discord sie als eine
    gemeinsame Galerie in EINER Nachricht anzeigen kann.
    """

    if not urls:
        return []

    results = []

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def download_one(url, index):
            try:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/131.0 Safari/537.36"
                    )
                }

                async with session.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                ) as response:

                    if response.status != 200:
                        return None

                    data = await response.read()

                    if not data:
                        return None

                    # Max. 8 MB pro Attachment, damit wir nicht
                    # unnoetig grosse Discord-Nachrichten bauen.
                    if len(data) > 8 * 1024 * 1024:
                        return None

                    filename = f"vinted_{index + 1}.jpg"

                    return discord.File(
                        BytesIO(data),
                        filename=filename,
                    )

            except Exception as e:
                print(f"Bild {index + 1} konnte nicht geladen werden: {e}")
                return None

        downloaded = await asyncio.gather(
            *[
                download_one(url, index)
                for index, url in enumerate(urls)
            ]
        )

        for file in downloaded:
            if file is not None:
                results.append(file)

    return results


# ============================================================
# VINTED LINK
# ============================================================

class ListingView(View):
    def __init__(self, url):
        super().__init__(timeout=None)

        if url:
            self.add_item(
                discord.ui.Button(
                    label="Auf Vinted oeffnen",
                    url=url,
                    style=discord.ButtonStyle.link,
                )
            )


# ============================================================
# AUTO DELETE
# ============================================================

async def delete_after_delay(message):
    await asyncio.sleep(DELETE_AFTER)

    try:
        await message.delete()

    except discord.NotFound:
        pass

    except discord.Forbidden:
        print("Keine Berechtigung zum Loeschen der Nachricht.")

    except Exception as e:
        print(f"Fehler beim Loeschen: {e}")


# ============================================================
# SEND ALERT
# ============================================================

async def send_alert(
    channel,
    item,
    profile_name,
    detected_at,
):
    """
    EIN Treffer = EINE Discord-Nachricht.

    - Preis / Groesse / Zustand / Marke stehen vor den Bildern.
    - Alle gefundenen Bilder werden in dieselbe Nachricht geladen.
    - Die Nachricht wird nach 20 Minuten geloescht.
    - Der Footer zeigt die echte Discord-Anzeigezeit als laufenden
      Sekunden-Zaehler. Kein Vinted-Onlinezeitstempel wird verwendet.
    """

    url = item.get("url")

    title = str(item.get("title") or "Vinted Angebot")
    price = item.get("price", "â")
    size = item.get("size", "â")
    condition = item.get("condition", "â")
    brand = item.get("brand")

    # Zeitpunkt, an dem der Treffer in Discord angezeigt wurde.
    displayed_at = time.monotonic()

    def build_embed():
        embed = discord.Embed(
            title="NEUER TREFFER",
            description=f"**{title}**",
            url=url or None,
            timestamp=datetime.now(timezone.utc),
            color=discord.Color.green(),
        )

        # Gewuenschte Reihenfolge: Daten zuerst, Bilder danach.
        embed.add_field(
            name="PREIS",
            value=f"**{price} EUR**",
            inline=True,
        )
        embed.add_field(
            name="GROESSE",
            value=f"**{size}**",
            inline=True,
        )
        embed.add_field(
            name="ZUSTAND",
            value=f"**{condition}**",
            inline=True,
        )

        if brand:
            embed.add_field(
                name="MARKE",
                value=f"**{brand}**",
                inline=False,
            )

        age = max(0, int(time.monotonic() - displayed_at))
        minutes, seconds = divmod(age, 60)

        if minutes:
            age_text = f"{minutes} Min {seconds:02d} Sek"
        else:
            age_text = f"{seconds} Sek"

        embed.set_footer(
            text=(
                f"Erkannt nach {detected_at:.2f}s"
                f" â¢ angezeigt vor {age_text}"
            )
        )

        return embed

    image_urls = get_image_urls(item)
    files = await download_images(image_urls)

    # EIN Treffer = EINE Nachricht. Mehrere Bild-Attachments gehoeren
    # dadurch zur selben Nachricht und koennen in Discord als Medien-
    # galerie geoeffnet werden.
    message = await channel.send(
        embed=build_embed(),
        view=ListingView(url),
        files=files if files else [],
    )

    # Timer separat aktualisieren. Die Abfrage bei Vinted wird dadurch
    # NICHT veraendert und es werden keine Vinted-Requests erzeugt.
    async def update_timer_and_delete():
        try:
            while True:
                elapsed = time.monotonic() - displayed_at
                remaining = DELETE_AFTER - elapsed

                if remaining <= 0:
                    break

                # Alle 5 Sekunden aktualisieren. Das ist bewusst nicht
                # jede Sekunde, um unnoetig viele Discord-Edits zu vermeiden.
                await asyncio.sleep(min(5, remaining))

                if time.monotonic() - displayed_at >= DELETE_AFTER:
                    break

                try:
                    await message.edit(embed=build_embed())
                except discord.NotFound:
                    return
                except discord.Forbidden:
                    return
                except discord.HTTPException as e:
                    print(f"Timer-Update fehlgeschlagen: {e}")

            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                print("Keine Berechtigung zum Loeschen der Treffer-Nachricht.")
            except discord.HTTPException as e:
                print(f"Fehler beim Loeschen der Treffer-Nachricht: {e}")

        except asyncio.CancelledError:
            raise

    asyncio.create_task(update_timer_and_delete())

    # Steuerungsmodul immer ganz unten halten: erst Treffer senden,
    # danach das Modul neu senden.
    await refresh_control_panel(channel, profile_name)


# ============================================================
# SETUP
# ============================================================

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
            "Dafuer brauchst du die Berechtigung **Server verwalten**.",
            ephemeral=True,
        )
        return

    channel = channel or interaction.channel

    try:
        con.execute(
            """INSERT INTO profiles
               (name,channel_id,query,sizes,max_price,condition)
               VALUES(?,?,?,?,?,?)""",
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
            "Profil erstellt.",
            ephemeral=True,
        )

        await refresh_control_panel(
            channel,
            name,
        )

    except sqlite3.IntegrityError:
        await interaction.response.send_message(
            "Profil existiert bereits.",
            ephemeral=True,
        )


# ============================================================
# PROFILES
# ============================================================

@tree.command(
    name="profiles",
    description="Suchprofile anzeigen",
)
async def profiles(interaction):
    rows = con.execute(
        "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
        "FROM profiles"
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "Noch keine Profile."
        )
        return

    await interaction.response.send_message(
        "\n\n".join(
            profile_text(row)
            for row in rows
        )
    )


# ============================================================
# PAUSE
# ============================================================

@tree.command(
    name="pause",
    description="Profil pausieren",
)
async def pause(
    interaction,
    name: str,
):
    if not is_allowed(interaction):
        await interaction.response.send_message(
            "Dafuer brauchst du die Berechtigung **Server verwalten**.",
            ephemeral=True,
        )
        return

    con.execute(
        "UPDATE profiles SET active=0 WHERE name=?",
        (name,),
    )
    con.commit()

    await interaction.response.send_message(
        "Suche pausiert."
    )


# ============================================================
# RESUME
# ============================================================

@tree.command(
    name="resume",
    description="Profil starten",
)
async def resume(
    interaction,
    name: str,
):
    if not is_allowed(interaction):
        await interaction.response.send_message(
            "Dafuer brauchst du die Berechtigung **Server verwalten**.",
            ephemeral=True,
        )
        return

    con.execute(
        "UPDATE profiles SET active=1 WHERE name=?",
        (name,),
    )
    con.commit()

    await interaction.response.send_message(
        "Suche gestartet."
    )


# ============================================================
# MONITOR
# ============================================================

async def monitor():

    await client.wait_until_ready()

    while not client.is_closed():

        profiles = con.execute(
            "SELECT id,name,channel_id,query,sizes,max_price,condition,active "
            "FROM profiles WHERE active=1"
        ).fetchall()

        for p in profiles:

            (
                pid,
                name,
                ch_id,
                query,
                sizes,
                max_price,
                condition,
                _,
            ) = p

            try:

                start = time.monotonic()

                # DEIN FUNKTIONIERENDER VINTED-FETCHER
                items = await source.search_new(query)

                for item in items:

                    iid = str(item["id"])

                    if con.execute(
                        "SELECT 1 FROM seen "
                        "WHERE profile_id=? AND item_id=?",
                        (pid, iid),
                    ).fetchone():
                        continue

                    con.execute(
                        "INSERT INTO seen "
                        "(profile_id,item_id,first_seen) "
                        "VALUES(?,?,?)",
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

                        # Erst jetzt die Detaildaten holen: komplette Galerie
                        # + echtes Erstellungsdatum des Angebots.
                        item = await source.enrich_item(item)

                        ch = client.get_channel(ch_id)

                        if ch:
                            await send_alert(
                                ch,
                                item,
                                name,
                                time.monotonic() - start,
                            )

            except Exception as e:
                print(f"[{name}] Fehler: {e}")

        await asyncio.sleep(POLL_SECONDS)


# ============================================================
# READY
# ============================================================

@client.event
async def on_ready():

    await tree.sync()

    print(
        f"ONLINE: {client.user}"
    )

    if not getattr(
        client,
        "_monitor_started",
        False,
    ):
        client._monitor_started = True
        asyncio.create_task(monitor())


# ============================================================
# START
# ============================================================

client.run(TOKEN)
