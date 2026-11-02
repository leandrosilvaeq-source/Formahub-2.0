"""Tela Custos (administração e gestão de custos): fórmulas, regras e telas.

Repositórios falsos (custos e estoque); nada acessa o Supabase. As migrations são conferidas
como texto aqui e executadas de verdade em tests/test_custos_sql_local.py.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.custos import (
    AQUISICAO,
    CONSUMO_KWH,
    ENERGIA_MANUAL,
    HORAS_MES,
    MANUTENCAO,
    MDO,
    MEI,
    PERDA,
    RESIDUAL,
    RETIRADA,
    TARIFA,
    TAXA_FIXA,
    TAXA_PERCENTUAL,
    VIDA_UTIL,
    CustoHora,
    aplicar_perda,
    custo_medio_unidade_lotes,
    depreciacao,
    energia_por_hora,
    formatar_custo,
    formatar_custo_aprox,
    formatar_numero,
    insumos_filamento,
    insumos_itens,
    ler_decimal,
    manutencao_por_hora,
    mdo_referencias,
    revisao_das_horas,
    subtotal_maquina,
    taxa_de_venda,
)
from app.custos_repositorio import (
    CustoRecusado,
    FalhaAoConsultarCustos,
    FalhaAoSalvarCustos,
    GastoManutencao,
    PeriodoManutencao,
)
from app.estoque_repositorio import ItemEstoque, get_repositorio_estoque
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)

RAIZ = Path(__file__).resolve().parent.parent
MIGRATION = RAIZ / "supabase" / "migrations" / "20261019120000_custos_gestao.sql"
CSRF = "csrf-de-teste"


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def pagina(**params):
    return client.get("/custos", params=params)


def enviar(caminho, dados=None, **extra):
    corpo = {"csrf": CSRF, **(dados or {}), **extra}
    return client.post(caminho, data=corpo, follow_redirects=False)


def texto(html):
    """Texto visível, sem marcação e com espaços normalizados."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def cartao(html, identificador):
    """Texto de um card (<li>) da tela, do id até o fim do próprio card."""
    inicio = html.index(f'id="{identificador}"')
    return texto(html[inicio : html.index("\n</li>", inicio)])


def secao(html, identificador):
    inicio = html.index(f'id="{identificador}"')
    return texto(html[inicio : html.index("</section>", inicio)])


# ---------- Fórmulas (Decimal, sem arredondar) ----------


def test_energia_consumo_mensal_vezes_tarifa_sobre_horas():
    valor = energia_por_hora(Decimal("40"), Decimal("1.0393"), Decimal("305"))

    casas = Decimal("1e-20")  # sem arredondar na conta: as primeiras 20 casas são as exatas
    assert valor.quantize(casas) == (Decimal("41.572") / Decimal("305")).quantize(casas)
    assert formatar_custo_aprox(valor) == "≈ R$ 0,1363"
    assert valor.quantize(Decimal("0.0001")) == Decimal("0.1363")


def test_energia_acompanha_as_horas_mensais():
    # Menos horas impressas no mês: o mesmo consumo se divide por menos horas.
    assert energia_por_hora(Decimal("40"), Decimal("1"), Decimal("200")) == Decimal("0.2")


def test_depreciacao_com_os_criterios_iniciais():
    resultado = depreciacao(Decimal("5000"), Decimal("0"), Decimal("5"), Decimal("305"))

    assert resultado.por_hora.quantize(Decimal("1e-20")) == (Decimal(5000) / 18300).quantize(
        Decimal("1e-20")
    )
    assert formatar_custo_aprox(resultado.por_hora) == "≈ R$ 0,2732"
    assert formatar_custo_aprox(resultado.mensal) == "≈ R$ 83,3333"


def test_depreciacao_com_residual_e_sem_arredondar_etapas():
    resultado = depreciacao(Decimal("5000"), Decimal("500"), Decimal("4"), Decimal("200"))

    assert resultado.por_hora == Decimal("0.46875")  # 4.500 / (4 x 12 x 200)
    assert resultado.mensal == Decimal("93.75")  # 4.500 / 48


def test_depreciacao_nao_usa_mais_dias_nem_horas_por_dia():
    import inspect

    assert list(inspect.signature(depreciacao).parameters) == [
        "aquisicao",
        "residual",
        "vida_util_anos",
        "horas_mes",
    ]


def test_manutencao_sem_dados_e_a_definir_nao_zero():
    periodo = PeriodoManutencao(date(2026, 10, 1), date(2026, 10, 31), Decimal("300"))
    gasto = GastoManutencao(1, date(2026, 10, 5), "Bico", Decimal("30"))

    assert manutencao_por_hora([], None) is None
    assert manutencao_por_hora([gasto], None) is None  # gastos sem período
    assert manutencao_por_hora([], periodo) is None  # período sem gastos: não vira zero
    assert manutencao_por_hora([gasto], periodo) == Decimal("0.1")


def test_manutencao_so_conta_gastos_do_periodo_e_divide_pelas_horas_dele():
    periodo = PeriodoManutencao(date(2026, 10, 1), date(2026, 10, 31), Decimal("250"))
    gastos = [
        GastoManutencao(1, date(2026, 10, 1), "Bico", Decimal("40.50")),  # primeiro dia
        GastoManutencao(2, date(2026, 10, 31), "Correia", Decimal("84.50")),  # último dia
        GastoManutencao(3, date(2026, 9, 30), "Fora (antes)", Decimal("999")),
        GastoManutencao(4, date(2026, 11, 1), "Fora (depois)", Decimal("999")),
    ]

    assert manutencao_por_hora(gastos, periodo) == Decimal("125") / Decimal("250")


def test_subtotal_da_maquina_sem_e_com_manutencao():
    assert subtotal_maquina(Decimal("0.14"), Decimal("0.27"), None) == (Decimal("0.41"), False)
    assert subtotal_maquina(Decimal("0.14"), Decimal("0.27"), Decimal("0.10")) == (
        Decimal("0.51"),
        True,
    )
    # Manutenção zero digitada é um valor (completo); None é ausência.
    assert subtotal_maquina(Decimal("0.14"), Decimal("0.27"), Decimal(0)) == (Decimal("0.41"), True)
    assert subtotal_maquina(None, Decimal("0.27"), Decimal("0.1")) == (None, False)


def test_mdo_referencias_de_0_10_20_e_30_minutos():
    assert mdo_referencias(Decimal("15")) == [
        (0, Decimal("0")),
        (10, Decimal("2.5")),
        (20, Decimal("5")),
        (30, Decimal("7.5")),
    ]


def test_taxa_de_venda_fixo_mais_percentual():
    assert taxa_de_venda(Decimal("200"), Decimal("0"), Decimal("0")) == 0
    assert taxa_de_venda(Decimal("200"), Decimal("2.50"), Decimal("4")) == Decimal("10.50")


def test_revisao_trimestral_das_horas():
    base = date(2026, 11, 1)

    assert revisao_das_horas(base, date(2026, 11, 2)) == (date(2027, 1, 30), False)
    assert revisao_das_horas(base, date(2027, 1, 30)) == (date(2027, 1, 30), False)
    assert revisao_das_horas(base, date(2027, 1, 31)) == (date(2027, 1, 30), True)
    assert revisao_das_horas(None, date(2027, 1, 31)) == (None, False)


def test_custo_hora_origem_e_valor_utilizado():
    calculado = CustoHora(Decimal("0.1363"))
    manual = CustoHora(Decimal("0.1363"), Decimal("0.14"), provisorio=True)
    sem_dados = CustoHora(None)

    assert (calculado.usado, calculado.origem) == (Decimal("0.1363"), "Calculado")
    assert (manual.usado, manual.origem) == (Decimal("0.14"), "Manual")
    assert (sem_dados.usado, sem_dados.origem) == (None, None)
    # Manual zero é um ajuste (não cai no calculado).
    assert CustoHora(Decimal("1"), Decimal(0)).usado == 0


def test_formatacao_e_leitura_de_valores():
    assert formatar_custo(Decimal("1234.5")) == "R$ 1.234,50"
    assert formatar_custo(Decimal("0.1363")) == "R$ 0,1363"
    assert formatar_numero(Decimal("305")) == "305"
    assert ler_decimal("1,0393") == (Decimal("1.0393"), "")
    assert ler_decimal("1,23456")[1] != ""


# ---------- Insumos: médias ponderadas e perda ----------


def lote(**dados):
    from tests.fake_estoque import RepositorioEstoqueMemoria as Repo

    repo = Repo()
    repo.com_filamento(**dados)
    return next(iter(repo.filamentos.values()))


def test_filamento_media_ponderada_pelo_peso_liquido_nao_media_simples():
    lotes = [
        lote(tipo="solido", peso_comprado_g=Decimal("3000"), valor_unitario=Decimal("90")),
        lote(tipo="solido", peso_comprado_g=Decimal("1000"), valor_unitario=Decimal("130")),
    ]

    solido = next(i for i in insumos_filamento(lotes, {}) if i.chave == "filamento:pla-solido")

    assert solido.calculado == Decimal("100")  # (3 x 90 + 1 x 130) / 4; a média simples daria 110
    assert (solido.comprado, solido.compras) == (4, 2)


def test_pla_nao_mistura_solido_velvet_silk_e_multicolor():
    lotes = [
        lote(tipo="solido", valor_unitario=Decimal("90")),
        lote(tipo="velvet", valor_unitario=Decimal("100")),
        lote(tipo="silk", valor_unitario=Decimal("130")),
        lote(tipo="bicolor", valor_unitario=Decimal("140")),
        lote(tipo="tricolor", valor_unitario=Decimal("160")),
        lote(material="PETG", tipo="silk", valor_unitario=Decimal("70")),
    ]

    custos = {i.titulo: i.calculado for i in insumos_filamento(lotes, {})}

    assert custos == {
        "PETG": Decimal("70"),  # PETG reúne todos os tipos
        "PLA Sólido": Decimal("90"),
        "PLA Velvet": Decimal("100"),
        "PLA Silk": Decimal("130"),
        "PLA Multicolor": Decimal("150"),  # bicolor + tricolor (pesos iguais)
    }


