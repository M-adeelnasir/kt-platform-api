"""AI layer (Phase 1): LLMProvider + Embedder interfaces, RAG, interview, synthesis,
grounded-answers contract. All model calls go through the interfaces here — business logic
never imports a model SDK directly (plan §3, §7).
"""
