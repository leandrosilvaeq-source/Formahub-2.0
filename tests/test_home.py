from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_renderiza_pagina_inicial():
    resposta = client.get("/")

    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]
    html = resposta.text
    assert "FormaHub 2.0" in html
    assert "Produtos, estoque e pedidos em um só lugar" in html
    for titulo in ("Produtos", "Estoque", "Pedidos"):
        assert f"<h2>{titulo}</h2>" in html


def test_css_e_servido():
    resposta = client.get("/static/css/app.css")

    assert resposta.status_code == 200
    assert "--cor-primaria" in resposta.text
