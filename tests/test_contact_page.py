from flask import template_rendered


def test_contact_page_renders_for_anonymous_user(client, app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append(template.name)

    template_rendered.connect(record, app)
    try:
        response = client.get("/contact")
    finally:
        template_rendered.disconnect(record, app)

    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert recorded == ["contact.html"]
    assert "Contact SMU" in html
    assert "Have a question, found a bug, or want to share feedback?" in html
    assert "Support" in html
    assert "Bug Report" in html
    assert "Feature Request" in html
    assert "Beta Feedback" in html
    assert "Privacy" in html
    assert 'name="name"' in html
    assert 'name="email"' in html
    assert 'name="message"' in html
    assert 'href="/privacy"' in html
    assert 'href="/contact"' in html


def test_contact_valid_submission_creates_message_and_confirms(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "  Andrew  ",
            "email": "  ANDREW@example.COM  ",
            "message": "  Contact page message.  ",
        },
        follow_redirects=True,
    )

    html = response.get_data(as_text=True)
    messages = module.ContactMessage.query.all()

    assert response.status_code == 200
    assert "message has been received" in html
    assert len(messages) == 1
    assert messages[0].name == "Andrew"
    assert messages[0].email == "andrew@example.com"
    assert messages[0].message == "Contact page message."


def test_contact_missing_email_does_not_create_message(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "Andrew",
            "email": "",
            "message": "Please help.",
        },
    )

    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert module.ContactMessage.query.count() == 0
    assert "A valid email is required." in html


def test_contact_empty_message_does_not_create_message(client, module):
    response = client.post(
        "/contact",
        data={
            "name": "Andrew",
            "email": "andrew@example.com",
            "message": "   ",
        },
    )

    html = response.get_data(as_text=True)

    assert response.status_code == 400
    assert module.ContactMessage.query.count() == 0
    assert "Message is required." in html


def test_contact_submission_is_not_publicly_rendered(client, module):
    submitted_message = "<script>alert('private contact')</script>"

    response = client.post(
        "/contact",
        data={
            "name": "Andrew",
            "email": "andrew@example.com",
            "message": submitted_message,
        },
        follow_redirects=True,
    )

    html = response.get_data(as_text=True)
    stored = module.ContactMessage.query.one()

    assert response.status_code == 200
    assert stored.message == submitted_message
    assert submitted_message not in html
    assert "&lt;script&gt;alert(&#39;private contact&#39;)&lt;/script&gt;" not in html
