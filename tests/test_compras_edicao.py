"""Tela Compras: editar uma compra (POST /compras/{id}/editar) e ordenar a lista pela data.

Repositório falso em memória; nenhum teste acessa o Supabase.
"""

import html as html_lib
import json
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.estoque_repositorio import (
    CompraParaGravar,
    FalhaNoEstoque,
    ItemCompraParaGravar,
    RepositorioEstoqueSupabase,
    get_repositorio_estoque,
)
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria
from tests.test_estoque_compra import JPEG, PNG, _ClienteFalso, compra, enviar, filamento, item

client = TestClient(app)


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def registrar(*linhas, fotos=None, **extra):
    assert enviar(compra(*linhas, **extra), fotos=fotos).status_code == 200


def editar(compra_id, dados: dict, fotos: dict | None = None, **opcoes):
    arquivos = {
        f"item_{i}_foto": (nome, conteudo, "application/octet-stream")
        for i, (nome, conteudo) in (fotos or {}).items()
    }
    return client.post(f"/compras/{compra_id}/editar", data=dados, files=arquivos or None, **opcoes)


def dados_de_edicao(pagina_html: str) -> list[dict]:
    """JSON de cada botão Editar da lista, na ordem da tabela."""
    return [
        json.loads(html_lib.unescape(bruto))
        for bruto in re.findall(r"data-editar-compra='([^']*)'", pagina_html)
    ]


# ---------- Botão Editar na lista ----------


def test_cada_compra_tem_botao_editar_com_os_dados_da_janela(estoque):
    registrar(
        filamento(0, quantidade="2", peso_rolo="500", valor_unitario="R$ 45,90", tipo="velvet"),
        item(
            1,
            "embalagem",
            nome="Caixa <P>",
            quantidade="7",
            valor_total="R$ 3,50",
            cor="Kraft",
            medidas="10x10 cm",
            marca="ignorada",  # embalagem não tem marca: o campo oculto não é gravado
        ),
        fotos={0: ("a.png", PNG)},
        local="loja_fisica",
        local_nome="Papelaria",
        frete="R$ 12,00",
        desconto="R$ 1,50",
    )

    pagina = client.get("/compras").text

    assert pagina.count("data-editar-compra=") == 1
    assert 'aria-label="Editar compra de 10/09/2026 (Loja Física · Papelaria)"' in pagina
    (dados,) = dados_de_edicao(pagina)
    campos = ("id", "data", "local", "local_nome", "frete", "desconto")
    assert {k: dados[k] for k in campos} == {
        "id": 1,
        "data": "10/09/2026",
        "local": "loja_fisica",
        "local_nome": "Papelaria",
        "frete": "12.00",
        "desconto": "1.50",
    }
    fil, emb = dados["itens"]
    assert {k: fil[k] for k in fil if k not in ("foto_atual", "foto_url")} == {
        "categoria": "filamento",
        "quantidade": "2",
        "material": "PLA",
        "tipo": "velvet",
        "peso_rolo": "500",  # o mesmo valor da opção da lista de pesos
        "cor": "Preto",
        "marca": "Marca Teste",
        "valor_unitario": "45.90",
    }
    assert fil["foto_atual"].startswith("compras/1/itens/1/")
    assert fil["foto_url"].startswith("https://storage.exemplo/assinada/compras/1/")
    assert emb == {
        "categoria": "embalagem",
        "quantidade": "7",
        "nome": "Caixa <P>",
        "cor": "Kraft",
        "medidas": "10x10 cm",
        "marca": "",
        "valor_unitario": "0.50",
        "foto_atual": "",
        "foto_url": "",
    }
    assert "Caixa <P>" not in pagina  # o texto vai escapado no atributo


def test_janela_tem_campo_da_foto_mantida_em_cada_linha(estoque):
    pagina = client.get("/compras").text

    assert pagina.count('data-campo="foto_atual"') == 1  # um só modelo de linha


def test_item_antigo_sem_unitario_abre_com_nota_do_total_anterior(estoque):
    """Item gravado antes do valor unitário (só valor total): a janela divide pela quantidade e,
    se a divisão não for exata, a linha avisa o total anterior (nada muda calado)."""
    from tests.fake_estoque import CompraGravada

    estoque._ultimo_id_compra = 1
    antigo = CompraParaGravar(
        id=1,
        chave_envio=uuid.uuid4(),
        data_compra=date(2026, 9, 10),
        local_tipo="shopee",
        local_nome=None,
        usuario_id=1,
        itens=(
            ItemCompraParaGravar(
                ordem=1,
                categoria="embalagem",
                quantidade=3,
                nome="Caixa",
                valor_total=Decimal("10"),
            ),
            ItemCompraParaGravar(
                ordem=2, categoria="acessorio", quantidade=4, nome="Pino", valor_total=Decimal("2")
            ),
        ),
    )
    estoque.compras[1] = CompraGravada(antigo, [])

    (dados,) = dados_de_edicao(client.get("/compras").text)

    caixa, pino = dados["itens"]
    assert caixa["valor_unitario"] == "3.33"
    assert caixa["nota"] == "Valor unitário arredondado: o total anterior da linha era R$ 10,00."
    assert pino["valor_unitario"] == "0.50" and "nota" not in pino


