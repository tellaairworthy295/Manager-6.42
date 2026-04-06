"""
WeChat Work Grafana Alert Forwarder — async-refactored edition
"""

import asyncio
import base64
import json
import os
import re
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Union
import pandas_market_calendars as mcal
import pandas as pd
import aiofiles
import httpx
import jwt
import uvicorn
import wechat_work_webhook
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

# ==============================================================================
# Section 0: Application lifespan — shared async HTTP client & Semaphores
# ==============================================================================

# Concurrency Limits (Adjust these values based on your API limits)
MAX_CONCURRENT_RENDERS = 3
MAX_CONCURRENT_WECOM_UPLOADS = 5

# Module-level placeholders; populated during startup.
_http_client: Optional[httpx.AsyncClient] = None
_render_semaphore: Optional[asyncio.Semaphore] = None
_wecom_semaphore: Optional[asyncio.Semaphore] = None


def _is_a_share_trading_day(date_obj: datetime) -> bool:
    """
    Pure sync check — pandas_market_calendars is blocking.
    Called via asyncio.to_thread() by the caller.
    """
    exchange = mcal.get_calendar("XSHG")
    timestamp = pd.Timestamp(date_obj.date())
    start_date = timestamp - pd.Timedelta(days=1)
    end_date = timestamp + pd.Timedelta(days=1)
    schedule = exchange.schedule(start_date=start_date, end_date=end_date)
    return timestamp in schedule.index


