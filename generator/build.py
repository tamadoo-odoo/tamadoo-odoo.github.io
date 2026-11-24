#!/usr/bin/env python3
"""Build the Tamadoo static site into docs/ from the tamadoo_store git branches.

Python 3 standard library only. If Pillow is importable, screenshots are
converted to WebP and banners to JPEG; without it the original files are copied.
"""
import argparse
import ast
import datetime
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser

SITE_URL = "https://tamadoo-odoo.github.io/"
SUPPORT_EMAIL = "tama.odoo@gmail.com"
STORE_URL = "https://apps.odoo.com/apps/modules/{series}/{module}"
VENDOR_URL = "https://apps.odoo.com/apps/modules/browse?author=Tamadoo"
SERIES = ["20.0", "19.0", "18.0", "17.0", "16.0"]
SOURCE_ORDER = ["20.0", "19.0", "18.0", "17.0", "16.0"]
MAX_IMG_WIDTH = 1600

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_STORE = os.path.join(os.path.dirname(ROOT), "tamadoo_store")
DEFAULT_STATUS = os.path.join(os.path.dirname(ROOT), "tasks", "store_audit", "store_status.json")
CACHE = os.path.join(HERE, ".cache")

try:
    from PIL import Image
except ImportError:
    Image = None

CATEGORIES = [
    ("pos", "Point of Sale", "POS add-ons for Odoo: payments, cashier control, reports, restaurant and retail tools."),
    ("sales", "Sales, CRM & Purchase", "Odoo add-ons for quotations, commissions, targets, discounts, CRM and purchasing."),
    ("inventory", "Inventory & Manufacturing", "Odoo add-ons for stock cards, stock valuation, delivery proof, assets and manufacturing cost."),
    ("accounting", "Accounting & Finance", "Odoo add-ons for financial reports, bank statements, invoice numbering and Indonesian accounting needs."),
    ("hr", "HR & Payroll", "Odoo add-ons for Indonesian payroll, leave, attendance, field tracking and recruitment tests."),
    ("website", "Website & eCommerce", "Odoo add-ons for the website shop: filters, brands, stock display and WhatsApp contact."),
    ("tools", "Productivity & Tools", "Odoo add-ons for backups, approvals, printing, menus, access and team knowledge."),
]
CAT_BY_KEY = {c[0]: c for c in CATEGORIES}

FEATURED = [
    "tamadoo_payroll_id", "tamadoo_pos_qris_payment", "tamadoo_sales_commission",
    "tamadoo_financial_reports_xlsx", "tamadoo_stock_card", "tamadoo_approval_engine",
    "tamadoo_attendance_geofence", "tamadoo_database_backup",
]


def category_key(odoo_category):
    top = (odoo_category or "").split("/")[0].strip().lower()
    if top == "point of sale":
        return "pos"
    if top in ("sales", "purchase", "crm"):
        return "sales"
    if top in ("inventory", "manufacturing"):
        return "inventory"
    if top == "accounting":
        return "accounting"
    if top == "human resources":
        return "hr"
    if top == "website":
        return "website"
    return "tools"


# ---------------------------------------------------------------- git access

class Repo:
    def __init__(self, path):
        self.path = path

    def git(self, *args, binary=False):
        out = subprocess.check_output(["git", "-C", self.path, *args])
        return out if binary else out.decode("utf-8")

    def branches(self):
        names = self.git("for-each-ref", "--format=%(refname:short)", "refs/heads").split()
        return [s for s in SERIES if s in names]

    def tree(self, series):
        """Return {path: blob_sha} for every file of the branch."""
        files = {}
        for line in self.git("ls-tree", "-r", series).splitlines():
            meta, path = line.split("\t", 1)
            files[path] = meta.split()[2]
        return files

    def blob(self, sha):
        return self.git("cat-file", "blob", sha, binary=True)

    def last_date(self, series, module):
        return self.git("log", "-1", "--format=%cs", series, "--", module).strip()


# ---------------------------------------------------------------- sanitiser

VOID = {"img", "br", "hr", "meta", "link", "input", "source", "col", "wbr"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "button",
                     "select", "textarea", "svg", "noscript", "template", "head", "title"}
KEEP = {"h2", "h3", "h4", "h5", "p", "ul", "ol", "li", "strong", "b", "em", "i", "code",
        "pre", "br", "table", "thead", "tbody", "tr", "th", "td", "img", "a", "div",
        "blockquote", "small", "sup", "sub", "dl", "dt", "dd", "figure", "figcaption"}
