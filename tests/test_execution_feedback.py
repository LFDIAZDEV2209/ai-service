"""Feedback del monitoreo: una respuesta calificada no califica todo el thread."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.admin import _load_extra
from app.db.models import AgentFeedback


async def test_feedback_is_attributed_to_the_selected_execution_only():
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"ai": None}})
    AgentFeedback.__table__.create(engine)
    with Session(engine) as db:
        db.add_all(
            [
                AgentFeedback(
                    id=str(uuid4()),
                    execution_id="answer-1",
                    thread_id="shared",
                    user_id="patient",
                    rating=5,
                    comment="Útil",
                    created_at=datetime.now(UTC),
                ),
                AgentFeedback(
                    id=str(uuid4()),
                    execution_id="answer-2",
                    thread_id="shared",
                    user_id="patient",
                    rating=1,
                    comment="No útil",
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        db.commit()

        class Adapter:
            async def execute(self, statement):
                if statement.column_descriptions[0]["entity"] is AgentFeedback:
                    return db.execute(statement)
                result = MagicMock()
                result.scalars.return_value.all.return_value = []
                return result

        def execution(execution_id):
            return SimpleNamespace(id=execution_id, thread_id="shared", agent_type_id="base")

        first = await _load_extra(execution("answer-1"), Adapter())
        second = await _load_extra(execution("answer-2"), Adapter())
        unrated = await _load_extra(execution("answer-3"), Adapter())
        assert (first["feedback_rating"], first["feedback_comment"]) == (5, "Útil")
        assert (second["feedback_rating"], second["feedback_comment"]) == (1, "No útil")
        assert unrated["feedback_rating"] is None
        assert unrated["feedback_comment"] is None
    engine.dispose()
