// All 20 names in the two homepage rows at https://venice.ai/ (checked 2026-10-21).
// These are provider/model families, not the full versioned API catalog or NoClip entitlements.
export const modelRows = [
  ["Claude", "Google", "DeepSeek", "OpenAI", "Mistral", "Meta", "Qwen", "Grok", "Kimi", "Black Forest Labs", "NVIDIA"],
  ["ElevenLabs", "Runway", "ByteDance", "MiniMax", "GLM", "Gemma", "Kling", "PixVerse", "Vidu"],
] as const;
export const modelCatalogSource = "https://venice.ai/models";

// Локальные SVG и атрибуция: docs/web-icon-sources.md; перечень не выдаёт права доступа.
export const modelIcons: Record<(typeof modelRows)[number][number], string> = {
  Claude: "/media/models/claude.svg", Google: "/media/models/google.svg",
  DeepSeek: "/media/models/deepseek.svg", OpenAI: "/media/models/openai.svg",
  Mistral: "/media/models/mistral.svg", Meta: "/media/models/meta.svg",
  Qwen: "/media/models/qwen.svg", Grok: "/media/models/grok.svg", Kimi: "/media/models/kimi.svg",
  "Black Forest Labs": "/media/models/black-forest-labs.svg", NVIDIA: "/media/models/nvidia.svg",
  ElevenLabs: "/media/models/elevenlabs.svg", Runway: "/media/models/runway.svg",
  ByteDance: "/media/models/bytedance.svg", MiniMax: "/media/models/minimax.svg",
  GLM: "/media/models/glm.svg", Gemma: "/media/models/gemma.svg", Kling: "/media/models/kling.svg",
  PixVerse: "/media/models/pixverse.svg", Vidu: "/media/models/vidu.svg",
};
