"""Testes das telas (Novo pedido, consulta e Produção) em um navegador real (Edge ou Chrome,
sem interface).

Controla o navegador pelo protocolo DevTools usando `websockets`, que já vem com o
projeto. Se nenhum navegador for encontrado, os testes são pulados.
Para indicar outro navegador: variável de ambiente FORMAHUB_NAVEGADOR=<caminho do executável>.
"""

import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from app.main import app

pytestmark = pytest.mark.navegador

CANDIDATOS = [
    os.environ.get("FORMAHUB_NAVEGADOR", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("msedge") or "",
]


def esperar(condicao, tempo=10.0, mensagem="tempo esgotado"):
    limite = time.monotonic() + tempo
    while time.monotonic() < limite:
        resultado = condicao()
        if resultado:
            return resultado
        time.sleep(0.05)
    raise TimeoutError(mensagem)


class Pagina:
    """Cliente mínimo do protocolo DevTools para uma aba."""

    def __init__(self, ws_url: str, base_url: str):
        from websockets.sync.client import connect

        self.ws = connect(ws_url, max_size=None)
        self.base_url = base_url
        self._ids = itertools.count(1)
        self.eventos = []

    def cmd(self, metodo: str, **params):
        id_ = next(self._ids)
        self.ws.send(json.dumps({"id": id_, "method": metodo, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=15))
            if msg.get("id") == id_:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})
            if "method" in msg:
                self.eventos.append(msg)

    def evento(self, metodo: str, tempo=5.0):
        """Espera um evento do navegador (ex.: Page.fileChooserOpened) e o devolve."""
        limite = time.monotonic() + tempo
        while time.monotonic() < limite:
            for i, msg in enumerate(self.eventos):
                if msg["method"] == metodo:
                    return self.eventos.pop(i)
            try:
                msg = json.loads(self.ws.recv(timeout=0.2))
            except TimeoutError:
                continue
            if "method" in msg:
                self.eventos.append(msg)
        raise TimeoutError(f"evento {metodo} não chegou")

    def js(self, expressao: str, aguardar=False):
        # userGesture: o navegador só abre o seletor de arquivos após uma ação do usuário.
        r = self.cmd(
            "Runtime.evaluate",
            expression=expressao,
            returnByValue=True,
            userGesture=True,
            awaitPromise=aguardar,
        )
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def tela(self, largura=1280, altura=900):
        self.cmd(
            "Emulation.setDeviceMetricsOverride",
            width=largura,
            height=altura,
            deviceScaleFactor=1,
            mobile=largura < 700,
        )

    def abrir(self, caminho="/pedidos/novo"):
        # Aba visível: em aba oculta o navegador não carrega imagens com loading="lazy".
        self.cmd("Page.bringToFront")
        self.cmd("Page.navigate", url=self.base_url + caminho)
        self._esperar_carregar()

    def _esperar_carregar(self):
        esperar(
            lambda: self.js("document.readyState === 'complete' && !window.__antiga"),
            mensagem="página não carregou",
        )

    def enviar(self):
        """Salva o pedido (envio por fetch, sem recarregar) e espera a resposta ser aplicada."""
        self.js(
            "document.getElementById('form-pedido').dataset.envio = '';"
            " document.querySelector('#form-pedido button[type=submit]').click()"
        )
        esperar(
            lambda: self.js("document.getElementById('form-pedido').dataset.envio === 'concluido'"),
            mensagem="o envio do pedido não terminou",
        )

    def digitar(self, seletor: str, texto: str):
        self.js(f"document.querySelector({json.dumps(seletor)}).focus()")
        for letra in texto:
            self.cmd("Input.insertText", text=letra)

    def apagar(self, vezes=1):
        for _ in range(vezes):
            tecla = {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8}
            self.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **tecla)
            self.cmd("Input.dispatchKeyEvent", type="keyUp", **tecla)

    def valor(self, seletor: str) -> str:
        return self.js(f"document.querySelector({json.dumps(seletor)}).value")

    def texto(self, seletor: str) -> str:
        return self.js(f"document.querySelector({json.dumps(seletor)}).textContent.trim()")

    def clicar(self, seletor: str):
        self.js(f"document.querySelector({json.dumps(seletor)}).click()")

    def fechar(self):
        self.ws.close()


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor():
    porta = porta_livre()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    esperar(lambda: server.started, mensagem="servidor não subiu")
    yield f"http://127.0.0.1:{porta}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def pagina(servidor, tmp_path_factory):
    executavel = next((c for c in CANDIDATOS if c and Path(c).exists()), None)
    if not executavel:
        pytest.skip("Edge/Chrome não encontrado (defina FORMAHUB_NAVEGADOR).")

    perfil = tmp_path_factory.mktemp("perfil-navegador")
    processo = subprocess.Popen(
        [
            executavel,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={perfil}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    porta = None
    try:
        arquivo_porta = perfil / "DevToolsActivePort"
        esperar(arquivo_porta.exists, mensagem="navegador não abriu a porta de depuração")
        porta = esperar(lambda: arquivo_porta.read_text().split("\n")[0].strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/list") as r:
            aba = next(t for t in json.load(r) if t["type"] == "page")
        p = Pagina(aba["webSocketDebuggerUrl"], servidor)
        p.cmd("Page.enable")
        yield p
        p.fechar()
    finally:
        if porta:
            fechar_navegador(porta)
        processo.terminate()
        processo.wait(timeout=10)


def fechar_navegador(porta):
    """Pede ao navegador que feche inteiro (Browser.close).

    O executável do Edge relança o navegador e sai; encerrar só o processo iniciado aqui
    deixava o navegador e os processos filhos abertos a cada execução dos testes.
    """
    from websockets.sync.client import connect

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/json/version") as r:
            endereco = json.load(r)["webSocketDebuggerUrl"]
        with connect(endereco) as ws:
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            ws.recv(timeout=5)
    except Exception:
        pass  # já fechado, ou fechou antes de responder


@pytest.fixture
def tela(pagina):
    pagina.tela(1280, 900)
    pagina.abrir()
    return pagina


ITEM = "#itens .item:nth-child({n}) [name={campo}]"


def item(n: int, campo: str) -> str:
    return ITEM.format(n=n, campo=campo)


# ---------- Máscaras ----------


def test_mascara_contato_aplicada_enquanto_digita(tela):
    tela.digitar("#contato", "11987654321")
    assert tela.valor("#contato") == "(11) 98765-4321"


def test_contato_ignora_letras_e_limita_digitos(tela):
    tela.digitar("#contato", "11a9b8765()-43219999")
    assert tela.valor("#contato") == "(11) 98765-4321"


def test_contato_apagar_remove_digitos(tela):
    tela.digitar("#contato", "119876")
    assert tela.valor("#contato") == "(11) 9876"
    tela.apagar(4)
    assert tela.valor("#contato") == "(11"
    tela.apagar(2)
    assert tela.valor("#contato") == ""


def test_quantidade_aceita_somente_inteiros(tela):
    campo = item(1, "item_quantidade")
    tela.js(f"document.querySelector('{campo}').value = ''")
    tela.digitar(campo, "2,5a-")
    assert tela.valor(campo) == "25"


def test_quantidade_minima_1_ao_sair_do_campo(tela):
    campo = item(1, "item_quantidade")
    tela.js(f"document.querySelector('{campo}').value = ''")
    tela.digitar(campo, "0")
    tela.js(f"document.querySelector('{campo}').blur()")
    assert tela.valor(campo) == "1"


def test_valor_unitario_estilo_bancario(tela):
    campo = item(1, "item_valor")
    for digito, esperado in [
        ("1", "R$ 0,01"),
        ("2", "R$ 0,12"),
        ("3", "R$ 1,23"),
        ("4", "R$ 12,34"),
        ("5", "R$ 123,45"),
        ("6", "R$ 1.234,56"),
    ]:
        tela.digitar(campo, digito)
        assert tela.valor(campo) == esperado
    tela.apagar()
    assert tela.valor(campo) == "R$ 123,45"


def test_valor_unitario_nao_aceita_negativo(tela):
    campo = item(1, "item_valor")
    tela.digitar(campo, "-500")
    assert tela.valor(campo) == "R$ 5,00"


def test_prazo_mascara_enquanto_digita(tela):
    for digito, esperado in [
        ("0", "0"),
        ("5", "05"),
        ("1", "05/1"),
        ("0", "05/10"),
        ("2", "05/10/2"),
        ("6", "05/10/26"),
        ("9", "05/10/26"),  # sete números: o excedente é ignorado
    ]:
        tela.digitar("#prazo_entrega", digito)
        assert tela.valor("#prazo_entrega") == esperado
    assert tela.js("document.getElementById('prazo_entrega').maxLength") == 8
    assert tela.js("document.getElementById('prazo_entrega').inputMode") == "numeric"


def test_prazo_ignora_letras_e_outros_caracteres(tela):
    tela.digitar("#prazo_entrega", "0a5/b1-0 x2.6")
    assert tela.valor("#prazo_entrega") == "05/10/26"


def test_prazo_apagar_e_corrigir(tela):
    tela.digitar("#prazo_entrega", "051026")
    for esperado in ["05/10/2", "05/10", "05/1", "05", "0", ""]:
        tela.apagar()
        assert tela.valor("#prazo_entrega") == esperado
    tela.digitar("#prazo_entrega", "0610")
    tela.apagar(2)
    tela.digitar("#prazo_entrega", "1127")
    assert tela.valor("#prazo_entrega") == "06/11/27"


@pytest.mark.parametrize("colado", ["051026", "05/10/26", "05.10.26", " 05-10-26 "])
def test_prazo_colado_recebe_as_barras(tela, colado):
    tela.js(
        "(() => { const c = document.getElementById('prazo_entrega'); c.focus();"
        f" c.value = {json.dumps(colado)};"
        " c.dispatchEvent(new InputEvent('input',"
        " {bubbles: true, inputType: 'insertFromPaste'})) })()"
    )
    assert tela.valor("#prazo_entrega") == "05/10/26"


def erro_do_prazo(tela):
    return tela.js(
        "(() => { const e = document.getElementById('prazo_entrega-erro');"
        " return e ? e.textContent : null })()"
    )


@pytest.mark.parametrize(
    ("digitado", "erro"),
    [
        ("310426", "Data inválida."),  # abril tem 30 dias
        ("290226", "Data inválida."),  # 2026 não é bissexto
        ("001026", "Data inválida."),
        ("051326", "Data inválida."),
        ("0510", "Use o formato dd/mm/aa."),
        ("290228", None),  # 2028 é bissexto
        ("290200", None),  # 2000 é bissexto
        ("010120", None),  # data passada é aceita
    ],
)
def test_prazo_validado_ao_sair_do_campo(tela, digitado, erro):
    tela.digitar("#prazo_entrega", digitado)
    tela.js("document.getElementById('prazo_entrega').blur()")

    assert erro_do_prazo(tela) == erro
    invalido = tela.js("document.getElementById('prazo_entrega').getAttribute('aria-invalid')")
    assert invalido == ("true" if erro else None)
    if erro:
        assert (
            tela.js("document.getElementById('prazo_entrega').getAttribute('aria-describedby')")
            == "prazo_entrega-erro"
        )
        assert tela.js("document.getElementById('campo-prazo').classList.contains('tem-erro')")


def test_erro_do_prazo_some_ao_corrigir(tela):
    tela.digitar("#prazo_entrega", "310426")
    tela.js("document.getElementById('prazo_entrega').blur()")
    assert erro_do_prazo(tela) == "Data inválida."

    tela.js("document.getElementById('prazo_entrega').focus()")
    tela.apagar(4)
    assert erro_do_prazo(tela) is None
    tela.digitar("#prazo_entrega", "0526")
    tela.js("document.getElementById('prazo_entrega').blur()")
    assert tela.valor("#prazo_entrega") == "31/05/26" and erro_do_prazo(tela) is None


def test_prazo_vazio_so_e_acusado_no_envio(tela):
    tela.js("document.getElementById('prazo_entrega').focus()")
    tela.js("document.getElementById('prazo_entrega').blur()")
    assert erro_do_prazo(tela) is None

    tela.enviar()
    assert erro_do_prazo(tela) == "Informe o prazo de entrega."


def test_prazo_preservado_no_erro_e_limpo_no_sucesso(tela, repo_pedidos):
    from datetime import date

    preencher_dois_itens(tela)
    tela.js("document.getElementById('cliente').value = ''")
    tela.enviar()

    assert tela.js("!!document.getElementById('cliente-erro')")
    assert tela.valor("#prazo_entrega") == "05/10/26"
    assert erro_do_prazo(tela) is None

    tela.digitar("#cliente", "Maria Souza")
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    assert next(iter(repo_pedidos.pedidos.values())).prazo_entrega == date(2026, 10, 5)
    assert tela.valor("#prazo_entrega") == ""
    # O campo novo (vindo do servidor) continua com a máscara.
    tela.digitar("#prazo_entrega", "311226")
    assert tela.valor("#prazo_entrega") == "31/12/26"


def test_prazo_invalido_recusado_pelo_servidor_fica_no_campo(tela, repo_pedidos):
    preencher_dois_itens(tela)
    tela.js("document.getElementById('prazo_entrega').value = '31/04/26'")
    tela.enviar()

    assert repo_pedidos.pedidos == {}
    assert tela.valor("#prazo_entrega") == "31/04/26"
    assert erro_do_prazo(tela) == "Data inválida."


@pytest.mark.parametrize("largura", [768, 1024, 1280])
def test_prazo_na_mesma_linha_do_cliente_e_contato(pagina, largura):
    pagina.tela(largura, 800)
    pagina.abrir()
    caixas = pagina.js(
        "['#cliente', '#contato', '#prazo_entrega'].map(s => {"
        " const r = document.querySelector(s).getBoundingClientRect();"
        " return [Math.round(r.left), Math.round(r.right), Math.round(r.top), r.height] })"
    )
    assert len({c[2] for c in caixas}) == 1  # mesma linha
    assert caixas[0][1] < caixas[1][0] < caixas[1][1] < caixas[2][0]
    assert caixas[2][1] - caixas[2][0] >= 110  # cabe "dd/mm/aa" com folga
    assert min(c[3] for c in caixas) >= 44


def test_celular_prazo_abaixo_do_contato(pagina):
    pagina.tela(360, 800)
    pagina.abrir()
    caixas = pagina.js(
        "['#cliente', '#contato', '#prazo_entrega'].map(s => {"
        " const r = document.querySelector(s).getBoundingClientRect();"
        " return [Math.round(r.left), Math.round(r.right), Math.round(r.top)] })"
    )
    assert len({(c[0], c[1]) for c in caixas}) == 1
    assert caixas[0][2] < caixas[1][2] < caixas[2][2]
    assert pagina.js("document.documentElement.scrollWidth") <= 360


# ---------- Cálculos e itens ----------


def test_subtotal_por_item_e_resumo(tela):
    tela.digitar(item(1, "item_produto"), "Caneca")
    tela.js(f"document.querySelector('{item(1, 'item_quantidade')}').value = ''")
    tela.digitar(item(1, "item_quantidade"), "3")
    tela.digitar(item(1, "item_valor"), "1050")
    assert tela.texto("#itens .item:nth-child(1) output") == "R$ 31,50"

    tela.clicar("#adicionar-item")
    tela.digitar(item(2, "item_produto"), "Camiseta")
    tela.js(f"document.querySelector('{item(2, 'item_quantidade')}').value = ''")
    tela.digitar(item(2, "item_quantidade"), "2")
    tela.digitar(item(2, "item_valor"), "199990")
    assert tela.texto("#itens .item:nth-child(2) output") == "R$ 3.999,80"

    assert tela.texto("#resumo-quantidade") == "5"
    assert tela.texto("#resumo-total") == "R$ 4.031,30"


def test_adicionar_e_remover_itens(tela):
    contar = "document.querySelectorAll('#itens .item').length"
    assert tela.js(contar) == 1

    tela.clicar("#adicionar-item")
    tela.clicar("#adicionar-item")
    assert tela.js(contar) == 3
    assert tela.js(
        f"document.activeElement === document.querySelector('{item(3, 'item_produto')}')"
    )

    tela.digitar(item(1, "item_valor"), "1000")
    tela.digitar(item(3, "item_valor"), "500")
    assert tela.texto("#resumo-total") == "R$ 15,00"
    assert tela.texto("#resumo-quantidade") == "3"

    tela.clicar("#itens .item:nth-child(3) .item-remover")
    assert tela.js(contar) == 2
    assert tela.texto("#resumo-total") == "R$ 10,00"
    assert tela.texto("#resumo-quantidade") == "2"

    tela.js("document.querySelectorAll('.item-remover').forEach(b => b.click())")
    assert tela.js(contar) == 0
    assert tela.js("document.getElementById('itens-vazio').offsetParent !== null")
    assert tela.texto("#resumo-total") == "R$ 0,00"


# ---------- Pagamento e entrega ----------


@pytest.mark.parametrize(
    ("grupo", "primeira", "segunda"),
    [
        ("pagamento", "PIX", "Cartão"),
        ("status_pagamento", "Pendente", "Pago"),
        ("entrega", "Entrega em mãos", "Retirada"),
    ],
)
def test_selecao_exclusiva_e_destacada(tela, grupo, primeira, segunda):
    marcadas = f"[...document.querySelectorAll('[name={grupo}]:checked')].map(i => i.value)"
    assert tela.js(marcadas) == []

    tela.clicar(f"#grupo-{grupo} input[value='{primeira}']")
    assert tela.js(marcadas) == [primeira]
    tela.clicar(f"#grupo-{grupo} input[value='{segunda}']")
    assert tela.js(marcadas) == [segunda]

    borda = (
        "getComputedStyle(document.querySelector("
        "\"#grupo-{g} input[value='{v}'] + .escolha-card\")).borderColor"
    )
    cor_primaria = tela.js(
        "(() => { const d = document.createElement('div'); d.style.color = 'var(--cor-primaria)';"
        " document.body.append(d); const c = getComputedStyle(d).color; d.remove(); return c })()"
    )
    # Espera a transição de cor do card terminar.
    esperar(lambda: tela.js(borda.format(g=grupo, v=segunda)) == cor_primaria, tempo=2)
    assert tela.js(borda.format(g=grupo, v=primeira)) != cor_primaria


def test_escolhas_funcionam_pelo_teclado(tela):
    tela.js("document.querySelector(\"input[name=pagamento][value='PIX']\").focus()")
    tecla = {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39}
    tela.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **tecla)
    tela.cmd("Input.dispatchKeyEvent", type="keyUp", **tecla)
    assert tela.js("document.querySelector('[name=pagamento]:checked').value") == "Dinheiro"


# ---------- Envio ----------


def test_envio_invalido_bloqueado_com_erros(tela):
    tela.enviar()

    assert tela.js("!!document.querySelector('.alerta-erro')")
    assert not tela.js("!!document.querySelector('.alerta-sucesso')")
    for campo in ("cliente", "contato", "prazo_entrega", "pagamento", "entrega"):
        assert tela.js(f"!!document.getElementById('{campo}-erro')"), campo
    assert "Informe o produto." in tela.texto("#itens")


def test_erro_some_ao_corrigir(tela):
    tela.enviar()
    tela.digitar("#cliente", "A")
    tela.clicar("#grupo-pagamento input[value='PIX']")
    assert not tela.js("!!document.getElementById('cliente-erro')")
    assert not tela.js("!!document.getElementById('pagamento-erro')")


def test_envio_valido_mostra_sucesso(tela):
    tela.digitar("#cliente", "Maria Souza")
    tela.digitar("#contato", "11987654321")
    tela.digitar("#prazo_entrega", "051026")
    tela.digitar(item(1, "item_produto"), "Caneca")
    tela.js(f"document.querySelector('{item(1, 'item_quantidade')}').value = ''")
    tela.digitar(item(1, "item_quantidade"), "2")
    tela.digitar(item(1, "item_valor"), "3550")
    tela.clicar("#grupo-pagamento input[value='Dinheiro']")
    tela.clicar("#grupo-status_pagamento input[value='Pago']")
    tela.clicar("#grupo-entrega input[value='Entrega em mãos']")
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith(
        "Pedido de Maria Souza salvo com sucesso — 2 itens, total R$ 71,00."
    )


# ---------- Responsivo ----------


@pytest.mark.parametrize("largura", [360, 768, 1024, 1280])
def test_sem_rolagem_horizontal(pagina, largura):
    pagina.tela(largura, 800)
    pagina.abrir()
    pagina.clicar("#adicionar-item")
    pagina.digitar(item(1, "item_produto"), "Produto com um nome bem comprido para testar")
    assert pagina.js("document.documentElement.scrollWidth") <= largura


def test_celular_campos_e_botoes_faceis_de_usar(pagina):
    pagina.tela(360, 800)
    pagina.abrir()
    cards = pagina.js(
        "[...document.querySelectorAll('#grupo-pagamento .escolha-card')]"
        ".map(c => { const r = c.getBoundingClientRect(); return [r.top, r.width, r.height] })"
    )
    assert len({round(top) for top, _, _ in cards}) == 1  # 3 opções lado a lado
    assert all(largura >= 44 and altura >= 44 for _, largura, altura in cards)
    alturas = pagina.js(
        "[...document.querySelectorAll('#form-pedido input:not([type=radio]), .item-remover')]"
        ".filter(e => e.offsetParent !== null).map(e => e.getBoundingClientRect().height)"
    )
    assert min(alturas) >= 40
    # Total do pedido cabe em uma linha.
    assert pagina.js(
        "(() => { const dd = document.getElementById('resumo-total');"
        " const fonte = parseFloat(getComputedStyle(dd).fontSize);"
        " return dd.getBoundingClientRect().height < fonte * 2 })()"
    )


def test_desktop_item_em_uma_linha(pagina):
    pagina.tela(1280, 900)
    pagina.abrir()
    topos = pagina.js(
        "['item_produto','item_quantidade','item_valor']"
        ".map(n => document.querySelector('[name=' + n + ']'))"
        ".map(el => Math.round(el.getBoundingClientRect().top))"
    )
    assert len(set(topos)) == 1


# ---------- Compactação (desktop) e ícones ----------


def preencher_dois_itens(tela):
    tela.digitar("#cliente", "Maria Souza")
    tela.digitar("#contato", "11987654321")
    tela.digitar("#prazo_entrega", "051026")
    tela.digitar(item(1, "item_produto"), "Caneca")
    tela.digitar(item(1, "item_valor"), "3550")
    tela.clicar("#adicionar-item")
    tela.digitar(item(2, "item_produto"), "Camiseta")
    tela.digitar(item(2, "item_valor"), "9990")
    tela.clicar("#grupo-pagamento input[value='PIX']")
    tela.clicar("#grupo-status_pagamento input[value='Pendente']")
    tela.clicar("#grupo-entrega input[value='Entrega em mãos']")


# Elementos visíveis (dentro da página, exceto o cabeçalho) que ultrapassam a janela
# ou que escondem conteúdo com overflow.
CORTES = """
(() => {
  const ruins = [];
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  document.querySelectorAll('main *').forEach(el => {
    if (el.closest('template, svg, .visualmente-oculto') || el.offsetParent === null) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const nome = el.tagName + '.' + el.className;
    if (r.left < -0.5 || r.right > vw + 0.5) ruins.push(nome + ' fora na horizontal');
    if (LIMITE_VERTICAL && (r.top < -0.5 || r.bottom > vh + 0.5))
      ruins.push(nome + ' fora na vertical');
    const o = getComputedStyle(el);
    const rola = /(auto|scroll|hidden|clip)/.test(o.overflowX + o.overflowY);
    // O nome do arquivo termina em reticências de propósito (o texto completo está no title).
    const cortado = el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
    // Textarea rola por dentro por natureza (e o usuário pode redimensioná-lo).
    if (rola && cortado && el.tagName !== 'TEXTAREA' && !el.classList.contains('upload-nome'))
      ruins.push(nome + ' com conteúdo cortado');
  });
  return ruins;
})()
"""


def test_dois_itens_cabem_em_1280x720_sem_rolagem_vertical(pagina):
    pagina.tela(1280, 720)
    pagina.abrir()
    preencher_dois_itens(pagina)

    assert pagina.js("document.querySelectorAll('#itens .item').length") == 2
    assert pagina.js("document.documentElement.scrollHeight") <= 720
    assert pagina.js("document.body.scrollHeight") <= 720
    # Sem barra de rolagem: a largura útil é a da janela inteira.
    assert pagina.js("document.documentElement.clientWidth") == 1280
    assert pagina.js("document.documentElement.scrollWidth") <= 1280
    # Campos, resumo e botões finais visíveis, sem cortes nem rolagem interna.
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "true")) == []
    for seletor in (
        "#cliente",
        "#adicionar-item",
        "#resumo-total",
        "#form-pedido button[type=submit]",
    ):
        base = pagina.js(
            f"document.querySelector({json.dumps(seletor)}).getBoundingClientRect().bottom"
        )
        assert base <= 720, seletor
    assert pagina.texto("#resumo-total") == "R$ 135,40"


