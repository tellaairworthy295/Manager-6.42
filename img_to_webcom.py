import os
import re
import json
import time
import jwt
import base64
import urllib.parse
import requests
import wechat_work_webhook

from datetime import datetime, timezone
from typing import List, Union, Optional, Tuple, Dict

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn


# ==============================================================================
# Section 1: WeComAgentSender — Corp Agent (send to users/departments)
# ==============================================================================

class WeComAgentSender:
    """
    Sends markdown text and images via a WeCom Corp Application Agent.
    Credentials are carried in the URL token (base64 of corp_id:corp_secret:agent_id).
    """

    def __init__(self, corp_id: str, corp_secret: str, agent_id: int):
        self.corp_id = corp_id
        self.corp_secret = corp_secret
        self.agent_id = agent_id
        self._access_token: Optional[str] = None
        self._token_expire_time: float = 0

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        """Return a valid access token, fetching a new one when necessary."""
        if self._access_token and time.time() < self._token_expire_time:
            return self._access_token

        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
            f"?corpid={self.corp_id}&corpsecret={self.corp_secret}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("errcode") != 0:
            raise RuntimeError(f"Failed to fetch access_token: {data.get('errmsg')}")

        self._access_token = data["access_token"]
        # Expire 120 s earlier than the real expiry to avoid edge cases
        self._token_expire_time = time.time() + data.get("expires_in", 7200) - 120
        return self._access_token

    # ------------------------------------------------------------------
    # User lookup
    # ------------------------------------------------------------------

    def get_userid_by_mobile(self, mobile: str) -> str:
        """Resolve a mobile number to a WeCom user_id."""
        token = self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/user/getuserid?access_token={token}"
        resp = requests.post(url, json={"mobile": mobile}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(
                f"get_userid failed for mobile={mobile}: {data.get('errmsg')}"
            )
        return data["userid"]

    def resolve_user_ids(self, mobiles: Union[str, List[str]]) -> List[str]:
        """
        Accept a single mobile string, a comma-separated string, or a list
        and return a list of WeCom user IDs.
        """
        if isinstance(mobiles, str):
            mobiles = [m.strip() for m in mobiles.split(",") if m.strip()]
        return [self.get_userid_by_mobile(m) for m in mobiles]

    # ------------------------------------------------------------------
    # Media upload
    # ------------------------------------------------------------------

    def upload_image(self, image_path: str) -> str:
        """Upload a local image file and return its temporary media_id."""
        token = self.get_access_token()
        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/media/upload"
            f"?access_token={token}&type=image"
        )
        with open(image_path, "rb") as f:
            resp = requests.post(url, files={"media": f}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"Image upload failed: {data.get('errmsg')}")
        return data["media_id"]

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def _send_message(self, payload: dict) -> dict:
        """POST a message payload to the WeCom send API."""
        token = self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        print(f"[Agent] send_message response: {resp.text}")
        return result

    def send_markdown(
        self,
        content: str,
        user_ids: Optional[List[str]] = None,
        party_ids: Optional[List[str]] = None,
    ) -> dict:
        """
        Send a markdown message to users and/or departments.
        At least one of user_ids or party_ids must be provided.
        """
        payload: dict = {
            "msgtype": "markdown",
            "agentid": self.agent_id,
            "markdown": {"content": content},
            "safe": 0,
            "enable_id_trans": 0,
            "enable_duplicate_check": 0,
        }
        if user_ids:
            payload["touser"] = "|".join(user_ids)
        if party_ids:
            payload["toparty"] = "|".join(str(p) for p in party_ids)
        if not user_ids and not party_ids:
            raise ValueError("send_markdown requires at least one of user_ids or party_ids")

        result = self._send_message(payload)
        if result.get("errcode") == 0:
            return {"success": True, "msgid": result.get("msgid")}
        return {"success": False, "errcode": result.get("errcode"), "errmsg": result.get("errmsg")}

    def send_image(
        self,
        image_path: str,
        user_ids: Optional[List[str]] = None,
        party_ids: Optional[List[str]] = None,
    ) -> dict:
        """
        Upload and send an image to users and/or departments.
        At least one of user_ids or party_ids must be provided.
        """
        media_id = self.upload_image(image_path)
        payload: dict = {
            "msgtype": "image",
            "agentid": self.agent_id,
            "image": {"media_id": media_id},
            "safe": 0,
            "enable_id_trans": 0,
            "enable_duplicate_check": 0,
        }
        if user_ids:
            payload["touser"] = "|".join(user_ids)
        if party_ids:
            payload["toparty"] = "|".join(str(p) for p in party_ids)
        if not user_ids and not party_ids:
            raise ValueError("send_image requires at least one of user_ids or party_ids")

        result = self._send_message(payload)
        if result.get("errcode") == 0:
            return {
                "success": True,
                "msgid": result.get("msgid"),
                "invaliduser": result.get("invaliduser"),
            }
        return {"success": False, "errcode": result.get("errcode"), "errmsg": result.get("errmsg")}


