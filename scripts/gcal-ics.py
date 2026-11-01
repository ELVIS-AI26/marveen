#!/usr/bin/env python3
"""Google Calendar "secret iCal address" reader (no OAuth).

Recreated 2026-10-31 after the mogul -> nmeth_laszlo migration (the script
was untracked and did not survive the move; store/.gmail-imap-creds, which
holds the ICS URLs, did). Behavior follows
~/.claude/skills/headless-mail-calendar-setup/SKILL.md: fetches the private
basic.ics feed(s), expands RRULE (DAILY/WEEKLY/MONTHLY/YEARLY; COUNT/UNTIL),
honours EXDATE and a RECURRENCE-ID override. Rare RRULE parts (BYMONTHDAY,
BYSETPOS, ...) are NOT handled -- same documented gap as the original.

Usage:
  gcal-ics.py verify
  gcal-ics.py today

Output is one JSON object on stdout. Times are Europe/Budapest.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

STORE = os.path.join(os.path.dirname(__file__), "..", "store")
CREDS_FILE = os.path.join(STORE, ".gmail-imap-creds")
LOCAL_TZ = ZoneInfo("Europe/Budapest")
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _load_urls():
    urls = {}
    try:
        with open(CREDS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip().startswith("GCAL_ICS_URL"):
                    urls[k.strip()] = v.strip()
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"creds fajl nem olvashato ({CREDS_FILE}): {exc}"}))
        sys.exit(1)
    if not urls:
        print(json.dumps({"ok": False, "error": f"nincs GCAL_ICS_URL* a creds fajlban: {CREDS_FILE}"}))
        sys.exit(1)
    return urls


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def _unfold(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    for line in lines:
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _parse_prop(line):
    if ":" not in line:
        return None, {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            pk, pv = p.split("=", 1)
            params[pk.upper()] = pv
    return name, params, value


def _parse_dt(value, params):
    """-> (datetime_or_date, is_all_day)."""
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8])), True
    is_utc = value.endswith("Z")
    v = value[:-1] if is_utc else value
    dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
    if is_utc:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    elif "TZID" in params:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(params["TZID"]))
        except Exception:
            dt = dt.replace(tzinfo=LOCAL_TZ)
    else:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt, False


def _parse_rrule(value):
    rule = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        rule[k.upper()] = v
    return rule


def _unescape(text):
    return (text.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _parse_calendar(ics_text, calname_default):
    calname = calname_default
    m = re.search(r"^X-WR-CALNAME:(.*)$", ics_text, re.M)
    if m:
        calname = m.group(1).strip()
    lines = _unfold(ics_text)
    events = []  # masters
    overrides = {}  # (uid, recurrence_date) -> event dict
    cur = None
    for line in lines:
        if line.strip() == "BEGIN:VEVENT":
            cur = {"EXDATE": []}
            continue
        if line.strip() == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None:
            continue
        name, params, value = _parse_prop(line)
        if name is None:
            continue
        if name == "DTSTART":
            cur["dtstart"], cur["all_day"] = _parse_dt(value, params)
        elif name == "DTEND":
            cur["dtend"], _ = _parse_dt(value, params)
        elif name == "RRULE":
            cur["rrule"] = _parse_rrule(value)
        elif name == "EXDATE":
            for v in value.split(","):
                dt, _ = _parse_dt(v, params)
                cur["EXDATE"].append(dt)
        elif name == "RECURRENCE-ID":
            cur["recurrence_id"], _ = _parse_dt(value, params)
        elif name == "UID":
            cur["uid"] = value
        elif name == "SUMMARY":
            cur["summary"] = _unescape(value)
        elif name == "LOCATION":
            cur["location"] = _unescape(value)
        elif name == "STATUS":
            cur["status"] = value
    masters = []
    for ev in events:
        ev["calendar"] = calname
        if "recurrence_id" in ev:
            key = (ev.get("uid"), _as_date(ev["recurrence_id"]))
            overrides[key] = ev
        else:
            masters.append(ev)
    return masters, overrides


def _as_date(d):
    return d if isinstance(d, date) and not isinstance(d, datetime) else d.date()


def _occurs_on(ev, target):
    """True if the master `ev` has an occurrence on date `target` (local)."""
    start = _as_date(ev["dtstart"])
    if target < start:
        return False
    rrule = ev.get("rrule")
    if not rrule:
        return target == start
    freq = rrule.get("FREQ")
    interval = int(rrule.get("INTERVAL", "1"))
    until = rrule.get("UNTIL")
    count = rrule.get("COUNT")
    if until:
        until_dt, _ = _parse_dt(until, {})
        if target > _as_date(until_dt):
            return False
    if freq == "DAILY":
        delta = (target - start).days
        if delta % interval != 0:
            return False
        idx = delta // interval
    elif freq == "WEEKLY":
        byday = rrule.get("BYDAY")
        allowed = {WEEKDAYS[start.weekday()]} if not byday else set(byday.split(","))
        if WEEKDAYS[target.weekday()] not in allowed:
            return False
        week_start = start - timedelta(days=start.weekday())
        target_week_start = target - timedelta(days=target.weekday())
        weeks = (target_week_start - week_start).days // 7
        if weeks % interval != 0:
            return False
        idx = weeks  # approximate occurrence index for COUNT
    elif freq == "MONTHLY":
        if target.day != start.day:
            return False
        months = (target.year - start.year) * 12 + (target.month - start.month)
        if months < 0 or months % interval != 0:
            return False
        idx = months // interval
    elif freq == "YEARLY":
        if (target.month, target.day) != (start.month, start.day):
            return False
        years = target.year - start.year
        if years < 0 or years % interval != 0:
            return False
        idx = years // interval
    else:
        return False  # unsupported FREQ -- fail closed (no false positive)
    if count is not None and idx >= int(count):
        return False
    return True


def _collect_today(all_masters, all_overrides, target):
    out = []
    for ev in all_masters:
        if ev.get("status") == "CANCELLED":
            continue
        if not _occurs_on(ev, target):
            continue
        key = (ev.get("uid"), target)
        chosen = all_overrides.get(key, ev)
        if chosen.get("status") == "CANCELLED":
            continue
        if any(_as_date(x) == target for x in ev.get("EXDATE", [])) and key not in all_overrides:
            continue
        out.append(chosen)
    # standalone overrides whose master is outside this calendar fetch / not recurring here
    for (uid, odate), ev in all_overrides.items():
        if odate == target and not any(m.get("uid") == uid for m in all_masters):
            if ev.get("status") != "CANCELLED":
                out.append(ev)
    return out


def _fmt(ev):
    start, all_day = ev["dtstart"], ev.get("all_day", False)
    end = ev.get("dtend")
    if all_day:
        start_s, end_s = str(start), str(end) if end else None
    else:
        start_s = start.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")
        end_s = end.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S%z") if end else None
    return {
        "summary": ev.get("summary", ""),
        "location": ev.get("location", ""),
        "all_day": all_day,
        "start": start_s,
        "end": end_s,
        "calendar": ev.get("calendar", ""),
    }


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd not in ("verify", "today"):
        print(json.dumps({"ok": False, "error": "usage: gcal-ics.py verify|today"}))
        sys.exit(1)

    urls = _load_urls()
    all_masters, all_overrides = [], {}
    total_vevents = 0
    try:
        for key, url in urls.items():
            label = "privát" if key.endswith("PRIVATE") else "fő"
            text = _fetch(url)
            total_vevents += len(re.findall(r"^BEGIN:VEVENT\r?$", text, re.M))
            masters, overrides = _parse_calendar(text, label)
            all_masters.extend(masters)
            all_overrides.update(overrides)
    except (urllib.error.URLError, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"ICS letoltes sikertelen: {exc}"}))
        sys.exit(1)

    if cmd == "verify":
        print(json.dumps({"ok": True, "sources": len(urls), "vevents": total_vevents}))
        return

    target = datetime.now(LOCAL_TZ).date()
    todays = _collect_today(all_masters, all_overrides, target)
    todays.sort(key=lambda e: (e.get("all_day", False) is False and 0 or 1,
                                e["dtstart"].astimezone(LOCAL_TZ) if not e.get("all_day") else datetime.min))
    print(json.dumps({"ok": True, "date": str(target), "count": len(todays),
                       "events": [_fmt(e) for e in todays]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
