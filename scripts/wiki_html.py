#!/usr/bin/env python3
"""HTML-страницы базы знаний Wisp.

Зачем: по file:// страница не может читать соседние файлы. Поэтому общий CSS
и движок схем вшиты в каждую страницу, а данные для поиска и дерева — в
html/index.html. Скрипт копирует их и проверяет страницы. Схемы он не рисует.

Команды:
  serve [--port ПОРТ]                                   открыть базу в браузере с возможностью удаления
  create ИМЯ --section S --summary ТЕКСТ --heading ЗАГОЛОВОК ...   новая HTML-страница
  new <путь.md>... [--section S] [--sub S] [--force] [--out ФАЙЛ]   черновик страницы из md
  index [--html-only]                                   данные поиска и дерева → html/index.html
  sync                                                  wiki-css и wiki-engine из шаблона во все страницы
  links [--edited ИМЯ ...]                               ссылки и даты; у перечисленных страниц источник — HTML
  check [--html-only] [--only ИМЯ] [--render] [--screenshot ФАЙЛ]  проверка страниц
  file <имя заметки | путь.md>...                       адрес страницы: file:///…/html/<файл>.html

Имя файла страницы — транслит имени заметки (page_file): «Кворум (quorum)» → html/kvorum-quorum.html.
Имя заметки хранится в title блока wiki-meta.

При импорте из архива папка lectures/ содержит заметки встреч из Spook: раздел «Встречи» берётся по папке,
а Заметка без Выжимки и страницы для check — «в работе», не ошибка.

`new` нужен markdown-it-py:
  uv run --with markdown-it-py python3 scripts/wiki_html.py --markdown-root КАТАЛОГ new …
Без --markdown-root индекс и проверки используют только HTML проекта.
Остальные команды — только stdlib.
"""

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

PROJECT = Path(__file__).resolve().parent.parent
HTML_DIR = PROJECT / "html"
TEMPLATE = PROJECT / "_templates" / "Заметка.html"
INDEX = HTML_DIR / "index.html"
VAULT = None  # Корень Markdown задаёт только тот, кто работает с архивом.
SKIP_DIRS = {".obsidian", ".trash", "_templates", "_tools", "html", "_build", "_site"}
TERM_MAX = 8 * 1024
TEXT_MAX = 200000  # scst.html — одна большая страница, её текст ищется целиком

# Раздел и подраздел заметок вне SCST/ (в md не записывается — только в meta страниц).
SECTIONS = {
    ("Хранение данных", ""): [
        "ALUA (Asymmetric Logical Unit Access)", "CDB (Command Descriptor Block)", "ESXi (APD и PDL)",
        "FC exchange (обмен в Fibre Channel)", "FCP (Fibre Channel Protocol)", "Fibre Channel (FC)",
        "Flash память", "HDD, SSD", "IQN (iSCSI Qualified Name)", "Initiator и target (SCSI)",
        "LUN (Logical Unit Number)", "RDMA (Remote Direct Memory Access)",
        "SCSI (Small Computer System Interface)", "SCSI Persistent Reservations (PR)",
        "SFP (Small Form-factor Pluggable)", "TMF (Task Management Function)", "WWN (World Wide Name)",
        "iSCSI (Internet SCSI)", "iSER (iSCSI Extensions for RDMA)"],
    ("Железо", ""): [
        "DMA (Direct Memory Access)", "Embedded", "Firmware (прошивка устройства)", "HBA (Host Bus Adapter)",
        "IOMMU (Input-Output Memory Management Unit)", "MCU (Microcontroller Unit)",
        "MSI-X (Message Signaled Interrupts eXtended)", "NUMA (Non-Uniform Memory Access)",
        "NVMe (Non-Volatile Memory Express)", "PCIe (Peripheral Component Interconnect Express)",
        "SAS (Serial Attached SCSI)", "SBC (Single-Board Computer)", "SoC (System on a Chip)", "UEFI",
        "amd64, arm64, risk-v", "Кольцевая очередь и doorbell"],
    ("Linux", ""): [
        "Block layer (блочный уровень Linux)", "ELF (Executable and Linkable Format)",
        "In-tree и out-of-tree модули ядра Linux", "Scatter-gather list (SG-список)", "System.map",
        "initrd (initramfs)", "sysfs (файловая система ядра Linux)", "Кросскомпиляция"],
    ("Linux", "Пакеты"): ["APT-RPM (ALT Linux)", "NEVRA", "RPM", "apt", "dpkg"],
    ("Разработка", ""): [
        "Bootstrap", "DevOps", "Jenkins", "Manifest", "On-premise", "SDK (Software Development Kit)",
        "SDLC (Software Development Life Cycle)", "TUI (Text User Interface)", "Нативно"],
    ("Разработка", "Git"): ["Merge-коммит (git)", "git rebase"],
    ("Разработка", "Тестирование"): [
        "Benchmark", "Harness", "QA (Quality Assurance)", "RTM (Requirements Traceability Matrix)",
        "UAT (User Acceptance Testing)", "Виды тестирования"],
    ("Разработка", "Безопасность"): [
        "ACL (Access Control List)", "CVE (Common Vulnerabilities and Exposures)",
        "DAST (Dynamic Application Security Testing)", "OWASP (Open Worldwide Application Security Project)",
        "SAST (Static Application Security Testing)", "SCA (Software Composition Analysis)", "Фишинг"],
    ("Данные и распределённые системы", ""): [
        "2PC (2-phase commit)", "ACID", "Ledger", "PITR (Point-in-Time Recovery)", "WAL (Write-Ahead Logging)",
        "Кворум (quorum)", "Реконсиляция (reconsilation)"],
    ("Сеть и веб", ""): [
        "HTTP (Hypertext Transfer Protocol)", "PWA (Progressive Web App)",
        "WebDAV (Web Distributed Authoring and Versioning)", "Webhook (вебхук)", "Статус коды"],
    ("AI", ""): ["BM25 (Okapi BM25)", "RAG (Retrieval-Augmented Generation)"],
    ("Менеджмент", ""): [
        "KPI (Key Performance Indicator)", "SLA (Service Level Agreement)", "TBD (To Be Decided)",
        "Диаграмма Ганта"],
    ("Инструменты", ""): ["kitty (хоткеи)", "uv"],
    ("Входящие", ""): ["Claude Code", "Интересные проекты", "Паттерны_Kubernetes_Ибрам_Хасс", "kubernetes"],
    # заметки встреч по имени не перечисляются: раздел задаёт папка lectures/ (LECTURES_DIR, section_for).
    ("Встречи", ""): [],
}
SECTION_OF = {name: key for key, names in SECTIONS.items() for name in names}
# Папка заметок встреч (их создаёт Spook, оформляет скилл spook-lecture) и её раздел — он же третья вкладка
# главной. Имя Заметки начинается с «ГГГГ-ММ-ДД ЧЧ-ММ» (начало Сессии), поэтому вкладка сортируется по имени.
LECTURES_DIR = "lectures"
MEETINGS = "Встречи"
TRANSCRIPT_RE = re.compile(r"^## Транскрипт[ \t]*$", re.M)  # граница: выше — Выжимка Агента, ниже — Транскрипт Spook

STATUS_CLS = {"черновик": "st-draft", "понятно": "st-ok", "сверено с кодом": "st-verified"}
CALLOUT_TITLE = {"info": "Справка", "note": "Заметка", "tip": "Совет", "warning": "Внимание",
                 "danger": "Опасно", "example": "Пример", "question": "Вопрос", "abstract": "Кратко"}
# Тема светлая; ?theme=dark — тёмная, только для проверки скриншотами.
# in-frame — страница открыта в окне просмотра index.html: своя «База знаний» и поиск прячутся.
THEME_JS = ('<script>var t=(location.search.match(/[?&]theme=(light|dark)/)||[])[1];'
            'if(t)document.documentElement.setAttribute("data-theme",t);'
            'if(window.top!==window)document.documentElement.classList.add("in-frame")</script>')
