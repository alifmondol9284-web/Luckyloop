from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
import sqlite3
from datetime import datetime, timedelta
import os
import secrets
import string

app = Flask(__name__)
CORS(app)

app.secret_key = os.environ.get("SECRET_KEY", "luckyloop_secret_key_2024_xk92")

DB = os.path.join(os.environ.get("DB_PATH", "."), "jobs.db")

ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "luckyloop_admin_2024")
VIEWER_PASSWORD = os.environ.get("VIEWER_PASSWORD", "luckyloop2024")


def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name   TEXT    UNIQUE NOT NULL,
            position   TEXT,
            available  TEXT,
            link       TEXT,
            updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scraper_status (
            id         INTEGER PRIMARY KEY,
            status     TEXT,
            message    TEXT,
            updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id    TEXT PRIMARY KEY,
            device_name  TEXT,
            license_key  TEXT,
            license_type TEXT,
            ip_address   TEXT,
            first_seen   TEXT,
            last_seen    TEXT,
            is_blocked   INTEGER DEFAULT 0,
            block_reason TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS license_keys (
            key_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key    TEXT    UNIQUE NOT NULL,
            label          TEXT,
            display_name   TEXT    DEFAULT '',
            max_devices    INTEGER DEFAULT 1,
            bound_device   TEXT,
            created_at     TEXT,
            activated_at   TEXT,
            expires_at     TEXT    DEFAULT NULL,
            validity_days  INTEGER DEFAULT NULL,
            is_active      INTEGER DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS announcement (
            id         INTEGER PRIMARY KEY,
            enabled    INTEGER DEFAULT 0,
            message    TEXT    DEFAULT '',
            updated_at TEXT    DEFAULT ''
        )
    """)
    c.execute("""
        INSERT OR IGNORE INTO announcement (id, enabled, message, updated_at)
        VALUES (1, 0, '', '')
    """)

    # ── NEW: tasks_paid current value ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks_paid_current (
            id          INTEGER PRIMARY KEY,
            tasks_paid  INTEGER DEFAULT 0,
            updated_at  TEXT
        )
    """)
    c.execute("INSERT OR IGNORE INTO tasks_paid_current (id, tasks_paid, updated_at) VALUES (1, 0, '')")

    # ── NEW: tasks_paid change log ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks_paid_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tasks_paid  INTEGER,
            diff        INTEGER,
            logged_at   TEXT
        )
    """)

    # ── NEW: Pushover ON/OFF setting (default ON) ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS pushover_setting (
            id         INTEGER PRIMARY KEY,
            enabled    INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)
    c.execute("INSERT OR IGNORE INTO pushover_setting (id, enabled, updated_at) VALUES (1, 1, '')")

    # ── Migrate existing tables if columns missing ──
    cols = [row[1] for row in c.execute("PRAGMA table_info(jobs)")]
    if "updated_at" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")

    lic_cols = [row[1] for row in c.execute("PRAGMA table_info(license_keys)")]
    if "expires_at" not in lic_cols:
        c.execute("ALTER TABLE license_keys ADD COLUMN expires_at TEXT DEFAULT NULL")
    if "validity_days" not in lic_cols:
        c.execute("ALTER TABLE license_keys ADD COLUMN validity_days INTEGER DEFAULT NULL")
    if "display_name" not in lic_cols:
        c.execute("ALTER TABLE license_keys ADD COLUMN display_name TEXT DEFAULT ''")

    conn.commit()
    conn.close()
    print("[DB] Ready")


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def is_viewer_logged_in():
    return session.get("viewer_logged_in") is True


def check_admin(req):
    pw = req.headers.get("X-Admin-Password") or req.args.get("password") or ""
    return pw == ADMIN_PASSWORD


def generate_license_key(label=""):
    chars  = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(16))
    prefix = label.replace(" ", "-")[:12].upper() if label else "USER"
    return f"LL-{prefix}-{suffix}"


def days_remaining(expires_at_str):
    if not expires_at_str:
        return None
    try:
        expires = datetime.fromisoformat(expires_at_str)
        delta = expires - datetime.now()
        return delta.days
    except:
        return None


# ══════════════════════════════════════════════════════════
#  VIEWER LOGIN/LOGOUT
# ══════════════════════════════════════════════════════════

@app.route("/viewer-login", methods=["POST"])
def viewer_login():
    data = request.get_json(silent=True) or {}
    pw = str(data.get("password", "") or "").strip()
    if pw == VIEWER_PASSWORD:
        session["viewer_logged_in"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "ভুল পাসওয়ার্ড"}), 401


@app.route("/viewer-logout")
def viewer_logout():
    session.pop("viewer_logged_in", None)
    return redirect("/latest")


# ══════════════════════════════════════════════════════════
#  PROTECTED ROUTES
# ══════════════════════════════════════════════════════════

@app.route("/")
def home():
    if not is_viewer_logged_in():
        return render_template("latest.html", locked=True)
    return render_template("index.html")


@app.route("/latest")
def latest():
    if not is_viewer_logged_in():
        return render_template("latest.html", locked=True)
    return render_template("latest.html", locked=False)


@app.route("/api/latest")
def api_latest():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY updated_at DESC"
    ).fetchall()
    status_row = conn.execute(
        "SELECT * FROM scraper_status WHERE id=1"
    ).fetchone()
    conn.close()
    scraper_ok  = True
    scraper_msg = "OK"
    if status_row:
        scraper_ok  = status_row["status"] == "ok"
        scraper_msg = status_row["message"]
    return jsonify({
        "jobs":        [dict(r) for r in rows],
        "scraper_ok":  scraper_ok,
        "scraper_msg": scraper_msg
    })


# ══════════════════════════════════════════════════════════
#  TASKS PAID API — নতুন
# ══════════════════════════════════════════════════════════

@app.route("/api/tasks-paid", methods=["POST"])
def update_tasks_paid():
    """scraper.py থেকে push হবে — Tasks paid বাড়লে log করবে"""
    data = request.get_json(silent=True) or {}
    new_val = data.get("tasks_paid")
    if new_val is None:
        return jsonify({"error": "tasks_paid required"}), 400
    try:
        new_val = int(new_val)
    except:
        return jsonify({"error": "invalid value"}), 400

    now = datetime.now().isoformat()
    conn = get_db()

    # আগের value নাও
    current_row = conn.execute(
        "SELECT tasks_paid FROM tasks_paid_current WHERE id=1"
    ).fetchone()
    old_val = current_row["tasks_paid"] if current_row else 0

    # Update current
    conn.execute(
        "UPDATE tasks_paid_current SET tasks_paid=?, updated_at=? WHERE id=1",
        (new_val, now)
    )

    # বাড়লে log করো
    diff = new_val - old_val
    if diff > 0:
        conn.execute(
            "INSERT INTO tasks_paid_log (tasks_paid, diff, logged_at) VALUES (?, ?, ?)",
            (new_val, diff, now)
        )
        print(f"[TasksPaid] +{diff} → {new_val:,}")

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "diff": diff})


@app.route("/api/tasks-paid", methods=["GET"])
def get_tasks_paid():
    """latest.html এ দেখাবে — current value + last 10 logs"""
    conn = get_db()
    current = conn.execute(
        "SELECT tasks_paid, updated_at FROM tasks_paid_current WHERE id=1"
    ).fetchone()
    logs = conn.execute(
        "SELECT tasks_paid, diff, logged_at FROM tasks_paid_log ORDER BY id DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return jsonify({
        "tasks_paid": current["tasks_paid"] if current else 0,
        "updated_at": current["updated_at"] if current else "",
        "logs": [dict(r) for r in logs]
    })


# ══════════════════════════════════════════════════════════
#  PUSHOVER ON/OFF API — নতুন
# ══════════════════════════════════════════════════════════

@app.route("/api/pushover", methods=["GET"])
def get_pushover():
    """scraper.py ও latest.html দুটোই এটা পড়বে (শুধু true/false দেখায়)"""
    conn = get_db()
    row = conn.execute("SELECT enabled FROM pushover_setting WHERE id=1").fetchone()
    conn.close()
    return jsonify({"enabled": bool(row["enabled"]) if row else True})


@app.route("/api/pushover", methods=["POST"])
def set_pushover():
    """শুধু লগইন করা viewer (বা admin) ON/OFF করতে পারবে"""
    if not (is_viewer_logged_in() or check_admin(request)):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    enabled = 1 if data.get("enabled") else 0
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE pushover_setting SET enabled=?, updated_at=? WHERE id=1",
        (enabled, now)
    )
    conn.commit()
    conn.close()
    print(f"[Pushover] {'ON' if enabled else 'OFF'}")
    return jsonify({"ok": True, "enabled": bool(enabled)})


# ══════════════════════════════════════════════════════════
#  USER INFO API
# ══════════════════════════════════════════════════════════

@app.route("/api/user-info", methods=["POST"])
def user_info():
    data        = request.get_json(silent=True) or {}
    device_id   = str(data.get("device_id",   "") or "").strip()
    license_key = str(data.get("license_key", "") or "").strip()

    if not license_key:
        return jsonify({"ok": False, "expired": False, "message": "No license key"}), 400

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM license_keys WHERE license_key=?", (license_key,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"ok": False, "expired": True, "message": "License not found"})

    if not row["is_active"]:
        return jsonify({"ok": False, "expired": True, "message": "License deactivated"})

    remaining = days_remaining(row["expires_at"])

    if remaining is not None and remaining < 0:
        return jsonify({
            "ok":           False,
            "expired":      True,
            "message":      "Your license has expired. Please contact admin.",
            "days_left":    0,
            "display_name": row["display_name"] or row["label"] or "User",
        })

    return jsonify({
        "ok":           True,
        "expired":      False,
        "display_name": row["display_name"] or row["label"] or "User",
        "days_left":    remaining,
        "label":        row["label"] or "",
        "expires_at":   row["expires_at"] or None,
    })


# ══════════════════════════════════════════════════════════
#  SCRAPER STATUS
# ══════════════════════════════════════════════════════════

@app.route("/api/scraper-status", methods=["POST"])
def update_scraper_status():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "no data"}), 400
    status  = data.get("status", "ok")
    message = data.get("message", "")
    now     = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO scraper_status (id, status, message, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status     = excluded.status,
            message    = excluded.message,
            updated_at = excluded.updated_at
    """, (status, message, now))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/save", methods=["POST", "OPTIONS"])
