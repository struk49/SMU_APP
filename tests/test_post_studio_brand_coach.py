import app as smu_app
from conftest import create_post, create_user, login


def test_studio_save_updates_brand_coach_with_post_instance(client, module, monkeypatch):
    user = create_user(module)
    post = create_post(module, user)
    login(client, user)

    captured = {}

    def fake_update_brand_coach(candidate, brand_context=""):
        captured["post"] = candidate
        captured["brand_context"] = brand_context
        return {"overall_score": 8.0}

    monkeypatch.setattr(module, "update_brand_coach", fake_update_brand_coach)

    response = client.post(
        f"/post/{post.id}/studio",
        data={"final_caption": "Updated studio caption"},
    )

    assert response.status_code == 302
    assert response.location.endswith(f"/post/{post.id}/studio")
    assert isinstance(captured["post"], module.Post)
    assert captured["post"].id == post.id
    assert captured["post"] is not smu_app.requests.post
