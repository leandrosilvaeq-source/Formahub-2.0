"""Tela Estoque em navegador real (Edge ou Chrome, sem interface), nas larguras do projeto.

Inclui a janela Registrar Compra. Reaproveita o servidor e o navegador de test_navegador.py;
só dados fictícios em memória.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.estoque_repositorio import get_repositorio_estoque
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
        estoque.com_filamento(
            cor,
            material,
            tipo,
            marca,
            peso_g=Decimal("1234567.89"),
            data_compra=date(2026, 9, 1),
            peso_rolo_g=Decimal("1"),
            valor_unitario=Decimal("1234.56"),
        )
    for categoria, nome in (
        ("acessorio", "Argola de chaveiro inox 25 mm"),
        ("embalagem", "Caixa kraft 10x10x10"),
    ):
        estoque.com_item(nome, categoria, 999999, date(2026, 9, 2), Decimal("12499987.50"))
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


def tecla(p, key, code=None, codigo=0, shift=False):
    dados = {"key": key, "code": code or key, "windowsVirtualKeyCode": codigo}
    if shift:
        dados["modifiers"] = 8
    p.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **dados)
    p.cmd("Input.dispatchKeyEvent", type="keyUp", **dados)


def abrir_janela(p, categoria="filamento"):
    p.clicar(f'[data-abrir-compra="{categoria}"]')
    nav.esperar(lambda: p.js("document.getElementById('compra-janela').open"))


def linha(n: int, campo: str) -> str:
    return f'#compra-itens > .compra-item:nth-child({n}) [data-campo="{campo}"]'


def calculo(p, n: int, nome: str) -> str:
    return p.texto(f'#compra-itens > .compra-item:nth-child({n}) [data-calculo="{nome}"]')


def preencher(p, campos: dict):
    for seletor, valor in campos.items():
        p.digitar(seletor, valor)


def escolher_local(p, codigo):
    p.clicar(f'#campo-local input[value="{codigo}"]')


def preencher_filamento(p, n, qtd="3", peso="1000", valor="8990", cor="Preto"):
    preencher(
        p,
        {
            linha(n, "quantidade"): qtd,
            linha(n, "cor"): cor,
            linha(n, "material"): "PLA",
            linha(n, "marca"): "Marca Teste",
            linha(n, "peso_rolo"): peso,
            linha(n, "valor_unitario"): valor,
        },
    )
    p.js(
        f"(() => {{ const s = document.querySelector('{linha(n, 'tipo')}'); s.value = 'silk';"
        " s.dispatchEvent(new Event('change', {bubbles: true})) })()"
    )


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


# ---------- Lista ----------


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
    assert pagina.js("document.querySelectorAll('[data-abrir-compra]').length") == 3


@pytest.mark.parametrize("largura", LARGURAS)
def test_tabela_no_desktop_e_cartoes_no_celular(pagina, com_lotes, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    cabecalho = "getComputedStyle(document.querySelector('.estoque-tabela-{} thead')).position"
    assert (pagina.js(cabecalho.format("filamentos")) == "static") == (largura >= 1024)
    assert (pagina.js(cabecalho.format("itens")) == "static") == (largura >= 760)
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
             .filter(el => el.type !== 'hidden' && el.getClientRects().length
                     && el.getBoundingClientRect().height < 44)
             .map(el => el.textContent.trim() || el.name)"""
    )
    assert pequenos == []


