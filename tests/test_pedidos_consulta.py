"""Consulta de pedidos (listagem e detalhes) com repositório e Storage falsos.

Nenhum teste acessa o Supabase; os pedidos são criados só no repositório em memória.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pedidos_repositorio import FalhaAoConsultar, ItemParaGravar, PedidoParaGravar

client = TestClient(app)


def item(ordem, produto, quantidade, valor, imagem=None):
    valor = Decimal(valor)
    return ItemParaGravar(ordem, produto, quantidade, valor, quantidade * valor, imagem)


def novo_pedido(repo, cliente="Cliente Teste", itens=None, **extra):
    itens = itens or [item(1, "Caneca", 11, "12.00")]
    dados = {
        "id": repo.reservar_id(),
        "criado_por": 1,
        "cliente_nome": cliente,
        "contato": "(11) 98765-4321",
        "forma_pagamento": "pix",
        "status_pagamento": "pendente",
        "tipo_entrega": "entrega",
        "observacoes": None,
        "valor_total": sum(i.subtotal for i in itens),
        "itens": tuple(itens),
    }
    dados.update(extra)
    pedido = PedidoParaGravar(**dados)
    repo.criar_pedido(pedido)
    return pedido


def com_imagem(repo, pedido_id, ordem):
    caminho = f"pedidos/{pedido_id}/itens/{ordem}/00000000-0000-4000-8000-00000000000{ordem}.png"
    repo.arquivos[caminho] = (b"\x89PNG", "image/png")
    return caminho


# ---------- Listagem ----------


def card(html, cliente):
    """HTML do card do cliente (do <li> até o fim do item)."""
    inicio = html.index(f"<h2>{cliente}</h2>")
    return html[html.rindex("<li", 0, inicio) : html.index("</li>", inicio)]


def test_card_mostra_os_dados_aprovados(repo_pedidos):
    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(1, "Caneca", 11, "12.00"), item(2, "Camiseta", 2, "30.00")],
        forma_pagamento="cartao",
        status_pagamento="pago",
        observacoes="Embalar para presente",
        criado_por=3,
    )

    resposta = client.get("/pedidos")

    assert resposta.status_code == 200
    html = resposta.text
    assert "<h1>Pedidos</h1>" in html
    assert '<a class="botao botao-primario" href="/pedidos/novo">Novo pedido</a>' in html
    c = card(html, "Cliente Teste")
    assert (
        '<dd class="pedido-card-pagamento">Cartão <span class="selo-status selo-status-pago">' in c
    )
    assert "Pago</span>" in c
    assert "<dt>Cadastrado por</dt><dd>Marise</dd>" in c
    assert '<span class="pedido-card-produtos">Caneca, Camiseta</span>' in c
    assert '<span class="pedido-card-unidades">13 unidades</span>' in c
    assert '<dd class="pedido-card-obs">Embalar para presente</dd>' in c
    assert f'href="/pedidos/{pedido.id}"' in c and ">Ver pedido</a>" in c


def test_card_nao_mostra_contato_data_entrega_nem_total(repo_pedidos):
    novo_pedido(repo_pedidos, tipo_entrega="retirada")

    c = card(client.get("/pedidos").text, "Cliente Teste")

    for removido in (
        "(11) 98765-4321",
        "Contato",
        "01/10/2026",
        "Cadastrado em",
        "Retirada",
        "Entrega",
        "R$ 132,00",
        "Total",
    ):
        assert removido not in c, removido


@pytest.mark.parametrize(("status", "rotulo"), [("pendente", "Pendente"), ("pago", "Pago")])
def test_card_mostra_o_status_com_icone_e_texto(repo_pedidos, status, rotulo):
    novo_pedido(repo_pedidos, status_pagamento=status)

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert f'<span class="selo-status selo-status-{status}">' in c
    selo = c.split(f"selo-status-{status}")[1].split("</span>")[0]
    assert "<svg" in selo and selo.rstrip().endswith(rotulo)


def test_listagem_vazia_convida_a_cadastrar(repo_pedidos):
    html = client.get("/pedidos").text

    assert "Nenhum pedido cadastrado" in html
    assert (
        '<a class="botao botao-primario" href="/pedidos/novo">Cadastrar primeiro pedido</a>' in html
    )
    assert "lista-pedidos" not in html


def test_listagem_do_mais_recente_para_o_mais_antigo(repo_pedidos):
    for nome in ("Primeira", "Segunda", "Terceira"):
        novo_pedido(repo_pedidos, cliente=nome)

    html = client.get("/pedidos").text

    posicoes = [html.index(f"<h2>{nome}</h2>") for nome in ("Terceira", "Segunda", "Primeira")]
    assert posicoes == sorted(posicoes)


@pytest.mark.parametrize(
    ("quantidades", "texto"),
    [([11], "11 unidades"), ([1, 2, 3], "6 unidades"), ([1], "1 unidade")],
)
def test_card_soma_as_quantidades_e_lista_todos_os_produtos(repo_pedidos, quantidades, texto):
    itens = [item(n, f"Produto {n}", q, "1.00") for n, q in enumerate(quantidades, 1)]
    novo_pedido(repo_pedidos, itens=itens)

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert f'<span class="pedido-card-unidades">{texto}</span>' in c
    nomes = ", ".join(f"Produto {n}" for n in range(1, len(quantidades) + 1))
    assert f'<span class="pedido-card-produtos">{nomes}</span>' in c


def test_card_sem_observacoes(repo_pedidos):
    novo_pedido(repo_pedidos, observacoes=None)

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert '<dd class="pedido-card-vazio">Sem observações</dd>' in c


def caminho_de(pedido_id, ordem):
    return f"pedidos/{pedido_id}/itens/{ordem}/00000000-0000-4000-8000-00000000000{ordem}.png"


def pedido_com_fotos(repo, especificacao, cliente="Cliente Teste"):
    """especificacao: lista de (quantidade, tem_imagem) na ordem dos itens."""
    pedido_id = repo.proximo_id
    itens = []
    for ordem, (quantidade, tem_imagem) in enumerate(especificacao, 1):
        caminho = caminho_de(pedido_id, ordem) if tem_imagem else None
        if caminho:
            repo.arquivos[caminho] = (b"\x89PNG", "image/png")
        itens.append(item(ordem, f"Produto {ordem}", quantidade, "1.00", caminho))
    return novo_pedido(repo, cliente=cliente, itens=itens)


@pytest.mark.parametrize(
    ("especificacao", "ordem_escolhida"),
    [
        ([(2, True), (5, True), (9, False)], 2),  # maior quantidade entre os que têm imagem
        ([(3, True), (3, True)], 1),  # empate: o primeiro pela ordem
        ([(10, False), (1, True)], 2),  # primeiro sem imagem, segundo com
        ([(1, True)], 1),
    ],
)
def test_foto_de_destaque_segue_a_regra(repo_pedidos, especificacao, ordem_escolhida):
    pedido = pedido_com_fotos(repo_pedidos, especificacao)

    html = client.get("/pedidos").text

    escolhido = caminho_de(pedido.id, ordem_escolhida)
    assert repo_pedidos.assinaturas == [[escolhido]]
    url = "https://armazenamento.exemplo/assinada/0.img?token=falso"
    c = card(html, "Cliente Teste")
    assert 'class="pedido-card tem-foto"' in c
    assert f'<img class="pedido-card-foto" src="{url}"' in c
    assert f'alt="Foto de referência: Produto {ordem_escolhida}"' in c
    assert escolhido not in html  # o caminho interno não vai para a tela


def test_pedido_sem_imagem_nao_tem_foto_nem_espaco(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(2, False), (3, False)])

    html = client.get("/pedidos").text

    c = card(html, "Cliente Teste")
    assert 'class="pedido-card"' in c and "tem-foto" not in c
    assert "<img" not in c and "pedido-card-aviso" not in c
    assert repo_pedidos.assinaturas == []  # nada para assinar


def test_no_maximo_uma_url_assinada_por_pedido_em_uma_chamada(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(1, True), (2, True), (3, True)], cliente="Ana")
    pedido_com_fotos(repo_pedidos, [(1, False)], cliente="Bia")
    pedido_com_fotos(repo_pedidos, [(4, True), (4, True)], cliente="Caio")

    html = client.get("/pedidos").text

    assert len(repo_pedidos.assinaturas) == 1  # um único pedido ao Storage
    (caminhos,) = repo_pedidos.assinaturas
    assert caminhos == [caminho_de(3, 1), caminho_de(1, 3)]  # Caio (mais recente) e Ana
    assert html.count('class="pedido-card-foto"') == 2


def test_falha_ao_assinar_mostra_os_cards_sem_foto_com_aviso(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(1, True)], cliente="Com foto")
    pedido_com_fotos(repo_pedidos, [(1, False)], cliente="Sem foto")
    repo_pedidos.falhar_em["assinar_imagens"] = FalhaAoConsultar()

    resposta = client.get("/pedidos")

    assert resposta.status_code == 200
    html = resposta.text
    assert "<img" not in html.split("<main")[1]
    assert "Foto indisponível no momento." in card(html, "Com foto")
    assert "Foto indisponível" not in card(html, "Sem foto")


def test_foto_que_nao_existe_no_bucket_vira_aviso(repo_pedidos):
    pedido = pedido_com_fotos(repo_pedidos, [(1, True)])
    del repo_pedidos.arquivos[caminho_de(pedido.id, 1)]

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert "<img" not in c
    assert "Foto indisponível no momento." in c


def test_falha_na_listagem_mostra_mensagem_simples(repo_pedidos):
    repo_pedidos.falhar_em["listar_pedidos"] = FalhaAoConsultar()

    resposta = client.get("/pedidos")

    assert resposta.status_code == 503
    assert "Não foi possível carregar os pedidos agora" in resposta.text
    assert "FalhaAoConsultar" not in resposta.text
    assert "Cadastrar primeiro pedido" not in resposta.text


def test_sem_configuracao_do_banco_a_listagem_fica_indisponivel():
    from app.pedidos_repositorio import get_repositorio_pedidos

    app.dependency_overrides[get_repositorio_pedidos] = lambda: None

    assert client.get("/pedidos").status_code == 503
    assert client.get("/pedidos/1").status_code == 503


def test_nome_do_cliente_e_escapado(repo_pedidos):
    novo_pedido(repo_pedidos, cliente="<script>alert(1)</script>")

    html = client.get("/pedidos").text

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------- Detalhes ----------


def test_detalhes_completos(repo_pedidos):
    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(1, "Caneca", 11, "12.00"), item(2, "Camiseta", 2, "30.50")],
        observacoes="Embalar para presente\nEntregar à tarde",
        criado_por=2,
    )

    resposta = client.get(f"/pedidos/{pedido.id}")

    assert resposta.status_code == 200
    html = resposta.text
    assert "<h1>Pedido de Cliente Teste</h1>" in html
    assert "<dt>Nome</dt><dd>Cliente Teste</dd>" in html
    assert "<dt>Contato</dt><dd>(11) 98765-4321</dd>" in html
    assert "<dt>Pagamento</dt><dd>PIX</dd>" in html
    assert "<dt>Entrega</dt><dd>Entrega em mãos</dd>" in html
    assert "<dt>Cadastrado por</dt><dd>Kassia</dd>" in html
    assert "01/10/2026 às 09:00" in html
    assert "Embalar para presente\nEntregar à tarde" in html
    assert "<dt>Unidades</dt><dd>13</dd>" in html
    assert "<dt>Total do pedido</dt><dd>R$ 193,00</dd>" in html
    for texto in ("R$ 12,00", "R$ 132,00", "R$ 30,50", "R$ 61,00"):
        assert texto in html
    assert html.index("<span>Caneca</span>") < html.index("<span>Camiseta</span>")
    assert '<a class="botao botao-secundario" href="/pedidos">Voltar para pedidos</a>' in html
    assert '<a class="botao botao-primario" href="/pedidos/novo">Novo pedido</a>' in html


def test_itens_na_ordem_original(repo_pedidos):
    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(2, "Segundo", 1, "1.00"), item(1, "Primeiro", 1, "1.00")],
    )

    html = client.get(f"/pedidos/{pedido.id}").text

    assert html.index("<span>Primeiro</span>") < html.index("<span>Segundo</span>")


def test_sem_observacoes(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    assert "Sem observações." in client.get(f"/pedidos/{pedido.id}").text


@pytest.mark.parametrize("pedido_id", ["999", "0", "abc", "1.5", "-1", "99999999999999999999", "١"])
def test_pedido_inexistente_tem_pagina_404_amigavel(repo_pedidos, pedido_id):
    novo_pedido(repo_pedidos)

    resposta = client.get(f"/pedidos/{pedido_id}")

    assert resposta.status_code == 404
    assert "<h1>Pedido não encontrado</h1>" in resposta.text
    assert 'href="/pedidos">Voltar para pedidos</a>' in resposta.text
    for tecnico in ("Traceback", "detail", "Not Found", "int_parsing"):
        assert tecnico not in resposta.text


def test_falha_ao_consultar_mostra_mensagem_simples(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.falhar_em["consultar_pedido"] = FalhaAoConsultar()

    resposta = client.get(f"/pedidos/{pedido.id}")

    assert resposta.status_code == 503
    assert "Não foi possível carregar os pedidos agora" in resposta.text


def test_item_sem_imagem_nao_tem_espaco_de_imagem(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    html = client.get(f"/pedidos/{pedido.id}").text

    assert "detalhe-miniatura" not in html.split("<ol")[1].split("</ol>")[0]
    assert "<img" not in html.split("<ol")[1].split("</ol>")[0]
    assert repo_pedidos.assinaturas == []  # nada a assinar


def test_item_com_imagem_usa_url_assinada_sem_mostrar_o_caminho(repo_pedidos):
    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(1, "Sem foto", 1, "1.00"), item(2, "Com foto", 1, "2.00")],
    )
    caminho = com_imagem(repo_pedidos, pedido.id, 2)
    itens = (pedido.itens[0], item(2, "Com foto", 1, "2.00", caminho))
    repo_pedidos.pedidos[pedido.id] = PedidoParaGravar(**{**vars(pedido), "itens": itens})

    html = client.get(f"/pedidos/{pedido.id}").text

    url = "https://armazenamento.exemplo/assinada/0.img?token=falso"
    assert repo_pedidos.assinaturas == [[caminho]]
    assert f'<img src="{url}" alt="Imagem de referência de Com foto"' in html
    assert f'href="{url}"' in html and "data-ampliar" in html
    assert html.count("detalhe-miniatura") == 1
    assert caminho not in html
    assert 'id="imagem-ampliada"' in html
    assert "/static/js/pedido-detalhe.js" in html


def test_imagem_que_nao_existe_no_bucket_nao_aparece(repo_pedidos):
    caminho = "pedidos/1/itens/1/00000000-0000-4000-8000-000000000001.png"
    pedido = novo_pedido(repo_pedidos, itens=[item(1, "Caneca", 1, "5.00", caminho)])
    # nenhum arquivo guardado: o Storage não assina

    html = client.get(f"/pedidos/{pedido.id}").text

    assert repo_pedidos.assinaturas == [[caminho]]
    assert "detalhe-miniatura" not in html.split("<ol")[1]


def test_falha_ao_assinar_mostra_o_pedido_sem_imagens(repo_pedidos):
    pedido = novo_pedido(repo_pedidos, itens=[item(1, "Caneca", 1, "5.00")])
    caminho = com_imagem(repo_pedidos, pedido.id, 1)
    repo_pedidos.pedidos[pedido.id] = PedidoParaGravar(
        **{**vars(pedido), "itens": (item(1, "Caneca", 1, "5.00", caminho),)}
    )
    repo_pedidos.falhar_em["assinar_imagens"] = FalhaAoConsultar()

    resposta = client.get(f"/pedidos/{pedido.id}")

    assert resposta.status_code == 200
    assert "Não foi possível carregar as imagens agora" in resposta.text
    assert "<span>Caneca</span>" in resposta.text
    assert "detalhe-miniatura" not in resposta.text.split("<ol")[1]


# ---------- Segurança e navegação ----------


@pytest.mark.sem_login
@pytest.mark.parametrize("caminho", ["/pedidos", "/pedidos/1", "/pedidos/abc", "/pedidos/novo"])
def test_rotas_exigem_login(repo_pedidos, caminho):
    novo_pedido(repo_pedidos)

    resposta = TestClient(app).get(caminho, follow_redirects=False)

    assert resposta.status_code == 303
    assert resposta.headers["location"].startswith("/entrar")
    assert repo_pedidos.assinaturas == []


def test_consulta_nao_fica_em_cache(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)

    for caminho in ("/pedidos", f"/pedidos/{pedido.id}", "/pedidos/999"):
        assert client.get(caminho).headers["cache-control"] == "no-store"


def test_navegador_nao_acessa_o_supabase_diretamente(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    paginas = [client.get("/pedidos").text, client.get(f"/pedidos/{pedido.id}").text]
    js = client.get("/static/js/pedido-detalhe.js").text
    codigo = " ".join(linha.split("//")[0] for linha in js.splitlines()).lower()

    for html in paginas:
        for proibido in ("supabase", "secret", "service_role", "apikey"):
            assert proibido not in html.lower()
    for proibido in ("fetch(", "xmlhttprequest", "supabase", "localstorage", "sessionstorage"):
        assert proibido not in codigo


def test_consultas_nao_alteram_pedidos(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    escritas = repo_pedidos.escritas
    antes = dict(repo_pedidos.pedidos)

    for caminho in ("/pedidos", f"/pedidos/{pedido.id}", "/pedidos/999", "/pedidos/abc"):
        client.get(caminho)

    assert repo_pedidos.escritas == escritas
    assert repo_pedidos.pedidos == antes


def test_home_leva_para_a_listagem_e_para_novo_pedido():
    html = client.get("/").text

    assert '<a class="botao botao-primario" href="/pedidos">Ver pedidos</a>' in html
    assert '<a class="botao botao-secundario" href="/pedidos/novo">Novo pedido</a>' in html


def test_novo_pedido_continua_funcionando():
    resposta = client.get("/pedidos/novo")

    assert resposta.status_code == 200
    assert "<h1>Novo pedido</h1>" in resposta.text
