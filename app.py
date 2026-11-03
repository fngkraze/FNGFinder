import os
import re
import time
import threading
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
lock = threading.Lock()
cache = {}
requests_by_ip = {}
daily = {"date": "", "count": 0}
MAX_DAILY_SCANS = int(os.getenv("MAX_DAILY_SCANS", "5"))
PAGES_PER_QUERY = 1  # Conserves quota: broad searches return up to 50 videos each
CACHE_SECONDS = 21600
MAX_IP_SCANS_PER_HOUR = 3
MAX_SEARCH_REQUESTS = 6
MAX_ALIASES = 3


def yt(endpoint, **params):
    url = "https://www.googleapis.com/youtube/v3/" + endpoint + "?" + urlencode({"key": API_KEY, **params})
    try:
        import json
        with urlopen(Request(url, headers={"User-Agent": "2oo1finder/1.0"}), timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        import json
        try:
            msg = json.loads(error.read().decode()).get("error", {}).get("message", str(error))
        except Exception:
            msg = str(error)
        raise ValueError(msg)
    except (URLError, TimeoutError):
        raise ValueError("YouTube is temporarily unreachable.")


def normalize(value):
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^a-z0-9]", "", value)


def is_own_channel(channel, aliases, handle, own_id, channel_id):
    if own_id and channel_id == own_id:
        return True
    value = normalize(channel)
    if handle and value == normalize(handle):
        return True
    names = aliases if isinstance(aliases, list) else [aliases]
    for candidate in names:
        name = normalize(candidate)
        if value == name or value in {name + "beats", name + "typebeats", "prod" + name, name + "music"}:
            return True
    return False


def is_instrumental(title, description):
    t = title.lower()
    if re.search(r"\b(type beat|free beat|instrumental|beat pack|sample pack|drum kit|loop kit|fl studio tutorial|making a beat)\b", t):
        return True
    return bool(re.search(r"\b(buy this beat|lease this beat|purchase this beat)\b", description.lower())
                and not re.search(r"\b(music video|official audio|freestyle|remix)\b", t))


def confidence(title, description, aliases):
    combined = title + " " + description
    for alias in aliases:
        escaped = re.escape(alias)
        pattern = re.compile(r"(?i)(?:prod\.?\s*(?:by\s*)?|produced\s+by\s+|beatmaker\s+)" + escaped + r"\b")
        if pattern.search(combined):
            return "Explicit producer credit", 3
    if any(normalize(alias) in normalize(combined) for alias in aliases):
        return "Name mention — verify", 1
    return "No visible credit — review", 0


