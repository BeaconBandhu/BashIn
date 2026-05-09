from typing import Optional
from models.schemas import AISource


class AnthropicConnector:
    def __init__(self, api_key: Optional[str] = None):
        self.source = AISource.CLAUDE
        self.client = None
        if api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key)
            except ImportError:
                pass

    def chat(self, message: str, context: str = "") -> str:
        if not self.client:
            raise RuntimeError("Anthropic client not initialised — provide ANTHROPIC_API_KEY")
        system = "You are a helpful assistant."
        if context:
            system += f"\n\nContext from knowledge graph:\n{context}"
        resp = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": message}],
        )
        return resp.content[0].text
