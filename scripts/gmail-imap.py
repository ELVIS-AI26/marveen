#!/usr/bin/env python3
"""Gmail IMAP read-only helper (app password, no OAuth).

Recreated 2026-10-31 after the mogul -> nmeth_laszlo migration: the script
was untracked and did not survive the move, but store/.gmail-imap-creds did.
Behavior follows ~/.claude/skills/headless-mail-calendar-setup/SKILL.md:
reads "[Gmail]/All Mail" (the \\All special-use mailbox, whatever its
localized name is) via Gmail's X-GM-RAW search, never plain INBOX, because
filtered mail skips INBOX entirely.

Usage:
  gmail-imap.py verify
  gmail-imap.py recent [--hours N] [--max N] [--body]
  gmail-imap.py search "<query>" [--hours N] [--max N] [--body]

Output is one JSON object on stdout.
"""
import argparse
import email
import imaplib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from email.header import decode_header

STORE = os.path.join(os.path.dirname(__file__), "..", "store")
CREDS_FILE = os.path.join(STORE, ".gmail-imap-creds")
IMAP_HOST = "imap.gmail.com"


def _load_creds():
    creds = {}
    try:
        with open(CREDS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"creds fajl nem olvashato ({CREDS_FILE}): {exc}"}))
        sys.exit(1)
    if "GMAIL_ADDRESS" not in creds or "GMAIL_APP_PASSWORD" not in creds:
        print(json.dumps({"ok": False, "error": f"hianyos creds fajl: {CREDS_FILE}"}))
        sys.exit(1)
    return creds


def _connect(creds):
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    conn.login(creds["GMAIL_ADDRESS"], creds["GMAIL_APP_PASSWORD"])
    return conn


def _find_all_mail(conn):
    typ, data = conn.list()
    if typ == "OK":
        for raw in data:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            if "\\All" in line:
                m = re.search(r'"([^"]+)"\s*$', line)
                if m:
                    return m.group(1)
    for guess in ("[Gmail]/All Mail", "[Google Mail]/All Mail"):
        typ, _ = conn.select(f'"{guess}"', readonly=True)
        if typ == "OK":
            return guess
    return None


def _strip_accents(s):
    # Gmail's X-GM-RAW index is accent-insensitive, but imaplib encodes the
    # search literal to ASCII and raises on anything accented -- strip before
    # sending, keep the (accented) results as Gmail returns them.
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _decode_words(raw):
    if not raw:
        return ""
    out = []
    for text, enc in decode_header(raw):
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", "replace"))
            except (LookupError, UnicodeDecodeError):
                out.append(text.decode("utf-8", "replace"))
        else:
            out.append(text)
    return "".join(out)


def _body_snippet(msg, limit=400):
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, "replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", "replace")
            return re.sub(r"\s+", " ", text).strip()[:limit]
    return ""


def _fetch_uids(conn, uids, want_body):
    out = []
    if not uids:
        return out
    uidset = b",".join(uids)
    parts = "(RFC822)" if want_body else "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
    typ, data = conn.uid("fetch", uidset, parts)
    if typ != "OK":
        return out
    for item in data:
        if not isinstance(item, tuple):
            continue
        header_bytes, payload = item
        uid_m = re.search(rb"UID (\d+)", header_bytes)
        uid = uid_m.group(1).decode() if uid_m else None
        msg = email.message_from_bytes(payload)
        entry = {
            "uid": uid,
            "from": _decode_words(msg.get("From")),
            "subject": _decode_words(msg.get("Subject")),
            "date": msg.get("Date"),
        }
        if want_body:
            entry["body"] = _body_snippet(msg)
        out.append(entry)
    return out


def _search(conn, box, raw_query, hours, max_n):
    typ, _ = conn.select(f'"{box}"', readonly=True)
    if typ != "OK":
        raise RuntimeError(f"nem valaszthato ki a mailbox: {box}")
    terms = ["-in:sent", "-in:trash", "-in:spam", "-in:chats"]
    if hours:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        terms.append(f"after:{since.strftime('%Y/%m/%d')}")
    if raw_query:
        terms.append(_strip_accents(raw_query))
    gm_query = " ".join(terms)
    typ, data = conn.uid("search", None, "X-GM-RAW", f'"{gm_query}"')
    if typ != "OK":
        raise RuntimeError(f"X-GM-RAW kereses sikertelen: {data}")
    uids = data[0].split() if data and data[0] else []
    if max_n:
        uids = uids[-max_n:]
    uids.reverse()
    return uids, gm_query


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")

    p_recent = sub.add_parser("recent")
    p_recent.add_argument("--hours", type=int, default=24)
    p_recent.add_argument("--max", type=int, default=30)
    p_recent.add_argument("--body", action="store_true")

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--hours", type=int, default=0)
    p_search.add_argument("--max", type=int, default=30)
    p_search.add_argument("--body", action="store_true")

    args = ap.parse_args()
    creds = _load_creds()

    try:
        conn = _connect(creds)
    except (imaplib.IMAP4.error, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    try:
        box = _find_all_mail(conn)
        if not box:
            print(json.dumps({"ok": False, "error": "nem talalom az Osszes level mappat (\\All)"}))
            sys.exit(1)

        if args.cmd == "verify":
            typ, _ = conn.select(f'"{box}"', readonly=True)
            print(json.dumps({"ok": typ == "OK", "email": creds["GMAIL_ADDRESS"], "all_mail_folder": box}))
            return

        if args.cmd == "recent":
            uids, q = _search(conn, box, "", args.hours, args.max)
            messages = _fetch_uids(conn, uids, args.body)
        else:
            uids, q = _search(conn, box, args.query, args.hours, args.max)
            messages = _fetch_uids(conn, uids, args.body)

        print(json.dumps({"ok": True, "query": q, "count": len(messages), "messages": messages}, ensure_ascii=False))
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