BLOCK = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table", "img", "pre",
         "blockquote", "div", "section", "figure"}
MAP = {"section": "div", "article": "div", "header": "div", "footer": "div", "h5": "h4",
       "h6": "h4", "u": "em", "tt": "code", "center": "div", "font": None, "span": None}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or {})
        self.children = []
        self.parent = parent

    def text(self):
        parts = []
        for c in self.children:
            parts.append(c if isinstance(c, str) else c.text())
        return "".join(parts)

    def has_desc(self, tags):
        for c in self.children:
            if isinstance(c, Node) and (c.tag in tags or c.has_desc(tags)):
                return True
        return False


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, [(k, v or "") for k, v in attrs], self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, [(k, v or "") for k, v in attrs], self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


BADGE_RE = re.compile(r"background", re.I)
HIDDEN_RE = re.compile(r"display\s*:\s*none", re.I)


VENDOR_LLM_RE = re.compile(r"Gemini\s*(?:/|or)\s*[A-Z][a-z]+")


class DescriptionCleaner:
    """Turns a store index.html into plain, layout-safe HTML."""

    def __init__(self, resolve_img):
        self.resolve_img = resolve_img
        self.dropped_images = []

    def clean(self, source):
        source = VENDOR_LLM_RE.sub("Gemini", source)
        tb = TreeBuilder()
        tb.feed(source)
        tb.close()
        return self.render(tb.root).strip()

    def render(self, node):
        return "".join(self.render_child(c) for c in node.children)

    def render_child(self, c):
        if isinstance(c, str):
            return html.escape(c, quote=False)
        tag = c.tag
        style = c.attrs.get("style", "")
        if tag in DROP_WITH_CONTENT or tag == "h1" or HIDDEN_RE.search(style):
            return ""
        txt = " ".join(c.text().split())
        if tag in ("span", "div") and BADGE_RE.search(style) and len(txt) < 40 \
                and not c.has_desc(BLOCK | {"p", "li"}):
            return ""
        if tag == "i" and not txt:
            return ""
        if tag == "img":
            return self.render_img(c)
        if tag == "a":
            inner = self.render(c)
            href = c.attrs.get("href", "")
            if re.match(r"^(https?:|mailto:)", href):
                return '<a href="%s" rel="nofollow noopener">%s</a>' % (html.escape(href), inner)
            return inner
        tag = MAP.get(tag, tag)
        inner = self.render(c)
        if tag is None or tag not in KEEP:
            return inner
        if tag == "br":
            return "<br>"
        if tag in ("div", "p", "li", "td", "th", "strong", "b", "em", "i", "code") and not inner.strip():
            return ""
        attrs = ""
        if tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if c.attrs.get(k, "").isdigit():
                    attrs += ' %s="%s"' % (k, c.attrs[k])
        if tag == "table":
            return '<div class="table-wrap"><table>%s</table></div>' % inner
        return "<%s%s>%s</%s>" % (tag, attrs, inner, tag)

    def render_img(self, c):
        src = c.attrs.get("src", "")
        found = self.resolve_img(src)
        if not found:
            self.dropped_images.append(src)
            return ""
        url, w, h = found
        alt = c.attrs.get("alt") or c.attrs.get("title") or ""
        if not alt:
            stem = os.path.splitext(os.path.basename(src))[0]
            alt = re.sub(r"^(v\d+_)?\d+[_-]?", "", stem).replace("_", " ").replace("-", " ").strip()
            alt = (alt[:1].upper() + alt[1:]) if alt else "Screenshot"
        size = ' width="%d" height="%d"' % (w, h) if w else ""
        return '<img src="%s" alt="%s"%s loading="lazy" decoding="async">' % (
            html.escape(url), html.escape(alt), size)


# ---------------------------------------------------------------- images

