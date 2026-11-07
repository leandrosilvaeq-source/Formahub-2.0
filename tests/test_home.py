from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_renderiza_pagina_inicial():
    resposta = client.get("/")

    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]
    html = resposta.text
    assert "FormaHub 2.0" in html
    assert "Produtos, estoque e pedidos em um só lugar" not in html
    for titulo in ("Produtos", "Compras", "Pedidos"):
        assert f"<h2>{titulo}</h2>" in html


def test_css_e_servido():
    resposta = client.get("/static/css/app.css")

    assert resposta.status_code == 200
    assert "--cor-primaria" in resposta.text


def test_estaticos_sao_revalidados_para_atualizacoes_aparecerem():
    resposta = client.get("/static/css/app.css")

    assert resposta.headers["cache-control"] == "no-cache"
    etag = resposta.headers["etag"]
    revalidacao = client.get("/static/css/app.css", headers={"if-none-match": etag})
    assert revalidacao.status_code == 304


def test_cabecalho_exibe_logotipo_oficial():
    html = client.get("/").text

    assert 'alt="Forma 3D Studio"' in html
    assert "/static/img/logo-forma3d-horizontal.png" in html

    logo = client.get("/static/img/logo-forma3d-horizontal.png")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"


def test_fontes_locais_sao_servidas():
    for arquivo in ("sora-700-latin.woff2", "manrope-latin.woff2"):
        assert client.get(f"/static/fonts/{arquivo}").status_code == 200


def test_indicadores_do_mes_comparam_com_mes_anterior():
    from datetime import date, datetime
    from decimal import Decimal

    from app.pedidos import indicadores_do_mes
    from app.pedidos_repositorio import PedidoResumo

    def pedido(valor, criado, status="Pago", cancelado=False):
        return PedidoResumo(
            id=1,
            cliente_nome="x",
            forma_pagamento="PIX",
            status_pagamento=status,
            prazo_entrega=date(2026, 11, 20),
            criado_por_nome="x",
            produtos=("a",),
            quantidade_total=1,
            observacoes=None,
            cancelado=cancelado,
            valor_total=Decimal(valor),
            criado_em=criado,
        )

    pedidos = [
        pedido("100", datetime(2026, 11, 2)),
        pedido("50", datetime(2026, 11, 5)),
        pedido("999", datetime(2026, 11, 6), status="Pendente"),
        pedido("999", datetime(2026, 11, 6), cancelado=True),
        pedido("100", datetime(2026, 10, 30)),
    ]
    ind = indicadores_do_mes(pedidos, date(2026, 11, 7))

    assert ind["faturamento"]["atual"] == Decimal("150")
    assert ind["faturamento"]["variacao"] == {"direcao": "alta", "texto": "50,0%"}
    assert ind["pedidos"]["atual"] == 2
    assert ind["ticket"]["atual"] == Decimal("75")
    assert ind["ticket"]["variacao"]["direcao"] == "queda"
    assert indicadores_do_mes([pedido("1", None)], date(2026, 11, 7)) is None
