from conftest import create_post, create_user, login


def test_ai_content_studio_uses_responsive_layout(client, module):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    response = client.get(f"/post/{post.id}/studio")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "studio-shell" in html
    assert "studio-main-grid" in html
    assert "studio-lower-grid" in html
    assert "AI Writing Assistant" in html
    assert "Caption Workspace" in html
    assert "Brand Coach" in html
    assert "Latest Grade" in html
    assert "Revision Timeline" in html
    assert "Back to Post" in html
