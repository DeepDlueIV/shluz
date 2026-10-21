"use client";

import { AnimatePresence, motion } from "motion/react";
import styles from "./HomePage.module.css";

export default function PromptPreview({ text, visible }: { text: string; visible: boolean }) {
  // Введённый текст никогда не участвует в анимации подсказки.
  if (!visible) return null;
  return <span className={styles.promptPreview} aria-hidden="true" data-prompt-preview>
    <AnimatePresence initial={false} mode="sync">
      <motion.span key={text} data-preview-text initial={{ opacity: 0, x: -8 }}
        animate={{ opacity: 1, x: 0, transition: { duration: .12, ease: "easeOut" } }}
        exit={{ opacity: 0, x: 8, transition: { duration: .08, ease: "easeOut" } }}>
        {text}
      </motion.span>
    </AnimatePresence>
  </span>;
}
