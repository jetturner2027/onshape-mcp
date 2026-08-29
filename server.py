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
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount

load_dotenv()

ACTIVE_DOCUMENT = {"did": None, "wid": None, "eid": None}
ACCESS_KEY = os.getenv("ONSHAPE_ACCESS_KEY")
SECRET_KEY = os.getenv("ONSHAPE_SECRET_KEY")
BASE_URL = "https://cad.onshape.com"

# Controlled via the DRY_RUN environment variable (set in Render's dashboard).
# Defaults to "true" (safe) if not set at all. Accepts "true"/"false" (case-insensitive).
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

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


def resolve_document_ids(did, wid, eid):
    """
    Falls back to the active document (set via set_active_document) for any
    of did/wid/eid that weren't explicitly passed in.
    """
    did = did or ACTIVE_DOCUMENT["did"]
    wid = wid or ACTIVE_DOCUMENT["wid"]
    eid = eid or ACTIVE_DOCUMENT["eid"]
    return did, wid, eid


@mcp.tool()
def list_documents() -> dict:
    """List OnShape documents owned by the user."""
    return onshape_request("GET", "/api/documents", query="ownerType=0")


@mcp.tool()
def run_featurescript(did: str, wid: str, eid: str, script: str) -> dict:
    """Evaluate a FeatureScript expression and return its result. This is READ-ONLY — it does NOT create or persist any geometry in the document. Use create_box, create_cylinder, create_sketch_extrude, or boolean_subtract to actually add shapes. Useful for querying plane IDs, evaluating expressions, or inspecting geometry state."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/featurescript"
    return onshape_request("POST", path, body={"script": script})


@mcp.tool()
def get_features(did: str, wid: str, eid: str) -> dict:
    """Get the current feature list for a part studio."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"
    return onshape_request("GET", path)


@mcp.tool()
def create_box(width_cm: float, depth_cm: float, height_cm: float,
                did: str = None, wid: str = None, eid: str = None,
                x_cm: float = 0, y_cm: float = 0, z_cm: float = 0,
                name: str = "Box (from API)") -> dict:
    """Create a box using the custom boxFeature, at position x, y, z. If did/wid/eid are omitted, uses the currently active document set via set_active_document. did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio. Returns the created feature's featureId, which can be used later with boolean_subtract."""
    did, wid, eid = resolve_document_ids(did, wid, eid)
    if not (did and wid and eid):
        return {"error": "No document specified and no active document set. Call set_active_document or pass did/wid/eid explicitly."}

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


