"""Gravação do pedido: banco e Storage falsos (em memória). Nenhum teste acessa o Supabase."""

import re
from decimal import Decimal

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario
from app.main import app
from app.pedidos import (
    LIMITE_IMAGEM_BYTES,
    PedidoNaoSalvo,
    gravar_pedido,
    ler_formulario,
    tipo_da_imagem,
    validar,
)
from app.pedidos_repositorio import FalhaAoGravar

client = TestClient(app)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def formulario(**extra):
    dados = {
        "cliente": "Maria Souza",
        "contato": "(11) 98765-4321",
        "item_produto": ["Caneca personalizada", "Camiseta", "Chaveiro"],
        "item_quantidade": ["2", "1", "3"],
        "item_valor": ["R$ 35,50", "R$ 1.200,00", "R$ 0,05"],
        "pagamento": "PIX",
        "entrega": "Retirada",
        "observacoes": "",
        "csrf": "csrf-de-teste",
    }
    dados.update(extra)
    return dados


def enviar(dados=None, imagens=None, **kwargs):
    """POST multipart, como o JavaScript da tela: imagens = {posição: (nome, bytes, tipo)}."""
    arquivos = [(f"item_imagem_{i}", img) for i, img in (imagens or {}).items()]
    # Sem arquivos o TestClient enviaria urlencoded; um campo vazio força o multipart.
    arquivos = arquivos or [("sem_arquivo", ("", b"", "application/octet-stream"))]
    return client.post("/pedidos/novo", data=dados or formulario(), files=arquivos, **kwargs)


def unico_pedido(repo_pedidos):
    assert len(repo_pedidos.pedidos) == 1
    return next(iter(repo_pedidos.pedidos.values()))


def assert_nada_gravado(repo_pedidos):
    assert repo_pedidos.pedidos == {}
    assert repo_pedidos.arquivos == {}


# ---------- Pedido válido ----------


def test_pedido_sem_imagens_e_gravado(repo_pedidos):
    resposta = enviar(formulario(observacoes="Embalar para presente"))

    assert resposta.status_code == 200
    assert "Pedido de Maria Souza salvo com sucesso — 6 itens, total R$ 1.271,15." in resposta.text
    pedido = unico_pedido(repo_pedidos)
    assert pedido.cliente_nome == "Maria Souza"
    assert pedido.contato == "(11) 98765-4321"
    assert pedido.forma_pagamento == "pix"
    assert pedido.tipo_entrega == "retirada"
    assert pedido.observacoes == "Embalar para presente"
    assert pedido.valor_total == Decimal("1271.15")
    assert [(i.ordem, i.produto, i.quantidade) for i in pedido.itens] == [
        (1, "Caneca personalizada", 2),
        (2, "Camiseta", 1),
        (3, "Chaveiro", 3),
    ]
    assert [i.valor_unitario for i in pedido.itens] == [
        Decimal("35.50"),
        Decimal("1200.00"),
        Decimal("0.05"),
    ]
    assert [i.subtotal for i in pedido.itens] == [
        Decimal("71.00"),
        Decimal("1200.00"),
        Decimal("0.15"),
    ]
    assert all(i.imagem_caminho is None for i in pedido.itens)
    assert repo_pedidos.arquivos == {}


@pytest.mark.parametrize(
    ("pagamento", "entrega", "codigos"),
    [
        ("PIX", "Retirada", ("pix", "retirada")),
        ("Dinheiro", "Entrega em mãos", ("dinheiro", "entrega")),
        ("Cartão", "Retirada", ("cartao", "retirada")),
    ],
)
def test_opcoes_da_tela_viram_codigos_do_banco(repo_pedidos, pagamento, entrega, codigos):
    enviar(formulario(pagamento=pagamento, entrega=entrega))

    pedido = unico_pedido(repo_pedidos)
    assert (pedido.forma_pagamento, pedido.tipo_entrega) == codigos


