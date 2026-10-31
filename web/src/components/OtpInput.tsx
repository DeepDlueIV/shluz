"use client";

import { useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import styles from "./Account.module.css";

/** 6 отдельных полей одноразового кода: авто-переход, вставка «123456»
 *  распределяется по полям, Backspace возвращает в предыдущее поле. */
export function OtpInput({
  length = 6,
  onComplete,
  disabled = false,
}: {
  length?: number;
  onComplete: (code: string) => void;
  disabled?: boolean;
}) {
  const [values, setValues] = useState<string[]>(() => Array<string>(length).fill(""));
  const refs = useRef<(HTMLInputElement | null)[]>([]);

  const emit = (next: string[]) => {
    const joined = next.join("");
    if (joined.length === length && next.every((value) => value !== "")) {
      onComplete(joined);
    }
  };

  const focusAt = (index: number) => {
    refs.current[Math.min(Math.max(index, 0), length - 1)]?.focus();
  };

  const applyDigits = (index: number, digits: string) => {
    const next = [...values];
    for (let offset = 0; offset < digits.length && index + offset < length; offset += 1) {
      next[index + offset] = digits[offset];
    }
    setValues(next);
    focusAt(index + digits.length);
    emit(next);
  };

  const onChange = (index: number, raw: string) => {
    const digits = raw.replace(/\D/g, "");
    if (!digits) {
      const next = [...values];
      next[index] = "";
      setValues(next);
      return;
    }
    applyDigits(index, digits.length > 1 ? digits : digits.slice(-1));
  };

  const keydown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Backspace") {
      event.preventDefault();
      const next = [...values];
      if (next[index]) {
        next[index] = "";
        setValues(next);
        emit(next);
        return;
      }
      if (index > 0) {
        next[index - 1] = "";
        setValues(next);
        focusAt(index - 1);
        emit(next);
      }
      return;
    }
    if (event.key === "ArrowLeft" && index > 0) {
      event.preventDefault();
      focusAt(index - 1);
    }
    if (event.key === "ArrowRight" && index < length - 1) {
      event.preventDefault();
      focusAt(index + 1);
    }
  };

  const paste = (index: number, event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    const digits = event.clipboardData.getData("text").replace(/\D/g, "");
    if (digits) applyDigits(index, digits.slice(0, length - index));
  };

  return (
    <div className={styles.otpRow} role="group" aria-label="One-time code">
      {values.map((value, index) => (
        <input
          key={index}
          ref={(element) => {
            refs.current[index] = element;
          }}
          className={styles.otpBox}
          value={value}
          inputMode="numeric"
          autoComplete={index === 0 ? "one-time-code" : "off"}
          maxLength={1}
          disabled={disabled}
          aria-label={`Digit ${index + 1} of ${length}`}
          onChange={(event) => onChange(index, event.target.value)}
          onKeyDown={(event) => keydown(index, event)}
          onPaste={(event) => paste(index, event)}
          onFocus={(event) => event.target.select()}
        />
      ))}
    </div>
  );
}
