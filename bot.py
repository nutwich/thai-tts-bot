"""
Thai TTS Discord bot (th-TH only)
- Free: uses Microsoft Edge's online neural voices via edge-tts (no API key, no cost)
- Reads messages from one text channel aloud in the voice channel it has joined
- Slash commands: /join, /leave, /say, /skip, /voice, /myvoice, /resetvoice
- Settings (/myvoice, /voice) are kept in MongoDB Atlas (free) when MONGODB_URI is set
"""

import asyncio
import json
import os
import re
import shutil
import sys
import time
import tempfile
from typing import Optional

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


def _env_num(name: str, default, cast=float):
    try:
        return cast(os.getenv(name, "").strip().rstrip("%Hzx") or default)
    except ValueError:
        return default


# server-wide defaults (from TTS_RATE / TTS_PITCH)
try:
    DEFAULT_SPEED = max(0.5, 1 + int(RATE.strip().rstrip("%")) / 100)   # +50% -> 1.5
except ValueError:
    DEFAULT_SPEED = 1.5
try:
    DEFAULT_PITCH = int(PITCH.strip().rstrip("Hz").rstrip("hz"))          # +15Hz -> 15
except ValueError:
    DEFAULT_PITCH = 15

# limits for each member's own /myvoice settings
SPEED_MIN = _env_num("SPEED_MIN", 1.0)
SPEED_MAX = max(SPEED_MIN + 0.1, _env_num("SPEED_MAX", 2.0))
PITCH_MIN = _env_num("PITCH_MIN", -20, int)
PITCH_MAX = max(PITCH_MIN + 1, _env_num("PITCH_MAX", 40, int))

# per-member settings survive restarts (only voice/speed/pitch, never messages)
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_settings.json")
# optional free MongoDB Atlas database so settings are never lost (secret, like the token)
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "").strip() or "thai_tts_bot"


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


# --------------------------------------------------------- settings storage ---
# "users":  user_id  -> {"voice", "speed", "pitch"}   (/myvoice, same in every server)
# "guilds": guild_id -> {"voice"}                      (/voice server default)
# With MONGODB_URI (MongoDB Atlas free M0) the database is the real copy, so cloud
# redeploys and moving between PC and cloud lose nothing. user_settings.json next to
# bot.py is always kept too: the only store without a database, otherwise an offline
# copy plus a list of changes not yet saved to the database ("pending").
KINDS = ("users", "guilds")


def _safe_err(e: Exception) -> str:
    """Exception text without the connection string (it contains the DB password)."""
    msg = str(e)
    if MONGODB_URI:
        msg = msg.replace(MONGODB_URI, "<MONGODB_URI>")
    return f"{type(e).__name__}: {msg[:200]}"


def load_local_settings() -> dict:
    state = {"users": {}, "guilds": {}, "pending": {"users": [], "guilds": []}}
    try:
        with open(SETTINGS_FILE, "rb") as f:
            data = json.loads(f.read())
    except FileNotFoundError:
        return state
    except Exception as e:
        print(f"WARNING: could not read user_settings.json ({type(e).__name__}); starting empty")
        return state
    if not isinstance(data, dict):
        return state
    if not isinstance(data.get("users"), dict):   # first version: the file was only the users
        data = {"users": data}
    for kind in KINDS:
        if isinstance(data.get(kind), dict):
            state[kind] = data[kind]
        pending = (data.get("pending") or {}).get(kind)
        if isinstance(pending, list):
            state["pending"][kind] = [str(k) for k in pending]
    return state


def write_local_settings(state: dict):
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_FILE)
    except OSError as e:
        print(f"WARNING: could not save settings file: {e}")


class TTSBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.queues: dict[int, asyncio.Queue] = {}
        self.workers: dict[int, asyncio.Task] = {}
        self.read_channel: dict[int, int] = {}   # guild_id -> text channel id
        local = load_local_settings()
        self.user_settings: dict[str, dict] = local["users"]    # user_id -> voice/speed/pitch
        self.guild_settings: dict[str, dict] = local["guilds"]  # guild_id -> voice
        # ids changed but not yet saved to MongoDB (kept in the file across restarts)
        self._pending: dict[str, set] = {k: set(local["pending"][k]) for k in KINDS}
        self._db = None
        self._sync_task: Optional[asyncio.Task] = None
        self.last_text: dict[int, int] = {}      # guild_id -> text channel of the last /join
        self.auto_paused: set[int] = set()       # guilds where /leave was used
        self.connect_locks: dict[int, asyncio.Lock] = {}
        self._health = None

    async def setup_hook(self):
        port = os.getenv("PORT")   # set by Render and similar hosts
        if MONGODB_URI:
            await self.connect_database()
        else:
            print(f"Settings: saved only in {SETTINGS_FILE} (MONGODB_URI is not set)")
            if port:
                print("WARNING: without MONGODB_URI, /myvoice and /voice settings "
                      "will be lost on every cloud redeploy")
        await self.tree.sync()
        if port:
            self._health = await start_health_server(int(port))

    # ----- settings: MongoDB + local file -----
    async def connect_database(self):
        """Save changes made while offline, then load everything from MongoDB.
        If MongoDB is unreachable the local copy is used and saving is retried."""
        try:
            from pymongo import AsyncMongoClient
            client = AsyncMongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000,
                                      appname="thai-tts-bot")
            self._db = client[MONGODB_DB]
        except Exception as e:   # bad connection string or pymongo missing: no point retrying
            print(f"WARNING: MongoDB not usable ({_safe_err(e)}); using the local file only")
            return
        try:
            await self._flush_pending()          # local changes not saved last time
            fresh = {}
            for kind in KINDS:
                fresh[kind] = {}
                async for doc in self._db[kind].find({}):
                    fresh[kind][str(doc.pop("_id"))] = doc
        except Exception as e:
            print(f"WARNING: MongoDB unreachable ({_safe_err(e)}); using the local copy for now")
            self._schedule_sync()
            return
        self.user_settings, self.guild_settings = fresh["users"], fresh["guilds"]
        self._write_local()
        print(f"Loaded settings from MongoDB (database {MONGODB_DB}): "
              f"{len(self.user_settings)} member(s), {len(self.guild_settings)} server(s)")

    def set_setting(self, kind: str, key: str, doc: Optional[dict]):
        """Change one member's ("users") or server's ("guilds") settings; None removes them.
        Writes the local file now and MongoDB in the background."""
        store = self.user_settings if kind == "users" else self.guild_settings
        if doc:
            store[key] = dict(doc)
        else:
            store.pop(key, None)
        self._pending[kind].add(key)
        self._write_local()
        self._schedule_sync()

    def _write_local(self):
        write_local_settings({
            "users": self.user_settings,
            "guilds": self.guild_settings,
            "pending": {k: sorted(v) for k, v in self._pending.items()},
        })

    def _schedule_sync(self):
        if self._db is not None and (self._sync_task is None or self._sync_task.done()):
            self._sync_task = asyncio.create_task(self._sync_worker())

    async def _sync_worker(self):
        failures = 0
        while any(self._pending.values()):
            await asyncio.sleep(1 if failures == 0 else min(300, 30 * failures))
            try:
                await self._flush_pending()
                failures = 0
            except Exception as e:
                failures += 1
                print(f"WARNING: saving settings to MongoDB failed ({_safe_err(e)}); will retry")

    async def _flush_pending(self):
        for kind in KINDS:
            store = self.user_settings if kind == "users" else self.guild_settings
            for key in sorted(self._pending[kind]):
                doc = dict(store[key]) if key in store else None
                if doc:
                    await self._db[kind].replace_one({"_id": key}, doc, upsert=True)
                else:
                    await self._db[kind].delete_one({"_id": key})
                if store.get(key) == doc:        # not changed again while saving
                    self._pending[kind].discard(key)
                print(f"  saved {kind[:-1]} {key} to MongoDB ({MONGODB_DB}.{kind})")
        self._write_local()

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


