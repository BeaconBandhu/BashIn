from typing import Optional
from models.schemas import AISource


class GeminiConnector:
    def __init__(self, api_key: Optional[str] = None):
        self.source = AISource.GEMINI
        self.model = None
        if api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel("gemini-pro")
            except ImportError:
                pass

    def chat(self, message: str, context: str = "") -> str:
        if not self.model:
            raise RuntimeError("Gemini client not initialised — provide GEMINI_API_KEY")
        prompt = message
        if context:
            prompt = f"Context from knowledge graph:\n{context}\n\nUser: {message}"
        resp = self.model.generate_content(prompt)
        return resp.text
