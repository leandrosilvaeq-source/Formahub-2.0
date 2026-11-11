"""Tela Compras em navegador real (Edge ou Chrome, sem interface), nas larguras do projeto.

Inclui a janela Registrar Compra. Reaproveita o servidor e o navegador de test_navegador.py;
só dados fictícios em memória.
"""

import time
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


# PNG 1x1 em data URI: a foto carrega de verdade (sem rede) e o botão da prévia não é removido.
PNG_1X1 = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "iZk9HQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def estoque():
    repo = RepositorioEstoqueMemoria()
    repo.assinar_imagens = lambda caminhos: {c: PNG_1X1 for c in caminhos if c in repo.imagens}
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


def abrir_janela(p):
    # Há um único botão Registrar Compra; a janela abre com uma linha de filamento.
    p.clicar("[data-abrir-compra]")
    nav.esperar(lambda: p.js("document.getElementById('compra-janela').open"))


def linha(n: int, campo: str) -> str:
    return f'#compra-itens .compra-item[data-pos="{n}"] [data-campo="{campo}"]'


def mudar_categoria(p, n: int, categoria: str):
    escolher_opcao(p, linha(n, "categoria"), categoria)


def adicionar(p, categoria=None) -> int:
    """'+ Adicionar item' (a linha nova repete a categoria da última); devolve a posição."""
    p.clicar("[data-adicionar]")
    n = p.js("document.querySelectorAll('#compra-itens .compra-item').length")
    if categoria:
        mudar_categoria(p, n, categoria)
    return n


# Nome acessível de cada linha, na ordem da tela.
NOMES_DAS_LINHAS = (
    "[...document.querySelectorAll('#compra-itens .compra-item')]"
    ".map(l => l.getAttribute('aria-label'))"
)


def rotulos(p, n: int) -> list[str]:
    """Rótulos dos campos visíveis da linha n, na ordem da tela."""
    return p.js(
        f'[...document.querySelectorAll(\'#compra-itens .compra-item[data-pos="{n}"]'
        " .compra-item-campos > .campo:not([hidden]) > label')]"
        ".map(l => l.firstChild.textContent.trim())"
    )


# Data local de hoje (dd/mm/aaaa), como a janela a preenche ao abrir.
HOJE_JS_EXPR = (
    "(() => { const d = new Date(); return String(d.getDate()).padStart(2, '0') + '/'"
    " + String(d.getMonth() + 1).padStart(2, '0') + '/' + d.getFullYear() })()"
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
            linha(n, "quantidade"): qtd,
            linha(n, "marca"): "Marca Teste",
            linha(n, "valor_unitario"): valor,
        },
    )
    escolher_opcao(p, linha(n, "material"), material)
    escolher_opcao(p, linha(n, "peso_rolo"), peso)
    escolher_opcao(p, linha(n, "tipo"), "silk")