def test_um_item_tambem_cabe_em_1280x720(pagina):
    pagina.tela(1280, 720)
    pagina.abrir()

    assert pagina.js("document.documentElement.scrollHeight") <= 720


def test_alvos_de_clique_de_44px_no_desktop_compacto(pagina):
    pagina.tela(1280, 720)
    pagina.abrir()
    menores = pagina.js(
        "[...document.querySelectorAll('a.botao, button, #form-pedido input:not([type=radio]),"
        " .escolha-card, textarea')].filter(e => e.offsetParent !== null)"
        ".filter(e => e.getBoundingClientRect().height < 44)"
        ".map(e => e.className || e.name)"
    )
    assert menores == []


@pytest.mark.parametrize("largura", [360, 768])
def test_telas_menores_rolam_na_vertical_sem_cortes(pagina, largura):
    pagina.tela(largura, 700)
    pagina.abrir()
    preencher_dois_itens(pagina)

    assert pagina.js("document.documentElement.scrollHeight") > 700  # rolagem vertical normal
    assert pagina.js("document.documentElement.scrollWidth") <= largura
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "false")) == []


def test_novos_icones_no_navegador(tela):
    # Entrega em mãos: ícone próprio, ao lado do título mantido.
    assert tela.js("!!document.querySelector('svg[data-icone=entrega-em-maos]')")
    assert (
        tela.texto("#grupo-entrega input[value='Entrega em mãos'] + .escolha-card .escolha-titulo")
        == "Entrega em mãos"
    )
    # PIX: símbolo preenchido que segue a cor do card (normal e selecionado).
    cor = (
        "[getComputedStyle(document.querySelector('svg[data-icone=pix] path')).fill,"
        " getComputedStyle(document.querySelector("
        "'#grupo-pagamento input[value=PIX] + .escolha-card')).color]"
    )
    esperar(lambda: (c := tela.js(cor)) and c[0] == c[1], tempo=2)
    tela.clicar("#grupo-pagamento input[value='PIX']")
    esperar(lambda: (c := tela.js(cor)) and c[0] == c[1], tempo=2)
    assert (
        tela.js("getComputedStyle(document.querySelector('svg[data-icone=pix] path')).stroke")
        == "none"
    )


def test_desktop_secoes_inferiores_alinhadas_as_superiores(pagina):
    pagina.tela(1280, 720)
    pagina.abrir()
    bordas = pagina.js(
        "['#grupo-pagamento', '#grupo-status_pagamento', '#grupo-entrega', '.secao-obs',"
        " '.resumo'].map(s => {"
        " const r = document.querySelector(s).getBoundingClientRect();"
        " return [Math.round(r.left * 10) / 10, Math.round(r.right * 10) / 10,"
        " Math.round(r.top), Math.round(r.bottom)] })"
    )
    pagamento, status, entrega, obs, resumo = bordas
    # Linha 1: Forma de pagamento (5fr) e Status do pagamento (4fr), lado a lado.
    assert status[0] > pagamento[1] and status[2:] == pagamento[2:]
    # Linha 2: Informações complementares na coluna da Forma de pagamento e Entrega na do
    # Status, mesmas bordas externas e mesma altura.
    assert obs[:2] == pagamento[:2]
    assert entrega[:2] == status[:2]
    assert obs[2:] == entrega[2:]
    # Linha 3: Resumo abaixo das duas, com a largura toda.
    assert resumo[2] > obs[3]
    assert resumo[0] == pagamento[0] and resumo[1] == status[1]
    # Total em uma linha só e o botão Salvar visível dentro do Resumo.
    assert pagina.js("document.querySelector('#resumo-total').getClientRects().length") == 1
    salvar = pagina.js(
        "(() => { const r = document.querySelector('#form-pedido button[type=submit]')"
        ".getBoundingClientRect(); return [r.top, r.bottom, r.width] })()"
    )
    assert salvar[2] > 100 and resumo[2] <= salvar[0] and salvar[1] <= resumo[3]


def test_desktop_dois_itens_cabem_com_o_resumo_embaixo(pagina):
    pagina.tela(1280, 720)
    pagina.abrir()
    preencher_dois_itens(pagina)

    assert pagina.js("document.documentElement.scrollHeight") <= 720
    assert (
        pagina.js(
            "document.querySelector('#form-pedido button[type=submit]').getBoundingClientRect()"
            ".bottom"
        )
        <= 720
    )
    assert pagina.js("document.querySelector('.resumo').getBoundingClientRect().bottom") <= 720
    assert pagina.js("document.documentElement.scrollWidth") <= 1280


# ---------- Imagem de referência de cada item (opcional, somente frontend) ----------

MB = 1024 * 1024

# Cria um File no navegador (PNG, JPEG ou WebP reais, desenhados em um canvas) e o guarda em
# window.__arquivos. `extra` acrescenta bytes ao final, só para chegar a um tamanho.
CRIAR_ARQUIVO = """
(async () => {
  const c = document.createElement('canvas'); c.width = 64; c.height = 48;
  const g = c.getContext('2d'); g.fillStyle = '#ff2d8f'; g.fillRect(0, 0, 64, 48);
  const blob = await new Promise(r => c.toBlob(r, __TIPO__));
  const partes = [blob]; if (__EXTRA__) partes.push(new Uint8Array(__EXTRA__));
  (window.__arquivos = window.__arquivos || []).push(new File(partes, __NOME__, {type: __TIPO__}));
  return true;
})()
"""

# Dispara um evento de arrastar do tipo `tipo` sobre a área da imagem do item N,
# levando os arquivos criados. `soltar` esvazia a fila de arquivos depois.
ARRASTAR = """
(() => {
  const dt = new DataTransfer();
  (window.__arquivos || []).forEach(f => dt.items.add(f));
  const alvo = document.querySelectorAll('.upload-item')[__N__];
  alvo.dispatchEvent(
    new DragEvent('__TIPO__', {dataTransfer: dt, bubbles: true, cancelable: true}));
  if ('__TIPO__' === 'drop') window.__arquivos = [];
  return true;
})()
"""


def area(n: int) -> str:
    return f"document.querySelectorAll('.upload-item')[{n}]"


def parte(n: int, classe: str) -> str:
    return f"{area(n)}.querySelector('.{classe}')"


def novo_arquivo(p, nome="foto.png", tipo="image/png", extra=0):
    p.js(
        CRIAR_ARQUIVO.replace("__TIPO__", json.dumps(tipo))
        .replace("__EXTRA__", str(extra))
        .replace("__NOME__", json.dumps(nome)),
        aguardar=True,
    )


def arquivo_falso(p, nome, tipo, tamanho=100):
    p.js(
        "(window.__arquivos = window.__arquivos || [])"
        f".push(new File([new Uint8Array({tamanho})], {json.dumps(nome)},"
        f" {{type: {json.dumps(tipo)}}}))"
    )


def arrastar(p, n, tipo):
    p.js(ARRASTAR.replace("__N__", str(n)).replace("__TIPO__", tipo))


def soltar(p, n=0):
    """Arrasta e solta os arquivos criados sobre a imagem do item n."""
    arrastar(p, n, "dragenter")
    arrastar(p, n, "dragover")
    arrastar(p, n, "drop")


def escolher(p, n=0):
    """Seleção pelo seletor de arquivos: coloca os arquivos no input e dispara 'change'."""
    p.js(
        "(() => { const dt = new DataTransfer();"
        " (window.__arquivos || []).forEach(f => dt.items.add(f));"
        f" const i = {parte(n, 'upload-arquivo')}; i.files = dt.files;"
        " window.__arquivos = []; i.dispatchEvent(new Event('change', {bubbles: true})) })()"
    )


def visivel(p, n, classe):
    return p.js(f"{parte(n, classe)}.offsetParent !== null")


def previa_visivel(p, n=0):
    return visivel(p, n, "upload-miniatura")


def esperar_previa(p, nome, n=0):
    esperar(
        lambda: previa_visivel(p, n) and p.js(f"{parte(n, 'upload-nome')}.textContent") == nome,
        mensagem=f"pré-visualização de {nome} não apareceu",
    )


def mensagem_erro(p, n=0):
    return p.js(
        f"(() => {{ const e = {parte(n, 'upload-erro')};"
        " return e.offsetParent === null ? '' : e.textContent.trim() })()"
    )


def esperar_erro(p, n=0):
    esperar(lambda: mensagem_erro(p, n), mensagem="mensagem de erro não apareceu")
    return mensagem_erro(p, n)


def arquivos_por_item(p):
    return p.js("window.ImagensReferencia.arquivos().map(a => a && a.name)")


def png_bytes(largura=8, altura=8) -> bytes:
    import struct
    import zlib

    def bloco(tipo, dados):
        corpo = tipo + dados
        return struct.pack(">I", len(dados)) + corpo + struct.pack(">I", zlib.crc32(corpo))

    linhas = b"".join(b"\x00" + b"\xff\x2d\x8f" * largura for _ in range(altura))
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloco(b"IHDR", struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0))
        + bloco(b"IDAT", zlib.compress(linhas))
        + bloco(b"IEND", b"")
    )


@pytest.fixture
def seletor_interceptado(pagina):
    """Faz o seletor de arquivos avisar por evento em vez de abrir uma janela."""
    pagina.cmd("Page.setInterceptFileChooserDialog", enabled=True)
    yield pagina
    pagina.cmd("Page.setInterceptFileChooserDialog", enabled=False)


def test_cada_item_tem_o_campo_de_imagem_de_referencia_opcional(tela):
    assert tela.js("document.querySelectorAll('#itens .item .upload-item').length") == 1
    assert tela.texto(".upload-dica") == "Arraste uma imagem aqui"
    assert tela.texto(".upload-selecionar") == "Selecionar imagem"
    assert tela.js(f"{parte(0, 'upload-arquivo')}.type") == "file"
    assert not previa_visivel(tela)
    assert mensagem_erro(tela) == ""

    # Item adicionado depois também ganha o campo, independente do primeiro.
    tela.clicar("#adicionar-item")
    assert tela.js("document.querySelectorAll('#itens .item .upload-item').length") == 2
    assert tela.js(f"{area(1)}.getAttribute('aria-label')") == "Imagem de referência do item 2"
    assert tela.js("window.ImagensReferencia.arquivos()") == [None, None]


def test_pedido_sem_imagem_e_valido_e_nao_mostra_erro_de_imagem(tela):
    preencher_dois_itens(tela)  # nenhum dos itens tem imagem
    assert tela.js("window.ImagensReferencia.arquivos()") == [None, None]
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    assert not tela.js("!!document.querySelector('.alerta-erro')")
    assert tela.js("[...document.querySelectorAll('.upload-erro')].every(e => e.hidden)")


def test_envio_invalido_sem_imagem_nao_acusa_falta_de_imagem(tela):
    tela.enviar()  # formulário vazio: erros nos campos obrigatórios, mas nunca na imagem

    assert tela.js("!!document.querySelector('.alerta-erro')")
    assert tela.js("[...document.querySelectorAll('.upload-erro')].every(e => e.hidden)")
    assert "imagem" not in tela.texto(".alerta-erro").lower()


def test_imagem_em_um_item_nao_afeta_o_outro(tela):
    tela.clicar("#adicionar-item")
    novo_arquivo(tela, "so-do-segundo.png")
    soltar(tela, 1)
    esperar_previa(tela, "so-do-segundo.png", 1)

    assert not previa_visivel(tela, 0)
    assert visivel(tela, 0, "upload-selecionar")
    assert arquivos_por_item(tela) == [None, "so-do-segundo.png"]

    novo_arquivo(tela, "do-primeiro.jpg", "image/jpeg")
    soltar(tela, 0)
    esperar_previa(tela, "do-primeiro.jpg", 0)
    assert arquivos_por_item(tela) == ["do-primeiro.jpg", "so-do-segundo.png"]
    # Um item pode ter no máximo uma imagem: escolher outra substitui a atual.
    novo_arquivo(tela, "substituta.png")
    soltar(tela, 0)
    esperar_previa(tela, "substituta.png", 0)
    assert arquivos_por_item(tela) == ["substituta.png", "so-do-segundo.png"]


def test_remover_o_item_libera_a_url_da_imagem(tela):
    tela.js(
        "window.__revogadas = []; const r = URL.revokeObjectURL.bind(URL);"
        " URL.revokeObjectURL = u => { window.__revogadas.push(u); r(u) }"
    )
    tela.clicar("#adicionar-item")
    novo_arquivo(tela, "segundo.png")
    soltar(tela, 1)
    esperar_previa(tela, "segundo.png", 1)
    url = tela.js(f"{parte(1, 'upload-miniatura')}.src")

    tela.clicar("#itens .item:nth-child(2) .item-remover")
    esperar(
        lambda: url in tela.js("window.__revogadas"), mensagem="URL do item removido não liberada"
    )
    assert tela.js("window.ImagensReferencia.arquivos()") == [None]


def test_botao_selecionar_abre_o_seletor_de_arquivos(seletor_interceptado, tela, tmp_path):
    tela.clicar(".upload-selecionar")
    aberto = tela.evento("Page.fileChooserOpened")
    assert aberto["params"]["mode"] == "selectSingle"  # somente uma imagem

    # Escolher um arquivo real no seletor mostra a pré-visualização.
    arquivo = tmp_path / "pelo-seletor.png"
    arquivo.write_bytes(png_bytes())
    tela.cmd(
        "DOM.setFileInputFiles",
        files=[str(arquivo)],
        backendNodeId=aberto["params"]["backendNodeId"],
    )
    esperar_previa(tela, "pelo-seletor.png")


@pytest.mark.parametrize(
    ("tecla", "codigo", "texto", "vk"),
    [("Enter", "Enter", "\r", 13), (" ", "Space", " ", 32)],
)
def test_seletor_abre_pelo_teclado(seletor_interceptado, tela, tecla, codigo, texto, vk):
    tela.js(f"{parte(0, 'upload-selecionar')}.focus()")
    dados = {"key": tecla, "code": codigo, "windowsVirtualKeyCode": vk}
    tela.cmd("Input.dispatchKeyEvent", type="keyDown", text=texto, **dados)
    tela.cmd("Input.dispatchKeyEvent", type="keyUp", **dados)
    tela.evento("Page.fileChooserOpened")


def test_foco_visivel_e_nome_acessivel(tela):
    # Chega ao botão apenas com a tecla Tab, a partir do valor unitário.
    tela.js(f"document.querySelector({json.dumps(item(1, 'item_valor'))}).focus()")
    tab = {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9}
    tela.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **tab)
    tela.cmd("Input.dispatchKeyEvent", type="keyUp", **tab)
    assert tela.js("document.activeElement.className.includes('upload-selecionar')")
    assert tela.js("document.activeElement.matches(':focus-visible')")
    contorno = tela.js("getComputedStyle(document.activeElement).outlineWidth")
    assert float(contorno.removesuffix("px")) >= 3
    # Nome acessível do botão = texto visível; a área é um grupo com nome que cita o item.
    assert tela.texto(".upload-selecionar") == "Selecionar imagem"
    assert tela.js(f"{area(0)}.getAttribute('role')") == "group"
    assert "item 1" in tela.js(f"{area(0)}.getAttribute('aria-label')")
    # O input de arquivo não entra na ordem de tabulação (o botão faz esse papel).
    assert tela.js(f"{parte(0, 'upload-arquivo')}.tabIndex") == -1


