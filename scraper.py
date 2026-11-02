import requests
import time
import threading
import os
import functools

print = functools.partial(print, flush=True)

SERVER_URL = "https://luckyloop-ttrr.onrender.com"
# এখন Microworkers এ লগইন ও ডেটা আনার কাজ করে ফোনের ব্রাউজার এক্সটেনশন (LuckyLoop Sync)।
# তাই সার্ভারে কোনো কুকি লাগে না। নিচের লাইনটা শুধু পুরোনো কোডের সামঞ্জস্যের জন্য।
PHPSESSID  = os.environ.get("MW_PHPSESSID", "").strip()

# ── ntfy নোটিফিকেশন ──────────────────────────────────
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()   # ঐচ্ছিক: ntfy অ্যাকাউন্টের access token
# কত সেকেন্ড পর পর নোটিফিকেশন যাবে (মাঝের সব বৃদ্ধি একসাথে যোগ হয়ে যাবে)
try:
    NOTIFY_INTERVAL = int(os.environ.get("NOTIFY_INTERVAL_SEC", "600"))
except ValueError:
    NOTIFY_INTERVAL = 600

# নোটিফিকেশন পাঠাতে ওই সময়ের মধ্যে কমপক্ষে কত বৃদ্ধি লাগবে (এর কম হলে মেসেজ যাবে না)
try:
    MIN_NOTIFY_DIFF = int(os.environ.get("MIN_NOTIFY_DIFF", "10"))
except ValueError:
    MIN_NOTIFY_DIFF = 10

# ── Pushover (মূল নোটিফিকেশন) ──
PO_USER  = os.environ.get("PUSHOVER_USER_KEY", "").strip()
PO_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "").strip()
PO_SOUND = os.environ.get("PUSHOVER_SOUND", "").strip()        # ঐচ্ছিক, যেমন: siren
def _int_env(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
PO_PRIORITY = _int_env("PUSHOVER_PRIORITY", 2)     # 2 = জরুরি অ্যালার্ম (বারবার বাজে), 1 = high, 0 = সাধারণ
PO_RETRY    = max(_int_env("PUSHOVER_RETRY_SEC", 60), 30)      # কত সেকেন্ড পর পর বাজবে (সর্বনিম্ন ৩০)
PO_EXPIRE   = min(_int_env("PUSHOVER_EXPIRE_SEC", 600), 10800) # কতক্ষণ পর্যন্ত বাজতে থাকবে (সর্বোচ্চ ৩ ঘণ্টা)

# একটা ঘটনার জন্য কতবার মেসেজ পাঠাবে ও কত সেকেন্ড পর পর (সাউন্ড টানা বাজানোর জন্য)
# ১০ বার × ৬ সেকেন্ড ≈ ১ মিনিট। ১ দিলে শুধু একবার যাবে।
PO_REPEAT_COUNT = min(max(_int_env("PUSHOVER_REPEAT_COUNT", 10), 1), 20)
PO_REPEAT_GAP   = max(_int_env("PUSHOVER_REPEAT_GAP_SEC", 6), 3)

# ── Pushover ON/OFF সুইচ (ওয়েবসাইটের বাটন থেকে নিয়ন্ত্রিত) ──
_po_toggle_cache = {"enabled": True, "checked_at": 0}
PO_TOGGLE_TTL = 5   # প্রতি ৫ সেকেন্ডে সার্ভার থেকে নতুন অবস্থা নেবে

def pushover_enabled():
    """সার্ভার থেকে Pushover ON/OFF অবস্থা নাও। সার্ভারে না পৌঁছালে শেষ জানা অবস্থা ব্যবহার করবে।"""
    now = time.time()
    if now - _po_toggle_cache["checked_at"] < PO_TOGGLE_TTL:
        return _po_toggle_cache["enabled"]
    try:
        r = requests.get(f"{SERVER_URL}/api/pushover", timeout=5)
        if r.status_code == 200:
            _po_toggle_cache["enabled"] = bool(r.json().get("enabled", True))
    except Exception as e:
        print(f"[pushover-toggle] Error: {type(e).__name__}")
    _po_toggle_cache["checked_at"] = now
    return _po_toggle_cache["enabled"]

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()   # ঐচ্ছিক
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "").strip()     # ঐচ্ছিক

last_sent_value = None   # যে সংখ্যা পর্যন্ত নোটিফিকেশন সফলভাবে গেছে
last_send_time = 0

