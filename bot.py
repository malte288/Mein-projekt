import os, sqlite3, asyncio, time
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ui import View, Button, Select
from dotenv import load_dotenv
from vinted import VintedSource

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
POLL_SECONDS = max(5, int(os.getenv("POLL_SECONDS", "10")))
DB = os.getenv("DB_PATH", "vinted_sniper.db")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in .env")

con = sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS profiles(
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, channel_id INTEGER,
 query TEXT, sizes TEXT, max_price REAL, condition TEXT, active INTEGER DEFAULT 1)""")
con.execute("""CREATE TABLE IF NOT EXISTS seen(
 profile_id INTEGER, item_id TEXT, first_seen TEXT, PRIMARY KEY(profile_id,item_id))""")
con.commit()

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
source = VintedSource()

def get_profile(name):
    return con.execute("SELECT id,name,channel_id,query,sizes,max_price,condition,active FROM profiles WHERE name=?", (name,)).fetchone()

def profile_text(p):
    pid,name,ch,query,sizes,price,cond,active = p
    return (f"**{name}**\n"
            f"🔎 {query}\n"
            f"📏 {sizes or 'alle'}\n"
            f"💰 bis {price if price is not None else '—'} €\n"
            f"✨ {cond or 'alle'}\n"
            f"{'🟢 AKTIV' if active else '⏸️ PAUSIERT'}")

async def send_alert(channel, item, profile_name, detected_at):
    embed = discord.Embed(title=f"🔥 NEUER TREFFER — {profile_name}",
                          description=item.get("title","Vinted-Angebot"),
                          url=item.get("url"), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="💰 Preis", value=f"{item.get('price','—')} €", inline=True)
    embed.add_field(name="📏 Größe", value=str(item.get("size","—")), inline=True)
    embed.add_field(name="✨ Zustand", value=str(item.get("condition","—")), inline=True)
    if item.get("brand"): embed.add_field(name="🏷️ Marke", value=item["brand"], inline=True)
    if item.get("image_url"): embed.set_image(url=item["image_url"])
    embed.set_footer(text=f"Erkannt nach {detected_at:.2f}s* • manuell kaufen")
    await channel.send(embed=embed)
    return time.monotonic()

class ControlView(View):
    def __init__(self, profile_name):
        super().__init__(timeout=None)
        self.profile_name = profile_name

    @discord.ui.button(label="▶️ Start", style=discord.ButtonStyle.success)
    async def start(self, interaction, button):
        con.execute("UPDATE profiles SET active=1 WHERE name=?", (self.profile_name,))
        con.commit()
        await interaction.response.send_message("🟢 Suche gestartet.", ephemeral=True)

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary)
    async def pause(self, interaction, button):
        con.execute("UPDATE profiles SET active=0 WHERE name=?", (self.profile_name,))
        con.commit()
        await interaction.response.send_message("⏸️ Suche pausiert.", ephemeral=True)

    @discord.ui.button(label="📊 Status", style=discord.ButtonStyle.primary)
    async def status(self, interaction, button):
        p = get_profile(self.profile_name)
        await interaction.response.send_message(profile_text(p), ephemeral=True)

@tree.command(name="setup", description="Vinted-Suchprofil erstellen")
@app_commands.describe(name="Profilname", query="Suchbegriff", sizes="z.B. M,L",
                       max_price="Maximalpreis", condition="z.B. Sehr gut",
                       channel="Discord-Kanal für Treffer")
async def setup(interaction: discord.Interaction, name: str, query: str,
                sizes: str = "M,L", max_price: float | None = None,
                condition: str | None = None, channel: discord.TextChannel | None = None):
    channel = channel or interaction.channel
    try:
        con.execute("""INSERT INTO profiles(name,channel_id,query,sizes,max_price,condition)
                       VALUES(?,?,?,?,?,?)""",
                    (name,channel.id,query,sizes,max_price,condition))
        con.commit()
        await interaction.response.send_message(
            f"✅ Profil erstellt.\n\n{profile_text(get_profile(name))}",
            view=ControlView(name))
    except sqlite3.IntegrityError:
        await interaction.response.send_message("❌ Profil existiert bereits.", ephemeral=True)

@tree.command(name="profiles", description="Suchprofile anzeigen")
async def profiles(interaction):
    rows = con.execute("SELECT id,name,channel_id,query,sizes,max_price,condition,active FROM profiles").fetchall()
    if not rows:
        await interaction.response.send_message("Noch keine Profile.")
        return
    await interaction.response.send_message("\n\n".join(profile_text(r) for r in rows))

@tree.command(name="pause", description="Profil pausieren")
async def pause(interaction, name: str):
    con.execute("UPDATE profiles SET active=0 WHERE name=?", (name,)); con.commit()
    await interaction.response.send_message("⏸️ Pausiert.")

@tree.command(name="resume", description="Profil starten")
async def resume(interaction, name: str):
    con.execute("UPDATE profiles SET active=1 WHERE name=?", (name,)); con.commit()
    await interaction.response.send_message("🟢 Gestartet.")

async def monitor():
    await client.wait_until_ready()
    while not client.is_closed():
        profiles = con.execute("SELECT id,name,channel_id,query,sizes,max_price,condition,active FROM profiles WHERE active=1").fetchall()
        for p in profiles:
            pid,name,ch_id,query,sizes,max_price,condition,_ = p
            try:
                start = time.monotonic()
                items = await source.search_new(query)
                for item in items:
                    iid = str(item["id"])
                    if con.execute("SELECT 1 FROM seen WHERE profile_id=? AND item_id=?", (pid,iid)).fetchone():
                        continue
                    con.execute("INSERT INTO seen VALUES(?,?,?)", (pid,iid,datetime.now(timezone.utc).isoformat()))
                    con.commit()
                    if source.matches(item, sizes, max_price, condition):
                        ch = client.get_channel(ch_id)
                        if ch:
                            await send_alert(ch, item, name, time.monotonic()-start)
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