def _clamp(value, low, high):
    return max(low, min(high, value))


def voice_params(guild_id: int, user_id: int | None) -> tuple[str, float, int]:
    """(voice key, speed, pitch Hz) for this member: own /myvoice settings within
    the limits, otherwise the server defaults."""
    s = bot.user_settings.get(str(user_id), {}) if user_id else {}
    gender = (s.get("voice") or bot.guild_settings.get(str(guild_id), {}).get("voice")
              or DEFAULT_VOICE)
    if gender not in VOICES:
        gender = "female"
    try:
        speed = _clamp(float(s["speed"]), SPEED_MIN, SPEED_MAX) if "speed" in s else DEFAULT_SPEED
        pitch = _clamp(int(s["pitch"]), PITCH_MIN, PITCH_MAX) if "pitch" in s else DEFAULT_PITCH
    except (TypeError, ValueError):
        speed, pitch = DEFAULT_SPEED, DEFAULT_PITCH
    return gender, speed, pitch


def speed_pitch_filter(speed: float, pitch_hz: int) -> str:
    """ffmpeg filter used when the engine itself cannot change speed/pitch.
    +15 Hz ~ 6% higher (asetrate 50880), like edge-tts' pitch shift."""
    ratio = _clamp(1 + pitch_hz / 250, 0.8, 1.25)
    tempo = speed / ratio
    steps = []
    while tempo > 2.0:      # older ffmpeg builds accept atempo 0.5..2.0 only
        steps.append("atempo=2.0")
        tempo /= 2.0
    while tempo < 0.5:
        steps.append("atempo=0.5")
        tempo /= 0.5
    steps.append(f"atempo={tempo:.3f}")
    return (f'-af "aresample=48000,asetrate={round(48000 * ratio)},aresample=48000,'
            f'{",".join(steps)}"')


EDGE_TIMEOUT = 3.0          # seconds to wait for Microsoft before using Google
EDGE_COOLDOWN = 600         # skip Microsoft for 10 min after it fails
_edge_disabled_until = 0.0


def _tmp_mp3() -> str:
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    return path


async def _edge(text: str, voice: str, speed: float, pitch: int) -> str:
    path = _tmp_mp3()
    try:
        rate = f"{round((speed - 1) * 100):+d}%"   # 1.5 -> +50%
        await edge_tts.Communicate(text, voice, rate=rate, pitch=f"{pitch:+d}Hz").save(path)
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


async def synthesise(text: str, guild_id: int, user_id: int | None = None) -> tuple[str, str, str]:
    """Returns (mp3 path, extra ffmpeg options, engine name)."""
    global _edge_disabled_until
    gender, speed, pitch = voice_params(guild_id, user_id)
    voice = VOICES[gender]
    fallback_filter = speed_pitch_filter(speed, pitch)

    if time.monotonic() < _edge_disabled_until:
        return await _google(text), fallback_filter, "google"

    # ask both at the same time; prefer Microsoft if it answers quickly
    google_task = asyncio.create_task(_google(text))
    try:
        path = await asyncio.wait_for(_edge(text, voice, speed, pitch), timeout=EDGE_TIMEOUT)
        google_task.add_done_callback(
            lambda t: _safe_remove(t.result()) if not t.cancelled() and not t.exception() else None)
        return path, "", "microsoft"
    except Exception as e:
        _edge_disabled_until = time.monotonic() + EDGE_COOLDOWN
        print(f"  Microsoft voice unavailable ({type(e).__name__}); using Google for the next 10 min")
        return await google_task, fallback_filter, "google"


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


