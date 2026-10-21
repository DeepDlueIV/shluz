// All 20 names in the two homepage rows at https://venice.ai/ (checked 2026-10-21).
// These are provider/model families, not the full versioned API catalog or NoClip entitlements.
export const modelRows = [
  ["Claude", "Google", "DeepSeek", "OpenAI", "Mistral", "Meta", "Qwen", "Grok", "Kimi", "Black Forest Labs", "NVIDIA"],
  ["ElevenLabs", "Runway", "ByteDance", "MiniMax", "GLM", "Gemma", "Kling", "PixVerse", "Vidu"],
] as const;
export const modelCatalogSource = "https://venice.ai/models";
