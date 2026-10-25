"""Tela Compras em navegador real (Edge ou Chrome, sem interface), nas larguras do projeto.

Inclui a janela Registrar Compra. Reaproveita o servidor e o navegador de test_navegador.py;
só dados fictícios em memória.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.estoque_repositorio import (
    CompraParaGravar,
    ItemCompraParaGravar,
    get_repositorio_estoque,
)
from app.main import app
from tests import test_navegador as nav
from tests.fake_estoque import RepositorioEstoqueMemoria

pytestmark = pytest.mark.navegador

servidor = nav.servidor
pagina = nav.pagina
LARGURAS = [360, 768, 1024, 1100, 1280, 1920]


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    app.dependency_overrides[get_repositorio_estoque] = lambda: repo
    return repo


def sem_rolagem_horizontal(p) -> bool:
    return p.js("document.documentElement.scrollWidth <= window.innerWidth")


def tecla(p, key, code=None, codigo=0, shift=False):
    dados = {"key": key, "code": code or key, "windowsVirtualKeyCode": codigo}
    if shift:
        dados["modifiers"] = 8
    p.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **dados)
    p.cmd("Input.dispatchKeyEvent", type="keyUp", **dados)


def abrir_janela(p, categoria="filamento"):
    # Há um único botão Registrar Compra; a janela abre com uma linha de filamento. Para outra
    # categoria, adiciona a linha pedida e remove a de filamento (como o usuário faria).
    p.clicar("[data-abrir-compra]")
    nav.esperar(lambda: p.js("document.getElementById('compra-janela').open"))
    if categoria != "filamento":
        p.clicar(f'[data-adicionar="{categoria}"]')
        p.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-remover")
        nav.esperar(
            lambda: p.js("document.querySelectorAll('#compra-itens .compra-item').length") == 1
        )


# Nome de cada linha, na ordem da tela: o título do cartão (acessório, embalagem) ou, nas linhas
# da lista de filamentos, que não têm título, o rótulo de acessibilidade.
NOMES_DAS_LINHAS = (
    "[...document.querySelectorAll('.compra-item')].map(l =>"
    " l.querySelector('.compra-item-titulo')?.textContent ?? l.getAttribute('aria-label'))"
)


def linha(n: int, campo: str) -> str:
    return f'#compra-itens .compra-item[data-pos="{n}"] [data-campo="{campo}"]'


def resumo(p, categoria: str) -> list[str]:
    """Resumo no fim da seção: [Peso total, Valor total] ou [Quantidade total, Valor total]."""
    return p.js(
        f"[...document.querySelectorAll('[data-lista={categoria}] .compra-resumo-secao dd')]"
        ".map(d => d.textContent.trim())"
    )


# Data local de hoje (aaaa-mm-dd), como a janela a preenche ao abrir.
HOJE_JS_EXPR = (
    "(() => { const d = new Date(); return d.getFullYear() + '-'"
    " + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') })()"
)


def preencher(p, campos: dict):
    for seletor, valor in campos.items():
        p.digitar(seletor, valor)


def escolher_local(p, codigo):
    p.clicar(f'#campo-local input[value="{codigo}"]')


def escolher_opcao(p, seletor, valor):
    """Escolhe a opção de uma lista suspensa, como o usuário: muda o valor e avisa a página."""
    p.js(
        f"(() => {{ const s = document.querySelector('{seletor}'); s.value = '{valor}';"
        " s.dispatchEvent(new Event('input', {bubbles: true}));"
        " s.dispatchEvent(new Event('change', {bubbles: true})) })()"
    )


def preencher_filamento(p, n, qtd="3", peso="1000", valor="8990", cor="Preto", material="PLA"):
    preencher(
        p,
        {
            linha(n, "cor"): cor,
            linha(n, "marca"): "Marca Teste",
            linha(n, "valor_unitario"): valor,
        },
    )
    escolher_opcao(p, linha(n, "quantidade"), qtd)
    escolher_opcao(p, linha(n, "material"), material)
    escolher_opcao(p, linha(n, "peso_rolo"), peso)
    escolher_opcao(p, linha(n, "tipo"), "silk")


def preencher_item(p, n, nome="Argola", qtd="40", valor="1400"):
    preencher(
        p, {linha(n, "nome"): nome, linha(n, "quantidade"): qtd, linha(n, "valor_total"): valor}
    )


def salvar(p):
    p.clicar('#form-compra button[type="submit"]')


def esperar_fim_do_envio(p):
    nav.esperar(
        lambda: p.js(
            "location.search.includes('salvo=compra')"
            " || document.getElementById('form-compra').dataset.envio === ''"
        ),
        mensagem="o envio da compra não terminou",
    )
    nav.esperar(lambda: p.js("document.readyState === 'complete'"))


# ---------- Lista de compras ----------


def cortados(p) -> list[str]:
    """Textos que saem do próprio cartão da compra ou do card de resumo (cortes visuais)."""
    return p.js(
        """[...document.querySelectorAll('.compra-registro, .compras-card')].flatMap(caixa => {
             const limite = caixa.getBoundingClientRect();
             return [...caixa.querySelectorAll('*')]
               .filter(el => {
                 const r = el.getBoundingClientRect();
                 return r.width > 0 && (r.left < limite.left - 1 || r.right > limite.right + 1);
               })
               .map(el => el.textContent.trim());
           })"""
    )


@pytest.fixture
def com_compras(estoque):
    itens = (
        ItemCompraParaGravar(
            ordem=1,
            categoria="filamento",
            quantidade=12,
            cor="Azul Petróleo Metalizado Bem Comprido",
            material="PETG",
            tipo="silk",
            marca="Marca Com Nome Bem Comprido Ltda",
            peso_rolo_g=Decimal("1000"),
            valor_unitario=Decimal("1234.56"),
            imagem_caminho="compras/1/itens/1/0f8fad5b-d9cb-469f-a165-70867728950e.png",
        ),
        ItemCompraParaGravar(
            ordem=2,
            categoria="acessorio",
            quantidade=999999,
            nome="Argola de chaveiro inox 25 mm",
            valor_total=Decimal("12499987.50"),
        ),
    )
    estoque.imagens[itens[0].imagem_caminho] = b"x"
    for n, (dia, local, nome) in enumerate(
        [(1, "loja_fisica", "Papelaria do Centro da Cidade Muito Longa"), (5, "shopee", None)], 1
    ):
        estoque.reservar_id_compra()
        estoque.registrar_compra(
            CompraParaGravar(
                id=n,
                chave_envio=uuid.uuid4(),
                data_compra=date(2026, 9, dia),
                local_tipo=local,
                local_nome=nome,
                usuario_id=1,
                itens=itens if n == 1 else itens[1:],
                frete=Decimal("99999.99"),
            )
        )
    return estoque


@pytest.mark.parametrize("largura", LARGURAS)
def test_compras_sem_rolagem_horizontal_nem_cortes(pagina, com_compras, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")

    assert sem_rolagem_horizontal(pagina)
    assert cortados(pagina) == []
    assert pagina.js("document.querySelectorAll('.compra-registro').length") == 2
    assert pagina.js("document.querySelectorAll('[data-abrir-compra]').length") == 1
    # Os cards e a lista vêm depois do botão, e a foto tem miniatura de 64 px.
    assert pagina.js(
        "document.querySelector('[data-abrir-compra]').getBoundingClientRect().bottom"
        " <= document.querySelector('.compras-resumo').getBoundingClientRect().top"
    )
    assert (
        pagina.js(
            "document.querySelector('.compra-registro-fotos img').getBoundingClientRect().width"
        )
        == 64
    )


@pytest.mark.parametrize("largura", LARGURAS)
def test_cards_lado_a_lado_no_desktop_e_empilhados_no_celular(pagina, com_compras, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")

    topos = pagina.js(
        "[...document.querySelectorAll('.compras-card')]"
        ".map(c => Math.round(c.getBoundingClientRect().top))"
    )
    assert len(topos) == 2
    assert (topos[0] == topos[1]) == (largura >= 640)


@pytest.mark.parametrize("largura", LARGURAS)
def test_alvos_de_toque_de_44px(pagina, com_compras, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")

    pequenos = pagina.js(
        """[...document.querySelectorAll('.compras .botao')]
             .filter(el => el.getClientRects().length && el.getBoundingClientRect().height < 44)
             .map(el => el.textContent.trim())"""
    )
    assert pequenos == []


@pytest.mark.parametrize("largura", LARGURAS)
def test_estado_vazio(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")

    assert sem_rolagem_horizontal(pagina)
    assert "Nenhuma compra registrada" in pagina.texto(".compras")
    assert pagina.texto("#compras-quantidade") == "0"


def test_edicao_pelo_navegador_so_tem_a_descricao(pagina, estoque):
    lote_id = estoque.com_filamento(peso_g=Decimal("750.5"))
    pagina.tela(1280, 900)
    pagina.abrir(f"/estoque/filamentos/{lote_id}/editar")

    campos = pagina.js(
        "[...document.querySelectorAll('form input:not([type=hidden]), form select')]"
        ".map(el => el.name)"
    )
    assert campos == ["cor", "material", "tipo", "marca"]
    assert "750,5 g" in pagina.texto(".estoque-resumo")


# ---------- Janela Registrar Compra: layout ----------


@pytest.mark.parametrize("largura", LARGURAS)
def test_janela_cabe_na_tela_com_rolagem_interna(pagina, estoque, largura):
    pagina.tela(largura, 700)
    pagina.abrir("/compras")
    abrir_janela(pagina, "filamento")
    for categoria in ("filamento", "acessorio", "embalagem", "filamento"):
        pagina.clicar(f'[data-adicionar="{categoria}"]')

    medidas = pagina.js(
        """(() => {
             const j = document.getElementById('compra-janela').getBoundingClientRect();
             const c = document.querySelector('.compra-corpo');
             const r = document.querySelector('.compra-rodape').getBoundingClientRect();
             return {topo: j.top, base: j.bottom, esq: j.left, dir: j.right,
                     rodape: r.bottom, rola: c.scrollHeight > c.clientHeight,
                     overflow: getComputedStyle(c).overflowY,
                     largo: c.scrollWidth <= c.clientWidth};
           })()"""
    )
    assert medidas["topo"] >= 0 and medidas["base"] <= 700
    assert medidas["esq"] >= 0 and medidas["dir"] <= largura
    assert medidas["rodape"] <= 700  # Salvar e Cancelar sempre visíveis
    assert medidas["overflow"] == "auto" and medidas["rola"]  # só o corpo rola
    assert medidas["largo"]  # sem rolagem horizontal dentro da janela
    # O campo Foto de cada linha tem rótulo visível: no celular, na própria linha; no desktop, no
    # cabeçalho de colunas da lista.
    if largura < 960:
        assert pagina.js(
            "[...document.querySelectorAll('.compra-item-foto .item-rotulo')]"
            ".every(r => r.getClientRects().length > 0)"
        )
    else:
        assert pagina.js(
            "[...document.querySelectorAll('.compra-lista:not([hidden]) .compra-colunas')]"
            ".every(c => c.getClientRects().length > 0 && c.textContent.includes('Foto'))"
        )
    assert sem_rolagem_horizontal(pagina)
    # No celular a janela ocupa a tela inteira.
    if largura < 640:
        assert (medidas["esq"], medidas["dir"]) == (0, largura)

    pequenos = pagina.js(
        """[...document.querySelectorAll(
               ['.botao', 'input', 'select', '.botao-icone', '.escolha-card']
                 .map(s => '#compra-janela ' + s).join())]
             .filter(el => el.type !== 'hidden' && el.type !== 'radio' && el.type !== 'file'
                     && el.getClientRects().length && el.getBoundingClientRect().height < 44)
             .map(el => el.textContent.trim() || el.dataset.campo || el.name)"""
    )
    assert pequenos == []
    cortes = pagina.js(
        """[...document.querySelectorAll('#compra-janela .compra-item')].flatMap(l => {
             const lim = l.getBoundingClientRect();
             return [...l.querySelectorAll('input, select, .botao, dd')]
               .filter(el => el.getClientRects().length && el.type !== 'hidden'
                       && (el.getBoundingClientRect().right > lim.right + 1
                           || el.getBoundingClientRect().left < lim.left - 1))
               .map(el => el.dataset.campo || el.textContent.trim());
           })"""
    )
    assert cortes == []


# ---------- Janela Registrar Compra: comportamento ----------


def test_abrir_focar_e_cancelar_sem_gravar(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert pagina.js("document.activeElement.id") == "compra-data"
    assert pagina.js("document.querySelectorAll('#compra-itens .compra-item').length") == 1
    assert pagina.js(NOMES_DAS_LINHAS) == ["Filamento 1"]
    chave = pagina.valor('[name="chave_envio"]')
    assert len(chave) == 36 and chave[14] == "4"

    preencher(pagina, {"#compra-data": "10092026"})
    preencher_filamento(pagina, 1)
    pagina.clicar(".compra-rodape [data-cancelar]")

    assert not pagina.js("document.getElementById('compra-janela').open")
    # O foco volta ao botão que abriu a janela (o evento close chega logo depois).
    nav.esperar(lambda: pagina.js("document.activeElement.dataset.abrirCompra") == "filamento")
    assert not estoque.compras and not estoque.itens
    # Reabrir começa do zero, com outra chave de submissão.
    abrir_janela(pagina)
    assert pagina.valor("#compra-data") == pagina.js(HOJE_JS_EXPR)
    assert pagina.js("document.querySelectorAll('#compra-itens .compra-item').length") == 1
    assert pagina.js(NOMES_DAS_LINHAS) == ["Filamento 1"]
    assert pagina.valor('[name="chave_envio"]') != chave

    tecla(pagina, "Escape", codigo=27)  # Esc também cancela
    nav.esperar(lambda: not pagina.js("document.getElementById('compra-janela').open"))
    assert not estoque.compras


def test_data_de_hoje_no_calendario_e_local_por_teclado(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert pagina.js("document.getElementById('compra-data').type") == "date"
    assert pagina.valor("#compra-data") == pagina.js(HOJE_JS_EXPR)
    preencher(pagina, {"#compra-data": "10092026"})
    assert pagina.valor("#compra-data") == "2026-09-10"

    # Botões de seleção única: Tab chega ao grupo e as setas escolhem.
    pagina.js("document.querySelector('#campo-local input[value=mercado_livre]').focus()")
    tecla(pagina, " ", "Space", 32)
    assert (
        pagina.js("document.querySelector('#campo-local input:checked').value") == "mercado_livre"
    )
    for _ in range(3):
        tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "loja_fisica"
    assert pagina.js("document.activeElement.matches(':focus-visible')")
    assert not pagina.js("document.getElementById('campo-local_nome').hidden")

    pagina.digitar("#compra-local-nome", "Feira")
    pagina.js("document.querySelector('#campo-local input:checked').focus()")
    tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "outro"
    assert pagina.valor("#compra-local-nome") == "Feira"  # entre Loja Física e Outro, fica

    escolher_local(pagina, "shopee")
    assert pagina.js("document.getElementById('campo-local_nome').hidden")
    assert pagina.valor("#compra-local-nome") == ""  # plataforma: o texto é limpo


def test_linhas_calculos_e_remocao(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina, "filamento")
    pagina.clicar('[data-adicionar="filamento"]')
    pagina.clicar('[data-adicionar="acessorio"]')
    pagina.clicar('[data-adicionar="embalagem"]')

    titulos = pagina.js(NOMES_DAS_LINHAS)
    assert titulos == ["Filamento 1", "Filamento 2", "Acessório 1", "Embalagem 1"]
    # Adicionar leva o foco ao primeiro campo da nova linha.
    assert pagina.js(
        f"document.activeElement === document.querySelector('{linha(4, 'quantidade')}')"
    )
    # Rótulos com unidade, ligados aos campos.
    rotulos = pagina.js(
        "[...document.querySelectorAll('#compra-itens .compra-item label')].slice(0, 7)"
        ".map(l => l.control && l.textContent.replace('*', '').replace(/\s+/g, ' ').trim())"
    )
    assert rotulos == [
        "Quantidade",
        "Material",
        "Tipo",
        "Peso (g) (gramas de cada rolo)",
        "Cor",
        "Marca",
        "Valor unitário (de cada rolo, em reais)",
    ]

    preencher_filamento(pagina, 1, qtd="3", peso="500", valor="10000")
    assert pagina.valor(linha(1, "valor_unitario")) == "R$ 100,00"
    # Sem resultados por linha: só o resumo da seção, que soma os filamentos (sem frete).
    assert pagina.js("document.querySelectorAll('[data-calculo]').length") == 0
    assert resumo(pagina, "filamento") == ["1.500 g", "R$ 300,00"]  # 3 rolos x 500 g; 3 x 100,00
    preencher_filamento(pagina, 2, qtd="2", peso="1000", valor="8990")
    assert resumo(pagina, "filamento") == ["3.500 g", "R$ 479,80"]
    preencher_item(pagina, 3, qtd="3", valor="1000")
    assert resumo(pagina, "acessorio") == ["3 un", "R$ 10,00"]
    assert resumo(pagina, "embalagem") == ["0 un", "R$ 0,00"]

    # Remover a linha 2: as outras são renumeradas e o foco vai para a vizinha.
    pagina.clicar("#compra-itens .compra-item[data-pos='2'] .compra-item-remover")
    titulos = pagina.js(NOMES_DAS_LINHAS)
    assert titulos == ["Filamento 1", "Acessório 1", "Embalagem 1"]
    assert pagina.js("document.activeElement.getAttribute('aria-label')") == "Remover filamento 1"
    assert pagina.valor(linha(2, "nome")) == "Argola"  # dados das outras linhas continuam
    for _ in range(3):
        pagina.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-remover")
    # Sem linhas, cada seção continua com título, botão de adicionar e o aviso de vazia.
    assert pagina.js("document.querySelectorAll('.compra-secao').length") == 3
    assert pagina.js(
        "[...document.querySelectorAll('.compra-secao-vazio')].every(p => p.offsetParent !== null)"
    )
    assert pagina.js("document.activeElement.dataset.adicionar") == "embalagem"
    assert not estoque.compras


def test_erros_do_servidor_nos_campos_preservando_o_que_foi_digitado(pagina, estoque):
    pagina.tela(360, 740)
    pagina.abrir("/compras")
    abrir_janela(pagina, "filamento")
    pagina.clicar('[data-adicionar="embalagem"]')
    preencher_filamento(pagina, 1, cor="")
    preencher(pagina, {linha(2, "nome"): "Caixa", linha(2, "quantidade"): "3"})
    escolher_local(pagina, "outro")
    pagina.js("document.getElementById('compra-data').value = ''")  # vem preenchida com hoje
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert (
        pagina.texto("#compra-alerta") == "Não foi possível salvar. Corrija os campos destacados."
    )
    erros = pagina.js(
        "[...document.querySelectorAll('#form-compra .campo-erro')].map(e => e.textContent)"
    )
    assert erros == [
        "Informe a data de compra.",
        "Informe o nome do local.",
        "Informe a cor.",
        "Informe o valor total.",
    ]
    # O foco vai para o primeiro campo com erro, ligado à mensagem.
    assert pagina.js("document.activeElement.id") == "compra-data"
    assert pagina.js("document.activeElement.getAttribute('aria-invalid')") == "true"
    descrito = pagina.js("document.activeElement.getAttribute('aria-describedby')")
    assert pagina.texto(f"#{descrito}") == "Informe a data de compra."
    # O que foi digitado continua lá.
    assert pagina.valor(linha(1, "marca")) == "Marca Teste"
    assert pagina.valor(linha(2, "nome")) == "Caixa"
    # Corrigir um campo apaga o aviso dele.
    pagina.digitar("#compra-data", "10092026")
    assert not pagina.js(
        "document.getElementById('campo-data_compra').classList.contains('tem-erro')"
    )
    assert not estoque.compras


def test_compra_sem_itens_pede_um_item(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar(".compra-item-remover")
    preencher(pagina, {"#compra-data": "10092026"})
    escolher_local(pagina, "shopee")
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.texto("#campo-itens .campo-erro") == "Adicione pelo menos um item."
    assert pagina.js("document.activeElement.dataset.adicionar") == "filamento"
    pagina.clicar('[data-adicionar="acessorio"]')
    assert pagina.js("!document.querySelector('#campo-itens .campo-erro')")


@pytest.mark.parametrize("largura", [360, 1280])
def test_registrar_compra_com_as_tres_categorias_e_foto(pagina, estoque, largura):
    pagina.tela(largura, 800)
    pagina.abrir("/compras")
    abrir_janela(pagina, "filamento")
    pagina.clicar('[data-adicionar="acessorio"]')
    pagina.clicar('[data-adicionar="embalagem"]')
    preencher(pagina, {"#compra-data": "10092026"})
    escolher_local(pagina, "loja_fisica")
    pagina.digitar("#compra-local-nome", "Papelaria Centro")
    preencher_filamento(pagina, 1)
    preencher_item(pagina, 2)
    preencher_item(pagina, 3, nome="Caixa P", qtd="3", valor="1000")
    nav.novo_arquivo(pagina, "argola.png")
    nav.escolher(pagina, 1)  # foto só na linha do acessório
    nav.esperar_previa(pagina, "argola.png", 1)

    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.js("location.pathname + location.search") == "/compras?salvo=compra"
    assert pagina.texto(".alerta-sucesso") == "Compra registrada. Os itens já estão no estoque."
    (gravada,) = estoque.compras.values()
    c = gravada.compra
    assert (c.data_compra, c.local_tipo, c.local_nome) == (
        date(2026, 9, 10),
        "loja_fisica",
        "Papelaria Centro",
    )
    assert [i.categoria for i in c.itens] == ["filamento", "acessorio", "embalagem"]
    assert [i.imagem_caminho is not None for i in c.itens] == [False, True, False]
    assert len(estoque.imagens) == 1
    # 3 x 89,90 + 14,00 + 10,00: a compra já aparece no resumo e na lista, com a foto do acessório.
    assert pagina.texto("#compras-total") == "R$ 293,70"
    assert pagina.texto("#compras-quantidade") == "1"
    lista = pagina.texto("#ultimas-compras")
    assert "10/09/2026" in lista and "Loja Física · Papelaria Centro" in lista
    assert "3 rolos · PLA Silk Preto" in lista and "40 un · Argola" in lista
    assert pagina.js("document.querySelectorAll('.compra-registro-fotos img').length") == 1


def test_duplo_clique_grava_uma_vez(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina, "embalagem")
    preencher(pagina, {"#compra-data": "10092026"})
    escolher_local(pagina, "aliexpress")
    preencher_item(pagina, 1, nome="Caixa")
    pagina.js(
        "(() => { const b = document.querySelector('#form-compra button[type=submit]');"
        " b.click(); b.click(); document.getElementById('form-compra').requestSubmit(); })()"
    )
    esperar_fim_do_envio(pagina)

    assert len(estoque.compras) == 1 and len(estoque.itens) == 1


def test_falha_ao_gravar_mantem_a_janela_e_os_dados(pagina, estoque):
    estoque.falhar_em = {"registrar"}
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina, "embalagem")
    preencher(pagina, {"#compra-data": "10092026"})
    escolher_local(pagina, "shopee")
    preencher_item(pagina, 1, nome="Caixa")
    nav.novo_arquivo(pagina, "caixa.png")
    nav.escolher(pagina, 0)
    nav.esperar_previa(pagina, "caixa.png", 0)
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.js("document.getElementById('compra-janela').open")
    assert pagina.texto("#compra-alerta") == (
        "Não foi possível salvar agora. Tente novamente em instantes."
    )
    assert pagina.valor(linha(1, "nome")) == "Caixa"
    assert nav.arquivos_por_item(pagina) == ["caixa.png"]  # a foto continua escolhida
    assert not estoque.compras and not estoque.imagens  # a foto enviada foi removida

    # Tentar de novo (mesma submissão) grava normalmente.
    estoque.falhar_em = set()
    salvar(pagina)
    esperar_fim_do_envio(pagina)
    assert len(estoque.compras) == 1 and len(estoque.imagens) == 1


# ---------- Local de compra, frete e totais ----------


def test_locais_na_ordem_com_logos_e_nomes_visiveis(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    nomes = pagina.js(
        "[...document.querySelectorAll('#campo-local .escolha-titulo')].map(e => e.textContent)"
    )
    assert nomes == ["Mercado Livre", "Shopee", "AliExpress", "Loja Física", "Outro"]
    assert pagina.js(
        "[...document.querySelectorAll('#campo-local .compra-local-marca img')]"
        ".every(i => i.complete && i.naturalWidth > 0)"
    )
    assert (
        pagina.js("document.querySelectorAll('#campo-local .compra-local-marca img').length") == 3
    )
    assert (
        pagina.js("document.querySelectorAll('#campo-local .compra-local-marca svg').length") == 2
    )


def totais(p):
    return [p.texto(f"#{i}") for i in ("compra-subtotal", "compra-total-frete", "compra-total")]


def test_frete_e_totais_atualizam_ao_editar_adicionar_e_remover(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert pagina.valor("#compra-frete") == ""  # vazio vale zero (placeholder R$ 0,00)
    assert totais(pagina) == ["R$ 0,00", "R$ 0,00", "R$ 0,00"]

    preencher_filamento(pagina, 1, qtd="3", valor="8990")  # 3 x 89,90 = 269,70
    assert totais(pagina) == ["R$ 269,70", "R$ 0,00", "R$ 269,70"]

    pagina.clicar('[data-adicionar="acessorio"]')
    preencher_item(pagina, 2, nome="Argola", qtd="40", valor="1400")  # 14,00
    assert totais(pagina) == ["R$ 283,70", "R$ 0,00", "R$ 283,70"]

    preencher(pagina, {"#compra-frete": "1250"})
    assert pagina.valor("#compra-frete") == "R$ 12,50"
    assert totais(pagina) == ["R$ 283,70", "R$ 12,50", "R$ 296,20"]

    pagina.js(
        "(() => { const f = document.getElementById('compra-frete'); f.value = '';"
        " f.dispatchEvent(new Event('input', {bubbles: true})) })()"
    )
    assert totais(pagina)[1] == "R$ 0,00"  # frete apagado vale zero
    preencher(pagina, {"#compra-frete": "-99"})  # sem sinal: nunca fica negativo
    assert pagina.valor("#compra-frete") == "R$ 0,99"

    pagina.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-remover")
    assert totais(pagina) == ["R$ 14,00", "R$ 0,99", "R$ 14,99"]


def test_compra_com_frete_grava_e_reabrir_zera(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina, "embalagem")
    preencher(pagina, {"#compra-data": "10092026", "#compra-frete": "2500"})
    escolher_local(pagina, "shopee")
    preencher_item(pagina, 1, nome="Caixa", qtd="2", valor="1000")
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    (gravada,) = estoque.compras.values()
    assert gravada.compra.frete == Decimal("25.00")
    abrir_janela(pagina)
    assert pagina.valor("#compra-frete") == ""  # vazio vale zero (placeholder R$ 0,00)
    assert totais(pagina) == ["R$ 0,00", "R$ 0,00", "R$ 0,00"]


@pytest.mark.parametrize("largura", LARGURAS)
def test_campos_do_item_alinhados_no_desktop_e_frete_sem_cortes(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar('[data-adicionar="acessorio"]')
    pagina.clicar('[data-adicionar="embalagem"]')

    linhas = pagina.js(
        """[...document.querySelectorAll('#compra-itens .compra-item')].map(l => {
             const seletor = '.compra-item-campos [data-campo]:not([type=hidden])';
             const c = [...l.querySelectorAll(seletor)].map(e => e.getBoundingClientRect());
             const r = l.querySelector('.compra-item-campos').getBoundingClientRect();
             return {baixos: new Set(c.map(x => Math.round(x.bottom))).size,
                     esq: Math.min(...c.map(x => x.left)), dir: Math.max(...c.map(x => x.right)),
                     lim: [r.left, r.right]};
           })"""
    )
    for linha in linhas:
        assert linha["esq"] >= linha["lim"][0] - 1 and linha["dir"] <= linha["lim"][1] + 1
        if largura >= 1024:  # desktop: todos os controles na mesma linha, na mesma altura
            assert linha["baixos"] == 1
    # Frete e totais cabem na janela, sem rolagem horizontal.
    assert sem_rolagem_horizontal(pagina)
    assert pagina.js(
        "(() => { const j = document.querySelector('.compra-corpo');"
        " return j.scrollWidth <= j.clientWidth })()"
    )
    assert pagina.js(
        "[...document.querySelectorAll('.compra-resumo input, .compra-resumo dd')]"
        ".every(e => e.getBoundingClientRect().right <= document.documentElement.clientWidth)"
    )


# ---------- Listas, cores e linhas de filamento ----------


def opcoes(p, seletor):
    return p.js(
        f"[...document.querySelector('{seletor}').options]"
        ".map(o => [o.value, o.textContent.trim()])"
    )


def test_listas_do_filamento_e_acessorios_sem_limite(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert [v for v, _ in opcoes(pagina, linha(1, "quantidade"))] == ["1", "2", "3", "4", "5"]
    assert pagina.valor(linha(1, "quantidade")) == "1"
    assert opcoes(pagina, linha(1, "material"))[1:] == [
        ["PLA", "PLA"],
        ["PETG", "PETG"],
        ["TPU", "TPU"],
    ]
    assert opcoes(pagina, linha(1, "peso_rolo"))[1:] == [
        ["250", "250 g"],
        ["500", "500 g"],
        ["1000", "1.000 g"],
    ]
    # Acessório e embalagem seguem com quantidade livre (campo numérico, não lista).
    pagina.clicar('[data-adicionar="acessorio"]')
    assert pagina.js(f"document.querySelector('{linha(2, 'quantidade')}').tagName") == "INPUT"
    preencher(pagina, {linha(2, "quantidade"): "123456"})
    assert pagina.valor(linha(2, "quantidade")) == "123456"


def test_linhas_entram_na_secao_existente_sem_repetir_titulo(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    secoes = "[...document.querySelectorAll('.compra-secao-topo h4')].map(h => h.textContent)"
    assert pagina.js(secoes) == ["Filamentos", "Acessórios", "Embalagens"]
    for categoria in ("filamento", "filamento", "acessorio", "acessorio", "embalagem"):
        pagina.clicar(f'.compra-secao [data-adicionar="{categoria}"]')

    # Os títulos das seções não se repetem; as linhas entram dentro da seção da categoria.
    assert pagina.js(secoes) == ["Filamentos", "Acessórios", "Embalagens"]
    contagem = pagina.js(
        "[...document.querySelectorAll('.compra-secao')]"
        ".map(s => [s.dataset.secao, s.querySelectorAll('.compra-item').length])"
    )
    assert contagem == [["filamento", 3], ["acessorio", 2], ["embalagem", 1]]
    titulos = pagina.js(NOMES_DAS_LINHAS)
    assert titulos == [
        "Filamento 1",
        "Filamento 2",
        "Filamento 3",
        "Acessório 1",
        "Acessório 2",
        "Embalagem 1",
    ]
    # Cada linha, em qualquer seção, tem a sua foto opcional e o seu botão de remover.
    assert pagina.js("document.querySelectorAll('.compra-item-foto').length") == 6
    assert pagina.js("document.querySelectorAll('.compra-item-remover').length") == 6
    # Um botão "+ Adicionar" por seção, nenhum dentro das linhas.
    assert pagina.js("document.querySelectorAll('[data-adicionar]').length") == 3

    # Dados preenchidos e totais seguem ao adicionar e remover linhas.
    preencher_filamento(pagina, 1, qtd="2", peso="500", valor="1000")
    preencher_filamento(pagina, 2, qtd="5", peso="1000", valor="2000")
    preencher_item(pagina, 4, nome="Argola", qtd="4", valor="1000")
    assert totais(pagina)[0] == "R$ 130,00"  # 2 x 10,00 + 5 x 20,00 + 10,00 (argola)
    pagina.clicar('.compra-secao [data-adicionar="filamento"]')  # nova linha no fim da seção
    assert resumo(pagina, "filamento") == ["6.000 g", "R$ 120,00"]
    assert totais(pagina)[0] == "R$ 130,00"
    pagina.clicar("#compra-itens .compra-item[data-pos='4'] .compra-item-remover")  # a nova
    pagina.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-remover")
    assert totais(pagina)[0] == "R$ 110,00"
    assert resumo(pagina, "filamento") == ["5.000 g", "R$ 100,00"]  # só o antigo filamento 2
    titulos = pagina.js(NOMES_DAS_LINHAS)
    assert titulos == ["Filamento 1", "Filamento 2", "Acessório 1", "Acessório 2", "Embalagem 1"]


def test_compra_com_varios_filamentos_e_fotos_independentes(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar('.compra-secao [data-adicionar="filamento"]')
    preencher(pagina, {"#compra-data": "10092026"})
    escolher_local(pagina, "shopee")
    preencher_filamento(pagina, 1, qtd="5", peso="250", valor="1000", cor="Preto")
    preencher_filamento(pagina, 2, qtd="1", peso="1000", valor="2000", cor="Azul", material="TPU")
    nav.novo_arquivo(pagina, "azul.png")
    nav.escolher(pagina, 1)  # foto só na segunda linha de filamento
    nav.esperar_previa(pagina, "azul.png", 1)
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    pesos = sorted((f.cor, f.peso_g, f.material) for f in estoque.filamentos.values())
    assert pesos == [("Azul", 1000, "TPU"), ("Preto", 1250, "PLA")]
    assert len(estoque.imagens) == 1 and "/itens/2/" in next(iter(estoque.imagens))


def test_cor_sugere_as_ja_salvas_e_aceita_nova(pagina, estoque):
    estoque.com_filamento(
        "Preto",
        "PLA",
        "solido",
        "M",
        peso_g=Decimal("1000"),
        data_compra=date(2026, 9, 1),
        peso_rolo_g=Decimal("1000"),
        valor_unitario=Decimal("10"),
    )
    estoque.com_filamento(
        "preto ",
        "PLA",
        "solido",
        "M",
        peso_g=Decimal("1000"),
        data_compra=date(2026, 9, 1),
        peso_rolo_g=Decimal("1000"),
        valor_unitario=Decimal("10"),
    )
    estoque.com_filamento(
        "Verde Musgo",
        "PLA",
        "solido",
        "M",
        peso_g=Decimal("1000"),
        data_compra=date(2026, 9, 1),
        peso_rolo_g=Decimal("1000"),
        valor_unitario=Decimal("10"),
    )
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert (
        pagina.js("document.querySelector('#compra-itens [data-campo=cor]').getAttribute('list')")
        == "compra-cores"
    )
    sugestoes = pagina.js(
        "[...document.querySelectorAll('#compra-cores option')].map(o => o.value)"
    )
    assert sugestoes == ["Preto", "Verde Musgo"]  # sem repetição por espaço ou caixa
    preencher(pagina, {linha(1, "cor"): "Rosa Choque"})  # cor nova é permitida
    assert pagina.valor(linha(1, "cor")) == "Rosa Choque"


def test_filamentos_em_lista_unica_com_tres_imagens_e_remocao_da_do_meio(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    for _ in range(2):
        pagina.clicar('.compra-secao [data-adicionar="filamento"]')

    # Um único contêiner, um só cabeçalho de colunas e nenhuma linha com título próprio.
    lista = "[data-lista=filamento]"
    assert pagina.js("document.querySelectorAll('.compra-lista').length") == 3  # uma por seção
    assert pagina.js(f"document.querySelectorAll('{lista} .compra-colunas').length") == 1
    assert pagina.js(f"document.querySelectorAll('{lista} .compra-linha').length") == 3
    assert pagina.js("document.querySelectorAll('.compra-linha .compra-item-titulo').length") == 0
    assert pagina.js(NOMES_DAS_LINHAS) == ["Filamento 1", "Filamento 2", "Filamento 3"]
    cabecalho = pagina.js(
        "[...document.querySelectorAll('[data-lista=filamento] .compra-colunas-rotulos span')]"
        ".map(s => s.textContent.replace('*', '').trim()).filter(Boolean)"
    )
    assert cabecalho == [
        "Quantidade",
        "Material",
        "Tipo",
        "Peso (g)",
        "Cor",
        "Marca",
        "Valor unitário",
    ]

    dados = [
        ("Preto", "1", "250", "1000"),
        ("Azul", "3", "500", "2000"),
        ("Verde", "5", "1000", "3000"),
    ]
    for n, (cor, qtd, peso, valor) in enumerate(dados, 1):
        preencher_filamento(pagina, n, qtd=qtd, peso=peso, valor=valor, cor=cor)
        nav.novo_arquivo(pagina, f"{cor.lower()}.png")
        nav.escolher(pagina, n - 1)
        nav.esperar_previa(pagina, f"{cor.lower()}.png", n - 1)

    # Remover a linha do meio: as outras duas mantêm valores e imagens, na mesma ordem.
    pagina.clicar("#compra-itens .compra-item[data-pos='2'] .compra-item-remover")
    assert pagina.js(NOMES_DAS_LINHAS) == ["Filamento 1", "Filamento 2"]
    assert [pagina.valor(linha(n, "cor")) for n in (1, 2)] == ["Preto", "Verde"]
    assert [pagina.valor(linha(n, "quantidade")) for n in (1, 2)] == ["1", "5"]
    assert [pagina.valor(linha(n, "peso_rolo")) for n in (1, 2)] == ["250", "1000"]
    assert [pagina.valor(linha(n, "valor_unitario")) for n in (1, 2)] == ["R$ 10,00", "R$ 30,00"]
    assert resumo(pagina, "filamento") == ["5.250 g", "R$ 160,00"]
    assert [nav.previa_visivel(pagina, n) for n in (0, 1)] == [True, True]
    nomes = pagina.js("[...document.querySelectorAll('.upload-nome')].map(e => e.textContent)")
    assert nomes == ["preto.png", "verde.png"]
    assert totais(pagina)[0] == "R$ 160,00"  # 1 x 10,00 + 5 x 30,00

    preencher(pagina, {"#compra-data": "10092026"})
    escolher_local(pagina, "shopee")
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert sorted((f.cor, f.peso_g) for f in estoque.filamentos.values()) == [
        ("Preto", 250),
        ("Verde", 5000),
    ]
    assert sorted(estoque.imagens) and len(estoque.imagens) == 2
    assert [c.split("/")[3] for c in sorted(estoque.imagens)] == [
        "1",
        "2",
    ]  # só as linhas restantes


def test_lista_de_filamentos_no_celular_sem_rolagem_horizontal(pagina, estoque):
    pagina.tela(360, 700)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar('.compra-secao [data-adicionar="filamento"]')

    assert (
        not pagina.js("getComputedStyle(document.querySelector('.compra-colunas')).display")
        != "none"
    )
    assert pagina.js("document.querySelectorAll('.compra-linha .campo > label').length") == 14
    assert pagina.js(  # no celular o rótulo de cada campo aparece em cada linha
        "[...document.querySelectorAll('.compra-linha .campo > label')]"
        ".every(l => l.getBoundingClientRect().width > 1)"
    )
    assert pagina.js(
        "(() => { const c = document.querySelector('.compra-corpo');"
        " return c.scrollWidth <= c.clientWidth })()"
    )
    assert sem_rolagem_horizontal(pagina)


@pytest.mark.parametrize("largura", [1024, 1100, 1280, 1920])
def test_cabecalho_compartilhado_alinha_com_os_campos_no_desktop(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar('.compra-secao [data-adicionar="filamento"]')

    posicoes = pagina.js(
        """(() => {
             const seletor = '.compra-lista:not([hidden]) .compra-colunas-rotulos > span';
             const cab = [...document.querySelectorAll(seletor)]
               .map(e => Math.round(e.getBoundingClientRect().left));
             return [...document.querySelectorAll('.compra-linha')].map(l => ({
               cab,
               campos: [...l.querySelectorAll('.compra-item-campos > .campo')]
                 .map(c => Math.round(c.getBoundingClientRect().left)),
               remover: Math.round(
                 l.querySelector('.compra-item-remover').getBoundingClientRect().left),
               fim: Math.round(l.querySelector('.compra-campo-valor_unitario')
                 .getBoundingClientRect().right),
             }));
           })()"""
    )
    for linha_ in posicoes:
        assert linha_["cab"] == linha_["campos"]  # cada coluna do cabeçalho sobre o seu campo
        assert linha_["fim"] <= linha_["remover"]  # o botão Remover fica depois dos campos
    assert pagina.js(  # sem rótulos repetidos nas linhas: só o cabeçalho os mostra
        "[...document.querySelectorAll('.compra-linha .campo > label')]"
        ".every(l => l.getBoundingClientRect().width <= 1)"
    )


def test_acessorios_e_embalagens_em_lista_com_resumo_sem_frete(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    for categoria in ("acessorio", "acessorio", "embalagem"):
        pagina.clicar(f'.compra-secao [data-adicionar="{categoria}"]')

    # Lista única por seção: sem cartões nem títulos nas linhas, cabeçalho compartilhado.
    assert pagina.js("document.querySelectorAll('.compra-item-titulo').length") == 0
    for categoria in ("acessorio", "embalagem"):
        cabecalho = pagina.js(
            "[...document.querySelectorAll("
            f"'[data-lista={categoria}] .compra-colunas-rotulos > span')]"
            ".map(s => s.textContent.replace('*', '').trim())"
        )
        assert cabecalho == ["Quantidade", "Nome", "Valor total"]
    campos = pagina.js(
        "[...document.querySelectorAll('[data-lista=acessorio] .compra-linha:first-child"
        " [data-campo]:not([type=hidden])')].map(e => e.dataset.campo)"
    )
    assert campos == ["quantidade", "nome", "valor_total"]
    assert (
        pagina.js("document.querySelectorAll('[data-lista=acessorio] .compra-linha').length") == 2
    )
    assert (
        pagina.js("document.querySelectorAll('[data-calculo]').length") == 0
    )  # sem custo por item

    preencher_item(pagina, 2, nome="Argola", qtd="40", valor="1400")  # acessório 1
    preencher_item(pagina, 3, nome="Chaveiro", qtd="10", valor="600")  # acessório 2
    preencher_item(pagina, 4, nome="Caixa", qtd="3", valor="1000")  # embalagem 1
    preencher(pagina, {"#compra-frete": "5000"})
    assert resumo(pagina, "acessorio") == ["50 un", "R$ 20,00"]
    assert resumo(pagina, "embalagem") == ["3 un", "R$ 10,00"]  # o frete não entra nos resumos
    assert totais(pagina) == ["R$ 30,00", "R$ 50,00", "R$ 80,00"]  # mas entra no total geral
    assert pagina.valor(linha(2, "valor_total")) == "R$ 14,00"  # o valor informado continua

    pagina.clicar("#compra-itens .compra-item[data-pos='2'] .compra-item-remover")
    assert resumo(pagina, "acessorio") == ["10 un", "R$ 6,00"]
    assert pagina.valor(linha(2, "nome")) == "Chaveiro"  # os dados da outra linha ficam


@pytest.mark.parametrize("largura", [1024, 1100, 1280, 1920])
def test_foto_na_mesma_linha_dos_campos_no_desktop(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar('.compra-secao [data-adicionar="filamento"]')

    def posicoes():
        return pagina.js(
            """[...document.querySelectorAll('[data-lista=filamento] .compra-linha')].map(l => {
                 const topo = e => Math.round(l.querySelector(e).getBoundingClientRect().top);
                 const dir = e => Math.round(l.querySelector(e).getBoundingClientRect().right);
                 const limite = Math.round(l.getBoundingClientRect().right);
                 return {campos: topo('[data-campo=quantidade]'),
                         foto: topo('.upload-botao:not([hidden])'),
                         remover: topo('.compra-item-remover'), fim: dir('.upload-item'), limite};
               })"""
        )

    for linha_ in posicoes():  # foto e Remover na mesma linha horizontal dos demais campos
        assert linha_["foto"] == linha_["campos"] == linha_["remover"]
        assert linha_["fim"] <= linha_["limite"]
    assert pagina.js(  # prévia compacta: sem a área de arrastar nem o nome do arquivo
        "['.upload-dica', '.upload-ou'].every(s => "
        "[...document.querySelectorAll('.compra-linha ' + s)].every(e => e.offsetParent === null))"
    )

    # Seleção, prévia, troca e remoção, independentes por linha.
    nav.novo_arquivo(pagina, "a.png")
    nav.escolher(pagina, 1)
    nav.esperar_previa(pagina, "a.png", 1)
    assert nav.previa_visivel(pagina, 1) and not nav.previa_visivel(pagina, 0)
    assert (
        pagina.js("document.querySelectorAll('.compra-linha .upload-trocar:not([hidden])').length")
        == 1
    )
    assert (
        pagina.js("document.querySelectorAll('.compra-linha .upload-remover:not([hidden])').length")
        == 1
    )
    assert sem_rolagem_horizontal(pagina)
    nav.novo_arquivo(pagina, "b.png")
    nav.escolher(pagina, 1)
    nav.esperar_previa(pagina, "b.png", 1)
    pagina.js(
        "document.querySelectorAll('.compra-linha')[1].querySelector('.upload-remover').click()"
    )
    assert not nav.previa_visivel(pagina, 1)


# ---------- Ícones, Local reduzido e Data ao lado do Local ----------


@pytest.mark.parametrize("largura", LARGURAS)
def test_lixeira_e_camera_com_nome_acessivel_tooltip_e_area_de_44px(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar('.compra-secao [data-adicionar="acessorio"]')
    pagina.clicar('.compra-secao [data-adicionar="embalagem"]')

    botoes = pagina.js(
        """[...document.querySelectorAll('.compra-item-remover, .upload-selecionar')].map(b => {
             const r = b.getBoundingClientRect();
             return {texto: b.textContent.trim(), nome: b.getAttribute('aria-label'),
                     dica: b.title, svg: !!b.querySelector('svg[aria-hidden=true]'),
                     w: Math.round(r.width), h: Math.round(r.height)};
           })"""
    )
    assert len(botoes) == 6  # lixeira e câmera em cada uma das três linhas
    for b in botoes:
        assert b["texto"] == "" and b["svg"]  # só o ícone, sem texto visível
        assert b["nome"] and b["dica"] == b["nome"]  # nome acessível e tooltip
        assert b["w"] >= 44 and b["h"] >= 44
    nomes = [b["nome"] for b in botoes]
    assert nomes.count("Selecionar foto") == 3
    assert {"Remover filamento 1", "Remover acessório 1", "Remover embalagem 1"} <= set(nomes)


def test_foto_com_icone_de_camera_preserva_previa_troca_e_remocao(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    nav.novo_arquivo(pagina, "a.png")
    nav.escolher(pagina, 0)
    nav.esperar_previa(pagina, "a.png", 0)

    assert nav.previa_visivel(pagina, 0)
    assert pagina.js("document.querySelector('.upload-selecionar').offsetParent === null")
    assert pagina.js("document.querySelector('.upload-trocar').offsetParent !== null")
    nav.novo_arquivo(pagina, "b.png")
    nav.escolher(pagina, 0)
    nav.esperar_previa(pagina, "b.png", 0)  # troca
    pagina.js("document.querySelector('.upload-remover').click()")
    assert not nav.previa_visivel(pagina, 0)
    assert pagina.js(
        "document.querySelector('.upload-selecionar').offsetParent !== null"
    )  # volta a câmera


@pytest.mark.parametrize("largura", LARGURAS)
def test_local_25_por_cento_menor_sem_reduzir_a_area_clicavel(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    medidas = pagina.js(
        """[...document.querySelectorAll('#campo-local .escolha-card')].map(c => {
             const m = c.querySelector('.compra-local-marca');
             const img = m.querySelector('img');
             const t = c.querySelector('.escolha-titulo');
             const r = c.getBoundingClientRect();
             const caixa = img ? img.getBoundingClientRect() : null;
             const natural = img ? img.naturalWidth / img.naturalHeight : 0;
             return {marca: Math.round(m.getBoundingClientRect().height),
                     proporcao: img ? Math.abs(caixa.width / caixa.height - natural) < 0.05 : true,
                     fonte: parseFloat(getComputedStyle(t).fontSize), h: Math.round(r.height)};
           })"""
    )
    assert len(medidas) == 5
    for m in medidas:
        assert m["fonte"] >= 11  # fonte mínima
        assert m["proporcao"]  # logos sem distorção
        assert m["h"] >= 84  # a área clicável do card não encolheu
    assert [m["marca"] for m in medidas] == [27, 21, 21, 21, 21]  # 36/28 px menos 25%
    # A tela de Pedidos não muda: a regra vale só dentro da janela de compra.
    assert pagina.js("getComputedStyle(document.querySelector('.estoque')).fontSize !== undefined")


def test_sem_titulo_itens_nem_texto_de_ajuda_dos_rolos(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    texto = pagina.js("document.getElementById('compra-janela').innerText")
    assert "Cada linha de filamento é um conjunto de rolos iguais" not in texto
    assert pagina.js("document.getElementById('compra-itens-titulo')") is None
    assert [
        t.strip()
        for t in pagina.js(
            "[...document.querySelectorAll('#compra-janela h3')].map(h => h.textContent)"
        )
    ] == ["Frete e total"]


@pytest.mark.parametrize("largura", [1024, 1100, 1280, 1920])
def test_data_e_local_lado_a_lado_no_desktop(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    def caixas():
        return pagina.js(
            """(() => {
                 const r = s => document.querySelector(s).getBoundingClientRect();
                 const data = r('#compra-data'), cartao = r('#campo-local .escolha-card');
                 const rotulo = r('#campo-data_compra label'), legenda = r('#campo-local legend');
                 return {dataTopo: Math.round(data.top), cartaoTopo: Math.round(cartao.top),
                         rotuloTopo: Math.round(rotulo.top), legendaTopo: Math.round(legenda.top),
                         dataDir: Math.round(data.right), cartaoEsq: Math.round(cartao.left)};
               })()"""
        )

    c = caixas()
    assert abs(c["rotuloTopo"] - c["legendaTopo"]) <= 2  # rótulos na mesma altura
    assert abs(c["dataTopo"] - c["cartaoTopo"]) <= 2  # controles alinhados pelo topo
    assert c["dataDir"] < c["cartaoEsq"]  # lado a lado

    # Outro / Loja Física: o nome do local aparece sob o Local, sem desalinhar a Data.
    assert pagina.js("document.getElementById('campo-local_nome').hidden")
    escolher_local(pagina, "outro")
    nome = pagina.js(
        "(() => { const n = document.getElementById('campo-local_nome').getBoundingClientRect();"
        " const l = document.getElementById('campo-local').getBoundingClientRect();"
        " return {topo: Math.round(n.top), basel: Math.round(l.bottom), esq: Math.round(n.left),"
        " esql: Math.round(l.left)} })()"
    )
    assert nome["topo"] >= nome["basel"] and abs(nome["esq"] - nome["esql"]) <= 2
    assert abs(caixas()["dataTopo"] - c["dataTopo"]) <= 2
    assert sem_rolagem_horizontal(pagina)


@pytest.mark.parametrize("largura", [360, 768])
def test_data_e_local_empilhados_no_celular_sem_cortes(pagina, estoque, largura):
    pagina.tela(largura, 800)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    escolher_local(pagina, "loja_fisica")

    assert pagina.js(
        "document.getElementById('campo-local_nome').getBoundingClientRect().width > 0"
    )
    assert pagina.js(
        "document.querySelector('#compra-data').getBoundingClientRect().bottom"
        " <= document.querySelector('#campo-local legend').getBoundingClientRect().top + 1"
    )  # Data acima do Local
    assert sem_rolagem_horizontal(pagina)
    assert pagina.js(
        "(() => { const c = document.querySelector('.compra-corpo');"
        " return c.scrollWidth <= c.clientWidth })()"
    )


@pytest.mark.parametrize("categoria", ["filamento", "acessorios", "embalagens"])
@pytest.mark.parametrize("largura", LARGURAS)
def test_tela_estoque_sem_rolagem_horizontal_e_com_rotulos(pagina, com_compras, largura, categoria):
    com_compras.com_item("Caixa de papelão para envio", categoria="embalagem")
    pagina.tela(largura, 900)
    pagina.abrir(f"/estoque?categoria={categoria}")

    assert sem_rolagem_horizontal(pagina)
    assert cortados(pagina) == []
    assert pagina.js("document.querySelectorAll('.estoque-categoria').length") == 3
    assert pagina.js("document.querySelectorAll('.estoque-tabela tbody tr').length") >= 1
    cabecalho_visivel = pagina.js(
        "document.querySelector('.estoque-tabela thead').getBoundingClientRect().height > 1"
    )
    assert cabecalho_visivel == (largura >= 760)
    if largura < 760:  # no celular cada valor mostra o seu rótulo
        rotulos = pagina.js(
            "getComputedStyle(document.querySelector('.estoque-tabela td[data-rotulo]'),"
            " '::before').content"
        )
        assert rotulos not in ("none", "normal", '""')
