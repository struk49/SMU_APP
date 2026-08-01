import app as smu_app
from conftest import create_user, login
from smu_core.models import Feedback


def test_feedback_model_remains_compatible(client, module):
    assert smu_app.Feedback is Feedback
    assert module.Feedback is Feedback
    assert Feedback.__table__.name == "feedback"
    assert sorted(Feedback.__table__.columns.keys()) == [
        "created_at",
        "id",
        "message",
        "page_url",
        "user_id",
    ]
    assert "feedback" in module.db.metadata.tables


def test_feedback_endpoint_still_creates_feedback_row(client, module):
    user = create_user(module)
    login(client, user)

    response = client.post(
        "/feedback",
        json={
            "message": "Feedback model move still works.",
            "page_url": "/calendar",
        },
    )
    feedback = module.Feedback.query.filter_by(user_id=user.id).first()

    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    assert feedback is not None
    assert feedback.message == "Feedback model move still works."
