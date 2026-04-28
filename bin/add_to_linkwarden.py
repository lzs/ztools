#!/usr/bin/env python3
import os
import sys
import requests

BASE_URL = "https://lw.tf.sg"
COLLECTION_NAME = "Daily Readings"


def read_dotenv(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if value and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                env[key] = value
    except FileNotFoundError:
        return {}
    return env


TOKEN = os.environ.get("LW_TOKEN") or read_dotenv(".env").get("LW_TOKEN")
if not TOKEN:
    print("Set LW_TOKEN in .env first.")
    sys.exit(1)

if len(sys.argv) != 2:
    print("Usage: python add_to_linkwarden.py urls.txt")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def api(method, path, **kwargs):
    url = f"{BASE_URL.rstrip('/')}{path}"
    try:
        r = requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
        r.raise_for_status()
    except requests.Timeout as e:
        raise RuntimeError(f"{method} {path} timed out after 30 seconds") from e
    except requests.HTTPError as e:
        response = e.response
        detail = response.text if response is not None else str(e)
        status_code = response.status_code if response is not None else "unknown"
        raise RuntimeError(f"{method} {path} failed: {status_code} {detail}") from e
    return r.json() if r.text else None


def unwrap_response(data):
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


def find_collection(name):
    collections = unwrap_response(api("GET", "/api/v1/collections"))
    for c in collections:
        if c.get("name") == name:
            return c
    return None


def create_collection(name):
    return unwrap_response(api("POST", "/api/v1/collections", json={
        "name": name,
        "description": "Articles queued for daily reading",
        "color": "#0ea5e9",
        "icon": "BookOpen",
        "iconWeight": "regular",
    }))


def main():
    collection = find_collection(COLLECTION_NAME)
    if not collection:
        print(f'Creating collection "{COLLECTION_NAME}"...')
        collection = create_collection(COLLECTION_NAME)

    collection_id = collection["id"]
    print(f'Using collection "{COLLECTION_NAME}" id={collection_id}')

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        urls = [
            line.strip()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    for url in urls:
        try:
            payload = {
                "url": url,
                "type": "url",
                "collection": {"id": collection_id},
                "tags": [{"name": "daily-readings"}],
            }
            api("POST", "/api/v1/links", json=payload)
            print(f"OK  {url}")
        except RuntimeError as e:
            print(f"ERR {url}: {e}")


if __name__ == "__main__":
    main()
