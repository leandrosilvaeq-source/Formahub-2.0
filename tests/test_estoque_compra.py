"""Registrar Compra (POST /estoque/compras) com repositório falso: validação, cálculos,
gravação atômica, proteção contra duplicação, fotos e o repositório do Supabase.

Nenhum teste acessa o Supabase; todas as compras são fictícias e ficam só em memória.
"""

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.estoque_compra import (
    LIMITE_ITENS,
    custo,
    gravar_compra,
    peso_recebido,
    validar_compra,
    valor_total,
)
from app.estoque_repositorio import (
    CompraParaGravar,
    FalhaNoEstoque,
    ItemCompraParaGravar,
    LoteNaoEncontrado,
    RepositorioEstoqueSupabase,
    custo_por_kg,
    get_repositorio_estoque,
)
from app.main import app
from tests.fake_estoque import RepositorioEstoqueMemoria

client = TestClient(app)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPEG = b"\xff\xd8\xff" + b"\0" * 64
CAMINHO_FOTO = re.compile(
    r"compras/(\d+)/itens/(\d+)/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}\.(png|jpg|webp)"
)


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def filamento(i: int, **extra) -> dict:
    dados = {
        "categoria": "filamento",
        "quantidade": "3",
        "cor": "Preto",
        "material": "PLA",
        "tipo": "silk",
        "marca": "Marca Teste",
        "peso_rolo": "1000",
        "valor_unitario": "R$ 89,90",
    }
    dados.update(extra)
    return {f"item_{i}_{campo}": valor for campo, valor in dados.items()}


def item(i: int, categoria: str, **extra) -> dict:
    dados = {
        "categoria": categoria,
        "nome": "Argola Teste" if categoria == "acessorio" else "Caixa Teste",
        "quantidade": "40",
        "valor_total": "R$ 14,00",
    }
    dados.update(extra)
    return {f"item_{i}_{campo}": valor for campo, valor in dados.items()}


def compra(*linhas: dict, **extra) -> dict:
    dados = {
        "csrf": "csrf-de-teste",
        "chave_envio": str(uuid.uuid4()),
        "data_compra": "10/09/2026",
        "local": "mercado_livre",
        "local_nome": "",
    }
    for linha in linhas:
        dados.update(linha)
    dados.update(extra)
    return dados


def enviar(dados: dict, fotos: dict | None = None, **opcoes):
    arquivos = {
        f"item_{i}_foto": (nome, conteudo, "application/octet-stream")
        for i, (nome, conteudo) in (fotos or {}).items()
    }
    return client.post("/estoque/compras", data=dados, files=arquivos or None, **opcoes)


def nada_gravado(repo: RepositorioEstoqueMemoria) -> bool:
    return not (repo.compras or repo.filamentos or repo.itens or repo.imagens)


# ---------- Acesso e segurança ----------


