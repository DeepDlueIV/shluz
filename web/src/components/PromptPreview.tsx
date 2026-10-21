"use client";

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { animate, motion, useMotionValue } from "motion/react";

export default function PromptPreview({ text }: { text: string }) {
  const [content, setContent] = useState({ text, revision: 0 });
  const requested = useRef(text);
  const displayed = useRef(text);
  const phase = useRef<"idle" | "out" | "in">("idle");
  const generation = useRef(0);
  const animations = useRef<ReturnType<typeof animate>[]>([]);
  const opacity = useMotionValue(1);
  const x = useMotionValue(0);

  const stop = useCallback(() => {
    generation.current += 1;
    for (const animation of animations.current) animation.stop();
    animations.current = [];
  }, []);

  const leave = useCallback(() => {
    // Пока текст исчезает, меняем только цель: не разворачиваем и не перезапускаем fade.
    if (phase.current === "out") return;
    stop();
    phase.current = "out";
    const version = generation.current;
    const fade = animate(opacity, 0, { duration: 0.22, ease: "easeInOut" });
    const slide = animate(x, 10, { duration: 0.22, ease: "easeInOut" });
    animations.current = [fade, slide];
    void fade.then(() => {
      if (generation.current !== version) return;
      slide.stop();
      x.set(-20);
      const target = requested.current;
      displayed.current = target;
      setContent(previous => ({ text: target, revision: previous.revision + 1 }));
    });
  }, [opacity, x, stop]);

  useLayoutEffect(() => {
    requested.current = text;
    if (text !== displayed.current) leave();
  }, [text, leave]);

  useLayoutEffect(() => {
    if (content.revision === 0) return;
    // Новая цель могла появиться между завершением fade и React commit. Старую не показываем.
    if (content.text !== requested.current) {
      const target = requested.current;
      displayed.current = target;
      setContent(previous => ({ text: target, revision: previous.revision + 1 }));
      return;
    }
    stop();
    phase.current = "in";
    const version = generation.current;
    const fade = animate(opacity, 1, { duration: 0.5, ease: "easeOut" });
    const slide = animate(x, 0, { duration: 0.5, ease: "easeOut" });
    animations.current = [fade, slide];
    void fade.then(() => {
      if (generation.current === version) phase.current = "idle";
    });
  }, [content, opacity, x, stop]);

  // Старый callback не должен менять текст после клика, ввода или ухода со страницы.
  useLayoutEffect(() => () => stop(), [stop]);

  return <motion.span style={{ opacity, x }}>{content.text}</motion.span>;
}
