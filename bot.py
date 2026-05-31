import os
import json
import base64
import threading
import textwrap
import io
import re
from datetime import datetime
from flask import Flask, jsonify, send_file
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

# ============================================
# PRODUCT ID EXTRACTOR
# ============================================

def follow_redirects(url, max_hops=5):
    """URL follow karo — final destination nikalo"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; EarnKaroBot/1.0)"}
        resp = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        return resp.url
    except:
        try:
            # Manual redirect follow
            current = url
            for _ in range(max_hops):
                r = requests.head(current, headers={"User-Agent": "Mozilla/5.0"}, 
                                allow_redirects=False, timeout=8)
                if r.status_code in (301, 302, 303, 307, 308):
                    current = r.headers.get("Location", current)
                else:
                    break
            return current
        except:
            return url

def extract_product_id(url):
    """URL se product ID nikalo"""
    try:
        final_url = follow_redirects(url)
        print(f"Final URL: {final_url}")

        # Flipkart
        fk = re.search(r'/p/(ITM[A-Z0-9]+)', final_url)
        if fk:
            return {"platform": "flipkart", "product_id": fk.group(1), "final_url": final_url}

        # Flipkart pid param
        fk2 = re.search(r'pid=([A-Z0-9]+)', final_url)
        if fk2:
            return {"platform": "flipkart", "product_id": fk2.group(1), "final_url": final_url}

        # Amazon ASIN
        amz = re.search(r'/dp/([A-Z0-9]{10})', final_url)
        if amz:
            return {"platform": "amazon", "product_id": amz.group(1), "final_url": final_url}

        amz2 = re.search(r'/gp/product/([A-Z0-9]{10})', final_url)
        if amz2:
            return {"platform": "amazon", "product_id": amz2.group(1), "final_url": final_url}

        # Myntra
        myn = re.search(r'/(\d{8,12})(?:/buy)?(?:\?|$)', final_url)
        if myn and 'myntra' in final_url:
            return {"platform": "myntra", "product_id": myn.group(1), "final_url": final_url}

        # Meesho
        mes = re.search(r'/product-detail/\?id=(\d+)', final_url)
        if mes:
            return {"platform": "meesho", "product_id": mes.group(1), "final_url": final_url}

        # Ajio
        ajio = re.search(r'-(\d{6,12})\.html', final_url)
        if ajio and 'ajio' in final_url:
            return {"platform": "ajio", "product_id": ajio.group(1), "final_url": final_url}

        return {"platform": "unknown", "product_id": None, "final_url": final_url}

    except Exception as e:
        print(f"Product ID extract error: {e}")
        return {"platform": "unknown", "product_id": None, "final_url": url}

def extract_links_from_text(text):
    if not text:
        return []
    return re.findall(r'https?://[^\s]+', text)

# ============================================
# FILE HELPERS
# ============================================

def load_proofs():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_proofs(proofs):
    with open(DATA_FILE, "w") as f:
        json.dump(proofs, f, indent=2)

def load_deals():
    try:
        with open(DEALS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_deals(deals):
    with open(DEALS_FILE, "w") as f:
        json.dump(deals, f, indent=2)

def get_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

# ============================================
# VERIFICATION LOGIC
# ============================================

def verify_post(text, links):
    """
    Post verify karo — product ID match check
    Returns: (status, matched_deal, reason)
    """
    deals = load_deals()
    if not deals:
        # Deals nahi hain — basic affiliate check
        affiliate_domains = ["ekaro.in","earnkaro","fktr.in","amzn.to","myntr.a",
                           "ajio.com","nykaa.com","bit.ly","clnk.in","cuelinks",
                           "optimisemedia","vcommission","admitad"]
        for link in links:
            for domain in affiliate_domains:
                if domain in link.lower():
                    return "Verified", None, f"Affiliate link found: {link}"
        return "Mismatch", None, "No affiliate link found"

    # Product ID se match karo
    for link in links:
        print(f"🔍 Checking link: {link}")
        result = extract_product_id(link)
        post_pid = result.get("product_id")
        platform = result.get("platform")
        
        if not post_pid:
            continue

        print(f"📦 Product ID from post: {post_pid} ({platform})")

        # Deals mein dhundo
        for deal in deals:
            deal_pid = deal.get("product_id")
            deal_platform = deal.get("platform")
            
            if deal_pid and post_pid:
                if deal_pid.upper() == post_pid.upper():
                    print(f"✅ MATCH! Deal: {deal.get('title')} | Product: {post_pid}")
                    return "Verified", deal, f"Product ID matched: {post_pid}"

    return "Mismatch", None, "Product ID match nahi mili kisi bhi deal se"

# ============================================
# SCREENSHOT GENERATOR
# ============================================

def get_photo_bytes(file_id):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        res = requests.get(url, timeout=10)
        file_path = res.json()["result"]["file_path"]
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        return requests.get(photo_url, timeout=15).content
    except Exception as e:
        print(f"Photo error: {e}")
        return None

def generate_screenshot(channel_name, text, link, status, matched_deal=None, 
                        reason="", photo_bytes=None, timestamp=None):
    if not PIL_AVAILABLE:
        return None
    try:
        W = 800
        PAD = 24
        BG = (23, 33, 43)
        HDR = (28, 39, 51)
        TC = (232, 234, 240)
        LC = (106, 179, 243)
        MT = (125, 139, 153)
        GREEN = (0, 217, 126)
        RED = (255, 87, 87)
        AMBER = (255, 184, 0)

        ts = timestamp or datetime.now().strftime("%d/%m/%Y %I:%M %p")

        try:
            fb = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 17)
            fr = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
            fs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            fm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 13)
        except:
            fb = fr = fs = fm = ImageFont.load_default()

        wrapped = textwrap.wrap(text or "(no text)", width=62)
        text_h = len(wrapped) * 24

        post_photo = None
        photo_h = 0
        if photo_bytes:
            try:
                post_photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
                ratio = post_photo.width / post_photo.height
                photo_h = int((W - PAD*2) / ratio)
                post_photo = post_photo.resize((W - PAD*2, photo_h))
            except:
                pass

        # Deal info box height
        deal_h = 70 if matched_deal else 0

        total_h = 65 + PAD + text_h + 36 + (photo_h + PAD if post_photo else 0) + deal_h + 65 + 46

        img = Image.new("RGB", (W, total_h), BG)
        draw = ImageDraw.Draw(img)

        # Header
        draw.rectangle([0, 0, W, 65], fill=HDR)
        av_color = (29, 158, 117) if status == "Verified" else (192, 57, 43)
        draw.ellipse([PAD, 12, PAD+40, 52], fill=av_color)
        initials = channel_name.replace("@", "")[:2].upper()
        draw.text((PAD+10, 18), initials, font=fb, fill=(255,255,255))
        draw.text((PAD+50, 12), channel_name, font=fb, fill=LC)
        draw.text((PAD+50, 34), ts, font=fs, fill=MT)
        draw.text((W-45, 20), "✈", font=fb, fill=LC)
        draw.line([0, 65, W, 65], fill=(42,58,74), width=1)

        y = 65 + PAD

        # Post text
        for line in wrapped:
            draw.text((PAD, y), line, font=fr, fill=TC)
            y += 24
        y += 8

        # Link
        if link:
            draw.text((PAD, y), link[:80], font=fm, fill=LC)
            y += 32

        # Photo
        if post_photo:
            y += 6
            img.paste(post_photo, (PAD, y))
            y += photo_h + PAD

        # Deal match box
        if matched_deal:
            draw.rectangle([PAD, y, W-PAD, y+60], fill=(0,20,10), outline=(0,100,50), width=1)
            draw.text((PAD+12, y+8), f"✅  Deal matched: {matched_deal.get('title','')}", font=fb, fill=GREEN)
            draw.text((PAD+12, y+32), f"Product ID: {matched_deal.get('product_id','')}  |  Platform: {matched_deal.get('platform','').title()}", font=fs, fill=MT)
            y += 70

        # Verification stamp
        sc = GREEN if status == "Verified" else RED
        sb = (0, 25, 12) if status == "Verified" else (25, 5, 5)
        st = f"✅  VERIFIED — {reason}" if status == "Verified" else f"❌  MISMATCH — {reason}"
        draw.rectangle([0, y, W, y+62], fill=sb)
        draw.line([0, y, W, y], fill=sc, width=2)
        draw.text((PAD, y+10), st[:80], font=fb, fill=sc)
        draw.text((PAD, y+34), f"EarnKaro Affiliate Tracker  •  {ts}", font=fs, fill=MT)
        y += 62

        # Footer
        draw.rectangle([0, y, W, y+46], fill=HDR)
        draw.line([0, y, W, y], fill=(42,58,74), width=1)
        draw.text((PAD, y+14), "EarnKaro Tracker — Auto Proof", font=fs, fill=MT)
        stxt = "✓ VERIFIED" if status == "Verified" else "✗ MISMATCH"
        draw.text((W-120, y+14), stxt, font=fb, fill=sc)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    except Exception as e:
        print(f"Screenshot error: {e}")
        return None

# ============================================
# MESSAGE PROCESSOR
# ============================================

def process_message(message):
    chat = message.get("chat", {})
    channel_name = chat.get("username") or chat.get("title") or str(chat.get("id",""))
    if channel_name and not channel_name.startswith("@"):
        channel_name = "@" + channel_name

    text = message.get("text") or message.get("caption") or ""
    post_id = message.get("message_id")
    timestamp = datetime.now().strftime("%d/%m/%Y %I:%M %p")

    links = extract_links_from_text(text)
    print(f"🔗 Links found: {links}")

    # Product ID se verify karo
    status, matched_deal, reason = verify_post(text, links)
    print(f"📊 Verification: {status} — {reason}")

    # Photo
    photo_bytes = None
    photos = message.get("photo")
    if photos:
        photo_bytes = get_photo_bytes(photos[-1]["file_id"])

    # Telegram message link
    username = chat.get("username")
    if username:
        tg_link = f"https://t.me/{username}/{post_id}"
    else:
        cid = str(chat.get("id","")).replace("-100","")
        tg_link = f"https://t.me/c/{cid}/{post_id}"

    # Screenshot banao
    main_link = (matched_deal or {}).get("original_link") or (links[0] if links else None)
    screenshot = generate_screenshot(
        channel_name=channel_name,
        text=text,
        link=main_link,
        status=status,
        matched_deal=matched_deal,
        reason=reason,
        photo_bytes=photo_bytes,
        timestamp=timestamp
    )

    proof = {
        "id": f"{chat.get('id')}_{post_id}_{int(datetime.now().timestamp())}",
        "channel": channel_name,
        "channel_id": str(chat.get("id","")),
        "post_id": post_id,
        "telegram_message_link": tg_link,
        "text": text,
        "links": links,
        "affiliate_link": links[0] if links else None,
        "matched_deal": matched_deal.get("title") if matched_deal else None,
        "matched_product_id": matched_deal.get("product_id") if matched_deal else None,
        "status": status,
        "reason": reason,
        "photo": screenshot,
        "has_photo": bool(screenshot),
        "timestamp": timestamp,
        "date": datetime.now().isoformat()
    }

    proofs = load_proofs()
    exists = any(p.get("channel_id")==str(chat.get("id")) and p.get("post_id")==post_id for p in proofs)
    if not exists:
        proofs.insert(0, proof)
        save_proofs(proofs[:500])
        print(f"✅ Saved: {channel_name} | {status} | {reason}")
    return proof

# ============================================
# POLLING
# ============================================

def poll_telegram():
    print("🤖 Polling started...")
    while True:
        try:
            offset = get_offset()
            res = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30,
                        "allowed_updates": ["channel_post","edited_channel_post"]},
                timeout=40
            )
            data = res.json()
            if not data.get("ok"):
                import time; time.sleep(5); continue

            for update in data.get("result", []):
                save_offset(update["update_id"] + 1)
                if "channel_post" in update:
                    process_message(update["channel_post"])
                if "edited_channel_post" in update:
                    msg = update["edited_channel_post"]
                    proofs = load_proofs()
                    for p in proofs:
                        if p.get("channel_id")==str(msg["chat"]["id"]) and p.get("post_id")==msg.get("message_id"):
                            p["edited"] = True
                            p["edited_text"] = msg.get("text") or ""
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

@flask_app.route("/")
def home():
    return jsonify({"status": "running", "proofs": len(load_proofs()), "deals": len(load_deals())})

@flask_app.route("/proofs")
def get_proofs():
    return jsonify([{k:v for k,v in p.items() if k!="photo"}|{"has_photo":bool(p.get("photo"))} for p in load_proofs()])

@flask_app.route("/proofs/<pid>")
def get_proof(pid):
    for p in load_proofs():
        if p["id"]==pid: return jsonify(p)
    return jsonify({"error":"Not found"}), 404

@flask_app.route("/photo/<pid>")
def get_photo(pid):
    for p in load_proofs():
        if p["id"]==pid and p.get("photo"):
            return send_file(io.BytesIO(base64.b64decode(p["photo"])), mimetype="image/png")
    return jsonify({"error":"Not found"}), 404

@flask_app.route("/proofs/<pid>", methods=["DELETE"])
def del_proof(pid):
    save_proofs([p for p in load_proofs() if p["id"]!=pid])
    return jsonify({"success":True})

@flask_app.route("/stats")
def stats():
    proofs = load_proofs()
    deals = load_deals()
    return jsonify({
        "total": len(proofs),
        "verified": sum(1 for p in proofs if p["status"]=="Verified"),
        "mismatch": sum(1 for p in proofs if p["status"]=="Mismatch"),
        "deals_registered": len(deals),
        "channels": list(set(p["channel"] for p in proofs))
    })

# Deal management APIs
@flask_app.route("/deals")
def get_deals():
    return jsonify(load_deals())

@flask_app.route("/deals", methods=["POST"])
def add_deal():
    """Tool se deal add karo + product ID auto extract"""
    data = request.json
    link = data.get("link","")
    print(f"🔍 Extracting product ID from: {link}")
    
    pid_result = extract_product_id(link)
    
    deal = {
        "id": f"deal_{int(datetime.now().timestamp())}",
        "title": data.get("title",""),
        "client": data.get("client",""),
        "original_link": link,
        "product_id": pid_result.get("product_id"),
        "platform": pid_result.get("platform"),
        "final_url": pid_result.get("final_url"),
        "added": datetime.now().isoformat()
    }
    deals = load_deals()
    deals.insert(0, deal)
    save_deals(deals)
    print(f"✅ Deal added: {deal['title']} | Product ID: {deal['product_id']}")
    return jsonify(deal)

@flask_app.route("/deals/<did>", methods=["DELETE"])
def del_deal(did):
    save_deals([d for d in load_deals() if d["id"]!=did])
    return jsonify({"success":True})

@flask_app.route("/extract", methods=["POST"])
def extract():
    """Link se product ID nikalo — test ke liye"""
    link = request.json.get("link","")
    result = extract_product_id(link)
    return jsonify(result)

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing!")
    else:
        t = threading.Thread(target=poll_telegram, daemon=True)
        t.start()
        print("✅ Bot polling started")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)
