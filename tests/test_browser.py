import re
import subprocess
import tempfile
import unittest
from urllib.parse import quote

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


@unittest.skipUnless(wiki.chrome_bin(), "для проверки интерфейса нужен Chrome или Chromium")
class BrowserTest(WikiCase):
    def index_dom(self, query):
        with tempfile.TemporaryDirectory() as profile:
            result = subprocess.run([wiki.chrome_bin(), "--headless=new", "--disable-gpu",
                                     "--disable-background-networking", "--user-data-dir=" + profile,
                                     "--dump-dom", self.index.as_uri() + query],
                                    capture_output=True, text=True, timeout=30, check=True)
        self.assertIn('data-ready="1"', result.stdout)
        return result.stdout

    def sidebar(self, query):
        dom = self.index_dom(query)
        match = re.search(r"<aside\b[^>]*>(.*?)</aside>", dom, re.S)
        self.assertIsNotNone(match)
        return match[1]

    def test_known_and_stale_bookmarks(self):
        self.create("Тема")
        self.cli("index")
        for bookmark, visible in (("tema.html%23пример", True), ("removed.html", False)):
            with self.subTest(bookmark=bookmark):
                dom = self.index_dom("#p=" + bookmark)
                frame = re.search(r'<iframe\b[^>]*\bid="view"[^>]*>', dom)
                self.assertIsNotNone(frame)
                self.assertEqual("hidden" not in frame[0], visible)

    def test_search_ranks_title_alias_and_heading_and_requires_all_words(self):
        self.create("Ёж в лесу")
        self.create("Атлас", "--alias", "Ёж в лесу")
        self.create("Карты", "--heading", "Ёж в лесу")
        self.create("Ёж")
        self.create("Встреча", "--tag", "Ёж", "--tag", "лес", section="Встречи")
        self.cli("index")
        result = self.sidebar("?q=" + quote("ЕЖ лес"))
        self.assertEqual(re.findall(r'data-file="([^"]+)"', result), ["ezh-v-lesu.html", "atlas.html", "karty.html"])
        self.assertIn("Найдено: 3 · во вкладке «Встречи» ещё 1", result)
        self.assertIn("<mark>Ёж</mark>", result)
        self.assertIn('href="karty.html#%s"' % quote("ёж-в-лесу"), result)

    def test_search_offers_other_tab_and_meetings_open_newest_first(self):
        self.create("2026-01-10 План", section="Встречи")
        self.create("2026-02-03 План", section="Встречи")
        self.create("Памятка")
        self.cli("index")
        result = self.sidebar("#q=" + quote("план"))
        self.assertIn("Во вкладке «Встречи» — 2", result)
        self.assertEqual(re.findall(r'data-file="([^"]+)"', result), [])
        meetings = self.sidebar("#t=lectures&q=" + quote("план"))
        self.assertEqual(re.findall(r'data-file="([^"]+)"', meetings), ["2026-02-03-plan.html", "2026-01-10-plan.html"])

    def test_render_validates_flow_and_sequence_and_writes_a_png(self):
        page = self.create("Схемы")
        figures = '<svg class="dia" id="flow"></svg><svg class="dia" id="seq"></svg>'
        source = page.read_text().replace("</h1>", "</h1>" + figures)
        page.write_text(source)
        report = self.cli("check", "--only", page.name, "--render", ok=False)
        self.assertIn("схема flow не нарисована", report)
        self.assertIn("схема seq не нарисована", report)
        draw = """<script>
WIKI.flow("flow", {vw: 300, vh: 180, nodes: {a: {k: "init", x: 100, y: 50},
  b: {k: "final", x: 100, y: 140}}, edges: [{f: "a", t: "b"}]});
WIKI.seq("seq", {parts: [{id: "a", t: "Один"}, {id: "b", t: "Два"}],
  msgs: [{f: "a", t: "b", lab: "Вопрос"}]});
</script>"""
        page.write_text(source.replace("</body>", draw + "</body>"))
        screenshot = self.root / "снимки" / "схемы.png"
        self.cli("check", "--only", page.name, "--screenshot", screenshot, "--height", "600")
        with screenshot.open("rb") as png:
            self.assertEqual(png.read(8), b"\x89PNG\r\n\x1a\n")
