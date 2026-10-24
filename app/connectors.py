"""Bounded, versioned source ingestion. Internet access is explicit, never implicit RAG."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import tempfile
import time
from urllib.parse import urljoin, urlsplit

import httpx
from defusedxml import ElementTree

from .database import now, uid
from .ingestion import ingest, MAX_FILE
from .providers import Providers

MAX_WEB_BYTES = 5_000_000
MAX_FEED_ITEMS = 20


def public_url(url):
    """Resolve and pin a public destination; redirects are checked independently."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an HTTP(S) URL without embedded credentials.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port != (443 if parts.scheme == "https" else 80):
            raise ValueError("Web sources must use standard HTTP(S) ports.")
        addresses = {record[4][0] for record in socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)}
    except (OSError, ValueError) as exc:
        raise ValueError("Source host could not be resolved to a public HTTP(S) destination.") from exc
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("Private, loopback, link-local and reserved network sources are blocked.")
    return parts, sorted(addresses)[0]


def fetch_public(url, max_bytes=MAX_WEB_BYTES, allowed_hosts=None):
    deadline=time.monotonic()+60
    for _ in range(5):
        if time.monotonic()>deadline:
            raise ValueError("Source download exceeded its time limit.")
        parts, address = public_url(url)
        if allowed_hosts and parts.hostname.lower() not in allowed_hosts:
            raise ValueError("The source or redirect destination is not in the administrator's allowlist.")
        # Pin the validated IP to avoid DNS rebinding. TLS still verifies the original hostname.
        host = parts.hostname.encode("idna").decode("ascii")
        address = f"[{address}]" if ":" in address else address
        target = f"{parts.scheme}://{address}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            request = client.build_request("GET", target, headers={"Host": host, "User-Agent": "MarketLens/1.0 (+source-import)", "Accept-Encoding": "identity"},
                                           extensions={"sni_hostname": host})
            response = client.send(request, stream=True)
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if not response.headers.get("location"):
                        raise ValueError("Source redirected without a destination.")
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                # Decompressors may allocate huge output before iter_bytes can
                # enforce its limit. Request uncompressed content and fail closed
                # when a server ignores that request.
                if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    raise ValueError("Source must support uncompressed downloads.")
                content = bytearray()
                for chunk in response.iter_raw(chunk_size=65536):
                    if time.monotonic()>deadline:
                        raise ValueError("Source download exceeded its time limit.")
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise ValueError("Source exceeds the web download limit.")
                return bytes(content), url, response.headers.get("content-type", "")
            finally:
                response.close()
    raise ValueError("Source has too many redirects.")


def article_text(data, url):
    import trafilatura
    extracted = trafilatura.extract(data, url=url, output_format="json", with_metadata=True,
                                    include_tables=True, include_comments=False)
    if not extracted:
        raise ValueError("No readable article was found; upload a copy or use another source.")
    article = json.loads(extracted)
    text = article.get("text", "").strip()
    if not text:
        raise ValueError("The source contains no readable article text.")
    return {"title": article.get("title") or urlsplit(url).hostname, "content": text,
            "published_at": article.get("date"), "author": article.get("author"), "url": url}


def feed_links(data, base_url):
    root = ElementTree.fromstring(data)
    links = []
    for entry in root.iter():
        if entry.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        for child in entry:
            if child.tag.split("}")[-1] == "link" and child.get("rel", "alternate") == "alternate":
                value = child.get("href") or child.text
                if value:
                    link = urljoin(base_url, value.strip())
                    if link not in links:
                        links.append(link)
                    break
        if len(links) >= MAX_FEED_ITEMS:
            break
    if not links:
        raise ValueError("No RSS/Atom article links were found.")
    return links


def _store_article(db, connector, article, raw):
    content_hash = hashlib.sha256(article["content"].encode()).hexdigest()
    version_id = "source_" + hashlib.sha256((connector["id"] + article["url"] + content_hash).encode()).hexdigest()[:32]
    # Serialize only persistence, after network/HTML extraction. Original bytes,
    # document chunks and version identity must commit together or not at all.
    with db.transaction("source-version:"+version_id) as atomic:
        existing = atomic.get_record("source_versions", version_id)
        if existing:
            return {**existing, "duplicate": True}
        file_id = atomic.save_upload("source.html", raw, hashlib.sha256(raw).hexdigest(), connector.get("dataset_id"),
                                    "Market research", "web", "none")
        doc = atomic.add_document(article["title"][:160], article["content"], article["url"], connector.get("dataset_id"),
                                  file_id=file_id, category="Market research", extraction_method="web",
                                  segments=[{"content": article["content"], "location": "Article body"}])
        version = {"id": version_id, "connector_id": connector["id"], "document_id": doc["id"],
                   "file_id": file_id, "url": article["url"], "content_hash": content_hash,
                   "published_at": article.get("published_at"), "author": article.get("author"), "fetched_at": now()}
        atomic.put_record("source_versions", version_id, version)
        return version


