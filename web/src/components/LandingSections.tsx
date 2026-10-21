"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import styles from "./LandingSections.module.css";
import presentation from "./Presentation.module.css";
import { useSitePreferences, type Translate } from "./SitePreferences";
import { modelRows, modelIcons } from "../lib/model-catalog";

type LandingSectionsProps = {
  onPrompt: (prompt: string) => void;
  onOpenChat: () => void;
};

type FeatureKind = "text" | "image" | "video" | "audio" | "code";

const getFeatures = (t: Translate): { kind: FeatureKind; label: string; title: string; description: string; prompt: string; action: string }[] => [
  { kind: "text", label: t("Text", "Текст"), title: t("Text chat", "Текстовый чат"),
    description: t("Ask questions, edit a draft or compare different explanations.", "Задавайте вопросы, редактируйте текст и сравнивайте объяснения."),
    prompt: t("Explain async and await in JavaScript with a small working example.", "Объясни async и await в JavaScript на небольшом рабочем примере."), action: t("Open chat", "Открыть чат") },
  { kind: "image", label: t("Images", "Изображения"), title: t("Image concepts", "Концепции изображений"),
    description: t("Describe a composition, lighting and style. Prepare a prompt for image generation.", "Опишите композицию, освещение и стиль. Подготовьте промпт для генерации изображения."),
    prompt: t("Write three image prompts for a sculpture made of metal rings, with soft lighting and a neutral background.", "Напиши три промпта для изображения скульптуры из металлических колец, с мягким освещением на нейтральном фоне."), action: t("Write an image prompt", "Составить промпт") },
  { kind: "video", label: t("Video", "Видео"), title: t("Video storyboards", "Раскадровки видео"),
    description: t("Plan scenes, camera movement and timing before generating or filming a video.", "Спланируйте сцены, движение камеры и длительность до генерации или съёмки ролика."),
    prompt: t("Create a three-scene storyboard for a 15-second video about particles passing through a wall. Specify the camera movement and timing.", "Создай раскадровку из трёх сцен для 15-секундного ролика: частицы проходят сквозь стену. Укажи движение камеры и длительность сцен."), action: t("Create a storyboard", "Составить раскадровку") },
  { kind: "audio", label: t("Audio", "Аудио"), title: t("Audio scripts", "Сценарии аудио"),
    description: t("Prepare a voiceover, podcast script or music brief.", "Подготовьте закадровый текст, сценарий подкаста или описание музыки."),
    prompt: t("Write a 30-second voiceover explaining what an AI coding agent can do. Use plain language and avoid marketing slogans.", "Напиши 30-секундный закадровый текст о возможностях AI-агента для программирования. Без рекламных лозунгов, простым языком."), action: t("Write a script", "Написать сценарий") },
  { kind: "code", label: t("Code", "Код"), title: t("Code assistance", "Помощь с кодом"),
    description: t("Break down a task, write code, explain an error or review an implementation.", "Разберите задачу, напишите код, объясните ошибку или проверьте реализацию."),
    prompt: t("Write a small TypeScript canvas animation and include support for prefers-reduced-motion.", "Напиши небольшую canvas-анимацию на TypeScript с поддержкой prefers-reduced-motion."), action: t("Start coding", "Перейти к коду") },
];

const apiExample = `curl "$NOCLIP_GATEWAY_URL/v1/chat/completions" \\
  -H "Authorization: Bearer $NOCLIP_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "MODEL_ID",
    "messages": [
      {
        "role": "user",
        "content": "Hello"
      }
    ]
  }'`;

function Arrow({ diagonal = false }: { diagonal?: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {diagonal ? (
        <path d="M6 18 18 6M6 6h12v12" stroke="currentColor" strokeWidth="1.5" />
      ) : (
        <path d="M4 12h15m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.5" />
      )}
    </svg>
  );
}

