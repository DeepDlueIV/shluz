"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./HomePage.module.css";
import presentation from "./Presentation.module.css";

export default function HeroVideo() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = useRef(true);
  const [mobile, setMobile] = useState(false);
  const [ready, setReady] = useState(false);
  const play = useCallback(() => {
    const video = videoRef.current;
    if (!video || document.hidden || !visible.current || !video.paused) return;
    video.defaultMuted = true;
    video.muted = true;
    // При запрете autoplay браузером повторяем попытку на обычном действии пользователя.
    // Отдельной кнопки и бесконечного таймера повторов нет.
    void video.play().catch(() => {});
  }, []);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px)");
    const update = () => setMobile(query.matches);
    update(); query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    setReady(false);
    visible.current = video.getBoundingClientRect().bottom > 0;
    const sync = () => {
      if (!document.hidden && visible.current) play();
      else video.pause();
    };
    const observer = new IntersectionObserver(([entry]) => {
      visible.current = entry.isIntersecting; sync();
    }, { threshold: 0 });
    observer.observe(video);
    video.addEventListener("canplay", sync);
    document.addEventListener("visibilitychange", sync);
    document.addEventListener("pointerdown", play);
    document.addEventListener("keydown", play);
    sync();
    return () => {
      observer.disconnect();
      video.removeEventListener("canplay", sync);
      document.removeEventListener("visibilitychange", sync);
      document.removeEventListener("pointerdown", play);
      document.removeEventListener("keydown", play);
    };
  }, [mobile, play]);
  const variant = mobile ? "mobile" : "desktop";
  return <div className={`${styles.heroMedia} ${presentation.background}`} data-video-ready={ready} aria-hidden="true">
    <picture><source media="(max-width: 700px)" srcSet="/media/hero-poster-mobile.webp" />
      <img className={styles.poster} src="/media/hero-poster.webp" alt="" fetchPriority="high" /></picture>
    <video key={variant} ref={videoRef} data-hero-video className={styles.video} muted loop playsInline autoPlay
      preload="auto" poster={mobile ? "/media/hero-poster-mobile.webp" : "/media/hero-poster.webp"}
      onPlaying={() => setReady(true)} onError={() => setReady(false)}>
      <source src={`/media/hero-${variant}.webm`} type="video/webm" />
      <source src={`/media/hero-${variant}.mp4`} type="video/mp4" />
    </video>
    <div className={styles.videoShade} />
  </div>;
}