def convert_image(data, kind):
    """Return (bytes, ext, width, height). kind: 'shot' or 'banner'."""
    if Image is None:
        return data, None, 0, 0
    im = Image.open(io.BytesIO(data))
    im.load()
    if getattr(im, "is_animated", False):
        return data, None, im.width, im.height
    if im.width > MAX_IMG_WIDTH:
        im = im.resize((MAX_IMG_WIDTH, round(im.height * MAX_IMG_WIDTH / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    if kind == "banner":
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        else:
            im = im.convert("RGB")
        im.save(buf, "JPEG", quality=86, optimize=True, progressive=True)
        return buf.getvalue(), ".jpg", im.width, im.height
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    im.save(buf, "WEBP", quality=80, method=4)
    return buf.getvalue(), ".webp", im.width, im.height


class ImageStore:
    def __init__(self, repo, docs):
        self.repo = repo
        self.docs = docs
        self.count = 0
        os.makedirs(CACHE, exist_ok=True)

    def put(self, module, src_path, sha, kind):
        """Write the image for blob sha; return (relative path from docs, w, h)."""
        stem, ext = os.path.splitext(os.path.basename(src_path))
        stem = re.sub(r"[^A-Za-z0-9_.-]", "-", stem)
        tag = "pil" if Image else "raw"
        meta_path = os.path.join(CACHE, "%s-%s-%s.json" % (sha, kind, tag))
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            with open(os.path.join(CACHE, meta["file"]), "rb") as f:
                data = f.read()
        else:
            raw = self.repo.blob(sha)
            try:
                data, new_ext, w, h = convert_image(raw, kind)
            except Exception:
                data, new_ext, w, h = raw, None, 0, 0
            if new_ext is None or len(data) >= len(raw) and new_ext != ".jpg":
                data, new_ext = raw, ext.lower()
            meta = {"file": "%s-%s-%s%s" % (sha, kind, tag, new_ext), "ext": new_ext, "w": w, "h": h}
            with open(os.path.join(CACHE, meta["file"]), "wb") as f:
                f.write(data)
            with open(meta_path, "w") as f:
                json.dump(meta, f)
        rel_dir = os.path.join("assets", module)
        os.makedirs(os.path.join(self.docs, rel_dir), exist_ok=True)
        name = stem + meta["ext"]
        rel = os.path.join(rel_dir, name)
        n = 2
        while os.path.exists(os.path.join(self.docs, rel)):
            rel = os.path.join(rel_dir, "%s-%d%s" % (stem, n, meta["ext"]))
            n += 1
        with open(os.path.join(self.docs, rel), "wb") as f:
            f.write(data)
        self.count += 1
        return rel.replace(os.sep, "/"), meta["w"], meta["h"]


# ---------------------------------------------------------------- data

def display_name(name):
    name = re.sub(r"^Tamadoo\s+", "", name.strip())
    return name


def short_name(name):
    return re.split(r"\s+[-–—]\s+|\s*\(", name, maxsplit=1)[0].strip()


def fmt_price(price, currency):
    try:
        p = float(price or 0)
    except (TypeError, ValueError):
        p = 0.0
    if p <= 0:
        return "Free", 0.0
    sym = {"USD": "$", "EUR": "€"}.get(currency or "USD")
    amount = ("%.2f" % p).rstrip("0").rstrip(".") if p != int(p) else "%d" % p
    return (sym + amount) if sym else "%s %s" % (amount, currency), p


def version_label(series_list):
    nums = sorted(int(float(s)) for s in series_list)
    if not nums:
        return ""
    if nums == list(range(nums[0], nums[-1] + 1)) and len(nums) > 2:
        return "%d–%d" % (nums[0], nums[-1])
    return ", ".join(str(n) for n in nums)


def load_status(path):
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    return {(r["series"], r["module"]): r.get("http") == 200 for r in data.get("rows", [])}


def collect(repo, status):
    branches = repo.branches()
    trees = {s: repo.tree(s) for s in branches}
    manifests = {}
    for s in branches:
        for path, sha in trees[s].items():
            parts = path.split("/")
            if len(parts) == 2 and parts[1] == "__manifest__.py" and parts[0].startswith("tamadoo_"):
                try:
                    manifests[(s, parts[0])] = ast.literal_eval(repo.blob(sha).decode("utf-8"))
                except Exception as e:
                    print("WARN manifest %s %s: %s" % (s, parts[0], e), file=sys.stderr)
    modules = sorted({m for (_, m) in manifests})
    result = []
    for m in modules:
        series = [s for s in SERIES if (s, m) in manifests and manifests[(s, m)].get("installable", True)]
        if not series:
            continue
        src = next(s for s in SOURCE_ORDER if s in series)
        man = manifests[(src, m)]
        listed = [s for s in series if status is None or status.get((s, m), False)]
        result.append({
            "module": m, "source": src, "series": series, "listed": listed, "manifest": man,
            "tree": trees[src],
        })
    return result


# ---------------------------------------------------------------- html

def esc(s):
    return html.escape(s or "", quote=True)


def meta_desc(text, limit=158):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def page(ctx, rel_path, title, description, body, og_image=None, jsonld=None, extra_head="", base=None):
    """rel_path: page path relative to docs (e.g. 'modules/x/')."""
    root = base if base is not None else "../" * rel_path.count("/")
    canonical = ctx["site_url"] + rel_path
    head = [
        '<!doctype html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>%s</title>' % esc(title),
        '<meta name="description" content="%s">' % esc(description),
        '<link rel="canonical" href="%s">' % esc(canonical),
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Tamadoo Odoo Apps">',
        '<meta property="og:title" content="%s">' % esc(title),
        '<meta property="og:description" content="%s">' % esc(description),
        '<meta property="og:url" content="%s">' % esc(canonical),
    ]
    if og_image:
        head.append('<meta property="og:image" content="%s">' % esc(ctx["site_url"] + og_image))
        head.append('<meta name="twitter:card" content="summary_large_image">')
    head += [
        '<meta name="theme-color" content="#714b67">',
        '<link rel="icon" href="%sassets/favicon.svg" type="image/svg+xml">' % root,
        '<link rel="stylesheet" href="%sassets/site.css">' % root,
    ]
    if jsonld:
        head.append('<script type="application/ld+json">%s</script>' %
                    json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/"))
    head.append(extra_head)
    head.append('</head>')
    nav = "".join('<li><a href="%scategory/%s/">%s</a></li>' % (root, k, esc(n)) for k, n, _ in CATEGORIES)
    header = (
        '<body>\n<a class="skip" href="#main">Skip to content</a>\n'
        '<header class="site-header"><div class="wrap bar">'
        '<a class="brand" href="%s"><span class="logo" aria-hidden="true">T</span>Tamadoo <span class="brand-sub">Odoo Apps</span></a>'
        '<nav aria-label="Categories"><details class="menu"><summary>Categories</summary><ul>%s</ul></details></nav>'
        '</div></header>\n<main id="main">\n' % (root, nav)
    )
    footer = (
        '\n</main>\n<footer class="site-footer"><div class="wrap">'
        '<p><strong>Tamadoo</strong> &middot; Odoo add-ons made in Indonesia &middot; Odoo 16&ndash;20</p>'
        '<p>Questions or a custom change? <a href="mailto:%s">%s</a></p>'
        '<p class="fine">All modules are sold and downloaded on the official '
        '<a href="%s" rel="noopener">Odoo Apps Store</a>. Odoo is a trademark of Odoo S.A.; '
        'this site is not affiliated with Odoo S.A.</p>'
        '</div></footer>\n</body>\n</html>\n' % (SUPPORT_EMAIL, SUPPORT_EMAIL, VENDOR_URL)
    )
    return "\n".join(head) + "\n" + header + body + footer


def card(mod, root):
    price_cls = "free" if mod["price_value"] == 0 else ""
    words = " ".join([mod["name"], mod["summary"], mod["module"], mod["cat_name"]]).lower()
    return (
        '<li class="card" data-cat="%s" data-free="%s" data-q="%s">'
        '<a href="%smodules/%s/">'
        '<img src="%s%s" alt="" width="560" height="280" loading="lazy" decoding="async">'
        '<span class="card-body"><span class="card-title">%s</span>'
        '<span class="card-sum">%s</span>'
        '<span class="card-meta"><span class="price %s">%s</span><span class="vers">Odoo %s</span></span>'
        '</span></a></li>'
    ) % (mod["cat"], "1" if mod["price_value"] == 0 else "0", esc(words), root, mod["module"],
         root, esc(mod["banner"]), esc(mod["name"]), esc(mod["summary"]), price_cls,
         esc(mod["price"]), esc(mod["versions"]))


def store_buttons(mod):
    out = []
    for s in mod["series"]:
        if s in mod["listed"]:
            out.append('<a class="btn btn-store" href="%s" rel="noopener">'
                       '<span>Get it on the Odoo Apps Store</span><small>Odoo %s</small></a>'
                       % (esc(STORE_URL.format(series=s, module=mod["module"])), s))
    pending = [s for s in mod["series"] if s not in mod["listed"]]
    note = ""
    if pending:
        note = ('<p class="note">Odoo %s: not listed on the store yet. Ask at '
                '<a href="mailto:%s">%s</a>.</p>' % (", ".join(pending), SUPPORT_EMAIL, SUPPORT_EMAIL))
    return '<div class="store-buttons">%s</div>%s' % ("".join(out), note)


def jsonld_app(ctx, mod):
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": mod["name"],
        "description": mod["summary"],
        "applicationCategory": "BusinessApplication",
        "applicationSubCategory": mod["cat_name"],
        "operatingSystem": "Odoo " + ", ".join(sorted(mod["series"])),
        "softwareVersion": mod["version"],
        "url": ctx["site_url"] + "modules/%s/" % mod["module"],
        "image": ctx["site_url"] + mod["banner"],
        "author": {"@type": "Organization", "name": "Tamadoo", "email": SUPPORT_EMAIL},
        "offers": {
            "@type": "Offer",
            "price": ("%.2f" % mod["price_value"]),
            "priceCurrency": mod["currency"],
            "availability": "https://schema.org/InStock" if mod["listed"] else "https://schema.org/PreOrder",
            "url": STORE_URL.format(series=(mod["listed"] or mod["series"])[0], module=mod["module"]),
        },
    }
    return data


def build_module_page(ctx, mod, images, repo):
    m = mod["module"]
    tree = mod["tree"]
    desc_dir = "%s/static/description/" % m

    def resolve(src):
        if not src or re.match(r"^(https?:|data:|//)", src):
            return None
        path = os.path.normpath(desc_dir + src.split("?")[0].split("#")[0]).replace(os.sep, "/")
        sha = tree.get(path)
        if not sha:
            return None
        rel, w, h = images.put(m, path, sha, "shot")
        return "../../" + rel, w, h

    cleaner = DescriptionCleaner(resolve)
    idx_sha = tree.get(desc_dir + "index.html")
    body_html = cleaner.clean(repo.blob(idx_sha).decode("utf-8", "replace")) if idx_sha else ""
    mod["dropped_images"] = cleaner.dropped_images
    shots = body_html.count("<img ")
    mod["shots"] = shots

    video = ctx["videos"].get(m) or mod["manifest"].get("youtube_id") or ""
    video_html = ""
    if re.match(r"^[A-Za-z0-9_-]{6,20}$", video or ""):
        video_html = (
            '<section class="video"><h2>Video</h2><div class="video-frame">'
            '<iframe src="https://www.youtube-nocookie.com/embed/%s" title="%s video" loading="lazy" '
            'allow="encrypted-media; picture-in-picture" allowfullscreen></iframe></div></section>'
            % (video, esc(mod["name"])))

    related = [x for x in ctx["modules"] if x["cat"] == mod["cat"] and x["module"] != m][:4]
    related_html = ""
    if related:
        related_html = ('<section class="related"><h2>More %s apps</h2><ul class="cards">%s</ul>'
                        '<p><a href="../../category/%s/">All %s apps &rarr;</a></p></section>'
                        % (esc(mod["cat_name"]), "".join(card(x, "../../") for x in related),
                           mod["cat"], esc(mod["cat_name"])))

    title = "%s for Odoo %s | Tamadoo" % (mod["short"], mod["versions"])
    description = meta_desc("%s. %s app for Odoo %s, %s on the Odoo Apps Store." % (
        mod["summary"].rstrip("."), mod["cat_name"], mod["versions"],
        "free" if mod["price_value"] == 0 else mod["price"]))
    crumbs = ('<nav class="crumbs" aria-label="Breadcrumb"><a href="../../">Home</a> &rsaquo; '
              '<a href="../../category/%s/">%s</a> &rsaquo; <span>%s</span></nav>'
              % (mod["cat"], esc(mod["cat_name"]), esc(mod["short"])))
    vers_chips = "".join('<li>%s</li>' % s for s in sorted(mod["series"], key=float))
    body = (
        '<div class="wrap">%s'
        '<article class="module">'
        '<header class="module-head">'
        '<div class="module-intro">'
        '<h1>%s</h1>'
        '<p class="lead">%s</p>'
        '<dl class="facts"><div><dt>Price</dt><dd class="price-big">%s</dd></div>'
        '<div><dt>Odoo versions</dt><dd><ul class="chips">%s</ul></dd></div>'
        '<div class="wide"><dt>Technical name</dt><dd><code>%s</code></dd></div>'
        '<div><dt>License</dt><dd>%s</dd></div></dl>'
        '%s'
        '</div>'
        '<img class="banner" src="../../%s" alt="%s banner" width="560" height="280">'
        '</header>'
        '%s'
        '<section class="desc" aria-label="Module description">'
        '<p class="note">Text and screenshots below are from the Odoo %s version of this module.</p>'
        '%s'
        '</section>'
        '<section class="cta"><h2>Get %s</h2>%s'
        '<p>Questions before buying? Write to <a href="mailto:%s">%s</a>.</p></section>'
        '</article>%s</div>'
    ) % (crumbs, esc(mod["name"]), esc(mod["summary"]), esc(mod["price"]), vers_chips, esc(m),
         esc(mod["license"]), store_buttons(mod), esc(mod["banner"]), esc(mod["short"]), video_html,
         mod["source"], body_html, esc(mod["short"]), store_buttons(mod), SUPPORT_EMAIL, SUPPORT_EMAIL,
         related_html)
    rel = "modules/%s/" % m
    return rel, page(ctx, rel, title, description, body, og_image=mod["banner"], jsonld=jsonld_app(ctx, mod))


SEARCH_JS = """<script>
(function(){
var q=document.getElementById('q'),c=document.getElementById('cat'),f=document.getElementById('free'),
list=document.getElementById('all'),out=document.getElementById('count');
if(!q||!list)return;
document.getElementById('filters').hidden=false;
var cards=list.querySelectorAll('.card');
function run(){
var words=q.value.toLowerCase().split(/\\s+/).filter(Boolean),cat=c.value,fr=f.value,n=0;
cards.forEach(function(el){
var ok=(!cat||el.dataset.cat===cat)&&(!fr||el.dataset.free===fr)&&words.every(function(w){return el.dataset.q.indexOf(w)>=0;});
el.hidden=!ok;if(ok)n++;});
out.textContent=n+(n===1?' app':' apps');}
[q,c,f].forEach(function(el){el.addEventListener('input',run);el.addEventListener('change',run);});
var p=new URLSearchParams(location.search).get('q');if(p){q.value=p;}
run();
})();
</script>"""


def build_home(ctx):
    mods = ctx["modules"]
    by_cat = {k: [x for x in mods if x["cat"] == k] for k, _, _ in CATEGORIES}
    free = sum(1 for x in mods if x["price_value"] == 0)
    cats_html = "".join(
        '<li><a href="category/%s/"><span class="cat-name">%s</span>'
        '<span class="cat-count">%d apps</span><span class="cat-desc">%s</span></a></li>'
        % (k, esc(n), len(by_cat[k]), esc(d)) for k, n, d in CATEGORIES if by_cat[k])
    featured = [x for f in FEATURED for x in mods if x["module"] == f]
    opts = "".join('<option value="%s">%s</option>' % (k, esc(n)) for k, n, _ in CATEGORIES if by_cat[k])
    body = (
        '<section class="hero"><div class="wrap">'
        '<h1>Odoo apps for POS, Sales, Inventory, Accounting, HR and Website</h1>'
        '<p class="lead">Tamadoo makes add-on modules for Odoo in Indonesia: %d apps for '
        'Odoo 16, 17, 18, 19 and 20, %d of them free. Every app is sold and downloaded on the '
        'official Odoo Apps Store.</p>'
        '<form class="hero-search" action="./" method="get" role="search">'
        '<label for="hq" class="sr">Search apps</label>'
        '<input id="hq" name="q" type="search" placeholder="Search apps, e.g. QRIS, payroll, commission">'
        '<button type="submit" class="btn">Search</button></form>'
        '<ul class="hero-points"><li>Odoo 16.0 &ndash; 20.0</li><li>%d free apps</li>'
        '<li>Indonesian localisation: PPh 21, BPJS, QRIS, Terbilang</li></ul>'
        '</div></section>'
        '<div class="wrap">'
        '<section aria-labelledby="cats"><h2 id="cats">Browse by category</h2><ul class="cats">%s</ul></section>'
        '<section aria-labelledby="feat"><h2 id="feat">Featured apps</h2><ul class="cards">%s</ul></section>'
        '<section aria-labelledby="allh"><h2 id="allh">All apps</h2>'
        '<div id="filters" class="filters" hidden>'
        '<label>Search <input id="q" type="search" placeholder="Name or keyword"></label>'
        '<label>Category <select id="cat"><option value="">All categories</option>%s</select></label>'
        '<label>Price <select id="free"><option value="">Free and paid</option>'
        '<option value="1">Free only</option><option value="0">Paid only</option></select></label>'
        '<output id="count" aria-live="polite"></output></div>'
        '<ul id="all" class="cards">%s</ul></section>'
        '<section class="contact" aria-labelledby="contact"><h2 id="contact">Contact</h2>'
        '<p>Questions about an app, a version you need, or a custom change: '
        '<a href="mailto:%s">%s</a>.</p>'
        '<p>Buying and downloading happens on <a href="%s" rel="noopener">apps.odoo.com</a>; '
        'each app page here links to its store listing for every Odoo version.</p></section>'
        '</div>'
    ) % (len(mods), free, free, cats_html, "".join(card(x, "") for x in featured), opts,
         "".join(card(x, "") for x in mods), SUPPORT_EMAIL, SUPPORT_EMAIL, VENDOR_URL)
    body += SEARCH_JS
    title = "Tamadoo Odoo Apps: POS, Sales, Inventory, Accounting, HR | Odoo 16-20"
    description = meta_desc("%d Odoo add-ons by Tamadoo for Point of Sale, Sales, Inventory, Accounting, "
                            "HR & Payroll and Website. Odoo 16 to 20, made in Indonesia, %d free apps." % (len(mods), free))
    jsonld = {"@context": "https://schema.org", "@type": "Organization", "name": "Tamadoo",
              "url": ctx["site_url"], "email": SUPPORT_EMAIL,
              "description": "Odoo add-on modules made in Indonesia."}
    return "", page(ctx, "", title, description, body, og_image=ctx["og_default"], jsonld=jsonld)


def build_category(ctx, key):
    _, name, desc = CAT_BY_KEY[key]
    mods = [x for x in ctx["modules"] if x["cat"] == key]
    if not mods:
        return None
    free = sum(1 for x in mods if x["price_value"] == 0)
    body = (
        '<div class="wrap"><nav class="crumbs" aria-label="Breadcrumb"><a href="../../">Home</a> &rsaquo; '
        '<span>%s</span></nav>'
        '<header class="cat-head"><h1>%s apps for Odoo</h1><p class="lead">%s %d apps, %d free, for Odoo 16 to 20.</p></header>'
        '<ul class="cards">%s</ul></div>'
    ) % (esc(name), esc(name), esc(desc), len(mods), free, "".join(card(x, "../../") for x in mods))
    title = "%s Apps for Odoo 16-20 | Tamadoo" % name
    names = ", ".join(x["short"] for x in mods[:6])
    description = meta_desc("%s %s and more." % (desc, names))
    rel = "category/%s/" % key
    jsonld = {"@context": "https://schema.org", "@type": "ItemList", "name": "%s apps for Odoo" % name,
              "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                   "url": ctx["site_url"] + "modules/%s/" % x["module"], "name": x["name"]}
                                  for i, x in enumerate(mods)]}
    return rel, page(ctx, rel, title, description, body, og_image=mods[0]["banner"], jsonld=jsonld)


def build_404(ctx):
    body = ('<div class="wrap narrow"><h1>Page not found</h1>'
            '<p>This page does not exist. Try the <a href="./">list of all Odoo apps</a> or one of the categories above.</p></div>')
    html_text = page(ctx, "404.html", "Page not found | Tamadoo Odoo Apps", "Page not found.", body,
                     extra_head='<meta name="robots" content="noindex">', base=ctx["site_url"])
    return html_text.replace('<link rel="canonical" href="%s404.html">\n' % ctx["site_url"], "")


FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
           'fill="#714b67"/><text x="32" y="45" font-family="Arial,Helvetica,sans-serif" font-size="38" '
           'font-weight="700" text-anchor="middle" fill="#fff">T</text></svg>\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site-url", default=os.environ.get("SITE_URL", SITE_URL),
                    help="public base URL, e.g. https://tamadoo-odoo.github.io/ or https://user.github.io/tamadoo/")
    ap.add_argument("--store", default=DEFAULT_STORE, help="path to the tamadoo_store git repository")
    ap.add_argument("--status", default=DEFAULT_STATUS,
                    help="store_status.json from the store audit; series not listed there get no store button")
    ap.add_argument("--no-status", action="store_true", help="link every series, ignore the audit file")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"))
    args = ap.parse_args()
    site_url = args.site_url.rstrip("/") + "/"
    repo = Repo(args.store)
    status = None if args.no_status else load_status(args.status)
    print("store status: %s" % ("not used" if status is None else args.status))
    print("Pillow: %s" % ("yes" if Image else "no, images copied unchanged"))

    out = args.out
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "assets"))
    shutil.copy(os.path.join(HERE, "site.css"), os.path.join(out, "assets", "site.css"))
    with open(os.path.join(out, "assets", "favicon.svg"), "w") as f:
        f.write(FAVICON)
    open(os.path.join(out, ".nojekyll"), "w").close()
    root_files = os.path.join(HERE, "root_files")
    for name in sorted(os.listdir(root_files)) if os.path.isdir(root_files) else []:
        shutil.copy(os.path.join(root_files, name), os.path.join(out, name))

    videos_path = os.path.join(HERE, "videos.json")
    videos = json.load(open(videos_path)) if os.path.exists(videos_path) else {}
    images = ImageStore(repo, out)
    raw = collect(repo, status)
    mods = []
    for r in raw:
        man = r["manifest"]
        m = r["module"]
        name = display_name(man.get("name") or m)
        price, pval = fmt_price(man.get("price"), man.get("currency"))
        banner_path = "%s/static/description/banner.png" % m
        banner = None
        for s in [r["source"]] + [x for x in SOURCE_ORDER if x in r["series"] and x != r["source"]]:
            t = r["tree"] if s == r["source"] else repo.tree(s)
            if banner_path in t:
                banner = images.put(m, banner_path, t[banner_path], "banner")[0]
                break
        cat = category_key(man.get("category"))
        mods.append({
            "module": m, "name": name, "short": short_name(name),
            "summary": " ".join((man.get("summary") or name).split()),
            "price": price, "price_value": pval, "currency": man.get("currency") or "USD",
            "license": man.get("license") or "", "version": man.get("version") or "",
            "series": r["series"], "listed": r["listed"], "source": r["source"],
            "versions": version_label(r["series"]), "cat": cat, "cat_name": CAT_BY_KEY[cat][1],
            "banner": banner or "assets/og-default.jpg", "manifest": man, "tree": r["tree"],
            "date": repo.last_date(r["source"], m),
        })
    shorts = [x["short"] for x in mods]
    for x in mods:
        if shorts.count(x["short"]) > 1:
            x["short"] = x["name"]
    mods.sort(key=lambda x: ([c[0] for c in CATEGORIES].index(x["cat"]), x["name"].lower()))
    og_default = next((x["banner"] for x in mods if x["module"] == FEATURED[0]), mods[0]["banner"])
    ctx = {"site_url": site_url, "modules": mods, "videos": videos, "og_default": og_default}

    pages = {}
    for mod in mods:
        rel, text = build_module_page(ctx, mod, images, repo)
        pages[rel] = text
    for k, _, _ in CATEGORIES:
        res = build_category(ctx, k)
        if res:
            pages[res[0]] = res[1]
    rel, text = build_home(ctx)
    pages[rel] = text
    for rel, text in pages.items():
        path = os.path.join(out, rel, "index.html") if rel.endswith("/") or rel == "" else os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    with open(os.path.join(out, "404.html"), "w", encoding="utf-8") as f:
        f.write(build_404(ctx))

    today = max(x["date"] for x in mods)
    dates = {"modules/%s/" % x["module"]: x["date"] for x in mods}
    urls = []
    for rel in sorted(pages, key=lambda r: (r.count("/"), r)):
        urls.append("<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (
            esc(site_url + rel), dates.get(rel, today)))
    with open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % "\n".join(urls))
    with open(os.path.join(out, "robots.txt"), "w") as f:
        f.write("User-agent: *\nAllow: /\n\nSitemap: %ssitemap.xml\n" % site_url)

    dropped = sum(len(x.get("dropped_images", [])) for x in mods)
    no_btn = sum(len(x["series"]) - len(x["listed"]) for x in mods)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    print("modules: %d  pages: %d (+404)  images written: %d  store buttons: %d  series not listed: %d"
          % (len(mods), len(pages), images.count, sum(len(x["listed"]) for x in mods), no_btn))
    print("description images missing in repo (dropped): %d" % dropped)
    for x in mods:
        for d in x.get("dropped_images", []):
            print("  missing: %s %s" % (x["module"], d))
    print("site size: %.1f MB in %s" % (size / 1e6, out))


if __name__ == "__main__":
    main()
