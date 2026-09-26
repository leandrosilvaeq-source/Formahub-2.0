"""Testes da tela "Novo pedido" em um navegador real (Edge ou Chrome, sem interface).

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

    def cmd(self, metodo: str, **params):
        id_ = next(self._ids)
        self.ws.send(json.dumps({"id": id_, "method": metodo, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=15))
            if msg.get("id") == id_:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expressao: str):
        r = self.cmd("Runtime.evaluate", expression=expressao, returnByValue=True)
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
        self.cmd("Page.navigate", url=self.base_url + caminho)
        self._esperar_carregar()

    def _esperar_carregar(self):
        esperar(
            lambda: self.js("document.readyState === 'complete' && !window.__antiga"),
            mensagem="página não carregou",
        )

    def enviar(self):
        self.js("window.__antiga = true; document.querySelector('button[type=submit]').click()")
        self._esperar_carregar()

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
        processo.terminate()
        processo.wait(timeout=10)


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
    [("pagamento", "PIX", "Cartão"), ("entrega", "Entrega em mãos", "Retirada")],
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
    for campo in ("cliente", "contato", "pagamento", "entrega"):
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
    tela.digitar(item(1, "item_produto"), "Caneca")
    tela.js(f"document.querySelector('{item(1, 'item_quantidade')}').value = ''")
    tela.digitar(item(1, "item_quantidade"), "2")
    tela.digitar(item(1, "item_valor"), "3550")
    tela.clicar("#grupo-pagamento input[value='Dinheiro']")
    tela.clicar("#grupo-entrega input[value='Entrega em mãos']")
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith(
        "Pedido de Maria Souza validado com sucesso — 2 itens, total R$ 71,00."
    )


# ---------- Responsivo ----------


@pytest.mark.parametrize("largura", [360, 768, 1280])
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
        ".map(e => e.getBoundingClientRect().height)"
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
