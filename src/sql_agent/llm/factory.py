"""LLM provider factory (Anthropic / OpenAI / Claude on Vertex AI) driven by settings."""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from sql_agent.config import get_settings

_REQUEST_TIMEOUT_SECONDS = 60
_API_MAX_RETRIES = 2  # transient HTTP errors only; SQL retries live in the graph


@lru_cache
def get_llm() -> BaseChatModel:
    """Shared chat model for the provider named by LLM_PROVIDER."""
    s = get_settings()
    provider = s.llm_provider.strip().lower()

    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set (see .env.example)")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=s.anthropic_model,
            api_key=s.anthropic_api_key,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            max_retries=_API_MAX_RETRIES,
        )

    if provider == "openai":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set (see .env.example)")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.openai_model,
            api_key=s.openai_api_key,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            max_retries=_API_MAX_RETRIES,
            # GPT-5.x reasoning models only allow tool calls on the Responses API.
            use_responses_api=True,
        )

    if provider == "vertex":
        # Claude through Vertex AI: auth comes from the runtime service account (needs
        # roles/aiplatform.user and the model enabled in Model Garden), not an API key.
        if not s.google_cloud_project:
            raise ValueError("GOOGLE_CLOUD_PROJECT is not set (required for LLM_PROVIDER=vertex)")
        from langchain_google_vertexai.model_garden import ChatAnthropicVertex

        return ChatAnthropicVertex(
            model_name=s.vertex_model,
            project=s.google_cloud_project,
            location=s.vertex_location,
            temperature=s.llm_temperature,
            max_output_tokens=s.llm_max_tokens,
            max_retries=_API_MAX_RETRIES,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER {s.llm_provider!r} (expected anthropic | openai | vertex)"
    )


def message_text(message: BaseMessage) -> str:
    """Plain text of a chat response, whether content is a string or a list of blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