# ==============================================================================
# Section 2: Token helpers for the agent URL
# ==============================================================================

def encode_agent_token(corp_id: str, corp_secret: str, agent_id: int) -> str:
    """
    Encode corp credentials into a URL-safe base64 token.

    Format before encoding:  ``<corp_id>:<corp_secret>:<agent_id>``

    Example
    -------
    >>> token = encode_agent_token("ww123", "secret456", 1000057)
    >>> # Use the returned string as the {agent_token} path segment
    """
    raw = f"{corp_id}:{corp_secret}:{agent_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_agent_token(agent_token: str) -> Tuple[str, str, int]:
    """
    Decode a base64 agent token back into (corp_id, corp_secret, agent_id).

    Raises
    ------
    HTTPException(400) if the token is malformed.
    """
    try:
        raw = base64.urlsafe_b64decode(agent_token.encode()).decode()
        parts = raw.split(":", 2)          # max 2 splits → 3 parts
        if len(parts) != 3:
            raise ValueError("Expected 3 colon-separated parts")
        corp_id, corp_secret, agent_id_str = parts
        return corp_id, corp_secret, int(agent_id_str)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_token: {exc}. "
                   "Generate one with encode_agent_token(corp_id, corp_secret, agent_id).",
        )


# ==============================================================================
# Section 3: Grafana helpers (shared by both endpoints)
# ==============================================================================

def generate_grafana_jwt(org_id: str, private_key_path: str = "private.pem") -> Optional[str]:
    """Generate a short-lived RS256 JWT for Grafana SSO login."""
    try:
        with open(private_key_path, "r") as f:
            private_key = f.read()

        payload = {
            "sub": f"wecom_viewer_org{org_id}",
            "email": f"org{org_id}@qq.com",
            "name": f"WeCom Viewer (Org {org_id})",
            "exp": int(time.time()) + 3600 * 24,
        }
        return jwt.encode(
            payload,
            private_key,
            algorithm="RS256",
            headers={"kid": "grafana-wecom-key"},
        )
    except Exception as exc:
        print(f"[JWT] Error generating JWT: {exc}")
        return None


def convert_to_render_url(panel_url: str, vars_dict: dict, width: str, height: str) -> str:
    """Convert a Grafana dashboard share URL to an image render URL."""
    parsed = urllib.parse.urlparse(panel_url)

    path_parts = parsed.path.split("/")
    if len(path_parts) >= 4 and path_parts[1] in ("d", "dashboard"):
        uid = path_parts[2]
        slug = path_parts[3] if len(path_parts) > 3 else ""
        new_path = f"/render/d-solo/{uid}/{slug}"
    else:
        raise ValueError(f"Invalid panel URL format: {panel_url}")

    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query_params["timezone"] = ["Asia/Shanghai"]

    view_panel_value = query_params.pop("viewPanel", [None])[0]
    if not view_panel_value:
        raise ValueError("The 'viewPanel' parameter is missing from the URL.")

    panel_id_str = view_panel_value.lstrip("panel-")
    try:
        panel_id = int(panel_id_str)
    except ValueError:
        raise ValueError(
            f"The 'viewPanel' value '{view_panel_value}' is not a valid numeric panel ID."
        )

    query_params["panelId"] = [str(panel_id)]

    if isinstance(vars_dict, dict):
        for var_name, var_value in vars_dict.items():
            processed_value = _process_timestamp_if_needed(var_value)
            query_params[f"var-{var_name}"] = [str(processed_value)]

    query_params["width"] = [width]
    query_params["height"] = [height]

    return urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        new_path,
        parsed.params,
        urllib.parse.urlencode(query_params, doseq=True),
        parsed.fragment,
    ))