# Иконка вкладки: цветная плашка раздела и белый значок 24×24. У подраздела может быть свой
# значок (цвет — от раздела); "" — главная. Новый раздел — добавить сюда и запустить sync.
ICON_COLOR = {
    "": "#1e293b", "Хранение данных": "#2563eb", "SCST": "#dc2626", "Железо": "#be185d",
    "Linux": "#d97706", "Разработка": "#059669", "Данные и распределённые системы": "#0891b2",
    "Сеть и веб": "#4f46e5", "AI": "#9333ea", "Менеджмент": "#c2410c", "Инструменты": "#57534e",
    "Работа": "#0f766e", "Встречи": "#4d7c0f", "Входящие": "#64748b",
}
ICON_GLYPH = {
    "": '<path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2zM22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/>',
    "Хранение данных": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>'
                       '<path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "SCST": '<circle cx="12" cy="12" r="9.5"/><circle cx="12" cy="12" r="5"/>'
            '<circle cx="12" cy="12" r="1.2" fill="#fff"/>',
    "Железо": '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/>'
              '<path d="M9 1.5v3.5M15 1.5v3.5M9 19v3.5M15 19v3.5M1.5 9h3.5M1.5 15h3.5M19 9h3.5M19 15h3.5"/>',
    "Linux": '<path d="M4 17l6-6-6-6M12 19h8"/>',
    "Linux › Пакеты": '<path d="M21 7.5l-9-5-9 5v9l9 5 9-5zM3 7.5l9 5 9-5M12 12.5v9"/>',
    "Разработка": '<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>',
    "Разработка › Git": '<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>'
                        '<path d="M18 9a9 9 0 0 1-9 9"/>',
    "Разработка › Тестирование": '<circle cx="12" cy="12" r="10"/><path d="M7.5 12.5l3 3 6-6.5"/>',
    "Разработка › Безопасность": '<path d="M12 2l8 3v7c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V5z"/>',
    "Данные и распределённые системы": '<circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/>'
                                       '<circle cx="19" cy="19" r="3"/><path d="M10.6 7.7l-4.2 8.6M13.4 7.7l4.2 8.6M8 19h8"/>',
    "Сеть и веб": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2c2.6 2.8 4 6.2 4 10s-1.4 7.2-4 10'
                  'c-2.6-2.8-4-6.2-4-10s1.4-7.2 4-10z"/>',
    "AI": '<path fill="#fff" d="M12 2c.7 5.3 3.7 8.3 9 9-5.3.7-8.3 3.7-9 9-.7-5.3-3.7-8.3-9-9 5.3-.7 8.3-3.7 9-9z"/>',
    "Менеджмент": '<path stroke-width="3" d="M5 21v-7M12 21V4M19 21v-11"/>',
    "Инструменты": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9'
                   'a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
    "Работа": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M2 13h20"/>',
    # наушники для встреч
    "Встречи": '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z'
              'M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>',
    "Входящие": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6'
                'l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
}
ICON_RE = re.compile(r'<link rel="icon"[^>]*>')
# Поле поиска в верхней панели страницы: Enter открывает index.html?q=… с результатами.
SEARCH_FORM = ('<form class="ix-search pg" action="index.html" target="_top" role="search"><input name="q" type="search" '
               'placeholder="Поиск по базе…" autocomplete="off" spellcheck="false" aria-label="Поиск"><kbd>/</kbd></form>')
SEARCH_RE = re.compile(r'<form class="ix-search pg"[^>]*>.*?</form>', re.S)
META_KEYS = ["title", "aliases", "tags", "term", "section", "sub", "order", "level", "status",
             "parent", "source", "version", "updated"]

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
WL_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
PH_RE = re.compile("(\\d+)")
URL_RE = re.compile(r"(?<![<(\[\"'=/])\bhttps?://[^\s<>\]\)\"'`]+")


# ---------------------------------------------------------------- общее

def esc(s, quote=True):
    return html.escape("" if s is None else str(s), quote=quote)


def slug(s):
    s = re.sub(r"[^\w\s-]", "", (s or "").lower()).strip()
    return re.sub(r"\s+", "-", s)


def unique_id(base, used_ids):
    base = base or "раздел"
    sid, k = base, 2
    while sid in used_ids:
        sid = "%s-%d" % (base, k)
        k += 1
    used_ids.add(sid)
    return sid


def head_slug(head):
    """Якорь заголовка; цепочка [[Заметка#Часть#Раздел]] → часть--раздел (так устроена scst.html)."""
    return "--".join(slug(h) for h in head.split("#") if h.strip())


TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def page_file(name):
    """Имя файла страницы: транслит, нижний регистр, цифры и дефисы. «Кворум (quorum)» → kvorum-quorum.html."""
    s = "".join(TRANSLIT.get(ch, ch) for ch in (name or "").lower())
    return (re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "page") + ".html"


def file_from_href(href):
    """Файл страницы из href: «kvorum-quorum.html#раздел» → kvorum-quorum.html; не страница — None."""
    path = unquote(href.partition("#")[0].partition("?")[0]).removeprefix("./")
    return path if path.endswith(".html") and "/" not in path else None


def today():
    return dt.date.today().isoformat()


def mdate(path):
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def read(p):
    return Path(p).read_text(encoding="utf-8")


def stage_text(path, draft, text, keep_mtime=False):
    draft.write_text(text, encoding="utf-8")
    if path.exists():
        st = path.stat()
        draft.chmod(st.st_mode & 0o777)
        if keep_mtime:
            os.utime(draft, ns=(st.st_atime_ns, st.st_mtime_ns))


def write_page(path, text, keep_mtime=False):
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        draft = Path(tmp) / path.name
        stage_text(path, draft, text, keep_mtime)
        draft.replace(path)