def enqueue(guild: discord.Guild, text: str, user_id: int | None = None):
    if guild.id not in bot.queues:
        bot.queues[guild.id] = asyncio.Queue(maxsize=20)
        bot.workers[guild.id] = asyncio.create_task(player(guild))
    queue = bot.queues[guild.id]
    if queue.full():
        return  # drop spam rather than lag behind
    task = asyncio.create_task(synthesise(text, guild.id, user_id))
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
    enqueue(interaction.guild, text[:MAX_CHARS], interaction.user.id)
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
    bot.set_setting("guilds", str(interaction.guild.id), {"voice": gender.value})
    await interaction.response.send_message(
        f"เปลี่ยนเสียงเริ่มต้นของเซิร์ฟเวอร์เป็นเสียง{gender.name}แล้ว "
        f"(คนที่ตั้ง /myvoice ไว้จะใช้เสียงของตัวเอง)")


VOICE_NAMES = {"female": "ผู้หญิง", "male": "ผู้ชาย"}


def describe_settings(guild_id: int, user_id: int) -> str:
    gender, speed, pitch = voice_params(guild_id, user_id)
    own = bot.user_settings.get(str(user_id), {})
    tag = lambda key: "" if key in own else " (ค่าเริ่มต้น)"
    return (f"🗣️ เสียง: **{VOICE_NAMES[gender]}**{tag('voice')}\n"
            f"⏩ ความเร็ว: **{speed:g}x**{tag('speed')}\n"
            f"🎵 ระดับเสียง: **{pitch:+d}**{tag('pitch')}\n"
            f"-# ลิมิต: ความเร็ว {SPEED_MIN:g}–{SPEED_MAX:g}x · ระดับเสียง {PITCH_MIN:+d} ถึง {PITCH_MAX:+d}")


@bot.tree.command(name="myvoice", description="ตั้งเสียง ความเร็ว และระดับเสียงที่บอทใช้อ่านข้อความของคุณ")
@app_commands.describe(
    voice="เสียงผู้หญิงหรือผู้ชาย",
    speed=f"ความเร็ว {SPEED_MIN:g}–{SPEED_MAX:g} เท่า (เช่น 1.5)",
    pitch=f"ระดับเสียง {PITCH_MIN:+d} ถึง {PITCH_MAX:+d} (0 = ปกติ, มาก = เสียงสูง)",
)
@app_commands.choices(voice=[
    app_commands.Choice(name="ผู้หญิง (Premwadee)", value="female"),
    app_commands.Choice(name="ผู้ชาย (Niwat)", value="male"),
])
async def myvoice(
    interaction: discord.Interaction,
    voice: Optional[app_commands.Choice[str]] = None,
    speed: Optional[app_commands.Range[float, SPEED_MIN, SPEED_MAX]] = None,
    pitch: Optional[app_commands.Range[int, PITCH_MIN, PITCH_MAX]] = None,
):
    key = str(interaction.user.id)
    if voice is None and speed is None and pitch is None:   # just show current settings
        return await interaction.response.send_message(
            describe_settings(interaction.guild_id, interaction.user.id), ephemeral=True)
    s = dict(bot.user_settings.get(key, {}))   # copy: the saver compares old vs new
    if voice is not None:
        s["voice"] = voice.value
    if speed is not None:
        s["speed"] = round(_clamp(speed, SPEED_MIN, SPEED_MAX), 2)
    if pitch is not None:
        s["pitch"] = _clamp(pitch, PITCH_MIN, PITCH_MAX)
    bot.set_setting("users", key, s)
    await interaction.response.send_message(
        "✅ บันทึกแล้ว\n" + describe_settings(interaction.guild_id, interaction.user.id),
        ephemeral=True)


@bot.tree.command(name="resetvoice", description="กลับไปใช้เสียงและความเร็วเริ่มต้นของเซิร์ฟเวอร์")
async def resetvoice(interaction: discord.Interaction):
    if str(interaction.user.id) in bot.user_settings:
        bot.set_setting("users", str(interaction.user.id), None)
    await interaction.response.send_message(
        "↩️ กลับเป็นค่าเริ่มต้นแล้ว\n" + describe_settings(interaction.guild_id, interaction.user.id),
        ephemeral=True)


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
    enqueue(message.guild, text, message.author.id)


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
