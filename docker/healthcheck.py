#!/usr/bin/env python3
"""Container health check for the Settlement Portal.

The /health/ endpoint verifies the Oracle connection. The request uses a
trusted Host header and X-Forwarded-Proto so Django's HTTPS redirect (enabled
when DEBUG=0) does not turn the probe into a 301 response.
"""

import os
import sys
import urllib.error
import urllib.request


def candidate_hosts():
    hosts = [
        host.strip()
        for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    candidates = []
    for host in hosts:
        if host == "*":
            candidates.append("localhost")
        elif host.startswith("."):
            # Django treats a leading dot as a subdomain wildcard; probe a
            # concrete subdomain so the Host header validates.
            candidates.append(f"www{host}")
        else:
            candidates.append(host)
    return candidates or ["localhost"]


def probe(port, host):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health/",
        headers={
            "Host": host,
            "X-Forwarded-Proto": "https",
            "User-Agent": "settlement-api-healthcheck/1.0",
        },
    )
    # Ignore HTTP_PROXY/HTTPS_PROXY from the environment for this local probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return response.status == 200


def main():
    port = os.environ.get("PORT", "8000").strip() or "8000"
    for host in candidate_hosts():
        try:
            if probe(port, host):
                return 0
        except urllib.error.HTTPError as exc:
            print(f"healthcheck: {host} returned HTTP {exc.code}", file=sys.stderr)
            # 400 means Django rejected the Host header. Try the next
            # configured host. Any other status (429/500/503) is a real
            # verdict from the app, so probing more Host values only creates
            # duplicate requests and hides the actual failure.
            if exc.code != 400:
                return 1
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"healthcheck: {host}: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
