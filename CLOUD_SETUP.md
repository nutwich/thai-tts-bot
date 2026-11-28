# รันบอทบน Cloud ฟรี (Render + cron ปลุก)

บอทออนไลน์โดยไม่ต้องเปิดคอม ไม่ต้องใช้บัตร ใช้เวลาตั้งค่าประมาณ 15 นาที

> ✅ ทดสอบแล้ว (27 พ.ย. 2026): บอทบน Render เข้าห้องเสียงและพูดได้ปกติ

## 1. สร้างบอทบน Render
1. ต้อง push โค้ดล่าสุดขึ้น GitHub ก่อน (repo เป็น private ได้)
2. ไปที่ https://render.com → **Sign up with GitHub**
3. กด **New → Blueprint** → เลือก repo `thai-tts-bot` (ถ้าไม่เห็น กด *Configure GitHub* ให้สิทธิ์ repo นี้)
4. Render อ่านไฟล์ `render.yaml` แล้วจะถามค่า:
   - **DISCORD_TOKEN** → วาง token บอท (ใส่ตรงนี้ที่เดียว ห้ามใส่ในไฟล์ใน GitHub)
   - **READ_CHANNEL_ID** → เว้นว่างได้ (ดูข้อ 4)
   - **MONGODB_URI** → ลิงก์ฐานข้อมูล MongoDB (ดูข้อ 5) ถ้าไม่ใส่ ค่า /myvoice และ /voice จะหายทุกครั้งที่ Render build ใหม่
   - **GOOGLE_TTS_API_KEY** → เว้นว่างได้ (ดูข้อ 6: เสียง Google Cloud ในโควตาฟรี)
5. กด **Apply** รอ build ประมาณ 3–5 นาที
6. เปิดหน้า service → แท็บ **Logs** ถ้าเห็น `Logged in as บอทท่านจิน#2654` = ออนไลน์แล้ว
7. จดลิงก์ของบอทที่มุมบนซ้าย เช่น `https://thai-tts-bot-xxxx.onrender.com`

> ⚠️ **ปิด `start.bat` บนคอมด้วย** ถ้ารัน 2 ที่พร้อมกันด้วย token เดียว บอทจะอ่านซ้ำ/งง

## 2. ตั้ง cron ปลุกไม่ให้หลับ
Render ฟรีจะหลับถ้าไม่มีคนเข้าลิงก์ 15 นาที เลยต้องให้ cron ยิงทุก 5 นาที
1. ไปที่ https://cron-job.org → สมัครฟรี
2. **Create cronjob**
   - URL: `https://thai-tts-bot-xxxx.onrender.com/health` (ลิงก์จากข้อ 1.7 + `/health`)
   - Execution schedule: **Every 5 minutes**
3. กด **Create** แล้วดูว่าผลเป็น `200 OK`

(ใช้ UptimeRobot แทนก็ได้ ตั้ง HTTP monitor ทุก 5 นาทีที่ลิงก์เดียวกัน)

## 3. Auto join ทำงานยังไง
- คนที่ **อยู่ในห้องเสียง** พิมพ์ใน **แชทของห้องเสียงนั้น** (ไอคอน 💬 ข้างชื่อห้อง) แล้วบอทยังไม่อยู่ในห้อง → บอทเข้าห้องนั้นเองและอ่านข้อความนั้นเลย
- คนที่ไม่ได้อยู่ในห้องเสียงพิมพ์ → บอทไม่เข้า
- บอทอยู่ห้องอื่นอยู่แล้ว → ไม่ย้ายไปแย่ง
- ทุกคน **ออกจากห้อง** → บอทออกตาม (พิมพ์ใหม่ครั้งหน้าก็เข้ามาใหม่)
- ใช้ **`/leave`** → บอทจะไม่กลับเข้ามาเองจนกว่าทุกคนออกจากห้องเสียงหมด (หรือมีคนใช้ `/join`)
- **`/join`** ใช้ได้ตามปกติ
- ไม่เข้าห้อง AFK
- ปิด auto join: ตั้ง `AUTO_JOIN` = `false`

