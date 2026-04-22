# ติดตั้ง Admit Watcher

## ไฟล์ที่ได้รับ
- `admit_watcher.py` — daemon หลัก
- `admit-watcher.service` — systemd service

---

## ขั้นตอนติดตั้ง

### 1. วางไฟล์
```bash
sudo cp admit_watcher.py   /opt/hospital-food/
sudo cp admit-watcher.service /etc/systemd/system/
```

### 2. แก้ไข config.ini (ไฟล์เดิม)
```ini
[database]
host     = 192.168.x.x
port     = 5432
dbname   = hosxp_pcu
user     = dbuser
password = dbpassword

[moph]
client_key = YOUR_CLIENT_KEY
secret_key  = YOUR_SECRET_KEY
```

### 3. แก้ path ใน service file
```bash
sudo nano /etc/systemd/system/admit-watcher.service
# แก้ WorkingDirectory และ ExecStart ให้ตรงกับ path จริง
```

### 4. เปิดใช้งาน
```bash
sudo systemctl daemon-reload
sudo systemctl enable admit-watcher    # เปิดตอน boot
sudo systemctl start admit-watcher
sudo systemctl status admit-watcher    # ตรวจสถานะ
```

### 5. ดู log แบบ realtime
```bash
journalctl -u admit-watcher -f
# หรือ
tail -f /opt/hospital-food/admit_watcher.log
```

---

## การทำงาน

```
ทุก 60 วินาที
    ↓
ดึง SQL → รายชื่อ Admit ปัจจุบัน (dchdate IS NULL)
    ↓
เทียบกับ seen_ans.json
    ↓
พบ AN ใหม่ → ส่ง MOPH LINE Notify ทันที
    ↓
บันทึก AN ลง seen_ans.json (ไม่แจ้งซ้ำ)
```

### ตัวอย่าง notification ที่ได้รับ:
```
🏥 ผู้ป่วยรับเข้า (Admit)
──────────────────────
👤 นางสมศรีXX สมใจXX
🪪  HN: 123456  |  AN: 690004317
🏢  อายุรกรรม
🛏  ห้องพิเศษ 201  เตียง A
📝  ศาสนา: พุทธ
🕒  Admit: 22/04/2568 10:30:00
──────────────────────
```

---

## คำสั่งมีประโยชน์

```bash
# หยุด
sudo systemctl stop admit-watcher

# รีสตาร์ท
sudo systemctl restart admit-watcher

# ล้าง seen_ans.json (จะแจ้งใหม่ทุกคน)
rm /opt/hospital-food/seen_ans.json

# ปรับความถี่ poll (แก้ใน admit_watcher.py บรรทัด POLL_INTERVAL)
POLL_INTERVAL = 30   # ทุก 30 วินาที
```

---

## ข้อแตกต่างจาก food_alert.py เดิม

| | food_alert.py | admit_watcher.py |
|---|---|---|
| รูปแบบ | รันครั้งเดียว | daemon รัน 24/7 |
| trigger | manual / cron | event-driven (ทันทีที่ Admit) |
| ซ้ำ? | แจ้งทุกครั้งที่รัน | ไม่ซ้ำ (seen_ans.json) |
| ต้องเปิดเว็บ | ไม่ | ไม่ |
