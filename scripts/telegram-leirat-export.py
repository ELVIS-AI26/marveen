#!/usr/bin/env python3
"""
Telegram-leirat export: a sajat (elvis-marveen-bot) Telegram beszelgetes
nyers, idorendi, napi bontasu leirata PDF-be es DOCX-be, a Claude Code
session-naplobol (NEM a Bot API-bol, annak nincs history funkcioja).

Minden futaskor csak az elozo export ota keletkezett uzeneteket dolgozza fel:
a hatar a celmappaban mar meglevo legujabb telegram-leirat_* fajl VEGDATUMA
(ha nincs ilyen fajl, teljes visszamenoleges backfill a legelso session-ben
talalhato Telegram-uzenetig).

Csak a TENYLEGESEN SIKERESEN kikuldott valasz kerul bele -- a sikertelen/
blokkolt kuldesi kiserlet nem (a tool_result "sent (id: ...)" szoveget
keressuk, is_error nelkul).

Hasznalat:
  python3 scripts/telegram-leirat-export.py [--dry-run] [--end YYYY-MM-DD]

Kimenet: store/telegram-leirat/telegram-leirat_<START>_<END>.pdf es .docx,
majd feltoltes a Drive ELVIS_Telegram mappajaba (.venv-gdocs/bin/python
scripts/gdrive-upload.py-val).
"""
import sys, os, json, re, glob, argparse, subprocess
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "store")
OUT_DIR = os.path.join(STORE, "telegram-leirat")
SESSIONS_GLOB = os.path.expanduser(
    "~/.claude/projects/-home-nmeth-laszlo-marveen/*.jsonl")
TZ = ZoneInfo("Europe/Budapest") if ZoneInfo else timezone(timedelta(hours=1))
OWNER_CHAT_ID = "8595399105"
DRIVE_FOLDER_ID = "1CISry05TGzSv7NLAj7BSDZs0xG9-FUS_"  # CEO_OPERATÍV/ELVIS_Telegram
FNAME_RE = re.compile(r"^telegram-leirat_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.(pdf|docx|doc)$")
CHANNEL_RE = re.compile(
    r'<channel source="plugin:telegram:telegram"([^>]*)>(.*?)</channel>',
    re.DOTALL)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _local_date(ts_iso):
    dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return dt.astimezone(TZ)


