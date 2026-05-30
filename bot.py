import os
import json
import base64
import threading
import textwrap
import io
import re
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
import requests

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))

DATA_FILE = "proofs.json"
DEALS_FILE = "deals.json"
OFFSET_FILE = "offset.txt"

# =========================
# TELEGRAM HELPERS
# =========================

def extract_links_from_text(text):
    if not text:
        return []
    return re.findall(r'https?://[^\s]+', text)


def follow_redirects(url):
    try:
        r = requests.get(url, allow_redirects=True, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        return r.url
    except:
        return url


def extract_product_id(url):
    final_url = follow_redirects(url)

    fk = re.search(r'/p/(ITM[A-Z0-9]+)', final_url)
    if fk:
        return {"platform": "flipkart", "product_id": fk.group(1), "final_url": final_url}

    fk2 = re.search(r'pid=([A-Z0-9]+)', final_url)
    if fk2:
        return {"platform": "flipkart", "product_id": fk2.group(1), "final_url": final_url}

    amz = re.search(r'/dp/([A-Z0-9]{10})', final_url)
    if amz:
        return {"platform": "amazon", "product_id": amz.group(1), "final_url": final_url}

    return {"platform": "unknown", "product_id": None, "final_url": final_url}


# =========================
# FILE OPS
# =========================

def load_json(file):
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return []


def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)


# =========================
# DEAL LOGIC (NEW CORE)
# =========================

def match_alias(text, deal):
    aliases = deal.get("aliases", [])
    text = (text or "").lower()
    return any(a.lower() in text for a in aliases)


def time_valid(deal):
    window = deal.get("valid_minutes", 60)
    created = deal.get("added")

    if not created:
        return True

    try:
        dt = datetime.fromisoformat(created)
        return datetime.now() <= dt + timedelta(minutes=window)
    except:
        return True


def price_valid(deal, price=None):
    if not deal.get("price_range"):
        return True
    try:
        if price is None:
            return True
        mn, mx = deal["price_range"]
        return mn <= float(price) <= mx
    except:
        return True


# =========================
# ENKR VERIFICATION (NEW)
# =========================

def get_enkr(short_url):
    try:
        r = requests.get(short_url,
                         headers={"User-Agent": "Mozilla/5.0"},
                         timeout=10)

        html = r.text

        patterns = [
            r"ENKR[:\s]*([A-Z0-9]+)",
            r"enkr[:\s]*([A-Z0-9]+)"
        ]

        for p in patterns:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                return m.group(1)

        return None
    except:
        return None


# =========================
# VERIFICATION ENGINE (FINAL)
# =========================

def verify_post(text, links):
    deals = load_json(DEALS_FILE)

    for link in links:
        result = extract_product_id(link)
        post_pid = result.get("product_id")

        for deal in deals:
            deal_pid = deal.get("product_id")

            # STEP 1: product match
            if deal_pid and post_pid and deal_pid.upper() == post_pid.upper():

                # STEP 2: alias match
                if not match_alias(text, deal):
                    continue

                # STEP 3: time check
                if not time_valid(deal):
                    return "Mismatch", None, "Time window expired"

                # STEP 4: ENKR check
                if deal.get("enkr"):
                    enkr = get_enkr(deal.get("original_link"))
                    if enkr and enkr != deal["enkr"]:
                        return "Mismatch", None, "ENKR mismatch"

                return "Verified", deal, f"Matched {post_pid}"

    return "Mismatch", None, "No match found"


# =========================
# SCREENSHOT
# =========================

def generate_screenshot(text, status, deal=None):
    if not PIL_AVAILABLE:
        return None

    img = Image.new("RGB", (800, 400), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    draw.text((20, 20), text[:80], fill=(255, 255, 255))
    draw.text((20, 100), f"Status: {status}", fill=(0, 255, 0) if status=="Verified" else (255,0,0))

    if deal:
        draw.text((20, 150), f"Deal: {deal.get('title')}", fill=(200,200,200))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# =========================
# TELEGRAM PROCESSOR
# =========================

def process_message(msg):
    text = msg.get("text") or msg.get("caption") or ""
    chat = msg.get("chat", {})
    post_id = msg.get("message_id")

    links = extract_links_from_text(text)

    status, deal, reason = verify_post(text, links)

    screenshot = generate_screenshot(text, status, deal)

    proof = {
        "id": f"{chat.get('id')}_{post_id}_{int(time.time())}",
        "text": text,
        "links": links,
        "status": status,
        "reason": reason,
        "deal": deal.get("title") if deal else None,
        "photo": screenshot,
        "timestamp": datetime.now().isoformat()
    }

    proofs = load_json(DATA_FILE)
    proofs.insert(0, proof)
    save_json(DATA_FILE, proofs[:500])

    print(f"[{status}] {reason}")


# =========================
# POLLING
# =========================

def poll():
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 20},
                timeout=30
            )

            data = r.json()

            for u in data.get("result", []):
                offset = u["update_id"] + 1

                if "channel_post" in u:
                    process_message(u["channel_post"])

            time.sleep(2)

        except Exception as e:
            print("poll error:", e)
            time.sleep(5)


# =========================
# FLASK API
# =========================

app = Flask(__name__)
CORS(app)


@app.route("/proofs")
def proofs():
    return jsonify(load_json(DATA_FILE))


@app.route("/deals", methods=["GET"])
def deals():
    return jsonify(load_json(DEALS_FILE))


@app.route("/deals", methods=["POST"])
def add_deal():
    data = request.json

    deal = {
        "id": str(int(time.time())),
        "title": data.get("title"),
        "aliases": data.get("aliases", []),
        "product_id": data.get("product_id"),
        "enkr": data.get("enkr"),
        "valid_minutes": data.get("valid_minutes", 60),
        "price_range": data.get("price_range"),
        "original_link": data.get("link"),
        "added": datetime.now().isoformat()
    }

    deals = load_json(DEALS_FILE)
    deals.insert(0, deal)
    save_json(DEALS_FILE, deals)

    return jsonify(deal)


@app.route("/")
def home():
    return jsonify({"status": "running"})


# =========================
# START
# =========================

if __name__ == "__main__":
    if BOT_TOKEN:
        threading.Thread(target=poll, daemon=True).start()
        print("Bot started")
    app.run(host="0.0.0.0", port=PORT)