def test_materiais_adicionais_ganham_o_seu_card_de_filamento():
    lotes = [
        lote(tipo="solido", valor_unitario=Decimal("90")),
        lote(material="TPU", tipo="velvet", valor_unitario=Decimal("200")),
        lote(material="TPU", tipo="silk", valor_unitario=Decimal("100")),
    ]

    insumos = insumos_filamento(lotes, {"filamento:material:TPU": Decimal("180")})

    assert [i.titulo for i in insumos] == [
        "PETG", "PLA Sólido", "PLA Velvet", "PLA Silk", "PLA Multicolor", "TPU",
    ]  # fmt: skip
    tpu = insumos[-1]
    assert (tpu.chave, tpu.calculado, tpu.manual) == (
        "filamento:material:TPU",
        Decimal("150"),
        Decimal("180"),
    )


def test_filamento_peso_comprado_nao_se_confunde_com_saldo():
    consumido = lote(
        tipo="solido",
        peso_g=Decimal("100"),
        peso_comprado_g=Decimal("2000"),
        valor_unitario=Decimal("100"),
    )

    pla = next(i for i in insumos_filamento([consumido], {}) if i.chave == "filamento:pla-solido")

    assert (pla.comprado, pla.saldo) == (2, Decimal("0.1"))
    assert pla.calculado == Decimal("100")  # o uso do estoque não muda o custo


def test_filamento_custo_por_grama_e_ajuste_manual_sem_tocar_nas_compras():
    petg = lote(material="PETG", valor_unitario=Decimal("80"))

    calculado = next(i for i in insumos_filamento([petg], {}) if i.chave == "filamento:petg")
    ajustado = next(
        i
        for i in insumos_filamento([petg], {"filamento:petg": Decimal("90")})
        if i.chave == "filamento:petg"
    )

    assert (calculado.usado, calculado.origem) == (Decimal("80"), "Calculado")
    assert (ajustado.usado, ajustado.origem) == (Decimal("90"), "Manual")
    assert ajustado.calculado == Decimal("80")  # o calculado segue visível como referência
    assert ajustado.usado / 1000 == Decimal("0.09")


def test_perda_e_aplicada_uma_vez_sobre_o_custo():
    assert aplicar_perda(Decimal("100"), Decimal("3")) == Decimal("103")
    assert aplicar_perda(Decimal("100"), Decimal("0")) == Decimal("100")
    # Duas aplicações seriam outra conta: a tela só aplica uma.
    assert aplicar_perda(aplicar_perda(Decimal("100"), Decimal("3")), Decimal("3")) != Decimal(
        "103"
    )


def test_acessorios_e_embalagens_agrupados_por_nome_com_media_ponderada():
    def item(nome, categoria, comprada, total, saldo):
        return ItemEstoque(1, categoria, nome, saldo, date(2026, 9, 1), comprada, total)

    itens = [
        item("Imã", "acessorio", 10, Decimal("30"), 4),
        item("imã ", "acessorio", 30, Decimal("30"), 30),  # mesmo nome: um só insumo
        item("Caixa P", "embalagem", 4, Decimal("10"), 4),
    ]

    resultado = insumos_itens(itens, {"embalagem:Caixa P": Decimal("3")})

    (ima,) = resultado["acessorio"]
    (caixa,) = resultado["embalagem"]
    assert ima.calculado == Decimal("60") / 40  # total pago / total de unidades
    assert (ima.comprado, ima.saldo, ima.compras) == (40, 34, 2)
    assert (caixa.calculado, caixa.manual, caixa.usado) == (Decimal("2.5"), Decimal("3"), 3)
    assert custo_medio_unidade_lotes([]) is None


# ---------- Acesso e navegação ----------


@pytest.mark.sem_login
@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("get", "/custos"),
        ("post", "/custos/valores/energia"),
        ("post", "/custos/valores/energia-ajuste/calculado"),
        ("post", "/custos/insumos/ajuste"),
        ("post", "/custos/insumos/calculado"),
        ("post", "/custos/manutencao/periodo"),
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


def test_pagina_nao_fica_em_cache(estoque):
    assert pagina().headers["cache-control"] == "no-store"


# ---------- Organização e valores iniciais ----------


def test_blocos_na_ordem_certa(estoque):
    html = pagina().text

    for titulo in ("Custos diretos", "Custos indiretos", "Despesas fixas e metas gerenciais"):
        assert f">{titulo}</h2>" in html
    assert (
        html.index('id="custos-diretos"')
        < html.index('id="filamentos"')
        < html.index('id="insumos"')
        < html.index('id="energia"')
        < html.index('id="perdas"')
        < html.index('id="mdo"')
        < html.index('id="custos-indiretos"')
        < html.index('id="depreciacao"')
        < html.index('id="manutencao"')
        < html.index('id="fixos-metas"')
        < html.index('id="mei"')
        < html.index('id="retirada"')
        < html.index('id="substituidos"')
    )


def test_cada_item_mostra_classificacao_unidade_origem_e_criterio(estoque):
    html = pagina().text

    for identificador in ("mei", "retirada", "horas"):
        bloco = secao(html, identificador)
        assert "Unidade:" in bloco and "Critério:" in bloco and "Origem:" in bloco
    for identificador in ("energia", "perdas", "mdo", "depreciacao", "manutencao"):  # cards
        bloco = cartao(html, identificador)
        assert "Unidade " in bloco and "Critério:" in bloco and "Origem " in bloco
        assert "Classificação " in bloco
    assert "Custo direto · máquina" in cartao(html, "energia")
    assert "Custo indireto · máquina" in cartao(html, "depreciacao")
    assert "Custo indireto · máquina" in cartao(html, "manutencao")
    assert "Custo direto · mão de obra" in cartao(html, "mdo")
    assert "Despesa fixa indireta" in secao(html, "mei")
    assert "Meta gerencial (não é custo)" in secao(html, "retirada")


def test_energia_fica_em_outros_custos_diretos_e_nao_na_secao_de_filamentos(estoque):
    html = pagina().text

    assert "Energia elétrica" not in secao(html, "filamentos")
    assert (
        html.index('id="outros-diretos"') < html.index('id="energia"') < html.index('id="perdas"')
    )
    assert "Taxa de perda</strong>" in html and ">MDO</strong>" in html
    assert "Mão de obra direta (manual)" not in html


def test_entrega_e_taxas_de_venda_saem_da_interface_mas_os_dados_ficam(estoque, repo_custos):
    html = pagina().text

    assert 'id="entrega"' not in html and 'id="taxas"' not in html
    assert "Entrega paga pela Forma" not in html and "Taxas de venda" not in html
    assert "Despesas variáveis de venda" not in html
    # Os parâmetros continuam gravados e a rota continua válida (nada foi apagado).
    assert repo_custos.parametros["taxa_venda_fixa"] == Decimal("0")
    resposta = enviar("/custos/valores/taxas", {"taxa_fixa": "2,50", "taxa_percentual": "3"})
    assert resposta.status_code == 303
    assert repo_custos.parametros["taxa_venda_fixa"] == Decimal("2.50")


def test_todo_card_editavel_tem_lapis_acessivel_com_dica_editar(estoque):
    estoque.com_filamento(tipo="solido")
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))
    html = pagina().text

    for identificador in (
        "energia", "perdas", "mdo", "depreciacao", "manutencao", "horas", "mei",
        "retirada", "insumo-1",
    ):  # fmt: skip
        trecho = html[html.index(f'id="{identificador}"') :]
        lapis = re.search(r'<a class="custos-lapis"[^>]*>', trecho).group(0)
        assert 'title="Editar"' in lapis and 'aria-label="Editar ' in lapis, identificador
        assert "?editar=" in lapis
    assert 'aria-label="Editar a energia elétrica"' in html
    assert 'aria-label="Editar o custo de Imã"' not in html  # a tabela de itens não tem Editar


def test_energia_mostra_calculado_e_ajuste_manual_provisorio(estoque):
    bloco = cartao(pagina().text, "energia")

    principal = bloco.split("Ver detalhes")[0]
    assert "Energia elétrica R$ 0,14 /h de máquina" in principal  # valor utilizado
    assert "Calculado (referência) ≈ R$ 0,1363 /h" in bloco
    assert "Origem Manual · provisório" in bloco
    assert "Voltar ao calculado" in bloco
    assert "40 kWh/mês" in bloco and "R$ 1,0393 /kWh" in bloco


def test_depreciacao_mostra_calculado_e_ajuste_manual_provisorio(estoque):
    bloco = cartao(pagina().text, "depreciacao")

    assert "Calculado (referência) ≈ R$ 0,2732 /h" in bloco
    assert "R$ 0,27 /h Revisar custos: valor manual abaixo do custo calculado" in bloco
    assert "Origem Manual · provisório" in bloco
    assert "R$ 5.000,00" in bloco and "5 anos" in bloco
    assert "dias" not in bloco and "horas/dia" not in bloco


def test_horas_mensais_com_data_base_e_aviso_trimestral(estoque):
    bloco = secao(pagina().text, "horas")

    assert "305 h/mês" in bloco and "Data-base 01/11/2026" in bloco
    assert "Revise esta referência a cada trimestre" in bloco
    assert "compartilhada por energia e depreciação" in bloco


def test_aviso_de_revisao_vencida(estoque, repo_custos):
    repo_custos.datas[HORAS_MES] = date(2020, 1, 1)

    assert "Revisão trimestral vencida em 31/03/2020" in secao(pagina().text, "horas")


def test_mdo_15_por_hora_com_referencias_e_sem_o_antigo_valor_de_impressao(estoque):
    bloco = secao(pagina().text, "mdo")

    assert "R$ 15,00 /h" in bloco
    for minutos, valor in ((0, "R$ 0,00"), (10, "R$ 2,50"), (20, "R$ 5,00"), (30, "R$ 7,50")):
        assert f"{minutos} min {valor}" in bloco
    assert "preparação, acabamento, montagem e embalagem" in bloco
    assert "0,50" not in bloco


def test_perda_inicial_de_3_por_cento_so_no_filamento(estoque):
    html = pagina().text

    assert "Taxa de perda 3 %" in cartao(html, "perdas")
    assert "só sobre o custo do filamento" in cartao(html, "perdas")
    assert "perda" not in cartao(html, "energia").lower()


def test_mei_e_retirada_ficam_separados(estoque):
    html = pagina().text

    assert "R$ 100,00 /mês" in secao(html, "mei")
    assert "R$ 500,00 /mês" in secao(html, "retirada")
    total = secao(html, "total-mensal")
    assert "Custos fixos indiretos R$ 100,00 /mês" in total
    assert "Meta de retirada (à parte) R$ 500,00 /mês" in total


