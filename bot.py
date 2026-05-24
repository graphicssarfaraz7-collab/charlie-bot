import os
import json
import base64
import threading
import textwrap
import io
from datetime import datetime
from flask import Flask, jsonify, send_file
from flask_cors import CORS
import requests

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("PIL not available — install Pillow")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))

AFFILIATE_DOMAINS = [
    "ekaro.in", "earnkaro", "fktr.in", "amzn.to", "myntr.a",
    "ajio.com", "nykaa.com", "bit.ly", "clnk.in", "cuelinks",
    "optimisemedia", "vcommission", "admitad"
]

DATA_FILE = "proofs.json"
OFFSET_FILE = "offset.txt"

def load_proofs():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_proofs(proofs):
    with open(DATA_FILE, "w") as f:
        json.dump(proofs, f, indent=2)

def get_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

def check_affiliate_link(text):
    if not text:
        return False, None
    for word in text.split():
        for domain in AFFILIATE_DOMAINS:
            if domain in word.lower():
                return True, word
    return False, None

def get_photo_bytes(file_id):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        res = requests.get(url, timeout=10)
        file_path = res.json()["result"]["file_path"]
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        photo_res = requests.get(photo_url, timeout=15)
        return photo_res.content
    except Exception as e:
        print(f"Photo error: {e}")
        return None

def generate_telegram_screenshot(channel_name, text, link, status, photo_bytes=None, timestamp=None):
    """Telegram jaisa screenshot banao PIL se"""
    if not PIL_AVAILABLE:
        return None

    try:
        W = 800
        PADDING = 24
        BG = (23, 33, 43)
        HEADER_BG = (28, 39, 51)
        TEXT_COLOR = (232, 234, 240)
        LINK_COLOR = (106, 179, 243)
        MUTED = (125, 139, 153)
        GREEN = (0, 217, 126)
        RED = (255, 87, 87)
        STAMP_GREEN_BG = (0, 30, 15)
        STAMP_RED_BG = (30, 5, 5)

        # Font setup
        try:
            font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            font_reg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            font_mono = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14)
        except:
            font_bold = ImageFont.load_default()
            font_reg = ImageFont.load_default()
            font_small = ImageFont.load_default()
            font_mono = ImageFont.load_default()

        ts = timestamp or datetime.now().strftime("%d/%m/%Y %I:%M %p")

        # Measure text height
        wrapped = textwrap.wrap(text or "(no text)", width=60)
        text_height = len(wrapped) * 26

        # Photo height
        post_photo = None
        photo_h = 0
        if photo_bytes:
            try:
                post_photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
                ratio = post_photo.width / post_photo.height
                photo_h = int((W - PADDING*2) / ratio)
                post_photo = post_photo.resize((W - PADDING*2, photo_h))
            except:
                pass

        total_h = (
            70 +           # header
            PADDING +      # top padding
            text_height +  # text
            (40 if link else 0) +  # link
            (photo_h + PADDING if post_photo else 0) +  # photo
            70 +           # stamp
            50             # footer
        )

        img = Image.new("RGB", (W, total_h), BG)
        draw = ImageDraw.Draw(img)

        # Header
        draw.rectangle([0, 0, W, 65], fill=HEADER_BG)

        # Avatar circle
        avatar_color = (29, 158, 117) if status == "Verified" else (192, 57, 43)
        draw.ellipse([PADDING, 12, PADDING+40, 52], fill=avatar_color)
        initials = channel_name.replace("@", "")[:2].upper()
        draw.text((PADDING+8, 18), initials, font=font_bold, fill=(255,255,255))

        # Channel name + time
        draw.text((PADDING+50, 12), channel_name, font=font_bold, fill=LINK_COLOR)
        draw.text((PADDING+50, 36), ts, font=font_small, fill=MUTED)

        # Telegram icon
        draw.text((W-50, 20), "✈", font=font_bold, fill=LINK_COLOR)

        # Divider
        draw.line([0, 65, W, 65], fill=(42, 58, 74), width=1)

        y = 65 + PADDING

        # Post text
        for line in wrapped:
            draw.text((PADDING, y), line, font=font_reg, fill=TEXT_COLOR)
            y += 26

        y += 8

        # Link
        if link:
            draw.text((PADDING, y), link, font=font_mono, fill=LINK_COLOR)
            y += 36

        # Photo
        if post_photo:
            y += 8
            img.paste(post_photo, (PADDING, y))
            y += photo_h + PADDING

        # Stamp
        stamp_bg = STAMP_GREEN_BG if status == "Verified" else STAMP_RED_BG
        stamp_color = GREEN if status == "Verified" else RED
        stamp_text = "✅  LINK VERIFIED — Original EarnKaro link confirmed" if status == "Verified" else "❌  LINK MISMATCH — Galat link use ki gayi"

        draw.rectangle([0, y, W, y+60], fill=stamp_bg)
        draw.line([0, y, W, y], fill=stamp_color, width=2)
        draw.text((PADDING, y+10), stamp_text, font=font_bold, fill=stamp_color)
        draw.text((PADDING, y+36), f"EarnKaro Affiliate Tracker  •  {ts}", font=font_small, fill=MUTED)
        y += 60

        # Footer
        draw.rectangle([0, y, W, y+46], fill=HEADER_BG)
        draw.line([0, y, W, y], fill=(42,58,74), width=1)
        draw.text((PADDING, y+14), "EarnKaro Tracker — Verified Proof", font=font_small, fill=MUTED)
        status_text = "✓ VERIFIED" if status == "Verified" else "✗ MISMATCH"
        draw.text((W-130, y+14), status_text, font=font_bold, fill=stamp_color)

        # Save to bytes
        buf = io.BytesIO()
        img.save(buf, format="PNG", quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    except Exception as e:
        print(f"Screenshot generation error: {e}")
        return None

def process_message(message):
    chat = message.get("chat", {})
    channel_name = chat.get("username") or chat.get("title") or str(chat.get("id", ""))
    if channel_name and not channel_name.startswith("@"):
        channel_name = "@" + channel_name

    text = message.get("text") or message.get("caption") or ""
    post_id = message.get("message_id")
    timestamp = datetime.now().strftime("%d/%m/%Y %I:%M %p")

    has_affiliate, found_link = check_affiliate_link(text)
    all_links = [w for w in text.split() if w.startswith("http")]
    status = "Verified" if has_affiliate else "Mismatch"

    # Photo fetch karo
    photo_bytes = None
    photos = message.get("photo")
    if photos:
        photo_bytes = get_photo_bytes(photos[-1]["file_id"])
        print(f"📸 Photo fetched: {'OK' if photo_bytes else 'FAILED'}")

    # Telegram-style screenshot banao
    print(f"🖼 Generating screenshot for {channel_name}...")
    screenshot_b64 = generate_telegram_screenshot(
        channel_name=channel_name,
        text=text,
        link=found_link or (all_links[0] if all_links else None),
        status=status,
        photo_bytes=photo_bytes,
        timestamp=timestamp
    )
    print(f"🖼 Screenshot: {'OK' if screenshot_b64 else 'FAILED'}")

    # Telegram message link banao
    username = chat.get("username")
    if username:
        telegram_link = f"https://t.me/{username}/{post_id}"
    else:
        # Private channel ke liye c/ format
        channel_id = str(chat.get("id", "")).replace("-100", "")
        telegram_link = f"https://t.me/c/{channel_id}/{post_id}"

    proof = {
        "id": f"{chat.get('id')}_{post_id}_{int(datetime.now().timestamp())}",
        "channel": channel_name,
        "channel_id": str(chat.get("id", "")),
        "post_id": post_id,
        "telegram_message_link": telegram_link,
        "text": text,
        "links": all_links,
        "affiliate_link": found_link,
        "has_affiliate_link": has_affiliate,
        "status": status,
        "photo": screenshot_b64,
        "has_photo": bool(screenshot_b64),
        "timestamp": timestamp,
        "date": datetime.now().isoformat(),
        "deleted": False
    }

    proofs = load_proofs()
    exists = any(
        p.get("channel_id") == str(chat.get("id")) and p.get("post_id") == post_id
        for p in proofs
    )
    if not exists:
        proofs.insert(0, proof)
        proofs = proofs[:500]
        save_proofs(proofs)
        print(f"✅ Captured: {channel_name} | {status} | Link: {found_link}")
    return proof

def poll_telegram():
    print("🤖 Polling started...")
    while True:
        try:
            offset = get_offset()
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 30, "allowed_updates": ["channel_post", "edited_channel_post"]}
            res = requests.get(url, params=params, timeout=40)
            data = res.json()

            if not data.get("ok"):
                print(f"Telegram error: {data}")
                import time; time.sleep(5)
                continue

            for update in data.get("result", []):
                save_offset(update["update_id"] + 1)
                if "channel_post" in update:
                    process_message(update["channel_post"])
                if "edited_channel_post" in update:
                    msg = update["edited_channel_post"]
                    proofs = load_proofs()
                    for p in proofs:
                        if p.get("channel_id") == str(msg["chat"]["id"]) and p.get("post_id") == msg.get("message_id"):
                            p["edited"] = True
                            p["edited_text"] = msg.get("text") or msg.get("caption") or ""
                            p["edited_time"] = datetime.now().strftime("%d/%m/%Y %I:%M %p")
                            break
                    save_proofs(proofs)

        except Exception as e:
            print(f"Poll error: {e}")
            import time; time.sleep(5)

flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route("/", methods=["GET"])
def home():
    proofs = load_proofs()
    return jsonify({"status": "EarnKaro Bot running!", "total_proofs": len(proofs), "pil": PIL_AVAILABLE})

@flask_app.route("/proofs", methods=["GET"])
def get_proofs():
    proofs = load_proofs()
    return jsonify([{k: v for k, v in p.items() if k != "photo"} | {"has_photo": bool(p.get("photo"))} for p in proofs])

@flask_app.route("/proofs/<proof_id>", methods=["GET"])
def get_proof(proof_id):
    for p in load_proofs():
        if p["id"] == proof_id:
            return jsonify(p)
    return jsonify({"error": "Not found"}), 404

@flask_app.route("/photo/<proof_id>", methods=["GET"])
def get_photo(proof_id):
    for p in load_proofs():
        if p["id"] == proof_id and p.get("photo"):
            img_data = base64.b64decode(p["photo"])
            return send_file(io.BytesIO(img_data), mimetype="image/png")
    return jsonify({"error": "Photo not found"}), 404

@flask_app.route("/proofs/<proof_id>", methods=["DELETE"])
def delete_proof(proof_id):
    proofs = [p for p in load_proofs() if p["id"] != proof_id]
    save_proofs(proofs)
    return jsonify({"success": True})

@flask_app.route("/stats", methods=["GET"])
def get_stats():
    proofs = load_proofs()
    return jsonify({
        "total": len(proofs),
        "verified": sum(1 for p in proofs if p["status"] == "Verified"),
        "mismatch": sum(1 for p in proofs if p["status"] == "Mismatch"),
        "channels": list(set(p["channel"] for p in proofs)),
        "pil_available": PIL_AVAILABLE
    })

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN set nahi hai!")
    else:
        poll_thread = threading.Thread(target=poll_telegram, daemon=True)
        poll_thread.start()
        print(f"✅ Bot started on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)
