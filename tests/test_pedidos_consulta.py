"""Consulta de pedidos (listagem e detalhes) com repositório e Storage falsos.

Nenhum teste acessa o Supabase; os pedidos são criados só no repositório em memória.
"""

import re
from dataclasses import replace
from datetime import date
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
        "prazo_entrega": date(2026, 10, 5),
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
    """HTML da linha da tabela do cliente (do <tr> até o fim da linha)."""
    inicio = html.index(f'data-cliente="{cliente}"')
    return html[html.rindex("<tr", 0, inicio) : html.index("</tr>", inicio)]


def celulas(html, cliente):
    """Conteúdo de cada coluna da linha, na ordem da tabela."""
    return re.findall(r"<td[^>]*>(.*?)</td>", card(html, cliente), flags=re.S)


def _clientes(html):
    return re.findall(r'<tr data-pedido="\d+" data-cliente="([^"]*)"', html)


def test_tabela_tem_as_colunas_na_ordem_pedida(repo_pedidos):
    novo_pedido(repo_pedidos)

    html = client.get("/pedidos").text

    # Títulos com filtro trazem o texto no 1º <span> (Foto, Quantidade, Valor e Ações não filtram).
    cabecalho = re.findall(
        r'<th scope="col"[^>]*>\s*(?:<span class="th-conteudo">\s*<span>)?([^<]*)', html
    )
    assert cabecalho == [
        "Foto",
        "Cliente",
        "Produto",
        "Quantidade",
        "Pagamento",
        "Valor Total",
        "Prazo",
        "Status Pagamento",
        "Status Entrega",
        "Observações",
        "Ações",
    ]
    assert "lista-pedidos" not in html


def test_linha_mostra_os_dados_do_pedido(repo_pedidos):
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
    (
        _foto,
        cliente,
        produto,
        quantidade,
        pagamento,
        valor,
        prazo,
        status_pag,
        status_prod,
        obs,
        _acoes,
    ) = celulas(html, "Cliente Teste")
    assert f'href="/pedidos/{pedido.id}"' in cliente and "Cliente Teste" in cliente
    assert '<span class="pedido-card-produtos">Caneca, Camiseta</span>' in produto
    assert quantidade.strip() == "13"
    assert pagamento.strip() == "Cartão"
    assert valor.strip() == "R$ 192,00"
    assert '<time datetime="2026-10-05">05/10/26</time>' in prazo
    assert 'data-status="pago"' in status_pag and "Pago" in status_pag
    assert "Na Fila" in status_prod
    assert "Embalar para presente" in obs
    assert "Marise" not in card(html, "Cliente Teste")  # "Cadastrado por" saiu da lista


def test_linha_nao_mostra_contato_data_de_cadastro_nem_entrega(repo_pedidos):
    novo_pedido(repo_pedidos, tipo_entrega="retirada")

    c = card(client.get("/pedidos").text, "Cliente Teste")

    for removido in ("(11) 98765-4321", "Contato", "01/10/2026", "Retirada"):
        assert removido not in c, removido


def test_valor_total_ausente_aparece_como_traco(repo_pedidos, monkeypatch):
    """Banco ainda sem a migration do valor na listagem: a coluna mostra um traço."""
    novo_pedido(repo_pedidos)
    listar = repo_pedidos.listar_pedidos
    monkeypatch.setattr(
        repo_pedidos,
        "listar_pedidos",
        lambda: [replace(p, valor_total=None) for p in listar()],
    )

    assert celulas(client.get("/pedidos").text, "Cliente Teste")[5].strip() == "—"


@pytest.mark.parametrize(("status", "rotulo"), [("pendente", "Pendente"), ("pago", "Pago")])
def test_status_do_pagamento_e_botao_com_icone_e_texto(repo_pedidos, status, rotulo):
    novo_pedido(repo_pedidos, status_pagamento=status)

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert f'class="selo-status selo-status-{status} producao-pagamento"' in c
    assert f'data-status="{status}"' in c and "<svg" in c and "<button" in c
    assert f'<span class="producao-pagamento-texto">{rotulo}</span>' in c


@pytest.mark.parametrize(
    ("etapa", "opcoes"),
    [
        ("fila_producao", ["Na Fila", "Em Produção", "Finalizado"]),
        ("em_producao", ["Em Produção", "Na Fila", "Ag. Entrega", "Finalizado"]),
        ("aguardando_entrega", ["Ag. Entrega", "Finalizado"]),
    ],
)
def test_status_da_producao_e_selecao_com_a_etapa_atual_e_destinos_permitidos(
    repo_pedidos, etapa, opcoes
):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = etapa

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert f'<select class="campo-texto pedidos-etapa" data-etapa="{etapa}"' in c
    assert re.findall(r"<option[^>]*>(.*?)</option>", c) == opcoes
    assert f'<option value="{etapa}" selected>' in c


def test_pedido_finalizado_nao_tem_selecao(repo_pedidos):
    pedido = novo_pedido(repo_pedidos)
    repo_pedidos.etapas[pedido.id] = "entregue"

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert "<select" not in c
    assert "Finalizado" in c


def test_pagina_carrega_o_script_e_o_token_da_tabela(repo_pedidos):
    novo_pedido(repo_pedidos)

    html = client.get("/pedidos").text

    assert "js/pedidos-lista.js" in html
    assert re.search(r'<table class="tabela-pedidos" id="tabela-pedidos" data-csrf="[^"]+"', html)


def test_listagem_vazia_convida_a_cadastrar(repo_pedidos):
    html = client.get("/pedidos").text

    assert "Nenhum pedido cadastrado" in html
    assert (
        '<a class="botao botao-primario" href="/pedidos/novo">Cadastrar primeiro pedido</a>' in html
    )
    assert "tabela-pedidos" not in html