def test_subtotal_da_maquina_e_total_mensal_separados_sem_somar_unidades(estoque):
    html = pagina().text
    resumo = secao(html, "resumo")

    assert "Subtotal parcial da máquina R$ 0,41 /h" in resumo
    assert "Manutenção A definir" in resumo
    assert "MDO" in resumo and "R$ 15" not in resumo  # a MDO não entra no subtotal
    assert "R$ 100" not in resumo and "R$ 500" not in resumo  # R$/mês fora do R$/h
    assert "Total mensal de custos fixos" in html


def test_valores_substituidos_sao_explicados(estoque):
    bloco = secao(pagina().text, "substituidos")

    assert "5% → 3%" in bloco
    assert "120 W" in bloco and "12 h/dia × 30 dias" in bloco and "R$ 0,50" in bloco
    assert "Pedidos e registros históricos não foram alterados" in bloco


def test_nao_ha_cadastro_proprio_de_filamento_acessorio_ou_embalagem(estoque):
    html = pagina().text

    assert "Cadastrar acessório" not in html and "Cadastrar embalagem" not in html
    assert 'action="/custos/itens' not in html and 'action="/custos/filamentos' not in html
    for caminho in ("/custos/filamentos/1", "/custos/itens/acessorios"):
        assert enviar(caminho, {"valor_kg": "1"}).status_code == 404


# ---------- Insumos na tela (vêm de Compras/Estoque) ----------


def card(html, n):
    inicio = html.index(f'id="insumo-{n}"')
    return html[inicio : html.index("</li>", inicio)]


def test_filamento_mostra_calculado_por_kg_e_por_grama_nos_detalhes(estoque):
    estoque.com_filamento(tipo="solido", peso_g=Decimal("500"), peso_comprado_g=Decimal("2000"))
    estoque.com_filamento(
        tipo="solido",
        peso_g=Decimal("1000"),
        peso_comprado_g=Decimal("1000"),
        valor_unitario=Decimal("130"),
    )

    detalhes = texto(card(pagina().text, 1)).split("Ver detalhes")[1]

    assert "Custo/kg calculado R$ 110,00 /kg" in detalhes  # (2 x 100 + 1 x 130) / 3, não 115
    assert "Custo/g calculado R$ 0,11 /g" in detalhes
    assert "Média ponderada das compras" in detalhes


def test_valor_principal_e_so_o_manual_sem_manual_fica_a_definir(estoque, repo_custos):
    estoque.com_filamento(tipo="solido", valor_unitario=Decimal("90"))
    estoque.com_filamento(tipo="silk", valor_unitario=Decimal("130"))
    repo_custos.ajustes["filamento:pla-solido"] = Decimal("95")
    html = pagina().text

    solido = texto(card(html, 1)).split("Ver detalhes")[0]
    silk = texto(card(html, 3)).split("Ver detalhes")[0]

    assert "R$ 95,00 /kg" in solido and "A definir" not in solido
    assert "A definir" in silk and "R$ 130" not in silk  # o calculado não entra no lugar
    assert "Custo/kg calculado R$ 90,00 /kg" in texto(card(html, 1))  # o manual não o altera


def test_card_de_filamento_sem_origem_e_sem_dados_mostra_travessao(estoque):
    html = pagina().text
    filamentos = html[html.index('id="filamentos"') : html.index('id="insumos"')]

    assert "Origem:" not in filamentos
    assert "A definir" in texto(card(html, 2)).split("Ver detalhes")[0]
    assert "Custo/kg calculado —" in texto(card(html, 2))
    assert "Custo/g calculado —" in texto(card(html, 2))
    assert "<table" not in filamentos


def test_detalhes_do_filamento_dizem_o_uso_real_do_custo(estoque):
    html = pagina().text

    petg = texto(card(html, 0))
    solido = texto(card(html, 1))
    assert "ainda não é usado em pedidos nem em precificação" in petg
    assert "comparação com a média dos cards de filamento em Estoque" in petg
    assert "comparação" not in solido  # só PETG e PLA Multicolor são comparados no Estoque


def test_filamentos_titulo_sem_etiquetas_e_cards_na_ordem_pedida(estoque):
    estoque.com_filamento(material="TPU", tipo="velvet", valor_unitario=Decimal("200"))

    html = pagina().text
    secao_html = html[html.index('id="filamentos"') : html.index('id="insumos"')]
    nomes = re.findall(r'class="custos-fil-nome">(.*?)</strong>', secao_html)

    # O título da seção não aparece na tela (fica só para leitores de tela).
    assert 'id="titulo-filamentos" class="visualmente-oculto">Filamentos</h4>' in html
    cabecalho = secao_html[: secao_html.index("custos-cartoes")]
    assert "custos-etiqueta" not in cabecalho  # sem "Custo direto" nem "Unidade"
    assert nomes == ["PLA Sólido", "PLA Velvet", "PLA Silk", "PLA Multicolor", "PETG", "TPU"]
    assert 'id="insumo-1"' in secao_html and secao_html.index('id="insumo-1"') < secao_html.index(
        'id="insumo-0"'
    )  # a ordem é só visual: o id de cada card não muda


def test_cinco_cards_fixos_mais_materiais_adicionais_sem_tabela(estoque):
    estoque.com_filamento(material="TPU", tipo="velvet", valor_unitario=Decimal("200"))

    html = pagina().text

    filamentos = html[html.index('id="filamentos"') : html.index('id="insumos"')]
    assert filamentos.count('class="custos-item custos-fil"') == 6
    assert ">TPU</strong>" in html and "custos-tabela-filamentos" not in html


def linhas_da_tabela(html, indice):
    # O fim da tabela externa (a janela de histórico de cada linha tem uma tabela própria).
    tabelas = re.findall(
        r'<table class="tabela-pedidos custos-tabela-insumos">.*?\n  </table>\n</div>', html, re.S
    )
    return tabelas[indice]


def test_acessorios_e_embalagens_em_duas_tabelas_sem_cards(estoque):
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))
    estoque.com_item("Caixa P", categoria="embalagem", quantidade=4, valor_total=Decimal("10"))

    html = pagina().text
    acessorios, embalagens = linhas_da_tabela(html, 0), linhas_da_tabela(html, 1)

    secao_itens = html[html.index('id="insumos"') : html.index('id="outros-diretos"')]
    assert "custos-cartoes" not in secao_itens  # nada de cards
    assert '<li class="custos-item' not in secao_itens
    for tabela in (acessorios, embalagens):
        cabecalho = tabela.split("</thead>")[0]
        colunas = [texto(c) for c in re.findall(r'<th scope="col"[^>]*>(.*?)</th>', cabecalho)]
        assert colunas == ["Foto", "Item", "Quantidade", "Custo unitário"]
    assert "Imã" in texto(acessorios) and "10 un" in texto(acessorios)
    assert "R$ 3,00" in texto(acessorios)
    assert "Caixa P" in texto(embalagens) and "4 un" in texto(embalagens)
    assert "R$ 2,50" in texto(embalagens)
    assert 'class="tabela-rolagem"' in html and "custos-lapis" not in acessorios
    assert html.count('class="custos-categoria"') == 2


def test_foto_de_referencia_vira_miniatura_com_previa_e_url_assinada(estoque):
    estoque.com_item("Imã", valor_total=Decimal("30"), imagem_caminho="compras/1/ima.png")
    estoque.com_item("Caixa P", categoria="embalagem", imagem_caminho="compras/1/sumiu.png")
    estoque.com_item("Fita")  # sem foto registrada
    estoque.imagens["compras/1/ima.png"] = b"x"  # a de "Caixa P" não existe mais no armazenamento

    html = pagina().text
    ima = linhas_da_tabela(html, 0)
    linhas = ima.split("<tr id=")
    fita = next(linha for linha in linhas if "Fita" in linha)
    ima = next(linha for linha in linhas if "Imã" in linha)

    assert 'class="foto-previa-botao"' in ima and 'aria-haspopup="dialog"' in ima
    assert 'aria-label="Ampliar a foto de Imã"' in ima and 'title="Ampliar foto"' in ima
    assert "https://storage.exemplo/assinada/compras/1/ima.png?token=t" in ima  # URL privada
    assert 'alt="Foto de referência: Imã"' in ima and 'width="48"' in ima
    assert "foto-previa-botao" not in fita and "custos-sem-foto" in fita  # marcador discreto
    caixa = linhas_da_tabela(html, 1)
    assert "foto-previa-botao" not in caixa and "custos-sem-foto" in caixa
    assert "js/foto-previa.js" in html and "js/custos-itens.js" in html


def test_foto_usa_a_primeira_compra_com_imagem_do_mesmo_item(estoque):
    estoque.com_item("Imã", imagem_caminho=None)
    estoque.com_item("imã", imagem_caminho="compras/2/ima.png")  # mesmo item, outra compra
    estoque.imagens["compras/2/ima.png"] = b"x"

    tabela = linhas_da_tabela(pagina().text, 0)

    assert tabela.count("<tr id=") == 1 and "compras/2/ima.png" in tabela


def test_tabela_continua_sem_as_fotos_se_o_armazenamento_falhar(estoque):
    estoque.com_item("Imã", imagem_caminho="compras/1/ima.png")
    estoque.imagens["compras/1/ima.png"] = b"x"
    estoque.falhar_em.add("assinar")

    resposta = pagina()

    assert resposta.status_code == 200
    tabela = linhas_da_tabela(resposta.text, 0)
    assert "Imã" in tabela and "custos-sem-foto" in tabela and "foto-previa-botao" not in tabela


def test_ajuste_manual_antigo_fica_guardado_mas_nao_substitui_a_media(estoque, repo_custos):
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))
    repo_custos.ajustes["acessorio:Imã"] = Decimal("9")

    tabela = linhas_da_tabela(pagina().text, 0)

    assert "R$ 3,00" in texto(tabela) and "R$ 9,00" not in texto(tabela)
    assert "manual" not in texto(tabela).lower() and "Editar" not in texto(tabela)
    assert repo_custos.ajustes == {"acessorio:Imã": Decimal("9")}  # preservado


def linha(tabela, nome):
    return texto(next(t for t in tabela.split("<tr id=") if nome in t))


