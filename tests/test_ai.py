from unittest import TestCase

from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from alves_ia.ai import (
    HANDOFF_TOOL,
    AIReply,
    GeminiProvider,
    OpenAIProvider,
    ToolCall,
    create_provider,
)
from alves_ia.config import Settings
from alves_ia.whatsapp.processing import HandoffArguments, HandoffReason


class SettingsForTests(Settings):
    model_config = SettingsConfigDict(env_file=None)


class ProviderSelectionTests(TestCase):
    def test_tool_calls_are_normalized_without_execution(self) -> None:
        reply = AIReply(
            text="", tool_calls=(ToolCall(name="request_human", arguments={"reason": "other"}),)
        )

        self.assertEqual(reply.tool_calls[0].name, "request_human")
        self.assertEqual(reply.tool_calls[0].arguments, {"reason": "other"})

    def test_provider_exposes_only_fixed_handoff_tool(self) -> None:
        self.assertEqual(HANDOFF_TOOL["name"], "request_human")
        self.assertEqual(
            HANDOFF_TOOL["parameters"]["properties"]["reason"]["enum"],
            ["customer_request", "urgent_risk", "unclear_request", "other"],
        )
        self.assertFalse(HANDOFF_TOOL["parameters"]["additionalProperties"])

    def test_handoff_arguments_reject_unknown_fields_and_reasons(self) -> None:
        parsed = HandoffArguments.model_validate({"reason": "customer_request"})
        self.assertEqual(parsed.reason, HandoffReason.CUSTOMER_REQUEST)
        with self.assertRaises(ValidationError):
            HandoffArguments.model_validate({"reason": "delete_data"})
        with self.assertRaises(ValidationError):
            HandoffArguments.model_validate({"reason": "other", "clinic_id": "attacker"})

    def test_gemini_is_the_default_provider(self) -> None:
        settings = SettingsForTests(
            ai_provider="gemini",
            gemini_api_key="test-key",
            gemini_model="gemini-2.5-flash",
            openai_api_key="",
            openai_model="gpt-4o-mini",
        )

        provider = create_provider(settings)

        self.assertIsInstance(provider, GeminiProvider)
        self.assertEqual(provider.model, "gemini-2.5-flash")

    def test_live_ai_is_disabled_by_default(self) -> None:
        self.assertFalse(SettingsForTests().ai_live_enabled)

    def test_openai_can_be_selected_as_an_alternative(self) -> None:
        settings = SettingsForTests(
            ai_provider="openai",
            gemini_api_key="",
            gemini_model="gemini-2.5-flash",
            openai_api_key="test-key",
            openai_model="gpt-4o-mini",
        )

        provider = create_provider(settings)

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model, "gpt-4o-mini")

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(ValidationError) as error:
            SettingsForTests.model_validate({"ai_provider": "unknown"})

        self.assertEqual(error.exception.errors()[0]["loc"], ("ai_provider",))
        self.assertEqual(error.exception.errors()[0]["type"], "literal_error")