@pytest.mark.parametrize("largura", LARGURAS)
def test_estado_vazio(pagina, estoque, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/estoque")

    assert sem_rolagem_horizontal(pagina)
    assert pagina.js("document.querySelectorAll('.estoque-vazio').length") == 3


def test_filtro_pelo_navegador_mantem_as_outras_areas(pagina, com_lotes):
    pagina.tela(1280, 900)
    pagina.abrir("/estoque?a_busca=argola")
    pagina.js("document.getElementById('f_tipo').value = 'silk'")
    pagina.js("document.querySelector('#filamentos form button[type=submit]').click()")
    nav.esperar(lambda: pagina.js("location.search.includes('f_tipo=silk')"))
    nav.esperar(lambda: pagina.js("document.readyState === 'complete'"))

    assert "a_busca=argola" in pagina.js("location.search")
    assert pagina.js("document.querySelectorAll('#filamentos tbody tr').length") == 1
    assert "Total filtrado" in pagina.texto("#filamentos-total")


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
    pagina.abrir("/estoque")
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
    # O campo Foto de cada linha mantém o rótulo visível em todas as larguras.
    assert pagina.js(
        "[...document.querySelectorAll('.compra-item-foto .item-rotulo')]"
        ".every(r => r.getClientRects().length > 0)"
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
    pagina.abrir("/estoque")
    abrir_janela(pagina, "acessorio")

    assert pagina.js("document.activeElement.id") == "compra-data"
    assert pagina.js("document.querySelectorAll('#compra-itens > .compra-item').length") == 1
    assert pagina.texto(".compra-item-titulo") == "1. Acessório"
    chave = pagina.valor('[name="chave_envio"]')
    assert len(chave) == 36 and chave[14] == "4"

    preencher(pagina, {"#compra-data": "10092026"})
    preencher_item(pagina, 1)
    pagina.clicar(".compra-rodape [data-cancelar]")

    assert not pagina.js("document.getElementById('compra-janela').open")
    # O foco volta ao botão que abriu a janela (o evento close chega logo depois).
    nav.esperar(lambda: pagina.js("document.activeElement.dataset.abrirCompra") == "acessorio")
    assert not estoque.compras and not estoque.itens
    # Reabrir começa do zero, com outra chave de submissão.
    abrir_janela(pagina, "embalagem")
    assert pagina.valor("#compra-data") == ""
    assert pagina.js("document.querySelectorAll('#compra-itens > .compra-item').length") == 1
    assert pagina.texto(".compra-item-titulo") == "1. Embalagem"
    assert pagina.valor('[name="chave_envio"]') != chave

    tecla(pagina, "Escape", codigo=27)  # Esc também cancela
    nav.esperar(lambda: not pagina.js("document.getElementById('compra-janela').open"))
    assert not estoque.compras


def test_data_com_mascara_e_local_por_teclado(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/estoque")
    abrir_janela(pagina)

    preencher(pagina, {"#compra-data": "1a0/09-2026999"})
    assert pagina.valor("#compra-data") == "10/09/2026"

    # Botões de seleção única: Tab chega ao grupo e as setas escolhem.
    pagina.js("document.querySelector('#campo-local input[value=mercado_livre]').focus()")
    tecla(pagina, " ", "Space", 32)
    assert (
        pagina.js("document.querySelector('#campo-local input:checked').value") == "mercado_livre"
    )
    for _ in range(3):
        tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "outro"
    assert pagina.js("document.activeElement.matches(':focus-visible')")
    assert not pagina.js("document.getElementById('campo-local_nome').hidden")

    pagina.digitar("#compra-local-nome", "Feira")
    pagina.js("document.querySelector('#campo-local input:checked').focus()")
    tecla(pagina, "ArrowRight", codigo=39)
    assert pagina.js("document.querySelector('#campo-local input:checked').value") == "loja_fisica"
    assert pagina.valor("#compra-local-nome") == "Feira"  # entre Outro e Loja Física, fica

    escolher_local(pagina, "shopee")
    assert pagina.js("document.getElementById('campo-local_nome').hidden")
    assert pagina.valor("#compra-local-nome") == ""  # plataforma: o texto é limpo


def test_linhas_calculos_e_remocao(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/estoque")
    abrir_janela(pagina, "filamento")
    pagina.clicar('[data-adicionar="filamento"]')
    pagina.clicar('[data-adicionar="acessorio"]')
    pagina.clicar('[data-adicionar="embalagem"]')

    titulos = pagina.js(
        "[...document.querySelectorAll('.compra-item-titulo')].map(t => t.textContent)"
    )
    assert titulos == ["1. Filamento", "2. Filamento", "3. Acessório", "4. Embalagem"]
    # Adicionar leva o foco ao primeiro campo da nova linha.
    assert pagina.js(f"document.activeElement === document.querySelector('{linha(4, 'nome')}')")
    # Rótulos com unidade, ligados aos campos.
    rotulos = pagina.js(
        "[...document.querySelectorAll('#compra-itens > .compra-item:first-child label')]"
        ".map(l => l.control && l.textContent.replace('*', '').replace(/\s+/g, ' ').trim())"
    )
    assert rotulos == [
        "Quantidade de rolos",
        "Cor",
        "Material",
        "Tipo",
        "Marca",
        "Peso por rolo (g) (gramas de cada rolo)",
        "Valor unitário por rolo (R$)",
    ]

    preencher_filamento(pagina, 1, qtd="3", peso="750,5", valor="10000")
    assert pagina.valor(linha(1, "valor_unitario")) == "R$ 100,00"
    assert calculo(pagina, 1, "peso_total") == "2.251,5 g"
    assert calculo(pagina, 1, "valor_total") == "R$ 300,00"
    assert calculo(pagina, 1, "custo") == "R$ 133,24"
    preencher(pagina, {linha(2, "peso_rolo"): "1.000,555", linha(2, "quantidade"): "2x"})
    assert pagina.valor(linha(2, "peso_rolo")) == "1,00"  # ponto vira vírgula; 2 casas
    assert pagina.valor(linha(2, "quantidade")) == "2"
    preencher_item(pagina, 3, qtd="3", valor="1000")
    assert calculo(pagina, 3, "custo") == "R$ 3,33"

    # Remover a linha 2: as outras são renumeradas e o foco vai para a vizinha.
    pagina.clicar("#compra-itens > .compra-item:nth-child(2) .compra-item-remover")
    titulos = pagina.js(
        "[...document.querySelectorAll('.compra-item-titulo')].map(t => t.textContent)"
    )
    assert titulos == ["1. Filamento", "2. Acessório", "3. Embalagem"]
    assert (
        pagina.js("document.activeElement.getAttribute('aria-label')")
        == "Remover item 2 (Acessório)"
    )
    assert pagina.valor(linha(2, "nome")) == "Argola"  # dados das outras linhas continuam
    for _ in range(3):
        pagina.clicar("#compra-itens > .compra-item:first-child .compra-item-remover")
    assert not pagina.js("document.getElementById('compra-itens-vazio').hidden")
    assert pagina.js("document.activeElement.dataset.adicionar") == "filamento"
    assert not estoque.compras


def test_erros_do_servidor_nos_campos_preservando_o_que_foi_digitado(pagina, estoque):
    pagina.tela(360, 740)
    pagina.abrir("/estoque")
    abrir_janela(pagina, "filamento")
    pagina.clicar('[data-adicionar="embalagem"]')
    preencher_filamento(pagina, 1, cor="")
    preencher(pagina, {linha(2, "nome"): "Caixa", linha(2, "quantidade"): "3"})
    escolher_local(pagina, "outro")
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
    pagina.abrir("/estoque")
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
    pagina.abrir("/estoque")
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

    assert pagina.js("location.pathname + location.search") == "/estoque?salvo=compra"
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
    assert "3.000 g" in pagina.texto("#filamentos-total")
    assert "40 un" in pagina.texto("#acessorios-total")
    assert "3 un" in pagina.texto("#embalagens-total")


def test_duplo_clique_grava_uma_vez(pagina, estoque):
    pagina.tela(1280, 900)
    pagina.abrir("/estoque")
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
    pagina.abrir("/estoque")
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