@app.get("/")
def home():
    return render_template("index.html", ready=bool(API_KEY))


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/api/scan")
def scan():
    if not API_KEY:
        return jsonify(error="The website owner must configure YOUTUBE_API_KEY on Render."), 503
    body = request.get_json(silent=True) or {}
    raw_producer = str(body.get("producer", ""))
    aliases = list(dict.fromkeys(" ".join(part.split()).strip() for part in raw_producer.split(",")))
    producer = aliases[0] if aliases else ""
    handle = str(body.get("handle", "")).strip().removeprefix("@")
    hide_instrumentals = body.get("hide_beats", True) is not False
    if not 1 <= len(aliases) <= MAX_ALIASES or any(
        not 2 <= len(alias) <= 50 or not re.fullmatch(r"[\w .'-]+", alias, re.UNICODE)
        for alias in aliases
    ):
        return jsonify(error="Enter 1–3 comma-separated producer names (2–50 characters each)."), 400
    if handle and (len(handle) > 40 or not re.fullmatch(r"[\w.-]+", handle)):
        return jsonify(error="Invalid YouTube handle."), 400
    cache_key = (tuple(a.casefold() for a in aliases), handle.casefold(), hide_instrumentals)
    now = time.time()
    today = datetime.now(timezone.utc).date().isoformat()
    # Bounded shared API quota; cache hits don't use quota.
    with lock:
        cached = cache.get(cache_key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return jsonify(**cached[1], cached=True)
        if daily["date"] != today:
            daily.update(date=today, count=0)
            requests_by_ip.clear()
        # Limits apply per server instance. For larger deployments use Redis.
        ip = request.remote_addr or "unknown"
        history = [stamp for stamp in requests_by_ip.get(ip, []) if now - stamp < 3600]
        if len(history) >= MAX_IP_SCANS_PER_HOUR:
            return jsonify(error="Hourly search limit reached. Please try later."), 429
        if daily["count"] >= MAX_DAILY_SCANS:
            return jsonify(error="Today's free search quota is used. Try again tomorrow."), 429
        history.append(now)
        requests_by_ip[ip] = history
        daily["count"] += 1

    own_id = ""
    if handle:
        try:
            chan = yt("channels", part="id", forHandle="@" + handle)
            own_id = (chan.get("items") or [{}])[0].get("id", "")
        except ValueError:
            pass

    # Broader queries return more name-only songs per unit than eight narrowly phrased searches.
    # Every alias gets up to two complementary searches; total max six requests.
    queries = [(alias, f'"{alias}"') for alias in aliases]
    queries += [(alias, f'"{alias}" music video') for alias in aliases]
    search_budget = MAX_SEARCH_REQUESTS
    videos = {}
    try:
        request_count = 0
        for alias, query in queries:
            # Ensure each alias is searched before spending remaining quota on extra pages.
            if request_count >= search_budget:
                break
            data = yt("search", part="snippet", q=query, maxResults=50,
                      type="video", order="relevance")
            request_count += 1
            for item in data.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})
                title = snippet.get("title", "")
                channel = snippet.get("channelTitle", "")
                channel_id = snippet.get("channelId", "")
                if not video_id or is_own_channel(channel, aliases, handle, own_id, channel_id):
                    continue
                entry = videos.setdefault(video_id, {
                    "id": video_id, "title": title, "channel": channel,
                    "channel_id": channel_id, "description": snippet.get("description", ""),
                    "published": snippet.get("publishedAt", "")[:10],
                    "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                    "url": "https://www.youtube.com/watch?v=" + video_id,
                    "views": 0, "search_aliases": []
                })
                if alias not in entry["search_aliases"]:
                    entry["search_aliases"].append(alias)

        ids = list(videos)
        for offset in range(0, len(ids), 50):
            response = yt("videos", part="snippet,statistics", id=",".join(ids[offset:offset+50]))
            for item in response.get("items", []):
                video = videos.get(item["id"])
                if not video:
                    continue
                info = item.get("snippet", {})
                video["description"] = info.get("description", "")
                video["views"] = int(item.get("statistics", {}).get("viewCount", 0))
        results = []
        for video in videos.values():
            if is_own_channel(video["channel"], aliases, handle, own_id, video["channel_id"]):
                continue
            if hide_instrumentals and is_instrumental(video["title"], video["description"]):
                continue
            label, score = confidence(video["title"], video["description"], aliases)
            combined = video["title"] + " " + video["description"]
            literal_hits = [a for a in aliases if a.casefold() in combined.casefold()]
            normalized_hits = [a for a in aliases if normalize(a) in normalize(combined)]
            video["matched_aliases"] = literal_hits or normalized_hits or video["search_aliases"]
            video["credit"] = label
            video["score"] = score
            results.append(video)
        results.sort(key=lambda v: (-v["score"], -v["views"]))
        result = {"results": results, "total": len(results), "producer": producer, "aliases": aliases,
                  "note": "Quota-efficient discovery: up to two broad searches per alias. Name-only matches are included; verify songs manually."}
        with lock:
            cache[cache_key] = (time.time(), result)
        return jsonify(**result, cached=False)
    except ValueError as error:
        message = str(error)
        if "quota" in message.lower() or "dailylimit" in message.lower():
            return jsonify(error="YouTube's daily API quota is exhausted. It usually resets at midnight Pacific time. Try again after the reset."), 429
        return jsonify(error="YouTube API error: " + message), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