@contextmanager
def wiki_write_lock():
    """Один писатель для HTTP и CLI. Блокировку снимает ОС при завершении процесса."""
    HTML_DIR.mkdir(exist_ok=True)
    if os.name == "nt":
        import msvcrt
        with (HTML_DIR / ".wisp.lock").open("a+b") as lock:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            yield
    else:
        import fcntl
        fd = os.open(HTML_DIR, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def all_notes():
    """Имя заметки → путь к md (имя = имя файла без .md)."""
    res = {}
    for p in sorted(VAULT.rglob("*.md")):
        rel = p.relative_to(VAULT)
        if rel.parts[0] in SKIP_DIRS or rel.parts[0].startswith("."):
            continue
        if p.stem in res:
            raise ValueError("одно имя у двух заметок: %s и %s" % (res[p.stem], p))
        res[p.stem] = p
    return res


def page_paths():
    if not HTML_DIR.is_dir():
        return []
    return [p for p in sorted(HTML_DIR.glob("*.html"))
            if p.name != "index.html" and not p.name.startswith("_") and not p.is_symlink() and p.is_file()]


def page_title(p):
    """Имя заметки страницы — title в wiki-meta (имя файла — только транслит)."""
    meta = get_json(read(p), "wiki-meta")
    title = meta.get("title") if isinstance(meta, dict) else None
    return title if isinstance(title, str) and title.strip() else p.stem


def all_pages():
    """Имя заметки → путь к странице."""
    return {page_title(p): p for p in page_paths()}


def by_file(pages):
    """Имя файла страницы → имя заметки."""
    return {p.name: name for name, p in pages.items()}


def frontmatter(text):
    """Простой разбор YAML-шапки: key: value, [a, b], "строка"."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        if text.rstrip().endswith("\n---"):
            end = text.rstrip().rfind("\n---")
        else:
            return {}, text
    fm, body = text[4:end], text[end + 5:]
    data = {}
    for line in fm.split("\n"):
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            items, cur, q = [], "", None
            for ch in v[1:-1]:
                if q:
                    if ch == q:
                        q = None
                    else:
                        cur += ch
                elif ch in "\"'":
                    q = ch
                elif ch == ",":
                    items.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            if cur.strip():
                items.append(cur.strip())
            data[k] = [i for i in items if i]
        elif len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            data[k] = v[1:-1]
        elif re.fullmatch(r"-?\d+", v):
            data[k] = int(v)
        else:
            data[k] = v
    return data, body


def block(text, tag, ident):
    """Найти <tag id="ident">…</tag> (для script/style с типом тоже)."""
    m = re.search(r'<%s(?: type="application/json")? id="%s">(.*?)</%s>' % (tag, re.escape(ident), tag), text, re.S)
    return m


def get_json(text, ident, default=None):
    m = block(text, "script", ident)
    if not m:
        return default
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return default


def set_json(text, ident, data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", r"\u003c")
    m = block(text, "script", ident)
    new = '<script type="application/json" id="%s">%s</script>' % (ident, payload)
    if not m:
        raise ValueError("нет блока %s" % ident)
    return text[:m.start()] + new + text[m.end():]


def description(text):
    m = re.search(r'<meta name="description" content="([^"]*)">', text)
    return html.unescape(m.group(1)) if m else ""


def template_blocks():
    t = read(TEMPLATE)
    css = re.search(r'<style id="wiki-css">.*?</style>', t, re.S)
    eng = re.search(r'<script id="wiki-engine">.*?</script>', t, re.S)
    if not css or not eng:
        raise SystemExit("в шаблоне %s нет wiki-css или wiki-engine" % TEMPLATE)
    return css.group(0), eng.group(0)


def scst_info(path):
    """Подраздел SCST (путь подпапок через ›)."""
    rel = path.relative_to(VAULT / "SCST")
    return " › ".join(rel.parts[:-1])


def scst_order():
    """Порядок чтения: ссылки раздела «Порядок чтения» в SCST/SCST.md."""
    moc = VAULT / "SCST" / "SCST.md"
    if not moc.exists():
        return {}
    _, body = frontmatter(read(moc))
    m = re.search(r"^## Порядок чтения\n(.*?)(?=^## )", body, re.S | re.M)
    order = {"SCST": 0}
    if m:
        for wl in WL_RE.findall(m.group(1)):
            name = wl.replace("\\|", "|").split("|")[0].split("#")[0].strip()
            order.setdefault(name, len(order))
    return order


def is_lecture(path):
    """Заметка встречи — любой md в папке lectures/."""
    return path.relative_to(VAULT).parts[0] == LECTURES_DIR


def lecture_in_progress(path):
    """Заметка встречи без Выжимки: над «## Транскрипт» ничего, кроме свойств. Сессия идёт или ждёт Агента."""
    _, body = frontmatter(read(path))
    m = TRANSCRIPT_RE.search(body)
    return not (body[:m.start()] if m else body).strip()


def section_for(name, path, section=None, sub=None):
    rel = path.relative_to(VAULT)
    if rel.parts[0] == "SCST":
        return section or "SCST", sub if sub is not None else scst_info(path)
    if is_lecture(path):
        return section or MEETINGS, sub or ""
    if section:
        return section, sub or ""
    if name in SECTION_OF:
        s, ss = SECTION_OF[name]
        return s, sub if sub is not None else ss
    return None, None


# ---------------------------------------------------------------- md → html

class Converter:
    def __init__(self, notes):
        try:
            from markdown_it import MarkdownIt
        except ModuleNotFoundError as exc:
            if exc.name != "markdown_it":
                raise
            raise ValueError("для импорта Markdown нужен пакет markdown-it-py") from exc
        self.notes = notes
        self.links = []
        self.used_ids = set()
        md = MarkdownIt("commonmark", {"breaks": True, "html": True}).enable(["table", "strikethrough"])
        md.add_render_rule("fence", self.r_fence)
        md.add_render_rule("table_open", self.r_table_open)
        md.add_render_rule("table_close", lambda s, t, i, o, e: "</table></div>\n")
        self.md = md

    # -- render rules
    def r_fence(self, tokens, idx, options, env):
        tok = tokens[idx]
        info = tok.info.strip().split()[0] if tok.info.strip() else ""
        src = tok.content
        if info in ("mermaid", "plantuml", "puml") or src.lstrip().startswith("@startuml"):
            kind = info if info in ("mermaid", "plantuml", "puml") else "plantuml"
            return ('<figure class="dia-todo" data-lang="%s"><figcaption>Схема ещё не перерисована (%s). '
                    'Исходник:</figcaption><pre><code>%s</code></pre></figure>\n' % (kind, kind, esc(src, False)))
        cls = ' class="language-%s"' % esc(info) if info else ""
        return "<pre><code%s>%s</code></pre>\n" % (cls, esc(src, False))

    def r_table_open(self, tokens, idx, options, env):
        return '<div class="tbl"><table class="tr%s">\n' % (" nw" if tokens[idx].meta.get("nw") else "")

    # -- ссылки
    def link_html(self, inner, embed=False):
        inner = inner.replace("\\|", "|")
        target, _, label = inner.partition("|")
        target = target.strip()
        name, _, head = target.partition("#")
        name, head, label = name.strip(), head.strip(), label.strip()
        if embed and name.endswith(".base"):
            return ""
        if not label:
            label = name or head
        lab = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(label, False))
        if not name:
            return '<a href="#%s">%s</a>' % (esc(head_slug(head)), lab)
        if name in self.notes:
            href = page_file(name) + ("#" + head_slug(head) if head else "")
            return '<a class="wl" href="%s">%s</a>' % (esc(href), lab)
        return '<span class="wl planned" data-target="%s">%s</span>' % (esc(target), lab)

    def placeholder(self, inner, embed=False):
        self.links.append(self.link_html(inner, embed))
        return "%d" % (len(self.links) - 1)

    def label_text(self, s):
        """Текст для заголовков и slug: заглушки → подписи ссылок."""
        return PH_RE.sub(lambda m: re.sub(r"<[^>]+>", "", html.unescape(self.links[int(m.group(1))])), s)

    def restore(self, s):
        return PH_RE.sub(lambda m: self.links[int(m.group(1))], s)

    # -- подготовка md: ссылки → заглушки (вне кода), голые URL → <url>
    def prep_line(self, line):
        out, i, n = [], 0, len(line)
        buf = []

        def autolink(match):
            url = match[0].rstrip(".,;:")
            return "<%s>%s" % (url, match[0][len(url):])

        def flush():
            if buf:
                t = "".join(buf)
                t = URL_RE.sub(autolink, t) if "http" in t else t
                out.append(t)
                buf.clear()
        while i < n:
            if line.startswith("[[", i):
                m = WL_RE.match(line, i)
                if m:
                    embed = bool(buf) and buf[-1] == "!"
                    if embed:
                        buf.pop()
                    flush()
                    out.append(self.placeholder(m.group(1), embed))
                    i = m.end()
                    continue
            if line[i] == "`":
                j = i
                while j < n and line[j] == "`":
                    j += 1
                ticks = line[i:j]
                k = line.find(ticks, j)
                while k >= 0 and k + len(ticks) < n and line[k + len(ticks)] == "`":
                    k = line.find(ticks, k + len(ticks) + 1)
                if k >= 0:
                    flush()
                    out.append(line[i:k + len(ticks)])
                    i = k + len(ticks)
                    continue
                buf.append(ticks)
                i = j
                continue
            buf.append(line[i])
            i += 1
        flush()
        return "".join(out)

    def prep(self, lines):
        res, fence = [], None
        for ln in lines:
            if fence:
                res.append(ln)
                m = FENCE_RE.match(ln)
                if m and m.group(2)[0] == fence[0] and len(m.group(2)) >= fence[1] and not m.group(3).strip():
                    fence = None
                continue
            m = FENCE_RE.match(ln)
            if m:
                fence = (m.group(2)[0], len(m.group(2)))
                res.append(ln)
                continue
            res.append(self.prep_line(ln))
        return "\n".join(res)

    # -- проходы по токенам
    def inline_text(self, tok):
        parts = []
        for c in tok.children or []:
            if c.type in ("text", "code_inline"):
                parts.append(c.content)
            elif c.type in ("softbreak", "hardbreak"):
                parts.append(" ")
        return self.label_text("".join(parts))

    def transform(self, tokens, chips=False):
        from markdown_it.token import Token
        stack = []
        for i, tok in enumerate(tokens):
            if tok.type == "heading_open":
                tok.attrSet("id", unique_id(slug(self.inline_text(tokens[i + 1])), self.used_ids))
            elif tok.type == "blockquote_open":
                stack.append(tok)
                if i + 2 < len(tokens) and tokens[i + 1].type == "paragraph_open":
                    inl = tokens[i + 2]
                    m = re.match(r"^\[!(\w+)\][+-]?\s*", inl.content)
                    if m and inl.children and inl.children[0].type == "text":
                        kind = m.group(1).lower()
                        tok.tag = "div"
                        tok.attrSet("class", "callout callout-" + kind)
                        tok.meta["callout"] = True
                        ch = inl.children
                        ch[0].content = re.sub(r"^\[!\w+\][+-]?\s*", "", ch[0].content)
                        k = 0
                        while k < len(ch) and ch[k].type not in ("softbreak", "hardbreak"):
                            k += 1
                        title, rest = ch[:k], ch[k + 1:]
                        if not "".join(c.content for c in title).strip():
                            t = Token("text", "", 0)
                            t.content = CALLOUT_TITLE.get(kind, kind.capitalize())
                            title = [t]
                        o, c = Token("html_inline", "", 0), Token("html_inline", "", 0)
                        o.content, c.content = '<b class="h">', "</b>"
                        # заголовок — <b class="h"> (display: block) в начале первого абзаца
                        inl.children = [o] + title + [c] + rest
            elif tok.type == "blockquote_close":
                op = stack.pop() if stack else None
                if op is not None and op.meta.get("callout"):
                    tok.tag = "div"
            elif tok.type == "list_item_open" and i + 2 < len(tokens) and tokens[i + 1].type == "paragraph_open":
                inl = tokens[i + 2]
                m = re.match(r"^\[([ xX])\]\s+", inl.content)
                if m and inl.children and inl.children[0].type == "text":
                    tok.attrSet("class", "task" + (" done" if m.group(1) in "xX" else ""))
                    inl.children[0].content = re.sub(r"^\[[ xX]\]\s+", "", inl.children[0].content)
                    cb = Token("html_inline", "", 0)
                    cb.content = '<span class="cb"></span>'
                    inl.children.insert(0, cb)
            elif tok.type == "table_open":
                firsts, j, first_cell = [], i, False
                while j < len(tokens) and tokens[j].type != "table_close":
                    if tokens[j].type == "tr_open":
                        first_cell = True
                    elif tokens[j].type == "td_open" and first_cell:
                        firsts.append(len(self.label_text(tokens[j + 1].content)))
                        first_cell = False
                    elif tokens[j].type == "th_open":
                        first_cell = False
                    j += 1
                tok.meta["nw"] = bool(firsts) and max(firsts) <= 28
        if chips:
            for i, tok in enumerate(tokens):
                if tok.type == "bullet_list_open" and tok.level == 0:
                    j, ok = i + 1, True
                    while tokens[j].type != "bullet_list_close" or tokens[j].level != 0:
                        if tokens[j].type == "inline" and not re.fullmatch("\\d+", tokens[j].content.strip()):
                            ok = False
                        j += 1
                    if ok:
                        tok.attrSet("class", "chips")
        return tokens

    def render(self, lines, chips=False, sub=False):
        """md-строки → (html первого абзаца или '', html остального)."""
        src = self.prep(lines)
        env = {}
        tokens = self.md.parse(src, env)
        self.transform(tokens, chips)
        first = ""
        if sub and tokens and tokens[0].type == "paragraph_open":
            k = next(i for i, t in enumerate(tokens) if t.type == "paragraph_close")
            tokens[0].attrSet("class", "sub")
            first = self.md.renderer.render(tokens[:k + 1], self.md.options, env)
            tokens = tokens[k + 1:]
        rest = self.md.renderer.render(tokens, self.md.options, env)
        return self.restore(first).strip(), self.restore(rest).strip()

    def inline(self, s):
        return self.restore(self.md.renderInline(self.prep_line(s)))