def test_observacoes_vazias_viram_nulo(repo_pedidos):
    enviar(formulario(observacoes="   "))

    assert unico_pedido(repo_pedidos).observacoes is None


def test_sucesso_limpa_o_formulario():
    html = enviar().text

    assert 'class="alerta alerta-sucesso"' in html
    assert 'value="Maria Souza"' not in html
    assert 'value="Caneca personalizada"' not in html
    assert " checked" not in html
    assert html.count('class="item" data-indice=') == 2  # uma linha vazia + o modelo


def test_envio_urlencoded_sem_javascript_tambem_grava(repo_pedidos):
    resposta = client.post("/pedidos/novo", data=formulario())

    assert resposta.status_code == 200
    assert len(repo_pedidos.pedidos) == 1


# ---------- Imagens ----------


def test_imagem_em_apenas_um_item(repo_pedidos):
    resposta = enviar(imagens={1: ("foto.png", PNG, "image/png")})

    assert resposta.status_code == 200
    pedido = unico_pedido(repo_pedidos)
    caminhos = [i.imagem_caminho for i in pedido.itens]
    assert caminhos[0] is None and caminhos[2] is None
    assert re.fullmatch(rf"pedidos/{pedido.id}/itens/2/{UUID}\.png", caminhos[1])
    assert repo_pedidos.arquivos == {caminhos[1]: (PNG, "image/png")}


def test_imagens_em_varios_itens(repo_pedidos):
    enviar(
        imagens={
            0: ("a.png", PNG, "image/png"),
            1: ("b.jpeg", JPEG, "image/jpeg"),
            2: ("c.webp", WEBP, "image/webp"),
        }
    )

    pedido = unico_pedido(repo_pedidos)
    caminhos = [i.imagem_caminho for i in pedido.itens]
    for ordem, (caminho, extensao) in enumerate(
        zip(caminhos, ["png", "jpg", "webp"], strict=True), 1
    ):
        assert re.fullmatch(rf"pedidos/{pedido.id}/itens/{ordem}/{UUID}\.{extensao}", caminho)
    assert [repo_pedidos.arquivos[c][1] for c in caminhos] == [
        "image/png",
        "image/jpeg",
        "image/webp",
    ]
    assert len(set(caminhos)) == 3


def test_banco_guarda_so_o_caminho_nunca_o_conteudo(repo_pedidos):
    enviar(imagens={0: ("a.png", PNG, "image/png")})

    item = unico_pedido(repo_pedidos).itens[0]
    assert isinstance(item.imagem_caminho, str)
    assert "base64" not in item.imagem_caminho.lower()
    assert len(item.imagem_caminho) < 120
    assert not any(isinstance(v, bytes) for v in vars(item).values())


def test_imagem_fica_com_o_item_certo_mesmo_com_linha_em_branco(repo_pedidos):
    dados = formulario(
        item_produto=["A", "", "B"],
        item_quantidade=["1", "1", "1"],
        item_valor=["R$ 1,00", "", "R$ 2,00"],
    )
    enviar(dados, imagens={2: ("b.png", PNG, "image/png")})

    pedido = unico_pedido(repo_pedidos)
    assert [(i.ordem, i.produto) for i in pedido.itens] == [(1, "A"), (2, "B")]
    assert pedido.itens[0].imagem_caminho is None
    assert pedido.itens[1].imagem_caminho.startswith(f"pedidos/{pedido.id}/itens/2/")


def test_tipo_vem_do_conteudo_e_nao_do_nome_nem_do_tipo_declarado(repo_pedidos):
    enviar(imagens={0: ("foto.png", JPEG, "image/png")})

    caminho = unico_pedido(repo_pedidos).itens[0].imagem_caminho
    assert caminho.endswith(".jpg")
    assert repo_pedidos.arquivos[caminho][1] == "image/jpeg"