@pytest.mark.sem_login
def test_registrar_compra_exige_login(estoque):
    resposta = enviar(compra(filamento(0)), follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")
    assert nada_gravado(estoque)


def test_pagina_expirada_nao_grava(estoque):
    resposta = enviar(compra(filamento(0), csrf="outro"), fotos={0: ("f.png", PNG)})

    assert resposta.status_code == 403
    assert resposta.json() == {"erro": "A página expirou. Confira os dados e salve de novo."}
    assert resposta.headers["cache-control"] == "no-store"
    assert nada_gravado(estoque)


def test_outra_origem_nao_grava(estoque):
    resposta = enviar(compra(filamento(0)), headers={"Origin": "https://exemplo.com"})
    assert resposta.status_code == 403
    assert nada_gravado(estoque)


@pytest.mark.parametrize("chave", ["", "nao-e-uuid", "11111111-1111-1111-1111-111111111111"])
def test_sem_chave_de_envio_valida_nao_grava(estoque, chave):
    resposta = enviar(compra(filamento(0), chave_envio=chave))
    assert resposta.status_code == 403
    assert nada_gravado(estoque)


def test_usuario_da_compra_e_sempre_o_da_sessao(estoque):
    enviar(compra(filamento(0), usuario_id="99", criado_por="99"))
    (gravada,) = estoque.compras.values()
    assert gravada.compra.usuario_id == 1


# ---------- Compra com as três categorias ----------


def test_compra_com_as_tres_categorias_e_varias_linhas(estoque):
    dados = compra(
        filamento(0, quantidade="3", peso_rolo="1000", valor_unitario="R$ 89,90"),
        filamento(
            1,
            quantidade="2",
            cor="Branco",
            tipo="solido",
            peso_rolo="750,5",
            valor_unitario="R$ 100,00",
        ),
        item(2, "acessorio", quantidade="40", valor_total="R$ 14,00"),
        item(3, "embalagem", quantidade="3", valor_total="R$ 10,00"),
        item(4, "embalagem", nome="Caixa G", quantidade="2", valor_total="R$ 7,00"),
    )
    resposta = enviar(dados)

    assert resposta.status_code == 200
    assert resposta.json() == {"destino": "/estoque?salvo=compra"}
    (gravada,) = estoque.compras.values()
    c = gravada.compra
    assert (c.data_compra, c.local_tipo, c.local_nome) == (date(2026, 9, 10), "mercado_livre", None)
    assert str(c.chave_envio) == dados["chave_envio"]
    assert [i.ordem for i in c.itens] == [1, 2, 3, 4, 5]
    assert [i.categoria for i in c.itens] == [
        "filamento",
        "filamento",
        "acessorio",
        "embalagem",
        "embalagem",
    ]
    # Um lote por linha, mesmo com duas linhas da mesma categoria.
    assert len(gravada.lotes) == 5
    assert len(estoque.filamentos) == 2 and len(estoque.itens) == 3
    preto, branco = estoque.filamentos.values()
    assert preto.peso_g == Decimal("3000")
    assert branco.peso_g == Decimal("1501.0")
    assert branco.custo_kg == Decimal("100.00") * 1000 / Decimal("750.5")

    html = client.get(resposta.json()["destino"]).text
    assert "Compra registrada. Os itens já estão no estoque." in html
    assert "4.501 g" in html  # peso disponível total dos filamentos
    assert "R$ 89,90" in html and "R$ 133,24" in html  # custo/kg de cada lote
    assert "R$ 0,35" in html and "R$ 3,33" in html and "R$ 3,50" in html  # custo unitário
    assert "10/09/2026" in html


def test_texto_sem_espacos_extras_e_data_iso(estoque):
    enviar(compra(filamento(0, cor="  Azul   Royal ", marca=" X "), data_compra="2026-09-05"))
    (lote,) = estoque.filamentos.values()
    assert (lote.cor, lote.marca, lote.data_compra) == ("Azul Royal", "X", date(2026, 9, 5))


def test_compras_distintas_do_mesmo_insumo_viram_lotes_separados(estoque):
    enviar(compra(item(0, "acessorio", quantidade="40"), data_compra="01/08/2026"))
    enviar(compra(item(0, "acessorio", quantidade="10"), data_compra="01/09/2026"))

    assert len(estoque.compras) == 2
    assert sorted(i.quantidade for i in estoque.itens.values()) == [10, 40]


# ---------- Local de compra ----------


@pytest.mark.parametrize("local", ["mercado_livre", "shopee", "aliexpress"])
def test_plataforma_desconsidera_o_nome_do_local(estoque, local):
    enviar(compra(item(0, "embalagem"), local=local, local_nome="Texto que ficou para trás"))
    (gravada,) = estoque.compras.values()
    assert (gravada.compra.local_tipo, gravada.compra.local_nome) == (local, None)


@pytest.mark.parametrize("local", ["outro", "loja_fisica"])
def test_local_personalizado_exige_o_nome(estoque, local):
    resposta = enviar(compra(item(0, "embalagem"), local=local, local_nome="  "))
    assert resposta.status_code == 422
    assert resposta.json()["campos"] == {"local_nome": "Informe o nome do local."}
    assert nada_gravado(estoque)

    resposta = enviar(compra(item(0, "embalagem"), local=local, local_nome=" Papelaria  Centro "))
    assert resposta.status_code == 200
    (gravada,) = estoque.compras.values()
    assert (gravada.compra.local_tipo, gravada.compra.local_nome) == (local, "Papelaria Centro")


def test_nome_do_local_longo(estoque):
    resposta = enviar(compra(item(0, "embalagem"), local="outro", local_nome="x" * 61))
    assert resposta.json()["campos"]["local_nome"] == "Use no máximo 60 caracteres."


@pytest.mark.parametrize("local", ["", "amazon", "Mercado Livre"])
def test_local_fora_da_lista(estoque, local):
    resposta = enviar(compra(item(0, "embalagem"), local=local))
    assert resposta.json()["campos"] == {"local": "Escolha o local de compra."}
    assert nada_gravado(estoque)


# ---------- Campos obrigatórios ----------


def test_compra_sem_itens(estoque):
    resposta = enviar(compra())
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erro": "Não foi possível salvar. Corrija os campos destacados.",
        "campos": {"itens": "Adicione pelo menos um item."},
    }
    assert nada_gravado(estoque)


