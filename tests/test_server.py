import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


class ServerTest(WikiCase):
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
