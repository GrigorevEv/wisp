# Wisp

Назначение — в issue `#1` «Первая версия Wisp». Язык проекта — `GLOSSARY.md`; решения — `docs/adr/`; требования — `openspec/specs/`. Всё это появляется по ходу первой версии (`docs/agents/workflow.md`, раздел «Первая версия»).

## Соглашения

- Стек ещё не выбран: он будет в ADR, а соглашения кода (каталоги, команды, тесты, имена в коде по словарю) — здесь, вместе с первым срезом.
- Тексты интерфейса и документация — по-русски; сообщения коммитов — по-английски, в повелительном наклонении: `Add…`, `Fix…` (`/commit` в проекте — `.claude/skills/commit/`).
- Артефакты изменения (снимки, прототипы, журналы проверок) — `.scratch/<имя изменения OpenSpec>/artifacts/<NN>-<slug>/`, живут до архивации изменения; исследовательские заметки для репозитория — `docs/research/`; скрипты, которые нужны и после влития, — `scripts/`.

## Agent skills

### Порядок работы

Issue → `/grill-with-docs` → `/opsx:propose` → `/opsx:apply` → PR → merge; маршрут зависит от типа issue. См. `docs/agents/workflow.md`.

### Issue tracker

GitHub Issues через `gh` (`/ghub`), без MCP. Тип — одна метка из `bug`, `enhancement`, `chore`, `documentation`; тело — по шаблону из `.github/ISSUE_TEMPLATE/`, без спеки; `proposal.md` ссылается на issue строкой `Issue: #N`, PR закрывает его `Fixes #N`. См. `docs/agents/issue-tracker.md`.

### OpenSpec

Работа ведётся через OpenSpec (`openspec/config.yaml`). Текущие требования — `openspec/specs/<capability>/spec.md`; любое изменение поведения — изменение в `openspec/changes/<change>/` (`proposal.md`, `design.md`, `tasks.md`, дельты спек): `/opsx:propose` → `/opsx:apply` → `/opsx:archive`. Задачи, замеры и прототипы — пункты `tasks.md`, а не отдельные файлы в `.scratch/`.

### Domain docs

Один `GLOSSARY.md` и `docs/adr/` в корне. Термины — только из словаря, без синонимов. Если решение противоречит ADR, скажи об этом прямо, а не обходи молча.
