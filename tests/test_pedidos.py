from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pedidos import formatar_brl, formatar_contato, ler_formulario, parse_moeda, validar

client = TestClient(app)


def form_valido(**extra):
    dados = {
        "cliente": "Maria Souza",
        "contato": "(11) 98765-4321",
        "item_produto": ["Caneca personalizada", "Camiseta"],
        "item_quantidade": ["2", "1"],
        "item_valor": ["R$ 35,50", "R$ 1.200,00"],
        "pagamento": "PIX",
        "entrega": "Retirada",
        "observacoes": "",
    }
    dados.update(extra)
    return dados


def pedido_de(dados):
    return validar(ler_formulario({k: v if isinstance(v, list) else [v] for k, v in dados.items()}))


def erros_do_item(**item):
    base = {"item_produto": "X", "item_quantidade": "1", "item_valor": "R$ 1,00"}
    base.update(item)
    return pedido_de(form_valido(**{k: [v] for k, v in base.items()})).itens[0].erros


# ---------- Tela ----------


def test_tela_novo_pedido_responde_200():
    resposta = client.get("/pedidos/novo")

    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]


def test_tela_exibe_somente_os_campos_da_especificacao():
    html = client.get("/pedidos/novo").text

    assert "<h1>Novo pedido</h1>" in html
    assert ">Voltar</a>" in html
    for nome in ("cliente", "contato", "item_produto", "item_quantidade", "item_valor"):
        assert f'name="{nome}"' in html
    for opcao in ("PIX", "Dinheiro", "Cartão"):
        assert f'type="radio" name="pagamento" value="{opcao}"' in html
    for opcao in ("Entrega em mãos", "Retirada"):
        assert f'type="radio" name="entrega" value="{opcao}"' in html
    assert 'name="observacoes"' in html
    assert 'id="resumo-quantidade"' in html
    assert 'id="resumo-total"' in html
    assert ">Cancelar</a>" in html
    assert ">Salvar pedido</button>" in html


@pytest.mark.parametrize(
    "removido",
    [
        'name="email"',
        'name="data"',
        'name="prazo"',
        'name="status"',
        'name="desconto"',
        'name="frete"',
        'name="personalizado"',
        'name="personalizacao"',
        "Pedido personalizado",
        "Tipo de entrega",
        "Status inicial",
        "E-mail",
        "Desconto",
        "Frete",
    ],
)
def test_campos_removidos_nao_aparecem(removido):
    assert removido not in client.get("/pedidos/novo").text


def test_pagamento_e_entrega_iniciam_sem_selecao():
    assert " checked" not in client.get("/pedidos/novo").text


def test_icones_sao_svg_inline_sem_biblioteca_externa():
    html = client.get("/pedidos/novo").text

    assert html.count('<svg class="escolha-icone"') >= 5
    assert "<link" in html and html.count("<link") == 1  # só o app.css
    assert "cdn" not in html.lower()


def test_tela_tem_recursos_para_adicionar_e_remover_itens():
    html = client.get("/pedidos/novo").text

    assert 'id="adicionar-item"' in html
    assert '<template id="item-modelo">' in html
    assert "item-remover" in html
    assert client.get("/static/js/pedido-novo.js").status_code == 200


def test_home_leva_para_novo_pedido():
    assert 'href="/pedidos/novo"' in client.get("/").text


# ---------- Formatação ----------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("", ""),
        ("1", "(1"),
        ("11", "(11"),
        ("119", "(11) 9"),
        ("1198765", "(11) 98765"),
        ("11987654321", "(11) 98765-4321"),
        ("119876543210000", "(11) 98765-4321"),
        ("(11) 98765-4321", "(11) 98765-4321"),
    ],
)
def test_formatar_contato(texto, esperado):
    assert formatar_contato(texto) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("R$ 1.234,56", Decimal("1234.56")),
        ("R$\xa01.234,56", Decimal("1234.56")),
        ("1234,56", Decimal("1234.56")),
        ("0,05", Decimal("0.05")),
        ("abc", None),
    ],
)
def test_parse_moeda(texto, esperado):
    assert parse_moeda(texto) == esperado


def test_formatar_brl():
    assert formatar_brl(Decimal("1234.5")) == "R$ 1.234,50"
    assert formatar_brl(Decimal("0")) == "R$ 0,00"
    assert formatar_brl(Decimal("1000000")) == "R$ 1.000.000,00"


# ---------- Validações ----------


@pytest.mark.parametrize(
    ("contato", "erro"),
    [
        ("", "Informe o contato."),
        ("11 9876a-4321", "Use somente números."),
        ("(11) 9876-4321", "Informe DDD e número: (00) 00000-0000."),
        ("119876543210", "Informe DDD e número: (00) 00000-0000."),
    ],
)
def test_contato_invalido(contato, erro):
    assert pedido_de(form_valido(contato=contato)).erros["contato"] == erro


