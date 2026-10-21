"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import styles from "./LandingSections.module.css";

type LandingSectionsProps = {
  onPrompt: (prompt: string) => void;
  onOpenChat: () => void;
};

type FeatureKind = "text" | "image" | "video" | "audio" | "code";

const features: {
  kind: FeatureKind;
  label: string;
  title: string;
  description: string;
  prompt: string;
  action: string;
}[] = [
  {
    kind: "text",
    label: "Текст и диалог",
    title: "У каждой мысли\nесть продолжение.",
    description:
      "Обсудите сложную тему, найдите неожиданный сюжет или доведите черновик до ясного текста. Вы выбираете, куда пойдёт разговор.",
    prompt: "Придумай начало романа о городе, в котором однажды исчезли все зеркала. Сделай его атмосферным и немного загадочным.",
    action: "Начать разговор",
  },
  {
    kind: "image",
    label: "Изображения",
    title: "Дайте форму\nсвоему воображению.",
    description:
      "От первой идеи до точного визуального направления. Исследуйте образы, собирайте настроение и находите свой язык.",
    prompt: "Помоги придумать визуальную концепцию: скульптура из тонких металлических колец, мягкий свет, спокойное пространство. Предложи три художественных направления.",
    action: "Придумать образ",
  },
  {
    kind: "video",
    label: "Видео",
    title: "История\nприходит в движение.",
    description:
      "Продумайте сцену, ритм и настроение будущего ролика. Превратите короткую мысль в историю, которую хочется увидеть.",
    prompt: "Помоги написать сценарий атмосферного 15-секундного ролика о любопытстве. Нужны три сцены, движение камеры и короткий текст за кадром.",
    action: "Найти свою историю",
  },
  {
    kind: "audio",
    label: "Аудио",
    title: "У ваших идей\nпоявляется голос.",
    description:
      "Найдите нужную интонацию для текста, подготовьте подкаст или придумайте звуковой образ. Начните с того, что хотите сказать.",
    prompt: "Напиши короткое письмо себе в будущее для спокойного голосового прочтения. Тёплый тон, без пафоса, примерно на 30 секунд.",
    action: "Начать с голоса",
  },
  {
    kind: "code",
    label: "Код",
    title: "От «а что, если»\nдо первой версии.",
    description:
      "Разберите задачу, исследуйте подходы и соберите работающий прототип. Хорошие вопросы помогают писать хороший код.",
    prompt: "Помоги написать на TypeScript небольшую анимацию орбиты на canvas. Объясни структуру и добавь поддержку prefers-reduced-motion.",
    action: "Разобрать задачу",
  },
];

const modelNames = ["Venice", "Qwen", "DeepSeek", "Llama", "Mistral"];

