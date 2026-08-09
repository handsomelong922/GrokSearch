from .openai_compatible import OpenAICompatibleSearchProvider


class GrokSearchProvider(OpenAICompatibleSearchProvider):
    """Backward-compatible alias for OpenAICompatibleSearchProvider.

    This class is kept for compatibility with existing code. New code
    should use OpenAICompatibleSearchProvider directly.
    """
    def __init__(self, api_url: str, api_key: str, model: str = "grok-4-fast", provider_name: str = "Grok"):
        super().__init__(api_url, api_key, model, provider_name)