def preencher_item(p, n, nome="Argola", qtd="40", valor="35", **extras):
    """Linha sem filamento: nome, quantidade, valor unitário (centavos digitados) e extras."""
    campos = {
        linha(n, "nome"): nome,
        linha(n, "quantidade"): qtd,
        linha(n, "valor_unitario"): valor,
    }
    campos.update({linha(n, campo): texto for campo, texto in extras.items()})
    preencher(p, campos)


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
    """Textos que saem do próprio card de resumo (cortes visuais)."""
    return p.js(
        """[...document.querySelectorAll('.compras-card')].flatMap(caixa => {
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
def test_estoque_usa_a_largura_das_telas_de_tabela_sem_rolagem(pagina, estoque, largura):
    estoque.com_filamento(cor="Azul", marca="Marca A")
    estoque.com_filamento(cor="Azul", marca="Marca B")
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    assert sem_rolagem_horizontal(pagina)
    assert pagina.js("document.querySelectorAll('.estoque-tabela tbody tr').length") == 1
    assert pagina.js("getComputedStyle(document.querySelector('main')).maxWidth") == "1600px"


@pytest.mark.parametrize("largura", LARGURAS)
def test_compras_sem_rolagem_horizontal_nem_cortes(pagina, com_compras, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")

    assert sem_rolagem_horizontal(pagina)
    assert cortados(pagina) == []
    assert pagina.js("document.querySelectorAll('.compra-registro').length") == 2
    assert pagina.js("document.querySelectorAll('[data-abrir-compra]').length") == 1
    # O filtro e os cards vêm depois do botão; a tabela rola por dentro, nunca a página.
    assert pagina.js(
        "document.querySelector('[data-abrir-compra]').getBoundingClientRect().bottom"
        " <= document.querySelector('.compras-filtro').getBoundingClientRect().top"
    )
    assert (
        pagina.js(
            "document.querySelector('.tabela-compras .pedido-tabela-foto')"
            ".getBoundingClientRect().width"
        )
        == 48
    )


@pytest.mark.parametrize("largura", LARGURAS)
def test_cards_lado_a_lado_no_desktop_e_empilhados_no_celular(pagina, com_compras, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")

    topos = pagina.js(
        "[...document.querySelectorAll('.compras-card')]"
        ".map(c => Math.round(c.getBoundingClientRect().top))"
    )
    assert len(topos) == 6
    assert (topos[0] == topos[1]) == (largura >= 640)
    # Desktop: duas linhas de três cards.
    assert (topos[:3] == [topos[0]] * 3 and topos[3:] == [topos[3]] * 3) == (largura >= 1000)


def test_filtro_de_periodo_troca_cards_e_tabela(pagina, com_compras):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    assert pagina.js("document.querySelectorAll('.compra-registro').length") == 2

    pagina.js(
        "(() => { const s = document.getElementById('compras-periodo');"
        " s.value = '2026-09'; s.dispatchEvent(new Event('change', {bubbles: true})) })()"
    )
    nav.esperar(lambda: "periodo=2026-09" in pagina.js("location.search"))
    nav.esperar(lambda: pagina.js("document.readyState") == "complete")

    assert pagina.js("document.getElementById('compras-periodo').value") == "2026-09"
    assert "Compras de Setembro/2026" in pagina.texto("#titulo-ultimas-compras")
    assert sem_rolagem_horizontal(pagina)


def esperar_foto(p):
    nav.esperar(
        lambda: p.js("document.querySelector('.tabela-compras .pedido-tabela-foto').complete")
    )


def previa_aberta(p):
    return p.js("!!document.querySelector('.foto-previa')")


def dentro_da_tela(p) -> bool:
    return p.js(
        "(() => { const r = document.querySelector('.foto-previa').getBoundingClientRect();"
        " return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight })()"
    )


ESCALA = (
    "new DOMMatrix(getComputedStyle(document.querySelector('.foto-previa-quadro img')).transform).a"
)
ORIGEM = "getComputedStyle(document.querySelector('.foto-previa-quadro img')).transformOrigin"


def test_previa_abre_apos_2s_no_mouse_e_a_lupa_segue_o_cursor(pagina, com_compras):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    esperar_foto(pagina)
    x, y = pagina.js(
        "(() => { const r = document.querySelector('.foto-previa-botao').getBoundingClientRect();"
        " return [r.left + r.width / 2, r.top + r.height / 2] })()"
    )

    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    time.sleep(0.8)
    assert not previa_aberta(pagina)  # antes de 2 s nada abre
    nav.esperar(lambda: previa_aberta(pagina), tempo=4, mensagem="a prévia não abriu")
    assert dentro_da_tela(pagina)
    assert pagina.js(ESCALA) == 1

    esq, topo = pagina.js(
        "(() => { const r = document.querySelector('.foto-previa-quadro').getBoundingClientRect();"
        " return [r.left, r.top] })()"
    )
    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=esq + 40, y=topo + 40)
    nav.esperar(lambda: pagina.js(ESCALA) == 2.5, mensagem="a lupa não ampliou")
    assert pagina.js(ORIGEM).startswith("40px 40px")  # o foco acompanha o cursor

    pagina.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
    nav.esperar(lambda: not previa_aberta(pagina))


@pytest.mark.parametrize("largura", [360, 768])
def test_previa_por_clique_ou_toque_tem_lupa_fechar_e_cabe_na_tela(pagina, com_compras, largura):
    pagina.tela(largura, 700)
    pagina.abrir("/compras")
    esperar_foto(pagina)

    pagina.clicar(".foto-previa-botao")
    assert previa_aberta(pagina) and dentro_da_tela(pagina)
    assert pagina.js("document.activeElement.className").startswith("foto-previa-fechar")
    assert pagina.js("document.querySelector('.foto-previa-lupa').offsetWidth") >= 44

    # A lupa fixa a ampliação (toque e teclado); as setas movem a área ampliada.
    pagina.clicar(".foto-previa-lupa")
    assert pagina.js("document.querySelector('.foto-previa-lupa').ariaPressed") == "true"
    nav.esperar(lambda: pagina.js(ESCALA) == 2.5)
    antes = pagina.js(ORIGEM)
    tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js(ORIGEM) != antes
    pagina.clicar(".foto-previa-lupa")
    nav.esperar(lambda: pagina.js(ESCALA) == 1)

    pagina.clicar(".foto-previa-fechar")
    assert not previa_aberta(pagina)
    assert pagina.js("document.activeElement.className") == "foto-previa-botao"

    pagina.clicar(".foto-previa-botao")
    tecla(pagina, "Escape", codigo=27)
    nav.esperar(lambda: not previa_aberta(pagina))


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
    assert "0 compras" in pagina.texto("#card-compras")


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


def totais(p):
    return [p.texto(f"#{i}") for i in ("compra-subtotal", "compra-total")]


CATEGORIAS = ("filamento", "acessorio", "embalagem", "peca_manutencao", "outro")


def janela_com_as_cinco_categorias(p):
    abrir_janela(p)
    for categoria in CATEGORIAS[1:]:
        adicionar(p, categoria)


@pytest.mark.parametrize("largura", LARGURAS)
def test_janela_cabe_na_tela_com_rolagem_interna(pagina, estoque, largura):
    pagina.tela(largura, 700)
    pagina.abrir("/compras")
    janela_com_as_cinco_categorias(pagina)
    for _ in range(2):
        pagina.clicar('#compra-itens .compra-item[data-pos="1"] .compra-item-duplicar')

    medidas = pagina.js(
        """(() => {
             const j = document.getElementById('compra-janela').getBoundingClientRect();
             const c = document.querySelector('.compra-corpo');
             const r = document.querySelector('.compra-rodape').getBoundingClientRect();
             const s = document.querySelector('#form-compra button[type=submit]')
               .getBoundingClientRect();
             return {topo: j.top, base: j.bottom, esq: j.left, dir: j.right,
                     rodape: r.bottom, salvar: s.bottom, rola: c.scrollHeight > c.clientHeight,
                     overflow: getComputedStyle(c).overflowY,
                     largo: c.scrollWidth <= c.clientWidth};
           })()"""
    )
    assert medidas["topo"] >= 0 and medidas["base"] <= 700
    assert medidas["esq"] >= 0 and medidas["dir"] <= largura
    # Resumo e Salvar/Cancelar ficam no rodapé fixo; só o corpo (com os itens) rola.
    assert medidas["rodape"] <= 700 and medidas["salvar"] <= 700
    assert medidas["overflow"] == "auto" and medidas["rola"]
    assert medidas["largo"]
    pagina.js("document.querySelector('.compra-corpo').scrollTop = 99999")
    assert pagina.js(
        "document.getElementById('compra-total').getBoundingClientRect().bottom <= 700"
    )
    assert sem_rolagem_horizontal(pagina)
    if largura < 640:  # no celular a janela ocupa a tela inteira
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
             return [...l.querySelectorAll('input, select, .botao, output, label')]
               .filter(el => el.getClientRects().length && el.type !== 'hidden'
                       && (el.getBoundingClientRect().right > lim.right + 1
                           || el.getBoundingClientRect().left < lim.left - 1
                           || el.scrollWidth > el.clientWidth + 1 && el.tagName === 'LABEL'))
               .map(el => el.dataset.campo || el.textContent.trim());
           })"""
    )
    assert cortes == []