def bounded_kaggle_handle(handle):
    """Pin a version and reject oversized bundles before downloading archives."""
    from kagglehub.clients import build_kaggle_client
    from kagglesdk.datasets.types.dataset_api_service import ApiGetDatasetRequest, ApiListDatasetFilesRequest
    parts=handle.split("/")
    with build_kaggle_client() as client:
        api=client.datasets.dataset_api_client
        if len(parts)==4:
            version=int(parts[3])
        else:
            request=ApiGetDatasetRequest()
            request.owner_slug,request.dataset_slug=parts[:2]
            version=api.get_dataset(request).current_version_number
        if version<1:raise ValueError("Kaggle did not return a valid dataset version.")
        request=ApiListDatasetFilesRequest()
        request.owner_slug,request.dataset_slug=parts[:2]
        request.dataset_version_number=version
        request.page_size=65
        listing=api.list_dataset_files(request)
        files=listing.dataset_files or []
        if listing.error_message or not files:
            raise ValueError("Kaggle dataset files could not be verified before download.")
        if listing.next_page_token or len(files)>64 or sum(file.total_bytes for file in files)>100_000_000:
            raise ValueError("Dataset exceeds 64 files or 100 MB; choose a smaller dataset.")
    return "/".join(parts[:2])+"/versions/"+str(version)


def sync_connector(db, settings, connector_id):
    connector = db.get_record("connectors", connector_id)
    if not connector:
        raise ValueError("Connector not found.")
    if getattr(settings, "local_only", False):
        raise ValueError("Source synchronization is disabled by the local-only policy.")
    if not getattr(settings, "allow_external_sources", True):
        raise ValueError("External sources have been disabled by the administrator.")
    allowed_hosts = {h.strip().lower() for h in getattr(settings, "source_allowed_hosts", "").split(",") if h.strip()}
    if allowed_hosts and connector["kind"] != "kaggle" and urlsplit(connector["url"]).hostname not in allowed_hosts:
        raise ValueError("This source host is not in the administrator's allowlist.")
    versions, errors = [], []
    try:
        if connector["kind"] in {"web", "rss"}:
            data, final_url, _ = fetch_public(connector["url"], allowed_hosts=allowed_hosts)
            links = feed_links(data, final_url) if connector["kind"] == "rss" else [final_url]
            for url in links:
                try:
                    raw, resolved, _ = (data, final_url, "") if url == final_url else fetch_public(url, allowed_hosts=allowed_hosts)
                    versions.append(_store_article(db, connector, article_text(raw, resolved), raw))
                except (ValueError, httpx.HTTPError) as exc:
                    errors.append({"url": url, "error": f"Source import failed ({type(exc).__name__}). Check the URL, format and access policy."})
        elif connector["kind"] == "kaggle":
            import kagglehub
            handle = connector.get("dataset", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+(?:/versions/[0-9]+)?", handle):
                raise ValueError("Use a Kaggle dataset handle owner/name or owner/name/versions/N.")
            handle=bounded_kaggle_handle(handle)
            # Download to an isolated directory, never mix source files with application files.
            with tempfile.TemporaryDirectory(prefix="market-lens-kaggle-") as folder:
                downloaded = Path(kagglehub.dataset_download(handle, output_dir=folder)).resolve()
                if not downloaded.is_relative_to(Path(folder).resolve()):
                    raise ValueError("Downloaded dataset escaped its isolated directory.")
                allowed = {".csv", ".tsv", ".xlsx", ".json", ".jsonl", ".parquet", ".txt", ".md"}
                files = [f for f in downloaded.rglob("*") if f.is_file() and not f.is_symlink()
                         and f.suffix.lower() in allowed and f.resolve().is_relative_to(downloaded)]
                if len(files) > 64 or sum(f.stat().st_size for f in files) > 100_000_000:
                    raise ValueError("Dataset exceeds 64 files or 100 MB; choose a smaller dataset.")
                for file in files:
                    if file.stat().st_size > MAX_FILE:
                        errors.append({"file": file.name, "error": "File exceeds 25 MB."})
                        continue
                    content=file.read_bytes()
                    identity=hashlib.sha256(content).hexdigest()+":"+str(connector.get("dataset_id"))
                    with db.transaction("kaggle-file:"+identity) as atomic:
                        result = ingest(atomic, settings, Providers(settings), file.name, content,
                                        dataset_id=connector.get("dataset_id"), category="Market research")
                    versions.append(result)
                if not files:
                    raise ValueError("Dataset contains no supported table or text files.")
        else:
            raise ValueError("Unsupported connector type.")
        if not versions and errors:
            raise ValueError("No source could be imported: " + errors[0]["error"])
        connector.update(last_synced_at=now(), status="partial" if errors else "ready", last_error=None)
        db.put_record("connectors", connector_id, connector)
        result = {"id": uid("sync"), "connector_id": connector_id, "created_at": now(), "sources": versions, "errors": errors}
        db.put_record("sync_results", result["id"], result)
        return result
    except Exception as exc:
        # Keep raw provider errors out of persisted records; credentials can occur in download URLs.
        connector.update(status="failed", last_error=f"Source synchronization failed ({type(exc).__name__}). Check the source and server logs.")
        db.put_record("connectors", connector_id, connector)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(connector["last_error"]) from exc