def send_ntfy(title, message):
    if not NTFY_TOPIC:
        return False
    headers = {"Title": title, "Priority": "max"}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        print(f"[ntfy] Sent '{message}' -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"[ntfy] Response: {r.text[:150]}")
        return r.status_code == 200
    except Exception as e:
        print(f"[ntfy] Error: {e}")
        return False

def send_telegram(message):
    """ঐচ্ছিক: সেট না থাকলে চুপচাপ বাদ"""
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": message},
            timeout=10,
        )
        print(f"[telegram] HTTP {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"[telegram] Error: {type(e).__name__}")
        return False

def send_pushover(title, message, priority=None):
    if not PO_USER or not PO_TOKEN:
        return False
    # ওয়েবসাইটে Pushover OFF থাকলে মেসেজ পাঠাবে না
    if not pushover_enabled():
        print("[pushover] OFF — message skipped")
        return False
    # টানা ~১ মিনিট বাজানোর জন্য: কয়েক সেকেন্ড পর পর একই মেসেজ আবার পাঠানো হয়
    if priority is None and PO_REPEAT_COUNT > 1:
        ok = _po_post(title, message, 1)
        if ok:
            threading.Thread(target=_po_repeat, args=(title, message), daemon=True).start()
        return ok
    prio = PO_PRIORITY if priority is None else priority
    return _po_post(title, message, prio)

def _po_repeat(title, message):
    for n in range(2, PO_REPEAT_COUNT + 1):
        time.sleep(PO_REPEAT_GAP)
        if not pushover_enabled():
            print("[pushover] OFF — repeat stopped")
            return
        _po_post(title, message, 1)

def _po_post(title, message, prio):
    data = {"token": PO_TOKEN, "user": PO_USER, "title": title,
            "message": message, "priority": prio}
    if prio == 2:
        data["retry"] = PO_RETRY
        data["expire"] = PO_EXPIRE
    if PO_SOUND:
        data["sound"] = PO_SOUND
    try:
        r = requests.post("https://api.pushover.net/1/messages.json", data=data, timeout=10)
        print(f"[pushover] Sent '{message}' (priority {prio}) -> HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"[pushover] Response: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"[pushover] Error: {type(e).__name__}")
        return False

def send_notification(title, message, priority=None):
    """Pushover, ntfy, Telegram - যেগুলো সেট করা আছে সবগুলোতে পাঠাও"""
    results = [
        send_pushover(title, message, priority),
        send_ntfy(title, message),
        send_telegram(f"{title}\n{message}"),
    ]
    if not (PO_USER and PO_TOKEN) and not NTFY_TOPIC and not (TG_TOKEN and TG_CHAT):
        print("[notify] কোনো নোটিফিকেশন মাধ্যম সেট করা নেই")
    return any(results)

def check_paid_change(new):
    """সংখ্যা বাড়লে নোটিফিকেশন দাও। NOTIFY_INTERVAL সেকেন্ডের মধ্যে একবারের বেশি নয়।
    পাঠাতে ব্যর্থ হলে বৃদ্ধিটা হারায় না, পরের চেকে আবার চেষ্টা করে।"""
    global last_sent_value, last_send_time
    if last_sent_value is None:
        last_sent_value = new
        return
    if new < last_sent_value:
        last_sent_value = new
        return
    if new == last_sent_value:
        return
    if time.time() - last_send_time < NOTIFY_INTERVAL:
        return

    # এই সময়ের বৃদ্ধি MIN_NOTIFY_DIFF এর কম হলে মেসেজ পাঠাবে না,
    # শুধু হিসাব এগিয়ে রেখে নতুন ১০ মিনিটের হিসাব শুরু করবে
    if new - last_sent_value < MIN_NOTIFY_DIFF:
        print(f"[notify] +{new - last_sent_value} (< {MIN_NOTIFY_DIFF}) — message skipped")
        last_sent_value = new
        last_send_time = time.time()
        return

    # Pushover OFF আর অন্য কোনো মাধ্যম নেই হলে: শুধু হিসাব এগিয়ে রাখো,
    # যাতে ON করলে OFF থাকার সময়ের পুরনো বৃদ্ধি একসাথে না যায়
    other_channels = bool(NTFY_TOPIC) or bool(TG_TOKEN and TG_CHAT)
    if not pushover_enabled() and not other_channels:
        last_sent_value = new
        last_send_time = time.time()
        return

    diff = new - last_sent_value
    if send_notification("Microworkers Task Paid!", f"{last_sent_value:,} -> {new:,} (+{diff})"):
        last_sent_value = new
        last_send_time = time.time()
    else:
        print("[notify] Failed, will retry on next check")
# ═════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════
# সার্ভার এখন শুধু এক্সটেনশনের পাঠানো Tasks paid দেখে, বাড়লে নোটিফিকেশন পাঠায়।
# ═════════════════════════════════════════════════════

# (সামঞ্জস্যের জন্য রাখা; এক্সটেনশনেও একই তালিকা আছে)
JOB_NAMES = [
    {"full": "TTV-Data Entry - PC required. Not for mobile phones. (E766-1470)", "short": "1470"},
    {"full": "TTV-Data Entry from images (E502-1033)",                            "short": "1033"},
    {"full": "TTV-Data Entry from images (E1096-1891)",                           "short": "1891"},
    {"full": "TTV-Data Entry from images (E833-1532)",                            "short": "1532"},
    {"full": "TTV-Data Entry - PC required. Not for mobile phones. (E766-2289)",  "short": "2289"},
    {"full": "TTV-Data Entry (E766-1469sv)",                                      "short": "1469"},
    {"full": "TTV-Data Entry from images (E502-1001)",                            "short": "1001"},
]

def calc_available(pos_str):
    try:
        cur, total = pos_str.split("/")
        return str(max(int(total) - int(cur), 0))
    except:
        return "-"

def update_status(status, message):
    try:
        requests.post(f"{SERVER_URL}/api/scraper-status", json={
            "status" : status,
            "message": message
        }, timeout=10)
        print(f"[Status] {status} | {message}")
    except Exception as e:
        print(f"[Status Error] {e}")

def push(cid, position, available, link):
    try:
        requests.post(f"{SERVER_URL}/save", json={
            "job_name" : cid,
            "position" : position,
            "available": available,
            "link"     : link
        }, timeout=10)
        print(f"[Scraper] Pushed {cid} pos={position} avail={available}")
    except Exception as e:
        print(f"[Scraper] Push error: {e}")

# কত সেকেন্ড পর পর সার্ভার নিজের Tasks paid মান দেখবে
WATCH_INTERVAL = max(_int_env("WATCH_INTERVAL_SEC", 20), 5)

def _find_paid(obj):
    """JSON এর ভেতর থেকে 'paid' নামের কী-র সংখ্যা খোঁজো"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "paid" in str(k).lower():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    return int(v)
                if isinstance(v, str) and v.replace(",", "").strip().isdigit():
                    return int(v.replace(",", "").strip())
        for v in obj.values():
            r = _find_paid(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_paid(v)
            if r is not None:
                return r
    return None

def _find_big_int(obj):
    """কী-র নামে না মিললে: প্রথম বড় (>10000) পূর্ণসংখ্যা নাও"""
    if isinstance(obj, bool):
        return None
    if isinstance(obj, int):
        return obj if obj > 10000 else None
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_big_int(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_big_int(v)
            if r is not None:
                return r
    return None

_raw_logged = False

def fetch_tasks_paid():
    """নিজের সার্ভার থেকে সর্বশেষ Tasks paid নাও (এক্সটেনশন এটা POST করে রেখেছে)"""
    global _raw_logged
    r = requests.get(f"{SERVER_URL}/api/tasks-paid", timeout=10)
    if r.status_code != 200:
        print(f"[Notifier] /api/tasks-paid HTTP {r.status_code}")
        return None
    if not _raw_logged:
        print(f"[Notifier] /api/tasks-paid -> {r.text[:200]}")
        _raw_logged = True
    data = r.json()
    value = _find_paid(data)
    if value is None:
        value = _find_big_int(data)
    return value

def watch_loop():
    print("[Notifier] Starting — এক্সটেনশনের পাঠানো Tasks paid দেখছে...")
    print(f"[notify] pushover: {bool(PO_USER and PO_TOKEN)} | ntfy: {bool(NTFY_TOPIC)} | telegram: {bool(TG_TOKEN and TG_CHAT)} | interval: {NOTIFY_INTERVAL}s | priority: {PO_PRIORITY}")
    send_notification("LuckyLoop", "নোটিফায়ার চালু হয়েছে (test)", priority=0)
    time.sleep(5)
    warned = False
    while True:
        try:
            value = fetch_tasks_paid()
            if value is None or value <= 0:
                if not warned:
                    print("[Notifier] Tasks paid সংখ্যা এখনো পাওয়া যায়নি (এক্সটেনশন চালু আছে তো?)")
                    warned = True
            else:
                warned = False
                check_paid_change(value)
        except Exception as e:
            print(f"[Notifier] Error: {type(e).__name__}: {e}")
        time.sleep(WATCH_INTERVAL)

def start_scraper():
    t = threading.Thread(target=watch_loop, daemon=True)
    t.start()
