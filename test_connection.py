import os
import base64
import hashlib
import hmac
import random
import string
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_KEY = os.getenv("ONSHAPE_ACCESS_KEY")
SECRET_KEY = os.getenv("ONSHAPE_SECRET_KEY")
BASE_URL = "https://cad.onshape.com"

def make_nonce():
    return "".join(random.choices(string.ascii_letters + string.digits, k=25))

def make_auth_header(method, path, query="", content_type="application/json"):
    nonce = make_nonce()
    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    # Official Onshape spec: join with \n, then lowercase the WHOLE string
    hmac_str = (
        method + "\n" +
        nonce + "\n" +
        date + "\n" +
        content_type + "\n" +
        path + "\n" +
        query + "\n"
    ).lower().encode("utf-8")

    signature = base64.b64encode(
        hmac.new(SECRET_KEY.encode("utf-8"), hmac_str, digestmod=hashlib.sha256).digest()
    ).decode("utf-8")

    auth_header = f"On {ACCESS_KEY}:HmacSHA256:{signature}"

    return {
        "Authorization": auth_header,
        "On-Nonce": nonce,
        "Date": date,
        "Content-Type": content_type
    }

path = "/api/documents"
query = "ownerType=0"

headers = make_auth_header("GET", path, query)
response = requests.get(f"{BASE_URL}{path}?{query}", headers=headers)

print("STATUS:", response.status_code)
print(response.text)