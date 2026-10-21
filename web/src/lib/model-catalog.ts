export const modelRows = [
  ["Claude", "Google", "DeepSeek", "OpenAI", "Mistral", "Meta", "Qwen", "Grok", "Kimi", "Black Forest Labs", "NVIDIA"],
  ["ElevenLabs", "Runway", "ByteDance", "MiniMax", "GLM", "Gemma", "Kling", "PixVerse", "Vidu"],
] as const;

// Локальные SVG из Lobe Icons. Происхождение и лицензия — public/media/models/SOURCES.md.
export const modelIconSlugs: Record<(typeof modelRows)[number][number], string> = {
  "Claude": "claude", "Google": "google", "DeepSeek": "deepseek", "OpenAI": "openai",
  "Mistral": "mistral", "Meta": "meta", "Qwen": "qwen", "Grok": "grok", "Kimi": "kimi",
  "Black Forest Labs": "bfl", "NVIDIA": "nvidia", "ElevenLabs": "elevenlabs",
  "Runway": "runway", "ByteDance": "bytedance", "MiniMax": "minimax", "GLM": "zai",
  "Gemma": "gemma", "Kling": "kling", "PixVerse": "pixverse", "Vidu": "vidu",
};
