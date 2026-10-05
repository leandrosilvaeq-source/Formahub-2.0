"""Tela Estoque em navegador real (Edge ou Chrome, sem interface), nas larguras do projeto.

Reaproveita o servidor e o navegador de test_navegador.py; só dados fictícios em memória.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.estoque_repositorio import Filamento, ItemEstoque, get_repositorio_estoque
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


@pytest.fixture
def com_lotes(estoque):
    nomes = [
        ("Azul Petróleo Metalizado", "PETG", "silk", "Marca Com Nome Bem Comprido Ltda"),
        ("Preto", "PLA", "solido", "Marca A"),
        ("Branco", "PLA", "tricolor", "Marca B"),
    ]
    for cor, material, tipo, marca in nomes:
        lote = Filamento(
            None,
            cor,
            material,
            tipo,
            marca,
            Decimal("1234567.89"),
            date(2026, 9, 1),
            Decimal("1234567.89"),
        )
        estoque.salvar_filamento(lote, 1)
    for categoria, nome in (
        ("acessorio", "Argola de chaveiro inox 25 mm"),
        ("embalagem", "Caixa kraft 10x10x10"),
    ):
        lote = ItemEstoque(None, categoria, nome, 999999, date(2026, 9, 2), Decimal("12.50"))
        estoque.salvar_item(lote, 1)
    return estoque


def sem_rolagem_horizontal(p) -> bool:
    return p.js("document.documentElement.scrollWidth <= window.innerWidth")


def cortados(p) -> list[str]:
    """Textos de células que saem da própria célula ou da seção (cortes visuais)."""
    return p.js(
        """[...document.querySelectorAll('.estoque-area')].flatMap(secao => {
             const limite = secao.getBoundingClientRect();
             return [...secao.querySelectorAll('th, td, .botao, input, select')]
               .filter(el => {
                 const r = el.getBoundingClientRect();
                 return r.width > 0 && (r.left < limite.left - 1 || r.right > limite.right + 1
                   || el.scrollWidth > el.clientWidth + 1 && el.tagName !== 'SELECT'
                      && el.tagName !== 'INPUT');
               })
               .map(el => el.textContent.trim() || el.name);
           })"""
    )


@pytest.mark.parametrize("largura", LARGURAS)
def test_lista_sem_rolagem_horizontal_nem_cortes(pagina, com_lotes, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    assert sem_rolagem_horizontal(pagina)
    assert cortados(pagina) == []
    titulos = pagina.js(
        "[...document.querySelectorAll('.estoque-area h2')].map(h => h.textContent)"
    )
    assert titulos == ["Filamentos", "Acessórios", "Embalagens"]


@pytest.mark.parametrize("largura", LARGURAS)
def test_tabela_no_desktop_e_cartoes_no_celular(pagina, com_lotes, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    cabecalho = "getComputedStyle(document.querySelector('.estoque-tabela-{} thead')).position"
    assert (pagina.js(cabecalho.format("filamentos")) == "static") == (largura >= 1024)
    assert (pagina.js(cabecalho.format("itens")) == "static") == (largura >= 760)
    # Nos cartões, cada valor leva o rótulo da coluna.
    rotulo = pagina.js(
        "getComputedStyle(document.querySelector('.estoque-tabela-filamentos td[data-rotulo]'),"
        " '::before').content"
    )
    assert (rotulo == '"Material"') == (largura < 1024)


@pytest.mark.parametrize("largura", LARGURAS)
def test_alvos_de_toque_de_44px(pagina, com_lotes, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    pequenos = pagina.js(
        """[...document.querySelectorAll('.estoque .botao, .estoque input, .estoque select')]
             .filter(el => el.type !== 'hidden' && el.getBoundingClientRect().height < 44)
             .map(el => el.textContent.trim() || el.name)"""
    )
    assert pequenos == []


@pytest.mark.parametrize("largura", LARGURAS)
def test_estado_vazio(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    assert sem_rolagem_horizontal(pagina)
    vazios = pagina.js("document.querySelectorAll('.estoque-vazio').length")
    assert vazios == 3


@pytest.mark.parametrize("largura", LARGURAS)
def test_formulario_de_filamento_com_erros(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque/filamentos/novo")
    pagina.js("document.querySelector('form[action^=\"/estoque\"] button[type=submit]').click()")
    nav.esperar(lambda: pagina.js("!!document.querySelector('.alerta-erro')"))

    assert sem_rolagem_horizontal(pagina)
    erros = pagina.js("document.querySelectorAll('.campo-erro').length")
    assert erros == 7
    # Campos lado a lado a partir de 640 px; empilhados no celular.
    colunas = pagina.js(
        "getComputedStyle(document.querySelector('.estoque-grade')).gridTemplateColumns"
        ".split(' ').length"
    )
    assert colunas == (1 if largura < 640 else 2)


def test_cadastro_pelo_navegador(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/estoque/filamentos/novo")
    for campo, valor in (
        ("cor", "Vermelho"),
        ("material", "PLA"),
        ("marca", "Marca Teste"),
        ("peso", "750,5"),
        ("custo_kg", "99,90"),
    ):
        pagina.digitar(f"#{campo}", valor)
    pagina.js("document.getElementById('tipo').value = 'velvet'")
    pagina.js("document.getElementById('data_compra').value = '2026-09-20'")
    pagina.js("document.querySelector('form[action^=\"/estoque\"] button[type=submit]').click()")
    nav.esperar(
        lambda: pagina.js(
            "location.pathname === '/estoque' && !!document.querySelector('.alerta-sucesso')"
        )
    )

    assert pagina.texto(".alerta-sucesso") == "Lote de filamento cadastrado."
    assert pagina.js("location.hash") == "#filamentos"
    assert "750,5 g" in pagina.texto("#filamentos-total")
    (lote,) = estoque.filamentos.values()
    assert (lote.tipo, lote.peso_g) == ("velvet", Decimal("750.5"))


def test_filtro_pelo_navegador_mantem_as_outras_areas(pagina, com_lotes):
    pagina.tela(1280, 900)
    pagina.abrir("/estoque?a_busca=argola")
    pagina.js("document.getElementById('f_tipo').value = 'silk'")
    pagina.js("document.querySelector('#filamentos form button[type=submit]').click()")
    nav.esperar(lambda: pagina.js("location.search.includes('f_tipo=silk')"))
    nav.esperar(lambda: pagina.js("document.readyState === 'complete'"))

    assert "a_busca=argola" in pagina.js("location.search")
    linhas = pagina.js("document.querySelectorAll('#filamentos tbody tr').length")
    assert linhas == 1
    assert "Total filtrado" in pagina.texto("#filamentos-total")
