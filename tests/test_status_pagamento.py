"""Status do pagamento (Pendente/Pago): formulário, validação, gravação e telas de consulta.

Repositório e Storage falsos; nenhum teste acessa o Supabase.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_pedidos_consulta import novo_pedido
from tests.test_pedidos_gravacao import enviar, formulario, unico_pedido

client = TestClient(app)


def grupo_status(html):
    inicio = html.index('id="grupo-status_pagamento"')
    return html[html.rindex("<fieldset", 0, inicio) : html.index("</fieldset>", inicio)]


# ---------- Formulário ----------


def test_tela_tem_o_grupo_status_com_radios_nativos_sem_selecao():
    grupo = grupo_status(client.get("/pedidos/novo").text)

    assert '<legend>Status do pagamento <span class="obrigatorio">*</span></legend>' in grupo
    for opcao in ("Pendente", "Pago"):
        assert f'type="radio" name="status_pagamento" value="{opcao}"' in grupo
        assert f'<span class="escolha-titulo">{opcao}</span>' in grupo
    assert grupo.count('type="radio"') == 2  # seleção única: um só name
    assert " checked" not in grupo
    assert 'data-icone="pendente"' in grupo and 'data-icone="pago"' in grupo
    assert 'data-obrigatorio="Escolha o status do pagamento."' in grupo


def test_status_fica_ao_lado_da_forma_de_pagamento():
    html = client.get("/pedidos/novo").text
    grade = html.split('<div class="grade-escolhas">')[1].split('<div class="grade-final">')[0]

    assert grade.index('id="grupo-pagamento"') < grade.index('id="grupo-status_pagamento"')
    assert 'id="grupo-entrega"' not in grade
    final = html.split('<div class="grade-final">')[1]
    assert final.index("secao-obs") < final.index('id="grupo-entrega"') < final.index("resumo")


@pytest.mark.parametrize("status", [None, "", "Quitado", "pago", "PAGO", "pendente "])
def test_status_obrigatorio_e_somente_valores_da_lista(repo_pedidos, status):
    dados = formulario()
    if status is None:
        del dados["status_pagamento"]
    else:
        dados["status_pagamento"] = status

    resposta = enviar(dados)

    # Só "Pendente" ou "Pago", exatamente como na tela (o código do banco não é aceito).
    assert resposta.status_code == 422
    assert (
        '<p class="campo-erro" id="status_pagamento-erro">Escolha o status do pagamento.</p>'
        in resposta.text
    )
    assert repo_pedidos.pedidos == {}


@pytest.mark.parametrize(("opcao", "codigo"), [("Pendente", "pendente"), ("Pago", "pago")])
def test_pendente_e_pago_sao_gravados(repo_pedidos, opcao, codigo):
    resposta = enviar(formulario(status_pagamento=opcao))

    assert resposta.status_code == 200
    assert unico_pedido(repo_pedidos).status_pagamento == codigo


def test_status_preservado_quando_ha_erro(repo_pedidos):
    html = enviar(formulario(cliente="", status_pagamento="Pago")).text

    assert 'name="status_pagamento" value="Pago" checked' in html
    assert 'name="status_pagamento" value="Pendente" checked' not in html
    assert repo_pedidos.pedidos == {}


def test_status_limpo_depois_do_sucesso():
    html = enviar(formulario(status_pagamento="Pago")).text

    assert "salvo com sucesso" in html
    assert " checked" not in grupo_status(html)


def test_status_enviado_varias_vezes_usa_so_o_primeiro(repo_pedidos):
    # Radio nativo manda um valor só; se vierem vários, o servidor não aceita "o melhor".
    enviar(formulario(status_pagamento=["Pendente", "Pago"]))

    assert unico_pedido(repo_pedidos).status_pagamento == "pendente"


# ---------- Detalhes ----------


@pytest.mark.parametrize(("codigo", "rotulo"), [("pendente", "Pendente"), ("pago", "Pago")])
def test_detalhes_mostram_o_status(repo_pedidos, codigo, rotulo):
    pedido = novo_pedido(repo_pedidos, status_pagamento=codigo)

    html = client.get(f"/pedidos/{pedido.id}").text

    trecho = html.split("<dt>Status do pagamento</dt>")[1].split("</dd>")[0]
    assert f'<span class="selo-status selo-status-{codigo}">' in trecho
    assert trecho.rstrip().endswith(f"{rotulo}</span>")
    assert "<dt>Pagamento</dt><dd>PIX</dd>" in html  # o restante continua