@pytest.mark.parametrize(
    ("conteudo", "tipo_declarado", "mensagem"),
    [
        (GIF, "image/gif", "Formato não aceito."),
        (b"texto qualquer", "image/png", "Formato não aceito."),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", "Formato não aceito."),
        (b"", "image/png", "Arquivo vazio."),
    ],
)
def test_formato_nao_aceito_e_rejeitado(repo_pedidos, conteudo, tipo_declarado, mensagem):
    resposta = enviar(imagens={1: ("x.png", conteudo, tipo_declarado)})

    assert resposta.status_code == 422
    assert f'<p class="campo-erro item-imagem-erro">{mensagem}</p>' in resposta.text
    assert "Corrija os campos destacados" in resposta.text
    assert_nada_gravado(repo_pedidos)


def test_limite_de_10_mb(repo_pedidos):
    no_limite = PNG + b"\x00" * (LIMITE_IMAGEM_BYTES - len(PNG))
    assert len(no_limite) == 10 * 1024 * 1024

    assert enviar(imagens={0: ("ok.png", no_limite, "image/png")}).status_code == 200
    assert len(repo_pedidos.arquivos) == 1

    resposta = enviar(imagens={0: ("grande.png", no_limite + b"\x00", "image/png")})
    assert resposta.status_code == 422
    assert "Maior que 10 MB." in resposta.text
    assert len(repo_pedidos.pedidos) == 1  # só o primeiro


def test_erro_de_imagem_mostra_so_no_item_dela():
    html = enviar(imagens={1: ("x.gif", GIF, "image/gif")}).text

    assert html.count("item-imagem-erro") == 1
    linhas = html.split('class="item" data-indice=')
    assert "item-imagem-erro" in linhas[2]  # segunda linha de item


def test_imagem_em_linha_sem_dados_e_um_item_incompleto(repo_pedidos):
    dados = formulario(
        item_produto=["A", ""], item_quantidade=["1", "1"], item_valor=["R$ 1,00", ""]
    )
    resposta = enviar(dados, imagens={1: ("b.png", PNG, "image/png")})

    assert resposta.status_code == 422
    assert "Informe o produto." in resposta.text
    assert_nada_gravado(repo_pedidos)


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        (PNG, ("image/png", "png")),
        (JPEG, ("image/jpeg", "jpg")),
        (WEBP, ("image/webp", "webp")),
        (GIF, None),
        (b"RIFF\x00\x00\x00\x00WAVE", None),
        (b"", None),
    ],
)
def test_tipo_da_imagem(conteudo, esperado):
    assert tipo_da_imagem(conteudo) == esperado


# ---------- Usuário e valores decididos pelo servidor ----------


def test_criador_e_o_usuario_da_sessao(repo_pedidos):
    enviar()

    assert unico_pedido(repo_pedidos).criado_por == 1  # Leandro, da sessão simulada


def test_usuario_enviado_pelo_navegador_e_ignorado(repo_pedidos):
    enviar(formulario(criado_por="3", usuario_id="3", usuario="Marise"))

    assert unico_pedido(repo_pedidos).criado_por == 1


def test_criador_acompanha_quem_esta_logado(repo_pedidos):
    def kassia(request: Request) -> Usuario:
        usuario = Usuario(id=2, nome="Kassia")
        request.state.usuario = usuario
        request.state.csrf = "csrf-de-teste"
        return usuario

    app.dependency_overrides[exigir_usuario] = kassia
    enviar(formulario(criado_por="1"))

    assert unico_pedido(repo_pedidos).criado_por == 2


def test_total_e_subtotais_sao_recalculados_no_servidor(repo_pedidos):
    enviar(
        formulario(
            valor_total="0,01",
            total="R$ 0,01",
            subtotal=["R$ 0,01", "R$ 0,01", "R$ 0,01"],
            item_subtotal=["1", "1", "1"],
        )
    )

    pedido = unico_pedido(repo_pedidos)
    assert pedido.valor_total == Decimal("1271.15")
    assert pedido.valor_total == sum(i.subtotal for i in pedido.itens)