function ModelStrip({ row }: { row: 0 | 1 }) {
  const direction = row === 0 ? "right" : "left";
  return <div className={styles.marquee}>
    <div data-marquee={direction} className={`${styles.marqueeTrack} ${row === 0 ? styles.marqueeReverse : ""}`}>
      {[0, 1].map(copy => <div className={styles.marqueeGroup} key={copy} aria-hidden={copy === 1 || undefined}>
        {modelRows[row].map(name => <div className={styles.modelWord} key={name} data-model-name={copy === 0 ? name : undefined}><img src={modelIcons[name]} className={styles.modelMark} width="34" height="34" alt="" decoding="async" /><span>{name}</span></div>)}
      </div>)}
    </div>
  </div>;
}

function TextDemo() {
  const { t } = useSitePreferences();
  return (
    <div className={`${styles.demo} ${styles.textDemo}`}>
      <div className={styles.demoTopline}><span className={styles.demoDot} />{t("Example conversation", "Пример диалога")}<span>{t("Text", "Текст")}</span></div>
      <div className={styles.demoConversation}>
        <div className={styles.userMessage}>{t("What does async/await do in JavaScript?", "Что делает async/await в JavaScript?")}</div>
        <div className={styles.answerMark}>n<span>↗</span></div>
        <p className={styles.answerText}>{t("It lets you write asynchronous operations in a sequential style.", "Позволяет записывать асинхронные операции в последовательном стиле.")}</p>
        <p className={styles.answerDetail}>{t("An async function returns a Promise. await pauses that function until the Promise settles.", "Функция async возвращает Promise. await приостанавливает эту функцию до завершения Promise.")}</p>
        <span className={styles.textCursor} aria-hidden="true" />
      </div>
      <div className={styles.demoInput}><span>{t("Show an example", "Покажи пример")}</span><span className={styles.smallSend}><Arrow /></span></div>
    </div>
  );
}

// Геометрический этюд построен кодом и не выдаётся за результат генерации.
const sculptureRings = Array.from({ length: 76 }, (_, ringIndex) => {
  const u = (ringIndex / 76) * Math.PI * 2;
  let depth = 0;
  const points = Array.from({ length: 65 }, (_, pointIndex) => {
    const v = (pointIndex / 64) * Math.PI * 2;
    const radius = 124 + 56 * Math.cos(v);
    const x = radius * Math.cos(u);
    const y = radius * Math.sin(u);
    const z = 56 * Math.sin(v);
    const tiltedY = y * Math.cos(0.83) - z * Math.sin(0.83);
    const tiltedZ = y * Math.sin(0.83) + z * Math.cos(0.83);
    depth += tiltedZ;
    const angle = -0.49;
    return `${300 + 1.19 * (x * Math.cos(angle) - tiltedY * Math.sin(angle))},${264 + 1.19 * (x * Math.sin(angle) + tiltedY * Math.cos(angle))}`;
  });
  return { points: points.join(" "), depth: depth / 65, index: ringIndex };
}).sort((a, b) => a.depth - b.depth);

function ImageDemo() {
  const { t } = useSitePreferences();
  const id = useId().replaceAll(":", "");
  return (
    <div className={`${styles.demo} ${styles.imageDemo}`}>
      <div className={styles.imageLabel}><span>{t("Visual example", "Визуальный пример")}</span></div>
      <svg className={styles.sculpture} viewBox="0 0 600 540" role="img" aria-label={t("Abstract sculpture made of metal rings", "Абстрактная скульптура из тонких металлических колец")}>
        <defs>
          <linearGradient id={`${id}-metal`} x1="0" x2="1" y1="0" y2="1"><stop stopColor="#68645f" /><stop offset=".24" stopColor="#e1d9cb" /><stop offset=".44" stopColor="#1e2024" /><stop offset=".68" stopColor="#77766f" /><stop offset="1" stopColor="#252729" /></linearGradient>
          <filter id={`${id}-shadow`}><feGaussianBlur stdDeviation="13" /></filter>
        </defs>
        <ellipse cx="300" cy="424" rx="153" ry="16" fill="#232528" opacity=".2" filter={`url(#${id}-shadow)`} />
        {sculptureRings.map((ring) => <polyline key={ring.index} points={ring.points} fill="none" stroke={`url(#${id}-metal)`} strokeWidth="2.15" strokeLinejoin="round" />)}
      </svg>
    </div>
  );
}

