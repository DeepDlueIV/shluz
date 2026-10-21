# Постоянный просмотр NoClip через Vercel

Один репозиторий `DeepDlueIV/shluz` достаточен: Vercel поддерживает приложения в подпапках. Вторую копию сайта и синхронизацию между репозиториями создавать не нужно. Здесь описана настройка; подключение к Vercel и публикация этой инструкцией сами по себе не выполняются.

## Настройки проекта

| Поле | Значение |
| --- | --- |
| Git repository | `DeepDlueIV/shluz` |
| Root Directory | `web` |
| Framework Preset | `Next.js` |
| Node.js | `24.x` |
| Install Command | Автоматически, по npm lockfile |
| Build Command | Автоматически: `npm run build` |
| Output Directory | Автоматически для Next.js; не задавать `out` |
| Environment Variable | `NEXT_PUBLIC_GATEWAY_ENABLED=false` в Production и Preview |

Не задавайте `NOCLIP_STATIC_EXPORT=1`: на Vercel используем обычный Next.js build. Для визуального просмотра настройки шлюза, SMTP, провайдеров и Turnstile не нужны. Флаг gateway встроен в клиентскую сборку; изменение env требует нового deployment.

## Как подключить текущую версию

Сайт пока находится в ветке `feat/noclip-homepage-v1`. В `main` каталога `web/` может ещё не быть. Выбор ветки в форме первого Import не следует считать гарантированным: документированный Git Import обычно начинает с default branch.

Для просмотра до объединения с `main`:

1. Создать проект Vercel через официальный Create Project API или `vercel project create`, отдельно от первого deployment. При использовании API указать существующий Git repository, `rootDirectory: "web"` и `framework: "nextjs"`.
2. Установить параметры из таблицы выше.
3. В Vercel открыть **Deployments → Create Deployment** и указать полное имя ветки `feat/noclip-homepage-v1`.
4. Взять фактический адрес ветки из результата deployment и сохранить его в закладки. Его не нужно угадывать по имени репозитория.

Эту разовую настройку можно выполнить через подключённый Vercel App. Если `web/` уже объединён с `main`, достаточно обычного **Add New → Project → Import shluz** и **Root Directory = web** перед Deploy.

Первый deployment нового проекта Vercel обозначает как Production независимо от исходной ветки. Это термин окружения Vercel; он не означает, что продукт готов к публичному запуску. После первой сборки обычные правила Production/Preview определяются выбранной Production Branch.

## Как видеть следующие изменения

Рабочий процесс: изменение кода → commit → push → автоматическая сборка Vercel → обновление сохранённого адреса.

- **Commit** сохраняет версию в истории локального Git. Сам по себе он не отправляет файлы на GitHub.
- **Push** отправляет коммиты и обновляет ветку GitHub. После подключения Git integration он запускает сборку Vercel.
- **Deploy** собирает код и размещает сайт по веб-адресу.

У конкретной сборки есть свой постоянный адрес, показывающий именно её. У Git-ветки есть другой, стабильный адрес, указывающий на последнюю сборку этой ветки. Для текущих правок нужен адрес **ветки**: после завершения сборки достаточно обновить открытую страницу. Автоматическое обновление открытой вкладки без refresh здесь не настраивается.

Обычно `main` — Production Branch, остальные ветки получают Preview. Если понадобится закрепить основной адрес отдельного проекта для просмотра за рабочей веткой, это меняется в **Settings → Environments → Production → Branch Tracking**. Default branch самого GitHub-репозитория при этом менять не требуется.

## Локальный запасной вариант

Скачать/обновить рабочую копию нужной ветки и дважды щёлкнуть `START-WEBSITE.cmd` в корне репозитория. Требуется Node.js 24+ с npm. Запускатор установит зависимости при необходимости, дождётся сервера и откроет браузер. Остановка — Ctrl+C в его окне.

Повторно устанавливать зависимости после каждой правки не требуется. Запущенный Next dev обновляет интерфейс при изменении локальных исходников; изменения, которые находятся только на GitHub, сначала должны попасть в локальную копию. Запускатор не делает автоматические pull, checkout или reset общего репозитория.

## Источники

- [Приложения в подпапках / monorepos](https://vercel.com/docs/monorepos)
- [Root Directory и параметры сборки](https://vercel.com/docs/builds/configure-a-build#root-directory)
- [Git integration и deployment из ветки](https://vercel.com/docs/git#creating-a-deployment-from-a-git-reference)
- [Создание проекта через API](https://vercel.com/docs/rest-api/projects/create-a-new-project)
- [Стабильные адреса веток](https://vercel.com/docs/deployments/generated-urls)
- [Настройка Production Branch](https://vercel.com/kb/guide/can-i-use-a-non-default-branch-for-production)
- [Первый deployment](https://vercel.com/docs/deployments/environments#first-deployment)