def save_job():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No JSON body received"}), 400
    job_name  = str(data.get("job_name",  "") or "").strip()
    position  = str(data.get("position",  "") or "").strip()
    available = str(data.get("available", "") or "").strip()
    link      = str(data.get("link",      "") or "").strip()
    now       = datetime.now().isoformat()
    if not job_name:
        return jsonify({"status": "error", "message": "job_name is required"}), 400
    conn = get_db()
    conn.execute("""
        INSERT INTO jobs (job_name, position, available, link, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(job_name) DO UPDATE SET
            position   = excluded.position,
            available  = excluded.available,
            link       = excluded.link,
            updated_at = excluded.updated_at
    """, (job_name, position, available, link, now))
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "job_name": job_name})


# ══════════════════════════════════════════════════════════
#  LINK → CAMPAIGN CHECKER
# ══════════════════════════════════════════════════════════

@app.route("/api/check-link", methods=["GET"])
def check_link():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"found": False, "campaign": None})
    task_id = url.rstrip("/").split("/")[-1]
    conn = get_db()
    rows = conn.execute("SELECT job_name, link FROM jobs").fetchall()
    conn.close()
    for row in rows:
        db_link    = (row["link"] or "").rstrip("/")
        db_task_id = db_link.split("/")[-1]
        if db_task_id and db_task_id == task_id:
            return jsonify({"found": True, "campaign": row["job_name"]})
    return jsonify({"found": False, "campaign": None})