def test_arrastar_e_soltar_com_estado_visual(tela):
    novo_arquivo(tela, "arrastada.png")
    assert tela.js(f"getComputedStyle({parte(0, 'upload-dica')}).borderStyle") == "dashed"
    assert tela.js(f"getComputedStyle({area(0)}).outlineStyle") == "none"

    # Durante o arraste: contorno contínuo e borda contínua (não só cor).
    arrastar(tela, 0, "dragenter")
    arrastar(tela, 0, "dragover")
    assert tela.js(f"{area(0)}.classList.contains('arrastando')")
    assert tela.js(f"getComputedStyle({area(0)}).outlineStyle") == "solid"
    assert tela.js(f"getComputedStyle({parte(0, 'upload-dica')}).borderStyle") == "solid"

    # Ao sair sem soltar, volta ao normal e nada é selecionado.
    arrastar(tela, 0, "dragleave")
    assert not tela.js(f"{area(0)}.classList.contains('arrastando')")
    assert not previa_visivel(tela)

    arrastar(tela, 0, "dragenter")
    arrastar(tela, 0, "drop")
    esperar_previa(tela, "arrastada.png")
    assert not tela.js(f"{area(0)}.classList.contains('arrastando')")


@pytest.mark.parametrize(
    ("nome", "tipo"),
    [("foto.png", "image/png"), ("foto.jpg", "image/jpeg"), ("foto.webp", "image/webp")],
)
@pytest.mark.parametrize("modo", ["arrastar", "seletor"])
def test_formatos_aceitos(tela, nome, tipo, modo):
    novo_arquivo(tela, nome, tipo)
    (soltar if modo == "arrastar" else escolher)(tela)

    esperar_previa(tela, nome)
    assert mensagem_erro(tela) == ""
    assert tela.js("window.ImagensReferencia.arquivos()[0].type") == tipo


def test_jpeg_com_extensao_jpeg_e_aceito(tela):
    novo_arquivo(tela, "foto.jpeg", "image/jpeg")
    soltar(tela)
    esperar_previa(tela, "foto.jpeg")


@pytest.mark.parametrize(
    ("nome", "tipo"),
    [
        ("documento.pdf", "application/pdf"),
        ("animacao.gif", "image/gif"),
        ("desenho.svg", "image/svg+xml"),
        ("notas.txt", "text/plain"),
        ("planilha.xlsx", "application/vnd.ms-excel"),
        ("imagem.bmp", "image/bmp"),
        ("sem-tipo.exe", ""),
    ],
)
def test_outros_formatos_sao_rejeitados(tela, nome, tipo):
    arquivo_falso(tela, nome, tipo)
    soltar(tela)

    assert "Formato não aceito" in esperar_erro(tela)
    assert not previa_visivel(tela)
    assert tela.js("window.ImagensReferencia.arquivos()") == [None]


def test_mais_de_um_arquivo_e_rejeitado(tela):
    novo_arquivo(tela, "a.png")
    novo_arquivo(tela, "b.png")
    soltar(tela)

    assert "só uma imagem" in esperar_erro(tela)
    assert not previa_visivel(tela)


def test_limite_de_10_mb(tela):
    novo_arquivo(tela, "grande.png", extra=10 * MB)  # PNG + 10 MB: passa do limite
    soltar(tela)
    assert "Maior que 10 MB" in esperar_erro(tela)
    assert not previa_visivel(tela)

    # Exatamente 10 MB ainda é aceito.
    novo_arquivo(tela, "sonda.png")
    base = tela.js("window.__arquivos.pop().size")
    novo_arquivo(tela, "no-limite.png", extra=10 * MB - base)
    assert tela.js("window.__arquivos[0].size") == 10 * MB
    soltar(tela)
    esperar_previa(tela, "no-limite.png")
    assert tela.texto(".upload-tamanho") == "10 MB"
    assert mensagem_erro(tela) == ""


def test_arquivo_invalido_nao_apaga_a_imagem_valida(tela):
    novo_arquivo(tela, "valida.png")
    soltar(tela)
    esperar_previa(tela, "valida.png")
    url = tela.js(f"{parte(0, 'upload-miniatura')}.src")

    arquivo_falso(tela, "documento.pdf", "application/pdf")
    soltar(tela)
    assert "Formato não aceito" in esperar_erro(tela)
    # Com o erro visível, a miniatura e os botões da imagem válida continuam na tela.
    assert previa_visivel(tela)
    assert visivel(tela, 0, "upload-remover")
    novo_arquivo(tela, "enorme.png", extra=10 * MB)
    soltar(tela)
    esperar(lambda: "Maior que 10 MB" in mensagem_erro(tela), mensagem="erro de tamanho ausente")

    assert tela.js(f"{parte(0, 'upload-miniatura')}.src") == url
    assert arquivos_por_item(tela) == ["valida.png"]

    # O erro some quando uma imagem válida é escolhida, e o nome volta a aparecer.
    novo_arquivo(tela, "outra.png")
    soltar(tela)
    esperar_previa(tela, "outra.png")
    assert mensagem_erro(tela) == ""
    assert visivel(tela, 0, "upload-info")


def test_erro_de_um_item_nao_aparece_no_outro(tela):
    tela.clicar("#adicionar-item")
    arquivo_falso(tela, "documento.pdf", "application/pdf")
    soltar(tela, 0)

    assert "Formato não aceito" in esperar_erro(tela, 0)
    assert mensagem_erro(tela, 1) == ""


def test_arquivo_corrompido_e_rejeitado(tela):
    arquivo_falso(tela, "quebrada.png", "image/png", tamanho=500)  # bytes que não são um PNG
    soltar(tela)

    assert "ilegível" in esperar_erro(tela)
    assert not previa_visivel(tela)


def test_erro_e_anunciado_e_nao_depende_so_da_cor(tela):
    arquivo_falso(tela, "documento.pdf", "application/pdf")
    soltar(tela)
    esperar_erro(tela)

    assert tela.js(f"{parte(0, 'upload-erro')}.getAttribute('role')") == "alert"
    # Ícone de alerta em texto e borda contínua, além da cor.
    assert "⚠" in tela.js(f"getComputedStyle({parte(0, 'upload-erro')}, '::before').content")
    assert tela.js(f"getComputedStyle({parte(0, 'upload-erro')}).borderStyle") == "solid"
    # A dica de formatos e limite fica sempre visível no cabeçalho da coluna.
    assert "PNG, JPG ou WebP · até 10 MB" in tela.texto(".itens-cabecalho")


def test_previa_mostra_miniatura_nome_e_tamanho_formatado(tela):
    nome = "Caneca personalizada com nome muito comprido.png"
    novo_arquivo(tela, nome, extra=1536 * 1024)
    soltar(tela)
    esperar_previa(tela, nome)

    assert tela.js(
        f"(() => {{ const i = {parte(0, 'upload-miniatura')};"
        " return i.src.startsWith('blob:') && i.naturalWidth === 64 && i.offsetParent !== null })()"
    )
    tamanho = tela.texto(".upload-tamanho")
    assert tamanho.startswith("1,5") and tamanho.endswith(" MB")  # vírgula decimal
    assert tela.texto(".upload-remover") == "Remover imagem"
    assert visivel(tela, 0, "upload-remover") and visivel(tela, 0, "upload-trocar")
    assert not visivel(tela, 0, "upload-selecionar")


def test_tamanho_pequeno_em_kb(tela):
    novo_arquivo(tela, "kb.png", extra=100 * 1024)
    soltar(tela)
    esperar_previa(tela, "kb.png")
    assert tela.texto(".upload-tamanho").endswith(" KB")


def test_remover_e_substituir_liberam_urls_temporarias(tela):
    tela.js(
        "window.__revogadas = []; const r = URL.revokeObjectURL.bind(URL);"
        " URL.revokeObjectURL = u => { window.__revogadas.push(u); r(u) }"
    )
    novo_arquivo(tela, "primeira.png")
    soltar(tela)
    esperar_previa(tela, "primeira.png")
    primeira = tela.js(f"{parte(0, 'upload-miniatura')}.src")

    # Substituir: a nova imagem entra e a URL anterior é liberada.
    novo_arquivo(tela, "segunda.jpg", "image/jpeg")
    escolher(tela)
    esperar_previa(tela, "segunda.jpg")
    segunda = tela.js(f"{parte(0, 'upload-miniatura')}.src")
    assert segunda != primeira
    assert primeira in tela.js("window.__revogadas")
    assert arquivos_por_item(tela) == ["segunda.jpg"]

    # Remover: volta ao estado vazio, libera a URL e devolve o foco ao botão de selecionar.
    tela.clicar(".upload-remover")
    assert not previa_visivel(tela)
    assert visivel(tela, 0, "upload-selecionar") and visivel(tela, 0, "upload-dica")
    assert segunda in tela.js("window.__revogadas")
    assert tela.js("window.ImagensReferencia.arquivos()") == [None]
    assert not tela.js(f"{parte(0, 'upload-miniatura')}.hasAttribute('src')")
    assert tela.js("document.activeElement.className.includes('upload-selecionar')")


def test_botao_trocar_abre_o_seletor(seletor_interceptado, tela):
    novo_arquivo(tela, "primeira.png")
    soltar(tela)
    esperar_previa(tela, "primeira.png")

    tela.clicar(".upload-trocar")
    tela.evento("Page.fileChooserOpened")


def test_pedido_com_imagem_em_um_item_e_sem_no_outro_e_salvo_e_limpa(tela):
    preencher_dois_itens(tela)
    novo_arquivo(tela, "referencia.png")
    soltar(tela, 0)  # o item 2 fica sem imagem
    esperar_previa(tela, "referencia.png", 0)
    assert arquivos_por_item(tela) == ["referencia.png", None]
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    # Depois do sucesso o formulário volta limpo, sem nenhuma imagem.
    assert not tela.js("[...document.querySelectorAll('.upload-miniatura')].some(i => !i.hidden)")
    assert tela.js("window.ImagensReferencia.arquivos().every(a => a === null)")
    assert not tela.js("!!document.querySelector('.upload-miniatura[src]')")


def test_nada_e_enviado_convertido_nem_guardado(tela):
    tela.js(
        "window.__rede = []; const f = window.fetch;"
        " window.fetch = (...a) => { window.__rede.push('fetch ' + a[0]); return f(...a) };"
        " const o = XMLHttpRequest.prototype.open;"
        " XMLHttpRequest.prototype.open = function (...a) { window.__rede.push('xhr ' + a[1]);"
        " return o.apply(this, a) };"
        " navigator.sendBeacon = (...a) => { window.__rede.push('beacon'); return false };"
        " window.__leituras = []; const l = FileReader.prototype.readAsDataURL;"
        " FileReader.prototype.readAsDataURL = function (...a) { window.__leituras.push(1);"
        " return l.apply(this, a) }"
    )
    novo_arquivo(tela, "privada.png")
    soltar(tela)
    esperar_previa(tela, "privada.png")

    assert tela.js("window.__rede") == []  # nenhuma chamada (Supabase ou qualquer outra)
    assert tela.js("window.__leituras") == []  # nada convertido em Base64
    assert tela.js("localStorage.length") == 0
    assert tela.js("sessionStorage.length") == 0
    assert tela.js(f"{parte(0, 'upload-miniatura')}.src.startsWith('data:')") is False
    # O formulário só carrega texto: o input de arquivo não tem nome e o arquivo não vai no envio.
    assert tela.js(f"{parte(0, 'upload-arquivo')}.hasAttribute('name')") is False
    assert tela.js(
        "[...new FormData(document.getElementById('form-pedido')).values()]"
        ".every(v => typeof v === 'string')"
    )
    assert "supabase" not in tela.js("document.documentElement.outerHTML.toLowerCase()")


def test_dois_itens_com_imagens_e_erros_cabem_em_1280x720(pagina):
    pagina.tela(1280, 720)
    pagina.abrir()
    preencher_dois_itens(pagina)
    assert pagina.js("document.documentElement.scrollHeight") <= 720
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "true")) == []

    # Duas imagens com nomes longos.
    for n, nome in enumerate(
        ["referencia-com-nome-longo-para-testar.png", "camiseta-frente-e-costas.jpg"]
    ):
        novo_arquivo(pagina, nome, extra=200 * 1024)
        soltar(pagina, n)
        esperar_previa(pagina, nome, n)
    assert pagina.js("document.documentElement.scrollHeight") <= 720
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "true")) == []

    # Erros nos dois itens, com as imagens válidas mantidas.
    for n in (0, 1):
        arquivo_falso(pagina, "documento.pdf", "application/pdf")
        soltar(pagina, n)
        esperar_erro(pagina, n)
    assert pagina.js("document.documentElement.scrollHeight") <= 720
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "true")) == []
    # A linha do item continua com a altura normal (o erro cabe no lugar do nome do arquivo).
    alturas = pagina.js(
        "[...document.querySelectorAll('#itens .item')].map(i => i.getBoundingClientRect().height)"
    )
    assert max(alturas) < 55


@pytest.mark.parametrize("largura", [360, 768])
def test_telas_menores_com_imagens_sem_rolagem_horizontal(pagina, largura):
    pagina.tela(largura, 800)
    pagina.abrir()
    pagina.clicar("#adicionar-item")
    nome = "nome-de-arquivo-extremamente-comprido-para-testar-o-layout.png"
    novo_arquivo(pagina, nome)
    soltar(pagina, 0)
    esperar_previa(pagina, nome, 0)
    arquivo_falso(pagina, "documento.pdf", "application/pdf")
    soltar(pagina, 1)
    esperar_erro(pagina, 1)

    assert pagina.js("document.documentElement.scrollWidth") <= largura
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "false")) == []
    # Botões visíveis da imagem têm alvo de toque confortável.
    alturas = pagina.js(
        "[...document.querySelectorAll('.upload-item button')].filter(b => b.offsetParent !== null)"
        ".map(b => b.getBoundingClientRect().height)"
    )
    assert alturas and min(alturas) >= 44

    pagina.clicar(".upload-remover")
    assert pagina.js("document.documentElement.scrollWidth") <= largura


# ---------- Gravação do pedido (repositório e Storage falsos) ----------


def imagem_no_item(tela, nome, n, tipo="image/png"):
    novo_arquivo(tela, nome, tipo)
    soltar(tela, n)
    esperar_previa(tela, nome, n)


def test_envio_grava_pedido_e_imagens_sem_recarregar(tela, repo_pedidos):
    preencher_dois_itens(tela)
    imagem_no_item(tela, "segundo.jpg", 1, "image/jpeg")
    tela.js("window.__mesma_pagina = true")
    tela.enviar()

    assert tela.js("window.__mesma_pagina") is True  # não recarregou
    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    pedido = next(iter(repo_pedidos.pedidos.values()))
    assert pedido.criado_por == 1
    assert pedido.itens[0].imagem_caminho is None
    caminho = pedido.itens[1].imagem_caminho
    assert caminho.startswith(f"pedidos/{pedido.id}/itens/2/") and caminho.endswith(".jpg")
    conteudo, tipo = repo_pedidos.arquivos[caminho]
    assert tipo == "image/jpeg" and conteudo[:3] == b"\xff\xd8\xff"
    # Formulário limpo, com uma linha vazia e sem imagens.
    assert tela.valor("#cliente") == ""
    assert tela.js("document.querySelectorAll('#itens .item').length") == 1
    assert tela.js("window.ImagensReferencia.arquivos()") == [None]
    assert not tela.js("!!document.querySelector('#form-pedido input:checked')")
    assert tela.texto("#resumo-total") == "R$ 0,00"
    assert tela.texto(".upload-selecionar") == "Selecionar imagem"


def test_erro_de_validacao_preserva_dados_e_imagens(tela, repo_pedidos):
    preencher_dois_itens(tela)
    imagem_no_item(tela, "primeiro.png", 0)
    imagem_no_item(tela, "segundo.webp", 1, "image/webp")
    tela.js("document.getElementById('cliente').value = ''")
    tela.enviar()

    assert tela.js("!!document.getElementById('cliente-erro')")
    assert tela.js("!!document.querySelector('.alerta-erro')")
    assert repo_pedidos.pedidos == {} and repo_pedidos.arquivos == {}
    assert arquivos_por_item(tela) == ["primeiro.png", "segundo.webp"]
    assert previa_visivel(tela, 0) and previa_visivel(tela, 1)
    assert tela.valor(item(1, "item_produto")) == "Caneca"
    assert tela.valor(item(2, "item_valor")) == "R$ 99,90"
    assert tela.js("document.querySelector('#grupo-pagamento input[value=PIX]').checked")

    # Corrigido o erro, o mesmo formulário salva com as duas imagens.
    tela.digitar("#cliente", "Maria Souza")
    assert not tela.js("!!document.getElementById('cliente-erro')")
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    pedido = next(iter(repo_pedidos.pedidos.values()))
    assert [i.imagem_caminho.rsplit(".", 1)[1] for i in pedido.itens] == ["png", "webp"]
    assert len(repo_pedidos.arquivos) == 2


def test_falha_ao_gravar_preserva_tudo_e_mostra_mensagem_simples(tela, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoGravar

    repo_pedidos.falhar_em["criar_pedido"] = FalhaAoGravar()
    preencher_dois_itens(tela)
    imagem_no_item(tela, "foto.png", 0)
    tela.enviar()

    assert tela.texto(".alerta-erro") == (
        "Não foi possível salvar o pedido agora. Tente novamente em instantes."
    )
    assert repo_pedidos.pedidos == {} and repo_pedidos.arquivos == {}
    assert len(repo_pedidos.removidos) == 1
    assert arquivos_por_item(tela) == ["foto.png", None]
    assert tela.valor("#cliente") == "Maria Souza"

    del repo_pedidos.falhar_em["criar_pedido"]  # o serviço volta: nova tentativa funciona
    tela.enviar()
    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    assert len(repo_pedidos.pedidos) == 1


def test_pagina_expirada_renova_o_token_e_permite_salvar(tela, repo_pedidos):
    preencher_dois_itens(tela)
    imagem_no_item(tela, "foto.png", 1)
    tela.js("document.getElementById('csrf-pedido').value = 'token-antigo'")
    tela.enviar()

    assert "A página expirou" in tela.texto(".alerta-erro")
    assert repo_pedidos.pedidos == {}
    assert tela.valor("#csrf-pedido") == "csrf-de-teste"
    assert arquivos_por_item(tela) == [None, "foto.png"]

    tela.enviar()
    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")


def test_botao_indica_envio_e_evita_duplo_clique(tela, repo_pedidos):
    preencher_dois_itens(tela)
    tela.js(
        "document.getElementById('form-pedido').dataset.envio = '';"
        " const b = document.querySelector('#form-pedido button[type=submit]');"
        " b.click(); window.__durante = b.textContent; b.click()"
    )
    esperar(lambda: tela.js("document.getElementById('form-pedido').dataset.envio === 'concluido'"))

    assert tela.js("window.__durante") == "Salvando…"
    assert tela.texto("#form-pedido button[type=submit]") == "Salvar pedido"
    assert len(repo_pedidos.pedidos) == 1


# ---------- Consulta de pedidos (repositório falso, sem Supabase) ----------


def pedidos_de_exemplo(repo, servidor, com_imagem=True):
    """Dois pedidos fictícios; o segundo com nome longo, observação longa e uma imagem."""
    from tests.test_pedidos_consulta import item, novo_pedido

    novo_pedido(repo, cliente="Ana", itens=[item(1, "Caneca", 11, "12.00")])
    caminho = "pedidos/2/itens/1/00000000-0000-4000-8000-000000000001.png"
    pedido = novo_pedido(
        repo,
        cliente="Cliente com um nome bem comprido para testar a quebra de linha na tela",
        itens=[
            item(
                1,
                "Produto com imagem e nome comprido para testar",
                2,
                "1234.56",
                caminho if com_imagem else None,
            ),
            item(2, "Chaveiro", 99999, "0.05"),
        ],
        observacoes="Observação longa " * 20,
        forma_pagamento="dinheiro",
        tipo_entrega="retirada",
    )
    repo.arquivos[caminho] = (b"", "image/png")
    # URL "assinada" falsa que o navegador consegue abrir: uma imagem do próprio servidor.
    repo.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    return pedido


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_listagem_sem_rolagem_horizontal_nem_cortes(pagina, servidor, repo_pedidos, largura):
    pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(largura, 800)
    pagina.abrir("/pedidos")

    assert pagina.js("document.querySelectorAll('.pedido-card').length") == 2
    assert pagina.js("document.documentElement.scrollWidth") <= largura
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "false")) == []
    # Botões com alvo de toque confortável.
    alturas = pagina.js(
        "[...document.querySelectorAll('main .botao')].map(b => b.getBoundingClientRect().height)"
    )
    assert alturas and min(alturas) >= 44


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_listagem_vazia_responsiva(pagina, largura):
    pagina.tela(largura, 800)
    pagina.abrir("/pedidos")

    assert pagina.texto(".lista-vazia .botao") == "Cadastrar primeiro pedido"
    assert pagina.js("document.documentElement.scrollWidth") <= largura


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_detalhe_sem_rolagem_horizontal_nem_cortes(pagina, servidor, repo_pedidos, largura):
    pedido = pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(largura, 800)
    pagina.abrir(f"/pedidos/{pedido.id}")
    # A miniatura carrega sob demanda (loading="lazy"): rola até ela, como a pessoa faria.
    pagina.js("document.querySelector('.detalhe-miniatura').scrollIntoView({block: 'center'})")
    esperar(
        lambda: pagina.js(
            "(() => { const i = document.querySelector('.detalhe-miniatura img');"
            " return i.complete && i.naturalWidth > 0 })()"
        )
    )
    pagina.js("window.scrollTo(0, 0)")

    assert pagina.js("document.documentElement.scrollWidth") <= largura
    assert pagina.js(CORTES.replace("LIMITE_VERTICAL", "false")) == []
    assert pagina.js("document.querySelector('.detalhe-miniatura img').naturalWidth") > 0
    assert pagina.js("document.querySelectorAll('.detalhe-miniatura').length") == 1


