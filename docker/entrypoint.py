#!/usr/bin/env python3
"""Validate runtime configuration, then exec the container command.

Secrets may be supplied either directly (NAME) or through a file (NAME_FILE),
which allows Docker secrets to be used without baking values into the image.
"""

import os
import sys

REQUIRED_ENV = (
    ("SECRET_KEY", "Django secret key"),
    ("PORTAL_ORACLE_DSN", "Oracle connect string"),
    ("PORTAL_ORACLE_USER", "Oracle schema user"),
    ("PORTAL_ORACLE_PASSWORD", "Oracle schema password"),
)

INSECURE_SECRET_KEYS = {
    "changeme",
    "change-me",
    "django-insecure-change-me",
    "replace-with-a-long-random-secret",
    "secret",
    "test-secret-key-for-dev",
}

TRUE_VALUES = {"1", "true", "yes", "on"}


def fail(message):
    """Exit with EX_CONFIG (78) so orchestrators can distinguish bad config."""
    print(f"entrypoint: {message}", file=sys.stderr)
    raise SystemExit(78)


def read_secret(name):
    """Return NAME or, when set, the contents of the file in NAME_FILE."""
    value = os.environ.get(name, "").strip()
    if value:
        return value

    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if not file_path:
        return ""

    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError as exc:
        fail(f"{name}_FILE cannot be read: {exc}")

    if not value:
        fail(f"{name}_FILE is empty")

    # Expose the value to Django and to any child management command.
    os.environ[name] = value
    return value


def main():
    missing = []
    for name, description in REQUIRED_ENV:
        if not read_secret(name):
            missing.append(f"{name} ({description})")

    if missing:
        fail("missing required configuration: " + ", ".join(missing))

    secret_key = os.environ["SECRET_KEY"]
    if secret_key in INSECURE_SECRET_KEYS:
        fail("SECRET_KEY is a known placeholder; generate a unique random value")
    if len(secret_key) < 32:
        fail("SECRET_KEY must be at least 32 characters long")

    if os.environ.get("DEBUG", "").strip().lower() in TRUE_VALUES:
        fail("DEBUG must be disabled in the container (use DEBUG=0)")

    allowed_hosts = [
        host.strip()
        for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    if not allowed_hosts:
        fail("ALLOWED_HOSTS must list the public hostname(s) served by this container")
    if "*" in allowed_hosts:
        print(
            "entrypoint: warning: ALLOWED_HOSTS contains '*'; "
            "prefer explicit hostnames in production",
            file=sys.stderr,
        )

    if len(sys.argv) < 2:
        fail("no container command was supplied")

    # Replace the entrypoint process so signals (SIGTERM) reach gunicorn.
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