# ══════════════════════════════════════════════════════════
#  DEVICE MANAGEMENT
# ══════════════════════════════════════════════════════════

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False, "blocked": False}), 400
    device_id    = str(data.get("device_id",    "") or "").strip()
    device_name  = str(data.get("device_name",  "") or "Unknown").strip()
    license_key  = str(data.get("license_key",  "") or "").strip()
    license_type = str(data.get("license_type", "") or "").strip()
    ip_address   = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    now          = datetime.now().isoformat()
    if not device_id:
        return jsonify({"ok": False, "blocked": False, "reason": "no device_id"}), 400
    conn = get_db()
    existing = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    if existing:
        conn.execute("""
            UPDATE devices SET device_name=?, license_key=?, license_type=?, ip_address=?, last_seen=?
            WHERE device_id=?
        """, (device_name, license_key, license_type, ip_address, now, device_id))
        is_blocked   = bool(existing["is_blocked"])
        block_reason = existing["block_reason"] or ""
    else:
        conn.execute("""
            INSERT INTO devices (device_id, device_name, license_key, license_type, ip_address, first_seen, last_seen, is_blocked)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (device_id, device_name, license_key, license_type, ip_address, now, now))
        is_blocked   = False
        block_reason = ""
    conn.commit()
    conn.close()
    if is_blocked:
        return jsonify({"ok": False, "blocked": True, "reason": block_reason or "আপনার device block করা হয়েছে।"})
    return jsonify({"ok": True, "blocked": False})


@app.route("/api/check/<device_id>", methods=["GET"])
def check_device(device_id):
    conn = get_db()
    row = conn.execute("SELECT is_blocked, block_reason FROM devices WHERE device_id=?", (device_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"ok": True, "blocked": False})
    if row["is_blocked"]:
        return jsonify({"ok": False, "blocked": True, "reason": row["block_reason"] or "আপনার device block করা হয়েছে।"})
    return jsonify({"ok": True, "blocked": False})


# ══════════════════════════════════════════════════════════
#  LICENSE KEY API
# ══════════════════════════════════════════════════════════

@app.route("/api/license/verify", methods=["POST"])
def license_verify():
    data = request.get_json(silent=True) or {}
    license_key = str(data.get("license_key", "") or "").strip()
    device_id   = str(data.get("device_id",   "") or "").strip()
    device_name = str(data.get("device_name", "") or "Unknown").strip()
    if not license_key or not device_id:
        return jsonify({"ok": False, "valid": False, "message": "❌ Missing license_key or device_id"}), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM license_keys WHERE license_key=?", (license_key,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "valid": False, "message": "❌ Invalid License Key!"})
    if not row["is_active"]:
        conn.close()
        return jsonify({"ok": False, "valid": False, "message": "❌ This license key has been deactivated!"})
    bound = row["bound_device"]
    now   = datetime.now().isoformat()
    if bound and bound != device_id:
        conn.close()
        return jsonify({"ok": False, "valid": False, "message": "❌ This license key is already activated on another device! Contact admin."})
    remaining = days_remaining(row["expires_at"])
    if remaining is not None and remaining < 0:
        conn.close()
        return jsonify({"ok": False, "valid": False, "message": "❌ This license key has expired! Contact admin."})
    if not bound:
        expires_at    = None
        validity_days = row["validity_days"]
        if validity_days:
            expires_at = (datetime.now() + timedelta(days=validity_days)).isoformat()
        conn.execute(
            "UPDATE license_keys SET bound_device=?, activated_at=?, expires_at=COALESCE(expires_at, ?) WHERE license_key=?",
            (device_id, now, expires_at, license_key)
        )
        conn.commit()
    label        = row["label"] or "License Key Active"
    display_name = row["display_name"] or label
    conn.close()
    return jsonify({"ok": True, "valid": True, "message": f"✅ License Activated! ({label})", "license_type": label, "display_name": display_name})


@app.route("/api/license/unbind", methods=["POST"])
def license_unbind():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    license_key = str(data.get("license_key", "") or "").strip()
    if not license_key:
        return jsonify({"error": "license_key required"}), 400
    conn = get_db()
    conn.execute("UPDATE license_keys SET bound_device=NULL, activated_at=NULL WHERE license_key=?", (license_key,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Key unbound successfully"})


@app.route("/api/admin/licenses", methods=["GET"])
def admin_get_licenses():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    rows = conn.execute("SELECT * FROM license_keys ORDER BY created_at DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["days_left"] = days_remaining(r["expires_at"])
        result.append(d)
    return jsonify(result)


@app.route("/api/admin/licenses/generate", methods=["POST"])
def admin_generate_license():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    label         = str(data.get("label", "") or "").strip()
    display_name  = str(data.get("display_name", "") or "").strip()
    max_devices   = int(data.get("max_devices", 1))
    count         = max(1, min(int(data.get("count", 1)), 50))
    validity_days = data.get("validity_days")
    expires_at    = None
    if validity_days:
        try: validity_days = int(validity_days)
        except: validity_days = None
    fixed_expires = str(data.get("expires_at", "") or "").strip()
    if fixed_expires:
        try:
            datetime.fromisoformat(fixed_expires)
            expires_at    = fixed_expires
            validity_days = None
        except: pass
    now  = datetime.now().isoformat()
    conn = get_db()
    generated = []
    for _ in range(count):
        key = generate_license_key(label)
        while conn.execute("SELECT 1 FROM license_keys WHERE license_key=?", (key,)).fetchone():
            key = generate_license_key(label)
        conn.execute(
            "INSERT INTO license_keys (license_key, label, display_name, max_devices, validity_days, expires_at, created_at, is_active) VALUES (?,?,?,?,?,?,?,1)",
            (key, label, display_name, max_devices, validity_days, expires_at, now)
        )
        generated.append(key)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "keys": generated})


@app.route("/api/admin/licenses/toggle", methods=["POST"])
def admin_toggle_license():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    license_key = str(data.get("license_key", "") or "").strip()
    is_active   = int(bool(data.get("is_active", True)))
    if not license_key:
        return jsonify({"error": "license_key required"}), 400
    conn = get_db()
    conn.execute("UPDATE license_keys SET is_active=? WHERE license_key=?", (is_active, license_key))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/licenses/update", methods=["POST"])
def admin_update_license():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    license_key   = str(data.get("license_key",  "") or "").strip()
    display_name  = str(data.get("display_name", "") or "").strip()
    label         = str(data.get("label",        "") or "").strip()
    expires_at    = str(data.get("expires_at",   "") or "").strip() or None
    validity_days = data.get("validity_days")
    if not license_key:
        return jsonify({"error": "license_key required"}), 400
    if validity_days is not None:
        try: validity_days = int(validity_days)
        except: validity_days = None
    conn = get_db()
    conn.execute(
        "UPDATE license_keys SET display_name=?, label=CASE WHEN ? != '' THEN ? ELSE label END, expires_at=?, validity_days=? WHERE license_key=?",
        (display_name, label, label, expires_at, validity_days, license_key)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/licenses/delete", methods=["POST"])
def admin_delete_license():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    license_key = str(data.get("license_key", "") or "").strip()
    if not license_key:
        return jsonify({"error": "license_key required"}), 400
    conn = get_db()
    conn.execute("DELETE FROM license_keys WHERE license_key=?", (license_key,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/licenses/restore", methods=["POST"])
def admin_restore_license():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    license_key   = str(data.get("license_key",  "") or "").strip()
    label         = str(data.get("label",         "") or "").strip()
    display_name  = str(data.get("display_name",  "") or "").strip()
    max_devices   = int(data.get("max_devices",   1)  or 1)
    bound_device  = str(data.get("bound_device",  "") or "").strip() or None
    created_at    = str(data.get("created_at",    "") or "").strip() or datetime.now().isoformat()
    activated_at  = str(data.get("activated_at",  "") or "").strip() or None
    expires_at    = str(data.get("expires_at",    "") or "").strip() or None
    validity_days = data.get("validity_days")
    is_active     = int(data.get("is_active", 1))
    if not license_key:
        return jsonify({"error": "license_key required"}), 400
    if validity_days is not None:
        try: validity_days = int(validity_days)
        except: validity_days = None
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM license_keys WHERE license_key=?", (license_key,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"ok": True, "skipped": True, "message": "Already exists"})
    conn.execute(
        "INSERT INTO license_keys (license_key, label, display_name, max_devices, bound_device, created_at, activated_at, expires_at, validity_days, is_active) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (license_key, label, display_name, max_devices, bound_device, created_at, activated_at, expires_at, validity_days, is_active)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "restored": True})


# ══════════════════════════════════════════════════════════
#  ANNOUNCEMENT API
# ══════════════════════════════════════════════════════════

@app.route("/api/announcement", methods=["GET"])
def get_announcement():
    conn = get_db()
    row = conn.execute("SELECT * FROM announcement WHERE id=1").fetchone()
    conn.close()
    if not row:
        return jsonify({"enabled": False, "message": ""})
    return jsonify({"enabled": bool(row["enabled"]), "message": row["message"] or ""})


@app.route("/api/admin/announcement", methods=["POST"])
def set_announcement():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data    = request.get_json(silent=True) or {}
    enabled = 1 if data.get("enabled") else 0
    message = str(data.get("message", "") or "").strip()
    now     = datetime.now().isoformat()
    conn = get_db()
    conn.execute("UPDATE announcement SET enabled=?, message=?, updated_at=? WHERE id=1", (enabled, message, now))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════
#  ADMIN PANEL & DEVICE ROUTES
# ══════════════════════════════════════════════════════════

@app.route("/admin")
def admin_panel():
    return render_template("admin.html")


@app.route("/api/admin/devices", methods=["GET"])
def admin_get_devices():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    rows = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/block", methods=["POST"])
def admin_block():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id", "") or "").strip()
    reason    = str(data.get("reason", "Admin কর্তৃক block করা হয়েছে") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    conn = get_db()
    conn.execute("UPDATE devices SET is_blocked=1, block_reason=? WHERE device_id=?", (reason, device_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "blocked": True})


@app.route("/api/admin/unblock", methods=["POST"])
def admin_unblock():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id", "") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    conn = get_db()
    conn.execute("UPDATE devices SET is_blocked=0, block_reason='' WHERE device_id=?", (device_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "blocked": False})


@app.route("/api/admin/delete", methods=["POST"])
def admin_delete():
    if not check_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id", "") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    conn = get_db()
    conn.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════
init_db()

from scraper import start_scraper
start_scraper()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
