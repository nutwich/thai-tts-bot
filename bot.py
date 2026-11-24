"""
Thai TTS Discord bot (th-TH only)
- Free: uses Microsoft Edge's online neural voices via edge-tts (no API key, no cost)
- Reads messages from one text channel aloud in the voice channel it has joined
- Slash commands: /join, /leave, /say, /skip, /voice
"""

import asyncio
import os
import re
import shutil
import sys
import time
import tempfile

import discord
import edge_tts
import imageio_ffmpeg
from aiohttp import web   # installed with discord.py
from gtts import gTTS
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

# show log lines immediately (also when running as a Linux service), and never
# crash on a channel/user name the console can't display (emoji etc.)
try:
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
except AttributeError:
    pass

# ---------------------------------------------------------------- settings ---
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

VOICES = {
    "female": "th-TH-PremwadeeNeural",   # Thai female (default)
    "male": "th-TH-NiwatNeural",         # Thai male
}
DEFAULT_VOICE = os.getenv("TTS_VOICE", "female")
RATE = os.getenv("TTS_RATE", "+50%")     # +50% = 1.5x speed
PITCH = os.getenv("TTS_PITCH", "+15Hz")  # slightly higher than normal
READ_NAME = os.getenv("READ_NAME", "false").lower() == "true"  # say "<name>:" first
MAX_CHARS = int(os.getenv("MAX_CHARS", "300"))
# join by itself when someone in a voice channel types in that channel's chat
AUTO_JOIN = os.getenv("AUTO_JOIN", "true").lower() == "true"
# optional extra text channel that also triggers auto-join (joins the typer's voice channel)
READ_CHANNEL_ID = int(os.getenv("READ_CHANNEL_ID", "").strip() or 0)


def _find_ffmpeg() -> str:
    """Bundled ffmpeg first (nothing to install); system ffmpeg as a backup."""
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        system = shutil.which("ffmpeg")
        if system:
            return system
        raise SystemExit("ไม่พบ ffmpeg — ติดตั้งด้วย: sudo apt install ffmpeg")


FFMPEG = _find_ffmpeg()


def _check_opus():
    """Windows: discord.py ships opus. Linux: needs the libopus0 system package."""
    if not discord.opus.is_loaded():
        try:
            discord.opus._load_default()
        except Exception:
            pass
    if not discord.opus.is_loaded():
        try:
            discord.opus.load_opus("libopus.so.0")
        except Exception:
            pass
    if not discord.opus.is_loaded():
        print("WARNING: Opus library not found, voice will not work. "
              "On Ubuntu/Debian run: sudo apt install libopus0")

# ------------------------------------------------------------------ client ---
intents = discord.Intents.default()
intents.message_content = True   # must also be switched on in the Developer Portal
intents.voice_states = True


class TTSBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.queues: dict[int, asyncio.Queue] = {}
        self.workers: dict[int, asyncio.Task] = {}
        self.read_channel: dict[int, int] = {}   # guild_id -> text channel id
        self.voice_choice: dict[int, str] = {}   # guild_id -> "female"/"male"
        self.last_text: dict[int, int] = {}      # guild_id -> text channel of the last /join
        self.auto_paused: set[int] = set()       # guilds where /leave was used
        self.connect_locks: dict[int, asyncio.Lock] = {}
        self._health = None

    async def setup_hook(self):
        await self.tree.sync()
        port = os.getenv("PORT")   # set by Render and similar hosts
        if port:
            self._health = await start_health_server(int(port))

    async def on_ready(self):
        print(f"Logged in as {self.user} | servers: {len(self.guilds)}")


bot = TTSBot()


# ---------------------------------------------------------------- helpers ---
def clean_text(message: discord.Message) -> str:
    text = message.clean_content                         # mentions -> names
    text = re.sub(r"https?://\S+", "ลิงก์", text)          # links
    text = re.sub(r"<a?:(\w+):\d+>", "", text)           # custom emoji
    text = re.sub(r"```.*?```", "โค้ด", text, flags=re.S)  # code blocks
    text = re.sub(r"[*_~`|>]", "", text)                 # markdown symbols
    text = re.sub(r"(.)\1{4,}", r"\1\1\1", text)         # 55555555 -> 555
    text = text.strip()
    if message.attachments and not text:
        text = "ส่งไฟล์แนบ"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " และอื่นๆ"
    return text


# ffmpeg filter used when the engine itself cannot change speed/pitch.
# Follows TTS_RATE (e.g. +50% -> 1.5x) and raises pitch ~6%.
try:
    _SPEED = max(0.5, 1 + int(RATE.strip().rstrip("%")) / 100)
except ValueError:
    _SPEED = 1.5
