import importlib.util
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


class WikiTest(WikiCase):
    def linked_notes(self):
        target = self.create("Цель")
        ref = self.create("Ссылка")
        link = '<a class="wl" href="tsel.html#пример"><span><em>подпись</em></span></a>'
        source = ref.read_text().replace("<h2>Пример</h2>", "<h2>Пример</h2>" + link)
        source = source.replace("/* ---------- Схемы страницы:", 'const node = {href: "Цель"};\n/* ---------- Схемы страницы:')
        ref.write_text(source)
        self.cli("links", "--edited", "Ссылка")
        self.cli("index")
        return target, ref

    def test_delete_and_recreate_linked_note(self):
        target, ref = self.linked_notes()
        with self.server() as base:
            self.assertEqual(self.request(base, "DELETE", target.name)[0], 204)
        self.assertFalse(target.exists())
        self.assertIn('data-target="Цель#пример"><span><em>подпись</em></span></span>', ref.read_text())
        self.assertEqual(wiki.get_json(ref.read_text(), "wiki-tips"), {})
        self.cli("check")
        self.create("Цель")
        self.cli("links", "--edited", "Цель")
        self.cli("index")
        self.assertIn('href="tsel.html#пример"><span><em>подпись</em></span></a>', ref.read_text())
        self.assertEqual(wiki.get_json(ref.read_text(), "wiki-tips"), {"Цель": target.name})
        self.cli("check")

    def test_rename_collision_and_concurrent_index(self):
        target, ref = self.linked_notes()
        with self.server() as base:
            self.assertEqual(self.request(base, "PATCH", target.name, "Ссылка")[0], 409)
            with ThreadPoolExecutor() as pool:
                rebuild = pool.submit(self.cli, "index")
                status, body = self.request(base, "PATCH", target.name, "Готово")
                rebuild.result()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["file"], "gotovo.html")
        self.assertFalse(target.exists())
        self.assertIn('href="gotovo.html#пример"><span><em>подпись</em></span></a>', ref.read_text())
        self.assertIn("gotovo.html", wiki.get_json(ref.read_text(), "wiki-tips").values())
        self.cli("links")
        self.cli("check")
        with self.server() as base:
            self.assertEqual(self.request(base, "DELETE", "gotovo.html")[0], 204)
        self.create("Готово")
        self.cli("links", "--edited", "Готово")
        self.assertEqual(wiki.get_json(ref.read_text(), "wiki-tips"), {"Готово": "gotovo.html"})

    def test_failed_publication_restores_every_file(self):
        target, _ = self.linked_notes()
        (self.html / "hardware").mkdir()
        before = {p: p.read_bytes() for p in self.html.rglob("*.html")}
        replace = Path.replace

        def interrupt(path, destination):
            if destination == self.index:
                raise OSError("Запись индекса прервана")
            return replace(path, destination)

        with patch.multiple(wiki, HTML_DIR=self.html, INDEX=self.index), patch.object(Path, "replace", interrupt):
            for change in ({"title": "Другое"}, {"folder": "hardware"}):
                with self.subTest(change=change), self.assertRaises(OSError):
                    wiki.change_note(target, **change)
                self.assertEqual(before, {p: p.read_bytes() for p in self.html.rglob("*.html")})

    def test_check_compares_search_data_and_keeps_good_index(self):
        target = self.create("Тема")
        self.cli("index")
        original = self.index.read_bytes()
        source = target.read_text().replace("<h2>Пример</h2>", "<h2>Сценарий</h2>")
        target.write_text(source)
        self.assertIn("данные поиска", self.cli("check", "--only", "index.html", ok=False))
        meta = wiki.get_json(source, "wiki-meta")
        meta["aliases"] = "алиас"
        target.write_text(wiki.set_json(source, "wiki-meta", meta))
        self.assertIn("списком строк", self.cli("index", ok=False))
        self.assertEqual(self.index.read_bytes(), original)
        target.unlink()
        self.assertIn("данные поиска", self.cli("check", ok=False))

    def test_import_protects_sources_index_and_multiple_drafts(self):
        notes = self.root / "notes"
        notes.mkdir()
        for name in ("index", "B"):
            (notes / (name + ".md")).write_text("# " + name + "\n\nТекст.\n")
        cmd = ["--markdown-root", str(notes), "new"]
        index_before = self.index.read_bytes()
        self.cli(*cmd, "index.md", "--section", "Входящие", "--force", ok=False)
        self.assertEqual(self.index.read_bytes(), index_before)
        draft = self.root / "draft.html"
        self.cli(*cmd, "index.md", "B.md", "--out", str(draft), ok=False)
        self.assertFalse(draft.exists())
        archive_before = (notes / "index.md").read_bytes()
        self.cli(*cmd, "B.md", "--out", str(notes / "index.md"), ok=False)
        self.assertEqual((notes / "index.md").read_bytes(), archive_before)
        live = self.create("Заметка")
        live_before = live.read_bytes()
        self.cli(*cmd, "B.md", "--out", str(live), ok=False)
        self.assertEqual(live_before, live.read_bytes())

    @unittest.skipUnless(importlib.util.find_spec("markdown_it"), "для импорта нужен markdown-it-py")
    def test_import_meeting_and_formatting(self):
        notes = self.root / "archive"
        meetings = notes / "lectures"
        meetings.mkdir(parents=True)
        (meetings / "Встреча.md").write_text(
            "---\naliases: [Созвон]\n---\n# Встреча\n\nОбсудили план.\n\n"
            "## Транскрипт\n\nСм. https://example.org.\n\n"
            "| Поле | Значение |\n|---|---|\n| Этап | Один |\n\n"
            "```text\n[[как в исходнике]]\n```\n")
        self.cli("--markdown-root", str(notes), "new", "lectures/Встреча.md")
        source = (self.html / "vstrecha.html").read_text()
        meta = wiki.get_json(source, "wiki-meta")
        self.assertEqual((meta["section"], meta["aliases"]), ("Встречи", ["Созвон"]))
        self.assertIn('href="https://example.org">https://example.org</a>.', source)
        self.assertIn('<div class="tbl"><table', source)
        self.assertIn("[[как в исходнике]]", source)