@pytest.mark.parametrize("largura", [1280, 1920])
def test_desktop_cada_linha_numa_faixa_com_icones(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    janela_com_as_cinco_categorias(pagina)

    # Em cada linha, campos, foto e ícones começam na mesma altura (uma só faixa).
    faixas = pagina.js(
        """[...document.querySelectorAll('#compra-itens .compra-item')].map(l =>
             new Set([...l.querySelectorAll(
                 '.compra-item-campos > .campo:not([hidden]) input,'
                 + ' .compra-item-campos > .campo:not([hidden]) select,'
                 + ' .upload-selecionar, .compra-item-duplicar, .compra-item-remover')]
               .map(el => Math.round(el.getBoundingClientRect().top))).size)"""
    )
    assert faixas == [1, 1, 1, 1, 1]
    # Rótulos compactos e visíveis acima de cada campo.
    assert pagina.js(
        "[...document.querySelectorAll('.compra-item-campos > .campo:not([hidden]) > label')]"
        ".every(l => l.getClientRects().length && parseFloat(getComputedStyle(l).fontSize) <= 13)"
    )
    # Foto, duplicar e remover são ícones (sem texto), com nome acessível e dica.
    icones = pagina.js(
        """[...document.querySelectorAll('.compra-item[data-pos="1"] .upload-selecionar,'
             + ' .compra-item[data-pos="1"] .compra-item-duplicar,'
             + ' .compra-item[data-pos="1"] .compra-item-remover')]
           .map(b => [b.textContent.trim(), b.getAttribute('aria-label'), b.title,
                      !!b.querySelector('svg')])"""
    )
    assert icones == [
        ["", "Selecionar foto (PNG, JPG ou WebP, até 10 MB)", "Selecionar foto", True],
        ["", "Duplicar item 1", "Duplicar item 1", True],
        ["", "Remover item 1", "Remover item 1", True],
    ]


def test_cada_categoria_mostra_so_os_campos_dela_na_ordem(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    janela_com_as_cinco_categorias(pagina)

    assert [rotulos(pagina, n) for n in range(1, 6)] == [
        ["Categoria", "Material", "Cor", "Tipo", "Peso/un (g)", "Qtd.", "Marca", "Valor unit."],
        ["Categoria", "Item", "Qtd.", "Cor", "Medidas", "Valor unit."],
        ["Categoria", "Item", "Qtd.", "Cor", "Medidas", "Valor unit."],
        ["Categoria", "Item", "Qtd.", "Marca", "Valor unit."],
        ["Categoria", "Item", "Qtd.", "Marca", "Valor unit."],
    ]
    opcoes = pagina.js(
        f"[...document.querySelector('{linha(1, 'categoria')}').options].map(o => o.text)"
    )
    assert opcoes == [
        "Filamento",
        "Acessórios para produtos",
        "Embalagens para produtos",
        "Peças de reposição",
        "Itens gerais",
    ]
    # Asterisco só nos obrigatórios da categoria: cor e marca são opcionais fora do filamento.
    obrigatorios = (
        '[...document.querySelectorAll(\'#compra-itens .compra-item[data-pos="{n}"]'
        " .campo:not([hidden]) > label')].filter(l => !l.querySelector('.obrigatorio').hidden)"
        ".map(l => l.firstChild.textContent.trim())"
    )
    assert pagina.js(obrigatorios.format(n=1))[-2:] == ["Marca", "Valor unit."]
    assert pagina.js(obrigatorios.format(n=2)) == ["Categoria", "Item", "Qtd.", "Valor unit."]
    assert pagina.js(obrigatorios.format(n=4)) == ["Categoria", "Item", "Qtd.", "Valor unit."]
    # Sugestões de nomes da categoria da linha; cor e marca com as listas gerais.
    assert pagina.js(f"document.querySelector('{linha(4, 'nome')}').getAttribute('list')") == (
        "compra-nomes-peca_manutencao"
    )
    assert pagina.js(f"document.querySelector('{linha(2, 'cor')}').getAttribute('list')") == (
        "compra-cores"
    )


def test_trocar_categoria_preenchida_avisa_guarda_e_desfaz(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    preencher_filamento(pagina, 1, qtd="2", valor="5000")

    mudar_categoria(pagina, 1, "outro")

    # Nada é apagado calado: os campos que somem são listados e a troca pode ser desfeita.
    aviso = "#compra-itens .compra-item[data-pos='1'] .compra-item-aviso"
    assert pagina.js(f'!document.querySelector("{aviso}").hidden')
    assert pagina.texto(f"{aviso} .compra-item-aviso-texto") == (
        "Não usados em Itens gerais e não serão salvos: Material, Cor, Tipo, Peso/un (g)."
    )
    # Quantidade, marca e valor unitário servem às duas categorias e continuam visíveis.
    assert rotulos(pagina, 1) == ["Categoria", "Item", "Qtd.", "Marca", "Valor unit."]
    assert [pagina.valor(linha(1, c)) for c in ("quantidade", "marca", "valor_unitario")] == [
        "2",
        "Marca Teste",
        "R$ 50,00",
    ]
    assert pagina.texto("#compra-subtotal") == "R$ 100,00"

    pagina.clicar(f"{aviso} .compra-item-desfazer")
    assert pagina.valor(linha(1, "categoria")) == "filamento"
    assert pagina.js(f'document.querySelector("{aviso}").hidden')
    assert [pagina.valor(linha(1, c)) for c in ("material", "cor", "tipo", "peso_rolo")] == [
        "PLA",
        "Preto",
        "silk",
        "1000",
    ]

    # Trocar de novo e salvar: só os campos visíveis da categoria são enviados.
    mudar_categoria(pagina, 1, "peca_manutencao")
    preencher(pagina, {linha(1, "nome"): "Bico"})
    escolher_local(pagina, "shopee")
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    (gravada,) = estoque.compras.values()
    (peca,) = gravada.compra.itens
    assert (peca.categoria, peca.nome, peca.marca, peca.cor, peca.material) == (
        "peca_manutencao",
        "Bico",
        "Marca Teste",
        None,
        None,
    )
    assert not estoque.itens and not estoque.filamentos


def test_adicionar_duplicar_e_remover_linhas(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    assert pagina.js(NOMES_DAS_LINHAS) == ["Item 1"]

    n = adicionar(pagina, "acessorio")
    preencher_item(pagina, n, nome="Argola", qtd="10", valor="150", cor="Dourada", medidas="2 cm")
    nav.novo_arquivo(pagina, "argola.png")
    nav.escolher(pagina, 1)
    nav.esperar_previa(pagina, "argola.png", 1)

    # Duplicar: a cópia entra logo abaixo, com os mesmos dados e sem a foto (cada linha tem a
    # sua), sem abrir outra janela.
    pagina.clicar("#compra-itens .compra-item[data-pos='2'] .compra-item-duplicar")
    assert pagina.js(NOMES_DAS_LINHAS) == ["Item 1", "Item 2", "Item 3"]
    assert pagina.js("document.activeElement.dataset.campo") == "categoria"
    assert [pagina.valor(linha(3, c)) for c in ("categoria", "nome", "cor", "medidas")] == [
        "acessorio",
        "Argola",
        "Dourada",
        "2 cm",
    ]
    assert nav.arquivos_por_item(pagina) == [None, "argola.png", None]
    assert pagina.texto("#compra-subtotal") == "R$ 30,00"

    # Duplicar a primeira insere entre ela e a segunda.
    pagina.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-duplicar")
    assert pagina.js(
        "[...document.querySelectorAll('#compra-itens .compra-item')].map(l => l.dataset.categoria)"
    ) == ["filamento", "filamento", "acessorio", "acessorio"]

    # Remover: o foco vai para a vizinha; sem linhas, para "+ Adicionar item".
    pagina.clicar("#compra-itens .compra-item[data-pos='3'] .compra-item-remover")
    assert pagina.js("document.activeElement.getAttribute('aria-label')") == "Remover item 3"
    assert nav.arquivos_por_item(pagina) == [None, None, None]
    for _ in range(3):
        pagina.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-remover")
    assert pagina.js("document.activeElement.hasAttribute('data-adicionar')")
    assert not pagina.js("document.querySelector('.compra-itens-vazio').hidden")
    assert pagina.texto("#compra-total") == "R$ 0,00"


def test_totais_atualizam_na_hora_com_desconto_e_frete(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert pagina.valor("#compra-desconto") == "" and pagina.valor("#compra-frete") == ""
    assert totais(pagina) == ["R$ 0,00", "R$ 0,00"]
    preencher_filamento(pagina, 1, qtd="3", valor="8990")  # 3 x 89,90 = 269,70
    assert pagina.texto("#compra-itens .compra-item[data-pos='1'] [data-total-linha]") == (
        "R$ 269,70"
    )
    n = adicionar(pagina, "embalagem")
    preencher_item(pagina, n, nome="Caixa", qtd="4", valor="225")  # 4 x 2,25 = 9,00
    assert totais(pagina) == ["R$ 278,70", "R$ 278,70"]

    preencher(pagina, {"#compra-desconto": "870", "#compra-frete": "1250"})
    assert pagina.valor("#compra-desconto") == "R$ 8,70"
    assert totais(pagina) == ["R$ 278,70", "R$ 282,50"]  # 278,70 − 8,70 + 12,50

    pagina.clicar("#compra-itens .compra-item[data-pos='1'] .compra-item-remover")
    assert totais(pagina) == ["R$ 9,00", "R$ 12,80"]  # 9,00 − 8,70 + 12,50
    assert pagina.js("document.getElementById('compra-desconto-aviso').hidden")
    pagina.js(
        "(() => { const f = document.getElementById('compra-desconto'); f.value = '';"
        " f.dispatchEvent(new Event('input', {bubbles: true})) })()"
    )
    preencher(pagina, {"#compra-desconto": "1000"})  # 10,00 > subtotal 9,00
    assert not pagina.js("document.getElementById('compra-desconto-aviso').hidden")
    preencher(pagina, {"#compra-frete": "-"})  # sem sinal: nunca fica negativo
    assert pagina.valor("#compra-frete").startswith("R$ ")


@pytest.mark.parametrize("largura", [360, 1280])
def test_compra_mista_com_as_cinco_categorias_fotos_e_atributos(pagina, estoque, largura):
    pagina.tela(largura, 800)
    pagina.abrir("/compras")
    janela_com_as_cinco_categorias(pagina)
    pagina.js("document.getElementById('compra-data').value = ''")
    preencher(pagina, {"#compra-data": "10092026"})
    assert pagina.valor("#compra-data") == "10/09/2026"
    escolher_local(pagina, "tiktok_shop")
    preencher_filamento(pagina, 1, qtd="2", valor="8000")
    preencher_item(pagina, 2, nome="Argola", qtd="10", valor="150", cor="Dourada", medidas="2 cm")
    preencher_item(pagina, 3, nome="Caixa", qtd="4", valor="225", medidas="10x10x5 cm")
    preencher_item(pagina, 4, nome="Bico 0,4", qtd="2", valor="1500", marca="Bambu")
    preencher_item(pagina, 5, nome="Fita dupla face", qtd="1", valor="750")
    preencher(pagina, {"#compra-desconto": "2150", "#compra-frete": "1200"})
    for indice, nome in ((1, "argola.png"), (4, "fita.png")):  # fotos independentes por linha
        nav.novo_arquivo(pagina, nome)
        nav.escolher(pagina, indice)
        nav.esperar_previa(pagina, nome, indice)
    assert totais(pagina) == ["R$ 221,50", "R$ 212,00"]

    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.js("location.pathname + location.search") == "/compras?salvo=compra"
    (gravada,) = estoque.compras.values()
    c = gravada.compra
    assert (c.data_compra, c.local_tipo, c.desconto, c.frete) == (
        date(2026, 9, 10),
        "tiktok_shop",
        Decimal("21.50"),
        Decimal("12.00"),
    )
    fil, ace, emb, peca, outro = c.itens
    assert [i.categoria for i in c.itens] == list(CATEGORIAS)
    assert (fil.cor, fil.marca, fil.valor_unitario) == ("Preto", "Marca Teste", Decimal("80.00"))
    assert (ace.cor, ace.medidas, ace.valor_total) == ("Dourada", "2 cm", Decimal("15.00"))
    assert (emb.cor, emb.medidas) == (None, "10x10x5 cm")
    assert (peca.marca, peca.valor_unitario) == ("Bambu", Decimal("15.00"))
    assert (outro.marca, outro.valor_total) == (None, Decimal("7.50"))
    assert [i.imagem_caminho is not None for i in c.itens] == [False, True, False, False, True]
    # Estoque só para filamento, acessório e embalagem.
    assert len(estoque.filamentos) == 1
    assert sorted(i.categoria for i in estoque.itens.values()) == ["acessorio", "embalagem"]
    # A compra aparece com o total já com desconto e frete.
    assert "R$ 212,00" in pagina.texto("#card-compras")
    lista = pagina.texto("#ultimas-compras")
    assert "TikTok Shop" in lista and "10 un · Argola · Dourada · 2 cm" in lista
    assert "2 un · Bico 0,4 · Bambu" in lista and "desconto de R$ 21,50" in lista


def test_abrir_focar_e_cancelar_sem_gravar(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert pagina.js("document.activeElement.id") == "compra-data"
    assert pagina.js(NOMES_DAS_LINHAS) == ["Item 1"]
    assert pagina.valor(linha(1, "categoria")) == "filamento"
    chave = pagina.valor('[name="chave_envio"]')
    assert len(chave) == 36 and chave[14] == "4"

    preencher_filamento(pagina, 1)
    pagina.clicar(".compra-rodape [data-cancelar]")

    assert not pagina.js("document.getElementById('compra-janela').open")
    nav.esperar(lambda: pagina.js("document.activeElement.hasAttribute('data-abrir-compra')"))
    assert not estoque.compras and not estoque.itens
    # Reabrir começa do zero, com outra chave de submissão.
    abrir_janela(pagina)
    assert pagina.valor("#compra-data") == pagina.js(HOJE_JS_EXPR)
    assert pagina.js(NOMES_DAS_LINHAS) == ["Item 1"]
    assert pagina.valor(linha(1, "cor")) == ""
    assert pagina.valor('[name="chave_envio"]') != chave

    tecla(pagina, "Escape", codigo=27)  # Esc também cancela
    nav.esperar(lambda: not pagina.js("document.getElementById('compra-janela').open"))
    assert not estoque.compras


def test_data_em_dd_mm_aaaa_e_local_por_teclado(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    assert pagina.js("document.getElementById('compra-data').type") == "text"
    assert pagina.js("document.getElementById('compra-data').placeholder") == "dd/mm/aaaa"
    assert pagina.valor("#compra-data") == pagina.js(HOJE_JS_EXPR)
    pagina.js("document.getElementById('compra-data').value = ''")
    preencher(pagina, {"#compra-data": "1009a2026"})
    assert pagina.valor("#compra-data") == "10/09/2026"

    # Botões de seleção única: Tab chega ao grupo e as setas escolhem.
    pagina.js("document.querySelector('#campo-local input[value=mercado_livre]').focus()")
    tecla(pagina, " ", "Space", 32)
    for _ in range(3):
        tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "loja_fisica"
    assert not pagina.js("document.getElementById('campo-local_nome').hidden")
    assert pagina.texto("#campo-local_nome label") == "Nome do estabelecimento ou site *"

    pagina.digitar("#compra-local-nome", "Feira")
    pagina.js("document.querySelector('#campo-local input:checked').focus()")
    tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "outro"
    assert pagina.valor("#compra-local-nome") == "Feira"  # entre Loja Física e Outros sites, fica

    escolher_local(pagina, "tiktok_shop")
    assert pagina.js("document.getElementById('campo-local_nome').hidden")
    assert pagina.valor("#compra-local-nome") == ""  # plataforma: o texto é limpo


def test_locais_na_ordem_com_logos_e_icones(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    nomes = pagina.js(
        "[...document.querySelectorAll('#campo-local .escolha-titulo')].map(e => e.textContent)"
    )
    assert nomes == [
        "TikTok Shop",
        "Mercado Livre",
        "AliExpress",
        "Shopee",
        "Loja Física",
        "Outros sites",
    ]
    assert pagina.js(
        "[...document.querySelectorAll('#campo-local .compra-local-marca img')]"
        ".every(i => i.complete && i.naturalWidth > 0)"
    )
    assert (
        pagina.js("document.querySelectorAll('#campo-local .compra-local-marca svg').length") == 3
    )
    # Todos os locais numa só linha no desktop.
    topos = pagina.js(
        "new Set([...document.querySelectorAll('#campo-local .escolha-card')]"
        ".map(c => Math.round(c.getBoundingClientRect().top))).size"
    )
    assert topos == 1


def test_erros_do_servidor_nos_campos_preservando_o_que_foi_digitado(pagina, estoque):
    pagina.tela(360, 740)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    n = adicionar(pagina, "embalagem")
    preencher_filamento(pagina, 1, cor="")
    preencher(pagina, {linha(n, "nome"): "Caixa", linha(n, "quantidade"): "3"})
    escolher_local(pagina, "outro")
    pagina.js("document.getElementById('compra-data').value = ''")  # vem preenchida com hoje
    nav.novo_arquivo(pagina, "caixa.png")
    nav.escolher(pagina, 1)
    nav.esperar_previa(pagina, "caixa.png", 1)
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
        "Informe o nome do estabelecimento ou site.",
        "Informe a cor.",
        "Informe o valor unitário.",
    ]
    # O foco vai para o primeiro campo com erro, ligado à mensagem.
    assert pagina.js("document.activeElement.id") == "compra-data"
    assert pagina.js("document.activeElement.getAttribute('aria-invalid')") == "true"
    descrito = pagina.js("document.activeElement.getAttribute('aria-describedby')")
    assert pagina.texto(f"#{descrito}") == "Informe a data de compra."
    # O que foi digitado e a foto escolhida continuam lá.
    assert pagina.valor(linha(1, "marca")) == "Marca Teste"
    assert pagina.valor(linha(n, "nome")) == "Caixa"
    assert nav.arquivos_por_item(pagina) == [None, "caixa.png"]
    # Corrigir um campo apaga o aviso dele.
    pagina.digitar("#compra-data", "10092026")
    assert not pagina.js(
        "document.getElementById('campo-data_compra').classList.contains('tem-erro')"
    )
    assert not estoque.compras


def test_desconto_maior_que_o_subtotal_volta_marcado(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    mudar_categoria(pagina, 1, "outro")
    preencher_item(pagina, 1, nome="Fita", qtd="1", valor="500")
    escolher_local(pagina, "shopee")
    preencher(pagina, {"#compra-desconto": "600"})
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.texto("#campo-desconto .campo-erro") == (
        "O desconto não pode ser maior que o subtotal dos itens."
    )
    assert pagina.js("document.activeElement.id") == "compra-desconto"
    assert not estoque.compras


def test_compra_sem_itens_pede_um_item(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    pagina.clicar(".compra-item-remover")
    escolher_local(pagina, "shopee")
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.texto("#campo-itens .campo-erro") == "Adicione pelo menos um item."
    assert pagina.js("document.activeElement.hasAttribute('data-adicionar')")
    pagina.clicar("[data-adicionar]")
    assert pagina.js("!document.querySelector('#campo-itens .campo-erro')")


def test_duplo_clique_grava_uma_vez(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    mudar_categoria(pagina, 1, "embalagem")
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
    abrir_janela(pagina)
    mudar_categoria(pagina, 1, "embalagem")
    escolher_local(pagina, "shopee")
    preencher_item(pagina, 1, nome="Caixa", cor="Kraft")
    preencher(pagina, {"#compra-desconto": "100"})
    nav.novo_arquivo(pagina, "caixa.png")
    nav.escolher(pagina, 0)
    nav.esperar_previa(pagina, "caixa.png", 0)
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.js("document.getElementById('compra-janela').open")
    assert pagina.texto("#compra-alerta") == (
        "Não foi possível salvar agora. Tente novamente em instantes."
    )
    assert pagina.valor(linha(1, "nome")) == "Caixa" and pagina.valor(linha(1, "cor")) == "Kraft"
    assert pagina.valor("#compra-desconto") == "R$ 1,00"
    assert nav.arquivos_por_item(pagina) == ["caixa.png"]  # a foto continua escolhida
    assert not estoque.compras and not estoque.imagens  # a foto enviada foi removida

    # Tentar de novo (mesma submissão) grava normalmente.
    estoque.falhar_em = set()
    salvar(pagina)
    esperar_fim_do_envio(pagina)
    assert len(estoque.compras) == 1 and len(estoque.imagens) == 1


def test_sugestoes_de_nomes_cores_e_marcas_ja_registrados(pagina, estoque):
    estoque.reservar_id_compra()
    estoque.registrar_compra(
        CompraParaGravar(
            1,
            uuid.uuid4(),
            date(2026, 9, 1),
            "shopee",
            None,
            1,
            (
                ItemCompraParaGravar(
                    ordem=1,
                    categoria="acessorio",
                    quantidade=2,
                    nome="Argola",
                    cor="Dourada",
                    valor_unitario=Decimal("1"),
                    valor_total=Decimal("2"),
                ),
                ItemCompraParaGravar(
                    ordem=2,
                    categoria="peca_manutencao",
                    quantidade=1,
                    nome="Bico 0,4",
                    marca="Bambu",
                    valor_unitario=Decimal("1"),
                    valor_total=Decimal("1"),
                ),
            ),
        )
    )
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)

    def opcoes(lista):
        return pagina.js(f"[...document.querySelectorAll('#{lista} option')].map(o => o.value)")

    assert opcoes("compra-nomes-acessorio") == ["Argola"]
    assert opcoes("compra-nomes-peca_manutencao") == ["Bico 0,4"]
    assert "Dourada" in opcoes("compra-cores") and opcoes("compra-marcas") == ["Bambu"]
    # Valores novos são aceitos.
    mudar_categoria(pagina, 1, "peca_manutencao")
    preencher_item(pagina, 1, nome="Correia GT2", qtd="1", valor="900", marca="Genérica")
    escolher_local(pagina, "shopee")
    salvar(pagina)
    esperar_fim_do_envio(pagina)
    assert estoque.compras[2].compra.itens[0].marca == "Genérica"


def test_foto_compacta_preserva_previa_troca_e_remocao(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_janela(pagina)
    nav.novo_arquivo(pagina, "rolo.png")
    nav.escolher(pagina, 0)
    nav.esperar_previa(pagina, "rolo.png", 0)

    foto = "#compra-itens .compra-item[data-pos='1'] "
    assert pagina.js(f'!document.querySelector("{foto}.upload-miniatura").hidden')
    assert pagina.js(
        f"document.querySelector(\"{foto}.upload-trocar\").getAttribute('aria-label')"
    ) == ("Trocar foto")
    pagina.clicar(f"{foto}.upload-remover")
    assert nav.arquivos_por_item(pagina) == [None]
    assert pagina.js(f'!document.querySelector("{foto}.upload-selecionar").hidden')


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


# ---------- Custos: tabelas de acessórios e embalagens com foto, prévia e lupa ----------


@pytest.fixture
def com_fotos_de_custos(estoque):
    estoque.com_item("Imã", valor_total=Decimal("30"), imagem_caminho="compras/1/ima.png")
    estoque.com_item("Fita dupla face", quantidade=4)  # sem foto
    estoque.com_item("Caixa P", categoria="embalagem", imagem_caminho="compras/1/caixa.png")
    estoque.imagens["compras/1/ima.png"] = b"x"
    estoque.imagens["compras/1/caixa.png"] = b"x"
    return estoque


def esperar_foto_de_custos(p):
    nav.esperar(
        lambda: p.js(
            "document.querySelector('.custos-tabela-insumos .pedido-tabela-foto').complete"
        )
    )


def test_custos_tabela_de_itens_no_padrao_da_lista_de_pedidos(pagina, com_fotos_de_custos):
    pagina.tela(1280, 900)
    pagina.abrir("/custos")
    esperar_foto_de_custos(pagina)

    colunas = pagina.js(
        "[...document.querySelector('.custos-tabela-insumos thead').querySelectorAll('th')]"
        ".map(th => th.textContent.trim())"
    )
    assert colunas == ["Foto", "Item", "Quantidade", "Custo unitário"]
    assert pagina.js("document.querySelectorAll('.custos-tabela-insumos').length") == 2
    botao = "document.querySelector('.custos-tabela-insumos .foto-previa-botao')"
    miniatura = f"{botao}.getBoundingClientRect()"
    assert pagina.js(f"{miniatura}.width") == 48 and pagina.js(f"{miniatura}.height") == 48
    assert (
        pagina.js("document.querySelectorAll('.custos-tabela-insumos .custos-sem-foto').length")
        == 1
    )
    # Sem coluna Editar nem lápis; mesmo quadro e tabela da Lista de Pedidos.
    assert (
        pagina.js("document.querySelectorAll('.custos-tabela-insumos .custos-lapis').length") == 0
    )
    assert pagina.js(
        "[...document.querySelectorAll('.custos-tabela-insumos')]"
        ".every(t => t.classList.contains('tabela-pedidos')"
        " && t.parentElement.classList.contains('tabela-rolagem'))"
    )
    assert (
        pagina.js(
            "getComputedStyle(document.querySelector('.custos-tabela-insumos thead th')).position"
        )
        == "sticky"
    )
    assert sem_rolagem_horizontal(pagina)


def test_custos_previa_abre_no_mouse_com_lupa_sem_deslocar_a_tabela_e_fecha_com_esc(
    pagina, com_fotos_de_custos
):
    pagina.tela(1280, 900)
    pagina.abrir("/custos")
    esperar_foto_de_custos(pagina)
    tabela = "document.querySelector('#insumos').getBoundingClientRect().height"
    altura = pagina.js(tabela)
    pagina.js(
        "document.querySelector('.custos-tabela-insumos .foto-previa-botao')"
        ".scrollIntoView({block: 'center', behavior: 'instant'})"
    )
    assentar_rolagem(pagina)
    x, y = pagina.js(
        "(() => { const r = document.querySelector('.custos-tabela-insumos .foto-previa-botao')"
        ".getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2] })()"
    )

    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    nav.esperar(lambda: previa_aberta(pagina), tempo=4, mensagem="a prévia não abriu")
    assert dentro_da_tela(pagina) and pagina.js(ESCALA) == 1
    assert pagina.js(tabela) == altura  # a prévia flutua: a tabela não se desloca

    esq, topo = pagina.js(
        "(() => { const r = document.querySelector('.foto-previa-quadro').getBoundingClientRect();"
        " return [r.left, r.top] })()"
    )
    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=esq + 40, y=topo + 40)
    nav.esperar(lambda: pagina.js(ESCALA) == 2.5, mensagem="a lupa não ampliou")
    assert pagina.js(ORIGEM).startswith("40px 40px")  # o movimento do cursor revela os detalhes

    pagina.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
    nav.esperar(lambda: not previa_aberta(pagina))


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_custos_previa_por_toque_e_teclado_tem_lupa_fechar_e_cabe_na_tela(
    pagina, com_fotos_de_custos, largura
):
    pagina.tela(largura, 700)
    pagina.abrir("/custos")
    esperar_foto_de_custos(pagina)
    assert sem_rolagem_horizontal(pagina)

    pagina.clicar(".custos-tabela-insumos .foto-previa-botao")
    assert previa_aberta(pagina) and dentro_da_tela(pagina)
    assert pagina.js("document.activeElement.className").startswith("foto-previa-fechar")
    assert pagina.js("document.querySelector('.foto-previa-lupa').offsetWidth") >= 44
    assert pagina.js("document.querySelector('.foto-previa-fechar').offsetWidth") >= 44

    pagina.clicar(".foto-previa-lupa")  # botões: fixam o zoom; as setas movem a área ampliada
    nav.esperar(lambda: pagina.js(ESCALA) == 2.5)
    antes = pagina.js(ORIGEM)
    tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js(ORIGEM) != antes
    pagina.clicar(".foto-previa-lupa")
    nav.esperar(lambda: pagina.js(ESCALA) == 1)

    pagina.clicar(".foto-previa-fechar")
    assert not previa_aberta(pagina)
    assert pagina.js("document.activeElement.className") == "foto-previa-botao"

    pagina.clicar(".custos-tabela-insumos .foto-previa-botao")
    tecla(pagina, "Escape", codigo=27)
    nav.esperar(lambda: not previa_aberta(pagina))
    assert pagina.js("document.documentElement.scrollWidth") <= largura


def janela_de_historico(p) -> bool:
    return p.js("!!document.querySelector('.custos-hist')")


def historico_dentro_da_tela(p) -> bool:
    return p.js(
        "(() => { const r = document.querySelector('.custos-hist').getBoundingClientRect();"
        " return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight })()"
    )


@pytest.fixture
def com_historico(estoque):
    estoque.com_item("Imã", quantidade=4, quantidade_comprada=10, valor_total=Decimal("30"))
    estoque.com_item(
        "Imã", quantidade=40, quantidade_comprada=40, valor_total=Decimal("1200.50"),
        data_compra=date(2026, 10, 15),
    )  # fmt: skip
    estoque.com_item("Caixa P", categoria="embalagem", quantidade=4, valor_total=Decimal("10"))
    return estoque


def test_custos_historico_de_compras_ao_passar_o_mouse_no_custo_unitario(pagina, com_historico):
    pagina.tela(1280, 900)
    pagina.abrir("/custos")
    altura = pagina.js("document.querySelector('#insumos').getBoundingClientRect().height")
    pagina.js(
        "document.querySelector('.custos-hist-gatilho')"
        ".scrollIntoView({block: 'center', behavior: 'instant'})"
    )
    assentar_rolagem(pagina)
    x, y = pagina.js(
        "(() => { const r = document.querySelector('.custos-hist-gatilho').getBoundingClientRect();"
        " return [r.left + r.width / 2, r.top + r.height / 2] })()"
    )
    assert not janela_de_historico(pagina)

    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    nav.esperar(lambda: janela_de_historico(pagina), tempo=3, mensagem="a janela não abriu")
    assert historico_dentro_da_tela(pagina)
    colunas = pagina.js(
        "[...document.querySelectorAll('.custos-hist thead th')].map(th => th.textContent.trim())"
    )
    linhas = pagina.js(
        "[...document.querySelectorAll('.custos-hist tbody tr')]"
        ".map(tr => [...tr.children].map(td => td.textContent.trim()))"
    )
    assert colunas == ["Data", "Quantidade", "Valor"]
    assert linhas == [["15/10/2026", "40", "R$ 1.200,50"], ["01/09/2026", "10", "R$ 30,00"]]
    assert pagina.js("document.querySelector('#insumos').getBoundingClientRect().height") == altura

    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
    nav.esperar(lambda: not janela_de_historico(pagina), tempo=3, mensagem="a janela não fechou")


def assentar_rolagem(p):
    """Espera o evento scroll: ele chega um quadro depois e fecha a prévia e o histórico."""
    p.js("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))", aguardar=True)


@pytest.mark.parametrize("largura", [1200, 1280, 1920])
@pytest.mark.parametrize("materiais", [("TPU",), ("TPU", "ABS", "Nylon")])
def test_custos_materiais_adicionais_ficam_na_mesma_linha_redimensionados(
    pagina, repo_custos, largura, materiais
):
    estoque = nav.estoque_de_custos()
    for material in materiais:
        estoque.com_filamento(material=material, valor_unitario=Decimal("1234.56"))
        repo_custos.ajustes[f"filamento:material:{material}"] = Decimal("1234.56")
    pagina.tela(largura, 900)
    pagina.abrir("/custos")

    cards = pagina.js(
        "[...document.querySelectorAll('#filamentos .custos-fil')].map(c => {"
        " const a = c.getBoundingClientRect();"
        " return {largura: Math.round(a.width), topo: Math.round(a.top),"
        " valor: Math.round(c.querySelector('.custos-fil-valor').getBoundingClientRect().top),"
        " cabe: c.scrollWidth <= c.clientWidth} })"
    )

    assert len(cards) == 4 + len(materiais), cards
    for campo in ("largura", "topo", "valor"):
        assert len({c[campo] for c in cards}) == 1, (campo, cards)
    assert all(c["cabe"] for c in cards), cards
    assert pagina.js("document.documentElement.scrollWidth") <= largura


@pytest.mark.parametrize(("largura", "por_linha"), [(360, 1), (768, 2), (1280, 4), (1600, 4)])
def test_custos_cards_iguais_com_valor_alinhado_e_centralizado(pagina, largura, por_linha):
    medir = (
        "[...document.querySelectorAll('#filamentos .custos-fil')]"
        ".map(c => { const a = c.getBoundingClientRect();"
        " const v = c.querySelector('.custos-fil-valor');"
        " const r = document.createRange(); r.selectNodeContents(v);"
        " const b = r.getBoundingClientRect(); const l = v.getBoundingClientRect();"
        " return {largura: Math.round(a.width), topo: Math.round(a.top),"
        " altura: Math.round(a.height), valor: Math.round(l.top),"
        " nome: Math.round(c.querySelector('.custos-fil-topo').getBoundingClientRect().top),"
        " dx: Math.abs((a.left + a.right - b.left - b.right) / 2)} })"
    )
    pagina.tela(largura, 900)
    pagina.abrir("/custos")
    cards = pagina.js(medir)

    assert len(cards) == 4 and len({c["largura"] for c in cards}) == 1, cards  # com a perda
    assert all(c["dx"] <= 1 for c in cards), cards
    assert pagina.js("document.documentElement.scrollWidth") <= largura
    for linha in {c["topo"] for c in cards}:
        mesma = [c for c in cards if c["topo"] == linha]
        assert len(mesma) <= por_linha
        for campo in ("altura", "valor", "nome"):
            assert len({c[campo] for c in mesma}) == 1, (campo, mesma)

    pagina.js("document.querySelector('#perdas details').open = true")
    # O valor não sai do lugar dentro do card.
    for depois, antes in zip(pagina.js(medir), cards, strict=True):
        assert abs((depois["valor"] - depois["topo"]) - (antes["valor"] - antes["topo"])) <= 1


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_custos_historico_por_toque_ou_teclado_cabe_na_tela_e_fecha_com_esc(
    pagina, com_historico, largura
):
    pagina.tela(largura, 700)
    pagina.abrir("/custos")
    assert pagina.js("document.querySelector('.custos-hist-gatilho').offsetHeight") >= 44

    pagina.clicar(".custos-hist-gatilho")
    assert janela_de_historico(pagina) and historico_dentro_da_tela(pagina)
    assert pagina.js(
        "document.querySelector('.custos-hist-gatilho').getAttribute('aria-describedby')"
    )
    assert pagina.js("document.documentElement.scrollWidth") <= largura

    tecla(pagina, "Escape", codigo=27)
    assert not janela_de_historico(pagina)
    assert pagina.js("document.activeElement.className") == "custos-hist-gatilho"

    assentar_rolagem(pagina)  # o Esc devolve o foco ao botão, o que pode rolar a página
    pagina.clicar(".custos-hist-gatilho")  # alterna: abre e, ao tocar de novo, fecha
    assert janela_de_historico(pagina)
    pagina.clicar(".custos-hist-gatilho")
    assert not janela_de_historico(pagina)


# ---------- Editar compra e ordenar pela data ----------


@pytest.fixture
def com_compra_editavel(estoque):
    """Compra 1 (01/09, com foto no filamento) e compra 2 (05/09, só acessório)."""
    foto = "compras/1/itens/1/0f8fad5b-d9cb-469f-a165-70867728950e.png"
    estoque.imagens[foto] = b"x"
    filamento = ItemCompraParaGravar(
        ordem=1,
        categoria="filamento",
        quantidade=2,
        cor="Preto",
        material="PETG",
        tipo="silk",
        marca="Voolt",
        peso_rolo_g=Decimal("500"),
        valor_unitario=Decimal("45.90"),
        imagem_caminho=foto,
    )
    argola = ItemCompraParaGravar(
        ordem=2,
        categoria="acessorio",
        quantidade=40,
        nome="Argola",
        cor="Dourada",
        medidas="2 cm",
        valor_unitario=Decimal("0.35"),
        valor_total=Decimal("14.00"),
    )
    for n, (dia, local, nome, itens) in enumerate(
        [(1, "loja_fisica", "Papelaria", (filamento, argola)), (5, "shopee", None, (argola,))], 1
    ):
        estoque.reservar_id_compra()
        estoque.registrar_compra(
            CompraParaGravar(
                n,
                uuid.uuid4(),
                date(2026, 9, dia),
                local,
                nome,
                1,
                itens,
                Decimal("7.50"),
                Decimal("1.00"),
            )
        )
    return estoque


def test_compras_usam_quase_toda_a_largura_da_janela(pagina, com_compra_editavel):
    pagina.tela(1920, 900)
    pagina.abrir("/compras")

    assert pagina.js("getComputedStyle(document.querySelector('main')).maxWidth") == "2000px"
    # A coluna Itens fica com a maior parte da tabela; Ações tem o botão Editar.
    larguras = pagina.js(
        "[...document.querySelectorAll('.tabela-compras thead th')]"
        ".map(th => th.getBoundingClientRect().width)"
    )
    assert larguras[2] == max(larguras) and larguras[2] > sum(larguras) / 2
    assert pagina.js("document.querySelectorAll('[data-editar-compra]').length") == 2


def test_botao_do_titulo_ordena_por_data(pagina, com_compra_editavel):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    datas = "[...document.querySelectorAll('.compra-registro time')].map(t => t.textContent)"
    assert pagina.js(datas) == ["05/09/2026", "01/09/2026"]

    pagina.clicar("#ordenar-data")
    nav.esperar(lambda: "ordem=antigas" in pagina.js("location.search"))
    nav.esperar(lambda: pagina.js("document.readyState === 'complete'"))

    assert pagina.js(datas) == ["01/09/2026", "05/09/2026"]
    assert pagina.js("document.getElementById('ordenar-data').classList.contains('ativo')")
    pagina.clicar("#ordenar-data")
    nav.esperar(lambda: "ordem=recentes" in pagina.js("location.search"))
    nav.esperar(lambda: pagina.js("document.readyState === 'complete'"))
    assert pagina.js(datas) == ["05/09/2026", "01/09/2026"]


def abrir_edicao(p, n):
    """Abre a janela pelo botão Editar da n-ésima compra da lista (1 = primeira)."""
    p.clicar(f".compra-registro:nth-child({n}) [data-editar-compra]")
    nav.esperar(lambda: p.js("document.getElementById('compra-janela').open"))


@pytest.mark.parametrize("largura", [360, 1280])
def test_editar_abre_a_compra_preenchida_e_salva_as_correcoes(pagina, com_compra_editavel, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/compras")
    abrir_edicao(pagina, 2)  # compra 1 (01/09), a segunda da lista

    assert pagina.texto("#compra-titulo") == "Editar Compra"
    assert pagina.texto('#form-compra button[type="submit"]') == "Salvar alterações"
    assert pagina.valor("#compra-data") == "01/09/2026"
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "loja_fisica"
    assert pagina.valor("#compra-local-nome") == "Papelaria"
    assert pagina.valor("#compra-frete") == "R$ 7,50"
    assert pagina.valor("#compra-desconto") == "R$ 1,00"
    campos = ("categoria", "quantidade", "material", "tipo", "peso_rolo", "cor", "marca")
    assert [pagina.valor(linha(1, c)) for c in campos] == [
        "filamento",
        "2",
        "PETG",
        "silk",
        "500",
        "Preto",
        "Voolt",
    ]
    assert pagina.valor(linha(1, "valor_unitario")) == "R$ 45,90"
    # Atributos do acessório reabrem como foram gravados.
    campos = ("categoria", "quantidade", "nome", "cor", "medidas", "valor_unitario")
    assert [pagina.valor(linha(2, c)) for c in campos] == [
        "acessorio",
        "40",
        "Argola",
        "Dourada",
        "2 cm",
        "R$ 0,35",
    ]
    assert rotulos(pagina, 2) == ["Categoria", "Item", "Qtd.", "Cor", "Medidas", "Valor unit."]
    # A foto atual aparece na linha do filamento, com Trocar e Remover.
    assert pagina.valor(linha(1, "foto_atual")).startswith("compras/1/itens/1/")
    assert pagina.js(
        "!document.querySelector('.compra-item[data-pos=\"1\"] .upload-remover').hidden"
    )
    assert totais(pagina) == ["R$ 105,80", "R$ 112,30"]  # 2 x 45,90 + 14,00 − 1,00 + 7,50
    assert sem_rolagem_horizontal(pagina)

    # Correções: outra data e outra cor; a argola sai e a foto do filamento é mantida.
    pagina.js("document.getElementById('compra-data').value = ''")
    preencher(pagina, {"#compra-data": "03092026"})
    pagina.js(f"document.querySelector('{linha(1, 'cor')}').value = ''")
    pagina.digitar(linha(1, "cor"), "Branco")
    pagina.clicar("#compra-itens .compra-item[data-pos='2'] .compra-item-remover")
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert pagina.js("location.pathname + location.search") == "/compras?salvo=compra-editada"
    assert pagina.texto(".alerta-sucesso") == "Compra atualizada. O estoque foi ajustado."
    c = com_compra_editavel.compras[1].compra
    assert c.data_compra == date(2026, 9, 3) and c.desconto == Decimal("1.00")
    assert [(i.categoria, i.cor) for i in c.itens] == [("filamento", "Branco")]
    assert c.itens[0].imagem_caminho.startswith("compras/1/itens/1/")
    assert len(com_compra_editavel.compras) == 2  # nenhuma compra nova
    assert "2 rolos · PETG Silk Branco" in pagina.texto("#ultimas-compras")


def test_editar_mantem_atributos_ao_salvar_sem_mudar(pagina, com_compra_editavel):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_edicao(pagina, 2)
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    _, argola = com_compra_editavel.compras[1].compra.itens
    assert (argola.cor, argola.medidas, argola.valor_unitario, argola.valor_total) == (
        "Dourada",
        "2 cm",
        Decimal("0.35"),
        Decimal("14.00"),
    )


def test_remover_a_foto_na_edicao_apaga_a_foto_gravada(pagina, com_compra_editavel):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_edicao(pagina, 2)

    pagina.clicar(".compra-item[data-pos='1'] .upload-remover")
    assert pagina.valor(linha(1, "foto_atual")) == ""
    salvar(pagina)
    esperar_fim_do_envio(pagina)

    assert com_compra_editavel.compras[1].compra.itens[0].imagem_caminho is None
    assert com_compra_editavel.removidas == [
        "compras/1/itens/1/0f8fad5b-d9cb-469f-a165-70867728950e.png"
    ]


def test_cancelar_edicao_e_registrar_volta_ao_modo_de_compra_nova(pagina, com_compra_editavel):
    pagina.tela(1280, 900)
    pagina.abrir("/compras")
    abrir_edicao(pagina, 1)
    pagina.clicar("#form-compra [data-cancelar]")
    nav.esperar(lambda: not pagina.js("document.getElementById('compra-janela').open"))
    nav.esperar(lambda: pagina.js("document.activeElement.hasAttribute('data-editar-compra')"))

    abrir_janela(pagina)

    assert pagina.texto("#compra-titulo") == "Registrar Compra"
    assert pagina.texto('#form-compra button[type="submit"]') == "Salvar compra"
    assert pagina.js("document.getElementById('form-compra').getAttribute('action')") == "/compras"
    assert pagina.js("document.querySelectorAll('#compra-itens .compra-item').length") == 1
    assert pagina.valor(linha(1, "cor")) == "" and pagina.valor("#compra-frete") == ""
    assert pagina.valor("#compra-desconto") == ""
    assert com_compra_editavel.edicoes == []
