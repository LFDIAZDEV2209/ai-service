"""Casos ES/EN independientes del nombre de los cuatro perfiles legados."""

from unittest.mock import AsyncMock

import pytest

from app.vita.routing import AuthorizedAgent, select_agent


def agent(id, slug, keywords, channels=None):
    return AuthorizedAgent.model_validate(
        {
            "id": id,
            "runtimeKey": id,
            "slug": slug,
            "name": slug,
            "versionId": f"v-{id}",
            "versionNumber": 1,
            "routing": {
                "topics": [],
                "keywords": keywords,
                "channels": ["text", "voice"] if channels is None else channels,
            },
        }
    )


@pytest.fixture
def catalog():
    return [
        agent("psy", "psychology", ["ansiedad", "anxiety", "psicologia"]),
        agent("nut", "nutrition", ["almuerzo", "lunch", "nutricion"]),
        agent("new", "breath-coach", ["respiracion", "breathing"]),
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Siento ansiedad", "psy"),
        ("I have anxiety", "psy"),
        ("Quiero psicología", "psy"),
        ("Ahora mi almuerzo", "nut"),
        ("Let's discuss breathing", "new"),
        ("Practicar respiración", "new"),
        ("No quiero nutrición, quiero psicología", "psy"),
    ],
)
async def test_fast_rules_select_new_and_existing_erp_agents(text, expected, catalog):
    classifier = AsyncMock()
    result = await select_agent(text, catalog, classifier=classifier)
    assert result.agent.id == expected
    classifier.assert_not_called()


@pytest.mark.parametrize("explicit", ["psy", "psychology"])
async def test_slug_and_guid_keep_compatibility(explicit, catalog):
    assert (await select_agent("Hola", catalog, explicit=explicit)).agent.id == "psy"


async def test_continuity_is_brief_and_rechecks_revocation(catalog):
    assert (await select_agent("¿Y cómo lo practico?", catalog, previous="psy")).agent.id == "psy"
    assert (await select_agent("Ahora mi almuerzo", catalog, previous="psy")).agent.id == "nut"
    assert (await select_agent("¿Y eso?", catalog[1:], previous="psy")).agent is None
    with pytest.raises(ValueError, match="not_authorized"):
        await select_agent("Hola", catalog[1:], explicit="psychology")


async def test_channel_permission_cannot_be_expanded_by_name():
    assert (await select_agent("breathing", [agent("new", "breathing", [], [])])).agent is None


@pytest.mark.parametrize(
    "text",
    [
        "Según la documentación de psicología, explica las heridas de infancia",
        "Using the psychology documents, explain the author's model",
        "Quiero hablar de salud mental",
    ],
)
async def test_actual_erp_labels_route_bilingually_without_metadata_or_extra_model(text):
    mental = agent("published-mental", "psychology", [])
    mental.name, mental.specialty = "Especialista en Salud Mental", "salud mental"
    medical = agent("published-health", "medical", [])
    medical.name, medical.specialty = "Especialista en Salud", "salud"
    classifier = AsyncMock(return_value=None)
    decision = await select_agent(text, [mental, medical], classifier=classifier)
    assert decision.agent.id == mental.id and decision.reason == "catalog_rule"
    classifier.assert_not_awaited()


@pytest.mark.parametrize(
    "text",
    [
        "Hola Vita cómo estás hoy",
        "Hello Vita how are you doing today?",
        "Cuéntame quién eres y cómo puedes ayudarme",
        "Can you tell me who are you?",
        "Gracias por todo lo que me has explicado",
        "Thank you for all your help today",
        "Cómo uso la app para encontrar mi perfil",
        "How do I use this app to find my profile?",
        "Necesito ayuda de la app para abrir mi perfil",
        "I need help with the app settings",
        "Esta entrada larga no contiene evidencia temática del catálogo",
    ],
)
async def test_service_and_unknown_long_queries_never_invoke_classifier(text, catalog):
    classifier = AsyncMock()
    result = await select_agent(text, catalog, previous="psy", classifier=classifier)
    assert result.agent is None
    classifier.assert_not_called()


async def test_structured_classification_only_for_ambiguity_and_bounded_to_catalog(catalog):
    classifier = AsyncMock(return_value="foreign")
    result = await select_agent("ansiedad y almuerzo", catalog, classifier=classifier)
    assert result.agent is None
    assert result.reason == "clarification"
    classifier.assert_awaited_once()
    for greeting in ("Hola", "Hi", "¿Quién eres?"):
        assert (await select_agent(greeting, catalog, classifier=classifier)).agent is None
    assert classifier.await_count == 1


@pytest.mark.parametrize(
    "text",
    [
        "Últimamente me siento desbordado y me cuesta dormir",
        "Me desvelo 2 veces por semana y necesito orientación",
    ],
)
async def test_nonliteral_personal_topic_uses_authorized_new_specialist_description(text):
    specialist = agent("new-psy", "wellbeing-guide", [])
    specialist.description = "Acompañamiento psicológico y bienestar emocional"
    classifier = AsyncMock(return_value=specialist.id)
    result = await select_agent(text, [specialist], classifier=classifier)
    assert result.agent.id == specialist.id and result.reason == "structured_classifier"
    classifier.assert_awaited_once_with(text, [specialist])
    assert classifier.call_args.args[1][0].description == specialist.description


@pytest.mark.parametrize("text", ["¿Cuánto es 12 por 7?", "What is 10 times 4 please?", "20 + 2"])
async def test_simple_calculation_never_classifies(text, catalog):
    classifier = AsyncMock()
    assert (await select_agent(text, catalog, classifier=classifier)).agent is None
    classifier.assert_not_called()