SPEED_PITCH_FILTER = (f'-af "aresample=48000,asetrate=50880,aresample=48000,'
                      f'atempo={_SPEED / 1.06:.3f}"')


EDGE_TIMEOUT = 3.0          # seconds to wait for Microsoft before using Google
EDGE_COOLDOWN = 600         # skip Microsoft for 10 min after it fails
_edge_disabled_until = 0.0


def _tmp_mp3() -> str:
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    return path


async def _edge(text: str, voice: str) -> str:
    path = _tmp_mp3()
    try:
        await edge_tts.Communicate(text, voice, rate=RATE, pitch=PITCH).save(path)
        if os.path.getsize(path) == 0:
            raise RuntimeError("empty audio")
        return path
    except BaseException:
        _safe_remove(path)
        raise


async def _google(text: str) -> str:
    path = _tmp_mp3()
    try:
        await asyncio.to_thread(lambda: gTTS(text=text, lang="th").save(path))
        return path
    except BaseException:
        _safe_remove(path)
        raise


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def synthesise(text: str, guild_id: int) -> tuple[str, str, str]:
    """Returns (mp3 path, extra ffmpeg options, engine name)."""
    global _edge_disabled_until
    voice = VOICES[bot.voice_choice.get(guild_id, DEFAULT_VOICE)]

    if time.monotonic() < _edge_disabled_until:
        return await _google(text), SPEED_PITCH_FILTER, "google"

    # ask both at the same time; prefer Microsoft if it answers quickly
    google_task = asyncio.create_task(_google(text))
    try:
        path = await asyncio.wait_for(_edge(text, voice), timeout=EDGE_TIMEOUT)
        google_task.add_done_callback(
            lambda t: _safe_remove(t.result()) if not t.cancelled() and not t.exception() else None)
        return path, "", "microsoft"
    except Exception as e:
        _edge_disabled_until = time.monotonic() + EDGE_COOLDOWN
        print(f"  Microsoft voice unavailable ({type(e).__name__}); using Google for the next 10 min")
        return await google_task, SPEED_PITCH_FILTER, "google"


async def player(guild: discord.Guild):
    queue = bot.queues[guild.id]
    while True:
        synth_task, queued_at = await queue.get()
        vc = guild.voice_client
        path = None
        try:
            path, opts, engine = await synth_task
            if not vc or not vc.is_connected():
                continue
            st = getattr(vc, "_connection", None)
            # wait (max 5 s) for Discord's end-to-end encryption to be ready
            for _ in range(50):
                if st is None or getattr(st, "dave_protocol_version", 0) == 0 or getattr(st, "can_encrypt", True):
                    break
                await asyncio.sleep(0.1)
            print(f"  ready in {time.monotonic() - queued_at:.1f}s via {engine}")
            done = asyncio.Event()
            vc.play(
                discord.FFmpegPCMAudio(path, executable=FFMPEG, options=f"-vn {opts}".strip()),
                after=lambda _e: bot.loop.call_soon_threadsafe(done.set),
            )
            await done.wait()
        except Exception as e:
            print(f"[{guild.name}] TTS error: {type(e).__name__}: {e}")
        finally:
            _safe_remove(path)


def enqueue(guild: discord.Guild, text: str):
    if guild.id not in bot.queues:
        bot.queues[guild.id] = asyncio.Queue(maxsize=20)
        bot.workers[guild.id] = asyncio.create_task(player(guild))
    queue = bot.queues[guild.id]
    if queue.full():
        return  # drop spam rather than lag behind
    task = asyncio.create_task(synthesise(text, guild.id))
    queue.put_nowait((task, time.monotonic()))


# ------------------------------------------------------------ voice / join ---
def humans(channel) -> list:
    return [m for m in channel.members if not m.bot]


def _lock(guild: discord.Guild) -> asyncio.Lock:
    return bot.connect_locks.setdefault(guild.id, asyncio.Lock())


async def _connect(guild: discord.Guild, channel) -> bool:
    """Connect (or move) to a voice channel, with one retry. Call with _lock held."""
    for attempt in (1, 2):
        try:
            vc = guild.voice_client
            if vc and vc.is_connected():
                if vc.channel != channel:
                    await vc.move_to(channel)
            else:
                if vc:  # half-open connection left over from a failed try
                    await vc.disconnect(force=True)
                await channel.connect(self_deaf=True, timeout=20, reconnect=True)
            return True
        except Exception as e:
            print(f"[{guild.name}] voice connect attempt {attempt} failed: {type(e).__name__}: {e}")
            if guild.voice_client:
                try:
                    await guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
            if attempt == 1:
                await asyncio.sleep(2)
    return False


