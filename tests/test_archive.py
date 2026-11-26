import importlib.util
import os
import unittest
from datetime import datetime

from scripts import wiki_html as wiki
from tests.wiki_case import WikiCase


needs_markdown = unittest.skipUnless(importlib.util.find_spec("markdown_it"), "для импорта нужен markdown-it-py")


class ArchiveTest(WikiCase):
    def setUp(self):
        super().setUp()
        self.archive = self.root / "archive"
        self.archive.mkdir()

    def note(self, relative, text):
        path = self.archive / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def archive_cli(self, *args, ok=True):
        return self.cli("--markdown-root", str(self.archive), *args, ok=ok)

    def test_check_distinguishes_unfinished_meetings_from_missing_pages(self):
        self.note("lectures/Созвон.md", "---\ntags: [встреча]\n---\n\n## Транскрипт\nЗапись.\n")
        self.note("_templates/Заметка.md", "# Шаблон")
        self.note(".trash/Удалено.md", "Архив")
        output = self.archive_cli("check")
        self.assertIn("в работе: lectures/Созвон.md", output)
        self.assertIn("ошибок: 0, предупреждений: 1", output)
        self.note("lectures/Созвон.md", "Обсудили план.\n\n## Транскрипт\nЗапись.\n")
        self.assertIn("нет страницы для заметки: lectures/Созвон.md",
                      self.archive_cli("check", "--only", "Созвон", ok=False))
        self.assertIn("заметок: 0", self.archive_cli("check", "--html-only"))

    def test_import_rejects_missing_archive_unknown_section_and_duplicate_names(self):
        self.note("Первая/Обзор.md", "Текст")
        self.assertIn("укажи --markdown-root", self.cli("new", "Обзор.md", ok=False))
        self.assertIn("каталог архива не найден", self.cli("--markdown-root", str(self.root / "нет"),
                                                           "new", "Обзор.md", ok=False))
        self.assertIn("нет такой заметки", self.archive_cli("new", "Нет.md", ok=False))
        self.assertIn("раздел неизвестен", self.archive_cli("new", "Первая/Обзор.md", ok=False))
        self.note("Другая/Обзор.md", "Другой текст")
        self.assertIn("одно имя у двух заметок", self.archive_cli("new", "Первая/Обзор.md", ok=False))
        self.assertEqual(list(self.html.glob("*.html")), [self.index])

    @needs_markdown
    def test_import_marks_diagrams_for_redrawing_and_excludes_their_source_from_search(self):
        self.note("RPM.md", "**RPM** управляет пакетами.\n\n~~~mermaid\ngraph LR\n  A --> B\n~~~\n")
        self.index.write_text(wiki.set_json(self.index.read_text(), "wiki-sections",
                                            [{"t": "Linux", "subs": ["Пакеты"]}]))
        self.archive_cli("new", "RPM.md")
        page = self.html / "rpm.html"
        self.assertIn('<figure class="dia-todo" data-lang="mermaid">', page.read_text())
        self.archive_cli("index")
        entry, = wiki.get_json(self.index.read_text(), "wiki-index")
        self.assertEqual((entry["section"], entry["sub"], entry["term"]), ("Linux", "Пакеты", True))
        self.assertIn("управляет пакетами", entry["text"])
        self.assertNotIn("graph LR", entry["text"])
        self.assertIn("не перерисовано схем (dia-todo): 1", self.archive_cli("check", ok=False))

    @needs_markdown
    def test_import_preserves_rich_text_and_resolves_links_outside_code(self):
        self.note("Цель.md", "# Цель\n")
        self.note("Памятка.md", """# Памятка

## Коротко
Перейти в [[Цель#Первый#Второй|справочник]].

## Содержание
Устаревшее оглавление

## Проверка
> [!tip]
> Порядок действий

- [x] Готово
- [ ] Проверить

[[#Проверка|сюда]] и `[[Только код]]`. ![[Вид.base]]

~~~~text
## Внутри кода
~~~
[[Только код]]
~~~~

## См. также
- [[Цель]]
- [[План|потом]]
""")
        self.archive_cli("new", "Памятка.md", "--section", "Входящие")
        source = (self.html / "pamyatka.html").read_text()
        self.assertEqual(wiki.description(source), "Перейти в справочник.")
        self.assertIn('<a class="wl" href="tsel.html#первый--второй">справочник</a>', source)
        self.assertIn('<a href="#проверка">сюда</a>', source)
        self.assertIn('<span class="wl planned" data-target="План">потом</span>', source)
        self.assertIn('<ul class="chips">', source)
        self.assertIn('<b class="h">Совет</b>', source)
        self.assertIn('<li class="task done">', source)
        self.assertIn('<li class="task">', source)
        self.assertIn('<code>[[Только код]]</code>', source)
        self.assertIn("## Внутри кода\n~~~\n[[Только код]]", source)
        self.assertNotIn("Вид.base", source)
        self.assertNotIn("Устаревшее оглавление", source)

    @needs_markdown
    def test_scst_import_reads_order_parent_and_nested_section(self):
        self.note("SCST/SCST.md", "## Порядок чтения\n- [[Глава|первая]]\n- [[Глава]]\n## Ещё\n")
        self.note("SCST/Ядро/Очереди/Глава.md",
                  '---\naliases: ["Раз, два", Три]\ntags: глава\nlevel: 2\nstatus: "понятно"\n'
                  'parent: "[[SCST]]"\nsource: a.c, b.c\nversion: "3.9"\n---\n# Начало\n\nТекст.\n')
        self.archive_cli("new", "SCST/Ядро/Очереди/Глава.md")
        source = (self.html / "glava.html").read_text()
        meta = wiki.get_json(source, "wiki-meta")
        self.assertEqual((meta["section"], meta["sub"], meta["order"]), ("SCST", "Ядро › Очереди", 1))
        self.assertEqual((meta["aliases"], meta["tags"]), (["Раз, два", "Три"], ["глава"]))
        self.assertEqual((meta["parent"], meta["level"], meta["version"], meta["status"]), ("SCST", 2, "3.9", "понятно"))
        self.assertIn('<a class="wl" href="scst.html">SCST</a>', source)
        self.assertIn("<code>a.c</code>, <code>b.c</code>", source)

    @needs_markdown
    def test_draft_and_force_preserve_source_and_section(self):
        note = self.note("Обзор.md", "Первая версия.\n")
        self.archive_cli("new", note, "--section", "Входящие")
        live = self.html / "obzor.html"
        before = live.read_bytes()
        note.write_text("Вторая версия.\n")
        self.assertIn("уже есть", self.archive_cli("new", note, ok=False))
        draft = self.root / "draft.html"
        self.archive_cli("new", note, "--out", draft)
        self.assertEqual(wiki.description(draft.read_text()), "Вторая версия.")
        self.assertEqual(live.read_bytes(), before)
        self.archive_cli("new", note, "--force")
        self.assertEqual(wiki.description(live.read_text()), "Вторая версия.")
        self.assertEqual(wiki.get_json(live.read_text(), "wiki-meta")["section"], "Входящие")
        self.assertEqual(note.read_text(), "Вторая версия.\n")

    @needs_markdown
    def test_edited_html_takes_over_dates_from_markdown(self):
        note = self.note("Тема.md", "Первая версия.\n")
        first, second, third = [datetime(2001, 1, day, 12).timestamp() for day in (1, 2, 3)]
        os.utime(note, (first, first))
        self.archive_cli("new", note, "--section", "Входящие")
        page = self.html / "tema.html"
        os.utime(note, (second, second))
        self.archive_cli("index")
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-index")[0]["updated"], "2001-01-02")
        self.archive_cli("index", "--html-only")
        self.assertEqual(wiki.get_json(self.index.read_text(), "wiki-index")[0]["updated"], "2001-01-01")
        self.archive_cli("links")
        self.assertIn("источник: <a", page.read_text())
        self.assertIn("обновлено 2001-01-02", page.read_text())
        os.utime(page, (third, third))
        self.archive_cli("links", "--edited", "Тема")
        self.archive_cli("links")
        meta = wiki.get_json(page.read_text(), "wiki-meta")
        self.assertNotIn("md", meta)
        self.assertNotIn("источник: <a", page.read_text())
        self.assertEqual(meta["updated"], "2001-01-03")
        self.assertIn("обновлено 2001-01-03", page.read_text())