def split_body(body):
    """Тело md → (h1, [(заголовок ## или None, строки)])."""
    chunks, h1, fence, seen = [[None, []]], None, None, False
    for ln in body.split("\n"):
        if fence:
            chunks[-1][1].append(ln)
            m = FENCE_RE.match(ln)
            if m and m.group(2)[0] == fence[0] and len(m.group(2)) >= fence[1] and not m.group(3).strip():
                fence = None
            continue
        m = FENCE_RE.match(ln)
        if m:
            fence = (m.group(2)[0], len(m.group(2)))
            chunks[-1][1].append(ln)
            seen = True
            continue
        if not seen and ln.startswith("# "):
            h1 = ln[2:].strip()
            seen = True
            continue
        if ln.strip():
            seen = True
        if ln.startswith("## "):
            chunks.append([ln[3:].strip(), []])
            continue
        chunks[-1][1].append(ln)
    return h1, [c for c in chunks if c[0] != "Содержание"]


def note_summary(path, md):
    """Первый абзац заметки простым текстом (для title, description, индекса)."""
    _, body = frontmatter(read(path))
    _, chunks = split_body(body)
    src = []
    for title, lines in chunks:
        if title is None or title == "Коротко":
            src = lines
            if "\n".join(lines).strip():
                break
    tokens = md.parse("\n".join(src))
    for i, t in enumerate(tokens):
        if t.type == "paragraph_open" and t.level == 0:
            raw = tokens[i + 1].content
            raw = WL_RE.sub(lambda m: (m.group(1).replace("\\|", "|").split("|", 1)[-1]
                                       if "|" in m.group(1) else m.group(1).split("#")[0]), raw)
            txt = re.sub(r"<[^>]+>", "", md.renderInline(raw))
            txt = re.sub(r"\s+", " ", html.unescape(txt)).strip()
            return txt if len(txt) <= 600 else txt[:597].rstrip() + "…"
        if t.type in ("heading_open", "bullet_list_open", "ordered_list_open", "table_open", "fence"):
            break
    return ""


def favicon(meta=None):
    """<link rel="icon"> по разделу страницы; без meta — иконка главной."""
    sec = (meta or {}).get("section") or ""
    sub = ((meta or {}).get("sub") or "").split(" › ")[0]
    glyph = ICON_GLYPH.get("%s › %s" % (sec, sub)) or ICON_GLYPH.get(sec) or ICON_GLYPH[""]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="%s"/>'
           '<g transform="translate(2.5 2.5) scale(1.125)" fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round" '
           'stroke-linejoin="round">%s</g></svg>') % (ICON_COLOR.get(sec, ICON_COLOR["Входящие"]), glyph)
    return '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%s">' % quote(svg, safe=" =:/,;-.()")


def build_page(meta, h1, sub_html, intro_html, sections, summary, md_rel=None, script="", *, toc=False):
    css, eng = template_blocks()
    crumbs = esc(meta["section"]) + (" › " + esc(meta["sub"]) if meta.get("sub") else "")
    section_links = ['<a href="#%s">%s</a>' % (esc(sid), esc(t)) for _, t, sid, _ in sections]
    nav = "\n    ".join(section_links)
    pills = []
    if meta.get("level") is not None:
        pills.append('<span class="pill lvl">L%s</span>' % esc(str(meta["level"])))
    if meta.get("status"):
        pills.append('<span class="pill %s">%s</span>' % (STATUS_CLS.get(meta["status"], "st-draft"), esc(meta["status"])))
    if meta.get("version"):
        pills.append('<span class="pill ver">%s</span>' % esc(meta["version"]))
    if meta.get("parent_html"):
        pills.append('<span class="mi">выше: %s</span>' % meta["parent_html"])
    if meta.get("source"):
        srcs = [s.strip() for s in str(meta["source"]).split(",") if s.strip()]
        pills.append('<span class="mi">код: %s</span>' % ", ".join("<code>%s</code>" % esc(s) for s in srcs))
    if meta.get("aliases"):
        pills.append('<span class="mi">также: %s</span>' % esc(", ".join(meta["aliases"])))
    meta_html = '<div class="meta">%s</div>\n' % "".join(pills) if pills else ""
    secs = "\n\n".join('<section class="card" id="%s">\n<h2>%s</h2>\n%s\n</section>' % (esc(sid), th, body)
                       for th, _, sid, body in sections)
    if toc:
        items = "\n".join("<li>%s</li>" % link for link in section_links)
        secs = ('<section class="card" id="содержание">\n<h2>Содержание</h2>\n'
                '<ol class="toc">\n%s\n</ol>\n</section>\n\n' % items) + secs
    jmeta = {k: meta.get(k) for k in META_KEYS}
    if md_rel:
        jmeta["md"] = md_rel
    payload = json.dumps(jmeta, ensure_ascii=False).replace("</", "<\\/")
    source = ""
    if md_rel:
        md_link = (VAULT / md_rel).as_uri()
        source = 'источник: <a href="%s"><code>%s</code></a> · ' % (esc(md_link), esc(md_rel))
    return """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{icon}
<meta name="description" content="{desc}">
{themejs}
<script type="application/json" id="wiki-meta">{meta}</script>
<script type="application/json" id="wiki-tips">{{}}</script>
{css}
</head>
<body>
<div class="top"><div class="top-in">
  <a class="home" href="index.html" target="_top">База знаний</a>
  {search}
  <span class="crumbs">{crumbs}</span>
  <nav>
    {nav}
  </nav>
</div></div>

<div class="wrap">
<h1>{h1}</h1>
{sub}
{metah}{intro}
{secs}

<footer class="foot">{source}обновлено {updated}</footer>
</div>

{eng}
<script>
/* ---------- Схемы страницы: WIKI.flow("svg-…", {{…}}) / WIKI.seq(…) ---------- */
{script}
</script>
</body>
</html>
""".format(title=esc(meta["title"]), desc=esc(summary), meta=payload, css=css, crumbs=crumbs, nav=nav,
           h1=h1, sub=sub_html, metah=meta_html,
           intro=(('<div class="intro">\n%s\n</div>\n' if sections else '<section class="card">\n%s\n</section>\n')
                  % intro_html) if intro_html else "",
           secs=secs, source=source, updated=meta["updated"], eng=eng, script=script,
           themejs=THEME_JS, icon=favicon(jmeta), search=SEARCH_FORM)