# ---------- Gravação da edição ----------


def test_editar_troca_dados_itens_e_lotes(estoque):
    registrar(filamento(0), item(1, "acessorio"), frete="R$ 5,00")
    chave_original = estoque.compras[1].compra.chave_envio

    resposta = editar(
        1,
        compra(
            item(0, "acessorio", nome="Argola Dourada", quantidade="12", valor_total="R$ 24,00"),
            item(1, "embalagem", nome="Saquinho", quantidade="50"),
            data_compra="2026-09-03",
            local="outro",
            local_nome="Feira",
            frete="",
        ),
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"destino": "/compras?salvo=compra-editada"}
    gravada = estoque.compras[1].compra
    assert (gravada.data_compra, gravada.local_tipo, gravada.local_nome, gravada.frete) == (
        date(2026, 9, 3),
        "outro",
        "Feira",
        Decimal("0"),
    )
    assert gravada.chave_envio == chave_original
    assert [i.nome for i in gravada.itens] == ["Argola Dourada", "Saquinho"]
    assert not estoque.filamentos  # o lote do filamento removido saiu do estoque
    assert sorted((i.nome, i.quantidade) for i in estoque.itens.values()) == [
        ("Argola Dourada", 12),
        ("Saquinho", 50),
    ]
    assert len(estoque.compras) == 1  # não cria outra compra

    pagina = client.get("/compras?salvo=compra-editada").text
    assert "Compra atualizada. O estoque foi ajustado." in pagina


def test_editar_mantem_foto_atual_e_remove_as_que_sairam(estoque):
    registrar(filamento(0), item(1, "acessorio"), fotos={0: ("a.png", PNG), 1: ("b.jpg", JPEG)})
    foto_fil, foto_ace = (i.imagem_caminho for i in estoque.compras[1].compra.itens)

    # O filamento sai; o acessório (agora a linha 0) mantém a sua foto.
    resposta = editar(1, compra(item(0, "acessorio", foto_atual=foto_ace)))

    assert resposta.status_code == 200
    (unico,) = estoque.compras[1].compra.itens
    assert unico.imagem_caminho == foto_ace
    assert estoque.removidas == [foto_fil]
    assert foto_ace in estoque.imagens


def test_editar_troca_a_foto_por_uma_nova(estoque):
    registrar(item(0, "acessorio"), fotos={0: ("a.png", PNG)})
    antiga = estoque.compras[1].compra.itens[0].imagem_caminho

    # A janela limpa foto_atual quando a foto é trocada; mesmo se viesse, a nova vence.
    resposta = editar(1, compra(item(0, "acessorio", foto_atual=antiga)), {0: ("n.jpg", JPEG)})

    assert resposta.status_code == 200
    nova = estoque.compras[1].compra.itens[0].imagem_caminho
    assert nova != antiga and nova.startswith("compras/1/itens/1/") and nova.endswith(".jpg")
    assert estoque.removidas == [antiga]


def test_editar_remove_a_foto(estoque):
    registrar(item(0, "acessorio"), fotos={0: ("a.png", PNG)})
    antiga = estoque.compras[1].compra.itens[0].imagem_caminho

    assert editar(1, compra(item(0, "acessorio"))).status_code == 200

    assert estoque.compras[1].compra.itens[0].imagem_caminho is None
    assert estoque.removidas == [antiga]


@pytest.mark.parametrize(
    "caminho",
    [
        "compras/2/itens/1/11111111-1111-4111-8111-111111111111.png",  # de outra compra
        "../segredo.png",
        "compras/1/itens/1/qualquer.png",
    ],
)
def test_foto_mantida_fora_da_pasta_da_compra_e_ignorada(estoque, caminho):
    registrar(item(0, "acessorio"))

    assert editar(1, compra(item(0, "acessorio", foto_atual=caminho))).status_code == 200

    assert estoque.compras[1].compra.itens[0].imagem_caminho is None


def test_editar_valida_como_no_registro(estoque):
    registrar(item(0, "acessorio"))

    resposta = editar(1, compra(item(0, "acessorio", quantidade="0"), local=""))

    assert resposta.status_code == 422
    assert set(resposta.json()["campos"]) == {"local", "item_0_quantidade"}
    assert estoque.compras[1].compra.itens[0].nome == "Argola Teste"  # nada mudou


def test_editar_compra_inexistente(estoque):
    resposta = editar(9, compra(item(0, "acessorio")), fotos={0: ("a.png", PNG)})

    assert resposta.status_code == 404
    assert resposta.json()["erro"] == "Esta compra não existe mais. Atualize a página."
    assert not estoque.imagens  # a foto enviada nesta tentativa foi removida


@pytest.mark.parametrize("compra_id", ["abc", "-1", "1" * 19])
def test_editar_id_invalido(estoque, compra_id):
    assert editar(compra_id, compra(item(0, "acessorio"))).status_code == 404


