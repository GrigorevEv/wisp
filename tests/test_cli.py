import os
import re
from unittest.mock import patch
from urllib.parse import quote

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


class CommandTest(WikiCase):
    def test_new_database_starts_with_an_empty_search_index(self):
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-index"), [])
        self.assertIn("проверено страниц: 0, заметок: 0, ошибок: 0", self.cli("check", "--html-only"))

    def test_nested_index_and_empty_folder(self):
        (self.html / "hardware" / "cpu").mkdir(parents=True)
        (self.html / "_draft").mkdir()
        page = self.create("Шина")
        page.rename(self.html / "hardware" / page.name)
        self.cli("sync")
        self.cli("index")
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-folders"), ["hardware", "hardware/cpu"])
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-index")[0]["file"], "hardware/shina.html")
        first = self.index.read_bytes()
        self.cli("index")
        self.assertEqual(self.index.read_bytes(), first)
        self.cli("check")

    def test_folder_must_belong_to_the_database(self):
        outside = self.root / "архив"
        outside.mkdir()
        (self.html / "alias").symlink_to(outside, target_is_directory=True)
        for folder in ("alias/cpu", "../архив", str(outside), "_draft"):
            with self.subTest(folder=folder):
                self.cli("create", "Шина", "--folder", folder, "--section", "Железо",
                         "--summary", "Шина", "--heading", "Пример", ok=False)
        self.assertEqual(list(outside.iterdir()), [])
        self.cli("index")
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-folders"), [])

    def test_create_indexes_metadata_and_links_to_each_heading(self):
        title = "Ёж & щит"
        summary = 'Сравнение «А» < «Б» & "В"'
        self.cli("create", title, "--section", "Входящие", "--summary", summary,
                 "--heading", "Содержание", "--heading", "Этап <А>", "--heading", "Этап <А>",
                 "--alias", "Щит", "--tag", "термин", "--tag", "пример")
        page = self.html / "ezh-shchit.html"
        source = page.read_text()
        self.assertIn("<h1>Ёж &amp; щит</h1>", source)
        self.assertIn("<h2>Этап &lt;А&gt;</h2>", source)
        self.cli("index", "--html-only")
        entry, = wiki.get_json(self.index.read_text(), "wiki-index")
        self.assertEqual((entry["title"], entry["file"], entry["summary"]), (title, page.name, summary))
        self.assertEqual((entry["aliases"], entry["tags"], entry["term"]), (["Щит"], ["термин", "пример"], True))
        self.assertEqual([h["id"] for h in entry["heads"]], ["содержание", "содержание-2", "этап-а", "этап-а-2"])
        for heading in entry["heads"][1:]:
            self.assertIn('href="#%s"' % heading["id"], source)
        self.cli("check")

    def test_create_refuses_empty_fields_reserved_names_and_collisions(self):
        page = self.create("Ёж")
        before = {p.name: p.read_bytes() for p in (self.index, page)}
        cases = [
            (" ", "Текст", "Раздел", "укажи имя"),
            ("Один", " ", "Раздел", "первый абзац"),
            ("Один", "Текст", " ", "заголовки"),
            ("Index", "Текст", "Раздел", "имя главной страницы"),
            ("Еж", "Текст", "Раздел", "уже есть"),
        ]
        for name, summary, heading, message in cases:
            with self.subTest(name=name, summary=summary, heading=heading):
                output = self.cli("create", name, "--section", "Входящие", "--summary", summary,
                                  "--heading", heading, ok=False)
                self.assertIn(message, output)
                self.assertEqual({p.name: p.read_bytes() for p in self.html.glob("*.html")}, before)

    def test_file_reports_existing_pages_and_missing_names_in_one_call(self):
        page = self.create("Ёж")
        output = self.cli("file", "архив/Ёж.md", "Ещё нет", "Ёж", ok=False)
        self.assertEqual(output.splitlines().count(page.as_uri()), 2)
        self.assertIn("нет страницы: Ещё нет (html/eshche-net.html)", output)
        self.assertEqual(self.cli("file", "Ёж").strip(), page.as_uri())

    def test_index_extracts_visible_text_and_uses_the_headings_own_anchor(self):
        page = self.create("Разбор")
        content = ('<section id="карточка"><h2 id="разбор">Разбор <em>&amp; вывод</em></h2>'
                   '<h3 id="пример-кода">Пример   кода</h3><p>До&nbsp;&amp;&nbsp;после<br>строкой ниже</p>'
                   '<svg><text>Скрытая схема</text></svg><script>Скрытый скрипт</script></section>')
        page.write_text(page.read_text().replace('<footer class="foot">', content + '<footer class="foot">'))
        self.cli("index")
        entry, = wiki.get_json(self.index.read_text(), "wiki-index")
        self.assertEqual(entry["heads"][-2:], [{"l": 2, "id": "разбор", "t": "Разбор & вывод"},
                                               {"l": 3, "id": "пример-кода", "t": "Пример кода"}])
        self.assertIn("До & после\nстрокой ниже", entry["text"])
        for hidden in ("Скрытая схема", "Скрытый скрипт", "обновлено"):
            self.assertNotIn(hidden, entry["text"])

    def test_index_refuses_to_replace_an_incomplete_index_page(self):
        self.create("Тема")
        self.index.write_text('<title>База</title><p>Пользовательский текст</p>')
        before = self.index.read_bytes()
        self.assertIn("нет блока wiki-index", self.cli("index", ok=False))
        self.assertEqual(self.index.read_bytes(), before)

    def test_render_reports_missing_browser_and_validates_screenshot_arguments(self):
        with patch.dict(os.environ, {"PATH": ""}):
            self.assertIn("не найден google-chrome", self.cli("check", "--only", "index", "--render", ok=False))
        for options in ((), ("--only", "index", "--height", "0")):
            with self.subTest(options=options):
                shot = self.root / "index.png"
                self.assertIn("для снимка укажи", self.cli("check", "--screenshot", shot, *options, ok=False))
                self.assertFalse(shot.exists())

    def test_sync_refreshes_shared_blocks_once_and_preserves_note_mtime(self):
        page = self.create("Справка")
        source = page.read_text().replace(wiki.SEARCH_FORM, "").replace(' target="_top">База знаний', ">База знаний")
        page.write_text(source)
        os.utime(page, (946728000, 946728000))
        stamp = page.stat().st_mtime_ns
        meta = wiki.get_json(source, "wiki-meta")
        css = '<style id="wiki-css">body { color: navy }</style>'
        engine = '<script id="wiki-engine">window.version = 2</script>'
        template = self.root / wiki.TEMPLATE.relative_to(wiki.PROJECT)
        template.write_text(css + engine)
        index_engine = '<script id="wiki-engine">window.indexVersion = 1</script>'
        self.index.write_text(self.index.read_text().replace("</style>", "</style>" + index_engine, 1))
        self.cli("sync")
        revised = page.read_text()
        self.assertIn(css, revised)
        self.assertIn(engine, revised)
        self.assertEqual(revised.count(wiki.SEARCH_FORM), 1)
        self.assertIn("<h2>Пример</h2>", revised)
        self.assertEqual(wiki.get_json(revised, "wiki-meta"), meta)
        self.assertEqual(page.stat().st_mtime_ns, stamp)
        self.assertIn(css, self.index.read_text())
        self.assertIn(index_engine, self.index.read_text())
        self.assertIn("обновлено страниц: 0", self.cli("sync"))

    def test_sync_adds_sidebar_to_existing_index_and_preserves_local_data(self):
        self.create("Памятка")
        self.cli("index")
        source = self.index.read_text().replace("<title>База знаний</title>", "<title>Моя база</title>")
        source = source.replace('<script id="wiki-navigation">', '<script>').replace(
            'function treeRows(pages)', 'function oldTreeRows(pages)')
        old_menu = '<div id="note-menu" popover><button id="menu-rename">Переименовать</button></div>'
        source = re.sub(r'<div id="note-menu" popover>.*?</div>', old_menu, source, flags=re.S)
        blocks = (("style", "wiki-sidebar-css"), ("script", "wiki-sidebar"))
        for tag, ident in blocks:
            existing = wiki.block(source, tag, ident)
            if existing:
                source = source.replace(existing[0], "")
        self.index.write_text(source)
        self.cli("sync")
        updated = self.index.read_text()
        for tag, ident in blocks:
            self.assertIsNotNone(wiki.block(updated, tag, ident), ident)
        self.assertIn("<title>Моя база</title>", updated)
        self.assertEqual(updated.count('id="menu-move"'), 1)
        self.assertEqual(updated.count("function treeRows(pages)"), 1)
        self.assertNotIn("function oldTreeRows", updated)
        for ident in ("wiki-index", "wiki-sections"):
            self.assertEqual(wiki.block(source, "script", ident)[0], wiki.block(updated, "script", ident)[0])
        self.assertIn("обновлено страниц: 0", self.cli("sync"))

    def test_links_resolves_exact_title_with_hash_and_keeps_missing_targets(self):
        target = self.create("C#")
        page = self.create("Обзор")
        links = ('<span class="wl planned" data-target="C#"><span>Язык</span></span>'
                 '<span class="wl planned" data-target="Потом" title="устарело">на очереди</span>'
                 '<a class="wl" href="missing.html">нет файла</a>')
        page.write_text(page.read_text().replace("</h1>", "</h1>" + links))
        before = page.read_bytes()
        self.assertIn("нет страниц", self.cli("links", "--edited", "Нет страницы", ok=False))
        self.assertEqual(page.read_bytes(), before)
        self.assertIn("битых: 1", self.cli("links", ok=False))
        result = page.read_text()
        self.assertIn('<a class="wl" href="%s"><span>Язык</span></a>' % target.name, result)
        self.assertIn('<span class="wl planned" data-target="Потом">на очереди</span>', result)
        self.assertIn('<a class="wl" href="missing.html">нет файла</a>', result)

    def test_check_distinguishes_missing_anchors_from_missing_pages(self):
        target = self.create("Цель")
        page = self.create("Обзор")
        valid = target.name + "#" + quote("пример")
        links = ('<a href="%s">есть</a><a href="tsel.html#позже">потом</a>'
                 '<a href="#другое">здесь</a>') % valid
        page.write_text(page.read_text().replace("</h1>", "</h1>" + links))
        warnings = self.cli("check", "--only", page.stem)
        self.assertIn("якорь tsel.html#позже не найден", warnings)
        self.assertIn("якорь #другое не найден", warnings)
        self.assertNotIn(valid, warnings)
        page.write_text(page.read_text().replace(valid, "missing.html"))
        self.assertIn("битая ссылка missing.html", self.cli("check", "--only", page.name, ok=False))
        self.cli("check", "--only", "Цель")

    def test_check_finds_shared_aliases_and_aliases_matching_another_title(self):
        self.create("Ёж", "--alias", "Первый", "--alias", "Рысь")
        self.create("Рысь", "--alias", "ПЕРВЫЙ")
        self.create("Барс")
        self.cli("index")
        output = self.cli("check", ok=False)
        self.assertIn("алиас «первый» у нескольких заметок", output)
        self.assertIn("алиас «рысь» у нескольких заметок", output)
        self.cli("check", "--only", "Барс")

    def test_check_diagnoses_invalid_sections_and_diagram_data(self):
        page = self.create("Обзор")
        source = page.read_text()
        meta = wiki.get_json(source, "wiki-meta")
        for block, value, message in (
            ("wiki-meta", dict(meta, section="Нет"), "раздела «Нет» нет"),
            ("wiki-meta", dict(meta, sub="Нет"), "подраздела «Входящие › Нет» нет"),
            ("wiki-meta", dict(meta, updated=42), "updated должно быть строкой"),
            ("wiki-tips", ["net.html"], "wiki-tips должен сопоставлять"),
            ("wiki-tips", {"Нет": "net.html"}, "схема ссылается на отсутствующий файл net.html"),
        ):
            with self.subTest(block=block, message=message):
                page.write_text(wiki.set_json(source, block, value))
                self.assertIn(message, self.cli("check", "--only", page.name, ok=False))