def get_http_client() -> httpx.AsyncClient:
    """Return the application-wide shared async HTTP client."""
    if _http_client is None:
        raise RuntimeError("HTTP client has not been initialised (app not started?)")
    return _http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and destroy the shared httpx client and semaphores."""
    global _http_client, _render_semaphore, _wecom_semaphore
    limits = httpx.Limits(
        max_keepalive_connections=50,
        max_connections=200,
    )
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0)

    # Initialize shared resources
    _http_client = httpx.AsyncClient(limits=limits, timeout=timeout)
    _render_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
    _wecom_semaphore = asyncio.Semaphore(MAX_CONCURRENT_WECOM_UPLOADS)

    try:
        yield
    finally:
        await _http_client.aclose()
        _http_client = None
        _render_semaphore = None
        _wecom_semaphore = None


# ==============================================================================
# Section 1: WeComAgentSender — fully async, race-condition-free token refresh
# ==============================================================================

class WeComAgentSender:
    """
    Sends markdown text and images via a WeCom Corp Application Agent.

    All network I/O is async (httpx).  The access-token cache is protected by
    an asyncio.Lock so concurrent coroutines never trigger duplicate refreshes.
    """

    def __init__(self, corp_id: str, corp_secret: str, agent_id: int):
        self.corp_id = corp_id
        self.corp_secret = corp_secret
        self.agent_id = agent_id
        self._access_token: Optional[str] = None
        self._token_expire_time: float = 0.0
        self._token_lock = asyncio.Lock()  # ← protects concurrent refresh

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def get_access_token(self) -> str:
        """
        Return a valid access token.

        Uses a double-checked locking pattern: the first check is lock-free for
        the common (already-valid) case; acquisition only happens when a refresh
        is actually needed.
        """
        # Fast path — token still valid
        if self._access_token and time.monotonic() < self._token_expire_time:
            return self._access_token

        async with self._token_lock:
            # Re-check inside the lock (another coroutine may have refreshed)
            if self._access_token and time.monotonic() < self._token_expire_time:
                return self._access_token

            url = (
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
                f"?corpid={self.corp_id}&corpsecret={self.corp_secret}"
            )
            resp = await get_http_client().get(url)
            resp.raise_for_status()
            data = resp.json()

            if data.get("errcode") != 0:
                raise RuntimeError(
                    f"Failed to fetch access_token: {data.get('errmsg')}"
                )

            self._access_token = data["access_token"]
            # Expire 120 s earlier than the real expiry to avoid edge cases
            self._token_expire_time = (
                    time.monotonic() + data.get("expires_in", 7200) - 120
            )
            return self._access_token

    # ------------------------------------------------------------------
    # User lookup
    # ------------------------------------------------------------------

    async def get_userid_by_mobile(self, mobile: str) -> str:
        """Resolve a mobile number to a WeCom user_id."""
        token = await self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/user/getuserid?access_token={token}"
        resp = await get_http_client().post(url, json={"mobile": mobile})
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(
                f"get_userid failed for mobile={mobile}: {data.get('errmsg')}"
            )
        return data["userid"]

    async def resolve_user_ids(self, mobiles: Union[str, List[str]]) -> List[str]:
        """
        Accept a single mobile string, a comma-separated string, or a list
        and return a list of WeCom user IDs.

        Mobile lookups are fired concurrently via asyncio.gather.
        """
        if isinstance(mobiles, str):
            mobiles = [m.strip() for m in mobiles.split(",") if m.strip()]
        # Resolve all mobiles concurrently
        return list(
            await asyncio.gather(
                *(self.get_userid_by_mobile(m) for m in mobiles)
            )
        )

    # ------------------------------------------------------------------
    # Media upload
    # ------------------------------------------------------------------

    async def upload_image(self, image_path: str) -> str:
        """Upload a local image file and return its temporary media_id."""
        token = await self.get_access_token()
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/media/upload"
            f"?access_token={token}&type=image"
        )
        # Read the file asynchronously before posting
        async with aiofiles.open(image_path, "rb") as f:
            content = await f.read()

        resp = await get_http_client().post(
            url,
            files={"media": (os.path.basename(image_path), content, "image/jpeg")},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"Image upload failed: {data.get('errmsg')}")
        return data["media_id"]

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    async def _send_message(self, payload: dict) -> dict:
        """POST a message payload to the WeCom send API."""
        token = await self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        resp = await get_http_client().post(url, json=payload)
        resp.raise_for_status()
        result = resp.json()
        print(f"[Agent] send_message response: {resp.text}")
        return result

    async def send_markdown(
            self,
            content: str,
            user_ids: Optional[List[str]] = None,
            party_ids: Optional[List[str]] = None,
    ) -> dict:
        """Send a markdown message to users and/or departments."""
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
            raise ValueError(
                "send_markdown requires at least one of user_ids or party_ids"
            )

        result = await self._send_message(payload)
        if result.get("errcode") == 0:
            return {"success": True, "msgid": result.get("msgid")}
        return {
            "success": False,
            "errcode": result.get("errcode"),
            "errmsg": result.get("errmsg"),
        }

    async def send_image(
            self,
            image_path: str,
            user_ids: Optional[List[str]] = None,
            party_ids: Optional[List[str]] = None,
    ) -> dict:
        """Upload and send an image to users and/or departments."""
        media_id = await self.upload_image(image_path)
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
            raise ValueError(
                "send_image requires at least one of user_ids or party_ids"
            )

        result = await self._send_message(payload)
        if result.get("errcode") == 0:
            return {
                "success": True,
                "msgid": result.get("msgid"),
                "invaliduser": result.get("invaliduser"),
            }
        return {
            "success": False,
            "errcode": result.get("errcode"),
            "errmsg": result.get("errmsg"),
        }


# ==============================================================================
# Section 2: Token helpers for the agent URL  (unchanged logic)
# ==============================================================================

def encode_agent_token(corp_id: str, corp_secret: str, agent_id: int) -> str:
    """
    Encode corp credentials into a URL-safe base64 token.

    Format before encoding:  ``<corp_id>:<corp_secret>:<agent_id>``
    """
    raw = f"{corp_id}:{corp_secret}:{agent_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_agent_token(agent_token: str) -> Tuple[str, str, int]:
    """Decode a base64 agent token back into (corp_id, corp_secret, agent_id)."""
    try:
        raw = base64.urlsafe_b64decode(agent_token.encode()).decode()
        parts = raw.split(":", 2)
        if len(parts) != 3:
            raise ValueError("Expected 3 colon-separated parts")
        corp_id, corp_secret, agent_id_str = parts
        return corp_id, corp_secret, int(agent_id_str)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid agent_token: {exc}. "
                "Generate one with encode_agent_token(corp_id, corp_secret, agent_id)."
            ),
        )


# ==============================================================================
# Section 3: Grafana helpers  (async I/O, concurrent rendering)
# ==============================================================================

def generate_grafana_jwt(
        org_id: str, private_key_path: str = "private.pem"
) -> Optional[str]:
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


def convert_to_render_url(
        panel_url: str, vars_dict: dict, width: str, height: str
) -> str:
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
    """Convert a Unix timestamp value to yyyy-MM-dd (UTC+8) if applicable."""
    try:
        str_val = str(value).strip()
        timestamp = float(str_val) if "." in str_val else int(str_val)
        if 0 < timestamp < 4102444800:
            dt = datetime.fromtimestamp(timestamp, tz=None)
            return dt.strftime("%Y-%m-%d")
        return value
    except (ValueError, TypeError, OverflowError):
        return value


GRAFANA_ORG_TOKENS: Dict[int, str] = {
    1: "glsa_F5RgzBEnTHGkCuh8ZQSVFGVh0YrfXbx5_fdd2e1d8",
    2: "glsa_slZNz8ZkIekwGEuT3MB5MNP8XajwrjPR_71cbd3d2",
    4: "glsa_nk0C6aMIBwXIjhPXvhPYK9LRff6HZ1G6_9a2a2254",
}
GRAFANA_FALLBACK_TOKEN: Optional[str] = None


def _get_render_token(org_id: int) -> str:
    token = GRAFANA_ORG_TOKENS.get(org_id, GRAFANA_FALLBACK_TOKEN)
    if not token:
        raise ValueError(
            f"No Grafana service account token configured for orgId={org_id}."
        )
    return token


def _extract_org_id(url: str) -> int:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(qs.get("orgId", ["1"])[0])
    except (ValueError, IndexError):
        return 1


async def download_panel_image(
        panel_url: str, vars_dict: dict, width: str, height: str
) -> Optional[str]:
    """
    Render and download a Grafana panel image asynchronously.

    Uses the shared httpx client and aiofiles for non-blocking disk writes.
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

    org_id = _extract_org_id(panel_url)
    try:
        bearer_token = _get_render_token(org_id)
    except ValueError as exc:
        print(f"[Render] Token error: {exc}")
        return None

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "X-Grafana-Org-Id": str(org_id),
    }
    print(f"[Render] orgId={org_id}  url={render_url}")

    resp = await get_http_client().get(render_url, headers=headers)

    if resp.status_code != 200:
        print(f"[Render] Failed (HTTP {resp.status_code}): {resp.text[:300]}")
        if resp.status_code == 404:
            print(
                f"[Render] 404 hint: the service account token for orgId={org_id} may "
                "not belong to that org, or the panel/dashboard UID does not exist."
            )
        return None

    dt = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
    filename = os.path.join(image_dir, f"img_org{org_id}_{dt}.jpg")

    # Async file write — does not block the event loop
    async with aiofiles.open(filename, "wb") as f:
        await f.write(resp.content)

    print(f"[Render] Saved image → {filename}")
    return filename


