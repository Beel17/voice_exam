"""The invigilators' status page and the overview behind it."""

from fastapi.testclient import TestClient

from tests.integration.test_api import client, clock, hello, platform  # noqa: F401


def test_the_overview_needs_no_token_and_holds_no_candidate_data(
    client: TestClient,  # noqa: F811
) -> None:
    overview = client.get("/v1/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["unsynced_writes"] == 0 and body["connected"] == 0
    assert body["language_model"] == "disabled"
    assert body["language_model_problem"] == "switched off in the configuration"
    assert body["now"] >= body["up_since"]
    # Counts and states only: no field could carry a name, number or session.
    assert all(isinstance(v, (int, float, str, type(None))) for v in body.values())
    assert not {"session_id", "candidate_id", "reg_no"} & set(body)


def test_the_status_page_is_served_and_self_contained(client: TestClient) -> None:  # noqa: F811
    assert client.get("/status", follow_redirects=False).headers["location"] == (
        "/client/status.html"
    )
    page = client.get("/client/status.html").text
    assert "/v1/overview" in page
    # Offline: nothing fetched from anywhere else.
    assert "http://" not in page and "https://" not in page