def test_custo_unitario_e_media_ponderada_de_todo_o_historico_e_quantidade_e_o_saldo(estoque):
    # Mesmo item em três compras: preços e quantidades diferentes, saldo parcial e um lote
    # já consumido (saldo 0), que continua contando no custo médio.
    estoque.com_item("Imã", quantidade=4, quantidade_comprada=10, valor_total=Decimal("30"))
    estoque.com_item("Imã", quantidade=40, quantidade_comprada=40, valor_total=Decimal("200"))
    estoque.com_item("Imã", quantidade=0, quantidade_comprada=5, valor_total=Decimal("25"))

    tabela = linhas_da_tabela(pagina().text, 0)

    assert tabela.count("<tr id=") == 1  # um item só, três compras
    texto_da_linha = linha(tabela, "Imã")
    assert "44 un" in texto_da_linha  # saldo: 4 + 40 + 0
    # (30 + 200 + 25) / (10 + 40 + 5) = 255 / 55; a média simples dos preços daria R$ 4,33.
    assert "R$ 4,64" in texto_da_linha and "4,33" not in texto_da_linha  # só arredonda na tela


def test_media_ponderada_em_decimal_sem_arredondar_antes(estoque):
    estoque.com_item("Imã", quantidade=3, quantidade_comprada=3, valor_total=Decimal("10"))
    estoque.com_item("Imã", quantidade=7, quantidade_comprada=7, valor_total=Decimal("1"))

    itens = insumos_itens(estoque.listar_estoque().itens, {})["acessorio"]

    assert itens[0].calculado == Decimal(11) / Decimal(10)  # soma dos valores / soma das unidades
    assert (itens[0].comprado, itens[0].saldo) == (10, 10)


def test_acessorios_e_embalagens_do_mesmo_nome_nao_se_misturam(estoque):
    estoque.com_item("Caixa", categoria="acessorio", quantidade=2, valor_total=Decimal("10"))
    estoque.com_item("Caixa", categoria="embalagem", quantidade=5, valor_total=Decimal("5"))

    html = pagina().text
    acessorios, embalagens = linhas_da_tabela(html, 0), linhas_da_tabela(html, 1)

    assert "2 un" in linha(acessorios, "Caixa") and "R$ 5,00" in linha(acessorios, "Caixa")
    assert "5 un" in linha(embalagens, "Caixa") and "R$ 1,00" in linha(embalagens, "Caixa")
    assert acessorios.count("<tr id=") == 1 and embalagens.count("<tr id=") == 1


def test_nome_com_caixa_e_espacos_diferentes_e_o_mesmo_item(estoque):
    estoque.com_item("Imã  grande", quantidade=2, valor_total=Decimal("4"))
    estoque.com_item("imã grande", quantidade=3, valor_total=Decimal("6"))

    tabela = linhas_da_tabela(pagina().text, 0)

    assert tabela.count("<tr id=") == 1
    assert "5 un" in texto(tabela) and "R$ 2,00" in texto(tabela)


def test_cabecalho_da_secao_sem_titulo_etiquetas_e_sem_linha_de_compras(estoque):
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))

    html = pagina().text
    secao_itens = html[html.index('id="insumos"') : html.index('id="outros-diretos"')]
    cabecalho = secao_itens[: secao_itens.index("custos-duas")]

    assert (
        'id="titulo-acessorios" class="visualmente-oculto"' in cabecalho
    )  # título só p/ leitor de tela
    assert "custos-etiqueta" not in cabecalho  # sem Custo direto, Unidade nem Origem
    assert "custos-item-origem" not in secao_itens and "un compradas" not in secao_itens
    assert "Custo unitário</th>" in secao_itens and "R$/un" not in secao_itens.split("<tbody>")[0]


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (Decimal("13.5"), "R$ 13,50"),
        (Decimal("0.0382"), "R$ 0,04"),  # arredondado só na apresentação
        (Decimal("1234.5678"), "R$ 1.234,57"),
        (Decimal("1234567.891"), "R$ 1.234.567,89"),
    ],
)
def test_custo_unitario_em_reais_com_milhar_e_duas_casas(estoque, valor, esperado):
    estoque.com_item("Imã", quantidade=1, quantidade_comprada=1, valor_total=valor)

    tabela = linhas_da_tabela(pagina().text, 0)
    gatilho = re.search(
        r'<button type="button" class="custos-hist-gatilho"[^>]*>(.*?)</button>', tabela, re.S
    )

    assert texto(gatilho.group(1)) == esperado
    assert "/un" not in texto(gatilho.group(0)) and "≈" not in texto(gatilho.group(0))


def test_historico_de_compras_traz_so_data_quantidade_e_valor_da_mais_recente_a_mais_antiga(
    estoque,
):
    from datetime import date

    estoque.com_item("Imã", quantidade=4, quantidade_comprada=10, valor_total=Decimal("30"),
                     data_compra=date(2026, 9, 1))  # fmt: skip
    estoque.com_item("Imã", quantidade=40, quantidade_comprada=40, valor_total=Decimal("1200.5"),
                     data_compra=date(2026, 10, 15))  # fmt: skip
    estoque.com_item("Imã", quantidade=0, quantidade_comprada=5, valor_total=Decimal("25"),
                     data_compra=date(2026, 8, 20))  # fmt: skip

    tabela = linhas_da_tabela(pagina().text, 0)
    dados = tabela[tabela.index("custos-hist-dados") :]
    colunas = [texto(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", dados.split("</thead>")[0])]
    linhas = [
        [texto(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr)]
        for tr in re.findall(
            r"<tr>(.*?)</tr>", dados.split("<tbody>")[1].split("</tbody>")[0], re.S
        )
    ]

    assert colunas == ["Data", "Quantidade", "Valor"]  # só as 3 informações de cada compra
    assert linhas == [
        ["15/10/2026", "40", "R$ 1.200,50"],
        ["01/09/2026", "10", "R$ 30,00"],
        ["20/08/2026", "5", "R$ 25,00"],
    ]


def test_historico_nao_mistura_itens_nem_categorias(estoque):
    estoque.com_item("Caixa", categoria="acessorio", quantidade=2, valor_total=Decimal("12"))
    estoque.com_item("Caixa", categoria="embalagem", quantidade=5, valor_total=Decimal("5"))

    html = pagina().text

    assert "R$ 12,00" in linhas_da_tabela(html, 0) and "R$ 5,00" not in linhas_da_tabela(html, 0)
    assert "R$ 5,00" in linhas_da_tabela(html, 1) and "R$ 12,00" not in linhas_da_tabela(html, 1)


def test_sem_acessorios_ou_embalagens_mostra_mensagem_e_nenhum_dado_ficticio(estoque):
    html = pagina().text

    assert "Nenhum acessório cadastrado nas compras do estoque." in html
    assert "Nenhuma embalagem cadastrada nas compras do estoque." in html
    assert 'class="custos-tabela"' not in html


def test_acessorios_e_embalagens_usam_o_custo_unitario_do_estoque(estoque):
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))
    estoque.com_item("Caixa P", categoria="embalagem", quantidade=4, valor_total=Decimal("10"))

    html = pagina().text

    assert "Imã" in secao(html, "insumos") and "R$ 3,00" in secao(html, "insumos")
    assert "Caixa P" in secao(html, "embalagens") and "R$ 2,50" in secao(html, "embalagens")
    assert "custo unitário × quantidade utilizada" in secao(html, "insumos")


def test_nome_do_insumo_e_escapado(estoque):
    estoque.com_item("<script>alert(1)</script>", quantidade=1)

    html = pagina().text

    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_tela_continua_sem_o_estoque(estoque):
    estoque.falhar = True

    resposta = pagina()

    assert resposta.status_code == 200
    assert "Não foi possível carregar as compras do estoque agora." in resposta.text
    assert 'id="energia"' in resposta.text


# ---------- Ajustes manuais de insumos ----------


def test_ajuste_manual_do_insumo_preserva_as_compras(estoque, repo_custos):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))
    antes = dict(estoque.filamentos)

    resposta = enviar("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "90,50"})

    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/custos?salvo=insumo-ajuste#insumos"
    assert repo_custos.ajustes == {"filamento:petg": Decimal("90.50")}
    assert repo_custos.gravacoes == [("ajuste", "filamento:petg", Decimal("90.50"), 1)]
    assert estoque.filamentos == antes  # compras e estoque intactos
    bloco = secao(pagina().text, "filamentos")
    assert "Origem" not in bloco
    assert "Custo/kg calculado R$ 80,00 /kg" in bloco  # o calculado não muda com o manual
    assert "R$ 90,50 /kg" in texto(card(pagina().text, 0)).split("Ver detalhes")[0]


def test_voltar_ao_calculado_do_insumo(estoque, repo_custos):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))
    repo_custos.ajustes["filamento:petg"] = Decimal("90")

    resposta = enviar("/custos/insumos/calculado", {"chave": "filamento:petg"})

    assert resposta.headers["location"] == "/custos?salvo=insumo-calculado#insumos"
    assert repo_custos.ajustes == {}
    petg = texto(card(pagina().text, 0)).split("Ver detalhes")[0]
    assert "A definir" in petg and "R$ 80" not in petg  # sem manual: não usa o calculado
    assert resposta.status_code == 303


def test_ajuste_de_acessorio_usa_a_chave_do_nome(estoque, repo_custos):
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))

    enviar("/custos/insumos/ajuste", {"chave": "acessorio:Imã", "valor": "4"})

    assert repo_custos.ajustes == {"acessorio:Imã": Decimal("4")}
    assert "R$ 3,00" in secao(pagina().text, "insumos")  # a média das compras, não o ajuste
    assert "R$ 4,00" not in secao(pagina().text, "insumos")


def test_ajuste_de_insumo_inexistente_ou_invalido_nao_grava(estoque, repo_custos):
    estoque.com_filamento(material="PETG")

    assert (
        enviar("/custos/insumos/ajuste", {"chave": "filamento:abs", "valor": "1"}).status_code
        == 404
    )
    assert (
        enviar("/custos/insumos/ajuste", {"chave": "acessorio:X", "valor": "1"}).status_code == 404
    )
    assert (
        enviar("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "-1"}).status_code
        == 422
    )
    assert (
        enviar("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "abc"}).status_code
        == 422
    )
    assert repo_custos.gravacoes == []


def test_formulario_de_ajuste_do_insumo_abre_com_o_valor_atual(estoque):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))
    html = pagina(editar="insumo-0").text

    assert 'action="/custos/insumos/ajuste"' in html
    assert 'name="chave" value="filamento:petg"' in html
    assert 'id="insumo-0-valor"' in html and 'value=""' in html  # sem manual: campo vazio


