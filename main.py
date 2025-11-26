import os
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, quote, unquote

# --------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("easyznab")

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------
app = FastAPI(title="easyznab Easynews proxy")

EASYNEWS_USER = os.getenv("EASYNEWS_USER")
EASYNEWS_PASS = os.getenv("EASYNEWS_PASS")
EASYNEWS_BASE = "https://members.easynews.com/1.0/global5/index.html"

API_KEY = os.getenv("PROXY_API_KEY", "changeme")

NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
ET.register_namespace("newznab", NEWZNAB_NS)


# --------------------------------------------------------------------
# Helpers for Easynews search
# --------------------------------------------------------------------
def fetch_easynews_rss(query: str) -> bytes:
    """
    Call Easynews RSS search with a simple subject query.
    """
    params = {
        "sbj": query,
        "sS": "5",  # global search
    }
    url = f"{EASYNEWS_BASE}?{urlencode(params)}"
    auth = (EASYNEWS_USER, EASYNEWS_PASS) if EASYNEWS_USER and EASYNEWS_PASS else None

    with httpx.Client(auth=auth, timeout=20.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def tidy_title(raw: str) -> str:
    """
    Try to turn Easynews-style subjects into something nicer.
    """
    title = raw or "No title"

    # 1) Drop a leading hex-like ID token if present
    parts = title.split()
    if parts:
        first = parts[0]
        if len(first) >= 8 and all(c in "0123456789abcdef" for c in first.lower()):
            title = " ".join(parts[1:]) or title

    # 2) Prefer anything inside double quotes
    if '"' in title:
        first_q = title.find('"')
        second_q = title.find('"', first_q + 1)
        if second_q > first_q + 1:
            title = title[first_q + 1 : second_q]

    # 3) Otherwise, fall back to the "(Some.Show.S01E01...)" pattern
    elif "(" in title:
        idx = title.find("(")
        if len(title) - idx > 10:
            title = title[idx + 1 :]
            if title.endswith(")"):
                title = title[:-1]

    # 4) Normalise separators
    title = title.replace(".", " ").replace("_", " ")

    # 5) Collapse repeated spaces and trim
    title = " ".join(title.split())

    return title or "No title"


def convert_to_newznab(
    easynews_xml: bytes,
    category_id: str = "2000",
    category_name: str = "Movies",
) -> bytes:
    """
    Convert Easynews RSS into a Newznab-like RSS.
    Category is passed in so Prowlarr/Radarr/Sonarr can distinguish Movies vs TV vs Audio.
    """
    src_root = ET.fromstring(easynews_xml)

    rss = ET.Element("rss", attrib={"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "Easynews via easyznab"
    ET.SubElement(channel, "description").text = "Easynews global search proxied as Newznab"
    ET.SubElement(channel, "link").text = "https://members.easynews.com"

    src_channel = src_root.find("channel")
    if src_channel is None:
        return ET.tostring(rss, encoding="utf-8", xml_declaration=True)

    for item in src_channel.findall("item"):
        new_item = ET.SubElement(channel, "item")

        raw_title = item.findtext("title") or "No title"
        title = tidy_title(raw_title)

        link = item.findtext("link") or ""
        pubdate = item.findtext("pubDate")

        enc = item.find("enclosure")
        nzb_url = None
        nzb_length = "0"
        if enc is not None:
            nzb_url = enc.get("url")
            nzb_length = enc.get("length") or "0"

        if not nzb_url:
            nzb_url = link

        # Encode once so it is safe in the query string
        proxy_url = f"/proxy_nzb?src={quote(nzb_url)}"

        ET.SubElement(new_item, "title").text = title
        ET.SubElement(new_item, "guid").text = proxy_url
        ET.SubElement(new_item, "link").text = proxy_url
        if pubdate:
            ET.SubElement(new_item, "pubDate").text = pubdate

        # Text category + newznab attr
        ET.SubElement(new_item, "category").text = category_name
        ET.SubElement(
            new_item,
            f"{{{NEWZNAB_NS}}}attr",
            attrib={"name": "category", "value": category_id},
        )

        ET.SubElement(
            new_item,
            "enclosure",
            attrib={
                "url": proxy_url,
                "length": nzb_length,
                "type": "application/x-nzb",
            },
        )

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


# --------------------------------------------------------------------
# Easynews session for NZB downloads
# --------------------------------------------------------------------
def get_easynews_session() -> httpx.Client:
    """
    Create a logged-in Easynews session.

    Uses HTTP basic auth to hit the members page so Easynews will
    set any cookies it needs. We then reuse that session to fetch NZBs.
    """
    if not EASYNEWS_USER or not EASYNEWS_PASS:
        raise RuntimeError("EASYNEWS_USER and EASYNEWS_PASS must be set")

    client = httpx.Client(timeout=60.0)
    client.auth = (EASYNEWS_USER, EASYNEWS_PASS)

    try:
        r = client.get("https://members.easynews.com/", follow_redirects=True)
        log.info(
            "Easynews login: status=%s content_type=%s",
            r.status_code,
            r.headers.get("content-type"),
        )
    except Exception:
        log.exception("Error logging in to Easynews")
        raise

    return client


def guess_filename_from_headers_or_url(
    headers: httpx.Headers, url: str, default: str = "easynews.nzb"
) -> str:
    # Try Content-Disposition first
    cd = headers.get("content-disposition")
    if cd and "filename=" in cd:
        try:
            fn = cd.split("filename=", 1)[1].strip().strip('";')
            if fn:
                return fn
        except Exception:
            pass

    # Fallback: last URL path component (without query)
    try:
        path_part = url.split("?", 1)[0].rsplit("/", 1)[-1]
        if path_part:
            if path_part.lower().endswith(".nzb"):
                return path_part
            return path_part + ".nzb"
    except Exception:
        pass

    return default


# --------------------------------------------------------------------
# Health-check
# --------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------
# NZB proxy
# --------------------------------------------------------------------
@app.get("/proxy_nzb")
def proxy_nzb(
    src: str = Query(
        ...,
        description="URL-encoded Easynews NZB download URL",
    ),
    debug: Optional[bool] = Query(
        False,
        description="If true, logs a short sample of the Easynews body",
    ),
):
    """
    Fetch an NZB from Easynews and stream it back to the caller.
    """
    if not src:
        raise HTTPException(status_code=400, detail="Missing NZB source URL")

    # Decode once to get back the original Easynews URL
    nzb_url = unquote(src)
    log.info("proxy_nzb: decoded Easynews URL: %s", nzb_url)

    try:
        session = get_easynews_session()
    except Exception as e:
        log.exception("Failed to create Easynews session")
        raise HTTPException(status_code=502, detail=str(e))

    try:
        resp = session.get(nzb_url, follow_redirects=True)
    except Exception:
        log.exception("Error fetching NZB from Easynews")
        raise HTTPException(status_code=502, detail="Error contacting Easynews")

    log.info(
        "Easynews NZB fetch: status=%s content_type=%s length=%s",
        resp.status_code,
        resp.headers.get("content-type"),
        len(resp.content),
    )

    if debug:
        try:
            text_sample = resp.text[:500]
        except UnicodeDecodeError:
            text_sample = "<binary>"
        log.info("Easynews body sample (first 500 chars): %r", text_sample)

    if resp.status_code != 200:
        log.error(
            "Easynews returned non-200 status: %s body_sample=%r",
            resp.status_code,
            resp.text[:200]
            if resp.headers.get("content-type", "").startswith("text/")
            else b"",
        )
        raise HTTPException(
            status_code=502,
            detail=f"Easynews returned HTTP {resp.status_code}",
        )

    ctype = resp.headers.get("content-type", "").lower()
    if "nzb" not in ctype and "xml" not in ctype:
        if "html" in ctype or "text" in ctype:
            log.error(
                "Easynews response looks like HTML/text, not NZB. "
                "content-type=%s sample=%r",
                ctype,
                resp.text[:200],
            )
            raise HTTPException(
                status_code=502,
                detail="Easynews did not return NZB data (HTML/text error page?)",
            )

    filename = guess_filename_from_headers_or_url(resp.headers, nzb_url)

    headers = {
        "Content-Type": "application/x-nzb",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }

    return Response(
        content=resp.content,
        media_type="application/x-nzb",
        headers=headers,
    )


# --------------------------------------------------------------------
# Debug endpoint (optional)
# --------------------------------------------------------------------
@app.get("/debug_nzb_plain")
def debug_nzb_plain(url: str = Query(..., description="Raw Easynews NZB URL")):
    log.info("debug_nzb_plain: url=%s", url)
    try:
        session = get_easynews_session()
        resp = session.get(url, follow_redirects=True)
    except Exception:
        log.exception("Error fetching Easynews NZB in debug endpoint")
        raise HTTPException(status_code=502, detail="Error contacting Easynews")

    return {
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type"),
        "length": len(resp.content),
        "body_sample": resp.text[:500]
        if resp.headers.get("content-type", "").startswith("text/")
        else "<binary>",
    }


# --------------------------------------------------------------------
# Newznab API
# --------------------------------------------------------------------
@app.get("/api")
def api_root(
    t: str = Query(..., description="newznab 't' param"),
    apikey: str = Query("", description="API key from Prowlarr"),
    q: str | None = Query(None, description="search query"),
    extended: int | None = Query(None, description="Prowlarr extended flag"),
    imdbid: str | None = Query(None),
    season: str | None = Query(None),
    ep: str | None = Query(None),
):
    # shared key check
    if apikey != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # --- CAPS ---
    if t == "caps":
        caps = f"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server appversion="0.1" version="0.1" title="easyznab" strapline="Easynews proxy" />
  <limits max="100" default="50" />
  <registration available="no" open="no" />
  <searching>
    <search available="yes" supportedParams="q" />
    <tv-search available="yes" supportedParams="q,imdbid,season,ep" />
    <movie-search available="yes" supportedParams="q,imdbid" />
    <audio-search available="yes" supportedParams="q" />
  </searching>
  <categories>
    <category id="2000" name="Movies">
      <subcat id="2010" name="Movies SD" />
      <subcat id="2040" name="Movies HD" />
    </category>
    <category id="5000" name="TV">
      <subcat id="5030" name="TV HD" />
    </category>
    <category id="7000" name="Audio">
      <subcat id="7010" name="Audio Books" />
    </category>
  </categories>
</caps>
"""
        return Response(content=caps, media_type="application/xml")

    # ---- SEARCH MODES ----
    if t in ("search", "tvsearch", "movie", "audio"):
        # Prowlarr test: no q/imdbid at all -> dummy item
        if not q and not imdbid:
            dummy_cat_id = "2000"
            dummy_cat_name = "Movies"
            dummy = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:newznab="{NEWZNAB_NS}">
  <channel>
    <title>Easynews via easyznab</title>
    <description>Dummy search response for indexer tests</description>
    <link>https://members.easynews.com</link>
    <item>
      <title>Dummy Easynews test item</title>
      <guid>/proxy_nzb?src=https%3A//members.easynews.com/dummy.nzb</guid>
      <link>/proxy_nzb?src=https%3A//members.easynews.com/dummy.nzb</link>
      <pubDate>Tue, 25 Mar 2025 12:00:00 GMT</pubDate>
      <category>{dummy_cat_name}</category>
      <newznab:attr name="category" value="{dummy_cat_id}" />
      <enclosure url="/proxy_nzb?src=https%3A//members.easynews.com/dummy.nzb"
                 length="1048576"
                 type="application/x-nzb" />
    </item>
  </channel>
</rss>
"""
            return Response(content=dummy, media_type="application/xml")

        # pick category based on t
        if t == "tvsearch":
            cat_id, cat_name = "5000", "TV"
        elif t == "audio":
            cat_id, cat_name = "7000", "Audio"
        else:  # "search" or "movie"
            cat_id, cat_name = "2000", "Movies"

        # naive imdb fallback
        if not q and imdbid:
            q = imdbid

        easy_xml = fetch_easynews_rss(q)
        newznab_xml = convert_to_newznab(
            easy_xml, category_id=cat_id, category_name=cat_name
        )
        return Response(content=newznab_xml, media_type="application/xml")

    raise HTTPException(status_code=400, detail=f"Unsupported t={t}")