## 4. (ไม่บังคับ) ให้ห้องแชทปกติเรียกบอทได้ด้วย
ถ้าอยากให้พิมพ์ใน **ห้องแชทปกติ** (เช่น #general) แล้วบอทเข้าห้องเสียงที่คนพิมพ์อยู่:
Discord: *User Settings → Advanced → เปิด Developer Mode* → คลิกขวาห้องแชท → **Copy Channel ID** → ใส่ใน `READ_CHANNEL_ID`
(เว้นว่าง = ใช้แค่แชทในห้องเสียง)

## 5. ฐานข้อมูล MongoDB (กันค่า /myvoice และ /voice หาย)
Render ฟรีจะล้างไฟล์ทุกครั้งที่ build ใหม่ บอทเลยเก็บค่าไว้ใน **MongoDB Atlas** แบบฟรี (M0 ฟรีถาวร ไม่ต้องใช้บัตร) ใช้ได้ทุกเซิร์ฟเวอร์ที่บอทอยู่
1. ไปที่ https://www.mongodb.com/cloud/atlas/register → สมัครด้วย Google ก็ได้
2. **Create cluster** → เลือก **M0 / Free** → Provider อะไรก็ได้ → Region **Singapore** (ใกล้ Render) → Create
3. **Database Access** (หรือหน้าที่ขึ้นมาให้สร้าง user) → สร้าง user + กด **Autogenerate password** (รหัสเป็นตัวอักษร/ตัวเลขล้วน) → สิทธิ์ **Read and write to any database**
4. **Network Access** → **Add IP Address** → **Allow access from anywhere** (`0.0.0.0/0`) เพราะ IP ของ Render เปลี่ยนเรื่อย ๆ (รหัสผ่านยังป้องกันอยู่)
5. **Connect** → **Drivers** → Python → copy ลิงก์แบบ `mongodb+srv://ชื่อuser:<db_password>@cluster0.xxxxx.mongodb.net/?...` → เปลี่ยน `<db_password>` เป็นรหัสจากข้อ 3
6. ใส่ลิงก์นี้ใน `MONGODB_URI` (Render: หน้า service → **Environment** / บนคอม: ไฟล์ `.env`)
7. เปิดบอทใหม่ ถ้า Logs ขึ้น `Loaded settings from MongoDB: ...` = ต่อได้แล้ว

> 🔒 ลิงก์นี้มี **รหัสผ่านฐานข้อมูล** อยู่ในตัว เก็บให้ลับเหมือน token บอท ห้ามโพสต์หรือใส่ใน GitHub ถ้าหลุด → Database Access → Edit → เปลี่ยนรหัส แล้วแก้ลิงก์ใหม่

- ใส่ลิงก์เดียวกันทั้งคอมและ Render ได้ ย้ายไปมาค่าก็ตามไป (แต่อย่ารันบอท 2 ที่พร้อมกัน)
- ถ้า MongoDB ล่มชั่วคราว บอทใช้ไฟล์ในเครื่องต่อ แล้วบันทึกขึ้นฐานข้อมูลให้เองเมื่อต่อได้

## 6. (ไม่บังคับ) เสียง Google Cloud เป็นเสียงหลัก — เฉพาะโควตาฟรี
เสียง Chirp 3 HD ของ Google Cloud มีเสียงผู้ชาย/ผู้หญิงจริง และไม่ล่มแบบ Microsoft ฟรีเดือนละ **1,000,000 ตัวอักษร**
บอทจะนับตัวอักษรเอง พอถึง **800,000** (ตั้งได้ที่ `GOOGLE_TTS_MONTHLY_LIMIT`) จะเปลี่ยนไปใช้ Microsoft ฟรี → Google Translate เองจนถึงเดือนหน้า

> ⚠️ Google **ไม่หยุดให้เอง** เมื่อหมดโควตาฟรี แต่จะตัดบัตร บอทเลยต้องนับเอง และต้องมี **MONGODB_URI** (ข้อ 5) ตัวนับจะได้ไม่รีเซ็ตตอน Render build ใหม่ ถ้าบอทอ่านตัวนับไม่ได้ บอทจะไม่ใช้ Google Cloud เลย (กันโดนเก็บเงิน)

1. ไปที่ https://console.cloud.google.com → สร้าง **New Project** (เช่น `thai-tts-bot`)
2. **Billing** → ผูกบัตร (Google บังคับ แม้ใช้แต่ของฟรี)
3. ค้นหา **Cloud Text-to-Speech API** → กด **Enable**
4. **APIs & Services → Credentials → Create credentials → API key**
   - กด **Edit API key** → **API restrictions** → เลือก **Restrict key** → ติ๊กแค่ **Cloud Text-to-Speech API** → Save
5. ตั้งกันพลาดอีกชั้น: **Billing → Budgets & alerts → Create budget** → จำนวน **$1** → แจ้งเตือนอีเมลที่ 50% / 100%
   (Google ยังไม่หยุดเองนะ แค่ส่งเมลเตือน ถ้าได้เมลนี้ให้ลบ `GOOGLE_TTS_API_KEY` ออกจาก Render ทันที)
6. ใส่ key ใน `GOOGLE_TTS_API_KEY` (Render: **Environment** / คอม: `.env`)
7. เปิดบอทใหม่ ถ้า Logs ขึ้น `Google Cloud TTS: on, 0 / 800,000 characters used in ...` = ใช้ได้
   ข้อความจะขึ้นว่า `via google-cloud (Kore, female)` หรือ `(Charon, male)`

> 🔒 API key เป็นความลับเหมือน token บอท ถ้าหลุด → Credentials → ลบ key แล้วสร้างใหม่

- แต่ละคนเลือกเสียง Google เองได้ใน `/myvoice` (พิมพ์ชื่อในช่อง voice แล้วเลือกจากรายการ เช่น `Puck · ผู้ชาย`) ถ้าโควตาหมด บอทจะใช้เสียงฟรีเพศเดียวกันแทน
- Logs ตอนเปิดบอทจะบอกว่ามีเสียงไทยกี่เสียง เช่น `Google Cloud TTS: 30 Thai voices (... female, ... male)`
- เสียงเริ่มต้น (ตอนเลือกแค่ ผู้หญิง/ผู้ชาย) เปลี่ยนได้ที่ `GOOGLE_TTS_FEMALE` / `GOOGLE_TTS_MALE`
- ดูว่าใช้ไปเท่าไหร่: Atlas → `thai_tts_bot` → `usage`
- ปิดใช้ Google Cloud: ลบ `GOOGLE_TTS_API_KEY` ออก → บอทกลับไปใช้แต่เสียงฟรี

แก้ค่าทีหลังได้ที่ Render: หน้า service → **Environment** → แก้ → **Save Changes** (บอทรีสตาร์ทเอง)

## อัปเดตโค้ด
push ขึ้น GitHub branch `main` → Render build ใหม่ให้อัตโนมัติ (บอทหลุดจากห้องแป๊บนึง พิมพ์ในแชทห้องเสียงอีกทีก็เข้ามาใหม่)

## ปัญหาที่อาจเจอ
- **เข้าห้องได้แต่ไม่มีเสียง** → Render อาจบล็อก UDP ใช้เครื่อง Linux ด้านล่างแทน
- **Logs ขึ้น `429` / `You are being blocked`** → IP ของ Render ใช้ร่วมกับคนอื่นแล้วโดน Discord บล็อกชั่วคราว ลองกด **Manual Deploy → Restart** ให้ได้เครื่องใหม่ หรือรอสักพัก
- **บอทหลุดบ่อย** → เช็กใน cron-job.org ว่ายังยิงผ่าน (`200 OK`) อยู่ไหม
- **Logs ขึ้น `Microsoft voice unavailable`** → บอทสลับไปใช้เสียง Google ให้เอง ยังใช้ได้
- **Logs ขึ้น `SSL handshake failed ... TLSV1_ALERT_INTERNAL_ERROR`** (MongoDB) → Atlas ไม่ยอมให้ IP ของ Render เข้า ไปที่ Atlas → **Network Access** → **Add IP Address** → **Allow access from anywhere** (`0.0.0.0/0`) รอจนสถานะเป็น **Active** บอทจะลองบันทึกใหม่เองภายในไม่กี่นาที
- การใช้ cron ปลุกเป็นช่องที่ Render อาจปิดในอนาคต ถ้าวันนึงใช้ไม่ได้ ไปใช้เครื่อง Linux แทน

---

## ทางเลือก: เครื่อง Linux (Google Cloud / คอมเก่า)
เครื่องที่ลง **Ubuntu 24.04** ได้ (เช่น Google Cloud e2-micro ฟรี หรือคอมเก่า):
```bash
git clone https://github.com/nutwich/thai-tts-bot.git
cd thai-tts-bot
bash setup.sh        # ครั้งแรกจะให้ใส่ token ด้วย: nano .env แล้วรันซ้ำ
```
บอทจะรันเป็น service เปิดเองตอนบูต รีสตาร์ทเองถ้าล่ม ไม่ต้องใช้ cron

| อยากทำอะไร | คำสั่ง |
|---|---|
| ดู log สด ๆ | `sudo journalctl -u thai-tts-bot -f` |
| รีสตาร์ท | `sudo systemctl restart thai-tts-bot` |
| หยุด | `sudo systemctl stop thai-tts-bot` |
| อัปเดต | `cd ~/thai-tts-bot && git pull && bash setup.sh` |
| แก้การตั้งค่า | `nano ~/thai-tts-bot/.env` แล้วรีสตาร์ท |