def test_ajuste_de_insumo_com_estoque_fora_do_ar(estoque, repo_custos):
    estoque.falhar = True

    resposta = enviar("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "1"})

    assert resposta.status_code == 503 and repo_custos.gravacoes == []


# ---------- Edição de parâmetros ----------


def test_formulario_de_edicao_traz_salvar_cancelar_e_valor_atual(estoque):
    html = pagina(editar="valores-energia").text

    assert 'action="/custos/valores/energia"' in html
    assert 'value="40"' in html and 'value="1,0393"' in html
    assert "Salvar" in html and 'href="/custos#energia"' in html


def test_parametro_editar_invalido_e_ignorado(estoque):
    assert "<form" not in secao(pagina(editar="valores-inexistente").text, "mei")


@pytest.mark.parametrize(
    ("grupo", "dados", "esperado"),
    [
        ("perdas", {"perda_percentual": "4,5"}, {PERDA: Decimal("4.5")}),
        ("mdo", {"mdo_hora": "18"}, {MDO: Decimal("18")}),
        ("mei", {"mei_mensal": "105,90"}, {MEI: Decimal("105.90")}),
        ("retirada", {"retirada": "800"}, {RETIRADA: Decimal("800")}),
        ("horas", {"horas_mes": "280"}, {HORAS_MES: Decimal("280")}),
        (
            "taxas",
            {"taxa_fixa": "1,50", "taxa_percentual": "2,5"},
            {TAXA_FIXA: Decimal("1.50"), TAXA_PERCENTUAL: Decimal("2.5")},
        ),
        (
            "energia",
            {"consumo_kwh": "42", "tarifa_kwh": "1,1"},
            {CONSUMO_KWH: Decimal("42"), TARIFA: Decimal("1.1")},
        ),
        (
            "depreciacao",
            {"valor_aquisicao": "6000", "valor_residual": "600", "vida_util_anos": "4"},
            {AQUISICAO: Decimal("6000"), RESIDUAL: Decimal("600"), VIDA_UTIL: Decimal("4")},
        ),
    ],
)
def test_salva_cada_grupo_de_parametros(estoque, repo_custos, grupo, dados, esperado):
    resposta = enviar(f"/custos/valores/{grupo}", dados)

    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith(f"/custos?salvo=valores-{grupo}#")
    assert repo_custos.gravacoes == [("parametros", esperado, 1)]
    assert {c: repo_custos.parametros[c] for c in esperado} == esperado


def test_editar_energia_recalcula_o_calculado_e_mantem_o_manual(estoque):
    enviar("/custos/valores/energia", {"consumo_kwh": "50", "tarifa_kwh": "1,0393"})

    bloco = cartao(pagina().text, "energia")

    assert "Calculado (referência) ≈ R$ 0,1704 /h" in bloco  # 50 x 1,0393 / 305
    principal = bloco.split("Ver detalhes")[0]
    assert "R$ 0,14 /h de máquina" in principal  # o manual continua valendo
    assert "valor manual abaixo do custo calculado" in principal  # 0,14 < 0,1704


def test_mudar_as_horas_recalcula_energia_e_depreciacao(estoque):
    enviar("/custos/valores/horas", {"horas_mes": "200"})
    html = pagina().text

    assert "R$ 0,2079 /h" in secao(html, "energia")  # 40 x 1,0393 / 200
    assert "R$ 0,4167 /h" in secao(html, "depreciacao")  # 5000 / 60 / 200


@pytest.mark.parametrize(
    ("grupo", "dados", "campo"),
    [
        ("perdas", {"perda_percentual": "100,01"}, "perda_percentual"),
        ("mdo", {"mdo_hora": "-1"}, "mdo_hora"),
        ("mdo", {"mdo_hora": ""}, "mdo_hora"),
        ("mei", {"mei_mensal": "abc"}, "mei_mensal"),
        ("horas", {"horas_mes": "0"}, "horas_mes"),
        ("horas", {"horas_mes": "745"}, "horas_mes"),
        ("energia", {"consumo_kwh": "1", "tarifa_kwh": "1,23456"}, "tarifa_kwh"),
        (
            "depreciacao",
            {"valor_aquisicao": "100", "valor_residual": "101", "vida_util_anos": "5"},
            "valor_residual",
        ),
        (
            "depreciacao",
            {"valor_aquisicao": "100", "valor_residual": "0", "vida_util_anos": "0"},
            "vida_util_anos",
        ),
    ],
)
def test_valores_invalidos_nao_gravam_e_preservam_o_digitado(
    estoque, repo_custos, grupo, dados, campo
):
    resposta = enviar(f"/custos/valores/{grupo}", dados)

    assert resposta.status_code == 422
    assert f'id="valores-{grupo}-{campo}-erro"' in resposta.text
    assert repo_custos.gravacoes == []
    assert f'value="{dados[campo]}"' in resposta.text or dados[campo] == ""


def test_grupo_desconhecido_e_404(estoque):
    assert enviar("/custos/valores/outro", {"x": "1"}).status_code == 404


# ---------- Ajuste manual dos custos derivados ----------


def test_ajuste_manual_da_energia_limpa_o_provisorio(estoque, repo_custos):
    resposta = enviar("/custos/valores/energia-ajuste", {"valor": "0,15"})

    assert resposta.status_code == 303
    assert repo_custos.parametros[ENERGIA_MANUAL] == Decimal("0.15")
    bloco = cartao(pagina().text, "energia")
    assert "Origem Manual" in bloco and "provisório" not in bloco
    assert "R$ 0,15 /h de máquina" in bloco.split("Ver detalhes")[0]
    assert "Calculado (referência) ≈ R$ 0,1363 /h" in bloco


def test_voltar_ao_calculado_apaga_o_ajuste_e_nao_zera(estoque, repo_custos):
    resposta = enviar("/custos/valores/energia-ajuste/calculado")

    assert resposta.headers["location"] == "/custos?salvo=calculado-energia-ajuste#energia"
    assert repo_custos.parametros[ENERGIA_MANUAL] is None
    bloco = cartao(pagina().text, "energia")
    assert "Origem Calculado" in bloco and "Voltar ao calculado" not in bloco
    assert "R$ 0,1363 /h de máquina" in bloco.split("Ver detalhes")[0]


def test_voltar_ao_calculado_da_depreciacao_e_o_resumo_acompanha(estoque, repo_custos):
    enviar("/custos/valores/energia-ajuste/calculado")
    enviar("/custos/valores/depreciacao-ajuste/calculado")

    resumo = secao(pagina().text, "resumo")

    assert "Subtotal parcial da máquina ≈ R$ 0,4095 /h" in resumo  # 0,13630 + 0,27322


def test_so_os_derivados_voltam_ao_calculado(estoque, repo_custos):
    assert enviar("/custos/valores/mdo/calculado").status_code == 404
    assert enviar("/custos/valores/mei/calculado").status_code == 404
    assert repo_custos.gravacoes == []


def test_ajuste_manual_invalido(estoque, repo_custos):
    assert enviar("/custos/valores/energia-ajuste", {"valor": "-1"}).status_code == 422
    assert enviar("/custos/valores/energia-ajuste", {"valor": ""}).status_code == 422
    assert repo_custos.parametros[ENERGIA_MANUAL] == Decimal("0.14")


def test_ajuste_manual_zero_e_um_valor(estoque, repo_custos):
    enviar("/custos/valores/energia-ajuste", {"valor": "0"})

    assert repo_custos.parametros[ENERGIA_MANUAL] == 0
    assert "R$ 0,00 /h de máquina" in cartao(pagina().text, "energia").split("Ver detalhes")[0]


# ---------- Manutenção ----------


def test_manutencao_comeca_a_definir_e_fora_do_subtotal(estoque):
    html = pagina().text

    assert "A definir" in cartao(html, "manutencao")
    assert "Ela fica fora do subtotal da máquina" in cartao(html, "manutencao")
    assert "Subtotal parcial" in secao(html, "resumo")


def gastos_das_manutencoes(repo_custos, *gastos):
    """Os gastos vêm das manutenções registradas na tela Manutenção (não de um cadastro aqui)."""
    repo_custos.gastos = [GastoManutencao(i, *g) for i, g in enumerate(gastos, start=1)]


def test_custo_por_hora_vem_das_manutencoes_do_periodo(estoque, repo_custos):
    r1 = enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "250"}
    )
    gastos_das_manutencoes(
        repo_custos,
        (date(2026, 10, 10), "Bico", Decimal("62.50")),
        (date(2026, 10, 20), "Correia", Decimal("62.50")),
    )

    assert r1.headers["location"] == "/custos?salvo=manutencao-periodo#manutencao"
    assert repo_custos.gravacoes == [
        ("periodo", date(2026, 10, 1), date(2026, 10, 31), Decimal("250"), 1)
    ]
    html = pagina().text
    bloco = cartao(html, "manutencao")
    assert "Calculado (referência) R$ 0,50 /h" in bloco  # R$ 125 / 250 h
    assert "Origem Calculado" in bloco
    assert "R$ 125,00 (2 manutenções)" in bloco
    assert "Subtotal da máquina" in secao(html, "resumo")
    assert "Subtotal parcial" not in secao(html, "resumo")


def test_custos_nao_tem_mais_cadastro_proprio_de_gasto_de_manutencao(estoque):
    html = pagina().text

    assert "Registrar gasto de manutenção" not in html and "manutencao-gasto" not in html
    assert 'href="/manutencao"' in html
    assert client.post("/custos/manutencao/gastos", data={"csrf": CSRF}).status_code == 404


def test_horas_paradas_nunca_sao_o_divisor_do_custo_por_hora(estoque, repo_custos):
    # O divisor é a hora impressa do período; o tempo parado é só um indicador da tela Manutenção.
    enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "100"}
    )
    gastos_das_manutencoes(repo_custos, (date(2026, 10, 5), "Peça", Decimal("50")))

    assert "Calculado (referência) R$ 0,50 /h" in cartao(pagina().text, "manutencao")


def test_manutencao_com_custo_zero_e_conhecida_e_a_definir_avisa_subtotal(estoque, repo_custos):
    enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "100"}
    )
    gastos_das_manutencoes(repo_custos, (date(2026, 10, 5), "Limpeza", Decimal("0")))
    repo_custos.manutencoes_sem_custo = (date(2026, 10, 7), date(2026, 8, 1))

    bloco = cartao(pagina().text, "manutencao")

    assert "Calculado (referência) R$ 0,00 /h" in bloco  # zero é um custo conhecido
    assert "1 manutenção do período está com custo “A definir”" in bloco  # a de agosto não conta
    assert "subtotal conhecido" in bloco


