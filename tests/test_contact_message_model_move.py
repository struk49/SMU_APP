import app as smu_app
from smu_core.models import ContactMessage


def test_contact_message_model_remains_compatible(module):
    assert smu_app.ContactMessage is ContactMessage
    assert module.ContactMessage is ContactMessage
    assert ContactMessage.__table__.name == "contact_message"
    assert sorted(ContactMessage.__table__.columns.keys()) == [
        "created_at",
        "email",
        "id",
        "message",
        "name",
    ]
    assert "contact_message" in module.db.metadata.tables


def test_contact_endpoint_still_creates_contact_message_row(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "Beta Contact",
            "email": "contact@example.com",
            "message": "ContactMessage model move still works.",
        },
        follow_redirects=True,
    )
    message = module.ContactMessage.query.filter_by(
        email="contact@example.com"
    ).first()

    assert response.status_code == 200
    assert "message has been received" in response.get_data(as_text=True)
    assert message is not None
    assert message.name == "Beta Contact"
    assert message.message == "ContactMessage model move still works."


def test_contact_endpoint_validation_still_rejects_invalid_email(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "Beta Contact",
            "email": "not-an-email",
            "message": "Hello",
        },
    )

    assert response.status_code == 400
    assert module.ContactMessage.query.count() == 0
    assert "A valid email is required." in response.get_data(as_text=True)