function VideoDemo({ active }: { active: boolean }) {
  const { t } = useSitePreferences();
  const videoRef = useRef<HTMLVideoElement>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (active && !reducedMotion) void video.play().catch(() => {});
    else video.pause();
  }, [active, reducedMotion]);

  return (
    <div className={`${styles.demo} ${styles.videoDemo}`}>
      <video ref={videoRef} className={styles.sceneVideo} poster="/media/hero-poster.webp" preload="none" muted loop playsInline controls aria-label={t("Video example", "Визуальный пример видео")}>
        <source src="/media/hero-desktop.mp4" type="video/mp4" />
      </video>
      <div className={styles.videoCaption}><span className={styles.liveSquare} />{t("Visual example", "Визуальный пример")}</div>
    </div>
  );
}

function AudioDemo() {
  const { t } = useSitePreferences();
  const [playing, setPlaying] = useState(false);
  return (
    <div className={`${styles.demo} ${styles.audioDemo} ${playing ? styles.audioPlaying : ""}`}>
      <div className={styles.demoTopline}><span className={styles.demoDot} />{t("Audio preview", "Пример аудио")}<span>{t("Voice", "Голос")}</span></div>
      <div className={styles.audioTitle}>{t("Audio waveform", "Звуковая волна")}</div>
      <div className={styles.waveform} aria-hidden="true">
        {Array.from({ length: 64 }, (_, i) => (
          <span key={i} className={styles.waveBar} style={{ "--bar-height": `${16 + Math.abs(Math.sin(i * 0.46) * Math.cos(i * 0.17)) * 94}px`, "--bar-delay": `${(i % 9) * -0.13}s` } as CSSProperties} />
        ))}
      </div>
      <div className={styles.audioControls}>
        <button type="button" className={styles.playButton} onClick={() => setPlaying(!playing)} aria-pressed={playing} aria-label={playing ? t("Stop visualization", "Остановить визуализацию") : t("Play silent visualization", "Запустить визуализацию без звука")}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{playing ? <path d="M6 4h4v16H6zm8 0h4v16h-4z" /> : <path d="m8 4 12 8-12 8V4Z" />}</svg>
        </button>
        <div><strong>{t("Sample waveform", "Пример волны")}</strong><span>{t("Visualization only — no audio", "Визуализация без звука")}</span></div>
        <span className={styles.audioStatus} aria-live="polite">{playing ? t("Animating", "Идёт анимация") : t("Play", "Нажмите play")}</span>
      </div>
    </div>
  );
}

function CodeDemo() {
  const { t } = useSitePreferences();
  return (
    <div className={`${styles.demo} ${styles.codeDemo}`}>
      <div className={styles.codeTopline}><div className={styles.windowDots}><i /><i /><i /></div><span>{t("Code example", "Пример кода")}</span><span>TS</span></div>
      <div className={styles.editorTabs}><span>◈ &nbsp; orbit.ts</span><span>style.css</span></div>
      <div className={styles.editorBody}>
        <div className={styles.lineNumbers} aria-hidden="true">01<br />02<br />03<br />04<br />05<br />06<br />07<br />08<br />09<br />10<br />11<br />12<br />13</div>
        <pre><code><span className={styles.codeMuted}>{"// Draw a circle on a canvas\n"}</span><span className={styles.codePurple}>const</span>{" canvas = document."}<span className={styles.codeBlue}>querySelector</span>{"(\n  "}<span className={styles.codeGreen}>{"'canvas'"}</span>{"\n);\n"}<span className={styles.codePurple}>const</span>{" ctx = canvas?."}<span className={styles.codeBlue}>getContext</span>{"("}<span className={styles.codeGreen}>{"'2d'"}</span>{");\n\n"}<span className={styles.codePurple}>if</span>{" (ctx) {\n  ctx.fillStyle = "}<span className={styles.codeGreen}>{"'#899bff'"}</span>{";\n  ctx."}<span className={styles.codeBlue}>beginPath</span>{"();\n  ctx."}<span className={styles.codeBlue}>arc</span>{"(240, 160, 64, 0, Math.PI * 2);\n  ctx."}<span className={styles.codeBlue}>fill</span>{"();\n}\n"}</code></pre>
      </div>
      <div className={styles.editorTerminal}><span>{t("TERMINAL", "ТЕРМИНАЛ")}</span><p><span>↳</span> {t("Example code — not executed", "Пример кода — не выполняется")}<span className={styles.terminalCursor} /></p></div>
    </div>
  );
}

