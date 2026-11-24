# รันบอทบน Cloud ฟรี (Render + cron ปลุก)

บอทออนไลน์โดยไม่ต้องเปิดคอม ไม่ต้องใช้บัตร ใช้เวลาตั้งค่าประมาณ 15 นาที

> ⚠️ **ยังไม่ได้ทดสอบเสียงบน Render จริง** ถ้าบอทเข้าห้องได้แต่ไม่มีเสียง แปลว่า Render ไม่ยอมให้ส่งเสียง (UDP) → ไปใช้ [เครื่อง Linux](#ทางเลือก-เครื่อง-linux-google-cloud--คอมเก่า) แทน

## 1. สร้างบอทบน Render
1. ต้อง push โค้ดล่าสุดขึ้น GitHub ก่อน (repo เป็น private ได้)
2. ไปที่ https://render.com → **Sign up with GitHub**
3. กด **New → Blueprint** → เลือก repo `thai-tts-bot` (ถ้าไม่เห็น กด *Configure GitHub* ให้สิทธิ์ repo นี้)
4. Render อ่านไฟล์ `render.yaml` แล้วจะถามค่า:
   - **DISCORD_TOKEN** → วาง token บอท (ใส่ตรงนี้ที่เดียว ห้ามใส่ในไฟล์ใน GitHub)
   - **READ_CHANNEL_ID** → เว้นว่างได้ (ดูข้อ 4)
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

แก้ค่าทีหลังได้ที่ Render: หน้า service → **Environment** → แก้ → **Save Changes** (บอทรีสตาร์ทเอง)

## อัปเดตโค้ด
push ขึ้น GitHub branch `main` → Render build ใหม่ให้อัตโนมัติ (บอทหลุดจากห้องแป๊บนึง พิมพ์ในแชทห้องเสียงอีกทีก็เข้ามาใหม่)

## ปัญหาที่อาจเจอ
- **เข้าห้องได้แต่ไม่มีเสียง** → Render อาจบล็อก UDP ใช้เครื่อง Linux ด้านล่างแทน
- **Logs ขึ้น `429` / `You are being blocked`** → IP ของ Render ใช้ร่วมกับคนอื่นแล้วโดน Discord บล็อกชั่วคราว ลองกด **Manual Deploy → Restart** ให้ได้เครื่องใหม่ หรือรอสักพัก
- **บอทหลุดบ่อย** → เช็กใน cron-job.org ว่ายังยิงผ่าน (`200 OK`) อยู่ไหม
- **Logs ขึ้น `Microsoft voice unavailable`** → บอทสลับไปใช้เสียง Google ให้เอง ยังใช้ได้
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