# ---------- Falhas: nada parcial, arquivos removidos ----------


def pedido_com_tres_imagens(repo_pedidos):
    return enviar(
        imagens={
            0: ("a.png", PNG, "image/png"),
            1: ("b.png", PNG, "image/png"),
            2: ("c.png", PNG, "image/png"),
        }
    )


def test_falha_no_upload_remove_as_imagens_ja_enviadas(repo_pedidos):
    repo_pedidos.falhar_upload_numero = 2

    resposta = pedido_com_tres_imagens(repo_pedidos)

    assert resposta.status_code == 503
    assert_nada_gravado(repo_pedidos)
    assert len(repo_pedidos.removidos) == 2  # a enviada e a que falhou (pode ter chegado)
    assert "Não foi possível salvar o pedido agora" in resposta.text


def test_falha_ao_gravar_itens_remove_todas_as_imagens(repo_pedidos):
    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar(incerta=False)

    resposta = pedido_com_tres_imagens(repo_pedidos)

    assert resposta.status_code == 503
    assert_nada_gravado(repo_pedidos)
    assert len(repo_pedidos.removidos) == 3


def test_falha_ao_reservar_o_pedido_nao_envia_nada(repo_pedidos):
    repo_pedidos.falhar_em["reservar_id"] = FalhaAoGravar(incerta=True)

    resposta = pedido_com_tres_imagens(repo_pedidos)

    assert resposta.status_code == 503
    assert_nada_gravado(repo_pedidos)
    assert repo_pedidos.removidos == []


def test_falha_ao_remover_imagens_nao_quebra_a_resposta(repo_pedidos):
    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar()
    repo_pedidos.falhar_em["remover_imagens"] = FalhaAoGravar()

    resposta = pedido_com_tres_imagens(repo_pedidos)

    assert resposta.status_code == 503
    assert repo_pedidos.pedidos == {}


def test_sem_resposta_do_banco_as_imagens_nao_sao_removidas(repo_pedidos):
    # O pedido pode ter sido gravado: apagar as imagens deixaria caminhos quebrados.
    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar(incerta=True)

    resposta = pedido_com_tres_imagens(repo_pedidos)

    assert resposta.status_code == 503
    assert repo_pedidos.removidos == []
    assert len(repo_pedidos.arquivos) == 3


def test_falha_mantem_os_dados_digitados_e_nao_mostra_detalhes(repo_pedidos):
    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar()

    html = enviar(formulario(observacoes="Embalar")).text

    assert 'value="Maria Souza"' in html
    assert 'value="Caneca personalizada"' in html
    assert 'value="PIX" checked' in html
    assert ">Embalar</textarea>" in html
    assert 'class="alerta alerta-erro" role="alert">Não foi possível salvar o pedido agora.' in html
    for detalhe in ("FalhaAoGravar", "Traceback", "supabase", "criar_pedido"):
        assert detalhe not in html


def test_sem_configuracao_do_banco_nada_e_gravado():
    from app.pedidos_repositorio import get_repositorio_pedidos

    app.dependency_overrides[get_repositorio_pedidos] = lambda: None

    resposta = enviar()

    assert resposta.status_code == 503
    assert "Não foi possível salvar o pedido agora" in resposta.text


def test_pedido_invalido_nao_grava_nem_envia_imagens(repo_pedidos):
    resposta = enviar(formulario(cliente=""), imagens={0: ("a.png", PNG, "image/png")})

    assert resposta.status_code == 422
    assert_nada_gravado(repo_pedidos)


def test_gravar_pedido_unitario_ordem_dos_passos(repo_pedidos):
    pedido = validar(
        ler_formulario(
            {
                "cliente": ["Ana"],
                "contato": ["11987654321"],
                "item_produto": ["A", "B"],
                "item_quantidade": ["1", "2"],
                "item_valor": ["R$ 10,00", "R$ 2,50"],
                "pagamento": ["Dinheiro"],
                "entrega": ["Entrega em mãos"],
            },
            {1: PNG},
        )
    )
    assert pedido.valido

    pedido_id = gravar_pedido(repo_pedidos, pedido, usuario_id=3)

    gravado = repo_pedidos.pedidos[pedido_id]
    assert gravado.criado_por == 3
    assert gravado.valor_total == Decimal("15.00")
    assert gravado.itens[1].imagem_caminho in repo_pedidos.arquivos