def test_compra_com_itens_demais(estoque):
    linhas = [item(i, "acessorio") for i in range(LIMITE_ITENS + 1)]
    resposta = enviar(compra(*linhas))
    assert resposta.json()["campos"] == {"itens": "Registre no máximo 50 itens por compra."}
    assert nada_gravado(estoque)


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("data_compra", "", "Informe a data de compra."),
        ("data_compra", "31/02/2026", "Data inválida."),
        ("data_compra", "01/01/1999", "Data inválida."),
        ("data_compra", "ontem", "Use o formato dd/mm/aaaa."),
        ("data_compra", "1/9/2026", "Use o formato dd/mm/aaaa."),
    ],
)
def test_validacao_da_data(estoque, campo, valor, mensagem):
    resposta = enviar(compra(item(0, "acessorio"), **{campo: valor}))
    assert resposta.json()["campos"] == {campo: mensagem}
    assert nada_gravado(estoque)


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("quantidade", "", "Informe a quantidade de rolos."),
        ("quantidade", "0", "A quantidade deve ser maior que zero."),
        ("quantidade", "-1", "Use um número inteiro."),
        ("quantidade", "1,5", "Use um número inteiro."),
        ("quantidade", "²", "Use um número inteiro."),
        ("quantidade", "10000", "Quantidade alta demais."),
        ("cor", "", "Informe a cor."),
        ("cor", "x" * 61, "Use no máximo 60 caracteres."),
        ("material", "  ", "Informe o material."),
        ("tipo", "", "Escolha o tipo."),
        ("tipo", "Sólido", "Escolha o tipo."),
        ("marca", "", "Informe a marca."),
        ("peso_rolo", "", "Informe o peso por rolo."),
        ("peso_rolo", "0", "O peso deve ser maior que zero."),
        ("peso_rolo", "0,00", "O peso deve ser maior que zero."),
        ("peso_rolo", "-5", "O peso deve ser maior que zero."),
        ("peso_rolo", "1,555", "Use só números, com até 2 casas decimais"),
        ("peso_rolo", "1.000,5", "Use só números, com até 2 casas decimais"),
        ("peso_rolo", "123456", "Use só números, com até 2 casas decimais"),
        ("valor_unitario", "", "Informe o valor unitário por rolo."),
        ("valor_unitario", "R$ -0,01", "O valor não pode ser negativo."),
        ("valor_unitario", "dez reais", "Valor inválido."),
        ("valor_unitario", "10000000", "Valor alto demais."),
        ("valor_unitario", "1.234", "Use no máximo 2 casas decimais."),
    ],
)
def test_validacao_do_filamento(estoque, campo, valor, mensagem):
    resposta = enviar(compra(item(0, "acessorio"), filamento(1, **{campo: valor})))

    assert resposta.status_code == 422
    campos = resposta.json()["campos"]
    assert list(campos) == [f"item_1_{campo}"]
    assert campos[f"item_1_{campo}"].startswith(mensagem)
    assert nada_gravado(estoque)  # a linha válida também não foi gravada


