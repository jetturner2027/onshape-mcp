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
from mcp.server.transport_security import TransportSecuritySettings
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount

load_dotenv()

ACTIVE_DOCUMENT = {"did": None, "wid": None, "eid": None, "namespace": None}
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


def resolve_document_ids(did, wid, eid, namespace=None):
    """
    Falls back to the active document (set via set_active_document, or
    auto-set by get_default_part_studio) for any of did/wid/eid/namespace
    that weren't explicitly passed in.
    """
    did = did or ACTIVE_DOCUMENT["did"]
    wid = wid or ACTIVE_DOCUMENT["wid"]
    eid = eid or ACTIVE_DOCUMENT["eid"]
    namespace = namespace or ACTIVE_DOCUMENT["namespace"]
    return did, wid, eid, namespace


def find_feature_studio_namespace(elements):
    """
    Given the raw list from /api/documents/d/{did}/w/{wid}/elements, finds
    the Feature Studio element and builds the namespace string OnShape
    expects when referencing its custom features: "e{elementId}::m{microversionId}".
    This must be looked up fresh per document, since a copied document gets
    its own Feature Studio element ID and microversion — the namespace is
    NOT the same as the source document's, even though the FeatureScript
    code inside is identical.
    """
    for element in elements:
        if element.get("elementType") == "FEATURESTUDIO":
            fs_id = element.get("id")
            fs_microversion = element.get("microversionId")
            if fs_id and fs_microversion:
                return f"e{fs_id}::m{fs_microversion}"
    return None


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


def boolean_param(param_id, value):
    return {
        "type": 144,
        "typeName": "BTMParameterBoolean",
        "message": {
            "value": bool(value),
            "parameterId": param_id,
            "libraryRelationType": "DEFAULT",
            "parameterName": "",
            "hasUserCode": False
        }
    }


def integer_param(param_id, int_value):
    return {
        "type": 147,
        "typeName": "BTMParameterQuantity",
        "message": {
            "units": "",
            "value": 0.0,
            "expression": str(int(int_value)),
            "isInteger": True,
            "parameterId": param_id,
            "libraryRelationType": "DEFAULT",
            "parameterName": "",
            "hasUserCode": False
        }
    }


@mcp.tool()
def list_documents() -> dict:
    """List OnShape documents owned by the user."""
    return onshape_request("GET", "/api/documents", query="ownerType=0")