const apiExample = `curl "$NOCLIP_GATEWAY_URL/v1/chat/completions" \\
  -H "Authorization: Bearer $NOCLIP_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "MODEL_ID",
    "messages": [
      {
        "role": "user",
        "content": "Начнём с идеи"
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

function ModelMark({ index }: { index: number }) {
  return (
    <svg viewBox="0 0 40 40" fill="none" aria-hidden="true" className={styles.modelMark}>
      {index % 5 === 0 && <><path d="m5 10 15 24L35 10M12 10l8 13 8-13" stroke="currentColor" strokeWidth="2" /><path d="M5 10h30" stroke="currentColor" strokeWidth="2" /></>}
      {index % 5 === 1 && <><path d="m20 4 4.7 9.4L35 15l-7.5 7.3L29.3 33 20 28l-9.3 5 1.8-10.7L5 15l10.3-1.6L20 4Z" stroke="currentColor" strokeWidth="1.6" /><path d="m12 23 16-10M15 32l7-27" stroke="currentColor" strokeWidth="1.2" /></>}
      {index % 5 === 2 && <path d="M4 18c6 2 9-5 15-4 5 1 7 6 12 7l6-5c0 10-8 17-17 15C10 30 6 24 4 18Zm9-9 4 5m9-7-3 8" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />}
      {index % 5 === 3 && <path d="M3 26C5 9 10 7 16 17l8 12c6 9 13 1 12-7C35 9 30 8 25 16L14 29C7 36 2 31 3 26Z" stroke="currentColor" strokeWidth="2.3" />}
      {index % 5 === 4 && <><path d="M6 29V11h7l7 10 7-10h7v18h-7V20l-7 10-7-10v9H6Z" fill="currentColor" /><path d="M6 33h28" stroke="currentColor" strokeWidth="2" /></>}
    </svg>
  );
}

function ModelStrip({ reverse = false }: { reverse?: boolean }) {
  const names = reverse ? [...modelNames].reverse() : modelNames;
  return (
    <div className={styles.marquee} aria-hidden={reverse || undefined}>
      <div className={`${styles.marqueeTrack} ${reverse ? styles.marqueeReverse : ""}`}>
        {[0, 1, 2].map((copy) => (
          <div className={styles.marqueeGroup} key={copy} aria-hidden={copy > 0 || undefined}>
            {names.map((name) => (
              <div className={styles.modelWord} key={name}>
                <ModelMark index={modelNames.indexOf(name)} />
                <span>{name}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function TextDemo() {
  return (
    <div className={`${styles.demo} ${styles.textDemo}`}>
      <div className={styles.demoTopline}><span className={styles.demoDot} />Пример диалога<span>Текст</span></div>
      <div className={styles.demoConversation}>
        <div className={styles.userMessage}>Как начать роман о городе, в котором исчезли все зеркала?</div>
        <div className={styles.answerMark}>n<span>↗</span></div>
        <p className={styles.answerText}>В то утро город впервые проснулся, не зная, как он выглядит.</p>
        <p className={styles.answerDetail}>Люди поправляли воротники по памяти и учились узнавать себя в глазах друг друга.</p>
        <span className={styles.textCursor} aria-hidden="true" />
      </div>
      <div className={styles.demoInput}><span>А что было дальше?</span><span className={styles.smallSend}><Arrow /></span></div>
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
  const id = useId().replaceAll(":", "");
  return (
    <div className={`${styles.demo} ${styles.imageDemo}`}>
      <div className={styles.imageLabel}><span>Этюд № 01</span><span>Визуальный пример</span></div>
      <svg className={styles.sculpture} viewBox="0 0 600 540" role="img" aria-label="Абстрактная скульптура из тонких металлических колец">
        <defs>
          <linearGradient id={`${id}-metal`} x1="0" x2="1" y1="0" y2="1"><stop stopColor="#68645f" /><stop offset=".24" stopColor="#e1d9cb" /><stop offset=".44" stopColor="#1e2024" /><stop offset=".68" stopColor="#77766f" /><stop offset="1" stopColor="#252729" /></linearGradient>
          <filter id={`${id}-shadow`}><feGaussianBlur stdDeviation="13" /></filter>
        </defs>
        <ellipse cx="300" cy="424" rx="153" ry="16" fill="#232528" opacity=".2" filter={`url(#${id}-shadow)`} />
        {sculptureRings.map((ring) => <polyline key={ring.index} points={ring.points} fill="none" stroke={`url(#${id}-metal)`} strokeWidth="2.15" strokeLinejoin="round" />)}
      </svg>
      <div className={styles.imageFoot}><span>Форма следует за мыслью.</span><span>↗</span></div>
    </div>
  );
}

function VideoDemo({ active }: { active: boolean }) {
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
      <video ref={videoRef} className={styles.sceneVideo} poster="/media/hero-poster.webp" preload="none" muted loop playsInline controls aria-label="Визуальный пример видео">
        <source src="/media/hero-desktop.mp4" type="video/mp4" />
      </video>
      <div className={styles.videoCaption}><span className={styles.liveSquare} />Визуальный пример</div>
      <div className={styles.videoOverline}>В каждом кадре —<br />новая возможность.</div>
    </div>
  );
}