async def _cleanup_image(path: str) -> None:
    """Silently remove a temporary image file (async-friendly)."""
    try:
        if path and os.path.exists(path):
            # os.remove is fast enough that run_in_executor is overkill here,
            # but wrapping it keeps the event loop strictly unblocked.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, os.remove, path)
    except Exception as exc:
        print(f"[Cleanup] Could not remove {path}: {exc}")


# ==============================================================================
# Section 4: Alert parsing  (pure CPU — unchanged)
# ==============================================================================

def parse_labels(labels_str: str) -> dict:
    labels: dict = {}
    content = labels_str.strip("{}")
    if not content:
        return labels
    pattern = r"(\w+)=(.*?)(?=,\s*\w+=|$)"
    for match in re.finditer(pattern, content, re.DOTALL):
        labels[match.group(1).strip()] = match.group(2).strip()
    return labels


def parse_display_labels(content: str) -> dict:
    display_labels = {}
    if not content or content == "None":
        return display_labels
    pattern = r"(\w+)==(.*?)(?=\n\w+==|$)"
    for match in re.finditer(pattern, str(content), re.DOTALL):
        display_labels[match.group(1).strip()] = match.group(2).strip()
    return display_labels


def format_record_line(record: dict) -> str:
    display_labels = parse_display_labels(record["labels"].get("标签", ""))
    if record["value"] == -1.0:
        pairs = [f"{k}: {v}" for k, v in display_labels.items()]
        return f"> - {', '.join(pairs)}\n"
    labels_str = ", ".join(display_labels.values())
    return f'> - {labels_str}: <font color="warning">{record["value"]}</font>'


