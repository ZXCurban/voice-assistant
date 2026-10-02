"""Local-LLM chat layer (separate from domain services).

Browser → chat API → ChatService → llama-server (OpenAI-compatible HTTP).
No direct DB access from the LLM path; booking operations will be wired
later through AssistantOrchestrator tool calls (see docs/voice-map.md).
"""