function FeatureMedia({ kind, active = false }: { kind: FeatureKind; active?: boolean }) {
  if (kind === "text") return <TextDemo />;
  if (kind === "image") return <ImageDemo />;
  if (kind === "video") return <VideoDemo active={active} />;
  if (kind === "audio") return <AudioDemo />;
  return <CodeDemo />;
}

function FeatureStory({ onPrompt }: Pick<LandingSectionsProps, "onPrompt">) {
  const { t } = useSitePreferences();
  const features = getFeatures(t);
  const [active, setActive] = useState(0);
  const [useSticky, setUseSticky] = useState(false);
  const sceneRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const query = window.matchMedia("(min-width: 1000px) and (min-height: 700px) and (prefers-reduced-motion: no-preference)");
    const update = () => setUseSticky(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!useSticky) return;
    let observer: IntersectionObserver;
    const observe = () => {
      observer?.disconnect();
      // Пиксели сохраняют узкую зону наблюдения и на широких экранах.
      const margin = Math.round(window.innerHeight * 0.46);
      observer = new IntersectionObserver((entries) => {
        const visible = entries.find((entry) => entry.isIntersecting);
        if (visible) setActive(Number((visible.target as HTMLElement).dataset.scene));
      }, { rootMargin: `-${margin}px 0px -${margin}px 0px`, threshold: 0 });
      sceneRefs.current.forEach((node) => { if (node) observer.observe(node); });
    };
    observe();
    window.addEventListener("resize", observe);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", observe);
    };
  }, [useSticky]);

  if (!useSticky) {
    return <div className={styles.featureList}>{features.map((feature, index) => (
      <article className={styles.featureArticle} key={feature.kind}>
        <div className={styles.featureCopy}>
          <p className={styles.featureNumber}>0{index + 1}<span />{feature.label}</p>
          <h3>{feature.title}</h3><p className={styles.featureDescription}>{feature.description}</p>
          <button type="button" className={styles.textButton} onClick={() => onPrompt(feature.prompt)}>{feature.action}<Arrow /></button>
        </div>
        <FeatureMedia kind={feature.kind} />
      </article>
    ))}</div>;
  }

  const current = features[active];
  return (
    <div className={styles.storyRun}>
      <div className={styles.sceneMarkers} aria-hidden="true">{features.map((feature, index) => <div key={feature.kind} className={styles.sceneMarker} data-scene={index} ref={(node) => { sceneRefs.current[index] = node; }} />)}</div>
      <div className={styles.storySticky}>
        <div className={styles.storyText}>
          <div className={styles.sceneNavigation} aria-label={t("Feature demonstrations", "Демонстрации возможностей")}>
            {features.map((feature, index) => <button type="button" key={feature.kind} aria-label={feature.label} aria-current={index === active ? "step" : undefined} className={index === active ? styles.sceneNavActive : ""} onClick={() => sceneRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "center" })}><span>0{index + 1}</span></button>)}
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={current.kind} className={styles.featureCopy} initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }} transition={{ duration: 0.28, ease: "easeOut" }}>
              <p className={styles.featureNumber}>0{active + 1}<span />{current.label}</p>
              <h3>{current.title}</h3><p className={styles.featureDescription}>{current.description}</p>
              <button type="button" className={styles.textButton} onClick={() => onPrompt(current.prompt)}>{current.action}<Arrow /></button>
            </motion.div>
          </AnimatePresence>
        </div>
        <div className={styles.storyMedia}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={current.kind} className={styles.storyMediaInner} initial={{ opacity: 0, y: 22, scale: 0.985 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -16, scale: 0.99 }} transition={{ duration: 0.34, ease: [0.22, 1, 0.36, 1] }}>
              <FeatureMedia kind={current.kind} active />
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function ApiExample() {
  const { t } = useSitePreferences();
  const [copyState, setCopyState] = useState<"idle" | "done" | "error">("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(apiExample);
      setCopyState("done");
    } catch {
      setCopyState("error");
    }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopyState("idle"), 2500);
  }

  return (
    <div className={styles.apiCode}>
      <div className={styles.apiCodeHeader}><span>{t("Request example", "Пример запроса")}<span className={styles.language}>cURL</span></span><button type="button" onClick={copy} aria-label={t("Copy request example", "Скопировать пример запроса")}><svg width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="6" y="6" width="10" height="11" rx="1.5" stroke="currentColor" /><path d="M12 6V3H3v11h3" stroke="currentColor" /></svg><span aria-live="polite">{copyState === "done" ? t("Copied", "Скопировано") : copyState === "error" ? t("Copy failed", "Не скопировано") : t("Copy", "Копировать")}</span></button></div>
      <pre><code><span className={styles.codePurple}>curl</span>{" "}<span className={styles.codeGreen}>{'"$NOCLIP_GATEWAY_URL/v1/chat/completions"'}</span>{" \\\n  -H "}<span className={styles.codeGreen}>{'"Authorization: Bearer $NOCLIP_TOKEN"'}</span>{" \\\n  -H "}<span className={styles.codeGreen}>{'"Content-Type: application/json"'}</span>{" \\\n  -d '{\n    "}<span className={styles.codeBlue}>{'"model"'}</span>{': "MODEL_ID",\n    '}<span className={styles.codeBlue}>{'"messages"'}</span>{': [\n      {\n        '}<span className={styles.codeBlue}>{'"role"'}</span>{': "user",\n        '}<span className={styles.codeBlue}>{'"content"'}</span>{': "Hello"\n      }\n    ]\n  }\''}</code></pre>
    </div>
  );
}