@pytest.mark.parametrize("categoria", ["acessorio", "embalagem"])
@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("nome", "", "Informe o nome d"),
        ("nome", "x" * 61, "Use no máximo 60 caracteres."),
        ("quantidade", "", "Informe a quantidade."),
        ("quantidade", "0", "A quantidade deve ser maior que zero."),
        ("quantidade", "2.5", "Use um número inteiro."),
        ("quantidade", "1000000", "Quantidade alta demais."),
        ("valor_total", "", "Informe o valor total."),
        ("valor_total", "-0,50", "O valor não pode ser negativo."),
    ],
)
def test_validacao_de_acessorio_e_embalagem(estoque, categoria, campo, valor, mensagem):
    resposta = enviar(compra(item(0, categoria, **{campo: valor})))
    campos = resposta.json()["campos"]
    assert list(campos) == [f"item_0_{campo}"]
    assert campos[f"item_0_{campo}"].startswith(mensagem)
    assert nada_gravado(estoque)


def test_mensagem_de_nome_conforme_a_categoria(estoque):
    campos = enviar(compra(item(0, "acessorio", nome=""), item(1, "embalagem", nome=""))).json()[
        "campos"
    ]
    assert campos == {
        "item_0_nome": "Informe o nome do acessório.",
        "item_1_nome": "Informe o nome da embalagem.",
    }


def test_valor_zero_e_aceito(estoque):
    resposta = enviar(
        compra(filamento(0, valor_unitario="0"), item(1, "embalagem", valor_total="0"))
    )
    assert resposta.status_code == 200
    (lote,) = estoque.filamentos.values()
    assert lote.custo_kg == 0


def test_categoria_desconhecida(estoque):
    resposta = enviar(compra(item(0, "parafuso")))
    assert "item_0_categoria" in resposta.json()["campos"]
    assert nada_gravado(estoque)


def test_erros_usam_a_posicao_da_linha_na_tela(estoque):
    # Posições salteadas (a tela renumera, mas o servidor não depende disso).
    resposta = enviar(compra(item(3, "acessorio", nome=""), filamento(7, cor="")))
    assert resposta.json()["campos"] == {
        "item_3_nome": "Informe o nome do acessório.",
        "item_7_cor": "Informe a cor.",
    }


def test_todos_os_erros_de_uma_vez(estoque):
    resposta = enviar(compra(filamento(0, cor="", peso_rolo=""), data_compra="", local=""))
    assert set(resposta.json()["campos"]) == {
        "data_compra",
        "local",
        "item_0_cor",
        "item_0_peso_rolo",
    }


# ---------- Cálculos ----------


def test_calculos_da_linha_de_filamento():
    linha = ItemCompraParaGravar(
        ordem=1,
        categoria="filamento",
        quantidade=3,
        peso_rolo_g=Decimal("750.5"),
        valor_unitario=Decimal("100.00"),
    )
    assert peso_recebido(linha) == Decimal("2251.5")
    assert valor_total(linha) == Decimal("300.00")
    # Sem arredondar a etapa intermediária: o valor exato da divisão.
    assert custo(linha) == Decimal("100.00") * 1000 / Decimal("750.5")
    assert custo(linha).quantize(Decimal("0.01")) == Decimal("133.24")


def test_calculos_de_acessorio_e_embalagem():
    linha = ItemCompraParaGravar(
        ordem=1, categoria="embalagem", quantidade=3, nome="Caixa", valor_total=Decimal("10.00")
    )
    assert valor_total(linha) == Decimal("10.00")
    assert custo(linha) == Decimal("10.00") / 3  # sem arredondar (28 dígitos)


def test_custo_por_kg_com_peso_decimal():
    assert custo_por_kg(Decimal("50.00"), Decimal("250")) == Decimal("200")


def test_validar_compra_monta_os_itens_em_decimal():
    dados = compra(filamento(0, peso_rolo="1000,25", valor_unitario="R$ 1.234,56"))
    resultado = validar_compra(dados)
    ((linha, foto),) = resultado.itens
    assert not resultado.erros and foto is None
    assert linha.peso_rolo_g == Decimal("1000.25")
    assert linha.valor_unitario == Decimal("1234.56")
    assert isinstance(linha.quantidade, int)


# ---------- Duplicação ----------


def test_reenvio_da_mesma_submissao_nao_duplica(estoque):
    dados = compra(filamento(0), item(1, "acessorio"))

    primeira = enviar(dados)
    segunda = enviar(dados, fotos={0: ("f.png", PNG)})  # duplo clique / reenvio

    assert primeira.status_code == segunda.status_code == 200
    assert len(estoque.compras) == 1
    assert len(estoque.filamentos) == 1 and len(estoque.itens) == 1
    # A foto da tentativa repetida não fica órfã no Storage.
    assert not estoque.imagens and len(estoque.removidas) == 1