def test_item_sem_imagem_nao_reserva_espaco(pagina, servidor, repo_pedidos):
    pedido = pedidos_de_exemplo(repo_pedidos, servidor, com_imagem=False)
    pagina.tela(1280, 800)
    pagina.abrir(f"/pedidos/{pedido.id}")

    assert (
        pagina.js(
            "document.querySelectorAll('#imagem-ampliada img[src], .detalhe-item img').length"
        )
        == 0
    )
    # O nome do produto começa na borda da célula (nenhum espaço vazio antes dele).
    assert (
        pagina.js(
            "(() => { const c = document.querySelector('.detalhe-produto');"
            " return c.querySelector('span').getBoundingClientRect().left"
            " - c.getBoundingClientRect().left })()"
        )
        == 0
    )


def test_imagem_ampliada_abre_e_fecha(pagina, servidor, repo_pedidos):
    pedido = pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir(f"/pedidos/{pedido.id}")

    pagina.clicar(".detalhe-miniatura")
    assert pagina.js("document.getElementById('imagem-ampliada').open") is True
    assert pagina.js("location.pathname") == f"/pedidos/{pedido.id}"  # não saiu da página
    assert pagina.js("document.querySelector('#imagem-ampliada img').src").endswith(
        "/static/img/logo-forma3d-horizontal.png"
    )
    assert pagina.texto("#imagem-ampliada figcaption").startswith("Produto com imagem")
    esperar(lambda: pagina.js("document.querySelector('#imagem-ampliada img').naturalWidth > 0"))
    largura_ampliada = pagina.js(
        "document.querySelector('#imagem-ampliada img').getBoundingClientRect().width"
    )
    assert largura_ampliada > 56 * 4

    pagina.clicar("#imagem-ampliada button")
    esperar(lambda: pagina.js("!document.getElementById('imagem-ampliada').open"))
    assert pagina.js("document.activeElement.classList.contains('detalhe-miniatura')")
    esperar(
        lambda: not pagina.js("document.querySelector('#imagem-ampliada img').hasAttribute('src')")
    )


def test_pagina_404_responsiva(pagina):
    pagina.tela(360, 800)
    pagina.abrir("/pedidos/999")

    assert pagina.texto("h1") == "Pedido não encontrado"
    assert pagina.js("document.documentElement.scrollWidth") <= 360


# ---------- Status do pagamento (formulário) ----------


def test_status_pelo_teclado_e_acessivel(tela):
    tela.js("document.querySelector(\"input[name=status_pagamento][value='Pendente']\").focus()")
    tecla = {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39}
    tela.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **tecla)
    tela.cmd("Input.dispatchKeyEvent", type="keyUp", **tecla)

    assert tela.js("document.querySelector('[name=status_pagamento]:checked').value") == "Pago"
    # Nome acessível: o rótulo do radio é o card; o grupo é um fieldset com legend.
    assert (
        tela.js(
            "document.querySelector(\"input[name=status_pagamento][value='Pago']\").labels[0]"
            ".textContent.trim()"
        )
        == "Pago"
    )
    assert (
        tela.js(
            "document.querySelector('#grupo-status_pagamento').tagName"
            " + '|' + document.querySelector('#grupo-status_pagamento legend').textContent.trim()"
        )
        == "FIELDSET|Status do pagamento *"
    )
    # Selecionado: além da cor, o card ganha o símbolo ✓.
    assert (
        tela.js(
            "getComputedStyle(document.querySelector(\"input[name=status_pagamento][value='Pago']"
            " + .escolha-card\"), '::after').content"
        )
        == '"✓"'
    )
    assert tela.js(
        "getComputedStyle(document.querySelector(\"input[name=status_pagamento][value='Pendente']"
        " + .escolha-card\"), '::after').content"
    ) in ("none", "normal")


def test_status_marcado_no_navegador_antes_da_resposta(tela):
    # A resposta do servidor nunca chega: o aviso visto vem só da validação no navegador.
    tela.js("window.fetch = () => new Promise(() => {})")
    tela.js("document.querySelector('#form-pedido button[type=submit]').click()")

    assert tela.texto("#status_pagamento-erro") == "Escolha o status do pagamento."
    assert tela.js(
        "document.getElementById('grupo-status_pagamento').classList.contains('tem-erro')"
    )
    assert (
        tela.js(
            "document.getElementById('grupo-status_pagamento').getAttribute('aria-describedby')"
        )
        == "status_pagamento-erro"
    )


def test_status_preservado_no_erro_e_limpo_no_sucesso(tela, repo_pedidos):
    preencher_dois_itens(tela)
    tela.clicar("#grupo-status_pagamento input[value='Pago']")
    tela.js("document.getElementById('cliente').value = ''")
    tela.enviar()

    assert tela.js("!!document.getElementById('cliente-erro')")
    assert tela.js("document.querySelector('[name=status_pagamento]:checked').value") == "Pago"
    assert not tela.js("!!document.getElementById('status_pagamento-erro')")

    tela.digitar("#cliente", "Maria Souza")
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    assert next(iter(repo_pedidos.pedidos.values())).status_pagamento == "pago"
    assert tela.js("document.querySelectorAll('[name=status_pagamento]:checked').length") == 0


def test_erro_do_status_some_ao_escolher(tela):
    tela.enviar()
    assert tela.js("!!document.getElementById('status_pagamento-erro')")

    tela.clicar("#grupo-status_pagamento input[value='Pendente']")

    assert not tela.js("!!document.getElementById('status_pagamento-erro')")
    assert not tela.js(
        "document.getElementById('grupo-status_pagamento').hasAttribute('aria-describedby')"
    )


def test_celular_secoes_empilhadas_na_ordem(pagina):
    pagina.tela(360, 800)
    pagina.abrir()
    caixas = pagina.js(
        "['#grupo-pagamento', '#grupo-status_pagamento', '.secao-obs', '#grupo-entrega',"
        " '.resumo'].map(s => { const r = document.querySelector(s).getBoundingClientRect();"
        " return [Math.round(r.left), Math.round(r.right), Math.round(r.top)] })"
    )

    assert len({(c[0], c[1]) for c in caixas}) == 1  # mesma largura, uma embaixo da outra
    assert [c[2] for c in caixas] == sorted(c[2] for c in caixas)
    alturas = pagina.js(
        "[...document.querySelectorAll('#grupo-status_pagamento .escolha-card')]"
        ".map(c => c.getBoundingClientRect().height)"
    )
    assert min(alturas) >= 44
    assert pagina.js("document.documentElement.scrollWidth") <= 360


@pytest.mark.parametrize("largura", [768, 1280])
def test_alvos_do_status_e_da_entrega_com_44px(pagina, largura):
    pagina.tela(largura, 800)
    pagina.abrir()
    alturas = pagina.js(
        "[...document.querySelectorAll('#grupo-status_pagamento .escolha-card,"
        " #grupo-entrega .escolha-card')].map(c => c.getBoundingClientRect().height)"
    )

    assert len(alturas) == 4 and min(alturas) >= 44


# ---------- Card da listagem com foto ----------


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_foto_do_card_compacta_quadrada_e_sem_deformar(pagina, servidor, repo_pedidos, largura):
    pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(largura, 800)
    pagina.abrir("/pedidos")
    pagina.js("document.querySelector('.pedido-card-foto').scrollIntoView({block: 'center'})")
    esperar(lambda: pagina.js("document.querySelector('.pedido-card-foto').naturalWidth > 0"))

    foto = pagina.js(
        "(() => { const i = document.querySelector('.pedido-card-foto');"
        " const r = i.getBoundingClientRect();"
        " return [r.width, r.height, getComputedStyle(i).objectFit, i.alt] })()"
    )
    largura_foto, altura_foto, ajuste, alternativo = foto
    assert ajuste == "cover"
    assert abs(largura_foto - altura_foto) < 1  # quadrada, recorte sem distorção
    assert 60 <= largura_foto <= 120  # compacta
    assert alternativo.startswith("Foto de referência: ")
    # Só o card com imagem tem foto; o outro não tem espaço reservado.
    assert pagina.js("document.querySelectorAll('.pedido-card-foto').length") == 1
    assert pagina.js(
        "[...document.querySelectorAll('.pedido-card')].filter(c => !c.querySelector('img'))"
        ".every(c => c.querySelector('.pedido-card-corpo').getBoundingClientRect().left"
        " - c.getBoundingClientRect().left < 20)"
    )
    assert pagina.js("document.documentElement.scrollWidth") <= largura


# ---------- Produção (quadro Kanban; repositório falso) ----------


def pedidos_producao(repo, servidor, quantidade=1):
    from tests.test_pedidos_consulta import item, novo_pedido

    pedidos = [
        novo_pedido(
            repo,
            cliente=f"Cliente {n}",
            itens=[item(1, "Porta doce com nome comprido para testar", 11, "12.00")],
            observacoes="7 Azuis\n4 Rosa Choque" if n == 1 else None,
        )
        for n in range(1, quantidade + 1)
    ]
    repo.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    return pedidos


def card_js(pedido_id):
    return f"document.querySelector('.producao-card[data-pedido=\"{pedido_id}\"]')"


def expandir_todos(p):
    """Os cards começam recolhidos; expande todos (pelo próprio botão de cada card)."""
    p.js(
        "document.querySelectorAll('.producao-recolher[aria-expanded=\"false\"]')"
        ".forEach(b => b.click())"
    )


def etapa_na_tela(p, pedido_id):
    return p.js(f"{card_js(pedido_id)}.closest('.coluna').dataset.etapa")


def contadores(p):
    return p.js("[...document.querySelectorAll('[data-contador]')].map(c => Number(c.textContent))")


def clicar_avancar(p, pedido_id):
    p.js(f"{card_js(pedido_id)}.querySelector('.producao-avancar').click()")


def clicar_voltar(p, pedido_id):
    p.js(f"{card_js(pedido_id)}.querySelector('.producao-voltar').click()")


def esperar_mensagem(p):
    esperar(
        lambda: p.js("!!document.querySelector('#producao-mensagem .alerta')"),
        mensagem="mensagem da movimentação não apareceu",
    )
    return p.texto("#producao-mensagem .alerta")


def arrastar_card(p, pedido_id, etapa_destino):
    """Simula o arraste (dragstart, dragover e drop); devolve [destacada, aceita]."""
    return p.js(
        "(() => { const card = " + card_js(pedido_id) + ";"
        " const destino = document.querySelector('.coluna[data-etapa=\"" + etapa_destino + "\"]');"
        " const dt = new DataTransfer();"
        " card.dispatchEvent(new DragEvent('dragstart', {dataTransfer: dt, bubbles: true}));"
        " const valido = destino.classList.contains('destino-valido');"
        " const over = new DragEvent('dragover',"
        " {dataTransfer: dt, bubbles: true, cancelable: true});"
        " destino.dispatchEvent(over);"
        " const aceito = over.defaultPrevented;"
        " destino.dispatchEvent(new DragEvent('drop',"
        " {dataTransfer: dt, bubbles: true, cancelable: true}));"
        " card.dispatchEvent(new DragEvent('dragend', {dataTransfer: dt, bubbles: true}));"
        " return [valido, aceito] })()"
    )


@pytest.mark.parametrize("largura", [360, 768, 1024, 1280])
def test_producao_responsiva_sem_rolagem_horizontal(pagina, servidor, repo_pedidos, largura):
    pedidos = pedidos_producao(repo_pedidos, servidor, 3)
    repo_pedidos.etapas[pedidos[1].id] = "em_producao"  # card com os três botões
    pagina.tela(largura, 800)
    pagina.abrir("/producao")

    colunas = pagina.js(
        "[...document.querySelectorAll('.coluna')].map(c => { const r = c.getBoundingClientRect();"
        " return [Math.round(r.left), Math.round(r.top),"
        " Math.round(r.width), Math.round(r.height)] })"
    )
    assert len(colunas) == 4
    assert pagina.js("document.documentElement.scrollWidth") <= largura
    if largura >= 1024:
        assert len({c[1] for c in colunas}) == 1  # lado a lado
        assert len({c[3] for c in colunas}) == 1  # mesma altura
        assert [c[0] for c in colunas] == sorted(c[0] for c in colunas)
    if largura == 360:
        assert len({c[0] for c in colunas}) == 1  # empilhadas
        assert [c[1] for c in colunas] == sorted(c[1] for c in colunas)
        card = pagina.js(
            "(() => { const c = document.querySelector('.producao-card').getBoundingClientRect(),"
            " col = document.querySelector('.coluna').getBoundingClientRect();"
            " return [c.width, col.width] })()"
        )
        assert card[1] - card[0] < 30  # card com a largura da coluna
    alturas = pagina.js(
        "[...document.querySelectorAll('main .botao')].filter(b => b.offsetParent)"
        ".map(b => b.getBoundingClientRect().height)"
    )
    assert alturas and min(alturas) >= 44
    # A lista de cards de cada coluna rola por dentro no desktop (de propósito); nada mais corta.
    cortes = pagina.js(CORTES.replace("LIMITE_VERTICAL", "false"))
    assert [c for c in cortes if not c.startswith("OL.coluna-cards")] == []


@pytest.mark.parametrize("largura", [360, 768, 1280])
def test_card_da_producao_com_prazo_e_foto_sem_deformar(pagina, servidor, repo_pedidos, largura):
    from tests.test_pedidos_consulta import item, novo_pedido

    caminho = "pedidos/1/itens/2/00000000-0000-4000-8000-000000000002.png"
    novo_pedido(
        repo_pedidos,
        cliente="Cliente com foto",
        itens=[item(1, "Pequeno", 1, "1.00"), item(2, "Maior", 9, "1.00", caminho)],
    )
    repo_pedidos.arquivos[caminho] = (b"\x89PNG", "image/png")
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    pagina.tela(largura, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    pagina.js("document.querySelector('.producao-card-foto').scrollIntoView({block: 'center'})")
    esperar(lambda: pagina.js("document.querySelector('.producao-card-foto').naturalWidth > 0"))

    largura_foto, altura_foto, ajuste, alternativo = pagina.js(
        "(() => { const i = document.querySelector('.producao-card-foto');"
        " const r = i.getBoundingClientRect();"
        " return [r.width, r.height, getComputedStyle(i).objectFit, i.alt] })()"
    )
    assert ajuste == "cover" and abs(largura_foto - altura_foto) < 1
    assert alternativo == "Foto de referência: Maior"
    card = pagina.js("document.querySelector('.producao-card').innerText")
    assert "Prazo\n05/10/26" in card and "Cadastrado por" not in card
    assert pagina.js("document.documentElement.scrollWidth") <= largura


def test_foto_que_falha_ao_carregar_some_sem_deixar_espaco(pagina, servidor, repo_pedidos):
    from tests.test_pedidos_consulta import item, novo_pedido

    caminho = "pedidos/1/itens/1/00000000-0000-4000-8000-000000000001.png"
    novo_pedido(repo_pedidos, itens=[item(1, "Caneca", 2, "1.00", caminho)])
    repo_pedidos.arquivos[caminho] = (b"\x89PNG", "image/png")
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/nao-existe.png" for c in caminhos
    }
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    # A foto é "lazy": rolar até ela faz o navegador carregá-la (e receber o erro).
    pagina.js("document.querySelector('.producao-card-foto')?.scrollIntoView({block: 'center'})")
    esperar(lambda: not pagina.js("!!document.querySelector('.producao-card-foto')"))
    assert pagina.js("document.querySelector('.producao-card h3').textContent") == "Cliente Teste"
    assert (
        pagina.js(
            "document.querySelector('.producao-card-topo h3').getBoundingClientRect().left"
            " - document.querySelector('.producao-card').getBoundingClientRect().left"
        )
        < 20
    )


def test_producao_muitos_cards_rolam_dentro_da_coluna(pagina, servidor, repo_pedidos):
    pedidos_producao(repo_pedidos, servidor, 12)
    pagina.tela(1280, 720)
    pagina.abrir("/producao")

    lista = pagina.js(
        "(() => { const l = document.querySelector("
        "'.coluna[data-etapa=fila_producao] .coluna-cards');"
        " return [getComputedStyle(l).overflowY, l.scrollHeight > l.clientHeight] })()"
    )
    assert lista == ["auto", True]
    assert pagina.js("document.documentElement.scrollWidth") <= 1280
    alturas = pagina.js(
        "[...document.querySelectorAll('.coluna')]"
        ".map(c => Math.round(c.getBoundingClientRect().height))"
    )
    assert len(set(alturas)) == 1


