# LuckyLoop Job Tracker

## Render.com এ Deploy করার ধাপ

### ধাপ ১: GitHub এ Upload করো
1. github.com এ নতুন repository বানাও (নাম: `luckyloop-tracker`)
2. এই সব files সেই repo তে upload করো

### ধাপ ২: Render.com এ Deploy করো
1. render.com এ যাও → GitHub দিয়ে Sign Up
2. **New → Web Service** চাপো
3. তোমার GitHub repo select করো
4. নিচের settings দাও:

| Setting | Value |
|---|---|
| Environment | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app` |

5. **Environment Variables** এ যোগ করো:
   - Key: `DB_PATH` → Value: `/data`

6. **Disk** section এ:
   - Mount Path: `/data`
   - Size: 1 GB

7. **Deploy** চাপো → কিছুক্ষণ পর তোমার URL পাবে

### ধাপ ৩: background.js আপডেট করো
Render দেওয়া URL টা background.js এর SERVER_URL এ paste করো।

## Local এ চালাতে চাইলে
```
pip install flask gunicorn
python app.py
```
