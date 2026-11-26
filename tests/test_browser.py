import asyncio
import json
import os
import re
import subprocess
import tempfile
import unittest
from urllib.parse import quote

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


@unittest.skipUnless(wiki.chrome_bin(), "для проверки интерфейса нужен Chrome или Chromium")
class BrowserTest(WikiCase):
    def index_dom(self, query, *, profile=None):
        with tempfile.TemporaryDirectory() as fresh_profile:
            result = subprocess.run([wiki.chrome_bin(), "--headless=new", "--disable-gpu",
                                     "--disable-background-networking", "--user-data-dir=" + str(profile or fresh_profile),
                                     "--dump-dom", self.index.as_uri() + query],
                                    capture_output=True, text=True, timeout=30, check=True)
        self.assertIn('data-ready="1"', result.stdout)
        return result.stdout

    def sidebar(self, query):
        dom = self.index_dom(query)
        match = re.search(r"<aside\b[^>]*>(.*?)</aside>", dom, re.S)
        self.assertIsNotNone(match)
        return match[1]

    def test_sidebar_width_survives_collapse_and_reload(self):
        observe = """<script>
const side = document.getElementById("side"), toggle = document.getElementById("toggle");
const divider = side.querySelector('[role="separator"][aria-orientation="vertical"]');
if (divider) {
  const widths = [side.offsetWidth];
  if (!localStorage.getItem("test-adjusted")) {
    localStorage.setItem("test-adjusted", "1");
    divider.focus();
    document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
    widths.push(side.offsetWidth);
    toggle.click();
    widths.push(side.offsetWidth, divider.getClientRects().length);
    toggle.click();
    widths.push(side.offsetWidth);
  }
  document.body.dataset.widths = widths.join(",");
}
</script>"""
        self.index.write_text(self.index.read_text().replace("</body>", observe + "</body>"))
        profile = self.root / "профиль"
        first = self.index_dom("", profile=profile)
        widths = re.search(r'data-widths="([0-9,]+)"', first)
        self.assertIsNotNone(widths, "нет вертикального разделителя списка")
        initial, adjusted, collapsed, visible_dividers, reopened = map(int, widths[1].split(","))
        self.assertEqual(initial, 320)
        self.assertGreater(adjusted, initial)
        self.assertEqual((collapsed, visible_dividers, reopened), (46, 0, adjusted))
        self.assertIn(f'data-widths="{adjusted}"', self.index_dom("", profile=profile))

    @unittest.skipUnless(os.name == "posix", "для канала ввода Chrome нужна POSIX-оболочка")
    def test_drag_sidebar_over_an_open_note(self):
        note = self.create("Проверка")
        self.cli("index")

        async def drag_over_note():
            # Chrome принимает команды DevTools через дескриптор 3 и отвечает через 4.
            chrome = await asyncio.create_subprocess_exec(
                "sh", "-c", 'exec "$@" 3<&0 4>&1 </dev/null >/dev/null', "chrome",
                wiki.chrome_bin(), "--headless=new", "--disable-gpu", "--remote-debugging-pipe",
                "--user-data-dir=" + str(self.root / "chrome"), "--window-size=1200,800",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            session = None

            async def command(method, **params):
                request = {"id": 1, "method": method, "params": params}
                if session:
                    request["sessionId"] = session
                chrome.stdin.write(json.dumps(request).encode() + b"\0")
                await chrome.stdin.drain()
                while True:
                    packet = await chrome.stdout.readuntil(b"\0")
                    reply = json.loads(packet[:-1])
                    if "id" in reply:
                        self.assertNotIn("error", reply)
                        return reply["result"]

            async def evaluate(expression):
                result = await command("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
                self.assertNotIn("exceptionDetails", result)
                return result["result"].get("value")

            try:
                target = await command("Target.createTarget", url="about:blank")
                attached = await command("Target.attachToTarget", targetId=target["targetId"], flatten=True)
                session = attached["sessionId"]
                await command("Page.navigate", url=self.index.as_uri() + "#p=" + note.name)
                await evaluate('document.readyState === "complete" || new Promise(r => window.addEventListener("load", () => r(true)))')
                width = 'document.getElementById("side").offsetWidth'
                start = await evaluate(width)
                self.assertEqual(await evaluate(f"document.elementFromPoint({start + 180}, 150).tagName"), "IFRAME")
                await command("Input.dispatchMouseEvent", type="mousePressed", x=start, y=150, button="left", buttons=1)
                for delta in (180, -40):
                    await command("Input.dispatchMouseEvent", type="mouseMoved", x=start + delta, y=150, button="left", buttons=1)
                    self.assertEqual(await evaluate(width), start + delta)
                await command("Input.dispatchMouseEvent", type="mouseReleased", x=start - 40, y=150, button="left", buttons=0)
                await command("Input.dispatchMouseEvent", type="mouseMoved", x=start + 180, y=150)
                self.assertEqual(await evaluate(width), start - 40)
                self.assertEqual(await evaluate(f"document.elementFromPoint({start + 180}, 150).tagName"), "IFRAME")
            finally:
                chrome.terminate()
                await chrome.wait()

        asyncio.run(asyncio.wait_for(drag_over_note(), timeout=30))

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