@mcp.tool()
def create_sketch_extrude(points: list, depth_cm: float,
                           did: str = None, wid: str = None, eid: str = None,
                           plane: str = "FRONT", x_cm: float = 0, y_cm: float = 0, z_cm: float = 0,
                           name: str = "Sketch Extrude (from API)") -> dict:
    """Create an extruded shape from an arbitrary 2D polygon. points is a list of [x_cm, y_cm] pairs, e.g. [[0,0],[3,0],[1.5,3]] — the polygon closes automatically. plane is one of FRONT, TOP, RIGHT. If did/wid/eid are omitted, uses the currently active document set via set_active_document. did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio. Returns the created feature's featureId, which can be used later with boolean_subtract."""
    did, wid, eid = resolve_document_ids(did, wid, eid)
    if not (did and wid and eid):
        return {"error": "No document specified and no active document set. Call set_active_document or pass did/wid/eid explicitly."}

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

    def point_item(px_cm, py_cm):
        return {
            "type": 1843,
            "typeName": "BTMArrayParameterItem",
            "message": {
                "parameters": [
                    quantity_param("px", px_cm),
                    quantity_param("py", py_cm)
                ],
                "hasUserCode": False
            }
        }

    points_array = {
        "type": 2025,
        "typeName": "BTMParameterArray",
        "message": {
            "items": [point_item(p[0], p[1]) for p in points],
            "parameterId": "points",
            "libraryRelationType": "DEFAULT",
            "parameterName": "",
            "hasUserCode": False
        }
    }

    plane_param = {
        "type": 145,
        "typeName": "BTMParameterEnum",
        "message": {
            "enumName": "PlaneChoice",
            "value": plane.upper(),
            "namespace": "efc3267f3a3b8cd872eba3c1d::m39a9a67356b63278366e62e5",
            "parameterId": "planeChoice",
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
                "featureType": "sketchExtrudeFeature",
                "name": name,
                "namespace": "efc3267f3a3b8cd872eba3c1d::m39a9a67356b63278366e62e5",
                "suppressed": False,
                "parameters": [
                    plane_param,
                    points_array,
                    quantity_param("depth", depth_cm),
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


@mcp.tool()
def create_cylinder(radius_cm: float, depth_cm: float,
                     did: str = None, wid: str = None, eid: str = None,
                     plane: str = "FRONT", x_cm: float = 0, y_cm: float = 0, z_cm: float = 0,
                     name: str = "Cylinder (from API)") -> dict:
    """Create a cylinder. plane is one of FRONT, TOP, RIGHT. If did/wid/eid are omitted, uses the currently active document set via set_active_document. did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio. Returns the created feature's featureId, which can be used later with boolean_subtract."""
    did, wid, eid = resolve_document_ids(did, wid, eid)
    if not (did and wid and eid):
        return {"error": "No document specified and no active document set. Call set_active_document or pass did/wid/eid explicitly."}

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

    plane_param = {
        "type": 145,
        "typeName": "BTMParameterEnum",
        "message": {
            "enumName": "PlaneChoice",
            "value": plane.upper(),
            "namespace": "efc3267f3a3b8cd872eba3c1d::m5d2006221798a1a8c823fbbe",
            "parameterId": "planeChoice",
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
                "featureType": "cylinderFeature",
                "name": name,
                "namespace": "efc3267f3a3b8cd872eba3c1d::m5d2006221798a1a8c823fbbe",
                "suppressed": False,
                "parameters": [
                    plane_param,
                    quantity_param("radius", radius_cm),
                    quantity_param("depth", depth_cm),
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


@mcp.tool()
def boolean_subtract(target_feature_id: str, tool_feature_id: str,
                      did: str = None, wid: str = None, eid: str = None,
                      name: str = "Boolean Subtract (from API)") -> dict:
    """Subtract the tool shape from the target shape, removing tool_feature_id's geometry from target_feature_id's geometry (e.g. cutting a hole). Both feature IDs must come from the featureId returned by an earlier create_box, create_cylinder, or create_sketch_extrude call in the SAME document/workspace. If did/wid/eid are omitted, uses the currently active document set via set_active_document. did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio."""
    did, wid, eid = resolve_document_ids(did, wid, eid)
    if not (did and wid and eid):
        return {"error": "No document specified and no active document set. Call set_active_document or pass did/wid/eid explicitly."}

    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

    def string_param(param_id, value):
        return {
            "type": 149,
            "typeName": "BTMParameterString",
            "message": {
                "value": value,
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
                "featureType": "booleanSubtractFeature",
                "name": name,
                "namespace": "efc3267f3a3b8cd872eba3c1d::m160f2bd7354b94be96bb15c1",
                "suppressed": False,
                "parameters": [
                    string_param("targetFeatureId", target_feature_id),
                    string_param("toolFeatureId", tool_feature_id)
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


@mcp.tool()
def copy_document(new_name: str) -> dict:
    """Create a new OnShape document by copying the template document, which already has box, cylinder, sketch-extrude, and boolean-subtract custom features defined. Use this when the user wants to start a new project or design, rather than working in an existing document. Returns newDocumentId and newWorkspaceId. IMPORTANT: after calling this, you must call get_default_part_studio with those two values to get the eid, then call set_active_document so subsequent shape tools work without needing did/wid/eid passed every time."""
    template_did = "e7a7295f056b7f47b58fb416"
    template_wid = "db250fad1eec3791e9940156"

    path = f"/api/documents/{template_did}/workspaces/{template_wid}/copy"
    body = {
        "newName": new_name,
        "isPublic": False
    }

    return onshape_request("POST", path, body=body)


@mcp.tool()
def get_default_part_studio(did: str, wid: str) -> dict:
    """Find the eid (element ID) of the Part Studio in a document/workspace. REQUIRED after copy_document, before calling any shape-creation tool (create_box, create_cylinder, create_sketch_extrude, boolean_subtract) on a newly created document — those tools need did, wid, AND eid, and eid is not returned by copy_document. After getting the eid, call set_active_document so subsequent shape tools don't need did/wid/eid passed every time."""
    path = f"/api/documents/d/{did}/w/{wid}/elements"
    result = onshape_request("GET", path)

    if DRY_RUN:
        return result

    for element in result:
        if element.get("elementType") == "PARTSTUDIO":
            return {"eid": element.get("id"), "name": element.get("name")}

    return {"error": "No part studio found in this document/workspace."}


@mcp.tool()
def set_active_document(did: str, wid: str, eid: str) -> dict:
    """Set the current working document/workspace/part studio, so subsequent create_box, create_cylinder, create_sketch_extrude, and boolean_subtract calls don't need did/wid/eid passed explicitly. Call this once after copy_document + get_default_part_studio, or to switch back to an existing document like the sandbox."""
    ACTIVE_DOCUMENT["did"] = did
    ACTIVE_DOCUMENT["wid"] = wid
    ACTIVE_DOCUMENT["eid"] = eid
    return {"active_document": dict(ACTIVE_DOCUMENT)}


@mcp.tool()
def get_active_document() -> dict:
    """Return the currently active did/wid/eid, if one has been set with set_active_document."""
    if not ACTIVE_DOCUMENT["did"]:
        return {"error": "No active document set. Call set_active_document first."}
    return dict(ACTIVE_DOCUMENT)


class ConnectorAuthMiddleware(BaseHTTPMiddleware):
    """
    Rejects any request that doesn't carry the correct x-api-key header.
    Set the CONNECTOR_SECRET environment variable (in Render's dashboard) to enable
    this check. If CONNECTOR_SECRET is unset, the check is skipped entirely — this
    keeps local testing (no env var set) working without extra setup.
    Uses x-api-key (rather than a custom header name) because claude.ai's custom
    connector settings only allow a small set of pre-approved header names.
    """
    async def dispatch(self, request, call_next):
        secret = os.getenv("CONNECTOR_SECRET")
        if secret:
            provided = request.headers.get("x-api-key")
            if provided != secret:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))

    mcp_app = mcp.streamable_http_app()

    app = Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=[Middleware(ConnectorAuthMiddleware)],
        lifespan=mcp_app.router.lifespan_context,
    )

    uvicorn.run(app, host="0.0.0.0", port=port)