@mcp.tool()
def run_featurescript(did: str, wid: str, eid: str, script: str) -> dict:
    """Evaluate a FeatureScript expression and return its result. This is READ-ONLY — it does NOT create or persist any geometry in the document. Use create_cylinder, create_sketch_extrude, or boolean_subtract to actually add shapes. Useful for querying plane IDs, evaluating expressions, or inspecting geometry state."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/featurescript"
    return onshape_request("POST", path, body={"script": script})


@mcp.tool()
def get_features(did: str, wid: str, eid: str) -> dict:
    """Get the current feature list for a part studio."""
    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"
    return onshape_request("GET", path)


def degree_param(param_id, deg_value):
    return {
        "type": 147,
        "typeName": "BTMParameterQuantity",
        "message": {
            "units": "",
            "value": 0.0,
            "expression": f"{deg_value} deg",
            "isInteger": False,
            "parameterId": param_id,
            "libraryRelationType": "DEFAULT",
            "parameterName": "",
            "hasUserCode": False
        }
    }


@mcp.tool()
def create_sketch_extrude(points: list, depth_cm: float,
                           did: str = None, wid: str = None, eid: str = None, namespace: str = None,
                           plane: str = "FRONT", x_cm: float = 0, y_cm: float = 0, z_cm: float = 0,
                           merge: bool = True, draft_deg: float = 0, draft_flip: bool = False,
                           name: str = "Sketch Extrude (from API)") -> dict:
    """Create an extruded shape from an arbitrary 2D polygon. points is a list of [x_cm, y_cm] pairs, e.g. [[0,0],[3,0],[1.5,3]] — the polygon closes automatically. plane is one of FRONT, TOP, RIGHT. merge (default True) merges this shape into any existing touching/overlapping solid so the part studio ends up with one combined part rather than a separate part per shape — the FIRST shape created in an empty part studio MUST use merge=False, since there is nothing yet to merge into. draft_deg (default 0) tapers the extrude's walls by this many degrees — always a positive magnitude; use draft_flip=True to reverse which direction it tapers (wider vs narrower toward the extrude end). If did/wid/eid/namespace are omitted, uses the currently active document (set automatically by get_default_part_studio, or manually via set_active_document). did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio. Returns the created feature's featureId, which can be used later with boolean_subtract."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

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
            "namespace": namespace,
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
                "namespace": namespace,
                "suppressed": False,
                "parameters": [
                    plane_param,
                    points_array,
                    quantity_param("depth", depth_cm),
                    quantity_param("x", x_cm),
                    quantity_param("y", y_cm),
                    quantity_param("z", z_cm),
                    boolean_param("merge", merge),
                    degree_param("draftAngle", draft_deg),
                    boolean_param("draftPullDirection", draft_flip)
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
                     did: str = None, wid: str = None, eid: str = None, namespace: str = None,
                     plane: str = "FRONT", x_cm: float = 0, y_cm: float = 0, z_cm: float = 0,
                     merge: bool = True,
                     name: str = "Cylinder (from API)") -> dict:
    """Create a cylinder. plane is one of FRONT, TOP, RIGHT. merge (default True) merges this shape into any existing touching/overlapping solid so the part studio ends up with one combined part rather than a separate part per shape — set merge=False if you specifically want a standalone body. If did/wid/eid/namespace are omitted, uses the currently active document (set automatically by get_default_part_studio, or manually via set_active_document). did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio. Returns the created feature's featureId, which can be used later with boolean_subtract."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

    plane_param = {
        "type": 145,
        "typeName": "BTMParameterEnum",
        "message": {
            "enumName": "PlaneChoice",
            "value": plane.upper(),
            "namespace": namespace,
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
                "namespace": namespace,
                "suppressed": False,
                "parameters": [
                    plane_param,
                    quantity_param("radius", radius_cm),
                    quantity_param("depth", depth_cm),
                    quantity_param("x", x_cm),
                    quantity_param("y", y_cm),
                    quantity_param("z", z_cm),
                    boolean_param("merge", merge)
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
def create_revolve(points: list, axis_x_cm: float, angle_deg: float,
                    did: str = None, wid: str = None, eid: str = None, namespace: str = None,
                    plane: str = "FRONT", merge: bool = True,
                    name: str = "Revolve (from API)") -> dict:
    """Create a solid of revolution by sketching a 2D profile on the given plane and revolving it around a vertical axis within that same plane. points is a list of [x_cm, y_cm] pairs defining the profile — the polygon closes automatically. axis_x_cm is the x-position (within the sketch plane's own coordinates) of the vertical revolve axis; for a typical revolve, keep all profile points on one side of axis_x_cm to avoid self-intersection. angle_deg is the revolve sweep angle (0-360, positive magnitude — use 360 for a full revolve). plane is one of FRONT, TOP, RIGHT. merge (default True) merges this shape into any existing touching/overlapping solid — the FIRST shape created in an empty part studio MUST use merge=False. Good for lures, handles, knobs, floats, or any part with a rotational axis. If did/wid/eid/namespace are omitted, uses the currently active document. did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio. Returns the created feature's featureId, which can be used later with boolean_subtract."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

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
            "namespace": namespace,
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
                "featureType": "revolveFeature",
                "name": name,
                "namespace": namespace,
                "suppressed": False,
                "parameters": [
                    plane_param,
                    points_array,
                    quantity_param("axisX", axis_x_cm),
                    degree_param("angle", angle_deg),
                    boolean_param("merge", merge)
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
def create_circular_pattern(source_feature_id: str, count: int, angle_step_deg: float,
                             did: str = None, wid: str = None, eid: str = None, namespace: str = None,
                             axis_x_cm: float = 0, axis_y_cm: float = 0) -> dict:
    """Create N-1 additional rotated copies of an existing shape around a vertical axis (total instances = count, including the original). source_feature_id is the featureId of an already-created shape (from create_cylinder, create_sketch_extrude, create_revolve, etc.) in the SAME document/workspace. count is the total number of instances (e.g. count=4 for one original plus 3 copies). angle_step_deg is the angle between each instance in degrees. axis_x_cm/axis_y_cm position the rotation axis (default is the origin, 0,0). Good for bolt-hole circles, repeated bosses, or any radially-repeated feature — much cheaper than calling the shape tool multiple times by hand. If did/wid/eid/namespace are omitted, uses the currently active document."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

    body = {
        "feature": {
            "type": 134,
            "typeName": "BTMFeature",
            "message": {
                "featureType": "circularPatternFeature",
                "name": "Circular Pattern (from API)",
                "namespace": namespace,
                "suppressed": False,
                "parameters": [
                    {
                        "type": 149,
                        "typeName": "BTMParameterString",
                        "message": {
                            "value": source_feature_id,
                            "parameterId": "sourceFeatureId",
                            "libraryRelationType": "DEFAULT",
                            "parameterName": "",
                            "hasUserCode": False
                        }
                    },
                    integer_param("count", count),
                    degree_param("angleStep", angle_step_deg),
                    quantity_param("axisX", axis_x_cm),
                    quantity_param("axisY", axis_y_cm)
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
def fillet_all_edges(target_feature_id: str, radius_cm: float,
                      did: str = None, wid: str = None, eid: str = None, namespace: str = None) -> dict:
    """Round every edge of an existing shape's body by a uniform radius. target_feature_id is the featureId of an already-created shape in the SAME document/workspace. This rounds ALL edges of that body uniformly — there is no way to selectively fillet only some edges (that would require fragile edge-by-edge selection). If only some edges of a part should be rounded, build the rounded and sharp portions as separate bodies and combine them afterward. If did/wid/eid/namespace are omitted, uses the currently active document."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

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
                "featureType": "filletAllEdgesFeature",
                "name": "Fillet All Edges (from API)",
                "namespace": namespace,
                "suppressed": False,
                "parameters": [
                    string_param("targetFeatureId", target_feature_id),
                    quantity_param("radius", radius_cm)
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
def create_tilted_sketch_extrude(points: list, depth_cm: float, tilt_deg: float,
                                  did: str = None, wid: str = None, eid: str = None, namespace: str = None,
                                  plane: str = "FRONT", merge: bool = True,
                                  draft_deg: float = 0, draft_flip: bool = False,
                                  name: str = "Tilted Sketch Extrude (from API)") -> dict:
    """Like create_sketch_extrude, but the sketch plane is tilted by tilt_deg (degrees) around its own local X-axis before the profile is sketched and extruded — useful for angled bosses, tilted pockets, or any feature that isn't perpendicular to the FRONT/TOP/RIGHT planes. points is a list of [x_cm, y_cm] pairs in the TILTED plane's own coordinates (not world coordinates) — the polygon closes automatically. plane is the base plane (FRONT, TOP, or RIGHT) before tilting. draft_deg/draft_flip work the same as in create_sketch_extrude — draft_deg is always a positive magnitude, use draft_flip to reverse taper direction. Same merge rules as the other shape tools — the FIRST shape in an empty part studio must use merge=False. If did/wid/eid/namespace are omitted, uses the currently active document. Returns the created feature's featureId."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

    path = f"/api/partstudios/d/{did}/w/{wid}/e/{eid}/features"

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
            "namespace": namespace,
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
                "featureType": "tiltedExtrudeFeature",
                "name": name,
                "namespace": namespace,
                "suppressed": False,
                "parameters": [
                    plane_param,
                    degree_param("tiltAngle", tilt_deg),
                    points_array,
                    quantity_param("depth", depth_cm),
                    boolean_param("merge", merge),
                    degree_param("draftAngle", draft_deg),
                    boolean_param("draftPullDirection", draft_flip)
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
                      did: str = None, wid: str = None, eid: str = None, namespace: str = None,
                      name: str = "Boolean Subtract (from API)") -> dict:
    """Subtract the tool shape from the target shape, removing tool_feature_id's geometry from target_feature_id's geometry (e.g. cutting a hole). Both feature IDs must come from the featureId returned by an earlier create_cylinder or create_sketch_extrude call in the SAME document/workspace. If did/wid/eid/namespace are omitted, uses the currently active document (set automatically by get_default_part_studio, or manually via set_active_document). did/wid/eid must reference an existing part studio — for a new project, get these from copy_document followed by get_default_part_studio."""
    did, wid, eid, namespace = resolve_document_ids(did, wid, eid, namespace)
    if not (did and wid and eid and namespace):
        return {"error": "No document specified and no active document set. Call get_default_part_studio (after copy_document) or set_active_document, or pass did/wid/eid/namespace explicitly."}

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
                "namespace": namespace,
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
    """Create a new OnShape document by copying the template document, which already has cylinder, sketch-extrude, and boolean-subtract custom features defined. Use this when the user wants to start a new project or design, rather than working in an existing document. Returns newDocumentId and newWorkspaceId. IMPORTANT: after calling this, you must call get_default_part_studio with those two values — it automatically finds the eid AND the correct namespace for this new document's own copy of the custom features, and activates them so subsequent shape tools work without needing did/wid/eid/namespace passed every time."""
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
    """Find the eid (element ID) of the Part Studio in a document/workspace, AND the correct namespace for its custom features (cylinder, sketch-extrude, boolean-subtract). REQUIRED after copy_document, before calling any shape-creation tool on a newly created document — each document (including copies) has its own distinct Feature Studio element and namespace; a namespace from a different document will NOT work here. This automatically activates the result (same effect as calling set_active_document), so create_cylinder/create_sketch_extrude/boolean_subtract can be called right after this with no did/wid/eid/namespace arguments needed."""
    path = f"/api/documents/d/{did}/w/{wid}/elements"
    result = onshape_request("GET", path)

    if DRY_RUN:
        return result

    eid = None
    part_studio_name = None
    for element in result:
        if element.get("elementType") == "PARTSTUDIO":
            eid = element.get("id")
            part_studio_name = element.get("name")
            break

    if not eid:
        return {"error": "No part studio found in this document/workspace."}

    namespace = find_feature_studio_namespace(result)
    if not namespace:
        return {
            "eid": eid,
            "name": part_studio_name,
            "warning": "No Feature Studio found in this document, so no namespace could be resolved. Shape-creation tools (create_cylinder, etc.) will not work here until a Feature Studio with the custom features exists."
        }

    ACTIVE_DOCUMENT["did"] = did
    ACTIVE_DOCUMENT["wid"] = wid
    ACTIVE_DOCUMENT["eid"] = eid
    ACTIVE_DOCUMENT["namespace"] = namespace

    return {
        "eid": eid,
        "name": part_studio_name,
        "namespace": namespace,
        "active_document": dict(ACTIVE_DOCUMENT)
    }


@mcp.tool()
def set_active_document(did: str, wid: str, eid: str) -> dict:
    """Set the current working document/workspace/part studio, so subsequent create_cylinder, create_sketch_extrude, and boolean_subtract calls don't need did/wid/eid/namespace passed explicitly. Automatically looks up the correct, current namespace for this document's Feature Studio (freshly, so it stays correct even if the Feature Studio has been edited since last time). Use this to switch back to an existing document like the sandbox, or as a manual alternative to get_default_part_studio."""
    path = f"/api/documents/d/{did}/w/{wid}/elements"
    result = onshape_request("GET", path)

    if DRY_RUN:
        return result

    namespace = find_feature_studio_namespace(result)

    ACTIVE_DOCUMENT["did"] = did
    ACTIVE_DOCUMENT["wid"] = wid
    ACTIVE_DOCUMENT["eid"] = eid
    ACTIVE_DOCUMENT["namespace"] = namespace

    if not namespace:
        return {
            "active_document": dict(ACTIVE_DOCUMENT),
            "warning": "No Feature Studio found in this document, so no namespace could be resolved. Shape-creation tools will not work here."
        }

    return {"active_document": dict(ACTIVE_DOCUMENT)}


@mcp.tool()
def get_active_document() -> dict:
    """Return the currently active did/wid/eid/namespace, if one has been set with set_active_document or get_default_part_studio."""
    if not ACTIVE_DOCUMENT["did"]:
        return {"error": "No active document set. Call set_active_document or get_default_part_studio first."}
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

    mcp_security = TransportSecuritySettings(
        allowed_hosts=[
            "onshape-mcp-xe4z.onrender.com",
            "onshape-mcp-xe4z.onrender.com:*",
            "127.0.0.1:*",
            "localhost:*",
        ],
        allowed_origins=[
            "https://onshape-mcp-xe4z.onrender.com",
        ],
    )

    mcp_app = mcp.streamable_http_app(transport_security=mcp_security, stateless_http=True)

    app = Starlette(
        routes=[Mount("/", app=mcp_app)],
        middleware=[Middleware(ConnectorAuthMiddleware)],
        lifespan=mcp_app.router.lifespan_context,
    )

    uvicorn.run(app, host="0.0.0.0", port=port)