def parse_grafana_payload(
        data: dict, is_agent: bool
) -> Tuple[str, List[Tuple[str, dict, str, str]]]:
    """Parse a raw Grafana alerting webhook payload (pure CPU, no I/O)."""
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
            width = annotations.get("width", "800")
            height = annotations.get("height", "1000")
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
                alerts_by_type[title]["records"].append(
                    {"var": var_name, "labels": labels, "value": value}
                )

    # Reorder: Shibor then IRR last
    for key in [t for t in alerts_by_type if "Shibor" in t]:
        alerts_by_type[key] = alerts_by_type.pop(key)
    for key in [t for t in alerts_by_type if "IRR" in t]:
        alerts_by_type[key] = alerts_by_type.pop(key)

    last_title_for_url: dict = {}
    for title, ad in alerts_by_type.items():
        # Deduplicate records
        deduped_records: dict = {}
        for record in ad["records"]:
            labels = record["labels"]
            record_time = labels.get("时间")
            display_labels = parse_display_labels(labels.get("标签", ""))
            other_labels = {k: v for k, v in display_labels.items() if k != "时间"}
            filter_labels = frozenset(other_labels.items())
            group_key = (record["var"], filter_labels)
            if group_key not in deduped_records:
                deduped_records[group_key] = record
            else:
                existing_record = deduped_records[group_key]
                existing_time = existing_record["labels"].get("时间")
                if record_time and existing_time:
                    if str(record_time) > str(existing_time):
                        deduped_records[group_key] = record
                else:
                    deduped_records[group_key] = record
        ad["records"] = list(deduped_records.values())
        ad["records"].sort(
            key=lambda r: str(r.get("labels", {}).get("排序", ""))
        )
        if ad.get("md", "0") != "0" and ad.get("panel_url"):
            last_title_for_url[ad["panel_url"]] = title

    details: list = []
    panel_urls_and_vars: list = []
    seen_urls: set = set()

    for title, ad in alerts_by_type.items():
        if ad.get("md", "0") != "0":
            if is_agent:
                lines = [f"##### 类型: {title}"]
            else:
                lines = [f"> **类型: {title}** ------"]
            if ad["formatted_time"] is not None:
                lines.append(f"> **时间: {ad['formatted_time']}**")
            for record in ad["records"]:
                lines.append(format_record_line(record))
            if ad.get("panel_url"):
                if last_title_for_url.get(ad["panel_url"]) == title:
                    lines.append(f"> 🔗 [查看图表]({ad['panel_url']})")
            details.append("\n".join(lines))

        if (
                ad.get("img", "0") != "0"
                and ad.get("panel_url")
                and ad["panel_url"] not in seen_urls
        ):
            seen_urls.add(ad["panel_url"])
            panel_urls_and_vars.append(
                (ad["panel_url"], ad["vars_dict"], ad["width"], ad["height"])
            )

    markdown_content = (
        "\n> ---------------------------\n".join(details) + "\n" if details else ""
    )
    return markdown_content, panel_urls_and_vars


# ==============================================================================
# Section 5: FastAPI application
# ==============================================================================

app = FastAPI(
    title="WeChat Work Grafana Alert Forwarder",
    description=(
        "Two delivery modes:\n\n"
        "- **Group Bot** → `POST /webhook/bot/{webhook_key}`\n"
        "- **Corp Agent** → `POST /webhook/agent/{agent_token}"
        "[?mobiles=...&party_ids=...]`\n\n"
        "Generate `agent_token` with `encode_agent_token(corp_id, corp_secret, agent_id)`."
    ),
    lifespan=lifespan,  # ← modern replacement for on_event("startup/shutdown")
)


# --------------------------------------------------------------------------
# Shared helper: render all panel images concurrently and return filenames
# --------------------------------------------------------------------------

async def _render_all_images(
        panel_urls_and_vars: List[Tuple[str, dict, str, str]],
        host_replacements: List[Tuple[str, str]],
) -> List[Optional[str]]:
    async def _one(panel_url: str, vars_dict: dict, width: str, height: str):
        render_url = panel_url
        for old, new in host_replacements:
            render_url = render_url.replace(old, new)

        # Limit concurrent requests to the Grafana/Render service
        async with _render_semaphore:
            return await download_panel_image(render_url, vars_dict, str(width), str(height))

    return list(
        await asyncio.gather(
            *(_one(url, vd, w, h) for url, vd, w, h in panel_urls_and_vars),
            return_exceptions=False,
        )
    )


# --------------------------------------------------------------------------
# Route 1 — Group Bot
# --------------------------------------------------------------------------

HOST_REPLACEMENTS = [
    ("localhost", "10.32.132.155"),
    ("10.29.92.50", "10.32.132.155"),
]


