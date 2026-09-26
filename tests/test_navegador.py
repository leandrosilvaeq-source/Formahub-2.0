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


# ---------- Compactação (desktop) e ícones ----------


def preencher_dois_itens(tela):
    tela.digitar("#cliente", "Maria Souza")
    tela.digitar("#contato", "11987654321")
    tela.digitar(item(1, "item_produto"), "Caneca")
    tela.digitar(item(1, "item_valor"), "3550")
    tela.clicar("#adicionar-item")
    tela.digitar(item(2, "item_produto"), "Camiseta")
    tela.digitar(item(2, "item_valor"), "9990")
    tela.clicar("#grupo-pagamento input[value='PIX']")
    tela.clicar("#grupo-entrega input[value='Entrega em mãos']")


# Elementos visíveis (dentro da página, exceto o cabeçalho) que ultrapassam a janela
# ou que escondem conteúdo com overflow.
CORTES = """
(() => {
  const ruins = [];
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  document.querySelectorAll('main *').forEach(el => {
    if (el.closest('template, svg') || el.offsetParent === null) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const nome = el.tagName + '.' + el.className;
    if (r.left < -0.5 || r.right > vw + 0.5) ruins.push(nome + ' fora na horizontal');
    if (LIMITE_VERTICAL && (r.top < -0.5 || r.bottom > vh + 0.5))
      ruins.push(nome + ' fora na vertical');
    const o = getComputedStyle(el);
    const rola = /(auto|scroll|hidden|clip)/.test(o.overflowX + o.overflowY);
    if (rola && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1))
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
    for seletor in ("#cliente", "#adicionar-item", "#resumo-total", "button[type=submit]"):
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
        " .escolha-card, textarea')].filter(e => e.getBoundingClientRect().height < 44)"
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
        "['#grupo-pagamento', '.secao-obs', '#grupo-entrega', '.resumo'].map(s => {"
        " const r = document.querySelector(s).getBoundingClientRect();"
        " return [Math.round(r.left * 10) / 10, Math.round(r.right * 10) / 10] })"
    )
    pagamento, obs, entrega, resumo = bordas
    assert obs == pagamento  # Informações complementares = largura de Forma de pagamento
    assert resumo == entrega  # Resumo = largura de Entrega