def test_total_de_pedidos_e_filtro_por_status(repo_pedidos):
    novo_pedido(repo_pedidos, cliente="Ana")
    bia = novo_pedido(repo_pedidos, cliente="Bia")
    cris = novo_pedido(repo_pedidos, cliente="Cris")
    dani = novo_pedido(repo_pedidos, cliente="Dani", status_pagamento="pago")
    repo_pedidos.etapas[bia.id] = "em_producao"
    repo_pedidos.etapas[cris.id] = "aguardando_entrega"
    repo_pedidos.etapas[dani.id] = "entregue"  # entregue e pago: fora do quadro

    html = client.get("/pedidos").text
    assert "Total de Pedidos" in html
    assert '<strong class="pedidos-total-valor">4</strong>' in html
    assert sorted(_clientes(html)) == ["Ana", "Bia", "Cris", "Dani"]
    for rotulo in ("Na Fila", "Em Produção", "Ag. Entrega", "Finalizado"):
        assert rotulo in html

    esperado = {
        "fila_producao": ["Ana"],
        "em_producao": ["Bia"],
        "aguardando_entrega": ["Cris"],
        "entregue": ["Dani"],
    }
    for etapa, clientes in esperado.items():
        filtrado = client.get(f"/pedidos?status={etapa}").text
        assert _clientes(filtrado) == clientes
        assert '<strong class="pedidos-total-valor">4</strong>' in filtrado


def test_filtro_com_status_desconhecido_mostra_todos(repo_pedidos):
    novo_pedido(repo_pedidos, cliente="Ana")
    html = client.get("/pedidos?status=qualquer").text
    assert _clientes(html) == ["Ana"]


def test_filtro_sem_pedidos_no_status(repo_pedidos):
    novo_pedido(repo_pedidos, cliente="Ana")
    html = client.get("/pedidos?status=entregue").text
    assert "Nenhum pedido neste status" in html


def test_listagem_do_mais_recente_para_o_mais_antigo(repo_pedidos):
    for nome in ("Primeira", "Segunda", "Terceira"):
        novo_pedido(repo_pedidos, cliente=nome)

    assert _clientes(client.get("/pedidos").text) == ["Terceira", "Segunda", "Primeira"]


@pytest.mark.parametrize(
    ("quantidades", "texto"),
    [([11], "11"), ([1, 2, 3], "6"), ([1], "1")],
)
def test_linha_soma_as_quantidades_e_lista_todos_os_produtos(repo_pedidos, quantidades, texto):
    itens = [item(n, f"Produto {n}", q, "1.00") for n, q in enumerate(quantidades, 1)]
    novo_pedido(repo_pedidos, itens=itens)

    html = client.get("/pedidos").text
    c = card(html, "Cliente Teste")

    assert celulas(html, "Cliente Teste")[3].strip() == texto
    nomes = ", ".join(f"Produto {n}" for n in range(1, len(quantidades) + 1))
    assert f'<span class="pedido-card-produtos">{nomes}</span>' in c


def test_linha_sem_observacoes(repo_pedidos):
    novo_pedido(repo_pedidos, observacoes=None)

    c = card(client.get("/pedidos").text, "Cliente Teste")

    assert '<span class="pedido-card-vazio">Sem observações</span>' in c


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
    foto = celulas(html, "Cliente Teste")[0]
    assert f'<img class="pedido-tabela-foto" src="{url}"' in foto
    assert f'alt="Foto de referência: Produto {ordem_escolhida}"' in foto
    assert escolhido not in html  # o caminho interno não vai para a tela


def test_pedido_sem_imagem_nao_tem_foto(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(2, False), (3, False)])

    html = client.get("/pedidos").text

    foto = celulas(html, "Cliente Teste")[0]
    assert "<img" not in foto and "pedido-card-aviso" not in foto
    assert repo_pedidos.assinaturas == []  # nada para assinar


def test_no_maximo_uma_url_assinada_por_pedido_em_uma_chamada(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(1, True), (2, True), (3, True)], cliente="Ana")
    pedido_com_fotos(repo_pedidos, [(1, False)], cliente="Bia")
    pedido_com_fotos(repo_pedidos, [(4, True), (4, True)], cliente="Caio")

    html = client.get("/pedidos").text

    assert len(repo_pedidos.assinaturas) == 1  # um único pedido ao Storage
    (caminhos,) = repo_pedidos.assinaturas
    assert caminhos == [caminho_de(3, 1), caminho_de(1, 3)]  # Caio (mais recente) e Ana
    assert html.count('class="pedido-tabela-foto"') == 2


def test_falha_ao_assinar_mostra_a_tabela_sem_foto_com_aviso(repo_pedidos):
    pedido_com_fotos(repo_pedidos, [(1, True)], cliente="Com foto")
    pedido_com_fotos(repo_pedidos, [(1, False)], cliente="Sem foto")
    repo_pedidos.falhar_em["assinar_imagens"] = FalhaAoConsultar()

    resposta = client.get("/pedidos")

    assert resposta.status_code == 200
    html = resposta.text
    assert "<img" not in html.split("<main")[1]
    assert "Indisponível" in celulas(html, "Com foto")[0]
    assert "Indisponível" not in celulas(html, "Sem foto")[0]


def test_foto_que_nao_existe_no_bucket_vira_aviso(repo_pedidos):
    pedido = pedido_com_fotos(repo_pedidos, [(1, True)])
    del repo_pedidos.arquivos[caminho_de(pedido.id, 1)]

    foto = celulas(client.get("/pedidos").text, "Cliente Teste")[0]

    assert "<img" not in foto
    assert "Indisponível" in foto


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
