"""
Thai TTS Discord bot (th-TH only)
- Free: uses Microsoft Edge's online neural voices via edge-tts (no API key, no cost)
- Reads messages from one text channel aloud in the voice channel it has joined
- Slash commands: /join, /leave, /say, /skip, /voice
"""

import asyncio
import os
import re
import time
import tempfile

import discord
import edge_tts
import imageio_ffmpeg
from gtts import gTTS
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

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

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()  # bundled ffmpeg, nothing to install

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

    async def setup_hook(self):
        await self.tree.sync()

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
            break
        except Exception as e:
            print(f"[{guild.name}] voice connect attempt {attempt} failed: {type(e).__name__}: {e}")
            if guild.voice_client:
                try:
                    await guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
            if attempt == 2:
                return await interaction.followup.send(
                    "❌ เชื่อมต่อห้องเสียงไม่สำเร็จ ลอง /join อีกครั้งในอีกสักครู่"
                )
            await asyncio.sleep(2)

    bot.read_channel[guild.id] = interaction.channel.id
    await interaction.followup.send(
        f"🔊 เข้าห้อง **{channel.name}** แล้ว จะอ่านข้อความใน {interaction.channel.mention}"
    )


@bot.tree.command(name="leave", description="ให้บอทออกจากห้องเสียง")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
    bot.read_channel.pop(interaction.guild.id, None)
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
    # leave automatically when everyone else has left the voice channel
    vc = member.guild.voice_client
    if vc and vc.channel and all(m.bot for m in vc.channel.members):
        await vc.disconnect()
        bot.read_channel.pop(member.guild.id, None)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("ไม่พบ DISCORD_TOKEN — กรุณาใส่โทเคนในไฟล์ .env")
    bot.run(TOKEN)
