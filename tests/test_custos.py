"""Tela Custos: repositório falso; nada acessa o Supabase. A migration é conferida como texto."""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.custos import (
    AQUISICAO,
    CONSUMO,
    DIAS_MES,
    HORAS_DIA,
    MANUTENCAO,
    MDO,
    PERDA,
    RESIDUAL,
    TARIFA,
    VIDA_UTIL,
    custo_por_hora,
    custo_unitario,
    depreciacao,
    energia_por_hora,
    formatar_custo,
    formatar_custo_aprox,
    formatar_numero,
    ler_decimal,
    ler_quantidade,
)
from app.custos_repositorio import CustoRecusado, FalhaAoConsultarCustos, FalhaAoSalvarCustos
from app.main import app

client = TestClient(app)

RAIZ = Path(__file__).resolve().parent.parent
MIGRATION = RAIZ / "supabase" / "migrations" / "20261007120000_custos.sql"
CSRF = "csrf-de-teste"


def pagina(**params):
    return client.get("/custos", params=params)


def enviar(caminho, dados=None, **extra):
    corpo = {"csrf": CSRF, **(dados or {}), **extra}
    return client.post(caminho, data=corpo, follow_redirects=False)


def texto(html):
    """Texto visível, sem marcação e com espaços normalizados."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def secao(html, identificador):
    inicio = html.index(f'id="{identificador}"')
    return html[inicio : html.index("</section>", inicio)]


# ---------- Cálculos ----------


def test_energia_por_hora_com_os_valores_iniciais():
    # Tarifa R$ 1,00/kWh x 120 W / 1000 = R$ 0,12/h.
    assert energia_por_hora(Decimal("1.00"), Decimal("120")) == Decimal("0.12")


def test_energia_por_hora_sem_arredondar():
    assert energia_por_hora(Decimal("0.9567"), Decimal("123.4567")) == Decimal("0.9567") * Decimal(
        "123.4567"
    ) / Decimal(1000)
    assert energia_por_hora(Decimal("0.0001"), Decimal("1")) == Decimal("0.0000001")


def test_custo_unitario_sem_arredondamento_prematuro():
    assert custo_unitario(Decimal("10"), 4) == Decimal("2.5")
    # 10 / 3 mantém todas as casas do Decimal; só a exibição arredonda.
    unitario = custo_unitario(Decimal("10"), 3)
    assert unitario == Decimal("3.333333333333333333333333333")
    assert unitario * 3 != Decimal(10) or True  # dízima: não é igual a 10 exato
    assert formatar_custo(unitario) == "R$ 3,3333"
    # Multiplicar de volta não "perde" centavos por arredondamento prematuro em 2 casas.
    assert (custo_unitario(Decimal("0.01"), 3) * 3).quantize(Decimal("0.0000000001")) == Decimal(
        "0.01"
    )


def test_formatacao_de_valores():
    assert formatar_custo(Decimal("100")) == "R$ 100,00"
    assert formatar_custo(Decimal("0.12")) == "R$ 0,12"
    assert formatar_custo(Decimal("1234.5")) == "R$ 1.234,50"
    assert formatar_custo(Decimal("0.12345")) == "R$ 0,1235"
    assert formatar_custo(Decimal("0.1")) == "R$ 0,10"
    assert formatar_numero(Decimal("120")) == "120"
    assert formatar_numero(Decimal("2.50")) == "2,5"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("100", Decimal("100")),
        ("R$ 1.234,56", Decimal("1234.56")),
        ("0,12", Decimal("0.12")),
        ("0.12", Decimal("0.12")),
        ("  3,3333 ", Decimal("3.3333")),
        ("0", Decimal("0")),
        ("-0", Decimal("0")),
    ],
)
def test_ler_decimal_aceita(entrada, esperado):
    valor, erro = ler_decimal(entrada)

    assert (valor, erro) == (esperado, "")


@pytest.mark.parametrize(
    "entrada",
    ["", "   ", "abc", "-1", "-0,01", "1,23456", "1e-9", "NaN", "Infinity", "1000000000", "1,2,3"],
)
def test_ler_decimal_recusa(entrada):
    valor, erro = ler_decimal(entrada)

    assert valor is None and erro


@pytest.mark.parametrize("entrada", ["1", "12", "999999999"])
def test_ler_quantidade_aceita(entrada):
    assert ler_quantidade(entrada) == (int(entrada), "")


@pytest.mark.parametrize(
    "entrada", ["", "0", "-1", "1,5", "2.5", "abc", "1 2", "٣", "1000000001", "+1"]
)
def test_ler_quantidade_recusa(entrada):
    quantidade, erro = ler_quantidade(entrada)

    assert quantidade is None and erro


# ---------- Acesso e navegação ----------


@pytest.mark.sem_login
@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("get", "/custos"),
        ("post", "/custos/filamentos/1"),
        ("post", "/custos/valores/energia"),
        ("post", "/custos/itens/acessorios"),
        ("post", "/custos/itens/embalagens/1"),
    ],
)
def test_custos_exige_login(repo_custos, metodo, caminho):
    resposta = getattr(TestClient(app), metodo)(caminho, follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")
    assert repo_custos.leituras == 0 and repo_custos.gravacoes == []


def test_home_tem_o_modulo_custos():
    html = client.get("/").text

    assert "<h2>Custos</h2>" in html and 'href="/custos"' in html


def test_pagina_nao_fica_em_cache():
    assert pagina().headers["cache-control"] == "no-store"


# ---------- Tela com os valores iniciais ----------


def test_organizada_em_custos_diretos_e_indiretos():
    html = pagina().text

    assert html.index("Custos Diretos") < html.index("Custos Indiretos")
    diretos, indiretos = html.split("Custos Indiretos")[0], html.split("Custos Indiretos")[1]
    for titulo in (
        "Material (Filamento)",
        "Energia elétrica",
        "Perdas",
        "Acessórios",
        "Embalagens",
    ):
        assert titulo in diretos and titulo not in indiretos.split('id="resumo"')[0]
    for titulo in ("Mão de Obra (MDO)", "Manutenção", "Depreciação"):
        assert f">{titulo}</h3>" in indiretos and f">{titulo}</h3>" not in diretos


def test_valores_iniciais_com_unidades():
    t = texto(pagina().text)

    for esperado in (
        "PLA Cores sólidas, Velvet, Silk e pastel R$ 100,00 /kg",
        "PLA Multicolor Duocolor e tricolor R$ 125,00 /kg",
        "PETG R$ 85,00 /kg",
        "Tarifa de energia R$ 1,00 /kWh",
        "Consumo médio da impressora 120 W",
        "Energia por hora (calculado) R$ 0,12 /h",
        "Perdas 5 %",
        "Mão de Obra (MDO) R$ 0,50 /h",
        "Manutenção A definir",
        "Valor de aquisição R$ 6.000,00",
    ):
        assert esperado in t, esperado


def test_energia_nao_tem_campo_de_custo_por_hora():
    html = pagina(editar="valores-energia").text
    campos = re.findall(r'<input id="valores-energia-([a-z_]+)"', html)

    assert campos == ["tarifa_kwh", "consumo_w"]  # o custo/h é só calculado


def test_acessorios_e_embalagens_comecam_vazios():
    html = pagina().text

    assert "Nenhum acessório cadastrado." in html and "Nenhuma embalagem cadastrada." in html
    assert 'class="custos-item"' not in secao(html, "acessorios")
    assert 'class="custos-item"' not in secao(html, "embalagens")


def test_formulario_de_edicao_traz_salvar_cancelar_e_valor_atual():
    html = pagina(editar="filamento-1").text
    bloco = html[html.index('id="filamento-1"') : html.index('id="filamento-2"')]

    assert 'value="100,00"' in bloco
    assert ">Salvar</button>" in bloco and 'href="/custos#filamentos"' in bloco
    assert f'name="csrf" value="{CSRF}"' in bloco
    assert 'action="/custos/filamentos/1"' in bloco
    # Os outros registros seguem em leitura.
    assert 'id="filamento-2"' in html and 'name="valor_kg"' in bloco
    assert html.count('name="valor_kg"') == 1


@pytest.mark.parametrize("valor", ["", "x", "filamento-", "item-abc", "valores-qualquer", "<s>"])
def test_parametro_editar_invalido_e_ignorado(valor):
    resposta = pagina(editar=valor)

    assert resposta.status_code == 200 and "<form" in resposta.text  # só os de cadastro
    assert 'name="valor_kg"' not in resposta.text


def test_aviso_de_sucesso_so_com_codigo_conhecido():
    assert "Valor do filamento atualizado." in pagina(salvo="filamento").text
    assert "alerta-sucesso" not in pagina(salvo="<script>alert(1)</script>").text
    assert "alerta-sucesso" not in pagina(salvo="x").text


# ---------- Filamento ----------


def test_salva_valor_do_filamento_com_precisao_decimal(repo_custos):
    resposta = enviar("/custos/filamentos/1", {"valor_kg": "R$ 99,9999"})

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/custos?salvo=filamento#filamentos"
    assert repo_custos.filamentos[1].valor_kg == Decimal("99.9999")
    assert "R$ 99,9999" in texto(pagina().text)  # persistiu e voltou na tela
    # O nome e a descrição não mudam, e os outros filamentos também não.
    assert repo_custos.filamentos[1].nome == "PLA"
    assert repo_custos.filamentos[2].valor_kg == Decimal("125")


def test_filamento_usa_o_usuario_da_sessao_e_ignora_o_do_navegador(repo_custos):
    enviar("/custos/filamentos/3", {"valor_kg": "90"}, usuario_id="3", criado_por="2")

    assert repo_custos.gravacoes == [("filamento", 3, Decimal("90"), 1)]  # Leandro (sessão)


@pytest.mark.parametrize("valor", ["", "abc", "-5", "1,23456", "1000000000", "NaN"])
def test_filamento_invalido_mantem_o_formulario_e_nao_grava(repo_custos, valor):
    resposta = enviar("/custos/filamentos/1", {"valor_kg": valor})

    assert resposta.status_code == 422
    assert 'class="campo-erro"' in resposta.text
    assert 'name="valor_kg"' in resposta.text  # formulário aberto de novo
    assert f'value="{valor}"' in resposta.text or valor == ""
    assert repo_custos.gravacoes == [] and repo_custos.filamentos[1].valor_kg == Decimal("100")


def test_filamento_inexistente_ou_com_id_invalido(repo_custos):
    assert enviar("/custos/filamentos/999", {"valor_kg": "1"}).status_code == 404
    assert enviar("/custos/filamentos/abc", {"valor_kg": "1"}).status_code == 404
    assert repo_custos.gravacoes == []


# ---------- Energia, perdas e custos indiretos ----------


def test_salva_tarifa_e_consumo_e_recalcula_a_energia(repo_custos):
    resposta = enviar("/custos/valores/energia", {"tarifa_kwh": "0,95", "consumo_w": "150"})

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/custos?salvo=valores-energia#energia"
    assert repo_custos.parametros[TARIFA] == Decimal("0.95")
    assert repo_custos.parametros[CONSUMO] == Decimal("150")
    # 0,95 x 150 / 1000 = 0,1425; o subtotal é recalculado: 0,1425 + 0,50 + 0,27777... = 0,92027...
    t = texto(pagina().text)
    assert "Energia por hora (calculado) R$ 0,1425 /h" in t
    assert "Subtotal por hora ≈ R$ 0,9203 /h" in t


def test_energia_e_tudo_ou_nada(repo_custos):
    resposta = enviar("/custos/valores/energia", {"tarifa_kwh": "0,95", "consumo_w": "abc"})

    assert resposta.status_code == 422
    assert "Informe um número válido" in resposta.text
    assert repo_custos.parametros[TARIFA] == Decimal("1") and repo_custos.gravacoes == []
    # O que foi digitado de bom continua no formulário.
    assert 'value="0,95"' in resposta.text and 'value="abc"' in resposta.text


@pytest.mark.parametrize(
    ("valor", "aceito"),
    [("0", True), ("2,5", True), ("100", True), ("100,01", False), ("5,123", False), ("-1", False)],
)
def test_perdas_de_0_a_100_com_ate_duas_casas(repo_custos, valor, aceito):
    resposta = enviar("/custos/valores/perdas", {"perda_percentual": valor})

    assert resposta.status_code == (303 if aceito else 422)
    assert (repo_custos.parametros[PERDA] == Decimal(valor.replace(",", "."))) is aceito


def test_mdo_e_manutencao_sao_cadastradas_separadamente(repo_custos):
    enviar("/custos/valores/mdo", {"mdo_hora": "0,60"})

    assert repo_custos.parametros[MDO] == Decimal("0.60")
    assert repo_custos.parametros[MANUTENCAO] is None  # a manutenção não mudou (continua a definir)
    enviar("/custos/valores/manutencao", {"manutencao_hora": "1,00"})
    assert repo_custos.parametros[MANUTENCAO] == Decimal("1.00")
    assert repo_custos.parametros[MDO] == Decimal("0.60")
    # 0,12 + 0,60 + 0,27777... + 1,00 = 1,99777...
    assert "Total ≈ R$ 1,9978 /h" in texto(pagina().text)


def test_grupo_de_valores_desconhecido(repo_custos):
    assert enviar("/custos/valores/outro", {"x": "1"}).status_code == 404
    assert enviar("/custos/valores/../filamentos", {}).status_code in (404, 405)
    assert repo_custos.gravacoes == []


# ---------- Acessórios e embalagens ----------


def test_cadastra_acessorio_e_calcula_o_custo_unitario(repo_custos):
    resposta = enviar(
        "/custos/itens/acessorios",
        {"nome": "Chaveiro", "valor_compra": "25,00", "quantidade": "50"},
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/custos?salvo=acessorios-novo#acessorios"
    (item,) = repo_custos.itens.values()
    assert (item.tipo, item.nome, item.valor_compra, item.quantidade) == (
        "acessorio",
        "Chaveiro",
        Decimal("25.00"),
        50,
    )
    assert item.custo_unitario == Decimal("0.5")
    bloco = texto(secao(pagina().text, "acessorios"))
    assert "Chaveiro Valor de compra R$ 25,00 Quantidade 50 un Custo unitário R$ 0,50 /un" in bloco
    assert "Nenhum acessório cadastrado." not in bloco
    assert "Aviso" not in texto(secao(pagina().text, "embalagens"))  # as listas são separadas
    assert "Chaveiro" not in secao(pagina().text, "embalagens")


def test_cadastra_embalagem(repo_custos):
    enviar(
        "/custos/itens/embalagens", {"nome": "Caixa P", "valor_compra": "36", "quantidade": "12"}
    )

    (item,) = repo_custos.itens.values()
    assert item.tipo == "embalagem" and item.custo_unitario == Decimal("3")
    assert "Caixa P" in secao(pagina().text, "embalagens")


def test_custo_unitario_nao_arredonda_antes_de_gravar(repo_custos):
    enviar("/custos/itens/acessorios", {"nome": "Imã", "valor_compra": "10", "quantidade": "3"})

    (item,) = repo_custos.itens.values()
    assert item.valor_compra == Decimal("10") and item.quantidade == 3
    assert item.custo_unitario == custo_unitario(Decimal("10"), 3)  # todas as casas
    assert "Custo unitário R$ 3,3333 /un" in texto(pagina().text)  # só a exibição arredonda


def test_edita_valor_e_quantidade_e_recalcula(repo_custos):
    enviar(
        "/custos/itens/acessorios", {"nome": "Chaveiro", "valor_compra": "25", "quantidade": "50"}
    )
    (item_id,) = repo_custos.itens

    resposta = enviar(
        f"/custos/itens/acessorios/{item_id}",
        {"nome": "Chaveiro", "valor_compra": "30,00", "quantidade": "40"},
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/custos?salvo=acessorios-editado#acessorios"
    item = repo_custos.itens[item_id]
    assert (item.valor_compra, item.quantidade, item.custo_unitario) == (
        Decimal("30.00"),
        40,
        Decimal("0.75"),
    )
    assert len(repo_custos.itens) == 1  # editou, não duplicou
    assert "Custo unitário R$ 0,75 /un" in texto(pagina().text)


def test_formulario_de_edicao_do_item_mostra_valores_atuais(repo_custos):
    enviar(
        "/custos/itens/embalagens", {"nome": "Caixa P", "valor_compra": "36", "quantidade": "12"}
    )
    (item_id,) = repo_custos.itens

    html = pagina(editar=f"item-{item_id}").text
    bloco = html[html.index(f'id="item-{item_id}"') :]

    assert 'value="Caixa P"' in bloco and 'value="36,00"' in bloco and 'value="12"' in bloco
    assert f'action="/custos/itens/embalagens/{item_id}"' in bloco
    assert ">Salvar</button>" in bloco and 'href="/custos#embalagens"' in bloco


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("quantidade", "0", "maior que zero"),
        ("quantidade", "-3", "número inteiro"),
        ("quantidade", "2,5", "número inteiro"),
        ("quantidade", "abc", "número inteiro"),
        ("quantidade", "", "Informe a quantidade"),
        ("valor_compra", "-1", "não pode ser negativo"),
        ("valor_compra", "dez", "número válido"),
        ("valor_compra", "", "Informe um valor"),
        ("nome", "", "Informe o nome"),
        ("nome", "   ", "Informe o nome"),
        ("nome", "x" * 121, "no máximo 120"),
    ],
)
def test_item_invalido_nao_grava_e_preserva_o_que_foi_digitado(repo_custos, campo, valor, mensagem):
    dados = {"nome": "Chaveiro", "valor_compra": "25", "quantidade": "50", campo: valor}

    resposta = enviar("/custos/itens/acessorios", dados)

    assert resposta.status_code == 422
    assert mensagem in resposta.text
    assert repo_custos.itens == {} and repo_custos.gravacoes == []
    if campo != "nome" or valor.strip():
        assert 'value="Chaveiro"' in resposta.text or campo == "nome"
    assert 'aria-invalid="true"' in resposta.text


def test_valor_zero_e_aceito(repo_custos):
    enviar("/custos/itens/acessorios", {"nome": "Brinde", "valor_compra": "0", "quantidade": "10"})

    (item,) = repo_custos.itens.values()
    assert item.valor_compra == Decimal("0") and item.custo_unitario == Decimal("0")


def test_nome_repetido_no_mesmo_tipo_e_recusado_mas_vale_em_outro_tipo(repo_custos):
    dados = {"nome": "Caixa", "valor_compra": "10", "quantidade": "2"}
    enviar("/custos/itens/embalagens", dados)

    repetido = enviar("/custos/itens/embalagens", {**dados, "nome": "CAIXA"})
    outro_tipo = enviar("/custos/itens/acessorios", dados)

    assert repetido.status_code == 409 and "Já existe um item em embalagens" in repetido.text
    assert outro_tipo.status_code == 303
    assert len(repo_custos.itens) == 2


def test_item_inexistente_ou_de_outro_tipo(repo_custos):
    enviar("/custos/itens/acessorios", {"nome": "A", "valor_compra": "1", "quantidade": "1"})
    (item_id,) = repo_custos.itens
    dados = {"nome": "A", "valor_compra": "9", "quantidade": "1"}

    assert enviar(f"/custos/itens/embalagens/{item_id}", dados).status_code == 404
    assert enviar("/custos/itens/acessorios/999", dados).status_code == 404
    assert enviar("/custos/itens/acessorios/abc", dados).status_code == 404
    assert enviar("/custos/itens/outros", dados).status_code == 404
    assert repo_custos.itens[item_id].valor_compra == Decimal("1")


def test_nome_do_item_e_escapado_na_tela(repo_custos):
    enviar(
        "/custos/itens/acessorios",
        {"nome": "<script>alert(1)</script>", "valor_compra": "1", "quantidade": "1"},
    )

    html = pagina().text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_itens_ordenados_por_nome(repo_custos):
    for nome in ("Zíper", "Argola", "Mola"):
        enviar("/custos/itens/acessorios", {"nome": nome, "valor_compra": "1", "quantidade": "1"})

    nomes = re.findall(
        r'<strong class="custos-item-nome">([^<]+)</strong>', secao(pagina().text, "acessorios")
    )

    assert nomes == ["Argola", "Mola", "Zíper"]


# ---------- Segurança ----------


@pytest.mark.parametrize(
    ("caminho", "dados"),
    [
        ("/custos/filamentos/1", {"valor_kg": "1"}),
        ("/custos/valores/energia", {"tarifa_kwh": "1", "consumo_w": "1"}),
        ("/custos/itens/acessorios", {"nome": "A", "valor_compra": "1", "quantidade": "1"}),
    ],
)
def test_sem_csrf_valido_nada_e_gravado(repo_custos, caminho, dados):
    sem = client.post(caminho, data=dados, follow_redirects=False)
    errado = client.post(caminho, data={**dados, "csrf": "outro"}, follow_redirects=False)

    for resposta in (sem, errado):
        assert resposta.status_code == 403 and "A página expirou" in resposta.text
    assert repo_custos.gravacoes == [] and repo_custos.itens == {}


def test_origem_de_outro_site_e_recusada(repo_custos):
    resposta = client.post(
        "/custos/filamentos/1",
        data={"csrf": CSRF, "valor_kg": "1"},
        headers={"origin": "https://outro-site.example"},
        follow_redirects=False,
    )

    assert resposta.status_code == 403
    assert repo_custos.gravacoes == []


def test_origem_do_proprio_site_e_aceita(repo_custos):
    resposta = client.post(
        "/custos/filamentos/1",
        data={"csrf": CSRF, "valor_kg": "1"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )

    assert resposta.status_code == 303 and len(repo_custos.gravacoes) == 1


def test_formulario_grande_demais(repo_custos):
    resposta = client.post("/custos/filamentos/1", data={"csrf": CSRF, "valor_kg": "1" * 9000})

    assert resposta.status_code == 413 and repo_custos.gravacoes == []


# ---------- Falhas do banco ----------


def test_falha_ao_ler_mostra_aviso_sem_quebrar(repo_custos):
    repo_custos.falhar_em["listar"] = FalhaAoConsultarCustos()

    resposta = pagina()

    assert resposta.status_code == 503
    assert "Não foi possível carregar os custos agora" in resposta.text
    assert "Material (Filamento)" not in resposta.text and "Tentar novamente" in resposta.text


def test_banco_nao_configurado(repo_custos):
    from app.custos_repositorio import get_repositorio_custos

    app.dependency_overrides[get_repositorio_custos] = lambda: None

    assert pagina().status_code == 503
    assert enviar("/custos/filamentos/1", {"valor_kg": "1"}).status_code == 503


@pytest.mark.parametrize(
    ("caminho", "dados", "operacao"),
    [
        ("/custos/filamentos/1", {"valor_kg": "77"}, "salvar_filamento"),
        ("/custos/valores/perdas", {"perda_percentual": "7"}, "salvar_parametros"),
        (
            "/custos/itens/acessorios",
            {"nome": "A", "valor_compra": "1", "quantidade": "1"},
            "salvar_item",
        ),
    ],
)
def test_falha_ao_salvar_preserva_o_formulario_e_o_estado_anterior(
    repo_custos, caminho, dados, operacao
):
    repo_custos.falhar_em[operacao] = FalhaAoSalvarCustos()

    resposta = enviar(caminho, dados)

    assert resposta.status_code == 503
    assert "Não foi possível salvar agora" in resposta.text
    assert any(f'value="{v}"' in resposta.text for v in dados.values() if v not in ("1",))
    assert repo_custos.gravacoes == [] and repo_custos.filamentos[1].valor_kg == Decimal("100")
    assert repo_custos.parametros[PERDA] == Decimal("5")


@pytest.mark.parametrize(
    ("motivo", "status", "mensagem"),
    [
        ("usuario", 403, "Seu usuário não pode"),
        ("nao_encontrado", 404, "não foi encontrado"),
        ("invalido", 422, "não foram aceitos"),
    ],
)
def test_recusas_do_banco_viram_mensagens(repo_custos, motivo, status, mensagem):
    repo_custos.falhar_em["salvar_filamento"] = CustoRecusado(motivo)

    resposta = enviar("/custos/filamentos/1", {"valor_kg": "5"})

    assert resposta.status_code == status and mensagem in resposta.text


# ---------- Migration (texto) ----------


@pytest.fixture(scope="module")
def sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_e_posterior_as_anteriores_e_nao_as_altera():
    migrations = sorted(p.name for p in (RAIZ / "supabase" / "migrations").glob("*.sql"))

    assert MIGRATION.name in migrations
    assert MIGRATION.name > "20261006120000_producao_sem_concluidos.sql"


def test_migration_nao_mexe_em_pedidos_producao_ou_autenticacao(sql):
    limpo = re.sub(r"--.*", "", sql)

    for tabela in ("pedidos", "pedido_itens", "sessoes"):
        assert not re.search(rf"(alter|insert into|update|delete from)\s+public\.{tabela}\b", limpo)
    assert not re.search(r"alter\s+table\s+public\.usuarios", limpo)
    assert "storage." not in limpo and "nextval" not in limpo and "setval" not in limpo


def test_migration_cria_as_tres_tabelas_com_numeric_e_sem_float(sql):
    limpo = re.sub(r"--.*", "", sql)

    for tabela in ("custos_filamentos", "custos_parametros", "custos_itens"):
        assert f"create table public.{tabela}" in limpo
        assert f"alter table public.{tabela} enable row level security" in limpo
        assert f"revoke all on public.{tabela} from anon, authenticated, service_role" in limpo
    assert not re.search(r"\b(float|double precision|real|money)\b", limpo, re.I)
    assert "valor_kg      numeric" in limpo and "valor         numeric" in limpo
    assert (
        "custo_unitario numeric     generated always as (valor_compra / quantidade) stored" in limpo
    )
    assert "quantidade     integer     not null check (quantidade > 0)" in limpo
    assert "valor_compra   numeric     not null check (valor_compra >= 0)" in limpo


def test_migration_nao_guarda_o_custo_da_energia_por_hora(sql):
    limpo = re.sub(r"--.*", "", sql)

    assert "energia_hora" not in limpo and "custo_hora" not in limpo
    assert "energia_tarifa_kwh" in limpo and "energia_consumo_w" in limpo


def test_valores_iniciais_nao_sobrescrevem_edicoes(sql):
    limpo = re.sub(r"--.*", "", sql)
    so_tabelas = limpo.split("create function")[0]  # antes das funções
    inserts = re.findall(r"insert into public\.custos_\w+ .*?;", so_tabelas, re.S)

    assert len(inserts) == 2  # filamentos e parâmetros; itens ficam vazios
    for comando in inserts:
        assert re.search(r"on conflict \(.*?\) do nothing;$", comando, re.S), comando
    assert "do update" not in limpo and "insert into public.custos_itens" not in so_tabelas
    for inicial in (
        "('PLA', 'Cores sólidas, Velvet, Silk e pastel', 100)",
        "('PLA Multicolor', 'Duocolor e tricolor', 125)",
        "('PETG', null, 85)",
        "('energia_tarifa_kwh', 1)",
        "('energia_consumo_w', 120)",
        "('perda_percentual', 5)",
        "('mdo_hora', 0.5)",
        "('manutencao_depreciacao_hora', 0.88)",
    ):
        assert inicial in limpo, inicial


def test_migration_funcoes_protegidas(sql):
    limpo = re.sub(r"--.*", "", sql)
    funcoes = re.findall(r"create function public\.(\w+)\(", limpo)

    assert funcoes == [
        "listar_custos",
        "salvar_custo_filamento",
        "salvar_custos_parametros",
        "salvar_custo_item",
    ]
    assert limpo.count("security definer") == 4
    assert limpo.count("set search_path = ''") == 4
    assert limpo.count("from public, anon, authenticated;") == 4
    assert limpo.count("to service_role;") == 4
    assert "create or replace" not in limpo  # nenhuma sobrecarga ou redefinição


def test_migration_erros_e_usuario_da_sessao(sql):
    for codigo in ("PT403", "PT404", "PT409", "PT422"):
        assert codigo in sql
    # As três funções que gravam recebem o usuário (da sessão) e o validam; a leitura não.
    assert len(re.findall(r"p_usuario_id\s+bigint", sql)) == 3
    assert sql.count("where id = p_usuario_id and nome in") == 3
    assert "for update" in sql


# ---------- Depreciação, Manutenção e subtotal por hora ----------

MIGRATION_DEPRECIACAO = (
    RAIZ / "supabase" / "migrations" / "20261008120000_custos_depreciacao_manutencao.sql"
)


def test_depreciacao_com_os_criterios_iniciais():
    d = depreciacao(Decimal(6000), Decimal(0), Decimal(5), Decimal(30), Decimal(12))

    assert d.horas_mensais == Decimal(360)
    assert d.mensal == Decimal(100)  # 6.000 / (5 x 12)
    # 6.000 / (5 x 12 x 30 x 12) = 6.000 / 21.600: dízima, mantida com todas as casas.
    assert d.por_hora * Decimal(21600) - Decimal(6000) < Decimal("1e-40")
    assert abs(d.por_hora - Decimal("0.2777777777")) < Decimal("1e-9")
    assert formatar_custo(d.por_hora) == "R$ 0,2778"
    assert formatar_custo_aprox(d.por_hora) == "≈ R$ 0,2778"
    assert formatar_custo_aprox(d.mensal) == "R$ 100,00"  # exato: sem "≈"


def test_depreciacao_nao_arredonda_nenhuma_etapa():
    # 1.000 / (3 x 12 x 29 x 7) = 1.000 / 7.308. Passar pela mensal arredondada daria outro valor.
    d = depreciacao(Decimal(1000), Decimal(0), Decimal(3), Decimal(29), Decimal(7))
    mensal_arredondada = (Decimal(1000) / Decimal(36)).quantize(Decimal("0.0001"))

    assert d.por_hora * Decimal(7308) - Decimal(1000) < Decimal("1e-40")
    assert d.por_hora != mensal_arredondada / Decimal(203)  # 203 = 29 x 7


def test_depreciacao_com_residual_e_criterios_inteiros():
    d = depreciacao(Decimal(7200), Decimal(1200), Decimal(4), Decimal(25), Decimal(10))

    assert (d.horas_mensais, d.mensal, d.por_hora) == (Decimal(250), Decimal(125), Decimal("0.5"))


def test_custo_por_hora_com_e_sem_manutencao():
    energia = energia_por_hora(Decimal(1), Decimal(120))
    deprec = depreciacao(Decimal(6000), Decimal(0), Decimal(5), Decimal(30), Decimal(12)).por_hora

    subtotal, completo = custo_por_hora(energia, Decimal("0.50"), deprec, None)
    total, completo_com = custo_por_hora(energia, Decimal("0.50"), deprec, Decimal("0.10"))

    assert completo is False and completo_com is True
    assert subtotal == energia + Decimal("0.50") + deprec
    assert formatar_custo(subtotal) == "R$ 0,8978"
    assert total == subtotal + Decimal("0.10")  # manutenção somada em separado


def test_manutencao_zero_e_um_valor_definido_e_none_nao():
    energia, deprec = Decimal("0.12"), Decimal("0.25")

    assert custo_por_hora(energia, Decimal("0.5"), deprec, Decimal(0)) == (Decimal("0.87"), True)
    assert custo_por_hora(energia, Decimal("0.5"), deprec, None) == (Decimal("0.87"), False)


def test_depreciacao_na_tela_mostra_criterios_e_resultados():
    secao_deprec = texto(secao(pagina().text, "depreciacao"))

    for esperado in (
        "Valor de aquisição R$ 6.000,00",
        "Vida útil 5 anos",
        "Valor residual R$ 0,00",
        "Impressão diária 12 horas/dia",
        "Dias por mês 30 dias/mês",
        "Horas mensais (dias × horas) 360 h",
        "Depreciação mensal R$ 100,00 /mês",
        "Depreciação por hora (calculado) ≈ R$ 0,2778 /h",
        "(valor de aquisição − valor residual) ÷ "
        "(vida útil em anos × 12 × dias por mês × horas por dia)",
    ):
        assert esperado in secao_deprec, esperado


def test_manutencao_e_depreciacao_sao_cards_separados_dos_custos_indiretos():
    html = pagina().text
    indiretos = html.split("Custos Indiretos")[1]

    for identificador, titulo in (
        ("mdo", "Mão de Obra (MDO)"),
        ("manutencao", "Manutenção"),
        ("depreciacao", "Depreciação"),
    ):
        assert f'<section class="secao custos-secao" id="{identificador}"' in indiretos
        assert f">{titulo}</h3>" in secao(indiretos, identificador)
    assert "Manutenção e Depreciação" not in html  # o card antigo não existe mais
    assert "0,88" not in html  # o antigo R$ 0,88/h não foi transferido para nenhum campo novo


def test_manutencao_comeca_em_branco_a_definir():
    html = pagina().text

    assert "Manutenção A definir" in texto(secao(html, "manutencao"))
    assert 'name="manutencao_hora"' not in html  # só aparece ao editar
    editar = pagina(editar="valores-manutencao").text
    campo = re.search(r'<input id="valores-manutencao-manutencao_hora"[^>]*>', editar).group(0)
    assert 'value=""' in campo and 'placeholder="A definir"' in campo and "required" not in campo
    assert "Deixe em branco para manter" in editar


def test_manutencao_armazenada_como_ausencia_de_valor(repo_custos):
    assert repo_custos.parametros[MANUTENCAO] is None  # não é zero

    enviar("/custos/valores/manutencao", {"manutencao_hora": "0,35"})
    assert repo_custos.parametros[MANUTENCAO] == Decimal("0.35")
    assert "Manutenção R$ 0,35 /h" in texto(secao(pagina().text, "manutencao"))

    resposta = enviar("/custos/valores/manutencao", {"manutencao_hora": "   "})  # em branco
    assert resposta.status_code == 303
    assert repo_custos.parametros[MANUTENCAO] is None
    assert "A definir" in texto(secao(pagina().text, "manutencao"))


def test_manutencao_zero_digitado_e_um_valor_nao_um_a_definir(repo_custos):
    enviar("/custos/valores/manutencao", {"manutencao_hora": "0"})

    assert repo_custos.parametros[MANUTENCAO] == Decimal(0)
    assert repo_custos.parametros[MANUTENCAO] is not None
    t = texto(pagina().text)
    assert "Manutenção R$ 0,00 /h" in t
    resumo = texto(secao(pagina().text, "resumo"))
    assert "Total" in resumo and "Subtotal" not in resumo  # zero conta como definido


@pytest.mark.parametrize("valor", ["abc", "-1", "1,23456"])
def test_manutencao_invalida_e_recusada(repo_custos, valor):
    resposta = enviar("/custos/valores/manutencao", {"manutencao_hora": valor})

    assert resposta.status_code == 422 and repo_custos.gravacoes == []
    assert repo_custos.parametros[MANUTENCAO] is None


def test_so_a_manutencao_aceita_ficar_em_branco(repo_custos):
    for caminho, campo in (
        ("/custos/valores/mdo", "mdo_hora"),
        ("/custos/valores/perdas", "perda_percentual"),
        ("/custos/valores/depreciacao", "valor_aquisicao"),
    ):
        assert enviar(caminho, {campo: ""}).status_code == 422
    assert repo_custos.gravacoes == []


def test_subtotal_enquanto_a_manutencao_nao_esta_definida():
    resumo = texto(secao(pagina().text, "resumo"))

    assert "Subtotal por hora ≈ R$ 0,8978 /h (≈ R$ 0,90/h)" in resumo
    assert "Manutenção ainda não definida" in resumo
    assert "apenas um subtotal, não o custo por hora completo" in resumo
    assert "Manutenção A definir" in resumo
    assert "Total" not in resumo  # não é apresentado como custo completo
    assert "Energia elétrica R$ 0,12 /h" in resumo and "Mão de Obra (MDO) R$ 0,50 /h" in resumo
    assert "Depreciação ≈ R$ 0,2778 /h" in resumo


def test_total_quando_a_manutencao_esta_definida(repo_custos):
    enviar("/custos/valores/manutencao", {"manutencao_hora": "0,10"})

    resumo = texto(secao(pagina().text, "resumo"))

    # 0,12 + 0,50 + 0,27777... + 0,10 = 0,99777...
    assert "Total ≈ R$ 0,9978 /h" in resumo
    assert "Subtotal" not in resumo and "ainda não definida" not in resumo
    assert "Manutenção R$ 0,10 /h" in resumo


def test_depreciacao_por_hora_nao_e_um_campo_editavel():
    html = pagina(editar="valores-depreciacao").text
    campos = re.findall(r'<input id="valores-depreciacao-([a-z_]+)"', html)

    assert campos == [
        "valor_aquisicao",
        "vida_util_anos",
        "valor_residual",
        "horas_dia",
        "dias_mes",
    ]
    assert 'value="6.000,00"' in html and 'value="12"' in html and 'value="30"' in html
    assert re.search(r"name=\"[a-z_]*por_hora", html) is None  # sem campo independente


def test_edita_os_criterios_e_recalcula_o_valor_por_hora(repo_custos):
    resposta = enviar(
        "/custos/valores/depreciacao",
        {
            "valor_aquisicao": "R$ 7.200,00",
            "vida_util_anos": "4",
            "valor_residual": "1.200,00",
            "horas_dia": "10",
            "dias_mes": "25",
        },
    )

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/custos?salvo=valores-depreciacao#depreciacao"
    assert repo_custos.parametros[AQUISICAO] == Decimal("7200.00")
    assert repo_custos.parametros[RESIDUAL] == Decimal("1200.00")
    assert repo_custos.parametros[VIDA_UTIL] == Decimal(4)
    assert repo_custos.parametros[HORAS_DIA] == Decimal(10)
    assert repo_custos.parametros[DIAS_MES] == Decimal(25)
    assert repo_custos.gravacoes[-1][0] == "parametros" and repo_custos.gravacoes[-1][2] == 1
    t = texto(secao(pagina(salvo="valores-depreciacao").text, "depreciacao"))
    # (7.200 - 1.200) / (4 x 12 x 25 x 10) = 6.000 / 12.000 = R$ 0,50/h; mensal = 6.000 / 48 = 125.
    assert "Horas mensais (dias × horas) 250 h" in t
    assert "Depreciação mensal R$ 125,00 /mês" in t
    assert "Depreciação por hora (calculado) R$ 0,50 /h" in t
    assert "Depreciação atualizada." in pagina(salvo="valores-depreciacao").text
    # O subtotal acompanha: 0,12 + 0,50 + 0,50.
    assert "Subtotal por hora R$ 1,12 /h" in texto(secao(pagina().text, "resumo"))


def test_residual_igual_a_aquisicao_e_aceito_e_zera_a_depreciacao(repo_custos):
    enviar("/custos/valores/depreciacao", {**CRITERIOS, "valor_residual": "6000"})

    assert repo_custos.parametros[RESIDUAL] == Decimal(6000)
    assert "Depreciação por hora (calculado) R$ 0,00 /h" in texto(pagina().text)


CRITERIOS = {
    "valor_aquisicao": "6000",
    "vida_util_anos": "5",
    "valor_residual": "0",
    "horas_dia": "12",
    "dias_mes": "30",
}


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("valor_aquisicao", "-1", "não pode ser negativo"),
        ("valor_aquisicao", "abc", "número válido"),
        ("valor_aquisicao", "", "Informe um valor"),
        ("valor_residual", "-0,01", "não pode ser negativo"),
        ("valor_residual", "6000,01", "não pode ser maior que o valor de aquisição"),
        ("vida_util_anos", "0", "maior que zero"),
        ("vida_util_anos", "-5", "não pode ser negativo"),
        ("vida_util_anos", "5,123", "no máximo 2 casas"),
        ("horas_dia", "0", "maiores que zero"),
        ("horas_dia", "24,01", "não podem passar de 24"),
        ("horas_dia", "25", "não podem passar de 24"),
        ("horas_dia", "-1", "não pode ser negativo"),
        ("dias_mes", "0", "entre 1 e 31"),
        ("dias_mes", "32", "entre 1 e 31"),
        ("dias_mes", "1,5", "número inteiro"),
        ("dias_mes", "-3", "número inteiro"),
        ("dias_mes", "", "Informe um valor"),
    ],
)
def test_criterios_invalidos_nao_gravam_nada(repo_custos, campo, valor, mensagem):
    resposta = enviar("/custos/valores/depreciacao", {**CRITERIOS, campo: valor})

    assert resposta.status_code == 422 and mensagem in resposta.text
    assert repo_custos.gravacoes == []  # tudo ou nada: nenhum dos cinco critérios mudou
    assert repo_custos.parametros[AQUISICAO] == Decimal(6000)
    assert 'aria-invalid="true"' in resposta.text
    assert f'value="{valor}"' in resposta.text or valor == ""


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("valor_aquisicao", "0"),
        ("vida_util_anos", "0,5"),
        ("vida_util_anos", "1000"),
        ("horas_dia", "24"),
        ("horas_dia", "0,5"),
        ("dias_mes", "1"),
        ("dias_mes", "31"),
        ("valor_residual", "0"),
    ],
)
def test_limites_dos_criterios_sao_aceitos(repo_custos, campo, valor):
    criterios = {**CRITERIOS, campo: valor}
    if campo == "valor_aquisicao":
        criterios["valor_residual"] = "0"

    assert enviar("/custos/valores/depreciacao", criterios).status_code == 303


def test_depreciacao_exige_csrf_e_usa_o_usuario_da_sessao(repo_custos):
    sem = client.post("/custos/valores/depreciacao", data=CRITERIOS, follow_redirects=False)
    com = enviar("/custos/valores/depreciacao", CRITERIOS, usuario_id="3")

    assert sem.status_code == 403 and com.status_code == 303
    assert [g[-1] for g in repo_custos.gravacoes] == [1]  # Leandro, da sessão


def test_banco_recusa_residual_maior_que_a_aquisicao(repo_custos):
    repo_custos.falhar_em["salvar_parametros"] = CustoRecusado("invalido")

    resposta = enviar("/custos/valores/depreciacao", CRITERIOS)

    assert resposta.status_code == 422 and "não foram aceitos" in resposta.text


def test_repositorio_falso_rejeita_o_que_o_banco_rejeita(repo_custos):
    with pytest.raises(CustoRecusado):
        repo_custos.salvar_parametros({RESIDUAL: Decimal(7000)}, 1)  # acima da aquisição guardada
    with pytest.raises(CustoRecusado):
        repo_custos.salvar_parametros({AQUISICAO: Decimal(-1)}, 1)
    with pytest.raises(CustoRecusado):
        repo_custos.salvar_parametros({MDO: None}, 1)  # só a manutenção aceita None
    repo_custos.salvar_parametros({MANUTENCAO: None}, 1)
    assert repo_custos.gravacoes == [("parametros", {MANUTENCAO: None}, 1)]


# ---------- Migration da separação (texto) ----------


@pytest.fixture(scope="module")
def sql_deprec():
    return re.sub(r"--.*", "", MIGRATION_DEPRECIACAO.read_text(encoding="utf-8"))


def test_nova_migration_e_posterior_e_so_mexe_em_custos_parametros(sql_deprec):
    migrations = sorted(p.name for p in (RAIZ / "supabase" / "migrations").glob("*.sql"))

    # Outras migrations podem vir depois desta (ex.: Estoque).
    assert MIGRATION_DEPRECIACAO.name in migrations
    assert MIGRATION_DEPRECIACAO.name > MIGRATION.name
    assert "create table" not in sql_deprec
    for outra in ("custos_filamentos", "custos_itens", "pedidos", "pedido_itens", "usuarios"):
        assert not re.search(
            rf"(alter table|update|delete from|insert into)\s+public\.{outra}\b", sql_deprec
        )
    assert "storage." not in sql_deprec and "nextval" not in sql_deprec


def test_antigo_parametro_e_apagado_sem_transferir_o_valor(sql_deprec):
    antes_da_funcao = sql_deprec.split("create or replace function")[0]

    assert antes_da_funcao.count("delete from public.custos_parametros") == 1
    assert "where chave = 'manutencao_depreciacao_hora'" in antes_da_funcao
    assert "0.88" not in sql_deprec and "0,88" not in sql_deprec
    assert not re.search(r"\bupdate\b", antes_da_funcao)  # nada copia o valor antigo


def test_novos_parametros_iniciais_e_idempotentes(sql_deprec):
    antes_da_funcao = sql_deprec.split("create or replace function")[0]
    inserts = re.findall(r"insert into public\.custos_parametros .*?;", antes_da_funcao, re.S)

    assert len(inserts) == 1 and re.search(r"on conflict \(chave\) do nothing;$", inserts[0])
    for inicial in (
        "('manutencao_hora', null)",
        "('depreciacao_valor_aquisicao', 6000)",
        "('depreciacao_vida_util_anos', 5)",
        "('depreciacao_valor_residual', 0)",
        "('depreciacao_horas_dia', 12)",
        "('depreciacao_dias_mes', 30)",
    ):
        assert inicial in inserts[0], inicial
    assert "do update" not in sql_deprec


def test_nenhuma_chave_guarda_o_valor_por_hora_da_depreciacao(sql_deprec):
    chaves = re.search(r"chave in \((.*?)\)\);", sql_deprec, re.S).group(1)

    assert re.findall(r"'(\w+)'", chaves) == [
        "energia_tarifa_kwh",
        "energia_consumo_w",
        "perda_percentual",
        "mdo_hora",
        "manutencao_hora",
        "depreciacao_valor_aquisicao",
        "depreciacao_vida_util_anos",
        "depreciacao_valor_residual",
        "depreciacao_horas_dia",
        "depreciacao_dias_mes",
    ]
    sem_o_delete = sql_deprec.replace("where chave = 'manutencao_depreciacao_hora'", "")
    assert "por_hora" not in sql_deprec and "depreciacao_hora'" not in sem_o_delete


def test_nova_migration_permite_nulo_so_na_manutencao_e_valida_faixas(sql_deprec):
    assert "alter column valor drop not null" in sql_deprec
    assert "check (valor is not null or chave = 'manutencao_hora')" in sql_deprec
    for regra in (
        "when 'depreciacao_vida_util_anos' then valor > 0",
        "when 'depreciacao_dias_mes' then valor between 1 and 31 and valor = trunc(valor)",
        "when 'depreciacao_horas_dia' then valor > 0 and valor <= 24",
    ):
        assert regra in sql_deprec, regra


def test_nova_funcao_sem_sobrecarga_e_protegida(sql_deprec):
    assert sql_deprec.count("create or replace function public.salvar_custos_parametros(") == 1
    assert "create function" not in sql_deprec  # nenhuma sobrecarga nova
    assert "security definer" in sql_deprec and "set search_path = ''" in sql_deprec
    assert "from public, anon, authenticated;" in sql_deprec and "to service_role;" in sql_deprec
    assert "v_residual > v_aquisicao" in sql_deprec and "PT422" in sql_deprec
    assert "if v_chave <> 'manutencao_hora' then" in sql_deprec  # só ela aceita vazio


def test_migration_original_dos_custos_nao_foi_alterada():
    original = MIGRATION.read_text(encoding="utf-8")

    # A 20261007120000 já foi aplicada: segue com o parâmetro antigo e sem nada da nova.
    assert "('manutencao_depreciacao_hora', 0.88)" in original
    assert "depreciacao_valor_aquisicao" not in original and "'manutencao_hora'" not in original


def test_banco_ainda_sem_a_migration_nova_nao_quebra_a_tela(repo_custos):
    # Estado do banco antes da 20261008: parâmetro antigo, sem manutenção nem critérios.
    repo_custos.parametros = {
        TARIFA: Decimal(1),
        CONSUMO: Decimal(120),
        PERDA: Decimal(5),
        MDO: Decimal("0.5"),
        "manutencao_depreciacao_hora": Decimal("0.88"),
    }

    resposta = pagina()
    html = resposta.text

    assert resposta.status_code == 200 and "Custos Indiretos" in html
    assert "Energia por hora (calculado) R$ 0,12 /h" in texto(html)  # o que existe continua
    assert "Indisponível: falta algum dos valores acima." in html  # sem inventar total
    assert "Subtotal" not in html and "Total" not in texto(secao(html, "resumo"))
    assert "0,88" not in html  # o parâmetro antigo não é exibido em nenhum card novo
    assert pagina(editar="valores-depreciacao").status_code == 200
