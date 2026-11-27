# CLAUDE.md — Thai TTS Discord Bot

## Purpose
A free text-to-speech Discord bot that reads chat messages aloud **in Thai only** (th-TH) in a voice channel, at **1.5x speed** and **slightly higher pitch**, without reading the sender's name. It runs on a friend's Discord server ("Jin's server") and is self-hosted on the owner's Windows PC.

Hard requirement: **100% free**. No paid APIs, no paid hosting, no API keys apart from the Discord bot token.

## Current status (23 Nov 2026)
- The bot application exists on the Discord Developer Portal as **"บอทท่านจิน"** (originally "Thai TTS"; bot user `บอทท่านจิน#2654`) (Application ID `1574447505498841338`, Public Bot = on).
- **Message Content Intent** is enabled. The other privileged intents are off.
- The bot is invited to the friend's server and logs in successfully (it logged in as `Thai TTS#2654` before the rename).
- **Voice connection works.** The DAVE / E2EE handshake completes ("Voice connection complete").
- **Working:** speech is heard in the voice channel after pinning `davey==0.1.7` and waiting for DAVE `can_encrypt` before playback (see Problems #6).
- **Ongoing:** Microsoft Edge TTS intermittently returns `NoAudioReceived`; the gTTS fallback covers it.

## Stack
| Part | Choice | Why |
|---|---|---|
| Language | Python 3.12 (Windows, per-user install via winget) | Prebuilt Windows wheels exist for every dependency, including `davey` |
| Discord library | `discord.py[voice]` >= 2.7.1 | 2.7 added **DAVE** support; Discord has required DAVE E2EE for all non-stage voice connections since **1 Mar 2026**, so older libraries cannot join voice |
| E2EE | `davey==0.1.7` (**pinned**) | DAVE protocol implementation; 0.2.0 left the session `ready=False` with discord.py 2.7.1 |
| TTS, primary | `edge-tts` → `th-TH-PremwadeeNeural` (female, default) / `th-TH-NiwatNeural` (male) | Free Microsoft Edge neural voices, with native `rate` / `pitch` support |
| TTS, fallback | `gTTS` (Google Translate, `lang="th"`) | Free; used only if edge-tts fails twice |
| Audio | `imageio-ffmpeg` (bundled ffmpeg binary) | No manual ffmpeg install on Windows |
| Config | `python-dotenv` → `.env` | Keeps the token out of the code |
| Settings DB | `pymongo>=4.13` → MongoDB Atlas M0 (optional, via `MONGODB_URI`) | Free forever with no card; the owner chose it so `/myvoice` and `/voice` survive cloud redeploys across all servers |

## Files (`Documents\thai-tts-bot\` on the host PC)
- `bot.py`: the whole bot (single file).
- `requirements.txt`: dependencies.
- `start.bat`: one-click launcher. It finds Python or installs it via `winget install Python.Python.3.12 --scope user`, creates `.venv`, runs `pip install -r requirements.txt` on every start, creates `.env` from `.env.example` and opens it in Notepad if it is missing, then runs `bot.py`.
- `setup.sh`: Linux (Ubuntu/Debian) setup/update. Installs `python3-venv` + `libopus0` (discord.py does not bundle opus on Linux), creates `.venv`, installs requirements, creates `.env` (chmod 600), and installs a systemd service `thai-tts-bot` (restart on crash, start on boot). Re-run after `git pull` to update. Must stay LF (`.gitattributes`).
- `Dockerfile` / `.dockerignore` / `render.yaml`: free Render hosting (Docker, `plan: free`, Singapore). Token is a Render dashboard secret (`sync: false`), never in git.
- `CLOUD_SETUP.md`: Thai step-by-step guide: Render + cron-job.org ping (primary), Linux VM via `setup.sh` (fallback). The owner could not sign up for Oracle Cloud, so Oracle is not an option.
- `.env.example`: config template.
- `.env`: **contains the bot token. Never commit, share or paste it anywhere.**

## Configuration (`.env`)
```
DISCORD_TOKEN=          # bot token (secret)
TTS_VOICE=female        # female | male
TTS_RATE=+50%           # edge-tts rate; +50% = 1.5x (ffmpeg fallback follows this too)
TTS_PITCH=+15Hz         # edge-tts pitch; slightly higher
READ_NAME=false         # true = prefix "<display name>:" before each message
MAX_CHARS=300           # truncate long messages ("และอื่นๆ")
AUTO_JOIN=true          # someone in voice types in that voice channel's chat -> bot joins
READ_CHANNEL_ID=        # optional extra text channel that can also summon the bot
SPEED_MIN=1.0           # /myvoice speed limits (x)
SPEED_MAX=2.0
PITCH_MIN=-20           # /myvoice pitch limits (Hz)
PITCH_MAX=40
MONGODB_URI=            # SECRET: MongoDB Atlas (free M0) connection string, contains the DB password
MONGODB_DB=             # optional database name, default thai_tts_bot
PORT=                   # set by Render: starts an aiohttp /health endpoint for cron pings
```

## Behaviour
- Slash commands (synced globally in `setup_hook`):
  - `/join` — joins the caller's voice channel (self-deafened) and binds **the text channel the command was used in** as the channel to read.
  - `/leave` — disconnects and unbinds.
  - `/say text` — speaks arbitrary text.
  - `/skip` — stops the current clip.
  - `/voice female|male` — switches the **server default** voice.
  - `/myvoice [voice] [speed] [pitch]` — each member sets their own voice, speed (x) and pitch (Hz) for their messages and `/say`. Discord enforces the limits (`app_commands.Range` from `SPEED_MIN/MAX`, `PITCH_MIN/MAX`), and stored values are clamped again when they are used. Options left out are kept. With no options it shows the current settings (ephemeral).
  - `/resetvoice` — removes that member's own settings (back to the server defaults).
- **Settings storage.** The owner does not want settings lost, and the bot is in several servers. Render wipes its disk on redeploy.
  - Two kinds of data:
    - `users`: user ID → voice/speed/pitch. This is global, the same in every server.
    - `guilds`: guild ID → `/voice` default.
  - **MongoDB Atlas M0** (free, no card) is the real copy when `MONGODB_URI` is set.
    - Database `MONGODB_DB` (default `thai_tts_bot`), with collections `users` and `guilds`. `_id` is the Discord ID as a string.
    - Uses `pymongo` `AsyncMongoClient`.
  - `user_settings.json` next to `bot.py` (git- and docker-ignored) is always written. It is the only store when there is no database, otherwise an offline cache.
    - Format: `{"users", "guilds", "pending": {"users": [...], "guilds": [...]}}`. A bare users dict from the first version is still read.
  - Changes go through `bot.set_setting(kind, key, doc|None)`. It writes the file now and adds the key to `pending`. `_sync_worker` then upserts or deletes in MongoDB, with backoff up to 5 min.
    - A key leaves `pending` only if it was not changed again while saving. Pass a **new dict**, not a mutated one.
  - At start, `setup_hook` → `connect_database()` first flushes `pending` from the file (changes made while offline), then loads everything from MongoDB.
    - If MongoDB is unreachable, the local copy is used and saving is retried.
  - Error text goes through `_safe_err()`, so the URI (which contains the password) is never logged.
  - On Render without `MONGODB_URI`, a warning is logged.
  - `SSL handshake failed ... TLSV1_ALERT_INTERNAL_ERROR` from `*.mongodb.net` means Atlas **Network Access** does not allow the caller's IP. Render IPs change, so the fix is to allow `0.0.0.0/0`. Seen on Render on 27 Nov 2026.
- `voice_params()` resolves voice/speed/pitch per message: member setting → server `/voice` → `.env` defaults. edge-tts gets `rate=f"{(speed-1)*100:+d}%"`, `pitch=f"{pitch:+d}Hz"`; the gTTS fallback uses `speed_pitch_filter(speed, pitch)` (pitch ratio `1 + Hz/250`, so +15 Hz ≈ 6%; atempo is chained to stay within 0.5–2.0).
- `on_message`: reads only non-bot messages in the bound channel, and only while the bot is connected to voice.
- Text cleaning before synthesis:
  - `clean_content` (mentions become names)
  - URLs become "ลิงก์"
  - custom emoji are stripped
  - code blocks become "โค้ด"
  - markdown symbols are stripped
  - 5+ repeated characters collapse to 3 (e.g. 55555555 → 555)
  - an attachment with no text becomes "ส่งไฟล์แนบ"
  - text is truncated at `MAX_CHARS`
- Each server has its own `asyncio.Queue` (maxsize 20, excess dropped to avoid lag) and its own player task. Each clip is a temp MP3, played through `FFmpegPCMAudio`, then deleted (no message or audio retention).
- Auto-disconnect when only bots remain in the voice channel.
- **Auto-join** (`AUTO_JOIN`, in `on_message` → `auto_join_from_message`). This is what the owner means by "auto join"; it does *not* join on voice-channel entry or on startup.
  - Trigger: the bot is not in voice in that server, and a human who is in a voice channel types in that voice channel's built-in chat. `READ_CHANNEL_ID` or the last `/join` text channel also count. The AFK channel is ignored.
  - Effect: the bot joins the typer's voice channel, binds the channel the message was typed in, and reads that message too.
  - The bot never moves if it is already in another channel.
  - `/leave` pauses auto-join for that server until no humans are in any voice channel, or until `/join`.
  - `/join` and auto-join share `_connect()` behind a per-guild `asyncio.Lock`, so simultaneous messages connect once.
- On Linux, `_check_opus()` loads `libopus.so.0` (warns if missing), and ffmpeg falls back to the system binary if the `imageio-ffmpeg` one is unavailable.

## TTS fallback chain (`synthesise()`)
1. edge-tts with `rate=RATE, pitch=PITCH`, so no ffmpeg filter is needed.
2. edge-tts plain, with speed and pitch applied by ffmpeg.
3. gTTS Thai, with speed and pitch applied by ffmpeg.

The ffmpeg filter for steps 2 and 3:
`-af "aresample=48000,asetrate=50880,aresample=48000,atempo=<speed/1.06>"`, where speed is derived from `TTS_RATE`.
This raises pitch by about 6%. At +50% the overall speed is 1.5x. Verified: a 2.0 s input becomes 1.34 s of output.

Each failure is logged with its exception type, for example `edge-tts (plain) failed: NoAudioReceived: ...`.

## Problems met and how they were handled
1. **DAVE / E2EE requirement.** Voice failing to connect is avoided by using discord.py >= 2.7.1 with `davey`. Confirmed: the handshake completes.
2. **edge-tts `NoAudioReceived`.** Known causes:
   - Microsoft-side throttling or changes to the undocumented endpoint
   - Incorrect PC clock (the request signature is time-based)
   - Network or proxy blocking `speech.platform.bing.com`
   - Parameter or voice mismatch

   Mitigations:
   - The fallback chain above
   - Checking that Windows time sync is on

   **Next diagnostic step:** read the per-engine failure lines in the console.
3. **Stale deployment.** The first fix did not reach the PC because old copies of the files were written. It was re-deployed and verified by file size: `bot.py` is 9163 bytes and `requirements.txt` includes `gTTS>=2.5`. The new `start.bat` prints "Checking bot packages..." on start.
4. **Token exposure.** The first token was pasted into a chat. The owner was told to **Reset Token** in the Developer Portal and put only the new one into `.env`. Assume the old token is compromised.
6. **Silent playback (DAVE not ready).** Clips "played" (ffmpeg exit 0, about 4 s) but nobody heard them. The diagnostic log showed `DAVE: v1 ready=False`. Fix: pin `davey==0.1.7` (0.2.0 changed the session/transition API, adding `prepare_transition`/`execute_transition`, which discord.py 2.7.1 does not call), and wait up to 5 s for `_connection.can_encrypt` before `vc.play`. The bot logs `clip: … | DAVE: …` and `played for …s` for each message, and saves `last_clip.mp3` for local listening.
5. **Windows setup friction.** Manual Python and ffmpeg installs were removed by automating them in `start.bat` (winget plus the bundled ffmpeg).

## Latency optimisation (added after 5–7 s delays)
- `synthesise()` now **races** edge-tts (3 s timeout) against gTTS. If Microsoft answers in time it is used; otherwise the already-running Google request is used.
- **Circuit breaker:** after **2 consecutive** Microsoft failures (`EDGE_FAILS_TO_SKIP`), Microsoft is skipped for **2 minutes** (`EDGE_COOLDOWN`), so the bot goes straight to Google. It was 1 failure → 10 min.
  - The owner reported that male/female did not switch. Google has only a female Thai voice, and one cold-start timeout on Render had locked everyone onto Google for 10 minutes.
- **Male voice on Google:** imitated by lowering the pitch (`MALE_FALLBACK_PITCH` = 0.78 in `speed_pitch_filter`). It is clearly deeper, but not a real male voice.
- The log shows `via microsoft (male)` / `via google (male, imitated)` / `via google (female)`.
- **Pipelining:** synthesis starts in `enqueue()` (when the message arrives), not when the player reaches it, so the next clip is prepared while the current one plays.
- Each message logs `ready in X.Xs via microsoft|google`.
- The `last_clip.mp3` diagnostic copy was removed.

## Constraints and caveats
- **Uptime:** on Windows the bot is online only while the host PC is on and `start.bat` is open. **Render free hosting works** (verified 27 Nov 2026, service `thai-tts-bot.onrender.com`, Singapore). Voice connects and plays; the owner hears speed/voice changes. Remaining risks: shared IPs can get Discord `429` blocks, and the free instance sleeps after 15 min without inbound HTTP (hence the cron ping to `/health`). Cloudflare Workers / Vercel cannot host this bot (no persistent process, no UDP); Railway / Fly.io have no usable free tier.
- Never run two copies with the same token (PC + cloud): both would read every message.
- **edge-tts** uses Microsoft's undocumented Edge "Read Aloud" endpoint. It is free but unofficial, and it can break without notice. **gTTS** is the same kind of unofficial endpoint, for Google Translate.
- **Privileged intents** (Message Content) need Discord review only once the bot reaches 10,000+ users. That is not relevant here.
- **Privacy:** the bot reads members' messages aloud. Server members should be told that a TTS bot is active in the bound channel. Messages and audio are never stored; only `/myvoice` preferences (user ID → voice/speed/pitch) and the `/voice` server default are saved (local file, plus MongoDB Atlas if configured).

## Ideas / possible next steps
- Run an optional command such as `py -m edge_tts --voice th-TH-PremwadeeNeural --text "ทดสอบ" --write-media test.mp3` on the host to isolate edge-tts problems from the bot itself.
- Add an option to read only messages from members who are currently in the voice channel.
- **Not yet tested live:** `/myvoice` / `/resetvoice` and the MongoDB storage (written on a PC without Python). Check that the commands appear after the global sync, that out-of-range values are rejected, and that the gTTS fallback follows each member's speed/pitch.
- Free always-on hosting: Oracle Cloud Always Free VM (needs card verification), or a spare PC or Raspberry Pi.
- An offline Thai TTS engine as a final fallback, if both free online services prove unreliable.

## Rules for any agent working on this repo
- Never read, print, log or commit `.env`, the token or `MONGODB_URI` (it contains the database password).
- Keep everything free. Do not introduce paid APIs. The only secrets are the Discord token and the optional `MONGODB_URI`, a free Atlas M0 database the owner approved.
- Keep Thai (th-TH) as the only speech language.
- Keep the Windows one-click flow (`start.bat`) working.
- When changing dependencies, make sure Windows wheels exist for Python 3.12.
