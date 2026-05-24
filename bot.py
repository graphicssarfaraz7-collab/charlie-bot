import os
import json
import asyncio
import base64
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading

# ============================================
# EARNKARO TELEGRAM BOT — Auto Screenshot System
# ============================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
AFFILIATE_DOMAINS = [
    "ekaro.in", "earnkaro", "fktr.in", "amzn.to", "myntr.a",
    "ajio.com", "nykaa.com", "bit.ly", "clnk.in", "cuelinks",
    "optimisemedia", "vcommission", "admitad"
]

# In-memory store (Railway pe persist hoga JSON file mein)
DATA_FILE = "proofs.json"

def load_proofs():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_proofs(proofs):
    with open(DATA_FILE, "w") as f:
        json.dump(proofs, f, indent=2)

def check_link(text):
    if not text:
        return False, None
    words = text.split()
    for word in words:
        for domain in AFFILIATE_DOMAINS:
            if domain in word.lower():
                return True, word
    return False, None

def extract_links(text):
    if not text:
        return []
    return [w for w in text.split() if w.startswith("http") or w.startswith("www")]

# ============================================
# TELEGRAM BOT — Post capture karta hai
# ============================================

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har channel post yahan aata hai"""
    message = update.channel_post
    if not message:
        return

    chat = message.chat
    channel_name = chat.username or chat.title or str(chat.id)
    text = message.text or message.caption or ""
    post_id = message.message_id
    timestamp = datetime.now().strftime("%d/%m/%Y %I:%M %p")

    # Link check karo
    has_affiliate, found_link = check_link(text)
    all_links = extract_links(text)

    # Photo capture
    photo_b64 = None
    if message.photo:
        photo = message.photo[-1]  # Best quality
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

    # Proof entry banao
    proof = {
        "id": f"{chat.id}_{post_id}_{int(datetime.now().timestamp())}",
        "channel": f"@{channel_name}" if channel_name and not channel_name.startswith("@") else channel_name,
        "channel_id": str(chat.id),
        "post_id": post_id,
        "text": text,
        "links": all_links,
        "affiliate_link": found_link,
        "has_affiliate_link": has_affiliate,
        "status": "Verified" if has_affiliate else "Mismatch",
        "photo": photo_b64,
        "timestamp": timestamp,
        "date": datetime.now().isoformat(),
        "deleted": False
    }

    proofs = load_proofs()
    proofs.insert(0, proof)
    # Max 500 proofs rakhein
    proofs = proofs[:500]
    save_proofs(proofs)

    print(f"✅ Post captured: {channel_name} | Status: {proof['status']} | Link: {found_link}")

async def handle_edited_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit hone pe bhi capture karo"""
    message = update.edited_channel_post
    if not message:
        return

    chat = message.chat
    post_id = message.message_id

    proofs = load_proofs()
    for p in proofs:
        if p.get("channel_id") == str(chat.id) and p.get("post_id") == post_id:
            p["edited"] = True
            p["edited_text"] = message.text or message.caption or ""
            p["edited_time"] = datetime.now().strftime("%d/%m/%Y %I:%M %p")
            break
    save_proofs(proofs)
    print(f"✏️ Post edited: {chat.username or chat.id} | Post: {post_id}")

# ============================================
# FLASK API — Tool se connect hoga
# ============================================

flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "EarnKaro Bot running!", "proofs": len(load_proofs())})

@flask_app.route("/proofs", methods=["GET"])
def get_proofs():
    proofs = load_proofs()
    # Sensitive data (photo) chhota karein for listing
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

def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# ============================================
# MAIN — Bot + API dono chalao
# ============================================

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN environment variable set nahi hai!")
        return

    # Flask API thread mein chalao
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask API started")

    # Telegram Bot chalao
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS, handle_channel_post))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_CHANNEL_POSTS, handle_edited_post))

    print("✅ EarnKaro Bot started — posts capture ho rahe hain!")
    app.run_polling(allowed_updates=["channel_post", "edited_channel_post"])

if __name__ == "__main__":
    main()
