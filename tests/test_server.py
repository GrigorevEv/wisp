import json
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


class ServerTest(WikiCase):
    def test_move_preserves_links_metadata_and_index(self):
        target = self.create("Шина")
        ref = self.create("Обзор")
        sibling = "hardware/Шины #1"
        for folder in ("hardware/cpu", sibling):
            (self.html / folder).mkdir(parents=True)
        for path, name, href in ((target, "Обзор", ref.name), (ref, "Шина", target.name)):
            link = f'<a class="wl" href="{href}?theme=dark#пример">{name}</a>'
            if path == ref:
                link += f'<a href="/{href}">Шина</a>'
            source = path.read_text().replace("</h1>", "</h1>" + link)
            path.write_text(source.replace("</body>", '<script>const node = {href: "' + name + '"};</script></body>'))
        self.cli("links")
        self.cli("index")
        meta = wiki.get_json(target.read_text(), "wiki-meta")
        stamp = target.stat().st_mtime_ns
        old = target
        with self.server() as base:
            for folder in ("hardware/cpu", sibling, ""):
                with self.subTest(folder=folder):
                    new = self.html / folder / target.name
                    key = new.relative_to(self.html).as_posix()
                    status, payload = self.request(base, "PATCH", quote(old.relative_to(self.html).as_posix()), folder=folder)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(payload), {"file": key})
                    self.assertFalse(old.exists())
                    self.assertEqual(new.stat().st_mtime_ns, stamp)
                    source = new.read_text()
                    self.assertEqual(wiki.get_json(source, "wiki-meta"), meta)
                    for path, name, dest in ((new, "Обзор", ref), (ref, "Шина", new)):
                        href = wiki.relative_href(dest, path.parent)
                        self.assertIn(f'href="{href}?theme=dark#пример"', path.read_text())
                        self.assertEqual(wiki.get_json(path.read_text(), "wiki-tips"), {name: href})
                    self.assertIn(f'href="/{quote(key)}"', ref.read_text())
                    home = wiki.relative_href(self.index, new.parent)
                    for attr in ("href", "action"):
                        self.assertIn(f'{attr}="{home}"', source)
                    entries = wiki.get_json(self.index.read_text(), "wiki-index")
                    self.assertEqual([e["file"] for e in entries if e["title"] == "Шина"], [key])
                    self.cli("check", "--html-only")
                    old = new

    def test_move_errors_and_current_folder_preserve_files(self):
        page = self.create("Шина")
        folder = self.html / "hardware"
        folder.mkdir()
        occupied = folder / page.name
        self.cli("index")
        occupied.write_text("Занято")
        before = [p.read_bytes() for p in (page, self.index, occupied)]
        with self.server() as base:
            for dest, expected in (("", 200), ("hardware", 409), ("missing", 404), ("../", 400), (False, 400)):
                with self.subTest(dest=dest):
                    self.assertEqual(self.request(base, "PATCH", page.name, folder=dest)[0], expected)
        self.assertEqual([p.read_bytes() for p in (page, self.index, occupied)], before)

    def test_serves_index_and_notes_with_query_strings(self):
        page = self.create("Заметка")
        self.cli("index")
        with self.server() as base:
            for path, expected in (("/?q=test", self.index), ("/zametka.html?theme=dark", page)):
                with self.subTest(path=path), urlopen(base + path, timeout=5) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.read(), expected.read_bytes())
            with urlopen(Request(base + "/zametka.html", method="HEAD"), timeout=5) as response:
                self.assertEqual(int(response.headers["Content-Length"]), page.stat().st_size)
                self.assertEqual(response.read(), b"")
            (self.html / "_draft.html").write_text("Черновик")
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/_draft.html", timeout=5)
            with error.exception as response:
                self.assertEqual(response.code, 404)

    def test_rejected_requests_preserve_the_note_and_index(self):
        page = self.create("Тема")
        self.create("Другая", "--alias", "Занято")
        self.cli("index")
        before = {p.name: p.read_bytes() for p in self.html.glob("*.html")}
        with self.server() as base:
            for title in (None, 17, "  ", "Два\nимени", "a" * 256):
                with self.subTest(title=title):
                    self.assertEqual(self.request(base, "PATCH", page.name, title)[0], 400)
            self.assertEqual(self.request(base, "PATCH", page.name, "занято")[0], 409)
            for method in ("PATCH", "DELETE"):
                with self.subTest(method=method):
                    self.assertEqual(self.request(base, method, page.name, origin=False)[0], 403)
                    self.assertEqual(self.request(base, method, "index.html")[0], 404)
                    self.assertEqual(self.request(base, method, "missing.html")[0], 404)
        self.assertEqual({p.name: p.read_bytes() for p in self.html.glob("*.html")}, before)

    def test_rename_using_same_filename_updates_parent_and_search(self):
        page = self.create("Еж")
        child = self.create("Потомок")
        for path, fields in ((page, {"md": "Еж.md"}), (child, {"parent": "Еж"})):
            source = path.read_text()
            meta = wiki.get_json(source, "wiki-meta")
            path.write_text(wiki.set_json(source, "wiki-meta", dict(meta, **fields)))
        self.cli("index")
        before = [p.read_bytes() for p in (page, child, self.index)]
        with self.server() as base:
            self.assertEqual(self.request(base, "PATCH", page.name, " Еж ")[0], 200)
            self.assertEqual([p.read_bytes() for p in (page, child, self.index)], before)
            status, payload = self.request(base, "PATCH", page.name, " Ёж ")
        self.assertEqual((status, json.loads(payload)), (200, {"file": page.name}))
        source = page.read_text()
        self.assertIn("<title>Ёж</title>", source)
        self.assertIn("<h1>Ёж</h1>", source)
        meta = wiki.get_json(source, "wiki-meta")
        self.assertEqual(meta["title"], "Ёж")
        self.assertNotIn("md", meta)
        self.assertEqual(wiki.get_json(child.read_text(), "wiki-meta")["parent"], "Ёж")
        entries = wiki.get_json(self.index.read_text(), "wiki-index")
        self.assertEqual({e["file"]: e["title"] for e in entries}, {page.name: "Ёж", child.name: "Потомок"})
        self.cli("check")

    def test_nested_links_survive_rename_and_delete(self):
        folder = "hardware/Шины #1"
        self.create("Шина", "--folder", folder)
        target = self.html / folder / "shina.html"
        self.assertIn('href="../../index.html"', target.read_text())
        self.assertIn('action="../../index.html"', target.read_text())
        self.assertIn(target.as_uri(), self.cli("file", "Шина"))
        root = self.create("Обзор")
        self.create("Карта", "--folder", "maps")
        neighbor = self.html / "maps/karta.html"
        pending = '<span class="wl planned" data-target="Шина#Пример">Шина</span>'
        for ref in (root, neighbor):
            ref.write_text(ref.read_text().replace("</h1>", "</h1>" + pending))
        self.cli("links")
        self.cli("index")
        self.cli("check")
        with self.server() as base:
            old = quote(folder + "/shina.html")
            with urlopen(base + "/" + old, timeout=5) as response:
                self.assertEqual(response.read(), target.read_bytes())
            self.assertEqual(self.request(base, "PATCH", old, "Index")[0], 409)
            status, payload = self.request(base, "PATCH", old, "Контроллер")
            self.assertEqual((status, json.loads(payload)), (200, {"file": folder + "/kontroller.html"}))
            new = quote(folder + "/kontroller.html")
            self.assertIn(f'href="{new}#пример"', root.read_text())
            self.assertIn(f'href="../{new}#пример"', neighbor.read_text())
            self.cli("check")
            self.assertEqual(self.request(base, "DELETE", new)[0], 204)
        self.assertIn('data-target="Контроллер#пример"', neighbor.read_text())
        self.assertIn(folder, wiki.get_json(self.index.read_text(), "wiki-folders"))
        self.cli("check")

    def test_delete_refuses_stale_or_unreadable_index(self):
        page = self.create("Тема")
        original = page.read_bytes()
        with self.server() as base:
            for invalid in ([], {"file": page.name}):
                with self.subTest(index=invalid):
                    self.index.write_text(wiki.set_json(self.index.read_text(), "wiki-index", invalid))
                    index_before = self.index.read_bytes()
                    self.assertEqual(self.request(base, "DELETE", page.name)[0], 409)
                    self.assertEqual(page.read_bytes(), original)
                    self.assertEqual(self.index.read_bytes(), index_before)
            self.cli("index")
            self.assertEqual(self.request(base, "DELETE", page.name)[0], 204)
        self.assertFalse(page.exists())
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-index"), [])