def test_falha_ao_editar_nao_muda_nada_e_remove_fotos_novas(estoque):
    registrar(item(0, "acessorio"))
    estoque.falhar_em.add("editar")

    resposta = editar(1, compra(item(0, "acessorio", nome="Outra")), {0: ("n.png", PNG)})

    assert resposta.status_code == 503
    assert estoque.compras[1].compra.itens[0].nome == "Argola Teste"
    assert not estoque.imagens


def test_editar_exige_pagina_valida(estoque):
    registrar(item(0, "acessorio"))

    resposta = editar(1, compra(item(0, "acessorio"), csrf="outro"))

    assert resposta.status_code == 403
    assert estoque.compras[1].compra.itens[0].nome == "Argola Teste"


def test_editar_usa_o_usuario_da_sessao(estoque):
    registrar(item(0, "acessorio"))

    editar(1, compra(item(0, "acessorio"), usuario_id="999"))

    assert estoque.edicoes == [("compra", 1, estoque.compras[1].compra.usuario_id)]
    assert estoque.compras[1].compra.usuario_id != 999


# ---------- Ordenação pela data ----------


def nomes_na_tabela(pagina: str) -> list[str]:
    return re.findall(r"<li>40 un · (\w+)</li>", pagina)


def test_ordena_por_data_nos_dois_sentidos(estoque):
    for nome, data in (("Meio", "10/09/2026"), ("Velha", "01/08/2026"), ("Nova", "05/10/2026")):
        registrar(item(0, "acessorio", nome=nome), data_compra=data)

    recentes = client.get("/compras").text
    antigas = client.get("/compras?ordem=antigas").text

    assert nomes_na_tabela(recentes) == ["Nova", "Meio", "Velha"]
    assert nomes_na_tabela(antigas) == ["Velha", "Meio", "Nova"]
    assert 'aria-sort="descending"' in recentes and 'aria-sort="ascending"' in antigas
    assert 'href="/compras?periodo=todos&amp;ordem=antigas"' in recentes
    assert 'href="/compras?periodo=todos&amp;ordem=recentes"' in antigas
    # O filtro de período mantém a ordem escolhida.
    assert '<input type="hidden" name="ordem" value="antigas">' in antigas
    assert 'name="ordem"' not in recentes


def test_ordem_antigas_vale_para_todas_as_paginas(estoque):
    for dia in range(1, 23):
        registrar(item(0, "acessorio"), data_compra=f"{dia:02d}/09/2026")

    primeira = client.get("/compras?ordem=antigas").text
    segunda = client.get("/compras?ordem=antigas&pagina=2").text

    assert "01/09/2026" in primeira and "22/09/2026" not in primeira
    assert "22/09/2026" in segunda and "01/09/2026" not in segunda


@pytest.mark.parametrize("ordem", ["", "x", "ANTIGAS"])
def test_ordem_invalida_vale_recentes(estoque, ordem):
    registrar(item(0, "acessorio", nome="Velha"), data_compra="01/08/2026")
    registrar(item(0, "acessorio", nome="Nova"), data_compra="05/10/2026")

    pagina = client.get(f"/compras?ordem={ordem}").text

    assert nomes_na_tabela(pagina) == ["Nova", "Velha"]


# ---------- Repositório do Supabase (cliente falso) ----------


def test_repositorio_envia_a_edicao_e_le_as_fotos_removidas():
    cliente = _ClienteFalso({"id": 7, "removidas": ["compras/7/itens/1/a.png"]})
    gravar = CompraParaGravar(
        id=7,
        chave_envio=uuid.uuid4(),
        data_compra=date(2026, 9, 10),
        local_tipo="shopee",
        local_nome=None,
        usuario_id=2,
        itens=(
            ItemCompraParaGravar(
                ordem=1, categoria="acessorio", quantidade=4, nome="A", valor_total=Decimal("2")
            ),
        ),
        frete=Decimal("3.50"),
    )

    assert RepositorioEstoqueSupabase(cliente).editar_compra(gravar) == ["compras/7/itens/1/a.png"]

    funcao, p = cliente.chamadas[0]
    assert funcao == "editar_compra"
    assert set(p) == {
        "p_id",
        "p_data_compra",
        "p_local_tipo",
        "p_local_nome",
        "p_frete",
        "p_itens",
        "p_usuario_id",
        "p_desconto",
    }
    assert (p["p_id"], p["p_data_compra"], p["p_frete"], p["p_usuario_id"]) == (
        7,
        "2026-09-10",
        "3.50",
        2,
    )
    assert p["p_itens"][0]["nome"] == "A"


def test_repositorio_resposta_estranha_da_edicao_e_incerta():
    cliente = _ClienteFalso({"id": 7})
    gravar = CompraParaGravar(1, uuid.uuid4(), date(2026, 9, 1), "shopee", None, 1, ())

    with pytest.raises(FalhaNoEstoque) as erro:
        RepositorioEstoqueSupabase(cliente).editar_compra(gravar)
    assert erro.value.incerta