function AudioDemo() {
  const [playing, setPlaying] = useState(false);
  return (
    <div className={`${styles.demo} ${styles.audioDemo} ${playing ? styles.audioPlaying : ""}`}>
      <div className={styles.demoTopline}><span className={styles.demoDot} />Пример аудио<span>Голос</span></div>
      <div className={styles.audioTitle}>Слова, которые<br /><em>звучат по-вашему.</em></div>
      <div className={styles.waveform} aria-hidden="true">
        {Array.from({ length: 64 }, (_, i) => (
          <span key={i} className={styles.waveBar} style={{ "--bar-height": `${16 + Math.abs(Math.sin(i * 0.46) * Math.cos(i * 0.17)) * 94}px`, "--bar-delay": `${(i % 9) * -0.13}s` } as CSSProperties} />
        ))}
      </div>
      <div className={styles.audioControls}>
        <button type="button" className={styles.playButton} onClick={() => setPlaying(!playing)} aria-pressed={playing} aria-label={playing ? "Остановить визуализацию" : "Запустить визуализацию без звука"}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{playing ? <path d="M6 4h4v16H6zm8 0h4v16h-4z" /> : <path d="m8 4 12 8-12 8V4Z" />}</svg>
        </button>
        <div><strong>Письмо себе в будущее</strong><span>Визуальный пример без звука</span></div>
        <span className={styles.audioStatus} aria-live="polite">{playing ? "Идёт анимация" : "Нажмите play"}</span>
      </div>
    </div>
  );
}

function CodeDemo() {
  return (
    <div className={`${styles.demo} ${styles.codeDemo}`}>
      <div className={styles.codeTopline}><div className={styles.windowDots}><i /><i /><i /></div><span>Пример кода</span><span>TS</span></div>
      <div className={styles.editorTabs}><span>◈ &nbsp; orbit.ts</span><span>style.css</span></div>
      <div className={styles.editorBody}>
        <div className={styles.lineNumbers} aria-hidden="true">01<br />02<br />03<br />04<br />05<br />06<br />07<br />08<br />09<br />10<br />11<br />12<br />13</div>
        <pre><code><span className={styles.codeMuted}>{"// Любая идея начинается с движения\n"}</span><span className={styles.codePurple}>const</span>{" canvas = document."}<span className={styles.codeBlue}>querySelector</span>{"(\n  "}<span className={styles.codeGreen}>{"'canvas'"}</span>{"\n);\n"}<span className={styles.codePurple}>const</span>{" ctx = canvas?."}<span className={styles.codeBlue}>getContext</span>{"("}<span className={styles.codeGreen}>{"'2d'"}</span>{");\n\n"}<span className={styles.codePurple}>if</span>{" (ctx) {\n  ctx.fillStyle = "}<span className={styles.codeGreen}>{"'#899bff'"}</span>{";\n  ctx."}<span className={styles.codeBlue}>beginPath</span>{"();\n  ctx."}<span className={styles.codeBlue}>arc</span>{"(240, 160, 64, 0, Math.PI * 2);\n  ctx."}<span className={styles.codeBlue}>fill</span>{"();\n}\n"}</code></pre>
      </div>
      <div className={styles.editorTerminal}><span>ТЕРМИНАЛ</span><p><span>↳</span> От идеи к первой версии<span className={styles.terminalCursor} /></p></div>
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
          <div className={styles.sceneNavigation} aria-label="Демонстрации возможностей">
            {features.map((feature, index) => <button type="button" key={feature.kind} aria-label={feature.label} aria-current={index === active ? "step" : undefined} className={index === active ? styles.sceneNavActive : ""} onClick={() => sceneRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "center" })}><span>0{index + 1}</span></button>)}
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={current.kind} className={styles.featureCopy} initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }} transition={{ duration: 0.28, ease: "easeOut" }}>
              <p className={styles.featureNumber}>0{active + 1}<span />{current.label}</p>
              <h3>{current.title}</h3><p className={styles.featureDescription}>{current.description}</p>
              <button type="button" className={styles.textButton} onClick={() => onPrompt(current.prompt)}>{current.action}<Arrow /></button>
            </motion.div>
          </AnimatePresence>
          <span className={styles.scrollHint}><span />Продолжайте исследовать</span>
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
      <div className={styles.apiCodeHeader}><span>Пример запроса<span className={styles.language}>cURL</span></span><button type="button" onClick={copy} aria-label="Скопировать пример запроса"><svg width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="6" y="6" width="10" height="11" rx="1.5" stroke="currentColor" /><path d="M12 6V3H3v11h3" stroke="currentColor" /></svg><span aria-live="polite">{copyState === "done" ? "Скопировано" : copyState === "error" ? "Не скопировано" : "Копировать"}</span></button></div>
      <pre><code><span className={styles.codePurple}>curl</span>{" "}<span className={styles.codeGreen}>{'"$NOCLIP_GATEWAY_URL/v1/chat/completions"'}</span>{" \\\n  -H "}<span className={styles.codeGreen}>{'"Authorization: Bearer $NOCLIP_TOKEN"'}</span>{" \\\n  -H "}<span className={styles.codeGreen}>{'"Content-Type: application/json"'}</span>{" \\\n  -d '{\n    "}<span className={styles.codeBlue}>{'"model"'}</span>{': "MODEL_ID",\n    '}<span className={styles.codeBlue}>{'"messages"'}</span>{': [\n      {\n        '}<span className={styles.codeBlue}>{'"role"'}</span>{': "user",\n        '}<span className={styles.codeBlue}>{'"content"'}</span>{': "Начнём с идеи"\n      }\n    ]\n  }\''}</code></pre>
      <div className={styles.apiCodeFoot}><span className={styles.demoDot} />Один формат. Простое подключение.</div>
    </div>
  );
}

