import asyncio
import json
import os
import re
import subprocess
import tempfile
import unittest
from contextlib import asynccontextmanager
from urllib.parse import quote

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


@unittest.skipUnless(wiki.chrome_bin(), "для проверки интерфейса нужен Chrome или Chromium")
class BrowserTest(WikiCase):
    def index_dom(self, query, *, profile=None, base=None):
        with tempfile.TemporaryDirectory() as fresh_profile:
            result = subprocess.run([wiki.chrome_bin(), "--headless=new", "--disable-gpu",
                                     "--disable-background-networking", "--user-data-dir=" + str(profile or fresh_profile),
                                     "--virtual-time-budget=1500", "--dump-dom", (base or self.index.as_uri()) + query],
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

    @asynccontextmanager
    async def browser(self, address):
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
            await command("Page.navigate", url=address)
            await evaluate('document.readyState === "complete" || new Promise(r => window.addEventListener("load", () => r(true)))')
            yield command, evaluate
        finally:
            chrome.terminate()
            await chrome.wait()

    @unittest.skipUnless(os.name == "posix", "для канала ввода Chrome нужна POSIX-оболочка")
    def test_drag_sidebar_over_an_open_note(self):
        note = self.create("Проверка")
        self.cli("index")

        async def drag_over_note():
            async with self.browser(self.index.as_uri() + "#p=" + note.name) as (command, evaluate):
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
        asyncio.run(asyncio.wait_for(drag_over_note(), timeout=30))

    async def wait_for_file(self, evaluate, file):
        selector = json.dumps('#list a[data-file="' + file + '"]')
        for _ in range(100):
            if await evaluate(f"document.readyState === 'complete' && document.querySelectorAll({selector}).length === 1"):
                self.assertTrue((self.html / file).is_file())
                return
            await asyncio.sleep(.05)
        state = await evaluate("""[location.hash, document.activeElement.outerHTML.slice(0, 200),
            document.body.dataset.error, Array.from(document.querySelectorAll('#list a[data-file]'), a => a.dataset.file)]""")
        self.fail(f"В списке не появилась заметка {file}: {state}")

    @unittest.skipUnless(os.name == "posix", "для канала ввода Chrome нужна POSIX-оболочка")
    def test_move_from_search_menu_keeps_open_heading(self):
        note = self.create("Шина")
        folder = "hardware/Шины #1"
        destination = self.html / folder / note.name
        destination.parent.mkdir(parents=True)
        self.cli("index")
        anchor = quote("пример")
        query = "#q=" + quote("Шина") + "&p=" + quote(note.name + "#" + anchor)

        async def use_menu(base):
            async with self.browser(base + "/" + query) as (command, evaluate):
                async def folders():
                    await evaluate("""document.querySelector('#list a[data-file]').dispatchEvent(
                        new MouseEvent('contextmenu', {bubbles:true})); document.querySelector('#menu-move').click()""")
                    return await evaluate("Array.from(document.querySelectorAll('#move-folders button'), b => b.textContent)")

                self.assertEqual(await folders(), ["Корень базы", "hardware", folder])
                self.assertTrue(await evaluate("document.querySelector('#move-folders button').disabled"))
                await command("Input.dispatchKeyEvent", type="keyDown", key="Escape", windowsVirtualKeyCode=27)
                await command("Input.dispatchKeyEvent", type="keyUp", key="Escape", windowsVirtualKeyCode=27)
                self.assertFalse(await evaluate("document.querySelector('#note-menu').matches(':popover-open')"))
                await folders()
                dest_button = "document.querySelector('#move-folders button:last-child')"
                destination.write_bytes(note.read_bytes())
                await evaluate("window.alert = msg => document.body.dataset.error = msg")
                await evaluate(dest_button + ".click()")
                error = await evaluate("""new Promise(resolve => {
                    function check() { document.body.dataset.error ? resolve(document.body.dataset.error) : setTimeout(check, 20); }
                    check();
                })""")
                self.assertIn("уже есть", error)
                destination.unlink()
                await folders()
                await evaluate(dest_button + ".focus()")
                await command("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", text="\r", windowsVirtualKeyCode=13)
                await command("Input.dispatchKeyEvent", type="keyUp", key="Enter", windowsVirtualKeyCode=13)
                moved = folder + "/" + note.name
                await self.wait_for_file(evaluate, moved)
                self.assertFalse(note.exists())
                self.assertEqual(await evaluate("document.querySelector('#q').value"), "Шина")
                expected = quote(moved) + "#" + anchor
                self.assertEqual(await evaluate("new URLSearchParams(location.hash.slice(1)).get('p')"), expected)
                self.assertEqual(await evaluate("document.querySelector('#view').contentWindow.location.href"), base + "/" + expected)
                await folders()
                await evaluate("document.querySelector('#move-folders button').click()")
                await self.wait_for_file(evaluate, note.name)
                self.assertFalse(destination.exists())
        with self.server() as base:
            asyncio.run(asyncio.wait_for(use_menu(base), timeout=30))
        self.cli("check", "--html-only")

    @unittest.skipUnless(os.name == "posix", "для канала ввода Chrome нужна POSIX-оболочка")
    def test_drag_note_to_closed_empty_and_nested_folders(self):
        note = self.create("Шина")
        (self.html / "hardware").mkdir()
        (self.html / "parts/cpu").mkdir(parents=True)
        self.cli("index")

        async def drag(base):
            async with self.browser(base + "/") as (command, evaluate):
                async def drop(selector, source="#list a[data-file]"):
                    async def center(css):
                        return await evaluate("""(() => {
                            const rect = document.querySelector(%s).getBoundingClientRect();
                            return [rect.x + rect.width / 2, rect.y + rect.height / 2];
                        })()""" % json.dumps(css))
                    x, y = await center(source)
                    dest_x, dest_y = await center(selector)
                    await command("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", buttons=1)
                    for step in range(1, 11):
                        await command("Input.dispatchMouseEvent", type="mouseMoved", x=x + (dest_x - x) * step / 10,
                                      y=y + (dest_y - y) * step / 10, button="left", buttons=1)
                    await command("Input.dispatchMouseEvent", type="mouseMoved", x=dest_x, y=dest_y, button="left", buttons=1)
                    highlighted = await evaluate("!!document.querySelector('.drop-folder')")
                    await command("Input.dispatchMouseEvent", type="mouseReleased", x=dest_x, y=dest_y, button="left")
                    return highlighted

                self.assertFalse(await drop("#stats"))
                self.assertTrue(note.exists())
                self.assertTrue(await drop('details[data-folder="hardware"]:not([open]) > summary', "#recent a[data-file]"))
                await self.wait_for_file(evaluate, "hardware/" + note.name)
                self.assertFalse(note.exists())
                await evaluate("""document.querySelector('[data-folder="hardware"] > summary').click();
                    document.querySelector('[data-folder="parts"] > summary').click()""")
                self.assertTrue(await drop('[data-folder="parts/cpu"] > summary'))
                await self.wait_for_file(evaluate, "parts/cpu/" + note.name)
                self.assertFalse((self.html / "hardware" / note.name).exists())
                await evaluate("document.querySelector('#list a[data-file]').click()")
                self.assertEqual(await evaluate("document.title"), "Шина — База знаний")
        with self.server() as base:
            asyncio.run(asyncio.wait_for(drag(base), timeout=30))
        self.cli("check", "--html-only")

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
        self.assertEqual(re.findall(r'data-file="([^"]+)"', result), ["ezh-v-lesu.html", "atlas.html", "karty.html", "vstrecha.html"])
        self.assertIn("Найдено: 4", result)
        self.assertIn("<mark>Ёж</mark>", result)
        self.assertIn('href="karty.html#%s"' % quote("ёж-в-лесу"), result)

    def test_global_search_and_meeting_order(self):
        self.create("2026-01-10 План", section="Встречи")
        self.create("2026-02-03 План", section="Встречи")
        self.create("Памятка")
        self.cli("index")
        result = self.sidebar("#q=" + quote("план"))
        self.assertIn("Найдено: 2", result)
        self.assertEqual(re.findall(r'data-file="([^"]+)"', result), ["2026-01-10-plan.html", "2026-02-03-plan.html"])
        meetings = self.sidebar("#t=lectures")
        self.assertEqual(re.findall(r'data-file="([^"]+)"', meetings), ["2026-02-03-plan.html", "2026-01-10-plan.html"])

    def test_folders_before_notes_and_global_folder_search(self):
        (self.html / "hardware" / "cpu").mkdir(parents=True)
        self.create("Атлас")
        self.cli("index")
        listing = self.sidebar("")
        self.assertLess(listing.index("<summary>hardware</summary>"), listing.index(">Атлас</a>"))
        self.assertIn("<summary>cpu</summary>", listing)
        self.assertIn("hardware/cpu", self.sidebar("#t=work&q=cpu"))
        observe = """<script>
document.getElementById("q").dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
</script>"""
        self.index.write_text(self.index.read_text().replace("</body>", observe + "</body>"))
        listing = self.sidebar("#t=work&q=cpu")
        self.assertIn('data-folder="hardware/cpu" open', listing)
        self.assertIn('data-folder="hardware" open', listing)
        self.assertIn(">Атлас</a>", listing)

    def test_open_nested_result_and_follow_link_on_server(self):
        self.create("Шина", "--folder", "hardware/Шины", "--heading", "Арбитраж", section="Железо")
        root = self.create("Обзор")
        root.write_text(root.read_text().replace("</h1>", '</h1><span class="wl planned" data-target="Шина#Арбитраж">Шина</span>'))
        self.cli("links")
        self.cli("index")
        for query in ("арбитраж", "железо"):
            self.assertIn('data-file="hardware/Шины/shina.html"', self.sidebar("#t=work&q=" + quote(query)))
        address = quote("hardware/Шины/shina.html") + "#" + quote("арбитраж")
        bookmark = "#p=" + quote(address, safe="")
        self.assertIn('data-folder="hardware/Шины" open', self.sidebar(bookmark))
        observe = """<script>
window.addEventListener("load", function () {
  var frame = document.getElementById("view");
  if (frame.contentDocument.querySelector("h1")?.textContent === "Обзор") {
    frame.contentDocument.querySelector("a.wl").click();
  }
  setTimeout(function () {
    var h1 = frame.contentDocument.querySelector("h1");
    if (h1) document.body.dataset.opened = h1.textContent;
    document.body.dataset.bookmark = location.hash;
    var selected = document.querySelector("#list a.on");
    if (selected) selected.dispatchEvent(new MouseEvent("contextmenu", {bubbles: true}));
    document.body.dataset.menuFile = document.getElementById("note-menu").dataset.file || "";
  }, 200);
});
</script>"""
        self.index.write_text(self.index.read_text().replace("</body>", observe + "</body>"))
        with self.server() as base:
            for start in ("#p=obzor.html", bookmark):
                dom = self.index_dom(start, base=base + "/")
                self.assertIn('data-opened="Шина"', dom)
                self.assertIn('data-menu-file="hardware/Шины/shina.html"', dom)
                self.assertIn(f'data-bookmark="{bookmark}"', dom)

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