def _process_timestamp_if_needed(value):
    """
    Check if a value is a Unix timestamp and convert it to yyyy-MM-dd format in UTC+8 (Beijing Time).
    If not a timestamp, return the original value.
    """
    try:
        # Convert to string first to handle both string and numeric inputs
        str_val = str(value).strip()

        # Check if the string represents a number
        if '.' in str_val:
            timestamp = float(str_val)
        else:
            timestamp = int(str_val)

        # Validate if it's a reasonable Unix timestamp
        # Basic check: must be positive and less than year 2100 (approx 4.1e9)
        if 0 < timestamp < 4102444800:  # 2100-01-01 as Unix timestamp
            # Check if it looks like a millisecond timestamp (future dates will be too far ahead)
            if timestamp > 2000000000:  # Approximate threshold for year 2033
                # Could be milliseconds, but let's assume it's seconds for now
                # If you know your data uses milliseconds, uncomment next line:
                # timestamp = timestamp / 1000
                pass

            dt = datetime.fromtimestamp(timestamp, tz=None)

            return dt.strftime('%Y-%m-%d')
        else:
            return value
    except (ValueError, TypeError, OverflowError):
        # If conversion fails, return original value
        return value


GRAFANA_ORG_TOKENS: Dict[int, str] = {
    1: "glsa_F5RgzBEnTHGkCuh8ZQSVFGVh0YrfXbx5_fdd2e1d8",
    2: "glsa_slZNz8ZkIekwGEuT3MB5MNP8XajwrjPR_71cbd3d2",
    4: "glsa_nk0C6aMIBwXIjhPXvhPYK9LRff6HZ1G6_9a2a2254",
    # add more orgs as needed
}

# Fallback token used when the orgId is not found in the map above.
# Set this to a Grafana Server Admin token if you want a true catch-all,
# or leave as None to surface the misconfiguration explicitly.
GRAFANA_FALLBACK_TOKEN: Optional[str] = None


token2 = "glsa_vJeTM7peYYLZUlxuYplYgPVlrXXBEmXR_a4de4480"
token4 = "glsa_RiK3O2BB8sL6dqBADdHgU2wAYlLxUPYi_42018c99"


def _get_render_token(org_id: int) -> str:
    """
    Return the service account token for the given Grafana org.

    Raises
    ------
    ValueError
        If no token is configured for the org and no fallback is set.
    """
    token = GRAFANA_ORG_TOKENS.get(org_id, GRAFANA_FALLBACK_TOKEN)
    if not token:
        raise ValueError(
            f"No Grafana service account token configured for orgId={org_id}. "
            f"Add an entry to GRAFANA_ORG_TOKENS or set GRAFANA_FALLBACK_TOKEN."
        )
    return token


def _extract_org_id(url: str) -> int:
    """
    Parse the orgId query parameter from a Grafana URL.

    Returns 1 (Grafana's default org) if the parameter is absent.
    """
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(qs.get("orgId", ["1"])[0])
    except (ValueError, IndexError):
        return 1


def download_panel_image(panel_url: str, vars_dict: dict, width: str, height: str) -> Optional[str]:
    """
    Render and download a Grafana panel image.

    Automatically selects the correct service account token based on the
    orgId embedded in the panel URL, so cross-org rendering works correctly.

    Returns the local file path on success, or None on failure.
    """
    image_dir = "images"
    os.makedirs(image_dir, exist_ok=True)

    try:
        render_url = convert_to_render_url(panel_url, vars_dict, width, height)
    except ValueError as exc:
        print(f"[Render] Could not build render URL from {panel_url}: {exc}")
        return None
    print(render_url)
    # Select the right token for this org
    org_id = _extract_org_id(panel_url)
    try:
        bearer_token = _get_render_token(org_id)
    except ValueError as exc:
        print(f"[Render] Token error: {exc}")
        return None

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "X-Grafana-Org-Id": f"{org_id}",
    }

    print(f"[Render] orgId={org_id}  url={render_url}")

    resp = requests.get(render_url, headers=headers, timeout=60)

    if resp.status_code != 200:
        print(f"[Render] Failed (HTTP {resp.status_code}): {resp.text[:300]}")
        # Emit a helpful hint for the most common mistake
        if resp.status_code == 404:
            print(
                f"[Render] 404 hint: the service account token for orgId={org_id} may "
                f"not belong to that org, or the panel/dashboard UID does not exist in "
                f"that org. Verify the token was created while Org {org_id} was active."
            )
        return None

    dt = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
    filename = os.path.join(image_dir, f"img_org{org_id}_{dt}.jpg")
    with open(filename, "wb") as f:
        f.write(resp.content)

    print(f"[Render] Saved image → {filename}")
    return filename


