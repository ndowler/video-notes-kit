"""vn: store YouTube transcript/takeaway Markdown notes and search them (stdlib only)."""
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

REPO = os.environ.get("VN_ROOT") or os.path.dirname(os.path.realpath(__file__))
SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def paths():
    return {
        "videos": os.path.join(REPO, "videos"),
        "db": os.path.join(REPO, ".index", "vn.sqlite"),
        "index": os.path.join(REPO, "INDEX.md"),
    }


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class VnError(Exception):
    pass


# ---------- parsing helpers ----------

def parse_video_id(s):
    """Extract an 11-char YouTube video ID from a URL or bare ID."""
    from urllib.parse import urlparse, parse_qs
    s = s.strip()
    if ID_RE.match(s):
        return s
    u = urlparse(s if "//" in s else "https://" + s)
    host = (u.hostname or "").lower()
    cand = None
    if host == "youtu.be":
        cand = u.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] in ("shorts", "embed", "live", "v") and len(parts) > 1:
            cand = parts[1]
        else:
            cand = (parse_qs(u.query).get("v") or [None])[0]
    if cand and ID_RE.match(cand):
        return cand
    raise VnError("could not find a YouTube video ID in %r" % s)


def slugify(title, maxlen=60):
    s = re.sub(r"[^a-z0-9]+", "-", html.unescape(title).lower()).strip("-")
    if len(s) > maxlen:
        s = s[:maxlen]
        if "-" in s:
            s = s.rsplit("-", 1)[0]
    return s.strip("-") or "video"