def test_periodo_sem_gastos_continua_a_definir_nao_zero(estoque, repo_custos):
    enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "250"}
    )

    bloco = cartao(pagina().text, "manutencao")

    assert "nenhum custo de manutenção registrado dentro do período" in bloco
    assert "Calculado (referência) A definir" in bloco


def test_gasto_fora_do_periodo_nao_entra(estoque, repo_custos):
    enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "100"}
    )
    gastos_das_manutencoes(repo_custos, (date(2026, 8, 1), "Antigo", Decimal("50")))

    bloco = cartao(pagina().text, "manutencao")

    assert "Calculado (referência) A definir" in bloco
    assert "Gastos no período —" in bloco


def test_ajuste_manual_da_manutencao_tem_precedencia(estoque, repo_custos):
    enviar("/custos/valores/manutencao-ajuste", {"valor": "0,30"})

    bloco = cartao(pagina().text, "manutencao")

    assert "Origem Manual" in bloco and bloco.split("Ver detalhes")[0].strip().endswith(
        "R$ 0,30 /h"
    )
    assert "Subtotal da máquina R$ 0,71 /h" in secao(pagina().text, "resumo")
    enviar("/custos/valores/manutencao-ajuste/calculado")
    assert repo_custos.parametros[MANUTENCAO] is None
    assert "A definir" in cartao(pagina().text, "manutencao")


@pytest.mark.parametrize(
    ("dados", "campo"),
    [
        ({"inicio": "2026-10-31", "fim": "2026-10-01", "horas": "10"}, "fim"),
        ({"inicio": "", "fim": "2026-10-01", "horas": "10"}, "inicio"),
        ({"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "0"}, "horas"),
        ({"inicio": "2026-10-01", "fim": "2026-10-31", "horas": "abc"}, "horas"),
    ],
)
def test_periodo_invalido_nao_grava(estoque, repo_custos, dados, campo):
    resposta = enviar("/custos/manutencao/periodo", dados)

    assert resposta.status_code == 422
    assert f'id="manutencao-periodo-{campo}-erro"' in resposta.text
    assert repo_custos.periodo is None


def test_periodo_pode_ter_horas_acima_de_um_mes(estoque, repo_custos):
    resposta = enviar(
        "/custos/manutencao/periodo", {"inicio": "2026-07-01", "fim": "2026-09-30", "horas": "900"}
    )

    assert resposta.status_code == 303 and repo_custos.periodo.horas == Decimal("900")


# ---------- Prevenção de dupla contagem ----------


def test_subtotal_da_maquina_nao_inclui_mdo_perda_nem_valores_mensais(estoque, repo_custos):
    enviar("/custos/valores/mdo", {"mdo_hora": "99"})
    enviar("/custos/valores/perdas", {"perda_percentual": "50"})
    enviar("/custos/valores/mei", {"mei_mensal": "777"})

    resumo = secao(pagina().text, "resumo")

    assert "Subtotal parcial da máquina R$ 0,41 /h" in resumo  # só energia + depreciação


def test_mdo_nao_usa_mais_o_valor_por_hora_de_impressao(estoque):
    html = pagina().text

    assert "R$ 0,50" not in secao(html, "mdo")
    assert "A hora de impressão não gera MDO" in secao(html, "mdo")


def test_perda_so_altera_o_custo_por_grama_do_filamento(estoque):
    estoque.com_filamento(tipo="solido", valor_unitario=Decimal("100"))
    estoque.com_item("Imã", quantidade=10, valor_total=Decimal("30"))
    enviar("/custos/valores/perdas", {"perda_percentual": "10"})
    html = pagina().text

    filamentos = secao(html, "filamentos")
    assert "Custo/g calculado R$ 0,10 /g" in filamentos  # a perda não muda o calculado
    assert "perda" not in secao(html, "insumos").lower()
    assert "R$ 3,00" in secao(html, "insumos")


# ---------- Filamentos: comparação do manual com o calculado e edição no próprio card ----------

FETCH = {"X-Requested-With": "fetch"}


def enviar_fetch(caminho, dados=None, csrf=CSRF):
    corpo = {"csrf": csrf, **(dados or {})}
    return client.post(caminho, data=corpo, headers=FETCH, follow_redirects=False)


def test_comparacao_do_manual_com_o_calculado_em_decimal():
    lotes = [lote(tipo="solido", valor_unitario=Decimal("100"))]  # calculado: R$ 100/kg

    def comparar(manual):
        ajustes = {} if manual is None else {"filamento:pla-solido": Decimal(manual)}
        return next(
            i for i in insumos_filamento(lotes, ajustes) if i.chave == "filamento:pla-solido"
        ).comparacao

    assert comparar("90") == "menor"
    assert comparar("110") == "maior"
    assert comparar("100.0000") == "igual"
    assert comparar(None) is None  # sem manual: sem comparação
    sem_base = insumos_filamento([], {"filamento:petg": Decimal("90")})
    assert sem_base[0].comparacao is None  # sem calculado: sem comparação


def test_comparacao_nao_arredonda_antes_de_comparar():
    lotes = [lote(tipo="solido", valor_unitario=Decimal("100"), peso_rolo_g=Decimal("3000"))]
    exato = insumos_filamento(lotes, {})[1].calculado  # 33,3333... (não termina)

    def comparar(manual):
        i = insumos_filamento(lotes, {"filamento:pla-solido": manual})[1]
        return i.comparacao

    assert formatar_custo_aprox(Decimal("33.3333")).lstrip("≈ ") == formatar_custo_aprox(
        exato
    ).lstrip("≈ ")  # parecem iguais
    assert comparar(Decimal("33.3333")) == "menor"
    assert comparar(Decimal("33.3334")) == "maior"
    assert comparar(exato) == "igual"


def test_cores_e_textos_de_comparacao_no_card(estoque, repo_custos):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))
    estoque.com_filamento(tipo="solido", valor_unitario=Decimal("100"))
    estoque.com_filamento(tipo="velvet", valor_unitario=Decimal("100"))
    estoque.com_filamento(tipo="silk", valor_unitario=Decimal("100"))
    repo_custos.ajustes.update(
        {
            "filamento:petg": Decimal("70"),  # menor
            "filamento:pla-solido": Decimal("120"),  # maior
            "filamento:pla-velvet": Decimal("100"),  # igual
            "filamento:pla-multicolor": Decimal("50"),  # sem base calculada
        }
    )
    html = pagina().text
    cards = [card(html, n) for n in range(5)]

    assert "custos-fil-valor-menor" in cards[0]  # manual 70 < calculado 80: vermelho + aviso
    assert "Revisar custos: valor manual abaixo do custo calculado" in texto(cards[0])
    assert "custos-fil-valor-maior" in cards[1]  # manual 120 > calculado 100: verde, sem alerta
    assert "Acima do custo calculado" in texto(cards[1]) and "Revisar custos" not in texto(cards[1])
    assert "custos-fil-valor-igual" in cards[2] and "Igual ao custo calculado" in cards[2]
    assert "custos-fil-valor-neutro" in cards[3] and "custo calculado" not in texto(
        cards[3]
    )  # sem manual
    assert "custos-fil-valor-neutro" in cards[4] and "custo calculado" not in texto(cards[4])
    assert "Revisar custos" not in "".join(cards[1:])  # só o manual menor pede revisão


def test_materiais_adicionais_tambem_comparam(estoque, repo_custos):
    estoque.com_filamento(material="TPU", tipo="velvet", valor_unitario=Decimal("200"))
    repo_custos.ajustes["filamento:material:TPU"] = Decimal("250")

    tpu = card(pagina().text, 5)

    assert ">TPU</strong>" in tpu and "custos-fil-valor-maior" in tpu and "Revisar" not in tpu
    assert 'data-chave="filamento:material:TPU"' in tpu


def test_salvar_por_fetch_devolve_json_com_o_card_atualizado(estoque, repo_custos):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "90,50"})

    corpo = resposta.json()
    assert resposta.status_code == 200 and corpo["ok"] is True
    assert repo_custos.ajustes == {"filamento:petg": Decimal("90.50")}
    assert repo_custos.gravacoes == [("ajuste", "filamento:petg", Decimal("90.50"), 1)]  # sessão
    html = corpo["html"]
    assert html.lstrip().startswith("<li") and "<html" not in html  # só o card
    assert 'id="insumo-0"' in html and "R$ 90,50" in texto(html)
    assert "custos-fil-valor-maior" in html  # 90,50 > 80: verde, sem alerta
    assert "Revisar custos" not in texto(html) and "Acima do custo calculado" in texto(html)
    assert "Custo/kg calculado R$ 80,00 /kg" in texto(html)  # o calculado não muda


def test_remover_valor_por_fetch(estoque, repo_custos):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))
    repo_custos.ajustes["filamento:petg"] = Decimal("90")

    corpo = enviar_fetch("/custos/insumos/calculado", {"chave": "filamento:petg"}).json()

    assert corpo["ok"] is True and repo_custos.ajustes == {}
    assert "A definir" in texto(corpo["html"]) and "custos-fil-valor-neutro" in corpo["html"]


@pytest.mark.parametrize("valor", ["", "abc", "-1", "1,23456"])
def test_fetch_com_valor_invalido_responde_422_e_nao_grava(estoque, repo_custos, valor):
    estoque.com_filamento(material="PETG")

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": valor})

    assert resposta.status_code == 422 and resposta.json()["ok"] is False
    assert resposta.json()["erro"] and repo_custos.gravacoes == []


def test_fetch_sem_csrf_valido_ou_com_chave_desconhecida_nao_grava(estoque, repo_custos):
    estoque.com_filamento(material="PETG")

    sem_csrf = enviar_fetch(
        "/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "1"}, csrf="falso"
    )
    desconhecida = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:nada", "valor": "1"})

    assert sem_csrf.status_code == 403 and "expirou" in sem_csrf.json()["erro"]
    assert desconhecida.status_code == 404 and desconhecida.json()["ok"] is False
    assert repo_custos.gravacoes == []


def test_fetch_com_falha_do_banco_responde_json_de_erro(estoque, repo_custos):
    estoque.com_filamento(material="PETG")
    repo_custos.falhar_em["salvar_ajuste_insumo"] = FalhaAoSalvarCustos()

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "1"})

    assert resposta.status_code == 503
    assert resposta.json() == {
        "ok": False,
        "salvo": False,
        "erro": "Não foi possível salvar agora. Tente novamente em instantes.",
    }