def cmd_create(args):
    name = args.name.strip()
    if not name or not args.summary.strip() or any(not h.strip() for h in args.heading):
        raise SystemExit("укажи имя, первый абзац и заголовки разделов")
    page = HTML_DIR / page_file(name)
    if page == INDEX:
        raise SystemExit("это имя главной страницы")
    meta = {"title": name, "section": args.section, "sub": args.sub, "aliases": args.alias,
            "tags": args.tag, "term": "термин" in args.tag, "updated": today()}
    used_ids = {"содержание", "wiki-css", "wiki-engine", "wiki-meta", "wiki-tips"}
    sections = []
    for heading in args.heading:
        heading = heading.strip()
        sid = unique_id(slug(heading), used_ids)
        sections.append((esc(heading), heading, sid, ""))
    summary = args.summary.strip()
    content = build_page(meta, esc(name), '<p class="sub">%s</p>' % esc(summary), "",
                         sections, summary, toc=True)
    HTML_DIR.mkdir(exist_ok=True)
    try:
        # Публикуем полностью записанную страницу, в том числе при двух одновременных create.
        with tempfile.TemporaryDirectory(dir=HTML_DIR) as tmp:
            draft = Path(tmp) / page.name
            draft.write_text(content, encoding="utf-8")
            os.link(draft, page)
    except FileExistsError:
        raise SystemExit("уже есть %s" % page)
    print("создан %s" % page)
    return 0


def cmd_new(args):
    notes = all_notes()
    order = scst_order()
    HTML_DIR.mkdir(exist_ok=True)
    rc = 0
    for arg in args.paths:
        path = Path(arg)
        if not path.is_absolute():
            path = (Path.cwd() / path) if (Path.cwd() / path).exists() else VAULT / path
        path = path.resolve()
        if not path.is_file() or path.suffix != ".md" or not path.is_relative_to(VAULT):
            print("нет такой заметки: %s" % arg)
            rc = 1
            continue
        name = path.stem
        page = HTML_DIR / page_file(name)
        out = Path(args.out).resolve() if args.out else page
        if out == INDEX or (out.is_relative_to(VAULT) and out.suffix.lower() == ".md"):
            raise ValueError("выбери отдельный файл для HTML: %s" % out)
        if out.exists() and out.parent == HTML_DIR and out != page and block(read(out), "script", "wiki-meta"):
            raise ValueError("черновик затрёт другую страницу: %s" % out.name)
        if out == page and out.exists() and not args.force:
            print("уже есть %s — не перезаписываю (--force затрёт нарисованные схемы; "
                  "--out ФАЙЛ — черновик в другой файл)" % out.relative_to(PROJECT))
            rc = 1
            continue
        old = get_json(read(page), "wiki-meta", {}) if page.is_file() and page != INDEX else {}
        if not isinstance(old, dict):
            old = {}
        if out == page and page.exists() and old.get("title") != name:
            raise ValueError("файл %s принадлежит другой странице" % page.name)
        section, sub = section_for(name, path, args.section or old.get("section"),
                                   args.sub if args.sub is not None else (old.get("sub") if old.get("section") else None))
        if not section:
            print("%s: раздел неизвестен, укажи --section" % name)
            rc = 1
            continue
        text = read(path)
        fm, body = frontmatter(text)
        conv = Converter(notes)
        h1, chunks = split_body(body)
        intro_lines = chunks[0][1]
        rest = chunks[1:]
        if not "\n".join(intro_lines).strip() and rest and rest[0][0] == "Коротко":
            intro_lines, rest = rest[0][1], rest[1:]
        sub_html, intro_html = conv.render(intro_lines, sub=True)
        sections = []
        for title, lines in rest:
            sid = unique_id(slug(conv.label_text(conv.prep_line(title))), conv.used_ids)
            th = conv.inline(title)
            _, bhtml = conv.render(lines, chips=title.strip() in ("См. также", "См. ещё"))
            sections.append((th, re.sub(r"<[^>]+>", "", html.unescape(th)), sid, bhtml))
        summary = note_summary(path, conv.md)
        first = next((ln for ln in body.split("\n") if ln.strip()), "")
        rel = path.relative_to(VAULT).as_posix()
        meta = {
            "title": name, "aliases": fm.get("aliases") or [], "tags": fm.get("tags") or [],
            "term": bool(re.match(r"^\*\*.+?\*\*", first)) and len(text.encode("utf-8")) < TERM_MAX,
            "section": section, "sub": sub or "", "order": None, "level": None, "status": None,
            "parent": None, "source": None, "version": None, "md": rel, "updated": mdate(path),
        }
        if isinstance(meta["aliases"], str):
            meta["aliases"] = [meta["aliases"]]
        if isinstance(meta["tags"], str):
            meta["tags"] = [meta["tags"]]
        if section == "SCST":
            meta["order"] = order.get(name)
            for k in ("level", "status", "source", "version"):
                if fm.get(k) not in (None, ""):
                    meta[k] = fm[k]
            par = fm.get("parent")
            if par:
                pm = WL_RE.search(str(par))
                meta["parent"] = (pm.group(1).split("|")[0] if pm else str(par)).strip()
                meta["parent_html"] = conv.restore(conv.prep_line(str(par)))
        page = build_page(meta, esc(h1 or name), sub_html, intro_html, sections, summary, rel)
        write_page(out, page)
        todo = page.count('class="dia-todo"')
        shown = out.relative_to(PROJECT) if out.is_relative_to(PROJECT) else out
        print("создан %s%s" % (shown, " — схем перерисовать: %d" % todo if todo else ""))
    return rc


# ---------------------------------------------------------------- sync / links / index

def cmd_sync(args):
    css, eng = template_blocks()
    n = 0
    for p in list(all_pages().values()) + ([INDEX] if INDEX.exists() else []):
        s = read(p)
        t = re.sub(r'<style id="wiki-css">.*?</style>', lambda m: css, s, count=1, flags=re.S)
        if p != INDEX:
            t = re.sub(r'<script id="wiki-engine">.*?</script>', lambda m: eng, t, count=1, flags=re.S)
        t = re.sub(r'<script>(?:try\{)?var t=\(location\.search[^\n]*?</script>', lambda m: THEME_JS, t, count=1)
        t = re.sub(r'\n\s*<button class="tbtn"[^>]*>[^<]*</button>', "", t)
        icon = favicon(None if p == INDEX else get_json(t, "wiki-meta", {}))
        if ICON_RE.search(t):
            t = ICON_RE.sub(lambda m: icon, t, count=1)
        else:
            t = re.sub(r'(<title>.*?</title>)', lambda m: m.group(1) + "\n" + icon, t, count=1)
        if p != INDEX:
            t = t.replace('<a class="home" href="index.html">', '<a class="home" href="index.html" target="_top">', 1)
            t = re.sub(r'(<span class="crumbs">)(.*?)(</span>)',
                       lambda m: m.group(1) + re.sub(r"</?a\b[^>]*>", "", m.group(2)) + m.group(3), t, count=1)
            if SEARCH_RE.search(t):
                t = SEARCH_RE.sub(lambda m: SEARCH_FORM, t, count=1)
            else:
                t = t.replace('<a class="home" href="index.html" target="_top">База знаний</a>\n',
                              '<a class="home" href="index.html" target="_top">База знаний</a>\n  %s\n' % SEARCH_FORM, 1)
        if t != s:
            write_page(p, t, keep_mtime=True)
            n += 1
    print("обновлено страниц: %d" % n)
    return 0


def diagram_targets(source, renamed=None):
    """Имена в href узлов схем. При переименовании обновляем и саму схему."""
    names = set()

    def visit_href(match):
        try:
            target = json.loads(match[2])
        except ValueError:
            return match[0]
        name, sep, anchor = target.partition("#")
        current = renamed.get(name, name) if renamed else name
        names.add(current)
        if current == name:
            return match[0]
        value = json.dumps(current + sep + anchor, ensure_ascii=False).replace("<", r"\u003c")
        return match[1] + value

    def visit_script(match):
        return re.sub(r'(\bhref\s*:\s*)("(?:[^"\\]|\\.)*")', visit_href, match[0])

    return re.sub(r"<script>.*?</script>", visit_script, source, flags=re.S), names


def diagram_files(source):
    tips = get_json(source, "wiki-tips")
    if tips is None and not block(source, "script", "wiki-tips"):
        return {}
    if not isinstance(tips, dict) or not all(isinstance(v, str) for v in tips.values()):
        raise ValueError("wiki-tips должен сопоставлять имена заметок с именами файлов")
    return tips