@app.post("/webhook/bot/{token}", summary="Deliver via Group Chat Bot")
async def webhook_bot(token: str, data: dict) -> PlainTextResponse:
    """
    Receive a Grafana alert webhook and forward it to a **WeCom group chat bot**.

    The sync `wechat_work_webhook` library is called in a thread-pool executor
    so it never blocks the asyncio event loop.
    """
    date = datetime.now()
    date_str = date.strftime("%Y-%m-%d")

    if not _is_a_share_trading_day(date):
        print(f"[Stocks] {date_str} is not a trading day, skipping.")
        return PlainTextResponse(f"skipping {date_str}")
    markdown_content, panel_urls_and_vars = parse_grafana_payload(data, False)

    loop = asyncio.get_running_loop()
    webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={token}"

    # Send markdown (offload blocking lib to thread pool)
    if markdown_content:
        def _send_md():
            wechat = wechat_work_webhook.connect(webhook_url)
            wechat.markdown(markdown_content)

        await loop.run_in_executor(None, _send_md)

    # Render all panel images concurrently
    if panel_urls_and_vars:
        filenames = await _render_all_images(panel_urls_and_vars, HOST_REPLACEMENTS)

        async def _send_one_image(filename: Optional[str], url: str):
            if not filename:
                print(f"[Bot] Skipping image for {url} — download failed.")
                return

            def _send_img():
                wechat = wechat_work_webhook.connect(webhook_url)
                wechat.image(filename)

            # Limit concurrent uploads to WeCom Bot
            async with _wecom_semaphore:
                await loop.run_in_executor(None, _send_img)

            await _cleanup_image(filename)

        await asyncio.gather(
            *(
                _send_one_image(fn, url)
                for fn, (url, _, __, ___) in zip(filenames, panel_urls_and_vars)
            )
        )

    return PlainTextResponse("OK")


# --------------------------------------------------------------------------
# Route 2 — Corp Agent
# --------------------------------------------------------------------------

@app.post("/webhook/agent/{agent_token}", summary="Deliver via Corp Agent")
async def webhook_agent(
        agent_token: str,
        data: dict,
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
    party_ids : str
        Comma-separated department IDs.

    At least one of *mobiles* or *party_ids* must be provided.
    """
    date = datetime.now()
    date_str = date.strftime("%Y-%m-%d")

    if not _is_a_share_trading_day(date):
        print(f"[Stocks] {date_str} is not a trading day, skipping.")
        return PlainTextResponse(f"skipping {date_str}")

    if not mobiles and not party_ids:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of ?mobiles=... or ?party_ids=... in the URL.",
        )

    corp_id, corp_secret, agent_id = decode_agent_token(agent_token)
    sender = WeComAgentSender(corp_id, corp_secret, agent_id)

    # Resolve recipients (concurrent mobile lookups inside resolve_user_ids)
    user_ids: List[str] = []
    if mobiles:
        try:
            user_ids = await sender.resolve_user_ids(mobiles)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Mobile lookup failed: {exc}")

    dept_ids: List[str] = (
        [p.strip() for p in party_ids.split(",") if p.strip()] if party_ids else []
    )

    markdown_content, panel_urls_and_vars = parse_grafana_payload(data, True)

    # Send markdown
    if markdown_content:
        try:
            md_result = await sender.send_markdown(
                content=markdown_content,
                user_ids=user_ids or ["EMP-151583"],
                party_ids=dept_ids or None,
            )
            print(f"[Agent] Markdown send result: {md_result}")
        except Exception as exc:
            print(f"[Agent] Markdown send error: {exc}")

    # Render all panel images concurrently, then send them concurrently
    if panel_urls_and_vars:
        filenames = await _render_all_images(panel_urls_and_vars, HOST_REPLACEMENTS)

        async def _send_one_image(filename: Optional[str], url: str):
            if not filename:
                print(f"[Agent] Skipping image for {url} — download failed.")
                return
            try:
                # Limit concurrent uploads to WeCom Agent
                async with _wecom_semaphore:
                    img_result = await sender.send_image(
                        image_path=filename,
                        user_ids=user_ids or ["EMP-151583"],
                        party_ids=dept_ids or None,
                    )
                print(f"[Agent] Image send result: {img_result}")
            except Exception as exc:
                print(f"[Agent] Image send error: {exc}")
            finally:
                await _cleanup_image(filename)

        await asyncio.gather(
            *(
                _send_one_image(fn, url)
                for fn, (url, _, __, ___) in zip(filenames, panel_urls_and_vars)
            )
        )

    return PlainTextResponse("OK")


# ==============================================================================
# Section 6: Entry point + token generation helper
# ==============================================================================

if __name__ == "__main__":
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
    print("  Group bot  : http://YOUR_SERVER:38888/webhook/bot/<webhook_key>")
    print(
        f"  Corp agent : http://YOUR_SERVER:38888/webhook/agent/{sample_token}"
        "?mobiles=13800000001&party_ids=3"
    )
    print("=" * 60)

    # For production, prefer: gunicorn main:app -k uvicorn.workers.UvicornWorker --workers 4
    uvicorn.run(app, host="0.0.0.0", port=38888)