import os
import json
import base64
import asyncio
import threading
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

# ============================================
# EARNKARO BOT — Fixed Version
# ============================================

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

def extract_all_links(text):
    if not text:
        return []
    return [w for w in text.split() if w.startswith("http") or w.startswith("www")]

def get_photo_base64(file_id):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        res = requests.get(url, timeout=10)
        file_path = res.json()["result"]["file_path"]
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        photo_res = requests.get(photo_url, timeout=15)
        return base64.b64encode(photo_res.content).decode("utf-8")
    except Exception as e:
        print(f"Photo error: {e}")
        return None

def process_message(message):
    """Telegram message ko process karo aur proof banao"""
    chat = message.get("chat", {})
    channel_name = chat.get("username") or chat.get("title") or str(chat.get("id", ""))
    if channel_name and not channel_name.startswith("@"):
        channel_name = "@" + channel_name

    text = message.get("text") or message.get("caption") or ""
    post_id = message.get("message_id")
    timestamp = datetime.now().strftime("%d/%m/%Y %I:%M %p")

    has_affiliate, found_link = check_affiliate_link(text)
    all_links = extract_all_links(text)

    # Photo capture
    photo_b64 = None
    photos = message.get("photo")
    if photos:
        best_photo = photos[-1]
        photo_b64 = get_photo_base64(best_photo["file_id"])

    proof = {
        "id": f"{chat.get('id')}_{post_id}_{int(datetime.now().timestamp())}",
        "channel": channel_name,
        "channel_id": str(chat.get("id", "")),
        "post_id": post_id,
        "text": text,
        "links": all_links,
        "affiliate_link": found_link,
        "has_affiliate_link": has_affiliate,
        "status": "Verified" if has_affiliate else "Mismatch",
        "photo": photo_b64,
        "has_photo": bool(photo_b64),
        "timestamp": timestamp,
        "date": datetime.now().isoformat(),
        "deleted": False
    }

    proofs = load_proofs()
    # Duplicate check
    exists = any(p.get("channel_id") == str(chat.get("id")) and p.get("post_id") == post_id for p in proofs)
    if not exists:
        proofs.insert(0, proof)
        proofs = proofs[:500]
        save_proofs(proofs)
        print(f"✅ Captured: {channel_name} | {proof['status']} | Link: {found_link}")
    return proof

def poll_telegram():
    """Telegram se naye messages fetch karo"""
    print("🤖 Polling started...")
    while True:
        try:
            offset = get_offset()
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["channel_post", "edited_channel_post"]
            }
            res = requests.get(url, params=params, timeout=40)
            data = res.json()

            if not data.get("ok"):
                print(f"Telegram error: {data}")
                import time; time.sleep(5)
                continue

            updates = data.get("result", [])
            for update in updates:
                update_id = update["update_id"]
                save_offset(update_id + 1)

                # Channel post
                if "channel_post" in update:
                    process_message(update["channel_post"])

                # Edited post — mark as edited
                if "edited_channel_post" in update:
                    msg = update["edited_channel_post"]
                    proofs = load_proofs()
                    for p in proofs:
                        if (p.get("channel_id") == str(msg["chat"]["id"]) and
                                p.get("post_id") == msg.get("message_id")):
                            p["edited"] = True
                            p["edited_text"] = msg.get("text") or msg.get("caption") or ""
                            p["edited_time"] = datetime.now().strftime("%d/%m/%Y %I:%M %p")
                            break
                    save_proofs(proofs)

        except Exception as e:
            print(f"Poll error: {e}")
            import time; time.sleep(5)

# ============================================
# FLASK API
# ============================================
flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route("/", methods=["GET"])
def home():
    proofs = load_proofs()
    return jsonify({
        "status": "EarnKaro Bot running!",
        "total_proofs": len(proofs),
        "bot_token_set": bool(BOT_TOKEN)
    })

@flask_app.route("/proofs", methods=["GET"])
def get_proofs():
    proofs = load_proofs()
    lite = []
    for p in proofs:
        entry = {k: v for k, v in p.items() if k != "photo"}
        entry["has_photo"] = bool(p.get("photo"))
        lite.append(entry)
    return jsonify(lite)

@flask_app.route("/proofs/<proof_id>", methods=["GET"])
def get_proof(proof_id):
    proofs = load_proofs()
    for p in proofs:
        if p["id"] == proof_id:
            return jsonify(p)
    return jsonify({"error": "Not found"}), 404

@flask_app.route("/proofs/<proof_id>", methods=["DELETE"])
def delete_proof(proof_id):
    proofs = load_proofs()
    proofs = [p for p in proofs if p["id"] != proof_id]
    save_proofs(proofs)
    return jsonify({"success": True})

@flask_app.route("/stats", methods=["GET"])
def get_stats():
    proofs = load_proofs()
    verified = sum(1 for p in proofs if p["status"] == "Verified")
    mismatch = sum(1 for p in proofs if p["status"] == "Mismatch")
    channels = list(set(p["channel"] for p in proofs))
    return jsonify({
        "total": len(proofs),
        "verified": verified,
        "mismatch": mismatch,
        "channels": channels
    })

# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN set nahi hai! Railway Variables mein add karo.")
    else:
        print(f"✅ Bot token found")
        # Polling thread mein chalao
        poll_thread = threading.Thread(target=poll_telegram, daemon=True)
        poll_thread.start()
        print(f"✅ Polling thread started")

    print(f"✅ Flask API starting on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)