def test_gravou_mas_a_montagem_do_card_falhou_continua_sendo_sucesso(
    estoque, repo_custos, monkeypatch
):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))

    def quebra(*args, **kwargs):
        raise RuntimeError("detalhe interno")

    monkeypatch.setattr("app.custos_rotas._montar", quebra)

    editado = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "90"})

    assert editado.status_code == 200
    assert editado.json() == {"ok": True, "salvo": True, "html": ""}  # só o visual falhou
    assert repo_custos.ajustes == {"filamento:petg": Decimal("90")}
    assert "detalhe interno" not in editado.text

    removido = enviar_fetch("/custos/insumos/calculado", {"chave": "filamento:petg"})

    assert removido.json() == {"ok": True, "salvo": True, "html": ""}
    assert repo_custos.ajustes == {}


def test_gravou_mas_nao_deu_para_reler_os_custos_continua_sendo_sucesso(estoque, repo_custos):
    estoque.com_filamento(material="PETG", valor_unitario=Decimal("80"))
    leituras = []
    listar = repo_custos.listar

    def listar_uma_vez():  # a leitura que valida a chave passa; a releitura do card falha
        leituras.append(1)
        if len(leituras) > 1:
            raise FalhaAoConsultarCustos
        return listar()

    repo_custos.listar = listar_uma_vez

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "90"})

    assert resposta.json() == {"ok": True, "salvo": True, "html": ""}
    assert repo_custos.ajustes == {"filamento:petg": Decimal("90")}


def test_erro_inesperado_antes_de_gravar_responde_json_e_nao_grava(
    estoque, repo_custos, monkeypatch
):
    estoque.com_filamento(material="PETG")

    def quebra(*args, **kwargs):
        raise RuntimeError("detalhe interno")

    monkeypatch.setattr("app.custos_rotas._escopo_do_insumo", quebra)

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "1"})

    assert resposta.status_code == 500
    assert resposta.json() == {
        "ok": False,
        "salvo": False,
        "erro": "Não foi possível salvar agora. Tente novamente em instantes.",
    }
    assert repo_custos.gravacoes == [] and "detalhe interno" not in resposta.text


def test_recusas_por_fetch_dizem_que_nao_salvaram(estoque, repo_custos):
    estoque.com_filamento(material="PETG")
    repo_custos.falhar_em["salvar_ajuste_insumo"] = FalhaAoSalvarCustos()

    falha = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "1"})
    invalido = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "x"})

    assert falha.json()["salvo"] is False and invalido.json()["salvo"] is False


@pytest.mark.parametrize(
    ("enviado", "esperado"),
    [
        ("0,01", Decimal("0.01")),
        ("1,00", Decimal("1.00")),
        ("100,00", Decimal("100.00")),
        ("1234,56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),  # milhar com ponto
        ("R$ 1.234.567,89", Decimal("1234567.89")),
        ("12,3456", Decimal("12.3456")),  # valor salvo com 4 casas
        ("999999999,99", Decimal("999999999.99")),
        ("100.5", Decimal("100.5")),
    ],
)
def test_valor_da_mascara_vira_o_decimal_exato(estoque, repo_custos, enviado, esperado):
    estoque.com_filamento(material="PETG")

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": enviado})

    assert resposta.json()["ok"] is True
    assert repo_custos.ajustes["filamento:petg"] == esperado
    assert repo_custos.ajustes["filamento:petg"].as_tuple().exponent == esperado.as_tuple().exponent


@pytest.mark.parametrize("enviado", ["1000000000,00", "1,23456", ",", "R$", "1,2,3"])
def test_valor_fora_do_limite_ou_malformado_e_recusado(estoque, repo_custos, enviado):
    estoque.com_filamento(material="PETG")

    resposta = enviar_fetch("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": enviado})

    assert resposta.status_code == 422 and repo_custos.gravacoes == []


def test_card_de_filamento_nao_tem_botao_remover_e_preserva_o_valor_salvo(estoque, repo_custos):
    estoque.com_filamento(tipo="solido")
    repo_custos.ajustes["filamento:pla-solido"] = Decimal("120")

    html = pagina().text
    filamentos = html[html.index('id="filamentos"') : html.index('id="insumos"')]

    assert "Remover" not in filamentos and "/insumos/calculado" not in filamentos
    assert "R$ 120,00" in texto(card(html, 1)) and 'data-valor="120,00"' in card(html, 1)
    assert repo_custos.ajustes == {"filamento:pla-solido": Decimal("120")}


@pytest.mark.sem_login
def test_fetch_sem_sessao_responde_401_json_e_nao_grava(estoque, repo_custos):
    estoque.com_filamento(material="PETG")

    resposta = TestClient(app).post(
        "/custos/insumos/ajuste",
        data={"csrf": CSRF, "chave": "filamento:petg", "valor": "1,00"},
        headers=FETCH,
        follow_redirects=False,
    )

    assert resposta.status_code == 401
    assert resposta.json() == {
        "ok": False,
        "salvo": False,
        "expirou": True,
        "login": "/entrar?next=%2Fcustos",
        "erro": "Sua sessão expirou. Entre de novo; o valor digitado será mantido.",
    }
    assert "formahub_sessao" in resposta.headers["set-cookie"]  # o cookie vencido é apagado
    assert repo_custos.gravacoes == []


@pytest.mark.sem_login
def test_post_comum_sem_sessao_continua_redirecionando_ao_login(estoque, repo_custos):
    cliente = TestClient(app)

    comum = cliente.post(
        "/custos/insumos/ajuste",
        data={"csrf": CSRF, "chave": "filamento:petg", "valor": "1"},
        follow_redirects=False,
    )
    leitura = cliente.get("/custos", headers=FETCH, follow_redirects=False)

    assert comum.status_code == 303 and comum.headers["location"].startswith("/entrar?")
    assert leitura.status_code == 303  # só o POST por fetch recebe JSON
    assert repo_custos.gravacoes == []


def test_sem_o_cabecalho_fetch_a_rota_continua_redirecionando(estoque, repo_custos):
    estoque.com_filamento(material="PETG")

    resposta = enviar("/custos/insumos/ajuste", {"chave": "filamento:petg", "valor": "5"})

    assert resposta.status_code == 303 and "application/json" not in resposta.headers.get(
        "content-type", ""
    )


def test_pagina_carrega_o_script_da_edicao_no_lugar(estoque):
    html = pagina().text

    assert "js/custos-filamentos.js" in html


# ---------- Outros custos diretos: cards como os de filamentos, edição no card ----------


def test_outros_custos_diretos_sao_tres_cards_no_padrao_dos_filamentos(estoque):
    html = pagina().text
    outros = html[html.index('id="outros-cards"') : html.index('id="custos-indiretos"')]

    assert re.findall(r'class="custos-fil-nome">(.*?)</strong>', outros) == [
        "Energia elétrica",
        "Taxa de perda",
        "MDO",
    ]
    assert outros.count('class="custos-item custos-fil"') == 3
    assert "R$ 0,14" in cartao(html, "energia") and "/h de máquina" in cartao(html, "energia")
    assert "R$ 15,00" in cartao(html, "mdo") and "/h ativa" in cartao(html, "mdo")
    assert "3 %" in cartao(html, "perdas")
    for ident in ("energia", "perdas", "mdo"):
        card = html[html.index(f'id="{ident}"') :]
        assert 'data-rota="/custos/valores/' in card and "custos-fil-valor" in card
        assert 'title="Editar"' in card and "Ver detalhes" in card


def test_mdo_mantem_as_referencias_de_tempo_ativo_nos_detalhes(estoque):
    bloco = cartao(pagina().text, "mdo")

    detalhes = bloco.split("Ver detalhes")[1]
    for minutos, valor in ((0, "R$ 0,00"), (10, "R$ 2,50"), (20, "R$ 5,00"), (30, "R$ 7,50")):
        assert f"{minutos} min {valor}" in detalhes
    assert "A hora de impressão não gera MDO" in detalhes


def test_comparacao_so_na_energia_que_tem_base_calculada(estoque, repo_custos):
    html = pagina().text

    assert "custos-fil-valor-maior" in html[html.index('id="energia"') :].split("\n</li>")[0]
    assert "Acima do custo calculado" in cartao(html, "energia")  # manual 0,14 > calculado 0,1363
    for ident in ("perdas", "mdo"):  # sem base calculada equivalente: neutros
        bloco = html[html.index(f'id="{ident}"') :].split("\n</li>")[0]
        assert "custos-fil-valor-neutro" in bloco and "custo calculado" not in bloco

    repo_custos.parametros[ENERGIA_MANUAL] = Decimal("0.10")
    assert "custos-fil-valor-menor" in pagina().text
    assert "Revisar custos: valor manual abaixo do custo calculado" in cartao(
        pagina().text, "energia"
    )
    repo_custos.parametros[ENERGIA_MANUAL] = None  # sem manual: neutro
    assert "custo calculado" not in cartao(pagina().text, "energia").split("Ver detalhes")[0]


@pytest.mark.parametrize(
    ("caminho", "campo", "enviado", "chave", "esperado", "mostra"),
    [
        ("energia-ajuste", "valor", "0,15", ENERGIA_MANUAL, Decimal("0.15"), "R$ 0,15"),
        ("mdo", "mdo_hora", "20,50", MDO, Decimal("20.50"), "R$ 20,50"),
        ("mdo", "mdo_hora", "1.234,56", MDO, Decimal("1234.56"), "R$ 1.234,56"),
        ("perdas", "perda_percentual", "3,5", PERDA, Decimal("3.5"), "3,5 %"),
        ("perdas", "perda_percentual", "0", PERDA, Decimal("0"), "0 %"),
    ],
)
def test_salvar_card_por_fetch_devolve_o_card_atualizado(
    estoque, repo_custos, caminho, campo, enviado, chave, esperado, mostra
):
    resposta = enviar_fetch(f"/custos/valores/{caminho}", {campo: enviado})

    corpo = resposta.json()
    assert resposta.status_code == 200 and corpo["ok"] is True and corpo["salvo"] is True
    assert repo_custos.parametros[chave] == esperado
    assert repo_custos.gravacoes[-1][2] == 1  # usuário da sessão
    assert corpo["html"].lstrip().startswith("<li") and "<html" not in corpo["html"]
    assert mostra in texto(corpo["html"]).split("Ver detalhes")[0]