def test_resposta_perdida_e_reenvio_gravam_uma_vez(estoque):
    dados = compra(item(0, "embalagem"))
    estoque.falhar_em = {"registrar_depois"}
    assert enviar(dados).status_code == 503  # gravou, mas a resposta não chegou

    estoque.falhar_em = set()
    assert enviar(dados).status_code == 200
    assert len(estoque.compras) == 1 and len(estoque.itens) == 1


def test_submissoes_diferentes_gravam_compras_diferentes(estoque):
    enviar(compra(item(0, "embalagem")))
    enviar(compra(item(0, "embalagem")))
    assert len(estoque.compras) == 2


# ---------- Falhas: nada pela metade ----------


@pytest.mark.parametrize("passo", ["reservar", "enviar", "registrar"])
def test_falha_na_gravacao_nao_deixa_nada(estoque, passo):
    estoque.falhar_em = {passo}
    dados = compra(filamento(0), item(1, "acessorio"), filamento(2))
    resposta = enviar(dados, fotos={0: ("a.png", PNG), 2: ("b.jpg", JPEG)})

    assert resposta.status_code == 503
    assert resposta.json() == {
        "erro": "Não foi possível salvar agora. Tente novamente em instantes."
    }
    assert nada_gravado(estoque)  # nenhum lote, nenhuma foto nova sobrando


def test_falha_apos_upload_remove_so_as_fotos_desta_tentativa(estoque):
    estoque.imagens["compras/1/itens/1/antiga.png"] = PNG  # foto de uma compra anterior
    estoque.falhar_em = {"registrar"}

    enviar(compra(filamento(0), item(1, "acessorio")), fotos={0: ("a.png", PNG), 1: ("b.png", PNG)})

    assert list(estoque.imagens) == ["compras/1/itens/1/antiga.png"]
    assert len(estoque.removidas) == 2
    assert all(CAMINHO_FOTO.fullmatch(c) for c in estoque.removidas)


def test_resposta_incerta_mantem_as_fotos(estoque):
    # A compra pode ter sido gravada: apagar as fotos deixaria caminhos quebrados.
    estoque.falhar_em = {"registrar_incerta"}
    resposta = enviar(compra(filamento(0)), fotos={0: ("a.png", PNG)})
    assert resposta.status_code == 503
    assert len(estoque.imagens) == 1 and not estoque.removidas


def test_falha_ao_remover_fotos_nao_quebra_a_resposta(estoque):
    estoque.falhar_em = {"registrar", "remover"}
    resposta = enviar(compra(filamento(0)), fotos={0: ("a.png", PNG)})
    assert resposta.status_code == 503


def test_sem_banco_configurado(estoque):
    app.dependency_overrides[get_repositorio_estoque] = lambda: None
    assert enviar(compra(filamento(0))).status_code == 503


# ---------- Fotos ----------


def test_fotos_sao_opcionais_por_linha(estoque):
    dados = compra(filamento(0), item(1, "acessorio"), item(2, "embalagem"))
    resposta = enviar(dados, fotos={1: ("argola.jpg", JPEG)})

    assert resposta.status_code == 200
    (gravada,) = estoque.compras.values()
    caminhos = [i.imagem_caminho for i in gravada.compra.itens]
    assert caminhos[0] is None and caminhos[2] is None
    encontrado = CAMINHO_FOTO.fullmatch(caminhos[1])
    assert encontrado.groups() == (str(gravada.compra.id), "2", "jpg")
    assert estoque.imagens[caminhos[1]] == JPEG


@pytest.mark.parametrize(
    ("caso", "mensagem"),
    [("gif", "Formato não aceito."), ("vazio", "Arquivo vazio."), ("grande", "Maior que 10 MB.")],
)
def test_foto_invalida_nao_grava_nada(estoque, caso, mensagem):
    conteudo = {
        "gif": b"GIF89a" + b"\x00" * 10,
        "vazio": b"",
        "grande": PNG + b"\x00" * (10 * 1024 * 1024),
    }[caso]
    resposta = enviar(compra(filamento(0), item(1, "acessorio")), fotos={1: ("x.png", conteudo)})

    assert resposta.status_code == 422
    assert resposta.json()["campos"] == {"item_1_foto": mensagem}
    assert nada_gravado(estoque)


