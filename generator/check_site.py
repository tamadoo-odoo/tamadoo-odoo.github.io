#!/usr/bin/env python3
"""Check the built site in docs/: internal links, images, tag balance, banned words, size."""
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
OPTIONAL_END = {"li", "p", "dt", "dd", "tr", "td", "th", "option", "thead", "tbody"}
BANNED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".banned_words")


def banned_re():
    if not os.path.exists(BANNED_FILE):
        return None
    words = [w.strip() for w in open(BANNED_FILE, encoding="utf-8") if w.strip()]
    return re.compile("|".join(re.escape(w) for w in words), re.I) if words else None


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.refs, self.imgs_no_alt, self.ids = [], [], [], 0, set()
        self.titles = 0
        self.desc = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "title":
            self.titles += 1
        if tag == "meta" and a.get("name") == "description":
            self.desc = a.get("content")
        for k in ("href", "src"):
            if a.get(k) and not (tag == "link" and a.get("rel") == "canonical") and tag != "meta":
                self.refs.append((tag, a[k]))
        if tag == "img" and "alt" not in a:
            self.imgs_no_alt += 1
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append("stray </%s> at %s" % (tag, self.getpos()))
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        self.errors.append("</%s> closes <%s> opened at %s" % (tag, self.stack[-1][0], self.stack[-1][1]))
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def main():
    banned_words = banned_re()
    pages = []
    for d, _, files in os.walk(ROOT):
        pages += [os.path.join(d, f) for f in files if f.endswith(".html")]
    broken, errors, banned, no_alt, links, img_refs, ext_links = [], [], [], 0, 0, 0, 0
    titles, descs = {}, {}
    used_files = set()
    for p in sorted(pages):
        rel_page = os.path.relpath(p, ROOT)
        text = open(p, encoding="utf-8").read()
        hit = banned_words.search(text) if banned_words else None
        if hit:
            banned.append((rel_page, hit.group(0)))
        parser = Page()
        parser.feed(text)
        parser.close()
        errors += ["%s: %s" % (rel_page, e) for e in parser.errors]
        errors += ["%s: unclosed <%s> from %s" % (rel_page, t, pos) for t, pos in parser.stack]
        no_alt += parser.imgs_no_alt
        m = re.search(r"<title>(.*?)</title>", text)
        if rel_page != "404.html":
            titles.setdefault(m.group(1) if m else "", []).append(rel_page)
            descs.setdefault(parser.desc or "", []).append(rel_page)
        if rel_page == "404.html":
            continue
        for tag, ref in parser.refs:
            parts = urlsplit(ref)
            if parts.scheme or ref.startswith("//"):
                ext_links += 1
                continue
            links += 1
            if tag == "img":
                img_refs += 1
            if not parts.path:
                if parts.fragment and parts.fragment not in parser.ids:
                    broken.append((rel_page, ref))
                continue
            target = os.path.normpath(os.path.join(os.path.dirname(p), unquote(parts.path)))
            if ref.endswith("/") or os.path.isdir(target):
                target = os.path.join(target, "index.html")
            if not target.startswith(ROOT) or not os.path.isfile(target):
                broken.append((rel_page, ref))
            else:
                used_files.add(target)
    all_files = [os.path.join(d, f) for d, _, fs in os.walk(ROOT) for f in fs]
    assets = [f for f in all_files if "/assets/" in f and f.endswith((".webp", ".jpg", ".png", ".gif"))]
    unused = [f for f in assets if f not in used_files]
    size = sum(os.path.getsize(f) for f in all_files)
    dup_titles = {t: v for t, v in titles.items() if len(v) > 1}
    dup_descs = {t: v for t, v in descs.items() if len(v) > 1}
    print("html pages: %d (incl. 404)" % len(pages))
    print("internal links+srcs checked: %d (images %d), external: %d, broken: %d" % (links, img_refs, ext_links, len(broken)))
    print("banned-word list: %s" % ("generator/.banned_words" if banned_words else "none (file missing)"))
    print("tag errors: %d, img without alt: %d, banned words: %d" % (len(errors), no_alt, len(banned)))
    print("duplicate titles: %d, duplicate descriptions: %d" % (len(dup_titles), len(dup_descs)))
    print("image files: %d, unreferenced: %d" % (len(assets), len(unused)))
    print("total size: %.1f MB, files: %d" % (size / 1e6, len(all_files)))
    for x in broken[:30]:
        print("BROKEN", *x)
    for x in errors[:30]:
        print("TAG", x)
    for x in banned:
        print("BANNED", *x)
    for t, v in list(dup_titles.items())[:10]:
        print("DUP TITLE", t, v)
    for t, v in list(dup_descs.items())[:10]:
        print("DUP DESC", t, v)
    for f in unused[:10]:
        print("UNUSED", os.path.relpath(f, ROOT))
    sys.exit(1 if broken or errors or banned or no_alt or dup_titles or dup_descs else 0)


if __name__ == "__main__":
    main()