def _cleanup_image(path: str) -> None:
    """Silently remove a temporary image file."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        print(f"[Cleanup] Could not remove {path}: {exc}")


# ==============================================================================
# Section 4: Alert parsing (shared by both endpoints)
# ==============================================================================

def parse_labels(labels_str: str) -> dict:
    """Parse a Prometheus-style label string ``{k=v, ...}`` into a dict."""
    labels: dict = {}
    content = labels_str.strip("{}")
    if not content:
        return labels
    pattern = r"(\w+)=(.*?)(?=,\s*\w+=|$)"
    for match in re.finditer(pattern, content, re.DOTALL):
        labels[match.group(1).strip()] = match.group(2).strip()
    return labels


def parse_display_labels(content: str) -> dict:
    """Parse custom 'key==value' formatted strings from the '标签' field into a dict."""
    display_labels = {}
    if not content or content == "None":
        return display_labels

    pattern = r"(\w+)==(.*?)(?=\n\w+==|$)"
    for match in re.finditer(pattern, str(content), re.DOTALL):
        display_labels[match.group(1).strip()] = match.group(2).strip()
    return display_labels


def format_record_line(record: dict) -> str:
    """Format a single alert record as a WeCom markdown bullet."""
    # Use the helper function here
    display_labels = parse_display_labels(record['labels'].get('标签', ''))

    if record["value"] == -1.0:
        pairs = [f"{k}: {v}" for k, v in display_labels.items()]
        return f"> - {', '.join(pairs)}\n"

    labels_str = ", ".join(display_labels.values())
    return f'> - {labels_str}: <font color="warning">{record["value"]}</font>'


def parse_grafana_payload(data: dict) -> Tuple[str, List[Tuple[str, dict, str, str]]]:
    """
    Parse a raw Grafana alerting webhook payload.

    Returns
    -------
    markdown_content : str
        Fully-formed markdown string ready to be sent (empty string if md=0 for all).
    panel_urls_and_vars : list of (url, vars_dict, width, height)
        Unique tuples to render as images (only populated when img!=0).
    """
    alerts: list = data.get("alerts", [])

    alerts_by_type: dict = {}
    for alert in alerts:
        alert_labels = alert.get("labels", {})
        title = alert_labels.get("alertname", "Unknown")
        img = str(alert_labels.get("img", 0))
        md = str(alert_labels.get("md", 0))

        if title not in alerts_by_type:
            timestamp = (alert.get("values") or {}).get("Time")
            formatted_time = None
            if timestamp:
                dt_object = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                formatted_time = dt_object.strftime("%Y-%m-%d %H:%M:%S")

            annotations = alert.get("annotations", {})
            panel_url = annotations.get("panel_url")

            width = annotations.get("width", '800')
            height = annotations.get("height", '1000')

            vars_str = annotations.get("vars")
            vars_dict = json.loads(vars_str) if isinstance(vars_str, str) else {}

            alerts_by_type[title] = {
                "records": [],
                "panel_url": panel_url,
                "img": img,
                "md": md,
                "width": width,
                "height": height,
                "formatted_time": formatted_time,
                "vars_dict": vars_dict,
            }

        value_string = alert.get("valueString", "")
        block_pattern = (
            r"\[\s*var='([^']+)'[^]]*labels=({[^}]*})[^]]*value=([^\s\]]+)\s*\]"
        )
        for match in re.finditer(block_pattern, value_string):
            var_name, labels_str, value_str = match.groups()
            labels = parse_labels(labels_str) if labels_str != "{}" else {}
            try:
                value = float(value_str)
            except ValueError:
                value = value_str  # type: ignore[assignment]

            if labels or var_name == "最新值":
                alerts_by_type[title]["records"].append({
                    "var": var_name,
                    "labels": labels,
                    "value": value,
                })

    # ====================================================================
    # REORDERING: Move "Shibor_3M" and any title containing "IRR" to the end
    # ====================================================================
    keys_to_move = [t for t in alerts_by_type if "IRR" in t or t == "Shibor_3M"]
    for t in keys_to_move:
        alerts_by_type[t] = alerts_by_type.pop(t)

    last_title_for_url = {}

    for title, ad in alerts_by_type.items():
        # [Feature 1] Deduplicate records
        deduped_records = {}
        for record in ad["records"]:
            labels = record["labels"]
            record_time = labels.get("时间")

            # Extract labels using the new helper function
            display_labels = parse_display_labels(labels.get('标签', ''))

            # Filter out '时间' and create the frozen set for grouping
            other_labels = {k: v for k, v in display_labels.items() if k != '时间'}
            filter_labels = frozenset(other_labels.items())

            group_key = (record["var"], filter_labels)

            if group_key not in deduped_records:
                deduped_records[group_key] = record
            else:
                existing_record = deduped_records[group_key]
                existing_time = existing_record["labels"].get("时间")

                # If both have a '时间', standard string comparison keeps the newest datetime
                if record_time and existing_time:
                    if str(record_time) > str(existing_time):
                        deduped_records[group_key] = record
                else:
                    # If no time to compare, just overwrite with the newest processed record
                    deduped_records[group_key] = record

        # Apply the deduplicated records back to the alert definition
        ad["records"] = list(deduped_records.values())

        # [Feature 2] Sort records based on '排序' value
        # Fixed variable shadowing bug by using a lambda function.
        # Defaults to empty string to prevent errors if '排序' is missing.
        ad["records"].sort(key=lambda r: str(r.get('labels', {}).get('排序', '')))

        # [Feature 3] Track the last title that uses each panel_url
        if ad.get("md", "0") != "0" and ad.get("panel_url"):
            last_title_for_url[ad["panel_url"]] = title

    # ====================================================================
    # BUILD MARKDOWN & QUEUE IMAGES
    # ====================================================================

    details: list = []
    panel_urls_and_vars: list = []
    seen_urls: set = set()

    for title, ad in alerts_by_type.items():
        # ONLY process Markdown text if md != 0
        if ad.get("md", "0") != "0":
            lines = [f'> <span style="font-size: 1.2em;">类型: {title}</span> ------']

            if ad["formatted_time"] is not None:
                lines.append(f"> **时间: {ad['formatted_time']}**")

            for record in ad["records"]:
                lines.append(format_record_line(record))

            if ad.get("panel_url"):
                # ONLY display the URL if this alert type is the LAST one referencing it
                if last_title_for_url.get(ad["panel_url"]) == title:
                    lines.append(f"> 🔗 [查看图表]({ad['panel_url']})")

            details.append("\n".join(lines))

        # ONLY queue Panel URL for Image Rendering if img != 0
        if ad.get("img", "0") != "0" and ad.get("panel_url") and ad["panel_url"] not in seen_urls:
            seen_urls.add(ad["panel_url"])
            panel_urls_and_vars.append((ad["panel_url"], ad["vars_dict"], ad["width"], ad["height"]))

    # Join multiple alerts cleanly, or return empty string if no md blocks were built
    if details:
        markdown_content = "\n> ---------------------------\n".join(details) + "\n"
    else:
        markdown_content = ""

    return markdown_content, panel_urls_and_vars


# ==============================================================================
# Section 5: FastAPI application
# ==============================================================================

app = FastAPI(
    title="WeChat Work Grafana Alert Forwarder",
    description=(
        "Two delivery modes:\n\n"
        "- **Group Bot** → `POST /webhook/bot/{webhook_key}`\n"
        "- **Corp Agent** → `POST /webhook/agent/{agent_token}[?mobiles=...&party_ids=...]`\n\n"
        "Generate `agent_token` with `encode_agent_token(corp_id, corp_secret, agent_id)`."
    ),
)

# --------------------------------------------------------------------------
# Route 1 — Group Bot  (original behaviour, zero breaking change)
# --------------------------------------------------------------------------


@app.post("/webhook/bot/{token}", summary="Deliver via Group Chat Bot")
async def webhook_bot(token: str, request: Request) -> PlainTextResponse:
    """
    Receive a Grafana alert webhook and forward it to a **WeCom group chat bot**.

    Path parameter
    --------------
    token : str
        The webhook key from the WeCom group bot URL.
    """
    raw = await request.body()
    data = json.loads(raw)

    markdown_content, panel_urls_and_vars = parse_grafana_payload(data)

    # Send markdown via group bot
    wechat = wechat_work_webhook.connect(
        f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={token}"
    )
    if markdown_content:
        wechat.markdown(markdown_content)

    # Render and send panel images
    for panel_url, vars_dict, width, height in panel_urls_and_vars:
        render_url = panel_url.replace("localhost", "10.32.132.155").replace(
            "10.29.92.50", "10.32.132.155"
        )
        filename = download_panel_image(render_url, vars_dict, str(width), str(height))
        if filename:
            wechat.image(filename)
            _cleanup_image(filename)
        else:
            print(f"[Bot] Skipping image for {render_url} — download failed.")

    return PlainTextResponse("OK")


# --------------------------------------------------------------------------
# Route 2 — Corp Agent  (new behaviour: send to specific users / departments)
# --------------------------------------------------------------------------

@app.post("/webhook/agent/{agent_token}", summary="Deliver via Corp Agent")
async def webhook_agent(
    agent_token: str,
    request: Request,
    mobiles: str = "",
    party_ids: str = "",
) -> PlainTextResponse:
    """
    Receive a Grafana alert webhook and forward it via a **WeCom Corp Application Agent**.

    Path parameter
    --------------
    agent_token : str
        Base64-encoded credentials — generate with ``encode_agent_token()``.

    Query parameters
    ----------------
    mobiles : str
        Comma-separated mobile numbers to resolve to user IDs.
        Example: ``?mobiles=13800000001,13800000002``
    party_ids : str
        Comma-separated department IDs.
        Example: ``?party_ids=2,5``

    At least one of *mobiles* or *party_ids* must be provided.

    Notes
    -----
    These query parameters must be appended to the webhook URL you configure
    in Grafana's notification policy.  Grafana passes the URL as-is, so embed
    the recipients once when creating the contact point URL, e.g.::

        http://your-server:38888/webhook/agent/<token>?mobiles=138XXXXXXXX&party_ids=3
    """
    if not mobiles and not party_ids:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of ?mobiles=... or ?party_ids=... in the URL.",
        )

    # Decode credentials from the URL token
    corp_id, corp_secret, agent_id = decode_agent_token(agent_token)
    sender = WeComAgentSender(corp_id, corp_secret, agent_id)

    # Resolve recipients
    user_ids: List[str] = []
    if mobiles:
        try:
            user_ids = sender.resolve_user_ids(mobiles)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Mobile lookup failed: {exc}")

    dept_ids: List[str] = (
        [p.strip() for p in party_ids.split(",") if p.strip()] if party_ids else []
    )

    # Parse the Grafana payload
    raw = await request.body()
    data = json.loads(raw)
    markdown_content, panel_urls_and_vars = parse_grafana_payload(data)

    if markdown_content:
        try:
            md_result = sender.send_markdown(
                content=markdown_content,
                user_ids=user_ids or ["EMP-151583"],
                party_ids=dept_ids or None,
            )
            print(f"[Agent] Markdown send result: {md_result}")
        except Exception as exc:
            print(f"[Agent] Markdown send error: {exc}")

    # Render and send panel images
    for panel_url, vars_dict, width, height in panel_urls_and_vars:
        render_url = panel_url.replace("localhost", "10.32.132.155").replace(
            "10.29.92.50", "10.32.132.155"
        )
        filename = download_panel_image(render_url, vars_dict, str(width), str(height))
        if filename:
            try:
                img_result = sender.send_image(
                    image_path=filename,
                    user_ids=user_ids or ["EMP-151583"],
                    party_ids=dept_ids or None,
                )
                print(f"[Agent] Image send result: {img_result}")
            except Exception as exc:
                print(f"[Agent] Image send error: {exc}")
            finally:
                _cleanup_image(filename)
        else:
            print(f"[Agent] Skipping image for {panel_url} — download failed.")

    return PlainTextResponse("OK")


# ==============================================================================
# Section 6: Entry point + token generation helper
# ==============================================================================

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Quick helper: run this block once to print your agent_token, then
    # paste it into your Grafana contact point URL.
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("Agent token generator")
    print("=" * 60)
    sample_token = encode_agent_token(
        corp_id="wx4fe684f1fb7538e5",
        corp_secret="baHccr2vt3CrL7YWy_ZrWa0z6SupKpIFZzZAA2pMwbg",
        agent_id=1000057,
    )
    print(f"Your agent_token : {sample_token}")
    print()
    print("Grafana contact point URLs:")
    print(f"  Group bot  : http://YOUR_SERVER:38888/webhook/bot/<webhook_key>")
    print(f"  Corp agent : http://YOUR_SERVER:38888/webhook/agent/{sample_token}?mobiles=13800000001&party_ids=3")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=38888)