def test_foto_sem_linha_correspondente_e_ignorada(estoque):
    resposta = enviar(compra(filamento(0)), fotos={5: ("x.png", PNG)})
    assert resposta.status_code == 200
    assert not estoque.imagens


# ---------- gravar_compra direto (sem a rota) ----------


def test_gravar_compra_usa_o_id_reservado_no_caminho_das_fotos(estoque):
    estoque.reservar_id_compra()  # o id 1 fica para outra compra
    dados = compra(item(0, "acessorio"))
    resultado = validar_compra(dados, {0: PNG})
    fixo = uuid.UUID("0f8fad5b-d9cb-469f-a165-70867728950e")

    assert gravar_compra(estoque, resultado, 1, novo_uuid=lambda: fixo) is False
    assert list(estoque.imagens) == [f"compras/2/itens/1/{fixo}.png"]


# ---------- Repositório do Supabase (cliente falso) ----------


class _Resposta:
    def __init__(self, data):
        self.data = data


class _ClienteFalso:
    def __init__(self, data=None, erro=None):
        self.data, self.erro, self.chamadas = data, erro, []

    def rpc(self, funcao, parametros):
        self.chamadas.append((funcao, parametros))
        return self

    def execute(self):
        if self.erro:
            raise self.erro
        return _Resposta(self.data)


def test_repositorio_converte_a_listagem():
    dados = {
        "filamentos": [
            {
                "id": 1,
                "cor": "Preto",
                "material": "PLA",
                "tipo": "solido",
                "marca": "M",
                "peso_disponivel_g": "2251.50",
                "data_compra": "2026-09-01",
                "peso_rolo_g": "750.50",
                "valor_unitario": "100.00",
            }
        ],
        "itens": [
            {
                "id": 2,
                "categoria": "embalagem",
                "nome": "Caixa",
                "quantidade_disponivel": 3,
                "data_compra": "2026-09-02",
                "quantidade_comprada": 3,
                "valor_total": "10.00",
            }
        ],
    }
    estoque = RepositorioEstoqueSupabase(_ClienteFalso(dados)).listar_estoque()

    (f,) = estoque.filamentos
    assert (f.peso_g, f.data_compra) == (Decimal("2251.50"), date(2026, 9, 1))
    assert f.custo_kg == Decimal("100.00") * 1000 / Decimal("750.50")
    (i,) = estoque.itens
    assert i.custo_unitario == Decimal("10.00") / 3


def test_repositorio_envia_a_compra_com_numeros_como_texto():
    cliente = _ClienteFalso({"id": 7, "duplicada": False})
    chave = uuid.uuid4()
    gravar = CompraParaGravar(
        id=7,
        chave_envio=chave,
        data_compra=date(2026, 9, 10),
        local_tipo="loja_fisica",
        local_nome="Papelaria",
        usuario_id=2,
        itens=(
            ItemCompraParaGravar(
                ordem=1,
                categoria="filamento",
                quantidade=3,
                cor="Preto",
                material="PLA",
                tipo="silk",
                marca="M",
                peso_rolo_g=Decimal("750.5"),
                valor_unitario=Decimal("89.90"),
                imagem_caminho="compras/7/itens/1/x.png",
            ),
            ItemCompraParaGravar(
                ordem=2,
                categoria="acessorio",
                quantidade=40,
                nome="Argola",
                valor_total=Decimal("14.00"),
            ),
        ),
    )
    assert RepositorioEstoqueSupabase(cliente).registrar_compra(gravar) is False

    funcao, p = cliente.chamadas[0]
    assert funcao == "registrar_compra"
    assert (p["p_id"], p["p_chave_envio"], p["p_data_compra"]) == (7, str(chave), "2026-09-10")
    assert (p["p_local_tipo"], p["p_local_nome"], p["p_usuario_id"]) == (
        "loja_fisica",
        "Papelaria",
        2,
    )
    fil, ace = p["p_itens"]
    assert (fil["peso_rolo_g"], fil["valor_unitario"], fil["valor_total"]) == (
        "750.5",
        "89.90",
        None,
    )
    assert fil["imagem_caminho"] == "compras/7/itens/1/x.png"
    assert (ace["valor_total"], ace["cor"], ace["peso_rolo_g"]) == ("14.00", None, None)


