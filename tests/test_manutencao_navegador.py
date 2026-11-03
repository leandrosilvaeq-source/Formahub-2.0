"""Tela Manutenção (e os cards de Custos indiretos) em navegador real, só com dados em memória."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.custos_repositorio import GastoManutencao, PeriodoManutencao
from app.manutencao_repositorio import FalhaAoSalvarManutencao, Manutencao
from tests import test_navegador as nav

pytestmark = pytest.mark.navegador

servidor = nav.servidor
pagina = nav.pagina
esperar = nav.esperar
tecla = nav.tecla

HOJE = date.today().isoformat()


def registro(id=1, **extra):
    base = {
        "id": id,
        "data": date(2026, 10, 10),
        "tipo": "corretiva",
        "minutos_parados": 1590,  # 26h30min
        "troca_pecas": True,
        "pecas": "Bico, correia",
        "custo": Decimal("62.50"),
        "comentarios": "Trocado",
        "criado_por": "Leandro",
        "atualizado_por": "Leandro",
    }
    return Manutencao(**{**base, **extra})


def abrir(p, repo, *registros, largura=1280):
    repo.manutencoes = list(registros)
    repo.proximo_id = max([r.id for r in registros], default=0) + 1
    p.tela(largura, 900)
    p.abrir("/manutencao")
    p.js("window.__marca = 'mesma-pagina'")


def texto(p, seletor):
    return nav.custos_texto(p, seletor)


@pytest.mark.parametrize("largura", [1280, 1600])
def test_cards_titulos_e_valores_alinhados_na_horizontal(pagina, largura):
    pagina.tela(largura, 900)
    pagina.abrir("/manutencao")  # sem período: o card do custo por hora é o mais alto

    for seletor in (".manut-indicador", ".manut-indicador-rotulo", ".manut-indicador-valor"):
        topos = pagina.js(
            f"[...document.querySelectorAll('{seletor}')]"
            ".map(e => Math.round(e.getBoundingClientRect().top))"
        )
        assert len(topos) == 4 and len(set(topos)) == 1, (seletor, topos)


@pytest.mark.parametrize("largura", [360, 1280])
def test_valor_no_centro_do_card_e_fonte_maior(pagina, repo_custos, largura):
    repo_custos.periodo = PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal("902.4"))
    repo_custos.gastos = [GastoManutencao(1, date(2026, 10, 29), "Peça", Decimal("107.62"))]
    pagina.tela(largura, 900)
    pagina.abrir("/manutencao")

    desvios = pagina.js(
        "[...document.querySelectorAll('.manut-indicador')].map(c => {"
        " const a = c.getBoundingClientRect();"
        " const v = c.querySelector('.manut-indicador-valor');"
        " const faixa = document.createRange(); faixa.selectNodeContents(v);"
        " const b = faixa.getBoundingClientRect();"
        " const linha = v.getBoundingClientRect();"
        " return [Math.abs((a.left + a.right - b.left - b.right) / 2),"
        " Math.abs((a.top + a.bottom - linha.top - linha.bottom) / 2)] })"
    )
    assert all(x <= 1 and y <= 1 for x, y in desvios), desvios
    assert pagina.js(
        "['.manut-indicador-rotulo', '.manut-indicador-valor']"
        ".map(s => getComputedStyle(document.querySelector(s)).fontSize)"
    ) == ["16px", "30px"]
    assert pagina.js("document.documentElement.scrollWidth") <= largura


@pytest.mark.parametrize(("largura", "por_linha"), [(360, 1), (768, 2), (1280, 4), (1600, 4)])
def test_cards_com_a_mesma_largura_e_altura(pagina, repo_custos, largura, por_linha):
    tamanhos = (
        "[...document.querySelectorAll('.manut-indicador')].map(e => {"
        " const r = e.getBoundingClientRect();"
        " return [Math.round(r.width), Math.round(r.height), Math.round(r.top)] })"
    )
    pagina.tela(largura, 900)
    pagina.abrir("/manutencao")  # sem período: o conteúdo do último card é o mais longo
    medidas = pagina.js(tamanhos)

    assert len({m[0] for m in medidas}) == 1 and len({m[1] for m in medidas}) == 1, medidas
    assert len({m[2] for m in medidas}) == 4 // por_linha, medidas
    assert pagina.js("document.documentElement.scrollWidth") <= largura

    pagina.js("document.querySelector('#manut-custo-hora details').open = true")
    medidas = pagina.js(tamanhos)
    assert len({m[0] for m in medidas}) == 1 and len({m[1] for m in medidas}) == 1, medidas


def test_abrir_detalhes_nao_dobra_a_altura_dos_cards(pagina, repo_custos):
    repo_custos.periodo = PeriodoManutencao(date(2026, 8, 3), date(2026, 11, 1), Decimal("902.4"))
    repo_custos.gastos = [GastoManutencao(1, date(2026, 10, 29), "Peça", Decimal("107.62"))]
    pagina.tela(1280, 900)
    pagina.abrir("/manutencao")
    valores = (
        "[...document.querySelectorAll('.manut-indicador-valor')]"
        ".map(e => Math.round(e.getBoundingClientRect().top))"
    )
    antes = pagina.js(valores)

    pagina.js("document.querySelector('#manut-custo-hora details').open = true")

    assert pagina.js(valores) == antes  # os valores não saem do lugar


def test_alerta_de_lubrificacao_na_pagina_inicial_cabe_no_celular(pagina):
    pagina.tela(360, 800)
    pagina.abrir("/")

    assert pagina.js("!!document.querySelector('#manut-alerta-lubrificacao a')")
    assert pagina.js("document.documentElement.scrollWidth") <= 360


def existe(p, seletor):
    return p.js(f"!!document.querySelector({json.dumps(seletor)})")


def definir(p, seletor, valor):
    """Coloca o valor num select ou campo (com evento de mudança), sem digitar tecla a tecla."""
    p.js(
        f"(c => {{ c.value = {json.dumps(valor)};"
        " c.dispatchEvent(new Event('input', {bubbles: true}));"
        " c.dispatchEvent(new Event('change', {bubbles: true})) })"
        f"(document.querySelector({json.dumps(seletor)}))"
    )


def preencher_nova(p, **campos):
    for nome, valor in campos.items():
        seletor = f"#manut-nova [name={nome}]"
        if nome == "tipo":
            definir(p, seletor, valor)
        elif nome == "data":
            p.escolher_data(seletor, valor)
        else:
            nav.preencher(p, seletor, valor)


def enter(p, ctrl=False):
    dados = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}
    if ctrl:
        dados["modifiers"] = 2
    p.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **dados)
    p.cmd("Input.dispatchKeyEvent", type="keyUp", **dados)


def duplo_clique(p, seletor):
    p.js(
        f"document.querySelector({json.dumps(seletor)})"
        ".dispatchEvent(new MouseEvent('dblclick', {bubbles: true, cancelable: true}))"
    )


def celula(id, campo):
    return f"#manut-{id} td[data-campo={campo}]"


def abrir_nova(p):
    p.clicar("#manut-adicionar")
    esperar(lambda: existe(p, "#manut-nova"))


# ---------- Linha nova ----------


def test_registrar_manutencao_cria_linha_editavel_e_salva_sem_recarregar(
    pagina, servidor, repo_manutencao
):
    abrir(pagina, repo_manutencao)
    assert "Registrar manutenção" in texto(pagina, "#manut-adicionar")
    assert "Nenhuma manutenção registrada" in texto(pagina, "#manut-tabela")

    abrir_nova(pagina)
    assert pagina.js("location.search") == "" and pagina.js("window.__marca") == "mesma-pagina"
    assert pagina.valor("#manut-nova [name=data]") == HOJE  # data de hoje, completa
    assert pagina.js("document.activeElement.name") == "data"  # foco no primeiro campo
    assert not existe(pagina, "#manut-nova [name=troca_pecas]")  # coluna única de peça
    assert not existe(pagina, ".manut-vazio:not([hidden])")

    preencher_nova(
        pagina,
        data="10102026",
        tipo="corretiva",
        tempo="1d02h30min",
        pecas="Bico, correia GT2",
        comentarios="Sexta",
    )
    pagina.js("document.querySelector('#manut-nova [name=custo]').focus()")
    pagina.digitar("#manut-nova [name=custo]", "6250")  # máscara bancária
    assert pagina.valor("#manut-nova [name=custo]") == "R$ 62,50"

    enter(pagina)
    esperar(lambda: not existe(pagina, "#manut-nova"), mensagem="não salvou")

    assert pagina.js("window.__marca") == "mesma-pagina"  # sem recarregar
    ((_, valores, usuario),) = repo_manutencao.gravacoes
    assert usuario == 1 and valores["minutos"] == 1590 and valores["custo"] == Decimal("62.50")
    assert valores["pecas"] == "Bico, correia GT2" and valores["troca_pecas"] is True
    assert valores["data"] == date(2026, 10, 10)
    linha = texto(pagina, "#manut-1")
    assert "10/10/26" in linha and "Corretiva" in linha and "26h30min" in linha
    assert "Bico, correia GT2" in linha and "R$ 62,50" in linha
    assert "Manutenção salva." in pagina.texto("#manut-mensagem")
    indicadores = texto(pagina, "#manut-indicadores")
    assert "Manutenções 1" in indicadores and "Horas paradas 26h30min" in indicadores
    assert "R$ 62,50" in indicadores and "subtotal conhecido" not in indicadores
    assert pagina.js("document.activeElement.className").startswith("custos-lapis")


def test_editores_da_linha_se_integram_as_celulas(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao)
    abrir_nova(pagina)

    estilos = pagina.js(
        "['data', 'tipo', 'tempo', 'pecas', 'custo', 'comentarios'].map(n => {"
        " const c = document.querySelector('#manut-nova [name=' + n + ']');"
        " const e = getComputedStyle(c);"
        " return [n, e.borderTopWidth, e.backgroundColor, c.getBoundingClientRect().height] })"
    )
    for nome, borda, fundo, altura in estilos:
        assert borda == "0px", nome  # sem borda permanente
        assert fundo == "rgba(0, 0, 0, 0)", nome  # sem caixa branca: vale o fundo da tabela
        assert altura >= 44, nome
    # Foco: indicação discreta (traço na base), não um contorno grosso.
    pagina.js("document.querySelector('#manut-nova [name=tempo]').focus()")
    sombra = pagina.js(
        "getComputedStyle(document.querySelector('#manut-nova [name=tempo]')).boxShadow"
    )
    assert sombra != "none" and "inset" in sombra


def test_ordem_de_tab_segue_as_colunas(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao)
    abrir_nova(pagina)

    ordem = []
    for _ in range(16):  # a data tem três partes (dia, mês, ano) que também recebem o Tab
        atual = pagina.js("document.activeElement.name || document.activeElement.className")
        if not ordem or ordem[-1] != atual:
            ordem.append(atual)
        tecla(pagina, "Tab", "Tab", 9)

    assert ordem[:6] == ["data", "tipo", "tempo", "pecas", "custo", "comentarios"]
    assert ordem[6].startswith("custos-fil-botao")  # depois dos campos, confirmar


def test_erro_preserva_o_preenchimento_e_nao_grava(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao)
    abrir_nova(pagina)
    preencher_nova(pagina, tempo="02:75", pecas="Correia", comentarios="Anotação")
    pagina.js("document.querySelector('#manut-nova [name=custo]').focus()")
    pagina.digitar("#manut-nova [name=custo]", "1230")

    enter(pagina)  # tipo faltando e minutos acima de 59
    esperar(lambda: existe(pagina, "#manut-nova .manut-erro:not([hidden])"))

    assert repo_manutencao.gravacoes == [] and pagina.js("window.__marca") == "mesma-pagina"
    assert "Escolha Preventiva ou Corretiva." in texto(pagina, "#manut-nova")
    assert "minutos devem estar entre 0 e 59" in texto(pagina, "#manut-nova")
    assert pagina.js("document.querySelector('#manut-nova [name=tempo]').ariaInvalid") == "true"
    assert pagina.valor("#manut-nova [name=tempo]") == "02:75"  # nada é corrigido sozinho
    assert pagina.valor("#manut-nova [name=custo]") == "R$ 12,30"
    assert pagina.valor("#manut-nova [name=pecas]") == "Correia"
    assert pagina.valor("#manut-nova [name=comentarios]") == "Anotação"
    assert not pagina.js("document.querySelector('#manut-nova [name=tipo]').disabled")

    # Corrigir e salvar: agora grava, uma única vez (mesmo com envio duplicado).
    preencher_nova(pagina, tipo="preventiva", tempo="00:45")
    pagina.js("document.querySelector('#manut-nova form').requestSubmit()")
    pagina.js("document.querySelector('#manut-nova form')?.requestSubmit()")
    esperar(lambda: not existe(pagina, "#manut-nova"))
    assert len(repo_manutencao.gravacoes) == 1
    assert repo_manutencao.manutencoes[0].minutos_parados == 45


def test_linha_nova_nao_grava_sem_confirmar(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao)
    abrir_nova(pagina)
    preencher_nova(pagina, tipo="corretiva", tempo="01:00")
    pagina.js("document.querySelector('#manut-nova [name=custo]').focus()")
    pagina.js("document.activeElement.blur()")  # sair do campo não grava

    assert repo_manutencao.gravacoes == [] and existe(pagina, "#manut-nova")


def test_falha_do_servidor_mantem_a_linha_e_permite_tentar_de_novo(
    pagina, servidor, repo_manutencao
):
    abrir(pagina, repo_manutencao)
    repo_manutencao.falhar_em["salvar"] = FalhaAoSalvarManutencao()
    abrir_nova(pagina)
    preencher_nova(pagina, tipo="corretiva", tempo="01:00", comentarios="Importante")

    pagina.clicar("#manut-nova button[type=submit]")
    esperar(lambda: "Não foi possível salvar agora" in texto(pagina, "#manut-nova"))
    assert pagina.valor("#manut-nova [name=comentarios]") == "Importante"
    assert repo_manutencao.manutencoes == []

    del repo_manutencao.falhar_em["salvar"]
    pagina.clicar("#manut-nova button[type=submit]")
    esperar(lambda: not existe(pagina, "#manut-nova"))
    assert len(repo_manutencao.manutencoes) == 1


def test_esc_cancela_a_nova_linha_sem_gravar(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao)
    abrir_nova(pagina)
    tecla(pagina, "Escape", "Escape", 27)

    assert not existe(pagina, "#manut-nova")
    assert not pagina.js("document.querySelector('.manut-vazio').hidden")
    assert repo_manutencao.gravacoes == []


# ---------- Edição de uma célula ----------


def test_duplo_clique_edita_so_aquele_campo_e_preserva_os_demais(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1))

    duplo_clique(pagina, celula(1, "tempo"))
    esperar(lambda: existe(pagina, celula(1, "tempo") + " input"))
    assert not existe(pagina, "#manut-1.manut-editando")  # a linha inteira não abre
    assert (
        pagina.js(
            "document.querySelectorAll('#manut-1 input, #manut-1 select, #manut-1 textarea').length"
        )
        == 1
    )
    assert pagina.valor(celula(1, "tempo") + " input") == "26h30min"
    assert pagina.js("document.activeElement.value") == "26h30min"

    pagina.digitar(celula(1, "tempo") + " input", "27:15")  # substitui (texto selecionado)
    esperar(lambda: pagina.valor(celula(1, "tempo") + " input") == "27:15")
    enter(pagina)
    esperar(lambda: repo_manutencao.manutencoes[0].minutos_parados == 1635)
    esperar(lambda: "27h15min" in texto(pagina, "#manut-1"))

    salva = repo_manutencao.manutencoes[0]
    assert salva.minutos_parados == 1635
    assert (salva.tipo, salva.pecas, salva.custo, salva.comentarios) == (
        "corretiva",
        "Bico, correia",
        Decimal("62.50"),
        "Trocado",
    )
    assert salva.data == date(2026, 10, 10) and pagina.js("window.__marca") == "mesma-pagina"
    esperar(lambda: "Tempo de máquina parada salvo." in pagina.texto("#manut-mensagem"))
    assert "Horas paradas 27h15min" in texto(pagina, "#manut-indicadores")


def test_esc_cancela_a_celula_e_enter_com_erro_mantem_o_digitado(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1))

    duplo_clique(pagina, celula(1, "tempo"))
    esperar(lambda: existe(pagina, celula(1, "tempo") + " input"))
    pagina.digitar(celula(1, "tempo") + " input", "02:75")
    enter(pagina)
    esperar(lambda: "minutos devem estar entre 0 e 59" in texto(pagina, celula(1, "tempo")))
    assert pagina.valor(celula(1, "tempo") + " input") == "02:75"
    assert repo_manutencao.gravacoes == []

    tecla(pagina, "Escape", "Escape", 27)
    assert "26h30min" in texto(pagina, "#manut-1") and not existe(pagina, "#manut-1 input")
    assert repo_manutencao.gravacoes == []


def test_comentarios_aceitam_quebra_de_linha_e_ctrl_enter_confirma(
    pagina, servidor, repo_manutencao
):
    abrir(pagina, repo_manutencao, registro(1))

    duplo_clique(pagina, celula(1, "comentarios"))
    esperar(lambda: existe(pagina, celula(1, "comentarios") + " textarea"))
    pagina.digitar(celula(1, "comentarios") + " textarea", "Linha 1")
    tecla(pagina, "Enter", "Enter", 13, chr(13))  # Enter simples quebra a linha
    pagina.cmd("Input.insertText", text="Linha 2")
    assert repo_manutencao.gravacoes == []
    assert pagina.valor(celula(1, "comentarios") + " textarea") == "Linha 1\nLinha 2"
    enter(pagina, ctrl=True)
    esperar(lambda: len(repo_manutencao.gravacoes) == 1)

    assert repo_manutencao.manutencoes[0].comentarios.replace("\r", "") == "Linha 1\nLinha 2"
    esperar(lambda: not existe(pagina, "#manut-1 textarea"))
    assert (
        pagina.js(
            "getComputedStyle(document.querySelector('#manut-1 .manut-comentario')).whiteSpace"
        )
        == "pre-line"
    )


def test_celulas_editam_pelo_teclado_e_tab_navega(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1))

    pagina.js(f"document.querySelector({json.dumps(celula(1, 'tempo'))}).focus()")
    enter(pagina)  # equivalente do duplo clique, para quem usa teclado
    esperar(lambda: existe(pagina, celula(1, "tempo") + " input"))
    pagina.digitar(celula(1, "tempo") + " input", "00:10")
    tecla(pagina, "Tab", "Tab", 9)  # confirma o que mudou e vai para a célula seguinte
    esperar(lambda: "00h10min" in texto(pagina, "#manut-1"))

    assert repo_manutencao.manutencoes[0].minutos_parados == 10
    esperar(lambda: pagina.js("document.activeElement.dataset.campo") == "pecas")

    # Sem mudança, Tab só navega (nada é gravado).
    enter(pagina)
    esperar(lambda: existe(pagina, celula(1, "pecas") + " input"))
    tecla(pagina, "Tab", "Tab", 9)
    esperar(lambda: pagina.js("document.activeElement.dataset.campo") == "custo")
    assert len(repo_manutencao.gravacoes) == 1


def test_peca_substituida_vazia_e_sem_troca(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1))
    assert "Bico, correia" in texto(pagina, celula(1, "pecas"))

    duplo_clique(pagina, celula(1, "pecas"))
    esperar(lambda: existe(pagina, celula(1, "pecas") + " input"))
    pagina.js("document.activeElement.value = ''")
    enter(pagina)
    esperar(lambda: "Sem troca" in texto(pagina, celula(1, "pecas")))
    assert repo_manutencao.manutencoes[0].pecas is None
    assert repo_manutencao.manutencoes[0].troca_pecas is False


def test_tipo_muda_pela_lista_na_propria_celula(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1))

    duplo_clique(pagina, celula(1, "tipo"))
    esperar(lambda: existe(pagina, celula(1, "tipo") + " select"))
    definir(pagina, celula(1, "tipo") + " select", "preventiva")
    esperar(lambda: repo_manutencao.manutencoes[0].tipo == "preventiva")
    esperar(lambda: not existe(pagina, celula(1, "tipo") + " select"))

    assert repo_manutencao.manutencoes[0].tipo == "preventiva"


def test_custo_na_celula_vazio_e_a_definir_e_zero_e_diferente(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1))

    duplo_clique(pagina, celula(1, "custo"))
    esperar(lambda: existe(pagina, celula(1, "custo") + " input"))
    assert pagina.valor(celula(1, "custo") + " input") == "R$ 62,50"
    pagina.js("document.activeElement.focus()")
    pagina.digitar(celula(1, "custo") + " input", "0")
    assert pagina.valor(celula(1, "custo") + " input") == "R$ 0,00"
    enter(pagina)
    esperar(lambda: "R$ 0,00" in texto(pagina, celula(1, "custo")))
    assert repo_manutencao.manutencoes[0].custo == Decimal("0")
    assert "subtotal conhecido" not in texto(pagina, "#manut-indicadores")

    duplo_clique(pagina, celula(1, "custo"))
    esperar(lambda: existe(pagina, celula(1, "custo") + " input"))
    pagina.apagar(8)
    assert pagina.valor(celula(1, "custo") + " input") == ""
    enter(pagina)
    esperar(lambda: "A definir" in texto(pagina, celula(1, "custo")))
    assert repo_manutencao.manutencoes[0].custo is None
    assert "subtotal conhecido A definir" in texto(pagina, "#manut-indicadores")


def test_so_uma_edicao_aberta_por_vez(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1), registro(2, data=date(2026, 9, 1)))

    duplo_clique(pagina, celula(1, "tempo"))
    esperar(lambda: existe(pagina, celula(1, "tempo") + " input"))
    duplo_clique(pagina, celula(2, "pecas"))

    assert not existe(pagina, celula(2, "pecas") + " input")
    assert "Termine ou cancele" in pagina.texto("#manut-mensagem")


def test_registro_antigo_incompleto_diz_o_que_falta_ao_editar_um_campo(
    pagina, servidor, repo_manutencao
):
    abrir(
        pagina,
        repo_manutencao,
        registro(1, tipo=None, minutos_parados=None, troca_pecas=False, pecas=None, legado=True),
    )
    assert "Não informado" in texto(pagina, "#manut-1")

    duplo_clique(pagina, celula(1, "comentarios"))
    esperar(lambda: existe(pagina, celula(1, "comentarios") + " textarea"))
    pagina.digitar(celula(1, "comentarios") + " textarea", "Revisado")
    enter(pagina, ctrl=True)
    esperar(lambda: "Escolha Preventiva ou Corretiva" in texto(pagina, celula(1, "comentarios")))
    assert repo_manutencao.gravacoes == []
    assert pagina.valor(celula(1, "comentarios") + " textarea") == "Revisado"


# ---------- Linha inteira (lápis) ----------


def test_lapis_abre_a_linha_inteira_com_o_tempo_no_formato_de_horas(
    pagina, servidor, repo_manutencao
):
    abrir(pagina, repo_manutencao, registro(1), registro(2, data=date(2026, 9, 1), custo=None))

    pagina.clicar("#manut-1 .manut-editar")
    esperar(lambda: existe(pagina, "#manut-1.manut-editando"))
    assert pagina.valor("#manut-1 [name=tempo]") == "26h30min"
    assert pagina.valor("#manut-1 [name=custo]") == "R$ 62,50"
    assert pagina.valor("#manut-1 [name=pecas]") == "Bico, correia"
    assert pagina.js("window.__marca") == "mesma-pagina"

    pagina.js("document.querySelector('#manut-2 .manut-editar').click()")
    assert not existe(pagina, "#manut-2.manut-editando")  # um editor por vez

    tecla(pagina, "Escape", "Escape", 27)  # Esc devolve a linha original
    esperar(lambda: not existe(pagina, "#manut-1.manut-editando"))
    assert "26h30min" in texto(pagina, "#manut-1") and repo_manutencao.gravacoes == []

    pagina.clicar("#manut-1 .manut-editar")
    esperar(lambda: existe(pagina, "#manut-1.manut-editando"))
    nav.preencher(pagina, "#manut-1 [name=tempo]", "02d05h")
    enter(pagina)
    esperar(lambda: "53h00min" in texto(pagina, "#manut-1"))
    assert repo_manutencao.manutencoes[0].minutos_parados == 3180


def test_nova_linha_entra_na_ordem_por_data(pagina, servidor, repo_manutencao):
    abrir(
        pagina,
        repo_manutencao,
        registro(1, data=date(2026, 10, 10)),
        registro(2, data=date(2026, 8, 1)),
    )
    abrir_nova(pagina)
    preencher_nova(pagina, data="01092026", tipo="preventiva", tempo="00:30")
    enter(pagina)
    esperar(lambda: not existe(pagina, "#manut-nova"))

    ids = pagina.js(
        "[...document.querySelectorAll('#manut-tabela tbody tr[data-id]')].map(l => l.dataset.id)"
    )
    assert ids == ["1", "3", "2"]  # 10/10, 01/09, 01/08


# ---------- Layout ----------


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_layout_sem_rolagem_horizontal_na_pagina(pagina, servidor, repo_manutencao, largura):
    abrir(
        pagina,
        repo_manutencao,
        registro(1),
        registro(2, custo=None, comentarios=None),
        largura=largura,
    )
    assert pagina.js("document.documentElement.scrollWidth <= window.innerWidth")
    pagina.clicar("#manut-1 .manut-editar")
    esperar(lambda: existe(pagina, "#manut-1.manut-editando"))
    assert pagina.js("document.documentElement.scrollWidth <= window.innerWidth")
    tecla(pagina, "Escape", "Escape", 27)
    duplo_clique(pagina, celula(2, "comentarios"))
    assert pagina.js("document.documentElement.scrollWidth <= window.innerWidth")


def test_celular_edicao_com_rotulos_visiveis_e_controles_de_44px(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1), largura=360)
    abrir_nova(pagina)

    rotulos = pagina.js(
        "[...document.querySelectorAll('#manut-nova label')].filter(l => l.getClientRects().length)"
        ".map(l => l.textContent.trim())"
    )
    assert {
        "Data",
        "Tipo de manutenção",
        "Tempo de máquina parada",
        "Peça substituída",
        "Custo (R$)",
        "Comentários",
    } <= set(rotulos)
    baixos = pagina.js(
        "[...document.querySelectorAll('#manut-nova input, #manut-nova select,"
        " #manut-nova textarea, #manut-nova button, #manut-nova a')]"
        ".filter(e => e.getClientRects().length"
        " && e.type !== 'hidden' && !e.disabled)"
        ".filter(e => e.getBoundingClientRect().height < 43.5).map(e => e.name || e.className)"
    )
    assert baixos == []
    # Linhas de leitura viram blocos com rótulo na frente do valor.
    rotulo = pagina.js(
        "getComputedStyle(document.querySelector('#manut-1 td:nth-child(3)'), '::before').content"
    )
    assert "Tempo de máquina parada" in rotulo
    assert (
        pagina.js("getComputedStyle(document.querySelector('#manut-tabela thead')).position")
        == "absolute"
    )


def test_celular_toque_na_celula_abre_o_editor(pagina, servidor, repo_manutencao):
    abrir(pagina, repo_manutencao, registro(1), largura=360)
    pagina.cmd("Emulation.setEmitTouchEventsForMouse", enabled=True, configuration="mobile")
    try:
        if not pagina.js("matchMedia('(pointer: coarse)').matches"):
            pytest.skip("o navegador de teste não emulou ponteiro de toque")
        pagina.js(f"document.querySelector({json.dumps(celula(1, 'pecas'))}).click()")
        esperar(lambda: existe(pagina, celula(1, "pecas") + " input"))
        assert not existe(pagina, "#manut-1.manut-editando")
    finally:
        pagina.cmd("Emulation.setEmitTouchEventsForMouse", enabled=False)


def test_sessao_expirada_guarda_o_preenchimento_para_depois_do_login(
    pagina, servidor, repo_manutencao
):
    abrir(pagina, repo_manutencao)
    pagina.js(
        "sessionStorage.setItem('manutencao-pendente', JSON.stringify({alvo: 'nova',"
        " em: Date.now(), valores: {data: '2026-10-10', tipo: 'corretiva',"
        " comentarios: 'Não perder', custo: '12,30', tempo: '01:00', pecas: ''}}))"
    )
    pagina.abrir("/manutencao")

    esperar(lambda: existe(pagina, "#manut-nova"))
    assert pagina.valor("#manut-nova [name=comentarios]") == "Não perder"
    assert pagina.valor("#manut-nova [name=custo]") == "R$ 12,30"
    assert pagina.valor("#manut-nova [name=tempo]") == "01:00"
    assert "Dados recuperados depois do login" in texto(pagina, "#manut-nova")
    assert repo_manutencao.gravacoes == []  # nunca salva sozinho


# ---------- Custos: cards de Depreciação e Manutenção ----------


def test_uso_de_maquina_acompanha_a_edicao_e_abre_os_detalhes(pagina, servidor, repo_custos):
    nav.estoque_de_custos()
    repo_custos.parametros["manutencao_hora"] = Decimal("0.12")
    pagina.tela(1280, 900)
    pagina.abrir("/custos")
    pagina.js("window.__marca = 'mesma-pagina'")
    assert "R$ 0,53" in texto(pagina, "#uso-maquina")
    assert not pagina.js("document.querySelector('#uso-maquina details').open")
    assert "Depreciação gerencial" not in texto(pagina, "#uso-maquina")  # só em "Ver detalhes"

    pagina.clicar("#uso-maquina summary")
    assert "Depreciação gerencial R$ 0,27/h" in texto(pagina, "#uso-maquina")

    pagina.clicar("#depreciacao .custos-lapis")
    pagina.digitar("#depreciacao-inline", "30")
    enter(pagina)

    esperar(lambda: "R$ 0,56" in texto(pagina, "#uso-maquina"))
    assert "Depreciação gerencial R$ 0,30/h" in texto(pagina, "#uso-maquina")  # segue aberto
    assert pagina.js("window.__marca") == "mesma-pagina"

    pagina.js("document.querySelector('#depreciacao .custos-voltar-valor button').click()")
    esperar(lambda: "≈ R$ 0,2732/h" in nav.texto_completo(pagina, "#uso-maquina"))


def test_custos_indiretos_editam_no_lugar_e_voltam_ao_calculado(pagina, servidor, repo_custos):
    nav.estoque_de_custos()
    pagina.tela(1280, 900)
    pagina.abrir("/custos")
    pagina.js("window.__marca = 'mesma-pagina'")
    assert "R$ 0,27/h" in texto(pagina, "#depreciacao")
    assert "Revisar custos: valor manual abaixo do custo calculado" in texto(pagina, "#depreciacao")
    assert "A definir" in texto(pagina, "#manutencao")

    pagina.clicar("#depreciacao .custos-lapis")
    assert pagina.valor("#depreciacao-inline") == "R$ 0,27"
    pagina.digitar("#depreciacao-inline", "30")
    enter(pagina)
    esperar(lambda: "R$ 0,30/h" in texto(pagina, "#depreciacao"))
    assert repo_custos.parametros["depreciacao_hora_manual"] == Decimal("0.30")
    assert "Acima do custo calculado" in texto(pagina, "#depreciacao")
    assert pagina.js("window.__marca") == "mesma-pagina"

    pagina.js("document.querySelector('#depreciacao .custos-voltar-valor button').click()")
    esperar(lambda: repo_custos.parametros["depreciacao_hora_manual"] is None)
    esperar(lambda: "R$ 0,2732/h" in nav.texto_completo(pagina, "#depreciacao"))
    assert pagina.js("window.__marca") == "mesma-pagina"

    pagina.clicar("#manutencao .custos-lapis")
    pagina.digitar("#manutencao-inline", "125")
    enter(pagina)
    esperar(lambda: "R$ 1,25/h" in texto(pagina, "#manutencao"))
    assert repo_custos.parametros["manutencao_hora"] == Decimal("1.25")
    assert 'href="/manutencao"' in pagina.js("document.querySelector('#manutencao').innerHTML")