def test_contato_so_numeros_recebe_mascara():
    pedido = pedido_de(form_valido(contato="11987654321"))

    assert pedido.valido
    assert pedido.contato == "(11) 98765-4321"


@pytest.mark.parametrize(
    ("quantidade", "erro"),
    [
        ("", "Informe a quantidade."),
        ("2,5", "Use um número inteiro."),
        ("1.5", "Use um número inteiro."),
        ("-1", "Use um número inteiro."),
        ("abc", "Use um número inteiro."),
        ("0", "Quantidade mínima é 1."),
    ],
)
def test_quantidade_deve_ser_inteira_e_minimo_1(quantidade, erro):
    assert erros_do_item(item_quantidade=quantidade) == {"quantidade": erro}


@pytest.mark.parametrize(
    ("valor", "erro"),
    [
        ("", "Informe o valor unitário."),
        ("R$ -5,00", "Valor não pode ser negativo."),
        ("abc", "Valor inválido."),
    ],
)
def test_valor_unitario_obrigatorio_e_nao_negativo(valor, erro):
    assert erros_do_item(item_valor=valor) == {"valor_unitario": erro}


def test_produto_obrigatorio():
    assert erros_do_item(item_produto="", item_valor="R$ 5,00") == {"produto": "Informe o produto."}


@pytest.mark.parametrize("campo", ["pagamento", "entrega"])
def test_pagamento_e_entrega_obrigatorios(campo):
    assert campo in pedido_de(form_valido(**{campo: ""})).erros
    assert campo in pedido_de(form_valido(**{campo: "Boleto"})).erros


def test_pelo_menos_um_item():
    pedido = pedido_de(form_valido(item_produto=[], item_quantidade=[], item_valor=[]))

    assert pedido.erros["itens"] == "Adicione pelo menos um item."


def test_linha_em_branco_unica_mostra_erros_nos_campos():
    pedido = pedido_de(form_valido(item_produto=[""], item_quantidade=["1"], item_valor=[""]))

    assert "itens" not in pedido.erros
    assert pedido.itens[0].erros == {
        "produto": "Informe o produto.",
        "valor_unitario": "Informe o valor unitário.",
    }


# ---------- Cálculos ----------


def test_subtotal_por_item_quantidade_e_total():
    pedido = pedido_de(form_valido())

    assert pedido.valido
    assert [item.subtotal for item in pedido.itens] == [Decimal("71.00"), Decimal("1200.00")]
    assert pedido.quantidade_total == 3
    assert pedido.total == Decimal("1271.00")


def test_varios_itens_e_item_removido():
    # Linhas removidas na tela não são enviadas; linhas em branco são ignoradas.
    dados = form_valido(
        item_produto=["A", "", "B", "C"],
        item_quantidade=["1", "1", "3", "10"],
        item_valor=["R$ 10,00", "", "R$ 2,50", "R$ 0,05"],
    )
    pedido = pedido_de(dados)

    assert pedido.valido
    assert [item.produto for item in pedido.itens] == ["A", "B", "C"]
    assert pedido.quantidade_total == 14
    assert pedido.total == Decimal("18.00")


# ---------- Envio ----------


def test_envio_invalido_mostra_erros_junto_aos_campos():
    resposta = client.post("/pedidos/novo", data={"cliente": "", "contato": ""})

    assert resposta.status_code == 422
    html = resposta.text
    assert "Corrija os campos destacados" in html
    assert '<p class="campo-erro" id="cliente-erro">Informe o nome do cliente.</p>' in html
    assert '<p class="campo-erro" id="contato-erro">Informe o contato.</p>' in html
    assert "Adicione pelo menos um item." in html
    assert '<p class="campo-erro" id="pagamento-erro">Escolha a forma de pagamento.</p>' in html
    assert '<p class="campo-erro" id="entrega-erro">Escolha a forma de entrega.</p>' in html
    assert "validado com sucesso" not in html


def test_envio_invalido_mantem_valores_e_selecoes():
    resposta = client.post("/pedidos/novo", data=form_valido(cliente="", entrega="Entrega em mãos"))

    html = resposta.text
    assert resposta.status_code == 422
    assert 'value="Caneca personalizada"' in html
    assert 'value="(11) 98765-4321"' in html
    assert 'value="PIX" checked' in html
    assert 'value="Entrega em mãos" checked' in html


def test_envio_valido_mostra_sucesso():
    resposta = client.post("/pedidos/novo", data=form_valido(observacoes="Embalar para presente"))

    assert resposta.status_code == 200
    html = resposta.text
    assert 'class="alerta alerta-sucesso"' in html
    assert "Pedido de Maria Souza validado com sucesso — 3 itens, total R$ 1.271,00." in html
    # O formulário volta limpo para um novo lançamento.
    assert 'value="Maria Souza"' not in html