def resolve_planned(source, pages):
    opening = re.compile(r'<span class="wl planned" data-target="([^"]*)"(?: title="[^"]*")?>')
    span_tag = re.compile(r'<(/?)span\b[^>]*>', re.I)
    pieces, end, fixed = [], 0, 0
    for match in opening.finditer(source):
        if match.start() < end:
            continue
        target = html.unescape(match[1]).strip()
        name, _, head = (target, "", "") if target in pages else target.partition("#")
        page = pages.get(name.strip())
        if page is None:
            pieces.extend((source[end:match.start()], '<span class="wl planned" data-target="%s">' % match[1]))
            end = match.end()
            continue
        depth = 1
        for tag in span_tag.finditer(source, match.end()):
            depth += -1 if tag[1] else 1
            if depth == 0:
                href = page.name + ("#" + head_slug(head) if head.strip() else "")
                pieces.extend((source[end:match.start()], '<a class="wl" href="%s">' % esc(href),
                               source[match.end():tag.start()], "</a>"))
                end = tag.end()
                fixed += 1
                break
    pieces.append(source[end:])
    return "".join(pieces), fixed


def cmd_links(args):
    pages = all_pages()
    files = by_file(pages)
    edited = set(args.edited or ())
    missing = edited.difference(pages)
    if missing:
        raise SystemExit("нет страниц: %s" % ", ".join(sorted(missing)))
    fixed = titles = broken = 0
    for name, p in pages.items():
        s = read(p)
        t, count = resolve_planned(s, pages)
        fixed += count

        def retitle(m):
            nonlocal titles, broken
            href = html.unescape(m.group(1))
            f = file_from_href(href)
            if f is None or f == "index.html":
                return m.group(0)
            if f not in files:
                broken += 1
                print("%s: ссылка на несуществующую страницу %s" % (name, href))
                return '<a class="wl" href="%s">' % m.group(1)
            new = '<a class="wl" href="%s">' % m.group(1)
            if new != m.group(0):
                titles += 1
            return new
        t = re.sub(r'<a class="wl" href="([^"]*)"(?: title="[^"]*")?>', retitle, t)
        # узлы схем с href: имя заметки → файл страницы (движок делает узел ссылкой, только если страница есть)
        _, names = diagram_targets(t)
        prior_tips = diagram_files(t)
        tips = {}
        for n in sorted(names):
            prior_file = prior_tips.get(n)
            if prior_file in files:
                tips[n] = prior_file
            elif n in pages:
                tips[n] = pages[n].name
        if block(t, "script", "wiki-tips") and get_json(t, "wiki-tips", {}) != tips:
            t = set_json(t, "wiki-tips", tips)
        original_meta = get_json(t, "wiki-meta", {}) or {}
        meta = original_meta.copy()
        if name in edited:
            if not meta.get("title"):
                raise SystemExit("%s: нет названия в wiki-meta" % p.name)
            # После прямой правки HTML прежний файл Obsidian остаётся в архиве.
            meta.pop("md", None)
            meta["updated"] = mdate(p)
            t = re.sub(r'(<footer class="foot">)источник: <a href="[^"]*\.md">.*?</a> · ',
                       r"\1", t, count=1, flags=re.S)
        elif not edited and VAULT is not None and meta.get("md"):
            mdp = VAULT / meta["md"]
            if mdp.is_file():
                meta["updated"] = mdate(mdp)
        if meta != original_meta:
            t = set_json(t, "wiki-meta", meta)
        if meta.get("updated") and (name in edited or not edited):
            t = re.sub(r'(<footer class="foot">.*?обновлено )\d{4}-\d\d-\d\d',
                       lambda m: m.group(1) + meta["updated"], t, count=1, flags=re.S)
        if t != s:
            write_page(p, t, keep_mtime=True)
    print("ожило ссылок: %d, обновлено ссылок: %d, битых: %d" % (fixed, titles, broken))
    return 1 if broken else 0


class TextOf:
    """Текст страницы для поиска: без скриптов, стилей, svg, исходников схем."""
    DROP = [r"<script\b.*?</script>", r"<style\b.*?</style>", r"<svg\b.*?</svg>",
            r'<figure class="dia-todo".*?</figure>', r'<div class="top">.*?</div></div>', r'<footer\b.*?</footer>',
            r'<span class="num">\d+</span>', r'<h1>.*?</h1>', r'<div class="meta">.*?</div>\n']

    @classmethod
    def get(cls, s):
        m = re.search(r'<div class="wrap">(.*)</div>', s, re.S)
        s = m.group(1) if m else s
        for d in cls.DROP:
            s = re.sub(d, " ", s, flags=re.S)
        s = re.sub(r"<(?:br|/p|/li|/h\d|/td|/th|/dt|/dd|/pre)\b[^>]*>", "\n", s)
        s = re.sub(r"<[^>]+>", " ", s)
        s = html.unescape(s)
        s = re.sub(r"[ \t ]+", " ", s)
        s = re.sub(r"\s*\n\s*", "\n", s).strip()
        return s