export default function LandingSections({ onPrompt, onOpenChat }: LandingSectionsProps) {
  const { t } = useSitePreferences();
  return <div className={styles.sections}>
    <section id="models" className={`${styles.models} ${styles.anchorSection}`}>
      <div className={`${styles.container} ${styles.modelsIntro}`}>
        <h2 className={styles.sectionTitle}>{t("Models & providers", "Модели и провайдеры")}</h2>
      </div>
      <div className={`${styles.modelStrips} ${presentation.modelStrips}`} data-model-strips><ModelStrip row={0} /><ModelStrip row={1} /></div>
    </section>
    <section id="features" className={`${styles.features} ${styles.anchorSection}`}>
      <div className={styles.container}>
        <div className={styles.featureHeading}><h2 className={styles.sectionTitle}>{t("Chat, writing and code", "Общение, тексты и код")}</h2></div>
        <FeatureStory onPrompt={onPrompt} />
      </div>
    </section>
    <section id="pricing" className={`${styles.pricing} ${styles.anchorSection}`}>
      <div className={styles.container}>
        <div className={styles.pricingHeading}><h2 className={styles.sectionTitle}>{t("Access options", "Варианты доступа")}</h2><p className={styles.sectionDescription}>{t("Plans and prices will be published before launch.", "Тарифы и цены будут опубликованы до запуска.")}</p></div>
        <div className={styles.plans}>
          <article className={styles.plan}><h3>{t("Guest preview", "Гостевой просмотр")}</h3><p className={styles.planDescription}>{t("Explore the interface without creating an account.", "Посмотрите интерфейс без создания аккаунта.")}</p><ul><li>{t("Homepage and chat interface", "Главная страница и интерфейс чата")}</li><li>{t("Example prompts", "Примеры промптов")}</li></ul><button type="button" className={styles.primaryButton} onClick={onOpenChat}>{t("Open chat", "Открыть чат")}<Arrow /></button></article>
          <article className={`${styles.plan} ${styles.personalPlan}`}><h3>{t("Personal account", "Личный аккаунт")}</h3><p className={styles.planDescription}>{t("A shared account for NoClip clients. Registration is not enabled in this preview.", "Общий аккаунт для клиентов NoClip. Регистрация пока не включена в этой версии.")}</p><ul><li>{t("Model access and quotas", "Доступ к моделям и квоты")}</li><li>{t("Usage tracking", "Учёт использования")}</li></ul><button type="button" className={styles.outlineButton} onClick={onOpenChat}>{t("Preview the interface", "Посмотреть интерфейс")}<Arrow /></button></article>
          <article className={styles.plan}><h3>{t("Developer API", "API для разработчиков")}</h3><p className={styles.planDescription}>{t("Connect applications through the NoClip gateway.", "Подключайте приложения через шлюз NoClip.")}</p><ul><li>Chat Completions API</li><li>{t("Server-side access control", "Контроль доступа на сервере")}</li></ul><a className={styles.outlineButton} href="#developers">{t("Request example", "Пример запроса")}<Arrow /></a></article>
        </div>
      </div>
    </section>
    <section id="developers" className={`${styles.developers} ${styles.anchorSection}`}>
      <div className={`${styles.container} ${styles.developerGrid}`}>
        <div className={styles.developerCopy}><h2 className={styles.sectionTitle}>{t("One API for your applications", "Единый API для приложений")}</h2><p>{t("Send requests through the gateway using the Chat Completions format.", "Отправляйте запросы через шлюз в формате Chat Completions.")}</p><div className={styles.apiNote}>{t("Set the gateway URL and token in your server environment. Use a MODEL_ID allowed for your account.", "Задайте URL шлюза и токен в серверном окружении. Используйте MODEL_ID, разрешённый вашему аккаунту.")}</div></div>
        <ApiExample />
      </div>
    </section>
    <section id="about" className={`${styles.about} ${styles.anchorSection}`}>
      <div className={styles.container}><h2 className={styles.aboutTitle}>{t("How NoClip works", "Как устроен NoClip")}</h2><div className={styles.principles}>
        <article><h3>{t("Shared access", "Общий доступ")}</h3><p>{t("The website, Telegram bot and desktop client use the same gateway.", "Сайт, Telegram-бот и десктопный клиент используют общий шлюз.")}</p></article>
        <article><h3>{t("Server-side limits", "Лимиты на сервере")}</h3><p>{t("The gateway checks permissions and quotas before sending a request.", "Шлюз проверяет права и квоты перед отправкой запроса.")}</p></article>
        <article><h3>{t("Model selection", "Выбор модели")}</h3><p>{t("Available models depend on the account and the client being used.", "Доступные модели зависят от аккаунта и используемого клиента.")}</p></article>
      </div></div>
    </section>
    <section className={styles.closing}><div className={styles.container}><h2>{t("Try NoClip", "Попробуйте NoClip")}</h2><button type="button" className={styles.primaryButton} onClick={onOpenChat}>{t("Open chat", "Открыть чат")}<Arrow diagonal /></button></div></section>
    <footer className={styles.footer}><div className={styles.container}>
      <div className={styles.footerMain}>
        <div className={styles.footerBrand}><div><img src="/media/noclip-mark.png" alt="" width="36" height="36" /><span>NoClip</span></div></div>
        <nav className={styles.footerColumn} aria-label={t("Product", "Продукт")}><h3>{t("Product", "Продукт")}</h3><a href="#models">{t("Models", "Модели")}</a><a href="#features">{t("Features", "Возможности")}</a><a href="#pricing">{t("Access", "Доступ")}</a></nav>
        <nav className={styles.footerColumn} aria-label={t("Developers", "Разработчикам")}><h3>{t("Developers", "Разработчикам")}</h3><a href="#developers">API</a><a href="#models">{t("Model catalog", "Каталог моделей")}</a></nav>
        <nav className={styles.footerColumn} aria-label="NoClip"><h3>NoClip</h3><a href="#about">{t("About", "О проекте")}</a><button type="button" onClick={onOpenChat}>{t("Open chat", "Открыть чат")}<Arrow diagonal /></button></nav>
      </div>
      <div className={styles.footerBottom}><span>© {new Date().getFullYear()} NoClip</span><a href="#home">{t("Back to top", "Наверх")} ↑</a></div>
    </div></footer>
  </div>;
}
