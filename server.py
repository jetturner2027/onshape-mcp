import os
import base64
import hashlib
import hmac
import random
import string
import json
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

ACCESS_KEY = os.getenv("ONSHAPE_ACCESS_KEY")
SECRET_KEY = os.getenv("ONSHAPE_SECRET_KEY")
BASE_URL = "https://cad.onshape.com"

# Set to False only when we're ready to actually hit the API
DRY_RUN = True

mcp = MCPServer("onshape-connector")


def make_nonce():
    return "".join(random.choices(string.ascii_letters + string.digits, k=25))


def make_auth_header(method, path, query="", content_type="application/json"):
    nonce = make_nonce()
    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

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


def onshape_request(method, path, query="", body=None):
    """
    Central request function. In DRY_RUN mode, prints what WOULD be sent
    and returns a fake response instead of hitting the API.
    """
    if DRY_RUN:
        preview = {
            "method": method,
            "url": f"{BASE_URL}{path}" + (f"?{query}" if query else ""),
            "body": body
        }
        print("DRY RUN — would send:")
        print(json.dumps(preview, indent=2))
        return {"dry_run": True, "preview": preview}

    headers = make_auth_header(method, path, query)
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")

    if method.upper() == "GET":
        resp = requests.get(url, headers=headers)
    elif method.upper() == "POST":
        resp = requests.post(url, headers=headers, json=body)
    else:
        raise ValueError(f"Unsupported method: {method}")

    if not resp.ok:
        print("ERROR RESPONSE BODY:", resp.text)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def list_documents() -> dict:
    """List OnShape documents owned by the user."""
    return onshape_request("GET", "/api/documents", query="ownerType=0")


@mcp.tool()
def create_document(name: str) -> dict:
    """Create a new OnShape document with the given name."""
    return onshape_request("POST", "/api/documents", body={"name": name})


@mcp.tool()
def run_featurescript(did: str, wid: str, eid: str, script: str) -> dict:
    """Run a FeatureScript snippet against a part studio to create or modify geometry."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/featurescript"
    return onshape_request("POST", path, body={"script": script})


@mcp.tool()
def get_features(did: str, wid: str, eid: str) -> dict:
    """Get the current feature list for a part studio."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"
    return onshape_request("GET", path)


@mcp.tool()
def create_box(did: str, wid: str, eid: str, width_cm: float, depth_cm: float, height_cm: float,
                x_cm: float = 0, y_cm: float = 0, z_cm: float = 0,
                name: str = "Box (from API)") -> dict:
    """Create a box in the given part studio using the custom boxFeature, at position x, y, z."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

    def quantity_param(param_id, cm_value):
        return {
            "type": 147,
            "typeName": "BTMParameterQuantity",
            "message": {
                "units": "",
                "value": 0.0,
                "expression": f"{cm_value} cm",
                "isInteger": False,
                "parameterId": param_id,
                "libraryRelationType": "DEFAULT",
                "parameterName": "",
                "hasUserCode": False
            }
        }

    body = {
        "feature": {
            "type": 134,
            "typeName": "BTMFeature",
            "message": {
                "featureType": "boxFeature",
                "name": name,
                "namespace": "efc3267f3a3b8cd872eba3c1d::m236e471bb36410a40b6af0ac",
                "suppressed": False,
                "parameters": [
                    quantity_param("width", width_cm),
                    quantity_param("depth", depth_cm),
                    quantity_param("height", height_cm),
                    quantity_param("x", x_cm),
                    quantity_param("y", y_cm),
                    quantity_param("z", z_cm)
                ],
                "subFeatures": [],
                "returnAfterSubfeatures": False,
                "suppressionState": {"type": 0},
                "parameterLibraries": [],
                "hasUserCode": False
            }
        }
    }

    return onshape_request("POST", path, body=body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)