def page_meta(path, source):
    meta = get_json(source, "wiki-meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("title"), str) or not meta["title"].strip():
        raise ValueError("%s: нет названия в wiki-meta" % path.name)
    for key in ("section", "sub", "updated", "md"):
        if meta.get(key) is not None and not isinstance(meta[key], str):
            raise ValueError("%s: %s должно быть строкой" % (path.name, key))
    for key in ("aliases", "tags"):
        value = meta.get(key)
        if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
            raise ValueError("%s: %s должно быть списком строк" % (path.name, key))
    return meta


def index_entry(path, source, html_only=True):
    meta = page_meta(path, source)
    heads = []
    for card_id, level, attrs, title in re.findall(
            r'(?:<section\b[^>]*\bid="([^"]+)"[^>]*>\s*)?<h([23])\b([^>]*)>(.*?)</h\2>', source, re.S):
        anchor = re.search(r'\bid="([^"]+)"', attrs)
        if anchor or card_id:
            label = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", title))).strip()
            heads.append({"l": int(level), "id": anchor[1] if anchor else card_id, "t": label})
    mdp = VAULT / meta["md"] if not html_only and meta.get("md") else None
    return {
        "title": meta["title"], "file": path.name, "aliases": meta.get("aliases") or [],
        "tags": meta.get("tags") or [], "term": bool(meta.get("term")), "section": meta.get("section") or "",
        "sub": meta.get("sub") or "", "order": meta.get("order"), "level": meta.get("level"),
        "status": meta.get("status"), "summary": description(source), "heads": heads,
        "text": TextOf.get(source)[:TEXT_MAX], "updated": mdate(mdp) if mdp and mdp.exists() else meta.get("updated"),
    }


def cmd_index(args):
    data = [index_entry(path, read(path), args.html_only) for path in page_paths()]
    s = read(INDEX)
    t = set_json(s, "wiki-index", data)
    if s != t:
        write_page(INDEX, t)
    print("индекс: %d страниц, %d КБ" % (len(data), len(t.encode("utf-8")) // 1024))
    return 0


def chrome_bin():
    for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(b):
            return b
    return None


def render_page(path, chrome, screenshot=None, height=3000):
    prof = tempfile.mkdtemp(prefix="wiki-chrome-")
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--user-data-dir=" + prof, "--enable-logging=stderr", "--v=0", "--virtual-time-budget=3000"]
    png = Path(prof) / "page.png"
    if screenshot:
        cmd.extend(["--hide-scrollbars", "--window-size=1400,%d" % height, "--screenshot=" + str(png)])
    try:
        r = subprocess.run([*cmd, "--dump-dom", path.as_uri()], capture_output=True, text=True, timeout=90)
        if r.returncode or not r.stdout.strip():
            return None, ["Chrome не вернул страницу (код %d)" % r.returncode]
        if screenshot:
            if not png.is_file() or not png.stat().st_size:
                return None, ["Chrome не сохранил снимок"]
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png, screenshot)
    except subprocess.TimeoutExpired:
        return None, ["таймаут Chrome"]
    except OSError as e:
        return None, [str(e)]
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    errs = []
    for ln in r.stderr.splitlines():
        m = re.search(r':CONSOLE[^\]]*\] "(.*)", source:', ln)
        if m and (m.group(1).startswith("Uncaught") or m.group(1).startswith("WIKI")):
            errs.append("консоль: " + m.group(1))
    return r.stdout, errs


def cmd_check(args):
    errors, warns = [], []
    pages = all_pages()
    files = by_file(pages)
    only = "index" if args.only == "index.html" else args.only
    if only and only not in pages and only != "index":
        only = files.get(only, files.get(only + ".html", only))  # можно и имя файла страницы
    notes = {} if args.html_only else all_notes()

    def no_page(n):
        """Заметка без страницы: Встреча без Выжимки — «в работе» (Сессия идёт или ждёт Агента), иначе ошибка."""
        rel = notes[n].relative_to(VAULT)
        if is_lecture(notes[n]) and lecture_in_progress(notes[n]):
            warns.append("в работе: %s — Заметка встречи без Выжимки и страницы, Сессия идёт или ждёт Агента" % rel)
        else:
            errors.append("нет страницы для заметки: %s (html/%s)" % (rel, page_file(n)))
    if only and only not in pages and only != "index":
        if only in notes:
            no_page(only)
        else:
            errors.append("нет страницы заметки «%s» (html/%s)" % (only, page_file(only)))
    index = read(INDEX) if INDEX.exists() else ""
    sections = get_json(index, "wiki-sections", [])
    if not INDEX.exists():
        errors.append("нет html/index.html")
    if not isinstance(sections, list) or not all(isinstance(s, dict) and isinstance(s.get("t"), str)
                                                 for s in sections):
        errors.append("неверный список wiki-sections в index.html")
        sections = []
    sec_names = {s["t"]: s for s in sections}
    if not only:
        for n in notes:
            if n not in pages:
                no_page(n)
        same = {}
        for n in sorted(notes.keys() | pages.keys()):
            same.setdefault(page_file(n), []).append(n)
        for f, ns in same.items():
            if len(ns) > 1:
                errors.append("одно имя файла html/%s у заметок: %s" % (f, ", ".join(ns)))
        seen = {}
        for p in page_paths():
            seen.setdefault(page_title(p), []).append(p.name)
        for n, fs in seen.items():
            if len(fs) > 1:
                errors.append("у заметки «%s» несколько страниц: %s" % (n, ", ".join(fs)))
    titles, aliases = {}, {}
    ids_cache = {}

    def ids_of(name):
        if name not in ids_cache:
            p = pages.get(name)
            ids_cache[name] = set(re.findall(r'\bid="([^"]+)"', read(p))) if p and p.exists() else set()
        return ids_cache[name]
    todo_total = 0
    expected_index = []
    for name, p in pages.items():
        s = read(p)
        here = (not only) or only == name
        try:
            meta = page_meta(p, s)
        except ValueError as exc:
            if here or only == "index":
                errors.append(str(exc))
            continue
        if not only or only == "index":
            expected_index.append(index_entry(p, s, args.html_only))
        titles.setdefault(meta.get("title", "").lower(), []).append(name)
        for a in meta.get("aliases") or []:
            aliases.setdefault(a.lower(), []).append(name)
        if not here:
            continue
        try:
            for file in set(diagram_files(s).values()) - files.keys():
                errors.append("%s: схема ссылается на отсутствующий файл %s" % (name, file))
        except ValueError as exc:
            errors.append("%s: %s" % (name, exc))
        missing = [k for k in META_KEYS if k not in meta]
        if missing:
            errors.append("%s: в meta нет полей %s" % (name, ", ".join(missing)))
        if p.name != page_file(name):
            errors.append("%s: файл html/%s, а должен быть html/%s" % (name, p.name, page_file(name)))
        sec, sub = meta.get("section"), meta.get("sub") or ""
        if sec not in sec_names:
            errors.append("%s: раздела «%s» нет в wiki-sections" % (name, sec))
        elif sec != "SCST" and sub and sub not in (sec_names[sec].get("subs") or []):
            errors.append("%s: подраздела «%s › %s» нет в wiki-sections" % (name, sec, sub))
        if not block(s, "style", "wiki-css") or not re.search(r'<script id="wiki-engine">', s):
            errors.append("%s: нет wiki-css или wiki-engine" % name)
        mdp = VAULT / meta["md"] if not args.html_only and meta.get("md") else None
        if mdp and not mdp.is_file():
            errors.append("%s: md «%s» не найден" % (name, meta.get("md")))
        elif mdp and not only and name not in notes:
            errors.append("страница без заметки: html/%s («%s»)" % (p.name, name))
        elif mdp and p.stat().st_mtime < mdp.stat().st_mtime:
            warns.append("%s: устарела — md изменён после страницы" % name)
        todo = s.count('class="dia-todo"')
        if todo:
            todo_total += todo
            errors.append("%s: не перерисовано схем (dia-todo): %d" % (name, todo))
        if re.search(r'\bsrc="(?:https?:)?//', s) or re.search(r'<link[^>]+rel="stylesheet"', s) or "@import" in s:
            errors.append("%s: ссылка на внешний ресурс" % name)
        for href in re.findall(r'\bhref="([^"]+)"', s):
            href = html.unescape(href)
            if re.match(r"^[a-z]+:", href) or href.startswith("#") or href.startswith("../"):
                if href.startswith("#") and len(href) > 1 and unquote(href[1:]) not in ids_of(name):
                    warns.append("%s: якорь %s не найден на странице" % (name, href))
                continue
            f = file_from_href(href)
            if f is None or f == "index.html":
                continue
            if f not in files:
                errors.append("%s: битая ссылка %s" % (name, href))
            elif "#" in href:
                anc = unquote(href.split("#", 1)[1])
                if anc and anc not in ids_of(files[f]):
                    warns.append("%s: якорь %s не найден" % (name, href))
        for tgt in re.findall(r'<span class="wl planned" data-target="([^"]*)"', s):
            tn = html.unescape(tgt).split("#")[0].strip()
            if tn in pages:
                warns.append("%s: заметка «%s» уже есть — запусти links" % (name, tn))
    for k, v in titles.items():
        if len(v) > 1:
            errors.append("одинаковые заголовки: %s" % ", ".join(v))
    for k, v in aliases.items():
        own = set(v)
        clash = titles.get(k, [])
        if len(own) > 1 or (clash and set(clash) - own):
            if not only or only in own or only in clash:
                errors.append("алиас «%s» у нескольких заметок: %s" % (k, ", ".join(sorted(own | set(clash)))))
    if index and (not only or only == "index"):
        if get_json(index, "wiki-index") != expected_index:
            errors.append("данные поиска расходятся со страницами — запусти index")
    if args.render or args.screenshot:
        chrome = chrome_bin()
        if not chrome:
            errors.append("--render: не найден google-chrome")
        else:
            targets = [pages[only]] if only and only in pages else ([] if only else list(pages.values()))
            if (not only or only == "index") and INDEX.exists():
                targets.append(INDEX)
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                results = ex.map(lambda p: render_page(p, chrome, args.screenshot, args.height), targets)
                for p, (dom, errs) in zip(targets, results):
                    nm = files.get(p.name, p.stem)
                    errors.extend("%s: %s" % (nm, e) for e in errs)
                    if dom is None:
                        continue
                    for tag in re.findall(r'<svg\b[^>]*class="dia"[^>]*>', dom):
                        sid = (re.search(r'\bid="([^"]*)"', tag) or [None, "?"])[1]
                        de = re.search(r'\bdata-errors="([^"]*)"', tag)
                        if de:
                            errors.append("%s: схема %s: %s" % (nm, sid, html.unescape(de.group(1))))
                        dr = re.search(r'\bdata-rendered="(\d+)"', tag)
                        if not dr or dr.group(1) == "0":
                            errors.append("%s: схема %s не нарисована" % (nm, sid))
                    if p == INDEX and 'data-ready="1"' not in dom:
                        errors.append("index: скрипт индекса не отработал")
    for w in warns:
        print("предупреждение: " + w)
    for e in errors:
        print("ошибка: " + e)
    n = 1 if only else len(pages)
    note_count = len(notes.keys() | pages.keys())
    print("проверено страниц: %d, заметок: %d, ошибок: %d, предупреждений: %d, dia-todo: %d"
          % (n, note_count, len(errors), len(warns), todo_total))
    return 1 if errors else 0


def change_note(page, title=None):
    """Переименовать или удалить заметку вместе с её связями и поисковым индексом."""
    index = read(INDEX)
    entries = get_json(index, "wiki-index")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError("Не удалось прочитать индекс заметок.")
    old_title = page_meta(page, read(page))["title"]
    if not any(e.get("file") == page.name and e.get("title") == old_title for e in entries):
        raise ValueError("Индекс заметок устарел.")
    target = HTML_DIR / page_file(title) if title else None
    if target:
        if len(target.name) > 255:
            raise ValueError("Название слишком длинное.")
        if target != page and os.path.lexists(target):
            raise FileExistsError()
    if old_title == title:
        return page.name

    link = re.compile(r'(<a\b[^<>]*?\shref=)(["\'])(?:\./)?' + re.escape(page.name)
                      + r'([?#][^"\']*)?\2([^>]*>)(.*?)</a>', re.S | re.I)

    def replace_link(match):
        prefix, quote_char, suffix, attrs, label = match.groups()
        if target:
            return prefix + quote_char + target.name + (suffix or "") + quote_char + attrs + label + "</a>"
        anchor = unquote(html.unescape(suffix or "").partition("#")[2])
        name = old_title + ("#" + anchor if anchor else "")
        return '<span class="wl planned" data-target="%s">%s</span>' % (esc(name), label)

    updates = {}
    entries = []
    for path in page_paths():
        if path == page and not target:
            continue
        source = read(path)
        meta = page_meta(path, source)
        if title and path != page and any(title.casefold() == n.casefold()
                                          for n in [meta["title"], *(meta.get("aliases") or [])]):
            raise FileExistsError()
        revised = link.sub(replace_link, source)
        if target:
            revised, _ = resolve_planned(revised, {old_title: target, title: target})
        tips = diagram_files(revised)
        diagram_names = {k for k, v in tips.items() if v == page.name} | {old_title}
        revised, names = diagram_targets(revised, dict.fromkeys(diagram_names, title or old_title))
        new_tips = {k: v for k, v in tips.items() if v != page.name}
        if target and title in names:
            new_tips[title] = target.name
        if new_tips != tips and block(revised, "script", "wiki-tips"):
            revised = set_json(revised, "wiki-tips", new_tips)
        if title and meta.get("parent") == old_title:
            meta["parent"] = title
            revised = set_json(revised, "wiki-meta", meta)
        if path == page:
            meta.update(title=title, updated=today())
            meta.pop("md", None)
            revised = set_json(revised, "wiki-meta", meta)
            for tag in ("title", "h1"):
                revised, count = re.subn(r"(<" + tag + r"\b[^>]*>).*?(</" + tag + r">)",
                                         lambda m: m[1] + esc(title) + m[2], revised, count=1, flags=re.S)
                if not count:
                    raise ValueError("В странице отсутствует заголовок.")
            revised = re.sub(r'(<footer class="foot">)(?:источник: <a href="[^"]*\.md">.*?</a> · )?обновлено \d{4}-\d\d-\d\d',
                             lambda m: m[1] + "обновлено " + meta["updated"], revised, count=1, flags=re.S)
        output = target if path == page else path
        entries.append(index_entry(output, revised))
        if revised != source:
            updates[output] = revised
    updates[INDEX] = set_json(index, "wiki-index", sorted(entries, key=lambda e: e["file"]))

    # Сначала готовим все файлы. При ошибке публикации восстанавливаем прежние версии.
    with tempfile.TemporaryDirectory(dir=HTML_DIR) as tmp:
        staging = Path(tmp)
        backups = staging / "originals"
        backups.mkdir()
        for path, content in updates.items():
            stage_text(page if path == target else path, staging / path.name, content,
                       keep_mtime=path not in (target, INDEX))
            if path.exists():
                os.link(path, backups / path.name)
        published = []
        try:
            for path in updates:
                if path == target and path != page:
                    os.link(staging / path.name, path)
                else:
                    (staging / path.name).replace(path)
                published.append(path)
            if page != target:
                page.unlink()
        except OSError:
            for path in reversed(published):
                backup = backups / path.name
                if backup.exists():
                    backup.replace(path)
                else:
                    path.unlink()
            raise
    return target.name if target else None


class WikiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        path = self.path.split("?", 1)[0]
        if (path != "/" and not re.fullmatch(r"/[a-z0-9-]+\.html", path)
                or (HTML_DIR / (path.lstrip("/") or "index.html")).is_symlink()):
            self.send_error(404)
            return None
        return super().send_head()

    def requested_note(self):
        host = "127.0.0.1:%d" % self.server.server_port
        if self.headers.get("Host") != host or self.headers.get("Origin") != "http://" + host:
            self.send_error(403)
            return
        match = re.fullmatch(r"/notes/([a-z0-9-]+\.html)", self.path)
        if not match or match[1] == "index.html":
            self.send_error(404)
            return
        page = HTML_DIR / match[1]
        if not page.is_file() or page.is_symlink():
            self.send_error(404)
            return
        return page

    def do_PATCH(self):
        page = self.requested_note()
        if page is None:
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError()
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            title = data.get("title") if isinstance(data, dict) else None
            if not isinstance(title, str) or not title.strip():
                raise ValueError()
            title = title.strip()
            title.encode("utf-8")
            if any(ord(c) < 32 for c in title):
                raise ValueError()
            with wiki_write_lock():
                filename = change_note(page, title)
        except FileExistsError:
            self.send_error(409)
            return
        except FileNotFoundError:
            self.send_error(404)
            return
        except ValueError:
            self.send_error(400)
            return
        except OSError:
            self.send_error(500)
            return
        payload = json.dumps({"file": filename}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_DELETE(self):
        page = self.requested_note()
        if page is None:
            return
        try:
            with wiki_write_lock():
                change_note(page)
        except FileNotFoundError:
            self.send_error(404)
            return
        except ValueError:
            self.send_error(409)
            return
        except OSError:
            self.send_error(500)
            return
        self.send_response(204)
        self.end_headers()


def cmd_serve(args):
    with ThreadingHTTPServer((args.bind, args.port), WikiHandler) as server:
        print("Откройте http://127.0.0.1:%d/" % server.server_port, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_file(args):
    """Адрес страницы заметки — для ссылки в ответе: без %-кодирования."""
    rc = 0
    for arg in args.names:
        name = Path(arg).stem if arg.endswith(".md") else arg
        page = HTML_DIR / page_file(name)
        if not page.exists():
            print("нет страницы: %s (html/%s)" % (name, page.name), file=sys.stderr)
            rc = 1
        print(page.as_uri())
    return rc


def main():
    global VAULT
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--markdown-root", type=Path, metavar="КАТАЛОГ", help="архив Markdown для импорта и сверки")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("serve", help="локальная база с удалением заметок")
    a.add_argument("--bind", default="127.0.0.1", help="адрес прослушивания")
    a.add_argument("--port", type=int, default=8000)
    a = sp.add_parser("create", help="новая HTML-страница по имени")
    a.add_argument("name", help="имя заметки")
    a.add_argument("--section", required=True)
    a.add_argument("--sub", default="")
    a.add_argument("--summary", required=True, help="первый абзац обычным текстом")
    a.add_argument("--heading", action="append", required=True, help="заголовок карточки (можно повторять)")
    a.add_argument("--alias", action="append", default=[])
    a.add_argument("--tag", action="append", default=[])
    a = sp.add_parser("new", help="черновик страницы из md")
    a.add_argument("paths", nargs="+")
    a.add_argument("--section")
    a.add_argument("--sub")
    a.add_argument("--force", action="store_true")
    a.add_argument("--out", help="записать черновик в этот файл, а не в html/ (для точечного переноса правок)")
    a = sp.add_parser("index", help="данные поиска и дерева → html/index.html")
    a.add_argument("--html-only", action="store_true", help="брать даты из HTML-страниц")
    sp.add_parser("sync", help="wiki-css и wiki-engine из шаблона во все страницы")
    a = sp.add_parser("links", help="ожившие ссылки, адреса для узлов схем, даты")
    a.add_argument("--edited", nargs="+", metavar="ИМЯ", help="имена заметок, отредактированных прямо в HTML")
    a = sp.add_parser("check", help="проверка страниц")
    a.add_argument("--html-only", action="store_true", help="проверять HTML без сверки с Markdown")
    a.add_argument("--only")
    a.add_argument("--render", action="store_true")
    a.add_argument("--screenshot", type=Path, metavar="ФАЙЛ.png", help="снимок при рендере одной страницы")
    a.add_argument("--height", type=int, default=3000, help="высота снимка (по умолчанию 3000 пикселей)")
    a = sp.add_parser("file", help="адрес страницы заметки: file:///…/html/<файл>.html")
    a.add_argument("names", nargs="+", help="имя заметки или путь к md")
    args = ap.parse_args()
    if args.cmd == "new" and args.out and len(args.paths) != 1:
        ap.error("--out принимает ровно одну исходную заметку")
    if args.markdown_root:
        VAULT = args.markdown_root.expanduser().resolve()
        if not VAULT.is_dir():
            ap.error("каталог архива не найден: %s" % VAULT)
    elif args.cmd == "new":
        ap.error("для импорта укажи --markdown-root КАТАЛОГ перед new")
    elif args.cmd in ("index", "check"):
        args.html_only = True
    if args.cmd == "check" and args.screenshot:
        if not args.only or args.screenshot.suffix.lower() != ".png" or args.height < 1:
            ap.error("для снимка укажи --only ИМЯ, файл .png и положительную высоту")
    fn = {"serve": cmd_serve, "create": cmd_create, "new": cmd_new, "index": cmd_index, "sync": cmd_sync, "links": cmd_links, "check": cmd_check,
          "file": cmd_file}[args.cmd]
    try:
        if args.cmd in ("create", "new", "links", "sync", "index"):
            with wiki_write_lock():
                return fn(args)
        return fn(args)
    except (ValueError, OSError) as exc:
        ap.exit(1, "ошибка: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())
