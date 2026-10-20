"""Владелец управляет ограничениями без env/SQL; секреты на странице не отражаются."""

from app.admin.routes import _admin_csrf_token


def test_web_protection_screen_and_csrf(browser):
    assert browser.get("/admin/web-access").status_code == 401
    auth = (
        browser.app.state.settings.admin_username,
        browser.app.state.settings.admin_password.get_secret_value(),
    )
    page = browser.get("/admin/web-access", auth=auth)
    assert page.status_code == 200
    assert "Защита демо" in page.text and "Почтовые коды" in page.text
    assert browser.app.state.web_settings.secret.get_secret_value() not in page.text
    assert browser.post("/admin/web-access/control", auth=auth).status_code == 403
    response = browser.post(
        "/admin/web-access/control",
        auth=auth,
        data={
            "csrf_token": _admin_csrf_token(browser.app.state.settings),
            "enabled": "on",
            "daily_usd": "0",
            "concurrent": "3",
            "timeout_seconds": "45",
            "idle_seconds": "15",
            "network_daily": "3",
            "sessions_hourly": "20",
            "sessions_global_daily": "1000",
            "captcha_minute": "10",
            "otp_email_daily": "5",
            "otp_network_daily": "20",
            "otp_global_daily": "100",
            "otp_verify_minute": "20",
            "http_network_minute": "120",
            "http_global_minute": "2000",
            "captcha_global_minute": "300",
            "otp_verify_global_minute": "500",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    response = browser.post(
        "/admin/web-access/model",
        auth=auth,
        data={
            "csrf_token": _admin_csrf_token(browser.app.state.settings),
            "provider": "venice",
            "model": "demo-future",
            "max_cost_usd": "NaN",
            "max_input_chars": "3000",
            "max_output_tokens": "512",
            "reviewed": "on",
        },
    )
    assert response.status_code == 422