async def auto_join_from_message(message: discord.Message):
    """Someone in a voice channel typed in that channel's chat (or in READ_CHANNEL_ID /
    the last /join channel) while the bot is not in voice: join them and read there."""
    guild = message.guild
    member = message.author
    voice = member.voice.channel if isinstance(member, discord.Member) and member.voice else None
    if (voice is None or not isinstance(voice, discord.VoiceChannel)
            or voice == guild.afk_channel or guild.id in bot.auto_paused):
        return
    if message.channel.id not in (voice.id, READ_CHANNEL_ID, bot.last_text.get(guild.id)):
        return
    if not voice.permissions_for(guild.me).connect:
        return
    async with _lock(guild):   # several messages at once -> connect only once
        if guild.voice_client:
            return
        if await _connect(guild, voice):
            bot.read_channel[guild.id] = message.channel.id
            print(f"[{guild.name}] auto-joined {voice.name} (message in #{message.channel.name})")


async def start_health_server(port: int) -> web.AppRunner:
    """Tiny web page so free hosts (Render) see a web service and cron can ping it."""
    async def ok(_request):
        return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Health check listening on port {port}")
    return runner


# --------------------------------------------------------------- commands ---
@bot.tree.command(name="join", description="ให้บอทเข้าห้องเสียงและอ่านข้อความในห้องแชทนี้")
async def join(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
        return await interaction.response.send_message("กรุณาเข้าห้องเสียงก่อน", ephemeral=True)
    # connecting can take several seconds; tell Discord we are working on it
    await interaction.response.defer(thinking=True)
    channel = member.voice.channel
    guild = interaction.guild
    bot.auto_paused.discard(guild.id)

    async with _lock(guild):
        ok = await _connect(guild, channel)
    if not ok:
        return await interaction.followup.send(
            "❌ เชื่อมต่อห้องเสียงไม่สำเร็จ ลอง /join อีกครั้งในอีกสักครู่"
        )

    bot.read_channel[guild.id] = interaction.channel.id
    bot.last_text[guild.id] = interaction.channel.id
    await interaction.followup.send(
        f"🔊 เข้าห้อง **{channel.name}** แล้ว จะอ่านข้อความใน {interaction.channel.mention}"
    )


@bot.tree.command(name="leave", description="ให้บอทออกจากห้องเสียง")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
    bot.read_channel.pop(interaction.guild.id, None)
    if AUTO_JOIN:  # don't jump straight back in; resumes once everyone has left voice
        bot.auto_paused.add(interaction.guild.id)
    await interaction.response.send_message("👋 ออกจากห้องเสียงแล้ว")


@bot.tree.command(name="say", description="ให้บอทพูดข้อความ")
@app_commands.describe(text="ข้อความที่ต้องการให้พูด")
async def say(interaction: discord.Interaction, text: str):
    if not interaction.guild.voice_client:
        return await interaction.response.send_message("ใช้ /join ก่อน", ephemeral=True)
    enqueue(interaction.guild, text[:MAX_CHARS])
    await interaction.response.send_message("✅", ephemeral=True)


@bot.tree.command(name="skip", description="ข้ามข้อความที่กำลังอ่าน")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
    await interaction.response.send_message("⏭️ ข้ามแล้ว", ephemeral=True)


@bot.tree.command(name="voice", description="เลือกเสียงผู้หญิงหรือผู้ชาย")
@app_commands.choices(gender=[
    app_commands.Choice(name="ผู้หญิง (Premwadee)", value="female"),
    app_commands.Choice(name="ผู้ชาย (Niwat)", value="male"),
])
async def voice(interaction: discord.Interaction, gender: app_commands.Choice[str]):
    bot.voice_choice[interaction.guild.id] = gender.value
    await interaction.response.send_message(f"เปลี่ยนเป็นเสียง{gender.name}แล้ว")


# ----------------------------------------------------------------- events ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if AUTO_JOIN and not message.guild.voice_client:
        await auto_join_from_message(message)   # then read this message too
    if bot.read_channel.get(message.guild.id) != message.channel.id:
        return
    if not message.guild.voice_client:
        return
    text = clean_text(message)
    if not text:
        return
    if READ_NAME:
        text = f"{message.author.display_name}: {text}"
    enqueue(message.guild, text)


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    # leave automatically when everyone else has left the voice channel
    vc = guild.voice_client
    if vc and vc.channel and not humans(vc.channel):
        await vc.disconnect()
        bot.read_channel.pop(guild.id, None)

    # after /leave, auto-join resumes once everyone has left voice
    if guild.id in bot.auto_paused and not any(humans(ch) for ch in guild.voice_channels):
        bot.auto_paused.discard(guild.id)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("ไม่พบ DISCORD_TOKEN — กรุณาใส่โทเคนในไฟล์ .env")
    _check_opus()
    bot.run(TOKEN)
