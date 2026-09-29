def test_production_app_imports():
    from app.main import app

    assert app.title == "Shluz"
