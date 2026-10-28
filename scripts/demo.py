"""Exercise a running API; never sends email or creates alerts by default."""
import argparse
import json
from uuid import uuid4
import secrets

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://localhost:8000")
args = parser.parse_args()
credentials = {"email": f"demo-{uuid4().hex[:10]}@example.com", "password": secrets.token_urlsafe(24)}

with httpx.Client(base_url=args.base_url, timeout=180) as client:
    response = client.post("/auth/register", json=credentials)
    response.raise_for_status()
    login = client.post("/auth/login", json=credentials)
    login.raise_for_status()
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    response = client.post("/searches", json={"query": "amul butter 500 g", "category": "groceries"})
    response.raise_for_status()
    data = response.json()
    history = client.get(f"/searches/{data['searchId']}")
    history.raise_for_status()
    assert history.json()["result"] == data
    print(json.dumps(data, indent=2))
    print(f"Verified saved history. Created local demo account: {credentials['email']}")