def _existing_range():
    """Determine the start boundary from the latest telegram-leirat_* file
    already on Drive. None -> first run, full backfill."""
    try:
        out = subprocess.run(
            [os.path.join(ROOT, ".venv-gdocs/bin/python"),
             os.path.join(ROOT, "scripts/gdrive.py"), "list", DRIVE_FOLDER_ID],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        rows = json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else []
    except Exception:
        rows = []
    latest_end = None
    for r in rows:
        m = FNAME_RE.match(r.get("name", ""))
        if m:
            end = m.group(2)
            if latest_end is None or end > latest_end:
                latest_end = end
    return latest_end


def _extract_incoming(content, ts_fallback):
    out = []
    for m in CHANNEL_RE.finditer(content):
        attrs = dict(ATTR_RE.findall(m.group(1)))
        if attrs.get("chat_id") != OWNER_CHAT_ID:
            continue
        text = m.group(2).strip()
        if not text:
            continue
        msg_id = attrs.get("message_id", "")
        ts = attrs.get("ts") or ts_fallback
        out.append({
            "ts": ts,
            "dedupe_key": "in:" + msg_id if msg_id else None,
            "speaker": "Németh László",
            "text": text,
        })
    return out


def _scan_file(path):
    incoming = []
    tool_uses = {}   # tool_use_id -> dict
    tool_results = {}  # tool_use_id -> success bool
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            ts = d.get("timestamp")
            msg = d.get("message") or {}
            content = msg.get("content")
            if t == "user":
                if isinstance(content, str) and "plugin:telegram:telegram" in content:
                    incoming.extend(_extract_incoming(content, ts))
                elif isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result":
                            tid = b.get("tool_use_id")
                            c = b.get("content")
                            ok = False
                            if not b.get("is_error"):
                                texts = []
                                if isinstance(c, str):
                                    texts = [c]
                                elif isinstance(c, list):
                                    texts = [x.get("text", "") for x in c if isinstance(x, dict)]
                                ok = any("sent (id:" in x for x in texts)
                            tool_results[tid] = ok
            elif t == "assistant" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("name") == "mcp__plugin_telegram_telegram__reply":
                        inp = b.get("input") or {}
                        if inp.get("chat_id") == OWNER_CHAT_ID and inp.get("text"):
                            tool_uses[b.get("id")] = {"ts": ts, "text": inp["text"]}
    outgoing = []
    for tid, info in tool_uses.items():
        if tool_results.get(tid):
            outgoing.append({
                "ts": info["ts"],
                "dedupe_key": "out:" + tid,
                "speaker": "Elvis_Marveen_bot",
                "text": info["text"],
            })
    return incoming + outgoing


def collect_messages():
    seen = set()
    all_msgs = []
    for path in sorted(glob.glob(SESSIONS_GLOB)):
        for m in _scan_file(path):
            k = m.get("dedupe_key")
            if k and k in seen:
                continue
            if k:
                seen.add(k)
            if not m.get("ts"):
                continue
            all_msgs.append(m)
    all_msgs.sort(key=lambda m: m["ts"])
    return all_msgs


def group_by_day(msgs, start_date, end_date):
    days = {}
    for m in msgs:
        local = _local_date(m["ts"])
        day = local.date().isoformat()
        if day < start_date or day > end_date:
            continue
        days.setdefault(day, []).append({
            "time": local.strftime("%H:%M"),
            "speaker": m["speaker"],
            "text": m["text"],
        })
    return dict(sorted(days.items()))


def render_html(days, start_date, end_date):
    import html as _html
    parts = [
        "<html><head><meta charset='utf-8'><style>",
        "body{font-family:Arial,sans-serif;font-size:11pt;color:#111;}",
        "h1{font-size:16pt;} h2{font-size:13pt;border-bottom:1px solid #ccc;"
        "margin-top:24px;}",
        ".msg{margin:6px 0;} .meta{font-weight:bold;} .nl .meta{color:#1a4d8f;}",
        ".bot .meta{color:#7a2020;} .text{white-space:pre-wrap;margin-left:4px;}",
        "</style></head><body>",
        f"<h1>Telegram-leirat {start_date} -- {end_date}</h1>",
    ]
    for day, msgs in days.items():
        parts.append(f"<h2>{day}</h2>")
        for m in msgs:
            cls = "nl" if m["speaker"] == "Németh László" else "bot"
            parts.append(
                f"<div class='msg {cls}'><span class='meta'>{m['time']} "
                f"{_html.escape(m['speaker'])}:</span> "
                f"<span class='text'>{_html.escape(m['text'])}</span></div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def render_docx(days, start_date, end_date, out_path):
    sys.path.insert(0, os.path.join(ROOT, ".venv-gdocs/lib/python3.14/site-packages"))
    import docx
    from docx.shared import Pt, RGBColor
    d = docx.Document()
    d.add_heading(f"Telegram-leirat {start_date} -- {end_date}", level=1)
    for day, msgs in days.items():
        d.add_heading(day, level=2)
        for m in msgs:
            p = d.add_paragraph()
            run = p.add_run(f"{m['time']} {m['speaker']}: ")
            run.bold = True
            run.font.color.rgb = RGBColor(0x1A, 0x4D, 0x8F) if m["speaker"] == "Németh László" else RGBColor(0x7A, 0x20, 0x20)
            p.add_run(m["text"])
    d.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--end", help="YYYY-MM-DD, default today (Europe/Budapest)")
    ap.add_argument("--start", help="YYYY-MM-DD override (ignores Drive-based boundary)")
    args = ap.parse_args()

    end_date = args.end or datetime.now(TZ).date().isoformat()

    if args.start:
        start_date = args.start
    else:
        prev_end = _existing_range()
        if prev_end:
            start_date = (datetime.fromisoformat(prev_end) + timedelta(days=1)).date().isoformat()
        else:
            start_date = "1900-01-01"  # full backfill, trimmed after we see real data

    msgs = collect_messages()
    if not msgs:
        print("Nincs egyetlen Telegram-uzenet sem a session-naploban.")
        return 0

    if start_date == "1900-01-01":
        start_date = _local_date(msgs[0]["ts"]).date().isoformat()

    if start_date > end_date:
        print(f"Nincs uj idoszak: start={start_date} > end={end_date} (mar minden fel van dolgozva).")
        return 0

    days = group_by_day(msgs, start_date, end_date)
    if not days:
        print(f"Nincs uzenet a {start_date}..{end_date} tartomanyban.")
        return 0

    real_end = max(days.keys())
    real_start = min(days.keys())
    total = sum(len(v) for v in days.values())
    print(f"Tartomany: {real_start}..{real_end}, {len(days)} nap, {total} uzenet.")

    os.makedirs(OUT_DIR, exist_ok=True)
    base = f"telegram-leirat_{real_start}_{real_end}"
    html_path = os.path.join(OUT_DIR, base + ".html")
    pdf_path = os.path.join(OUT_DIR, base + ".pdf")
    docx_path = os.path.join(OUT_DIR, base + ".docx")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(days, real_start, real_end))

    if args.dry_run:
        print("DRY RUN, nem renderelek PDF/DOCX-et, nem toltok fel.")
        print("HTML:", html_path)
        return 0

    r = subprocess.run(["node", os.path.join(ROOT, "scripts/html2pdf.cjs"), html_path, pdf_path],
                        cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print("PDF renderelesi hiba:", r.stdout, r.stderr, file=sys.stderr)
        return 1
    print("PDF:", pdf_path)

    render_docx(days, real_start, real_end, docx_path)
    print("DOCX:", docx_path)

    for local_path in (pdf_path, docx_path):
        r = subprocess.run(
            [os.path.join(ROOT, ".venv-gdocs/bin/python"), os.path.join(ROOT, "scripts/gdrive-upload.py"),
             local_path, "--folder", DRIVE_FOLDER_ID],
            cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"Feltoltesi hiba ({local_path}):", r.stdout, r.stderr, file=sys.stderr)
            return 1
        print("Feltoltve:", local_path, "->", r.stdout.strip())

    return 0


if __name__ == "__main__":
    sys.exit(main())
