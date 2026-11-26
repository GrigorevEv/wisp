# Wisp

Личная база знаний в автономных HTML-файлах. Заметки и поисковый индекс хранятся локально в `html/`.

После клонирования скопируйте шаблон главной страницы в `html/`.

```sh
mkdir -p html
test -e html/index.html || cp _templates/Индекс.html html/index.html
```

Для чтения и поиска откройте `html/index.html` в браузере.

## Локальный сервер

Запустите сервер из корня проекта, чтобы переименовывать и удалять заметки.

```sh
python3 scripts/wiki_html.py serve
```

Откройте адрес из терминала. Действия доступны через контекстное меню заметки. Для запуска в Docker используйте `docker compose up -d`. Данные хранятся в том же каталоге `html/`.

## После редактирования

Для страниц, изменённых вручную, передайте точные названия в `--edited`. Затем обновите поиск и проверьте базу.

```sh
python3 scripts/wiki_html.py links --edited "Название заметки"
python3 scripts/wiki_html.py index --html-only
python3 scripts/wiki_html.py check --html-only
```

Тесты запускаются командой `python3 -m unittest discover -s tests`. Для проверки отрисовки установите Chrome или Chromium и добавьте `--render` к команде `check`. Для импорта архива Markdown нужен пакет `markdown-it-py`.