@pytest.mark.parametrize(
    ("caminho", "campo", "enviado"),
    [
        ("energia-ajuste", "valor", ""),
        ("energia-ajuste", "valor", "abc"),
        ("energia-ajuste", "valor", "1,23456"),
        ("mdo", "mdo_hora", "-1"),
        ("perdas", "perda_percentual", "101"),
        ("perdas", "perda_percentual", "3,555"),
        ("perdas", "perda_percentual", ""),
    ],
)
def test_card_por_fetch_com_valor_invalido_responde_422_e_nao_grava(
    estoque, repo_custos, caminho, campo, enviado
):
    resposta = enviar_fetch(f"/custos/valores/{caminho}", {campo: enviado})

    assert resposta.status_code == 422
    assert resposta.json()["ok"] is False and resposta.json()["salvo"] is False
    assert resposta.json()["erro"] and repo_custos.gravacoes == []


def test_card_por_fetch_sem_csrf_ou_com_falha_do_banco_nao_diz_que_salvou(estoque, repo_custos):
    sem_csrf = enviar_fetch("/custos/valores/mdo", {"mdo_hora": "20"}, csrf="falso")
    repo_custos.falhar_em["salvar_parametros"] = FalhaAoSalvarCustos()
    falha = enviar_fetch("/custos/valores/mdo", {"mdo_hora": "20"})

    assert sem_csrf.status_code == 403 and "expirou" in sem_csrf.json()["erro"]
    assert falha.status_code == 503 and falha.json()["salvo"] is False
    assert repo_custos.parametros[MDO] == Decimal("15") and repo_custos.gravacoes == []


def test_card_por_fetch_gravou_mas_montagem_falhou_continua_sendo_sucesso(
    estoque, repo_custos, monkeypatch
):
    def quebra(*args, **kwargs):
        raise RuntimeError("detalhe interno")

    monkeypatch.setattr("app.custos_rotas._montar", quebra)

    resposta = enviar_fetch("/custos/valores/perdas", {"perda_percentual": "4"})

    assert resposta.json() == {"ok": True, "salvo": True, "html": ""}
    assert repo_custos.parametros[PERDA] == Decimal("4") and "detalhe interno" not in resposta.text


def test_energia_voltar_ao_calculado_por_fetch(estoque, repo_custos):
    resposta = enviar_fetch("/custos/valores/energia-ajuste/calculado")

    assert resposta.json()["ok"] is True
    assert repo_custos.parametros[ENERGIA_MANUAL] is None  # voltou ao calculado, não a zero
    assert "Voltar ao calculado" not in resposta.json()["html"]
    assert "R$ 0,1363" in texto(resposta.json()["html"]).split("Ver detalhes")[0]


def test_rota_do_card_sem_fetch_continua_redirecionando(estoque, repo_custos):
    resposta = enviar("/custos/valores/mdo", {"mdo_hora": "21"})

    assert resposta.status_code == 303 and repo_custos.parametros[MDO] == Decimal("21")


# ---------- Segurança ----------


@pytest.mark.parametrize(
    "caminho",
    [
        "/custos/valores/mei",
        "/custos/valores/energia-ajuste/calculado",
        "/custos/insumos/ajuste",
        "/custos/insumos/calculado",
        "/custos/manutencao/periodo",
        "/custos/valores/depreciacao-ajuste/calculado",
    ],
)
def test_sem_csrf_valido_nada_e_gravado(estoque, repo_custos, caminho):
    resposta = client.post(caminho, data={"csrf": "errado", "mei_mensal": "1"})

    assert resposta.status_code == 403
    assert "A página expirou" in resposta.text and repo_custos.gravacoes == []


def test_origem_de_outro_site_e_recusada(estoque, repo_custos):
    resposta = client.post(
        "/custos/valores/mei",
        data={"csrf": CSRF, "mei_mensal": "1"},
        headers={"origin": "https://outro-site.exemplo"},
    )

    assert resposta.status_code == 403 and repo_custos.gravacoes == []


def test_usuario_vem_da_sessao_e_nao_do_navegador(estoque, repo_custos):
    enviar("/custos/valores/mei", {"mei_mensal": "120", "usuario_id": "3", "p_usuario_id": "3"})

    assert repo_custos.gravacoes == [("parametros", {MEI: Decimal("120")}, 1)]


def test_formulario_grande_demais(estoque, repo_custos):
    resposta = enviar("/custos/valores/mei", {"mei_mensal": "1", "lixo": "x" * 9000})

    assert resposta.status_code == 413 and repo_custos.gravacoes == []


# ---------- Falhas do banco ----------


def test_falha_ao_ler_mostra_aviso_sem_quebrar(estoque, repo_custos):
    repo_custos.falhar_em["listar"] = FalhaAoConsultarCustos()

    resposta = pagina()

    assert resposta.status_code == 503
    assert "Não foi possível carregar os custos agora" in resposta.text
    assert "Tentar novamente" in resposta.text


def test_banco_nao_configurado(estoque):
    from app.custos_repositorio import get_repositorio_custos

    app.dependency_overrides[get_repositorio_custos] = lambda: None

    assert pagina().status_code == 503
    assert enviar("/custos/valores/mei", {"mei_mensal": "1"}).status_code == 503


@pytest.mark.parametrize(
    ("caminho", "dados", "operacao"),
    [
        ("/custos/valores/mei", {"mei_mensal": "1"}, "salvar_parametros"),
        ("/custos/valores/energia-ajuste/calculado", {}, "salvar_parametros"),
        (
            "/custos/manutencao/periodo",
            {"inicio": "2026-10-01", "fim": "2026-10-02", "horas": "1"},
            "definir_periodo_manutencao",
        ),
    ],
)
def test_falha_ao_salvar_preserva_o_formulario_e_o_estado_anterior(
    estoque, repo_custos, caminho, dados, operacao
):
    repo_custos.falhar_em[operacao] = FalhaAoSalvarCustos()
    antes = dict(repo_custos.parametros)

    resposta = enviar(caminho, dados)

    assert resposta.status_code == 503
    assert "Não foi possível salvar agora" in resposta.text
    assert repo_custos.parametros == antes and repo_custos.gravacoes == []


def test_recusas_do_banco_viram_mensagens(estoque, repo_custos):
    repo_custos.falhar_em["salvar_parametros"] = CustoRecusado("usuario")
    assert enviar("/custos/valores/mei", {"mei_mensal": "1"}).status_code == 403

    repo_custos.falhar_em["salvar_parametros"] = CustoRecusado("invalido")
    resposta = enviar("/custos/valores/mei", {"mei_mensal": "1"})
    assert resposta.status_code == 422 and "Os dados não foram aceitos" in resposta.text


def test_aviso_de_sucesso_so_com_codigo_conhecido(estoque):
    assert "Valor atualizado." in pagina(salvo="valores-mei").text
    assert "alert" not in pagina(salvo="<script>").text.replace('role="alert"', "")


# ---------- Migration (conferida como texto) ----------


def test_migration_e_posterior_as_anteriores_e_nao_toca_pedidos_producao_ou_compras():
    nomes = sorted(p.name for p in (RAIZ / "supabase" / "migrations").glob("*.sql"))
    sql = MIGRATION.read_text(encoding="utf-8")
    codigo = re.sub(r"--.*", "", sql)

    assert MIGRATION.name in nomes
    assert MIGRATION.name > "20261018120000_listar_compras_periodo.sql"
    assert not re.search(r"\b(pedidos|producao|compras|compra_itens|estoque_\w+)\b", codigo)
    assert "drop table" not in codigo.lower()
    for antiga in ("custos_parametros", "custos_filamentos", "custos_itens"):
        assert f"delete from public.{antiga}" not in codigo


def test_migration_nao_usa_float_e_guarda_so_ajustes_e_gastos():
    codigo = re.sub(r"--.*", "", MIGRATION.read_text(encoding="utf-8")).lower()

    assert (
        "float" not in codigo
        and "double precision" not in codigo
        and "real" not in re.findall(r"\b\w+\b", codigo)
    )
    for tabela in (
        "custos_ajustes_insumo",
        "custos_manutencao_gastos",
        "custos_manutencao_periodo",
    ):
        assert f"create table public.{tabela}" in codigo
        assert f"alter table public.{tabela} enable row level security" in codigo
        assert f"revoke all on public.{tabela} from anon, authenticated, service_role" in codigo
    # O custo por hora nunca é guardado: sempre calculado (ou ajuste manual explícito).
    assert "energia_hora'" not in codigo and "custo_hora" not in codigo


def test_migration_valores_iniciais_e_substituicoes_so_do_que_nunca_foi_editado():
    sql = MIGRATION.read_text(encoding="utf-8")

    for valor in (
        "('energia_consumo_kwh_mes', 40",
        "('horas_impressas_mes', 305",
        "('energia_hora_manual', 0.14",
        "('depreciacao_hora_manual', 0.27",
        "('mei_mensal', 100",
        "('retirada_desejada_mensal', 500",
        "('taxa_venda_fixa', 0",
        "('taxa_venda_percentual', 0",
    ):
        assert valor in sql
    assert "on conflict (chave) do nothing" in sql
    for chave in (
        "perda_percentual",
        "energia_tarifa_kwh",
        "mdo_hora",
        "depreciacao_valor_aquisicao",
    ):
        trecho = re.search(rf"where chave = '{chave}'[^;]*;", sql).group(0)
        assert "atualizado_por is null" in trecho  # edição manual nunca é sobrescrita
    assert "set valor = 3" in sql and "set valor = 1.0393" in sql
    assert "set valor = 15" in sql and "set valor = 5000" in sql


def test_migration_funcoes_protegidas_e_sem_sobrecarga():
    sql = MIGRATION.read_text(encoding="utf-8")

    for funcao in (
        "listar_custos()",
        "salvar_custos_parametros(jsonb, bigint)",
        "salvar_ajuste_insumo(text, numeric, bigint)",
        "registrar_gasto_manutencao(date, text, numeric, bigint)",
        "definir_periodo_manutencao(date, date, numeric, bigint)",
    ):
        assert (
            f"revoke all on function public.{funcao}\n  from public, anon, authenticated" in sql
            or (f"revoke all on function public.{funcao} from public, anon, authenticated" in sql)
        )
        assert f"grant execute on function public.{funcao}" in sql
    assert sql.count("security definer") == sql.count("set search_path = ''") == 5


def test_migration_original_dos_custos_nao_foi_alterada():
    original = (RAIZ / "supabase" / "migrations" / "20261007120000_custos.sql").read_text(
        encoding="utf-8"
    )

    assert "('perda_percentual', 5)" in original and "('mdo_hora', 0.5)" in original
