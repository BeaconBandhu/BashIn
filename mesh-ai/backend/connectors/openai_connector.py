from typing import Optional
from models.schemas import AISource


class OpenAIConnector:
    def __init__(self, api_key: Optional[str] = None):
        self.source = AISource.CHATGPT
        self.client = None
        if api_key:
            try:
                import openai
                self.client = openai.OpenAI(api_key=api_key)
            except ImportError:
                pass

    def chat(self, message: str, context: str = "") -> str:
        if not self.client:
            raise RuntimeError("OpenAI client not initialised — provide OPENAI_API_KEY")
        messages = []
        if context:
            messages.append({"role": "system", "content": f"Context from knowledge graph:\n{context}"})
        messages.append({"role": "user", "content": message})
        resp = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
        )
        return resp.choices[0].message.content
