import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ.setdefault(k.strip(), v)


load_env_file(os.path.join(HERE, "local.env"))

cookie = os.environ.get("MW_PHPSESSID", "").strip()
if not cookie:
    cookie = input("Microworkers এর PHPSESSID পেস্ট করুন: ").strip()
cookie = cookie.strip(";").strip()
if cookie.lower().startswith("phpsessid="):
    cookie = cookie[len("phpsessid="):]
if not cookie:
    print("কুকি দেওয়া হয়নি, বন্ধ হচ্ছে।")
    sys.exit(1)
os.environ["MW_PHPSESSID"] = cookie

import scraper

print("[Local] স্ক্র্যাপার চালু হচ্ছে। বন্ধ করতে Ctrl+C চাপুন।")
try:
    scraper.scrape_loop()
except KeyboardInterrupt:
    print("\n[Local] বন্ধ করা হয়েছে।")
