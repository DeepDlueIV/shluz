"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSitePreferences } from "./SitePreferences";
import styles from "./HomePage.module.css";
import presentation from "./Presentation.module.css";

export default function HeroVideo() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = useRef(true);
  const { ambientMotion, preferencesReady, enableAnimation, t } = useSitePreferences();
  const [mobile, setMobile] = useState(false);
  const [ready, setReady] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [failed, setFailed] = useState(false);
  const play = useCallback(() => {
    const video = videoRef.current;
    if (!video || !ambientMotion || document.hidden || !visible.current) return;
    // Set both properties before every attempt; browser autoplay checks the muted property.
    video.defaultMuted = true;
    video.muted = true;
    void video.play().then(() => { setBlocked(false); setFailed(false); }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setBlocked(true);
    });
  }, [ambientMotion]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px)");
    const update = () => setMobile(query.matches);
    update(); query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const sync = () => {
      if (ambientMotion && !document.hidden && visible.current) play();
      else video.pause();
    };
    const observer = new IntersectionObserver(([entry]) => { visible.current = entry.isIntersecting; sync(); }, { threshold: 0 });
    observer.observe(video);
    video.addEventListener("canplay", sync);
    document.addEventListener("visibilitychange", sync);
    // An interaction retries autoplay without resending any prompt or restarting the clip.
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
  }, [ambientMotion, mobile, play]);
  function retry() {
    enableAnimation();
    const video = videoRef.current;
    if (!video) return;
    video.defaultMuted = true; video.muted = true;
    if (failed) { video.load(); setFailed(false); }
    // This call stays inside the user gesture for browsers with restrictive autoplay.
    void video.play().then(() => setBlocked(false)).catch(() => setBlocked(true));
  }
  return <>
    <div className={`${styles.heroMedia} ${presentation.background}`} data-video-ready={ready} aria-hidden="true">
      <picture><source media="(max-width: 700px)" srcSet="/media/hero-poster-mobile.webp" />
        <img className={styles.poster} src="/media/hero-poster.webp" alt="" fetchPriority="high" /></picture>
      <video ref={videoRef} data-hero-video className={styles.video} muted loop playsInline autoPlay={ambientMotion}
        preload="auto" src={mobile ? "/media/hero-mobile.mp4" : "/media/hero-desktop.mp4"}
        poster={mobile ? "/media/hero-poster-mobile.webp" : "/media/hero-poster.webp"}
        onPlaying={() => { setReady(true); setBlocked(false); }} onError={() => setFailed(true)} />
      <div className={styles.videoShade} />
    </div>
    {preferencesReady && (!ambientMotion || blocked || failed) && <button type="button" className={presentation.enableAnimation} onClick={retry}
      aria-label={t("Enable animation", "Включить анимацию")}>
      <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true"><path fill="currentColor" d="m6 3 11 7-11 7Z" /></svg>
      {t("Enable animation", "Включить анимацию")}
    </button>}
  </>;
}