export default function LandingSections({ onPrompt, onOpenChat }: LandingSectionsProps) {
  return (
    <div className={styles.sections}>
      <section id="models" className={`${styles.models} ${styles.anchorSection}`}>
        <div className={`${styles.container} ${styles.modelsIntro}`}>
          <div><p className={styles.eyebrow}>Модели</p><h2 className={styles.sectionTitle}>Разные модели.<br /><em>Одна свобода.</em></h2></div>
          <p className={styles.sectionDescription}>У каждого подхода — свой характер.<br />Находите тот, что подходит вашей идее.</p>
        </div>
        <div className={styles.modelStrips}><ModelStrip /><ModelStrip reverse /></div>
        <p className={styles.modelDisclaimer}>Примеры семейств в экосистеме ИИ.<br className={styles.mobileBreak} /> Доступность моделей зависит от выбранного доступа.</p>
      </section>

      <section id="features" className={`${styles.features} ${styles.anchorSection}`}>
        <div className={styles.container}>
          <div className={styles.featureHeading}><p className={styles.eyebrow}>От мысли к результату</p><h2 className={styles.sectionTitle}>Возможности,<br /><span>которые не заканчиваются ответом.</span></h2><p className={styles.featureIntro}>Пять направлений для любопытства. Один повод начать.</p></div>
          <FeatureStory onPrompt={onPrompt} />
        </div>
      </section>

      <section id="pricing" className={`${styles.pricing} ${styles.anchorSection}`}>
        <div className={styles.container}>
          <div className={styles.pricingHeading}><div><p className={styles.eyebrow}>На ваших условиях</p><h2 className={styles.sectionTitle}>Свой темп.<br /><em>Свой доступ.</em></h2></div><p className={styles.sectionDescription}>Для первой идеи, личного проекта<br />или чего-то значительно большего.</p></div>
          <div className={styles.plans}>
            <article className={styles.plan}><div className={styles.planLabel}><span>01</span>Знакомство</div><h3>Попробовать</h3><p className={styles.planDescription}>Начните с вопроса.<br />Посмотрите, куда он вас приведёт.</p><ul><li>Первое знакомство с интерфейсом</li><li>Готовые идеи для разговора</li><li>Демонстрация возможностей</li></ul><button type="button" className={styles.primaryButton} onClick={onOpenChat}>Открыть демо<Arrow /></button></article>
            <article className={`${styles.plan} ${styles.personalPlan}`}><div className={styles.planLabel}><span>02</span>Для себя</div><h3>Личный доступ</h3><p className={styles.planDescription}>Пространство для ваших мыслей,<br />задач и ежедневных открытий.</p><ul><li>Общий аккаунт для инструментов</li><li>Выбор подходящих моделей</li><li>Понятные условия доступа</li></ul><button type="button" className={styles.outlineButton} onClick={onOpenChat}>Попробовать демо<Arrow /></button></article>
            <article className={styles.plan}><div className={styles.planLabel}><span>03</span>Для продукта</div><h3>Для разработчиков</h3><p className={styles.planDescription}>Ваши приложения и автоматизации.<br />Знакомый способ подключения.</p><ul><li>Единый шлюз для запросов</li><li>Формат Chat Completions</li><li>Условия и лимиты до старта</li></ul><a className={styles.outlineButton} href="#developers">Пример подключения<Arrow /></a></article>
          </div>
          <p className={styles.accessNote}>Состав доступа и лимиты уточняются перед запуском.</p>
        </div>
      </section>

      <section id="developers" className={`${styles.developers} ${styles.anchorSection}`}>
        <div className={`${styles.container} ${styles.developerGrid}`}>
          <div className={styles.developerCopy}><p className={styles.eyebrow}>Создано для создателей</p><h2 className={styles.sectionTitle}>Ваш продукт.<br /><em>Наш общий язык.</em></h2><p>Подключайте модели через единый шлюз. Формат запросов знаком тем, кто уже работает с Chat Completions.</p><a className={styles.textButton} href="#models">Исследовать модели<Arrow diagonal /></a><div className={styles.apiNote}><span>Небольшая настройка — и можно начать.</span>URL шлюза и токен задаются в окружении. MODEL_ID — идентификатор модели из вашего доступа.</div></div>
          <ApiExample />
        </div>
      </section>

      <section id="about" className={`${styles.about} ${styles.anchorSection}`}>
        <div className={styles.container}><p className={styles.eyebrow}>В основе NoClip</p><h2 className={styles.aboutTitle}>Меньше лишнего.<br /><em>Больше пространства.</em></h2><div className={styles.principles}>
          <article><span className={styles.principleNumber}>01 /</span><h3>Общий аккаунт</h3><p>Одна точка входа для работы с инструментами и управления доступом.</p></article>
          <article><span className={styles.principleNumber}>02 /</span><h3>Прозрачные лимиты</h3><p>Понятные условия использования. Состав доступа и ограничения уточняются до начала работы.</p></article>
          <article><span className={styles.principleNumber}>03 /</span><h3>Выбор инструмента</h3><p>Разным задачам подходят разные модели. Выбирайте подход к каждой новой идее.</p></article>
        </div></div>
      </section>

      <section className={styles.closing}>
        <div className={styles.container}><span className={styles.closingLabel}>Любопытству всегда есть куда расти.</span><h2>Начните<br /><em>со своего вопроса.</em></h2><button type="button" className={styles.primaryButton} onClick={() => onPrompt("Помоги посмотреть на привычную задачу с новой стороны. Начни с трёх неожиданных вопросов, которые помогут найти интересную идею.")}>Начать разговор<Arrow diagonal /></button><span className={styles.closingNote}>Следующая идея — ваша.</span></div>
      </section>

      <footer className={styles.footer}>
        <div className={styles.container}><div className={styles.footerMain}><div className={styles.footerBrand}><div><img src="/media/noclip-mark.png" alt="" width="36" height="36" /><span>NoClip</span></div><p>Пространство для ваших идей.</p></div><nav className={styles.footerColumn} aria-label="Продукт"><h3>Продукт</h3><a href="#models">Модели</a><a href="#features">Возможности</a><a href="#pricing">Доступ</a></nav><nav className={styles.footerColumn} aria-label="Разработчикам"><h3>Разработчикам</h3><a href="#developers">API</a><a href="#models">Семейства моделей</a></nav><nav className={styles.footerColumn} aria-label="О NoClip"><h3>NoClip</h3><a href="#about">О проекте</a><button type="button" onClick={onOpenChat}>Начать разговор<Arrow diagonal /></button></nav></div><div className={styles.footerBottom}><span>© {new Date().getFullYear()} NoClip</span><span>Меньше рамок. Больше возможностей.</span><a href="#models" aria-label="Вернуться к разделу моделей"><Arrow diagonal /></a></div></div>
      </footer>
    </div>
  );
}