def test_repositorio_reconhece_reenvio():
    cliente = _ClienteFalso({"id": 3, "duplicada": True})
    gravar = CompraParaGravar(1, uuid.uuid4(), date(2026, 9, 1), "shopee", None, 1, ())
    assert RepositorioEstoqueSupabase(cliente).registrar_compra(gravar) is True


def test_repositorio_edita_so_a_descricao():
    cliente = _ClienteFalso(5)
    repo = RepositorioEstoqueSupabase(cliente)
    repo.editar_filamento(5, "Preto", "PLA", "silk", "M", 2)
    repo.editar_item(6, "embalagem", "Caixa", 3)

    assert cliente.chamadas == [
        (
            "editar_estoque_filamento",
            {
                "p_id": 5,
                "p_cor": "Preto",
                "p_material": "PLA",
                "p_tipo": "silk",
                "p_marca": "M",
                "p_usuario_id": 2,
            },
        ),
        (
            "editar_estoque_item",
            {"p_id": 6, "p_categoria": "embalagem", "p_nome": "Caixa", "p_usuario_id": 3},
        ),
    ]


def test_repositorio_traduz_erros():
    from postgrest.exceptions import APIError

    nao_encontrado = _ClienteFalso(erro=APIError({"code": "PT404", "message": "x"}))
    with pytest.raises(LoteNaoEncontrado):
        RepositorioEstoqueSupabase(nao_encontrado).editar_item(5, "acessorio", "A", 1)

    recusado = _ClienteFalso(erro=APIError({"code": "23514", "message": "x"}))
    with pytest.raises(FalhaNoEstoque) as erro:
        RepositorioEstoqueSupabase(recusado).reservar_id_compra()
    assert erro.value.incerta is False

    sem_rede = _ClienteFalso(erro=TimeoutError())
    with pytest.raises(FalhaNoEstoque) as erro:
        RepositorioEstoqueSupabase(sem_rede).listar_estoque()
    assert erro.value.incerta is True

    # Banco ainda sem a migration das compras: resposta em formato inesperado.
    with pytest.raises(FalhaNoEstoque):
        RepositorioEstoqueSupabase(
            _ClienteFalso({"filamentos": [{"id": 1}], "itens": []})
        ).listar_estoque()


class _Bucket:
    def __init__(self, erro=None):
        self.erro, self.chamadas = erro, []

    def upload(self, caminho, conteudo, opcoes):
        self.chamadas.append(("upload", caminho, opcoes))
        if self.erro:
            raise self.erro

    def remove(self, caminhos):
        self.chamadas.append(("remove", caminhos))


class _ClienteComStorage(_ClienteFalso):
    def __init__(self, bucket):
        super().__init__()
        self.bucket = bucket
        self.storage = self

    def from_(self, nome):
        assert nome == "compra-imagens"
        return self.bucket


def test_repositorio_envia_fotos_ao_bucket_privado_sem_sobrescrever():
    from storage3.exceptions import StorageApiError

    bucket = _Bucket()
    repo = RepositorioEstoqueSupabase(_ClienteComStorage(bucket))
    repo.enviar_imagem("compras/1/itens/1/x.png", PNG, "image/png")
    repo.remover_imagens(["compras/1/itens/1/x.png"])
    assert bucket.chamadas == [
        ("upload", "compras/1/itens/1/x.png", {"content-type": "image/png", "upsert": "false"}),
        ("remove", ["compras/1/itens/1/x.png"]),
    ]

    recusado = RepositorioEstoqueSupabase(
        _ClienteComStorage(_Bucket(StorageApiError("x", "Invalid", 400)))
    )
    with pytest.raises(FalhaNoEstoque) as erro:
        recusado.enviar_imagem("compras/1/itens/1/x.png", PNG, "image/png")
    assert erro.value.incerta is False
