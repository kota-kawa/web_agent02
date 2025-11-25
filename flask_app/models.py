
SUPPORTED_MODELS = [
    # OpenAI
    {"provider": "openai", "model": "gpt-4.1", "label": "GPT-4.1"},
    {"provider": "openai", "model": "gpt-5.1", "label": "GPT-5.1"},
    {"provider": "openai", "model": "gpt-5-mini", "label": "GPT-5 mini"},
    # Gemini (Google)
    {"provider": "gemini", "model": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
    {"provider": "gemini", "model": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    {"provider": "gemini", "model": "gemini-3-pro-preview", "label": "Gemini 3 Pro Preview"},
    # Claude (Anthropic)
    {"provider": "claude", "model": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
    {"provider": "claude", "model": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    {"provider": "claude", "model": "claude-opus-4-5", "label": "Claude Opus 4.5"},
    # Groq
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B (Groq)"},
    {"provider": "groq", "model": "llama-3.1-8b-instant", "label": "Llama 3.1 8B (Groq)"},
    {"provider": "groq", "model": "openai/gpt-oss-20b", "label": "GPT-OSS 20B (Groq)"},
    {"provider": "groq", "model": "meta-llama/llama-4-maverick-17b-128e-instruct", "label": "Llama 4 Maverick 17B (Groq)"},
    {"provider": "groq", "model": "moonshotai/kimi-k2-instruct-0905", "label": "Kimi K2 Instruct 0905 (Groq)"},
    {"provider": "groq", "model": "qwen/qwen3-32b", "label": "Qwen3 32B (Groq)"},
]