def test_avancar_pelo_botao_move_o_card_e_mantem_o_foco(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    assert contadores(pagina) == [1, 0, 0, 0]

    assert pagina.texto("#coluna-fila_producao") == "Na Fila de Produção"
    clicar_avancar(pagina, pedido.id)
    texto = esperar_mensagem(pagina)

    assert texto == "Pedido de Cliente 1 movido para Em Produção."
    assert (
        pagina.js("document.querySelector('#producao-mensagem .alerta').getAttribute('role')")
        == "status"
    )
    assert etapa_na_tela(pagina, pedido.id) == "em_producao"
    assert contadores(pagina) == [0, 1, 0, 0]
    assert repo_pedidos.etapas[pedido.id] == "em_producao"
    assert repo_pedidos.movimentos[-1][3] == 1  # usuário da sessão
    foco = pagina.js("document.activeElement.textContent.trim()")
    assert foco == "Finalizar produção"
    assert pagina.js(f"{card_js(pedido.id)}.contains(document.activeElement)")
    assert not pagina.js(
        "document.querySelector('.coluna[data-etapa=fila_producao] .coluna-vazia').hidden"
    )
    # Em Produção, o card ganha "Voltar para fila" (secundário) antes de "Finalizar produção".
    botoes = pagina.js(
        f"[...{card_js(pedido.id)}.querySelectorAll('.producao-mover')]"
        ".map(b => [b.textContent, b.dataset.destino, b.className])"
    )
    assert [b[:2] for b in botoes] == [
        ["Voltar para fila", "fila_producao"],
        ["Finalizar produção", "aguardando_entrega"],
    ]
    assert "botao-secundario" in botoes[0][2] and "botao-primario" in botoes[1][2]


def test_voltar_para_fila_pelo_botao(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.etapas[pedido.id] = "em_producao"
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    assert contadores(pagina) == [0, 1, 0, 0]
    voltar = f"{card_js(pedido.id)}.querySelector('.producao-voltar')"
    estilo = pagina.js(f"getComputedStyle({voltar}).backgroundColor")
    assert estilo == pagina.js(
        "getComputedStyle(document.querySelector('.botao-secundario')).backgroundColor"
    )

    clicar_voltar(pagina, pedido.id)
    texto = esperar_mensagem(pagina)

    assert texto == "Pedido de Cliente 1 movido para Na Fila de Produção."
    assert etapa_na_tela(pagina, pedido.id) == "fila_producao"
    assert contadores(pagina) == [1, 0, 0, 0]
    assert repo_pedidos.etapas[pedido.id] == "fila_producao"
    assert repo_pedidos.movimentos == [(pedido.id, "em_producao", "fila_producao", 1)]
    assert not pagina.js(f"!!{voltar}")  # na fila não há retorno
    assert pagina.js("document.activeElement.textContent.trim()") == "Iniciar produção"
    assert pagina.js(f"{card_js(pedido.id)}.contains(document.activeElement)")
    assert not pagina.js("!!document.getElementById('confirmar-entrega').open")


def test_ida_e_volta_e_avanco_ate_entregue(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    for acao, etapa in [
        (clicar_avancar, "em_producao"),
        (clicar_voltar, "fila_producao"),
        (clicar_avancar, "em_producao"),
        (clicar_avancar, "aguardando_entrega"),
    ]:
        pagina.js("document.getElementById('producao-mensagem').replaceChildren()")
        acao(pagina, pedido.id)
        esperar_mensagem(pagina)
        assert etapa_na_tela(pagina, pedido.id) == etapa
    assert not pagina.js(f"!!{card_js(pedido.id)}.querySelector('.producao-voltar')")
    assert [m[1:3] for m in repo_pedidos.movimentos] == [
        ("fila_producao", "em_producao"),
        ("em_producao", "fila_producao"),
        ("fila_producao", "em_producao"),
        ("em_producao", "aguardando_entrega"),
    ]


def test_avancar_pelo_teclado(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    pagina.js(f"{card_js(pedido.id)}.querySelector('.producao-avancar').focus()")
    tecla = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}
    pagina.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **tecla)
    pagina.cmd("Input.dispatchKeyEvent", type="char", text="\r")
    pagina.cmd("Input.dispatchKeyEvent", type="keyUp", **tecla)

    esperar_mensagem(pagina)
    assert etapa_na_tela(pagina, pedido.id) == "em_producao"


def test_arrastar_para_a_proxima_coluna(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    valido, aceito = arrastar_card(pagina, pedido.id, "em_producao")

    assert valido and aceito
    esperar_mensagem(pagina)
    assert etapa_na_tela(pagina, pedido.id) == "em_producao"
    assert contadores(pagina) == [0, 1, 0, 0]
    assert not pagina.js("!!document.querySelector('.destino-valido, .destino-ativo, .arrastando')")


@pytest.mark.parametrize(
    ("origem", "destino"),
    [
        ("fila_producao", "aguardando_entrega"),
        ("fila_producao", "entregue"),
        ("fila_producao", "fila_producao"),
        ("em_producao", "entregue"),
        ("em_producao", "em_producao"),
        ("aguardando_entrega", "em_producao"),
        ("aguardando_entrega", "fila_producao"),
    ],
)
def test_arrastar_para_coluna_invalida_nao_move(pagina, servidor, repo_pedidos, origem, destino):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.etapas[pedido.id] = origem
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    valido, aceito = arrastar_card(pagina, pedido.id, destino)

    assert not valido and not aceito
    assert etapa_na_tela(pagina, pedido.id) == origem
    assert repo_pedidos.movimentos == []
    assert not pagina.js("!!document.querySelector('#producao-mensagem .alerta')")


@pytest.mark.parametrize("destino", ["fila_producao", "aguardando_entrega"])
def test_arrastar_de_em_producao_para_a_fila_ou_para_frente(
    pagina, servidor, repo_pedidos, destino
):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.etapas[pedido.id] = "em_producao"
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    destacadas = pagina.js(
        "(() => { const card = " + card_js(pedido.id) + "; const dt = new DataTransfer();"
        " card.dispatchEvent(new DragEvent('dragstart', {dataTransfer: dt, bubbles: true}));"
        " const r = [...document.querySelectorAll('.coluna.destino-valido')]"
        ".map(c => c.dataset.etapa);"
        " card.dispatchEvent(new DragEvent('dragend', {dataTransfer: dt, bubbles: true}));"
        " return r })()"
    )
    assert destacadas == ["fila_producao", "aguardando_entrega"]

    valido, aceito = arrastar_card(pagina, pedido.id, destino)

    assert valido and aceito
    esperar_mensagem(pagina)
    assert etapa_na_tela(pagina, pedido.id) == destino
    assert repo_pedidos.movimentos == [(pedido.id, "em_producao", destino, 1)]
    assert not pagina.js("!!document.getElementById('confirmar-entrega').open")


def test_entregue_pede_confirmacao(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.etapas[pedido.id] = "aguardando_entrega"
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)

    clicar_avancar(pagina, pedido.id)
    assert pagina.js("document.getElementById('confirmar-entrega').open") is True
    assert "Cliente 1" in pagina.texto("#confirmar-entrega-texto")
    assert pagina.js("document.activeElement.value") == "cancelar"  # foco na opção segura

    pagina.clicar("#confirmar-entrega button[value=cancelar]")
    esperar(lambda: not pagina.js("document.getElementById('confirmar-entrega').open"))
    assert repo_pedidos.movimentos == []
    assert etapa_na_tela(pagina, pedido.id) == "aguardando_entrega"

    clicar_avancar(pagina, pedido.id)
    pagina.clicar("#confirmar-entrega button[value=confirmar]")
    esperar_mensagem(pagina)

    assert etapa_na_tela(pagina, pedido.id) == "entregue"
    assert pagina.js(f"!!{card_js(pedido.id)}.querySelector('.producao-avancar')") is False
    assert pagina.js(f"{card_js(pedido.id)}.hasAttribute('draggable')") is False
    assert pagina.js("document.activeElement.textContent.trim()") == "Ver pedido"
    assert repo_pedidos.etapas[pedido.id] == "entregue"


def test_conflito_recarrega_o_quadro_com_aviso(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    repo_pedidos.etapas[pedido.id] = (
        "em_producao"  # outro usuário moveu enquanto a tela estava aberta
    )

    clicar_avancar(pagina, pedido.id)
    esperar(
        lambda: (
            pagina.texto("#producao-mensagem") == "Este pedido foi atualizado por outro usuário."
        )
    )

    assert (
        pagina.js("document.querySelector('#producao-mensagem .alerta').getAttribute('role')")
        == "alert"
    )
    assert etapa_na_tela(pagina, pedido.id) == "em_producao"  # quadro recarregado
    assert contadores(pagina) == [0, 1, 0, 0]
    assert repo_pedidos.movimentos == []
    assert pagina.js("document.activeElement.textContent.trim()") == "Finalizar produção"


def test_falha_ao_mover_mantem_o_card_e_avisa(pagina, servidor, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoMover

    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.falhar_em["mover_etapa"] = FalhaAoMover()
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)

    arrastar_card(pagina, pedido.id, "em_producao")
    texto = esperar_mensagem(pagina)

    assert texto == "Não foi possível mover o pedido agora. Tente novamente."
    assert etapa_na_tela(pagina, pedido.id) == "fila_producao"
    assert contadores(pagina) == [1, 0, 0, 0]
    botao = f"{card_js(pedido.id)}.querySelector('.producao-avancar')"
    assert pagina.js(f"{botao}.disabled") is False
    assert pagina.js(f"{botao}.textContent.trim()") == "Iniciar produção"
    assert pagina.js(f"document.activeElement === {botao}")


def test_falha_ao_recarregar_mostra_tentar_novamente(pagina, servidor, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoConsultar

    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    repo_pedidos.etapas[pedido.id] = "em_producao"
    repo_pedidos.falhar_em["listar_producao"] = FalhaAoConsultar()

    clicar_avancar(pagina, pedido.id)
    esperar(lambda: pagina.js("!!document.querySelector('.producao-tentar')"))

    assert "Não foi possível carregar a produção agora." in pagina.texto("#producao-mensagem")
    del repo_pedidos.falhar_em["listar_producao"]
    pagina.clicar(".producao-tentar")
    esperar(lambda: etapa_na_tela(pagina, pedido.id) == "em_producao")
    assert not pagina.js("!!document.querySelector('.producao-tentar')")


# ---------- Produção: largura, recolher, comentário e pagamento (repositório falso) ----------


def tecla(p, nome, codigo, vk, texto=None):
    dados = {"key": nome, "code": codigo, "windowsVirtualKeyCode": vk}
    p.cmd("Input.dispatchKeyEvent", type="rawKeyDown", **dados)
    if texto:
        p.cmd("Input.dispatchKeyEvent", type="char", text=texto)
    p.cmd("Input.dispatchKeyEvent", type="keyUp", **dados)


def linhas_e_colunas(p):
    """Quantas colunas do quadro ficam em cada linha da tela (pelo topo de cada uma)."""
    topos = p.js(
        "[...document.querySelectorAll('.coluna')]"
        ".map(c => Math.round(c.getBoundingClientRect().top))"
    )
    return [topos.count(t) for t in sorted(set(topos))]


def lento(repo, metodo, segundos=0.6):
    """Atrasa a resposta do repositório falso para observar a tela durante a operação."""
    original = getattr(repo, metodo)

    def atrasado(*args):
        time.sleep(segundos)
        return original(*args)

    setattr(repo, metodo, atrasado)


def momento_exemplo():
    from datetime import datetime

    return datetime(2026, 10, 1, 14, 32)


@pytest.mark.parametrize(
    ("largura", "esperado"),
    [(360, [1, 1, 1, 1]), (768, [2, 2]), (1000, [2, 2]), (1024, [4]), (1280, [4]), (1920, [4])],
)
def test_producao_colunas_por_largura(pagina, servidor, repo_pedidos, largura, esperado):
    pedidos_producao(repo_pedidos, servidor, 2)
    pagina.tela(largura, 800)
    pagina.abrir("/producao")

    assert linhas_e_colunas(pagina) == esperado
    assert pagina.js("document.documentElement.scrollWidth") <= largura


@pytest.mark.parametrize("largura", [360, 768, 1024, 1280, 1920, 2560])
def test_producao_larga_sem_rolagem_horizontal(pagina, servidor, repo_pedidos, largura):
    pedidos = pedidos_producao(repo_pedidos, servidor, 3)
    repo_pedidos.etapas[pedidos[1].id] = "em_producao"
    repo_pedidos.comentarios[pedidos[0].id] = ("Comentário comprido " * 3, momento_exemplo(), 2)
    pagina.tela(largura, 900)
    pagina.abrir("/producao")

    assert pagina.js("document.documentElement.scrollWidth") <= largura
    esquerda, direita = pagina.js(
        "(() => { const r = document.getElementById('quadro').getBoundingClientRect();"
        " return [r.left, window.innerWidth - r.right] })()"
    )
    assert esquerda >= 15.5 and direita >= 15.5  # margens laterais seguras
    cortes = pagina.js(CORTES.replace("LIMITE_VERTICAL", "false"))
    assert [c for c in cortes if not c.startswith("OL.coluna-cards")] == []


def test_producao_usa_quase_toda_a_largura_e_as_outras_telas_nao(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    largura_main = "document.querySelector('main').getBoundingClientRect().width"

    pagina.tela(1920, 900)
    pagina.abrir("/producao")
    quadro_1920 = pagina.js("document.getElementById('quadro').getBoundingClientRect().width")
    pagina.tela(2560, 900)
    pagina.abrir("/producao")
    main_2560 = pagina.js(largura_main)
    larguras = {}
    for caminho in ("/pedidos", "/pedidos/novo", f"/pedidos/{pedido.id}"):
        pagina.abrir(caminho)
        larguras[caminho] = pagina.js(largura_main)

    assert quadro_1920 >= 1920 - 2 * 40  # quase toda a janela
    assert main_2560 == 1920  # largura máxima da Produção
    assert all(valor <= 1080 for valor in larguras.values()), larguras


# Card recolhido: o que continua à vista e o que só aparece expandido.
SEMPRE_VISIVEIS = [
    "h3",
    ".producao-card-foto",
    ".producao-card-produtos",
    ".pedido-card-unidades",
    ".producao-comentario label",
    ".producao-comentario-campo",
    ".producao-salvar-comentario",
    ".producao-comentario-info",
    ".producao-recolher",
]
SO_NO_EXPANDIDO = [
    ".producao-card-dados",
    ".producao-pagamento",
    ".producao-card-prazo",
    ".producao-card-obs",
    ".producao-card-acoes",
    "a.botao",
    ".producao-mover",
]


def visivel_no_card(p, pedido_id, seletor):
    return p.js(
        f"(() => {{ const e = {card_js(pedido_id)}.querySelector({json.dumps(seletor)});"
        " return !!e && e.getClientRects().length > 0 })()"
    )


def test_recolher_e_expandir_cada_card(pagina, servidor, repo_pedidos):
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    foto = com_imagem(repo_pedidos, 1, 1)
    primeiro = novo_pedido(
        repo_pedidos,
        cliente="Ana",
        itens=[item(1, "Caneca", 2, "1.00", foto), item(2, "Copo", 3, "1.00")],
        observacoes="Observação original",
    )
    segundo = novo_pedido(repo_pedidos, cliente="Bia")
    repo_pedidos.comentarios[primeiro.id] = ("Pintar de azul", momento_exemplo(), 2)
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    escritas = repo_pedidos.escritas
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    card = card_js(primeiro.id)
    botao = f"{card}.querySelector('.producao-recolher')"
    outro = f"{card_js(segundo.id)}.querySelector('.producao-recolher')"

    def conferir_recolhido():
        assert pagina.js(f"{card}.classList.contains('recolhido')")
        assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"
        assert pagina.js(f"{botao}.textContent.trim()") == "Expandir pedido"
        assert pagina.js(f"{botao}.title") == "Expandir pedido"
        assert pagina.js(f"document.getElementById('card-corpo-{primeiro.id}').hidden") is True
        assert pagina.js(f"document.getElementById('card-acoes-{primeiro.id}').hidden") is True
        for seletor in SEMPRE_VISIVEIS:
            assert visivel_no_card(pagina, primeiro.id, seletor), seletor
        for seletor in SO_NO_EXPANDIDO:
            assert not visivel_no_card(pagina, primeiro.id, seletor), seletor
        visivel = [linha.strip() for linha in pagina.js(f"{card}.innerText").split("\n")]
        assert [linha for linha in visivel if linha] == [
            "Ana",
            "Expandir pedido",
            "Caneca, Copo 5 unidades",
            "Comentários da produção",
            "Ocultar comentário",
            "Atualizado por Kassia em 01/10/2026 às 14:32",
            "Salvar comentário",
        ]
        assert pagina.js(f"{card}.querySelector('textarea').value") == "Pintar de azul"

    # Ao abrir a página, todos os cards estão recolhidos.
    assert (
        pagina.js(f"{botao}.getAttribute('aria-controls')")
        == f"card-corpo-{primeiro.id} card-acoes-{primeiro.id}"
    )
    conferir_recolhido()
    assert pagina.js(f"{outro}.getAttribute('aria-expanded')") == "false"

    pagina.js(f"{botao}.click()")

    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "true"
    assert pagina.js(f"{botao}.textContent.trim()") == "Recolher pedido"
    assert pagina.js(f"{botao}.title") == "Recolher pedido"
    texto = pagina.js(f"{card}.innerText")
    for parte in ("Prazo", "Pagamento", "Entrega", "Observação original", "Ver pedido"):
        assert parte in texto, parte
    assert pagina.js(f"{card}.querySelector('textarea').value") == "Pintar de azul"
    assert pagina.js(f"{card}.querySelector('.producao-card-foto').offsetParent") is not None
    # O outro card continua recolhido.
    assert pagina.js(f"{outro}.getAttribute('aria-expanded')") == "false"
    assert "Ver pedido" not in pagina.js(f"{card_js(segundo.id)}.innerText")

    pagina.js(f"{botao}.click()")
    conferir_recolhido()

    # Expandido e recarregado: volta recolhido (nada é guardado).
    pagina.js(f"{botao}.click()")
    pagina.abrir("/producao")
    conferir_recolhido()
    # Nada mudou: mesma etapa, nenhum dado gravado.
    assert etapa_na_tela(pagina, primeiro.id) == "fila_producao"
    assert repo_pedidos.escritas == escritas and repo_pedidos.movimentos == []
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.alteracoes_pagamento == []


def test_recolher_pelo_teclado_com_foco_visivel(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    botao = f"{card_js(pedido.id)}.querySelector('.producao-recolher')"
    pagina.js(f"{botao}.focus()")

    # Começa recolhido: Enter expande, Espaço recolhe, Espaço expande de novo.
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"
    tecla(pagina, "Enter", "Enter", 13, "\r")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "true"
    assert visivel_no_card(pagina, pedido.id, ".producao-card-acoes")
    tecla(pagina, " ", "Space", 32, " ")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"
    assert not visivel_no_card(pagina, pedido.id, ".producao-card-acoes")
    tecla(pagina, " ", "Space", 32, " ")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "true"

    assert pagina.js(f"document.activeElement === {botao}")
    assert pagina.js(f"{botao}.matches(':focus-visible')")
    assert pagina.js(f"getComputedStyle({botao}).outlineStyle") == "solid"
    altura, largura = pagina.js(
        f"(() => {{ const r = {botao}.getBoundingClientRect(); return [r.height, r.width] }})()"
    )
    assert altura >= 44 and largura >= 44


def test_estado_do_card_continua_ao_mover_e_ao_atualizar_o_quadro(pagina, servidor, repo_pedidos):
    pedidos = pedidos_producao(repo_pedidos, servidor, 3)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    recolhido = "{}.classList.contains('recolhido')"

    # Recolhido (padrão) movido pelo arraste: continua recolhido, foco no botão de expandir.
    arrastar_card(pagina, pedidos[0].id, "em_producao")
    esperar_mensagem(pagina)
    assert etapa_na_tela(pagina, pedidos[0].id) == "em_producao"
    assert pagina.js(recolhido.format(card_js(pedidos[0].id)))
    assert pagina.js("document.activeElement.classList.contains('producao-recolher')")

    # Conflito em outro card: o quadro é recarregado; o expandido continua expandido e os
    # demais continuam recolhidos.
    pagina.js(f"{card_js(pedidos[2].id)}.querySelector('.producao-recolher').click()")
    repo_pedidos.etapas[pedidos[1].id] = "em_producao"
    clicar_avancar(pagina, pedidos[1].id)
    esperar(lambda: "outro usuário" in pagina.texto("#producao-mensagem"))
    assert not pagina.js(recolhido.format(card_js(pedidos[2].id)))
    assert pagina.js(f"document.getElementById('card-corpo-{pedidos[2].id}').hidden") is False
    assert pagina.js(recolhido.format(card_js(pedidos[0].id)))
    assert pagina.js(recolhido.format(card_js(pedidos[1].id)))


# ---------- Comentários da produção ----------


def comentario_js(pedido_id):
    return f"{card_js(pedido_id)}.querySelector('.producao-comentario-campo')"


def escrever_comentario(p, pedido_id, texto):
    p.js(f"{comentario_js(pedido_id)}.value = ''; {comentario_js(pedido_id)}.focus()")
    p.cmd("Input.insertText", text=texto)


def salvar_comentario(p, pedido_id):
    p.js(f"{card_js(pedido_id)}.querySelector('.producao-salvar-comentario').click()")


def mensagem_do_card(p, pedido_id):
    seletor = f"{card_js(pedido_id)}.querySelector('.producao-comentario-mensagem .alerta')"
    esperar(lambda: p.js(f"!!{seletor}"), mensagem="mensagem do comentário não apareceu")
    return p.js(f"[{seletor}.textContent, {seletor}.getAttribute('role')]")


def limpar_mensagem_do_card(p, pedido_id):
    p.js(f"{card_js(pedido_id)}.querySelector('.producao-comentario-mensagem').replaceChildren()")


def test_salvar_editar_e_apagar_comentario(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    obs = pagina.js(f"{card_js(pedido.id)}.querySelector('.producao-card-obs dd').textContent")
    rotulo = pagina.js(f"{comentario_js(pedido.id)}.labels[0].textContent")
    assert rotulo == "Comentários da produção"
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == ""

    escrever_comentario(pagina, pedido.id, "  Pintar de azul\nCom cuidado  ")
    salvar_comentario(pagina, pedido.id)

    assert mensagem_do_card(pagina, pedido.id) == ["Comentário salvo.", "status"]
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Pintar de azul\nCom cuidado"
    info = pagina.texto(f".producao-card[data-pedido='{pedido.id}'] .producao-comentario-info")
    assert info.startswith("Atualizado por Leandro em ")
    assert repo_pedidos.comentarios[pedido.id][0] == "Pintar de azul\nCom cuidado"

    limpar_mensagem_do_card(pagina, pedido.id)
    escrever_comentario(pagina, pedido.id, "Editado")
    salvar_comentario(pagina, pedido.id)
    assert mensagem_do_card(pagina, pedido.id)[0] == "Comentário salvo."
    limpar_mensagem_do_card(pagina, pedido.id)

    escrever_comentario(pagina, pedido.id, "   ")
    salvar_comentario(pagina, pedido.id)

    assert mensagem_do_card(pagina, pedido.id)[0] == "Comentário removido."
    assert repo_pedidos.comentarios[pedido.id][0] is None
    assert [a[1:3] for a in repo_pedidos.alteracoes_comentario] == [
        (None, "Pintar de azul\nCom cuidado"),
        ("Pintar de azul\nCom cuidado", "Editado"),
        ("Editado", None),
    ]
    # As observações do pedido não mudam e a etapa também não.
    assert obs == "7 Azuis\n4 Rosa Choque"
    assert repo_pedidos.pedidos[pedido.id].observacoes == "7 Azuis\n4 Rosa Choque"
    assert etapa_na_tela(pagina, pedido.id) == "fila_producao"

    pagina.abrir("/producao")
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == ""
    assert "Atualizado por Leandro" in pagina.js(f"{card_js(pedido.id)}.innerText")


def test_comentario_limitado_a_2000_caracteres(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    escrever_comentario(pagina, pedido.id, "x" * 2005)

    assert pagina.js(f"{comentario_js(pedido.id)}.maxLength") == 2000
    assert pagina.js(f"{comentario_js(pedido.id)}.value.length") == 2000
    salvar_comentario(pagina, pedido.id)
    assert mensagem_do_card(pagina, pedido.id)[0] == "Comentário salvo."
    assert len(repo_pedidos.comentarios[pedido.id][0]) == 2000


def test_salvar_comentario_desabilita_o_botao_durante_o_envio(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    lento(repo_pedidos, "salvar_comentario_producao")
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    botao = f"{card_js(pedido.id)}.querySelector('.producao-salvar-comentario')"

    escrever_comentario(pagina, pedido.id, "Texto")
    salvar_comentario(pagina, pedido.id)
    salvar_comentario(pagina, pedido.id)  # segundo clique é ignorado

    assert pagina.js(f"{botao}.disabled") is True
    assert pagina.js(f"{botao}.textContent") == "Salvando…"
    mensagem_do_card(pagina, pedido.id)
    assert pagina.js(f"{botao}.disabled") is False
    assert pagina.js(f"{botao}.textContent") == "Salvar comentário"
    assert len(repo_pedidos.alteracoes_comentario) == 1


def test_erro_ao_salvar_comentario_preserva_o_texto(pagina, servidor, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoAlterar

    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.falhar_em["salvar_comentario"] = FalhaAoAlterar()
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    escrever_comentario(pagina, pedido.id, "Meu texto importante")
    salvar_comentario(pagina, pedido.id)

    assert mensagem_do_card(pagina, pedido.id) == [
        "Não foi possível salvar o comentário agora. Tente novamente.",
        "alert",
    ]
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Meu texto importante"
    assert pagina.js(f"{comentario_js(pedido.id)}.dataset.salvo") == ""
    assert pagina.js("document.activeElement.textContent") == "Salvar comentário"


def test_conflito_de_comentario_recarrega_e_mantem_o_texto(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    repo_pedidos.comentarios[pedido.id] = ("Da Kassia", momento_exemplo(), 2)  # enquanto editava

    escrever_comentario(pagina, pedido.id, "Meu texto")
    salvar_comentario(pagina, pedido.id)

    texto, papel = mensagem_do_card(pagina, pedido.id)
    assert "alterado por outro usuário" in texto and papel == "alert"
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Meu texto"
    assert pagina.js(f"{comentario_js(pedido.id)}.dataset.salvo") == "Da Kassia"  # recarregado
    assert "Atualizado por Kassia" in pagina.js(f"{card_js(pedido.id)}.innerText")
    assert pagina.js(f"document.activeElement === {comentario_js(pedido.id)}")
    assert repo_pedidos.comentarios[pedido.id][0] == "Da Kassia"


def test_editar_o_comentario_nao_arrasta_o_card(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    pagina.js(f"{comentario_js(pedido.id)}.focus()")
    assert pagina.js(f"{card_js(pedido.id)}.hasAttribute('draggable')") is False
    pagina.js(f"{comentario_js(pedido.id)}.blur()")
    assert pagina.js(f"{card_js(pedido.id)}.getAttribute('draggable')") == "true"


# ---------- Status do pagamento ----------


def pagamento_js(pedido_id):
    return f"{card_js(pedido_id)}.querySelector('.producao-pagamento')"


def estado_pagamento(p, pedido_id):
    b = pagamento_js(pedido_id)
    return p.js(
        f"[{b}.dataset.status, {b}.textContent.trim(), {b}.getAttribute('aria-label'),"
        f" {b}.title, {b}.classList.contains('selo-status-' + {b}.dataset.status)]"
    )


def test_alterna_pagamento_nos_dois_sentidos(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    assert estado_pagamento(pagina, pedido.id) == [
        "pendente",
        "Pendente",
        "Pendente. Marcar pagamento como pago",
        "Marcar pagamento como pago",
        True,
    ]

    pagina.js(f"{pagamento_js(pedido.id)}.click()")
    assert esperar_mensagem(pagina) == "Pedido de Cliente 1: Pagamento marcado como pago."

    assert estado_pagamento(pagina, pedido.id) == [
        "pago",
        "Pago",
        "Pago. Marcar pagamento como pendente",
        "Marcar pagamento como pendente",
        True,
    ]
    assert pagina.js(f"document.activeElement === {pagamento_js(pedido.id)}")
    pagina.js("document.getElementById('producao-mensagem').replaceChildren()")

    pagina.js(f"{pagamento_js(pedido.id)}.click()")
    esperar_mensagem(pagina)

    assert estado_pagamento(pagina, pedido.id)[:2] == ["pendente", "Pendente"]
    assert repo_pedidos.alteracoes_pagamento == [
        (pedido.id, "pendente", "pago", 1),
        (pedido.id, "pago", "pendente", 1),
    ]
    assert etapa_na_tela(pagina, pedido.id) == "fila_producao"
    assert repo_pedidos.etapas[pedido.id] == "fila_producao" and repo_pedidos.movimentos == []


def test_pagamento_pelo_teclado(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    pagina.js(f"{pagamento_js(pedido.id)}.focus()")

    tecla(pagina, "Enter", "Enter", 13, "\r")
    esperar_mensagem(pagina)

    assert estado_pagamento(pagina, pedido.id)[0] == "pago"
    assert pagina.js(f"document.activeElement === {pagamento_js(pedido.id)}")
    assert pagina.js(f"getComputedStyle({pagamento_js(pedido.id)}).outlineStyle") == "solid"


def test_pagamento_so_muda_na_tela_depois_do_servidor(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    lento(repo_pedidos, "alterar_status_pagamento")
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    pagina.js(f"{pagamento_js(pedido.id)}.click()")
    pagina.js(f"{pagamento_js(pedido.id)}.click()")  # ignorado enquanto espera

    assert pagina.js(f"{pagamento_js(pedido.id)}.disabled") is True
    assert estado_pagamento(pagina, pedido.id)[0] == "pendente"
    esperar_mensagem(pagina)
    assert estado_pagamento(pagina, pedido.id)[0] == "pago"
    assert pagina.js(f"{pagamento_js(pedido.id)}.disabled") is False
    assert len(repo_pedidos.alteracoes_pagamento) == 1


def test_falha_no_pagamento_mantem_o_estado(pagina, servidor, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoAlterar

    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.falhar_em["alterar_pagamento"] = FalhaAoAlterar()
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    pagina.js(f"{pagamento_js(pedido.id)}.click()")

    texto = esperar_mensagem(pagina)
    assert texto == "Não foi possível alterar o pagamento agora. Tente novamente."
    assert estado_pagamento(pagina, pedido.id)[0] == "pendente"
    papel = pagina.js("document.querySelector('#producao-mensagem .alerta').getAttribute('role')")
    assert papel == "alert"
    assert repo_pedidos.pedidos[pedido.id].status_pagamento == "pendente"


def test_conflito_no_pagamento_recarrega_o_quadro(pagina, servidor, repo_pedidos):
    from dataclasses import replace

    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    repo_pedidos.pedidos[pedido.id] = replace(pedido, status_pagamento="pago")  # outro usuário

    pagina.js(f"{pagamento_js(pedido.id)}.click()")
    esperar(lambda: "outro usuário" in pagina.texto("#producao-mensagem"))

    assert estado_pagamento(pagina, pedido.id)[0] == "pago"  # quadro recarregado
    assert repo_pedidos.alteracoes_pagamento == []
    assert pagina.js(f"document.activeElement === {pagamento_js(pedido.id)}")


def test_pagamento_alterado_aparece_em_pedidos_e_detalhes(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    pagina.js(f"{pagamento_js(pedido.id)}.click()")
    esperar_mensagem(pagina)

    pagina.abrir("/pedidos")
    assert pagina.js("!!document.querySelector('.selo-status-pago')")
    pagina.abrir(f"/pedidos/{pedido.id}")
    assert pagina.js("!!document.querySelector('.selo-status-pago')")


# ---------- Produção: título do card, card recolhido e pedido concluído ----------


def pedido_na_etapa(repo, servidor, etapa, status="pendente", quantidade=1):
    from dataclasses import replace

    pedidos = pedidos_producao(repo, servidor, quantidade)
    for pedido in pedidos:
        repo.etapas[pedido.id] = etapa
        repo.pedidos[pedido.id] = replace(pedido, status_pagamento=status)
    return pedidos


def foco_no_card(p, pedido_id):
    return p.js(f"{card_js(pedido_id)}.contains(document.activeElement)")


# Texto do card -> tamanho em px. Mínimo de legibilidade: 11 px nos rótulos e na última
# atualização do comentário; 12 px em todo o resto (cliente, produtos, valores, campo, selo,
# mensagens e botões).
FONTES_DO_CARD_PX = {
    "h3": 12,  # nome do cliente
    ".producao-card-produtos": 12,
    ".pedido-card-unidades": 12,
    ".producao-card-dados dt": 11,
    ".producao-card-dados dd": 12,
    ".pedido-card-pagamento": 12,  # forma de pagamento
    ".producao-pagamento-texto": 12,  # selo Pendente/Pago
    ".producao-card-prazo time": 12,
    ".producao-card-obs dd": 12,
    ".producao-comentario label": 11,
    ".producao-comentario-campo": 12,
    ".producao-comentario-info": 11,
    ".producao-comentario-mensagem .alerta": 12,
    ".producao-salvar-comentario": 12,
    ".producao-card-acoes a.botao": 12,
    ".producao-mover": 12,
}
# Menor fonte entre os textos visíveis de todos os cards.
MENOR_FONTE_NOS_CARDS = """
Math.min(...[...document.querySelectorAll('.producao-card *')]
  .filter(e => e.getClientRects().length && !e.closest('.visualmente-oculto')
    && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
  .map(e => parseFloat(getComputedStyle(e).fontSize)))
"""
# Elementos visíveis que saem da caixa do próprio card.
SAINDO_DOS_CARDS = """
[...document.querySelectorAll('.producao-card')].flatMap(c => {
  const rc = c.getBoundingClientRect();
  return [...c.querySelectorAll('*')].filter(e => {
    if (!e.getClientRects().length) return false;
    const r = e.getBoundingClientRect();
    return r.left < rc.left - 0.5 || r.right > rc.right + 0.5 })
  .map(e => c.dataset.pedido + ' ' + e.tagName + '.' + e.className) })
"""
# Controles do card: no mínimo 44 px de altura (e o botão de expandir, 44 de largura).
CONTROLES_DO_CARD = [
    ".producao-recolher",
    ".producao-pagamento",
    ".producao-comentario-campo",
    ".producao-salvar-comentario",
    ".producao-card-acoes a.botao",
    ".producao-mover",
]


def test_fontes_do_card_com_minimo_de_legibilidade_e_controles_de_44px(
    pagina, servidor, repo_pedidos
):
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    pedido = novo_pedido(
        repo_pedidos,
        itens=[item(1, "Caneca", 2, "1.00", com_imagem(repo_pedidos, 1, 1))],
        observacoes="Observação",
    )
    repo_pedidos.etapas[pedido.id] = "em_producao"
    repo_pedidos.comentarios[pedido.id] = ("Azul", momento_exemplo(), 2)
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    card = card_js(pedido.id)
    pagina.js(  # uma mensagem do comentário, como depois de salvar
        f"{card}.querySelector('.producao-comentario-mensagem').innerHTML ="
        " '<div class=\"alerta alerta-sucesso\">Comentário salvo.</div>'"
    )

    medidas = pagina.js(
        f"(() => {{ const c = {card}, r = {{}};"
        f" for (const s of {json.dumps(list(FONTES_DO_CARD_PX))})"
        "  r[s] = parseFloat(getComputedStyle(c.querySelector(s)).fontSize);"
        " return r })()"
    )
    for seletor, px in FONTES_DO_CARD_PX.items():
        assert medidas[seletor] == px, (seletor, medidas[seletor])
    assert pagina.js(MENOR_FONTE_NOS_CARDS) == 11

    # O cliente continua o título principal do card: Sora Bold e o maior tamanho do card.
    familia, peso = pagina.js(
        f"(() => {{ const s = getComputedStyle({card}.querySelector('h3'));"
        " return [s.fontFamily, s.fontWeight] })()"
    )
    assert familia.split(",")[0].strip('"') == "Sora" and peso == "700"
    assert medidas["h3"] == max(medidas.values())

    # Ícones sem redução (tamanho calculado: a seta gira ao expandir e a caixa dela muda
    # durante a animação).
    icones = pagina.js(
        f"(() => {{ const c = {card};"
        " const m = s => { const e = getComputedStyle(c.querySelector(s));"
        "  return [parseFloat(e.width), parseFloat(e.height)] };"
        " return [m('.producao-recolher svg'), m('.producao-pagamento svg'),"
        " m('.producao-card-foto')] })()"
    )
    assert icones == [[22, 22], [15, 15], [56, 56]]

    # Controles com pelo menos 44 px.
    for seletor in CONTROLES_DO_CARD:
        altura, largura = pagina.js(
            f"(() => {{ const r = {card}.querySelector({json.dumps(seletor)})"
            ".getBoundingClientRect(); return [r.height, r.width] })()"
        )
        assert altura >= 44 and largura >= 44, (seletor, altura, largura)

    # Fora do card nada muda: os botões do topo da página continuam com 0.875rem (14 px).
    topo = "getComputedStyle(document.querySelector('.pagina-topo .botao')).fontSize"
    assert pagina.js(topo) == "14px"


@pytest.mark.parametrize("largura", [360, 768, 1024, 1100, 1280, 1920])
def test_cards_legiveis_e_sem_overflow_em_cada_largura(pagina, servidor, repo_pedidos, largura):
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    com_foto = novo_pedido(
        repo_pedidos,
        cliente="Ana Beatriz de Souza Albuquerque",
        itens=[item(1, "Caneca personalizada", 3, "1.00", com_imagem(repo_pedidos, 1, 1))],
        observacoes="Embalar para presente",
    )
    repo_pedidos.comentarios[com_foto.id] = ("Pintar em azul-claro", momento_exemplo(), 2)
    outros = pedidos_producao(repo_pedidos, servidor, 3)
    for pedido, etapa in zip(
        outros, ["em_producao", "aguardando_entrega", "entregue"], strict=True
    ):
        repo_pedidos.etapas[pedido.id] = etapa
    pagina.tela(largura, 900)
    pagina.abrir("/producao")
    controles = (
        "[...document.querySelectorAll('.producao-card button, .producao-card a,"
        " .producao-card textarea')].filter(e => e.getClientRects().length)"
        ".filter(e => e.getBoundingClientRect().height < 44).length"
    )

    for estado in ("recolhido", "expandido"):
        if estado == "expandido":
            expandir_todos(pagina)
        else:
            assert (
                pagina.js("document.querySelectorAll('.producao-card:not(.recolhido)').length") == 0
            )
            for seletor in (".producao-card-foto", ".producao-comentario-campo"):
                assert visivel_no_card(pagina, com_foto.id, seletor), (seletor, largura)
        assert pagina.js(MENOR_FONTE_NOS_CARDS) >= 11, (estado, largura)
        assert pagina.js(controles) == 0, (estado, largura)
        assert pagina.js(SAINDO_DOS_CARDS) == [], (estado, largura)
        assert pagina.js("document.documentElement.scrollWidth") <= largura, (estado, largura)
        cortes = pagina.js(CORTES.replace("LIMITE_VERTICAL", "false"))
        assert [c for c in cortes if not c.startswith("OL.coluna-cards")] == [], estado


def test_botao_de_recolher_fica_dentro_do_card_expandido_em_coluna_estreita_com_rolagem(
    pagina, servidor, repo_pedidos
):
    """Regressão: a 1024 px a coluna é estreita; com rolagem vertical (barra clássica de 15 px)
    o topo do card (foto + nome + botão de 44 px) era mais largo que o card e o botão de recolher
    passava da borda. Confere a geometria renderizada, não a regra de CSS."""
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    pedidos = [
        novo_pedido(
            repo_pedidos,
            cliente=f"Cliente {n}",
            itens=[item(1, "Caneca", 2, "1.00", com_imagem(repo_pedidos, n, 1))],
        )
        for n in range(1, 8)
    ]
    for pedido in pedidos:  # todos na mesma coluna: ela precisa de rolagem vertical
        repo_pedidos.etapas[pedido.id] = "aguardando_entrega"
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    pagina.tela(1024, 900)
    pagina.abrir("/producao")
    expandir_todos(pagina)

    # Pré-condições: a coluna rola na vertical e os cards expandidos têm foto.
    assert pagina.js(
        "(() => { const ol = document.querySelector('.coluna[data-etapa=aguardando_entrega]"
        " .coluna-cards'); return ol.scrollHeight > ol.clientHeight })()"
    )
    assert pagina.js("document.querySelectorAll('.producao-card .producao-card-foto').length") == 7

    fora = pagina.js(
        "[...document.querySelectorAll('.producao-card')].flatMap(c => {"
        " const rc = c.getBoundingClientRect(), b = c.querySelector('.producao-recolher')"
        "   .getBoundingClientRect();"
        " return (b.left >= rc.left - 0.5 && b.right <= rc.right + 0.5"
        "   && b.top >= rc.top - 0.5 && b.bottom <= rc.bottom + 0.5) ? []"
        "   : [c.dataset.pedido + ' ' + [rc.left, rc.right, b.left, b.right].join(',')] })"
    )
    assert fora == []  # o botão de recolher dentro dos limites de cada card
    assert pagina.js(SAINDO_DOS_CARDS) == []
    assert pagina.js("document.documentElement.scrollWidth") <= 1024
    assert pagina.js("document.querySelectorAll('.producao-card:not(.recolhido)').length") == 7


# Mede cada coluna: título (texto, tamanho, família, peso, caixa, cortado?), ícone,
# contador e a própria coluna.
MEDIDAS_COLUNAS = """
[...document.querySelectorAll('.coluna')].map(col => {
  const h = col.querySelector('.coluna-topo h2'), i = col.querySelector('.coluna-icone');
  const n = col.querySelector('.coluna-contador'), s = getComputedStyle(h);
  const caixa = e => { const r = e.getBoundingClientRect();
    return [r.left, r.right, r.top, r.bottom, r.width, r.height] };
  // Cada palavra numa única linha: a quebra acontece só entre as palavras.
  let pos = 0;
  const inteiras = h.textContent.split(' ').every(p => {
    const i = h.textContent.indexOf(p, pos), r = document.createRange();
    r.setStart(h.firstChild, i); r.setEnd(h.firstChild, i + p.length); pos = i + p.length;
    return r.getClientRects().length === 1 });
  return {texto: h.textContent, tamanho: parseFloat(s.fontSize), familia: s.fontFamily,
    peso: s.fontWeight, titulo: caixa(h), cortado: h.scrollWidth > h.clientWidth + 1,
    palavras_inteiras: inteiras,
    icone: caixa(i), contador: caixa(n),
    contador_fonte: parseFloat(getComputedStyle(n).fontSize), coluna: caixa(col)};
})
"""


def sobrepostos(a, b):
    """Caixas [left, right, top, bottom, ...] que se cruzam (com meio pixel de folga)."""
    return a[0] < b[1] - 0.5 and b[0] < a[1] - 0.5 and a[2] < b[3] - 0.5 and b[2] < a[3] - 0.5


@pytest.mark.parametrize("largura", [360, 768, 1024, 1100, 1280, 1920])
def test_titulos_das_colunas_com_1_5rem(pagina, servidor, repo_pedidos, largura):
    pedidos = pedidos_producao(repo_pedidos, servidor, 2)
    repo_pedidos.etapas[pedidos[1].id] = "em_producao"
    pagina.tela(largura, 900)
    pagina.abrir("/producao")
    raiz = pagina.js("parseFloat(getComputedStyle(document.documentElement).fontSize)")

    colunas = pagina.js(MEDIDAS_COLUNAS)

    assert [c["texto"] for c in colunas] == [
        "Na Fila de Produção",
        "Em Produção",
        "Ag. Entrega",
        "Entregue",
    ]
    for c in colunas:
        nome = (c["texto"], largura)
        assert c["tamanho"] == 1.5 * raiz == 24, nome  # era 2rem (32px): 25% menor
        assert c["familia"].split(",")[0].strip('"') == "Sora" and c["peso"] == "700", nome
        # Ícone e contador com os tamanhos de antes.
        assert c["icone"][4:] == [22, 22], nome
        assert c["contador_fonte"] == 13.6, nome
        # O título não encosta no ícone nem no contador, fica dentro da coluna, sem corte e
        # sem partir palavras.
        titulo, icone, contador, coluna = c["titulo"], c["icone"], c["contador"], c["coluna"]
        assert not sobrepostos(titulo, icone) and not sobrepostos(titulo, contador), nome
        for caixa in (titulo, icone, contador):
            assert coluna[0] <= caixa[0] and caixa[1] <= coluna[1] + 0.5, nome
        assert not c["cortado"] and c["palavras_inteiras"], nome
    assert pagina.js("document.documentElement.scrollWidth") <= largura
    cortes = pagina.js(CORTES.replace("LIMITE_VERTICAL", "false"))
    assert [c for c in cortes if not c.startswith("OL.coluna-cards")] == []


@pytest.mark.parametrize("largura", [360, 768, 1024, 1280, 1920])
def test_nome_longo_quebra_sem_rolagem_horizontal(pagina, servidor, repo_pedidos, largura):
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    nomes = ["Maria" + "Aparecida" * 10, "Ana Beatriz de Souza Albuquerque Cavalcanti Ferreira"]
    pedidos = [
        novo_pedido(
            repo_pedidos,
            cliente=nome,
            itens=[item(1, "Caneca", 2, "1.00", com_imagem(repo_pedidos, n, 1))],
        )
        for n, nome in enumerate(nomes, 1)
    ]
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    pagina.tela(largura, 900)
    pagina.abrir("/producao")

    for recolher in (True, False):  # começa recolhido; depois, expandido
        if not recolher:
            expandir_todos(pagina)
        assert pagina.js("document.documentElement.scrollWidth") <= largura
        for pedido in pedidos:
            dentro, sem_corte = pagina.js(
                f"(() => {{ const c = {card_js(pedido.id)}, h = c.querySelector('h3');"
                " const rc = c.getBoundingClientRect(), rh = h.getBoundingClientRect();"
                " return [rh.left >= rc.left && rh.right <= rc.right + 0.5,"
                " h.scrollWidth <= h.clientWidth + 1] })()"
            )
            assert dentro and sem_corte, (pedido.cliente_nome, recolher)
        cortes = pagina.js(CORTES.replace("LIMITE_VERTICAL", "false"))
        assert [c for c in cortes if not c.startswith("OL.coluna-cards")] == []


def test_recolhido_sem_foto_nao_deixa_espaco_vazio(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)  # item sem imagem
    pagina.tela(1280, 800)
    pagina.abrir("/producao")  # o card já começa recolhido

    assert pagina.js(f"{card_js(pedido.id)}.classList.contains('recolhido')")
    assert not pagina.js(f"!!{card_js(pedido.id)}.querySelector('img')")
    distancia = pagina.js(
        f"(() => {{ const c = {card_js(pedido.id)}.getBoundingClientRect(),"
        f" h = {card_js(pedido.id)}.querySelector('h3').getBoundingClientRect();"
        " return h.left - c.left })()"
    )
    assert distancia < 20  # o título começa junto à borda: nenhum espaço reservado à foto
    for seletor in SEMPRE_VISIVEIS:
        if seletor not in (".producao-card-foto", ".producao-comentario-info"):
            assert visivel_no_card(pagina, pedido.id, seletor), seletor


def test_recolhido_usa_a_mesma_foto_do_expandido(pagina, servidor, repo_pedidos):
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    pedido = novo_pedido(
        repo_pedidos, itens=[item(1, "Caneca", 2, "1.00", com_imagem(repo_pedidos, 1, 1))]
    )
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    foto = f"{card_js(pedido.id)}.querySelector('.producao-card-foto')"
    esperar(lambda: pagina.js(f"{foto}.naturalWidth > 0"))
    medir = f"[{foto}.src, {foto}.alt, getComputedStyle({foto}).objectFit, {foto}.width]"
    recolhido = pagina.js(medir)  # estado inicial

    pagina.js(f"{card_js(pedido.id)}.querySelector('.producao-recolher').click()")

    assert pagina.js(f"{card_js(pedido.id)}.querySelectorAll('img').length") == 1
    assert pagina.js(medir) == recolhido
    assert recolhido[1] == "Foto de referência: Caneca" and recolhido[2] == "cover"


def test_comentario_com_card_recolhido(pagina, servidor, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoAlterar

    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")  # o card já começa recolhido
    recolhido = f"{card_js(pedido.id)}.classList.contains('recolhido')"
    assert pagina.js(recolhido)

    # Salvar.
    escrever_comentario(pagina, pedido.id, "Feito com o card recolhido")
    salvar_comentario(pagina, pedido.id)
    assert mensagem_do_card(pagina, pedido.id) == ["Comentário salvo.", "status"]
    assert repo_pedidos.comentarios[pedido.id][0] == "Feito com o card recolhido"
    assert pagina.js(recolhido)
    assert visivel_no_card(pagina, pedido.id, ".producao-comentario-info")
    assert visivel_no_card(pagina, pedido.id, ".producao-comentario-mensagem .alerta")

    # Erro: o texto digitado continua no campo, o card continua recolhido.
    limpar_mensagem_do_card(pagina, pedido.id)
    repo_pedidos.falhar_em["salvar_comentario"] = FalhaAoAlterar()
    escrever_comentario(pagina, pedido.id, "Texto que não pode sumir")
    salvar_comentario(pagina, pedido.id)
    assert mensagem_do_card(pagina, pedido.id)[1] == "alert"
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Texto que não pode sumir"
    assert pagina.js(recolhido)
    assert pagina.js("document.activeElement.textContent") == "Salvar comentário"
    del repo_pedidos.falhar_em["salvar_comentario"]

    # Conflito: o quadro é recarregado, o card continua recolhido e o texto continua no campo.
    repo_pedidos.comentarios[pedido.id] = ("Da Kassia", momento_exemplo(), 2)
    salvar_comentario(pagina, pedido.id)
    texto, papel = mensagem_do_card(pagina, pedido.id)
    assert "alterado por outro usuário" in texto and papel == "alert"
    assert pagina.js(recolhido)
    assert pagina.js(f"document.getElementById('card-corpo-{pedido.id}').hidden") is True
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Texto que não pode sumir"
    assert pagina.js(f"{comentario_js(pedido.id)}.dataset.salvo") == "Da Kassia"
    assert pagina.js(f"document.activeElement === {comentario_js(pedido.id)}")
    assert repo_pedidos.comentarios[pedido.id][0] == "Da Kassia"


def test_recolher_e_expandir_nao_apaga_o_texto_nao_salvo(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    botao = f"{card_js(pedido.id)}.querySelector('.producao-recolher')"

    escrever_comentario(pagina, pedido.id, "Rascunho\nainda não salvo")
    pagina.js(f"{botao}.click()")
    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Rascunho\nainda não salvo"
    pagina.js(f"{botao}.click()")
    pagina.js(f"{botao}.click()")

    assert pagina.js(f"{comentario_js(pedido.id)}.value") == "Rascunho\nainda não salvo"
    assert pagina.js(f"{comentario_js(pedido.id)}.dataset.salvo") == ""
    assert repo_pedidos.alteracoes_comentario == []


def test_marcar_pago_na_coluna_entregue_conclui_e_retira_o_card(pagina, servidor, repo_pedidos):
    pedidos = pedido_na_etapa(repo_pedidos, servidor, "entregue", quantidade=2)
    lento(repo_pedidos, "alterar_status_pagamento")
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    assert contadores(pagina) == [0, 0, 0, 2]

    pagina.js(f"{pagamento_js(pedidos[0].id)}.click()")

    # Sem remoção antes da resposta: o card continua, com o botão ocupado.
    assert pagina.js(f"!!{card_js(pedidos[0].id)}")
    assert pagina.js(f"{pagamento_js(pedidos[0].id)}.disabled") is True
    texto = esperar_mensagem(pagina)
    assert texto == "Pedido de Cliente 1 concluído (entregue e pago) e retirado do quadro."
    papel = pagina.js("document.querySelector('#producao-mensagem .alerta').getAttribute('role')")
    assert papel == "status"
    assert not pagina.js(f"!!{card_js(pedidos[0].id)}")
    assert contadores(pagina) == [0, 0, 0, 1]
    # Foco no card vizinho da mesma coluna.
    assert foco_no_card(pagina, pedidos[1].id)
    assert pagina.js("document.activeElement.classList.contains('producao-recolher')")
    assert repo_pedidos.pedidos[pedidos[0].id].status_pagamento == "pago"
    assert repo_pedidos.etapas[pedidos[0].id] == "entregue"  # nada além do pagamento mudou

    # Último card da coluna: o foco vai para a mensagem de sucesso.
    pagina.js("document.getElementById('producao-mensagem').replaceChildren()")
    pagina.js(f"{pagamento_js(pedidos[1].id)}.click()")
    esperar_mensagem(pagina)
    assert contadores(pagina) == [0, 0, 0, 0]
    assert not pagina.js(
        "document.querySelector('.coluna[data-etapa=entregue] .coluna-vazia').hidden"
    )
    assert pagina.js(
        "document.activeElement === document.querySelector('#producao-mensagem .alerta')"
    )

    # Ao recarregar a página, os concluídos continuam fora; em Pedidos, continuam lá.
    pagina.abrir("/producao")
    assert contadores(pagina) == [0, 0, 0, 0]
    pagina.abrir("/pedidos")
    assert "Cliente 1" in pagina.js("document.querySelector('main').innerText")


def test_pedido_pago_movido_para_entregue_sai_do_quadro(pagina, servidor, repo_pedidos):
    pedidos = pedido_na_etapa(repo_pedidos, servidor, "aguardando_entrega", "pago", 2)
    lento(repo_pedidos, "mover_etapa")
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    assert contadores(pagina) == [0, 0, 2, 0]

    clicar_avancar(pagina, pedidos[1].id)
    pagina.clicar("#confirmar-entrega button[value=confirmar]")
    assert pagina.js(f"!!{card_js(pedidos[1].id)}")  # ainda esperando o servidor
    texto = esperar_mensagem(pagina)

    assert texto == "Pedido de Cliente 2 concluído (entregue e pago) e retirado do quadro."
    assert not pagina.js(f"!!{card_js(pedidos[1].id)}")
    assert contadores(pagina) == [0, 0, 1, 0]
    assert foco_no_card(pagina, pedidos[0].id)  # vizinho na coluna de onde o card saiu
    assert repo_pedidos.etapas[pedidos[1].id] == "entregue"


def test_pedido_pendente_movido_para_entregue_continua(pagina, servidor, repo_pedidos):
    (pedido,) = pedido_na_etapa(repo_pedidos, servidor, "aguardando_entrega")
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    clicar_avancar(pagina, pedido.id)
    pagina.clicar("#confirmar-entrega button[value=confirmar]")

    assert esperar_mensagem(pagina) == "Pedido de Cliente 1 movido para Entregue."
    assert etapa_na_tela(pagina, pedido.id) == "entregue"
    assert contadores(pagina) == [0, 0, 0, 1]


@pytest.mark.parametrize("etapa", ["fila_producao", "em_producao", "aguardando_entrega"])
def test_pagar_antes_da_entrega_mantem_o_card(pagina, servidor, repo_pedidos, etapa):
    (pedido,) = pedido_na_etapa(repo_pedidos, servidor, etapa)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")

    pagina.js(f"{pagamento_js(pedido.id)}.click()")

    assert esperar_mensagem(pagina) == "Pedido de Cliente 1: Pagamento marcado como pago."
    assert etapa_na_tela(pagina, pedido.id) == etapa
    assert estado_pagamento(pagina, pedido.id)[0] == "pago"


def test_falhas_mantem_o_card_status_e_etapa(pagina, servidor, repo_pedidos):
    from app.pedidos_repositorio import FalhaAoAlterar, FalhaAoMover

    entregue = pedido_na_etapa(repo_pedidos, servidor, "entregue")[0]
    pago = pedido_na_etapa(repo_pedidos, servidor, "aguardando_entrega", "pago")[0]
    repo_pedidos.falhar_em["alterar_pagamento"] = FalhaAoAlterar()
    repo_pedidos.falhar_em["mover_etapa"] = FalhaAoMover()
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    expandir_todos(pagina)
    antes = contadores(pagina)

    pagina.js(f"{pagamento_js(entregue.id)}.click()")
    assert esperar_mensagem(pagina).startswith("Não foi possível alterar o pagamento")
    assert etapa_na_tela(pagina, entregue.id) == "entregue"
    assert estado_pagamento(pagina, entregue.id)[0] == "pendente"
    assert pagina.js(f"document.activeElement === {pagamento_js(entregue.id)}")

    pagina.js("document.getElementById('producao-mensagem').replaceChildren()")
    clicar_avancar(pagina, pago.id)
    pagina.clicar("#confirmar-entrega button[value=confirmar]")
    assert esperar_mensagem(pagina) == "Não foi possível mover o pedido agora. Tente novamente."
    assert etapa_na_tela(pagina, pago.id) == "aguardando_entrega"
    assert estado_pagamento(pagina, pago.id)[0] == "pago"

    assert contadores(pagina) == antes
    assert repo_pedidos.alteracoes_pagamento == [] and repo_pedidos.movimentos == []


def test_conflito_ao_pagar_na_coluna_entregue_recarrega_o_quadro(pagina, servidor, repo_pedidos):
    from dataclasses import replace

    pedidos = pedido_na_etapa(repo_pedidos, servidor, "entregue", quantidade=2)
    pagina.tela(1280, 800)
    pagina.abrir("/producao")
    # Outro usuário já marcou como pago: o pedido foi concluído fora desta tela.
    repo_pedidos.pedidos[pedidos[0].id] = replace(pedidos[0], status_pagamento="pago")

    pagina.js(f"{pagamento_js(pedidos[0].id)}.click()")
    esperar(lambda: "outro usuário" in pagina.texto("#producao-mensagem"))

    assert not pagina.js(f"!!{card_js(pedidos[0].id)}")  # quadro recarregado, sem o concluído
    assert contadores(pagina) == [0, 0, 0, 1]
    assert repo_pedidos.alteracoes_pagamento == []


# ---------- Ocultar e mostrar o comentário (card recolhido) ----------


def alternar_comentario_js(pedido_id):
    return f"{card_js(pedido_id)}.querySelector('.producao-comentario-alternar')"


def comentario_a_vista(p, pedido_id):
    """Texto (campo) e última atualização visíveis."""
    return visivel_no_card(p, pedido_id, ".producao-comentario-campo") and visivel_no_card(
        p, pedido_id, ".producao-comentario-info"
    )


def test_ocultar_e_mostrar_o_comentario_de_cada_card_recolhido(pagina, servidor, repo_pedidos):
    com, sem, outro = pedidos_producao(repo_pedidos, servidor, 3)
    repo_pedidos.comentarios[com.id] = ("Pintar de azul", momento_exemplo(), 2)
    repo_pedidos.comentarios[outro.id] = ("Embalar com cuidado", momento_exemplo(), 2)
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    botao = alternar_comentario_js(com.id)
    escritas = repo_pedidos.escritas

    # Padrão: comentário à vista; o botão só existe (visível) em card com comentário.
    assert pagina.js(f"{card_js(com.id)}.classList.contains('recolhido')")
    assert comentario_a_vista(pagina, com.id)
    assert visivel_no_card(pagina, com.id, ".producao-comentario-alternar")
    assert not visivel_no_card(pagina, sem.id, ".producao-comentario-alternar")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "true"
    assert pagina.js(f"{botao}.textContent.trim()") == "Ocultar comentário"
    assert pagina.js(f"{botao}.title") == "Ocultar comentário"
    alvo = pagina.js(f"{botao}.getAttribute('aria-controls')")
    assert alvo == f"comentario-corpo-{com.id}"
    assert pagina.js(f"!!document.getElementById('{alvo}')")
    # O ícone fica na mesma linha do título.
    assert pagina.js(
        f"(() => {{ const t = {card_js(com.id)}.querySelector('.producao-comentario-titulo');"
        " const l = t.querySelector('label').getBoundingClientRect(),"
        f" b = {botao}.querySelector('svg').getBoundingClientRect();"
        " return b.top >= l.top - 8 && b.bottom <= l.bottom + 8 })()"
    )

    # Nenhuma requisição ao servidor para ocultar ou mostrar.
    pagina.js(
        "window.__chamadas = 0; const f = window.fetch;"
        " window.fetch = function () { window.__chamadas++; return f.apply(this, arguments) }"
    )

    pagina.js(f"{botao}.click()")

    assert not pagina.js(f"{comentario_js(com.id)}.getClientRects().length > 0")
    assert not visivel_no_card(pagina, com.id, ".producao-comentario-info")
    assert not visivel_no_card(pagina, com.id, ".producao-salvar-comentario")
    assert visivel_no_card(pagina, com.id, ".producao-comentario label")  # o título continua
    assert visivel_no_card(pagina, com.id, ".producao-comentario-alternar")  # e o botão também
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"
    assert pagina.js(f"{botao}.textContent.trim()") == "Mostrar comentário"
    assert pagina.js(f"{botao}.title") == "Mostrar comentário"
    # O clique não expande o card nem muda dados; os outros cards seguem como estavam.
    assert pagina.js(f"{card_js(com.id)}.classList.contains('recolhido')")
    recolher = f"{card_js(com.id)}.querySelector('.producao-recolher')"
    assert pagina.js(f"{recolher}.getAttribute('aria-expanded')") == "false"
    assert etapa_na_tela(pagina, com.id) == "fila_producao"
    assert comentario_a_vista(pagina, outro.id)  # cada card controla o seu
    assert pagina.js(f"{comentario_js(com.id)}.dataset.salvo") == "Pintar de azul"
    assert pagina.js(f"{comentario_js(com.id)}.value") == "Pintar de azul"

    # Expandido: o comentário aparece normalmente e o botão sai; recolhido de novo, a escolha
    # anterior (oculto) é respeitada.
    pagina.js(f"{recolher}.click()")
    assert comentario_a_vista(pagina, com.id)
    assert not visivel_no_card(pagina, com.id, ".producao-comentario-alternar")
    pagina.js(f"{recolher}.click()")
    assert not pagina.js(f"{comentario_js(com.id)}.getClientRects().length > 0")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"

    pagina.js(f"{botao}.click()")  # mostrar de novo
    assert comentario_a_vista(pagina, com.id)
    assert pagina.js(f"{botao}.textContent.trim()") == "Ocultar comentário"

    # Nenhuma requisição, escrita, comentário ou movimento.
    assert pagina.js("window.__chamadas") == 0
    assert repo_pedidos.escritas == escritas and repo_pedidos.movimentos == []
    assert repo_pedidos.alteracoes_comentario == [] and repo_pedidos.alteracoes_pagamento == []
    assert repo_pedidos.comentarios[com.id][0] == "Pintar de azul"

    # Ao recarregar a página, volta ao padrão (visível).
    pagina.js(f"{botao}.click()")
    assert not pagina.js(f"{comentario_js(com.id)}.getClientRects().length > 0")
    pagina.abrir("/producao")
    assert comentario_a_vista(pagina, com.id)
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "true"


def test_ocultar_comentario_por_teclado_com_foco_visivel(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.comentarios[pedido.id] = ("Pintar de azul", momento_exemplo(), 2)
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    botao = alternar_comentario_js(pedido.id)

    tecla(pagina, "Tab", "Tab", 9)  # modalidade de teclado: o foco passa a ser "visível"
    pagina.js(f"{botao}.focus()")
    estilo = f"getComputedStyle({botao})"
    contorno = pagina.js(f"[{estilo}.outlineStyle, {estilo}.outlineWidth]")
    assert contorno[0] != "none" and contorno[1] != "0px"

    tecla(pagina, "Enter", "Enter", 13, "\r")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"
    assert not pagina.js(f"{comentario_js(pedido.id)}.getClientRects().length > 0")
    assert pagina.js(f"document.activeElement === {botao}")  # o foco não se perde
    assert pagina.js(f"{botao}.textContent.trim()") == "Mostrar comentário"

    tecla(pagina, " ", "Space", 32, " ")
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "true"
    assert comentario_a_vista(pagina, pedido.id)
    assert pagina.js(f"{botao}.textContent.trim()") == "Ocultar comentário"
    assert pagina.js(f"{card_js(pedido.id)}.classList.contains('recolhido')")


def test_escolha_de_ocultar_sobrevive_ao_quadro_recarregado(pagina, servidor, repo_pedidos):
    com, outro = pedidos_producao(repo_pedidos, servidor, 2)
    repo_pedidos.comentarios[com.id] = ("Pintar de azul", momento_exemplo(), 2)
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    pagina.js(f"{alternar_comentario_js(com.id)}.click()")
    repo_pedidos.etapas[outro.id] = "em_producao"  # outro usuário moveu: o clique dá conflito

    expandir_todos(pagina)  # as ações do card só aparecem expandido
    clicar_avancar(pagina, outro.id)
    esperar(
        lambda: (
            pagina.texto("#producao-mensagem") == "Este pedido foi atualizado por outro usuário."
        )
    )

    botao = alternar_comentario_js(com.id)  # o quadro foi recarregado: elementos novos
    assert pagina.js(f"{botao}.getAttribute('aria-expanded')") == "false"
    assert pagina.js(f"{botao}.textContent.trim()") == "Mostrar comentário"
    assert pagina.js(f"{card_js(com.id)}.classList.contains('recolhido')") is False  # expandido
    pagina.js(f"{card_js(com.id)}.querySelector('.producao-recolher').click()")  # recolhe
    assert not pagina.js(f"{comentario_js(com.id)}.getClientRects().length > 0")
    assert repo_pedidos.alteracoes_comentario == []


def test_botao_do_comentario_acompanha_salvar_e_apagar(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    assert not visivel_no_card(pagina, pedido.id, ".producao-comentario-alternar")

    escrever_comentario(pagina, pedido.id, "Primeiro comentário")
    salvar_comentario(pagina, pedido.id)
    mensagem_do_card(pagina, pedido.id)
    assert visivel_no_card(pagina, pedido.id, ".producao-comentario-alternar")  # agora existe

    pagina.js(f"{alternar_comentario_js(pedido.id)}.click()")
    assert not pagina.js(f"{comentario_js(pedido.id)}.getClientRects().length > 0")
    pagina.js(f"{alternar_comentario_js(pedido.id)}.click()")
    limpar_mensagem_do_card(pagina, pedido.id)
    escrever_comentario(pagina, pedido.id, "   ")
    salvar_comentario(pagina, pedido.id)
    assert mensagem_do_card(pagina, pedido.id)[0] == "Comentário removido."

    # Sem comentário não há o que ocultar: o botão some e o campo continua à vista.
    assert not visivel_no_card(pagina, pedido.id, ".producao-comentario-alternar")
    assert visivel_no_card(pagina, pedido.id, ".producao-comentario-campo")


def test_botao_do_comentario_nao_arrasta_o_card(pagina, servidor, repo_pedidos):
    (pedido,) = pedidos_producao(repo_pedidos, servidor)
    repo_pedidos.comentarios[pedido.id] = ("Pintar de azul", momento_exemplo(), 2)
    pagina.tela(1280, 900)
    pagina.abrir("/producao")
    card = card_js(pedido.id)
    botao = alternar_comentario_js(pedido.id)
    apertar = f"{botao}.dispatchEvent(new PointerEvent('pointerdown', {{bubbles: true}}))"
    soltar = "document.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))"
    assert pagina.js(f"{card}.getAttribute('draggable')") == "true"

    pagina.js(apertar)
    assert pagina.js(f"{card}.hasAttribute('draggable')") is False  # apertado: não arrasta
    pagina.js(soltar)
    assert pagina.js(f"{card}.getAttribute('draggable')") == "true"  # solto: volta ao normal

    # Durante a edição do comentário o card segue não arrastável (a regra anterior vale).
    pagina.js(f"{comentario_js(pedido.id)}.focus()")
    assert pagina.js(f"{card}.hasAttribute('draggable')") is False
    pagina.js(apertar)
    pagina.js(f"{comentario_js(pedido.id)}.focus()")
    pagina.js(soltar)
    assert pagina.js(f"{card}.hasAttribute('draggable')") is False


GEOMETRIA_DO_COMENTARIO = """
[...document.querySelectorAll('.producao-card')].flatMap(c => {
  const rc = c.getBoundingClientRect(), b = c.querySelector('.producao-comentario-alternar'),
    l = c.querySelector('.producao-comentario-titulo label'),
    t = c.querySelector('.producao-comentario-titulo'), i = b.querySelector('svg');
  const rb = b.getBoundingClientRect(), rl = l.getBoundingClientRect(),
    ri = i.getBoundingClientRect(), rt = t.getBoundingClientRect();
  const linha = parseFloat(getComputedStyle(l).lineHeight) || rl.height;
  const problemas = [];
  if (rb.left < rc.left - 0.5 || rb.right > rc.right + 0.5) problemas.push('botão fora do card');
  if (Math.round(rl.height / linha) !== 1) problemas.push('título em mais de uma linha');
  if (rt.height > rl.height + 1) problemas.push('linha do título mais alta que o texto');
  if (ri.left < rl.right - 0.5 && ri.bottom > rl.top && ri.top < rl.bottom)
    problemas.push('ícone sobre o título');
  if (l.scrollWidth > l.clientWidth + 1) problemas.push('título cortado');
  return problemas.map(p => c.dataset.pedido + ' ' + p);
})
"""


@pytest.mark.parametrize("largura", [360, 768, 1024, 1100, 1280, 1920])
def test_botao_do_comentario_na_mesma_linha_sem_aumentar_o_card(
    pagina, servidor, repo_pedidos, largura
):
    from tests.test_pedidos_consulta import com_imagem, item, novo_pedido

    pedidos = [
        novo_pedido(
            repo_pedidos,
            cliente=f"Cliente {n}",
            itens=[item(1, "Caneca", 2, "1.00", com_imagem(repo_pedidos, n, 1))],
            observacoes="Embalar para presente",
        )
        for n in range(1, 8)
    ]
    for pedido in pedidos:  # mesma coluna: rolagem vertical e coluna estreita em 1024
        repo_pedidos.etapas[pedido.id] = "aguardando_entrega"
        repo_pedidos.comentarios[pedido.id] = ("Pintar em azul-claro", momento_exemplo(), 2)
    repo_pedidos.assinar_imagens = lambda caminhos: {
        c: f"{servidor}/static/img/logo-forma3d-horizontal.png" for c in caminhos
    }
    pagina.tela(largura, 900)
    pagina.abrir("/producao")
    cards = "[...document.querySelectorAll('.producao-card')]"
    controles = (
        f"{cards}.flatMap(c => [...c.querySelectorAll('button, a, textarea')])"
        ".filter(e => e.getClientRects().length)"
        ".filter(e => e.getBoundingClientRect().height < 44).length"
    )
    alturas = f"{cards}.map(c => c.getBoundingClientRect().height)"
    fotos = f"{cards}.every(c => c.querySelector('.producao-card-foto').getClientRects().length)"

    for oculto in (False, True):
        if oculto:
            pagina.js(
                f"{cards}.forEach(c => c.querySelector('.producao-comentario-alternar').click())"
            )
        contexto = (largura, oculto)
        assert pagina.js(GEOMETRIA_DO_COMENTARIO) == [], contexto
        assert pagina.js(controles) == 0, contexto
        assert pagina.js(SAINDO_DOS_CARDS) == [], contexto
        assert pagina.js("document.documentElement.scrollWidth") <= largura, contexto
        assert pagina.js(MENOR_FONTE_NOS_CARDS) >= 11, contexto
        assert pagina.js(fotos), contexto  # a foto continua à vista

    # O botão não aumenta o card: a altura é a mesma com o botão escondido (como em um card sem
    # comentário), ou seja, o ícone está na linha do título e não cria outra.
    com_botao = pagina.js(alturas)
    pagina.js(
        f"{cards}.forEach(c => c.querySelector('.producao-comentario-alternar').hidden = true)"
    )
    sem_botao = pagina.js(alturas)
    assert [round(a - b, 1) for a, b in zip(com_botao, sem_botao, strict=True)] == [0.0] * 7


# ---------- Custos (repositório falso) ----------


def navegar(p, expressao):
    """Executa o clique que muda de página e espera a página nova terminar de carregar."""
    p.js("window.__antiga = true")
    p.js(expressao)
    p._esperar_carregar()


def custos_texto(p, seletor):
    return " ".join(p.js(f"document.querySelector({json.dumps(seletor)}).innerText").split())


def editar_custo(p, seletor_link):
    navegar(p, f"document.querySelector({json.dumps(seletor_link)}).click()")


def preencher(p, seletor, texto):
    p.js(f"document.querySelector({json.dumps(seletor)}).value = ''")
    p.digitar(seletor, texto)


def salvar_custo(p, seletor_form):
    navegar(
        p, f"document.querySelector({json.dumps(seletor_form)} + ' button[type=submit]').click()"
    )


def test_custos_edicao_e_persistencia_no_navegador(pagina, servidor, repo_custos):
    from decimal import Decimal

    pagina.tela(1280, 900)
    pagina.abrir("/custos")
    assert "R$ 100,00/kg" in custos_texto(pagina, "#filamento-1")
    assert "Energia por hora (calculado) R$ 0,12/h" in custos_texto(pagina, "#energia")
    resumo = custos_texto(pagina, "#resumo")
    assert "Subtotal por hora ≈ R$ 0,8978/h (≈ R$ 0,90/h)" in resumo
    assert "Manutenção ainda não definida" in resumo and "Total" not in resumo
    assert "Manutenção A definir" in custos_texto(pagina, "#manutencao")
    assert "Depreciação por hora (calculado) ≈ R$ 0,2778/h" in custos_texto(pagina, "#depreciacao")

    # Editar -> Salvar: o valor novo aparece, com aviso de sucesso, e fica gravado.
    editar_custo(pagina, "#filamento-1 .custos-editar")
    assert pagina.js("location.search") == "?editar=filamento-1"
    assert pagina.valor("#filamento-1-valor_kg") == "100,00"
    preencher(pagina, "#filamento-1-valor_kg", "99,5")
    salvar_custo(pagina, "#filamento-1 form")
    assert pagina.texto(".alerta-sucesso") == "Valor do filamento atualizado."
    assert pagina.js("document.querySelector('.alerta-sucesso').getAttribute('role')") == "status"
    assert "R$ 99,50/kg" in custos_texto(pagina, "#filamento-1")
    assert repo_custos.filamentos[1].valor_kg == Decimal("99.5")
    assert repo_custos.gravacoes == [("filamento", 1, Decimal("99.5"), 1)]  # usuário da sessão
    pagina.abrir("/custos")  # persistiu: recarregar mantém
    assert "R$ 99,50/kg" in custos_texto(pagina, "#filamento-1")

    # Cancelar: nada é gravado.
    editar_custo(pagina, "#filamento-2 .custos-editar")
    preencher(pagina, "#filamento-2-valor_kg", "1")
    navegar(pagina, "document.querySelector('#filamento-2 a.botao-secundario').click()")
    assert repo_custos.filamentos[2].valor_kg == Decimal("125")
    assert "R$ 125,00/kg" in custos_texto(pagina, "#filamento-2")
    assert pagina.js("document.querySelectorAll('form input[name=valor_kg]').length") == 0

    # Valor inválido: o formulário continua aberto, com o que foi digitado e o erro.
    editar_custo(pagina, "#filamento-3 .custos-editar")
    preencher(pagina, "#filamento-3-valor_kg", "abc")
    salvar_custo(pagina, "#filamento-3 form")
    assert pagina.valor("#filamento-3-valor_kg") == "abc"
    assert "número válido" in pagina.texto("#filamento-3 .campo-erro")
    assert (
        pagina.js("document.getElementById('filamento-3-valor_kg').getAttribute('aria-invalid')")
        == "true"
    )
    assert repo_custos.filamentos[3].valor_kg == Decimal("85")

    # Energia: tarifa e consumo; o custo por hora é recalculado (0,95 x 150 / 1000 = 0,1425).
    pagina.abrir("/custos")
    editar_custo(pagina, "#energia .custos-editar")
    preencher(pagina, "#valores-energia-tarifa_kwh", "0,95")
    preencher(pagina, "#valores-energia-consumo_w", "150")
    salvar_custo(pagina, "#energia form")
    assert "Energia por hora (calculado) R$ 0,1425/h" in custos_texto(pagina, "#energia")
    # Subtotal: 0,1425 + 0,50 + 0,27777... (a manutenção segue a definir).
    assert "Subtotal por hora ≈ R$ 0,9203/h" in custos_texto(pagina, "#resumo")

    # Manutenção: sai de "A definir" para um valor; o resumo passa a ser o total completo.
    editar_custo(pagina, "#manutencao .custos-editar")
    assert pagina.valor("#valores-manutencao-manutencao_hora") == ""
    preencher(pagina, "#valores-manutencao-manutencao_hora", "0,10")
    salvar_custo(pagina, "#manutencao form")
    assert pagina.texto(".alerta-sucesso") == "Manutenção atualizada."
    assert "Manutenção R$ 0,10/h" in custos_texto(pagina, "#manutencao")
    assert "Total ≈ R$ 1,0203/h" in custos_texto(pagina, "#resumo")
    assert repo_custos.parametros["manutencao_hora"] == Decimal("0.10")
    editar_custo(pagina, "#manutencao .custos-editar")  # em branco volta a "A definir"
    preencher(pagina, "#valores-manutencao-manutencao_hora", "")
    salvar_custo(pagina, "#manutencao form")
    assert "A definir" in custos_texto(pagina, "#manutencao")
    assert repo_custos.parametros["manutencao_hora"] is None

    # Depreciação: edita os critérios e o valor por hora é recalculado (6.000 / 12.000 = 0,50).
    editar_custo(pagina, "#depreciacao .custos-editar")
    assert pagina.valor("#valores-depreciacao-valor_aquisicao") == "6.000,00"
    preencher(pagina, "#valores-depreciacao-valor_aquisicao", "7200")
    preencher(pagina, "#valores-depreciacao-vida_util_anos", "4")
    preencher(pagina, "#valores-depreciacao-valor_residual", "1200")
    preencher(pagina, "#valores-depreciacao-horas_dia", "10")
    preencher(pagina, "#valores-depreciacao-dias_mes", "25")
    salvar_custo(pagina, "#depreciacao form")
    assert pagina.texto(".alerta-sucesso") == "Depreciação atualizada."
    deprec = custos_texto(pagina, "#depreciacao")
    assert "Depreciação por hora (calculado) R$ 0,50/h" in deprec
    assert (
        "Depreciação mensal R$ 125,00/mês" in deprec
        and "Horas mensais (dias × horas) 250 h" in deprec
    )
    # Critério inválido: residual maior que a aquisição; nada é gravado e o formulário fica aberto.
    editar_custo(pagina, "#depreciacao .custos-editar")
    preencher(pagina, "#valores-depreciacao-valor_residual", "9999")
    salvar_custo(pagina, "#depreciacao form")
    assert "não pode ser maior que o valor de aquisição" in pagina.texto("#depreciacao .campo-erro")
    assert pagina.valor("#valores-depreciacao-valor_residual") == "9999"
    assert repo_custos.parametros["depreciacao_valor_residual"] == Decimal("1200.00")

    # Acessório: cadastro e edição recalculam o custo unitário (10 / 3, depois 10 / 4).
    pagina.abrir("/custos")
    preencher(pagina, "#novo-acessorios-nome", "Chaveiro")
    preencher(pagina, "#novo-acessorios-valor_compra", "10")
    preencher(pagina, "#novo-acessorios-quantidade", "3")
    salvar_custo(pagina, "#acessorios .custos-novo")
    assert pagina.texto(".alerta-sucesso") == "Acessório cadastrado."
    item = custos_texto(pagina, "#acessorios .custos-item")
    assert "Chaveiro" in item and "Valor de compra R$ 10,00" in item and "Quantidade 3 un" in item
    assert "Custo unitário R$ 3,3333/un" in item
    (item_id,) = repo_custos.itens
    editar_custo(pagina, f"#item-{item_id} .custos-editar")
    preencher(pagina, f"#item-{item_id}-quantidade", "4")
    salvar_custo(pagina, f"#item-{item_id} form")
    assert pagina.texto(".alerta-sucesso") == "Acessório atualizado."
    assert "Custo unitário R$ 2,50/un" in custos_texto(pagina, f"#item-{item_id}")
    assert "Quantidade 4 un" in custos_texto(pagina, f"#item-{item_id}")
    assert len(repo_custos.itens) == 1

    # Quantidade inválida no cadastro: nada é gravado e o digitado fica.
    preencher(pagina, "#novo-embalagens-nome", "Caixa")
    preencher(pagina, "#novo-embalagens-valor_compra", "20")
    preencher(pagina, "#novo-embalagens-quantidade", "0")
    salvar_custo(pagina, "#embalagens .custos-novo")
    assert "maior que zero" in pagina.texto("#embalagens .campo-erro")
    assert pagina.valor("#novo-embalagens-nome") == "Caixa"
    assert len(repo_custos.itens) == 1


MENOR_FONTE_NA_PAGINA = """
Math.min(...[...document.querySelectorAll('main *')]
  .filter(e => e.getClientRects().length && !e.closest('.visualmente-oculto, svg')
    && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
  .map(e => parseFloat(getComputedStyle(e).fontSize)))
"""
CONTROLES_BAIXOS = """
[...document.querySelectorAll('main a.botao, main button, main input')]
  .filter(e => e.getClientRects().length && e.type !== 'hidden')
  .filter(e => e.getBoundingClientRect().height < 44).map(e => e.tagName + '.' + e.className)
"""
SAINDO_NA_HORIZONTAL = """
[...document.querySelectorAll('main *')].filter(e => {
  if (!e.getClientRects().length || e.closest('svg')) return false;
  const r = e.getBoundingClientRect();
  return r.left < -0.5 || r.right > document.documentElement.clientWidth + 0.5 })
  .map(e => e.tagName + '.' + e.className)
"""


@pytest.mark.parametrize("largura", [360, 768, 1024, 1100, 1280, 1920])
def test_custos_legivel_e_sem_rolagem_horizontal_em_cada_largura(
    pagina, servidor, repo_custos, largura
):
    from decimal import Decimal

    repo_custos.salvar_item(None, "acessorio", "Chaveiro " + "x" * 90, Decimal("1234.5678"), 7, 1)
    repo_custos.salvar_item(None, "acessorio", "Imã", Decimal("10"), 3, 1)
    repo_custos.salvar_item(None, "embalagem", "Caixa " + "y" * 100, Decimal("36"), 12, 1)
    pagina.tela(largura, 900)

    estados = [
        "/custos",
        "/custos?editar=filamento-1",
        "/custos?editar=valores-energia",
        "/custos?editar=valores-perdas",
        "/custos?editar=valores-mdo",
        "/custos?editar=valores-manutencao",
        "/custos?editar=valores-depreciacao",
        "/custos?editar=item-1",
        "/custos?editar=item-3",
    ]
    for estado in estados:
        pagina.abrir(estado)
        contexto = (largura, estado)
        assert pagina.js("document.documentElement.scrollWidth") <= largura, contexto
        assert pagina.js(SAINDO_NA_HORIZONTAL) == [], contexto
        assert pagina.js(CONTROLES_BAIXOS) == [], contexto
        assert pagina.js(MENOR_FONTE_NA_PAGINA) >= 11, contexto
        # Campo de texto com valor comprido rola por dentro por natureza.
        cortes = [
            c
            for c in pagina.js(CORTES.replace("LIMITE_VERTICAL", "false"))
            if not c.startswith(("INPUT", "TEXTAREA"))
        ]
        assert cortes == [], contexto
        # Custos Diretos antes de Custos Indiretos, e todas as seções visíveis.
        titulos = pagina.js(
            "[...document.querySelectorAll('main h2')].map(h => h.textContent.trim())"
        )
        assert titulos == ["Custos Diretos", "Custos Indiretos"], contexto
        assert pagina.js("document.querySelectorAll('main section.secao').length") == 9, contexto