def fmt_ts(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def paragraphize(segments, min_s=60, max_s=90):
    """Group caption segments into [(start_s, text)] paragraphs of ~60-90s,
    breaking at sentence ends once min_s elapsed, hard-breaking at max_s."""
    paras, cur, start = [], [], None
    for seg in segments:
        text = html.unescape(str(seg.get("text", ""))).replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        t = float(seg.get("start_s", 0))
        if start is None:
            start = t
        elif t - start >= max_s or (t - start >= min_s and SENTENCE_END.search(cur[-1])):
            paras.append((int(start), " ".join(cur)))
            cur, start = [], t
        cur.append(text)
    if cur:
        paras.append((int(start), " ".join(cur)))
    return paras


def render_transcript(title, video_id, length, paras, caption_type="auto"):
    kind = ("YouTube's auto-generated English captions, unedited (expect some misheard words)"
            if caption_type == "auto" else "Human-written English captions")
    out = ["# %s" % title, "",
           "- Source: https://www.youtube.com/watch?v=%s" % video_id,
           "- Length: %s" % length,
           "- Transcript: %s" % kind, "", "## Transcript", ""]
    for start, text in paras:
        url = "https://www.youtube.com/watch?v=%s&t=%ds" % (video_id, start)
        out.append("**[%s](%s)** %s" % (fmt_ts(start), url, text))
        out.append("")
    return "\n".join(out)


PARA_RE = re.compile(r"^\*\*\[([\d:]+)\]\((\S+?)\)\*\*\s*(.*)$")


def parse_transcript(md):
    """Return (title, [(start_s, url, text)])."""
    title, paras = "", []
    for line in md.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
        m = PARA_RE.match(line)
        if m:
            ts, url, text = m.groups()
            t = re.search(r"[?&]t=(\d+)", url)
            if t:
                start = int(t.group(1))
            else:
                start = 0
                for p in ts.split(":"):
                    start = start * 60 + int(p)
            paras.append((start, url, text.strip()))
    return title, paras


def parse_takeaways(md):
    """Split Markdown into [(heading, body)] sections by heading (fence-aware)."""
    sections, head, buf, fence = [], "", [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^#{1,6}\s+(.*?)\s*#*$", line)
        if m:
            sections.append((head, "\n".join(buf).strip()))
            head, buf = m.group(1), []
        else:
            buf.append(line)
    sections.append((head, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if b or h]


# ---------- storage ----------

def video_dirs():
    root = paths()["videos"]
    if not os.path.isdir(root):
        return []
    return sorted(os.path.join(root, d) for d in os.listdir(root)
                  if os.path.isfile(os.path.join(root, d, "meta.json")))


def load_meta(d):
    with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
        m = json.load(f)
    m["_dir"] = d
    m["_slug"] = os.path.basename(d)
    return m


def write_meta(d, meta):
    meta = {k: v for k, v in meta.items() if not k.startswith("_")}
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")


def find_video(ref):
    metas = [load_meta(d) for d in video_dirs()]
    exact = [m for m in metas if ref in (m["video_id"], m["_slug"])]
    if not exact:
        try:
            vid = parse_video_id(ref)
            exact = [m for m in metas if m["video_id"] == vid]
        except VnError:
            pass
    if not exact:
        exact = [m for m in metas if ref in m["_slug"]]
    if len(exact) != 1:
        raise VnError("no video matches %r" % ref if not exact
                      else "%r is ambiguous: %s" % (ref, ", ".join(m["_slug"] for m in exact)))
    return exact[0]


def regenerate_index_md():
    metas = sorted((load_meta(d) for d in video_dirs()),
                   key=lambda m: (m.get("captured", ""), m["title"]), reverse=True)
    rel = "videos/"
    lines = ["# Video notes index", "",
             "_Generated by `vn` (do not edit by hand). %d videos._" % len(metas), "",
             "| Captured | Title | Length | Transcript | Takeaways |", "|---|---|---|---|---|"]
    for m in metas:
        s = m["_slug"]
        has = os.path.isfile(os.path.join(m["_dir"], "takeaways.md"))
        lines.append("| %s | [%s](%s) | %s | [transcript](%s%s/transcript.md) | %s |" % (
            m.get("captured", ""), m["title"].replace("|", "\\|"), m["url"], m.get("length", ""),
            rel, s, "[takeaways](%s%s/takeaways.md)" % (rel, s) if has else "-"))
    with open(paths()["index"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------- indexing & search ----------

def _fts_ok():
    try:
        c = sqlite3.connect(":memory:")
        c.execute("create virtual table t using fts5(a)")
        return True
    except sqlite3.Error:
        return False


def reindex():
    p = paths()
    os.makedirs(os.path.dirname(p["db"]), exist_ok=True)
    if os.path.exists(p["db"]):
        os.remove(p["db"])
    db = sqlite3.connect(p["db"])
    fts = _fts_ok()
    cols = "text, video_id UNINDEXED, slug UNINDEXED, title UNINDEXED, kind UNINDEXED, heading UNINDEXED, start_s UNINDEXED, url UNINDEXED, path UNINDEXED"
    if fts:
        db.execute("create virtual table docs using fts5(%s, tokenize='porter unicode61')" % cols)
    else:
        db.execute("create table docs(%s)" % cols.replace(" UNINDEXED", ""))
    db.execute("create table info(k text, v text)")
    db.execute("insert into info values('fts', ?)", ("1" if fts else "0",))
    n = 0
    ins = "insert into docs(text,video_id,slug,title,kind,heading,start_s,url,path) values(?,?,?,?,?,?,?,?,?)"
    for d in video_dirs():
        m = load_meta(d)
        base = dict(vid=m["video_id"], slug=m["_slug"], title=m["title"])
        tp = os.path.join(d, "transcript.md")
        if os.path.isfile(tp):
            _, paras = parse_transcript(_read(tp))
            for start, url, text in paras:
                db.execute(ins, (text, base["vid"], base["slug"], base["title"], "transcript",
                                 "", start, url, "videos/%s/transcript.md" % base["slug"]))
                n += 1
        kp = os.path.join(d, "takeaways.md")
        if os.path.isfile(kp):
            for head, body in parse_takeaways(_read(kp)):
                db.execute(ins, ((head + "\n" + body).strip(), base["vid"], base["slug"], base["title"],
                                 "takeaways", head, 0, m["url"], "videos/%s/takeaways.md" % base["slug"]))
                n += 1
    db.commit()
    db.close()
    return n


def _stale():
    db = paths()["db"]
    if not os.path.exists(db):
        return True
    t = os.path.getmtime(db)
    root = paths()["videos"]
    for dp, _, fs in os.walk(root):
        for f in fs:
            if os.path.getmtime(os.path.join(dp, f)) > t:
                return True
    return False


def _terms(q):
    return re.findall(r"\w+", q, re.UNICODE)


def search(query, scope="all", limit=10):
    terms = _terms(query)
    if not terms:
        raise VnError("empty query")
    if _stale():
        reindex()
    db = sqlite3.connect(paths()["db"])
    fts = db.execute("select v from info where k='fts'").fetchone()[0] == "1"
    kinds = {"transcripts": ["transcript"], "takeaways": ["takeaways"], "all": ["transcript", "takeaways"]}[scope]
    kph = ",".join("?" * len(kinds))
    rows = []
    if fts:
        sql = ("select video_id,slug,title,kind,heading,start_s,url,path,"
               "snippet(docs,0,'**','**','…',24), bm25(docs) from docs "
               "where docs match ? and kind in (%s) order by bm25(docs) limit ?" % kph)
        for joiner in (" AND ", " OR "):
            match = joiner.join('"%s"' % t for t in terms)
            rows = db.execute(sql, (match, *kinds, limit)).fetchall()
            if rows:
                break
    else:
        like = " AND ".join("text like ?" for _ in terms)
        raw = db.execute("select video_id,slug,title,kind,heading,start_s,url,path,text from docs "
                         "where %s and kind in (%s) limit ?" % (like, kph),
                         (*["%%%s%%" % t for t in terms], *kinds, limit)).fetchall()
        for r in raw:
            txt = r[8]
            i = txt.lower().find(terms[0].lower())
            frag = txt[max(0, i - 80): i + 120]
            for t in terms:
                frag = re.sub("(%s)" % re.escape(t), r"**\1**", frag, flags=re.I)
            rows.append(r[:8] + (frag, 0.0))
    db.close()
    out = []
    for vid, slug, title, kind, head, start, url, path, snip, score in rows:
        out.append({"video_id": vid, "slug": slug, "title": title, "kind": kind,
                    "heading": head, "timestamp": fmt_ts(start) if kind == "transcript" else "",
                    "start_s": start if kind == "transcript" else None,
                    "url": url, "file": path, "snippet": " ".join(snip.split()), "score": score})
    return out


# ---------- fetching ----------

def fetch_captions(video_id):
    treg = os.environ.get("VN_TREG") or shutil.which("treg") or "treg"
    data = json.dumps({"video": video_id, "language": "en"})
    try:
        p = subprocess.run([treg, "--json", "call", "crawl4ai.youtube.video.captions",
                            "--method", "POST", "--data", data],
                           capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise VnError("treg failed to run: %s" % e)
    if p.returncode != 0:
        raise VnError("treg exited %d: %s" % (p.returncode, (p.stderr or p.stdout).strip()[:500]))
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
        row = res["result"]["rows"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        raise VnError("no captions returned for video %s (unexpected treg output)" % video_id)
    if not row.get("segments"):
        raise VnError("video %s has no English captions" % video_id)
    return row


def add_video(url, takeaways=None, tags=None, today=None):
    vid = parse_video_id(url)
    for d in video_dirs():
        if load_meta(d)["video_id"] == vid:
            raise VnError("video %s already exists: %s (use `vn attach` to update takeaways)" % (vid, d))
    if takeaways and not os.path.isfile(takeaways):
        raise VnError("takeaways file not found: %s" % takeaways)
    row = fetch_captions(vid)
    segs = row["segments"]
    end = max(float(s.get("start_s", 0)) + float(s.get("duration_s", 0)) for s in segs)
    length = "about " + fmt_ts(end)
    title = html.unescape(row.get("title") or vid).strip()
    paras = paragraphize(segs)
    captured = today or dt.date.today().isoformat()
    ctype = "auto" if row.get("is_auto_generated", True) else "human"
    meta = {"video_id": vid, "title": title,
            "channel": row.get("channel") or row.get("author") or row.get("uploader") or "",
            "url": "https://www.youtube.com/watch?v=" + vid, "length": length,
            "captured": captured, "caption_type": ctype, "tags": tags or []}
    return _store(meta, render_transcript(title, vid, length, paras, ctype), takeaways)


def _store(meta, transcript_md, takeaways=None):
    d = os.path.join(paths()["videos"], "%s-%s" % (meta["captured"], slugify(meta["title"])))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "transcript.md"), "w", encoding="utf-8") as f:
        f.write(transcript_md if transcript_md.endswith("\n") else transcript_md + "\n")
    write_meta(d, meta)
    if takeaways:
        shutil.copyfile(takeaways, os.path.join(d, "takeaways.md"))
    reindex()
    regenerate_index_md()
    return d


def import_transcript(path, takeaways=None, channel="", tags=None, caption_type="auto"):
    md = _read(path)
    title, paras = parse_transcript(md)
    src = re.search(r"^- Source:\s*(\S+)", md, re.M)
    if not title or not src or not paras:
        raise VnError("%s does not look like a vn transcript" % path)
    vid = parse_video_id(src.group(1))
    ln = re.search(r"^- Length:\s*(.+)$", md, re.M)
    captured = dt.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    meta = {"video_id": vid, "title": title, "channel": channel,
            "url": "https://www.youtube.com/watch?v=" + vid,
            "length": ln.group(1).strip() if ln else "", "captured": captured,
            "caption_type": caption_type, "tags": tags or []}
    return _store(meta, md, takeaways)


def attach_takeaways(ref, path):
    if not os.path.isfile(path):
        raise VnError("file not found: %s" % path)
    m = find_video(ref)
    shutil.copyfile(path, os.path.join(m["_dir"], "takeaways.md"))
    reindex()
    regenerate_index_md()
    return m["_dir"]


# ---------- CLI ----------

def _tags(s):
    return [t.strip() for t in (s or "").split(",") if t.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vn", description="Searchable YouTube video notes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="fetch captions for a YouTube URL and store it")
    a.add_argument("url")
    a.add_argument("--takeaways", metavar="FILE")
    a.add_argument("--tags", default="")
    t = sub.add_parser("attach", help="add/replace takeaways.md: vn attach <id-or-slug> takeaways FILE")
    t.add_argument("video")
    t.add_argument("what", choices=["takeaways"])
    t.add_argument("file")
    i = sub.add_parser("import", help="import an existing transcript .md (+ takeaways)")
    i.add_argument("transcript")
    i.add_argument("--takeaways", metavar="FILE")
    i.add_argument("--channel", default="")
    i.add_argument("--tags", default="")
    s = sub.add_parser("search", help="full-text search")
    s.add_argument("query")
    s.add_argument("--in", dest="scope", choices=["transcripts", "takeaways", "all"], default="all")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--json", action="store_true")
    sub.add_parser("list", help="list videos")
    sub.add_parser("reindex", help="rebuild search index and INDEX.md")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            print(add_video(args.url, args.takeaways, _tags(args.tags)))
        elif args.cmd == "attach":
            print(attach_takeaways(args.video, args.file))
        elif args.cmd == "import":
            print(import_transcript(args.transcript, args.takeaways, args.channel, _tags(args.tags)))
        elif args.cmd == "search":
            res = search(args.query, args.scope, args.n)
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                if not res:
                    print("no matches")
                for k, r in enumerate(res, 1):
                    where = ("@ " + r["timestamp"]) if r["kind"] == "transcript" else ("§ " + r["heading"])
                    print("%d. [%s] %s %s\n   %s\n   %s" % (k, r["kind"], r["title"], where, r["url"], r["snippet"]))
                    if r["kind"] == "takeaways":
                        print("   file: " + r["file"])
        elif args.cmd == "list":
            for d in sorted(video_dirs(), key=lambda d: load_meta(d).get("captured", ""), reverse=True):
                m = load_meta(d)
                has = "yes" if os.path.isfile(os.path.join(d, "takeaways.md")) else "no"
                print("%s  %-60.60s  %-14s takeaways:%s  %s" % (
                    m.get("captured", ""), m["title"], m.get("length", ""), has, m["video_id"]))
        elif args.cmd == "reindex":
            n = reindex()
            regenerate_index_md()
            print("indexed %d passages from %d videos" % (n, len(video_dirs())))
    except VnError as e:
        print("vn: error: %s" % e, file=sys.stderr)
        return 1
    return 0