def test_gravar_pedido_falha_vira_pedido_nao_salvo(repo_pedidos):
    pedido = validar(
        ler_formulario(
            {
                "cliente": ["Ana"],
                "contato": ["11987654321"],
                "item_produto": ["A"],
                "item_quantidade": ["1"],
                "item_valor": ["R$ 10,00"],
                "pagamento": ["PIX"],
                "entrega": ["Retirada"],
            },
            {0: WEBP},
        )
    )
    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar()

    with pytest.raises(PedidoNaoSalvo):
        gravar_pedido(repo_pedidos, pedido, usuario_id=1)
    assert_nada_gravado(repo_pedidos)


# ---------- Proteção: CSRF e login ----------


@pytest.mark.parametrize("csrf", [None, "", "outro-token"])
def test_csrf_ausente_ou_errado_e_recusado(repo_pedidos, csrf):
    dados = formulario()
    if csrf is None:
        del dados["csrf"]
    else:
        dados["csrf"] = csrf

    resposta = enviar(dados, imagens={0: ("a.png", PNG, "image/png")})

    assert resposta.status_code == 403
    assert "A página expirou" in resposta.text
    assert 'value="Maria Souza"' in resposta.text  # dados preservados
    assert_nada_gravado(repo_pedidos)


def test_origem_de_outro_site_e_recusada(repo_pedidos):
    resposta = enviar(headers={"origin": "https://outro-site.example"})

    assert resposta.status_code == 403
    assert_nada_gravado(repo_pedidos)


def test_tela_tem_o_token_csrf_da_sessao():
    html = client.get("/pedidos/novo").text

    assert '<input type="hidden" id="csrf-pedido" name="csrf" value="csrf-de-teste">' in html


@pytest.mark.sem_login
def test_rota_protegida_sem_sessao(repo_pedidos):
    anonimo = TestClient(app)

    resposta = anonimo.post("/pedidos/novo", data=formulario(), follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")
    assert repo_pedidos.pedidos == {}
    assert anonimo.get("/pedidos/novo", follow_redirects=False).status_code == 303


# ---------- Tela ----------


def test_linhas_trazem_a_posicao_para_ligar_as_imagens():
    html = enviar(
        formulario(
            cliente="",
            item_produto=["A", "", "B"],
            item_quantidade=["1", "1", "1"],
            item_valor=["R$ 1,00", "", "R$ 2,00"],
        )
    ).text

    assert 'class="item" data-indice="0"' in html
    assert 'class="item" data-indice="2"' in html
    assert 'class="item" data-indice="1"' not in html.split('<template id="item-modelo">')[0]


def test_regioes_atualizadas_pelo_javascript_estao_marcadas():
    html = client.get("/pedidos/novo").text

    for regiao in (
        'id="campo-cliente" data-regiao',
        'id="campo-contato" data-regiao',
        'id="grupo-pagamento" data-regiao',
        'id="grupo-entrega" data-regiao',
        'id="resumo-quantidade" data-regiao',
        'id="resumo-total" data-regiao',
    ):
        assert regiao in html, regiao
    assert 'class="campo-texto" data-regiao' in html


def test_javascript_envia_multipart_sem_chave_secreta():
    js = client.get("/static/js/pedido-novo.js").text

    assert "new FormData(form)" in js
    assert '"item_imagem_" + i' in js
    html = client.get("/pedidos/novo").text.lower()
    for proibido in ("secret", "sb_secret", "service_role", "supabase_"):
        assert proibido not in js.lower()
        assert proibido not in html
