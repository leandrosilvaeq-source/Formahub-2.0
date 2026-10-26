"""Testes das telas (Novo pedido, consulta e Produção) em um navegador real (Edge ou Chrome,
sem interface).

Controla o navegador pelo protocolo DevTools usando `websockets`, que já vem com o
projeto. Se nenhum navegador for encontrado, os testes são pulados.
Para indicar outro navegador: variável de ambiente FORMAHUB_NAVEGADOR=<caminho do executável>.
"""

import itertools
import json
import os
import re
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
        if self.js(f"document.querySelector({json.dumps(seletor)}).type") == "date":
            return self.escolher_data(seletor, texto)
        self.js(f"document.querySelector({json.dumps(seletor)}).focus()")
        for letra in texto:
            self.cmd("Input.insertText", text=letra)

    def escolher_data(self, seletor: str, texto: str):
        """Campo de calendário: dd mm aa ou dd mm aaaa (só números) vira o valor aaaa-mm-dd."""
        d = re.sub(r"\D", "", texto)
        dia, mes, ano = d[:2], d[2:4], d[4:]
        iso = f"{ano if len(ano) == 4 else '20' + ano}-{mes}-{dia}"
        self.js(
            f"(() => {{ const c = document.querySelector({json.dumps(seletor)});"
            f" c.value = {json.dumps(iso)};"
            " c.dispatchEvent(new Event('input', {bubbles: true}));"
            " c.dispatchEvent(new Event('change', {bubbles: true})) })()"
        )

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


def erro_do_prazo(tela):
    return tela.js(
        "(() => { const e = document.getElementById('prazo_entrega-erro');"
        " return e ? e.textContent : null })()"
    )


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
    assert tela.valor("#prazo_entrega") == "2026-10-05"
    assert erro_do_prazo(tela) is None

    tela.digitar("#cliente", "Maria Souza")
    tela.enviar()

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza salvo com sucesso")
    assert next(iter(repo_pedidos.pedidos.values())).prazo_entrega == date(2026, 10, 5)
    assert tela.valor("#prazo_entrega") == ""


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

    assert pagina.js("document.querySelectorAll('#tabela-pedidos tbody tr').length") == 2
    # A tabela rola dentro do próprio quadro; a página não rola na horizontal.
    assert pagina.js("document.documentElement.scrollWidth") <= largura
    # Botões com alvo de toque confortável.
    alturas = pagina.js(
        "[...document.querySelectorAll('main .botao')].map(b => b.getBoundingClientRect().height)"
    )
    assert alturas and min(alturas) >= 44


def test_filtros_de_coluna_prazo_em_ordem_de_data_e_cliente(pagina, repo_pedidos):
    from datetime import date

    from tests.test_pedidos_consulta import novo_pedido

    novo_pedido(repo_pedidos, cliente="Ana", prazo_entrega=date(2026, 10, 20))
    novo_pedido(repo_pedidos, cliente="Bia", prazo_entrega=date(2026, 9, 30))
    novo_pedido(repo_pedidos, cliente="Cris", prazo_entrega=date(2026, 10, 5))
    pagina.tela(1280, 800)
    pagina.abrir("/pedidos")
    visiveis = (
        "[...document.querySelectorAll('#tabela-pedidos tbody tr:not([hidden])')]"
        ".map(l => l.dataset.cliente)"
    )

    def filtrar(chave, manter):
        """Abre o filtro da coluna, desmarca tudo e marca só o valor `manter`."""
        pagina.clicar(f'.filtro-botao[data-filtro="{chave}"]')
        rotulos = pagina.js(
            "[...document.querySelectorAll('.filtro-opcao span')].map(s => s.textContent)"
        )
        pagina.js("document.querySelector('.filtro-todos input').click()")
        pagina.js(
            "[...document.querySelectorAll('.filtro-opcao')]"
            f".find(o => o.textContent === {json.dumps(manter)}).querySelector('input').click()"
        )
        pagina.js("document.body.click()")  # fecha o painel
        return rotulos[1:]  # sem o "(Selecionar tudo)"

    # Prazo: valores em ordem de data (não de dia), no mesmo formato da tabela.
    assert filtrar("prazo", "05/10/26") == ["30/09/26", "05/10/26", "20/10/26"]
    assert pagina.js(visiveis) == ["Cris"]

    pagina.clicar('.filtro-botao[data-filtro="prazo"]')
    pagina.clicar(".filtro-limpar")
    assert sorted(filtrar("cliente", "Bia")) == ["Ana", "Bia", "Cris"]
    assert pagina.js(visiveis) == ["Bia"]


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
    pagina.js("document.querySelector('.pedido-tabela-foto').scrollIntoView({block: 'center'})")
    esperar(lambda: pagina.js("document.querySelector('.pedido-tabela-foto').naturalWidth > 0"))

    foto = pagina.js(
        "(() => { const i = document.querySelector('.pedido-tabela-foto');"
        " const r = i.getBoundingClientRect();"
        " return [r.width, r.height, getComputedStyle(i).objectFit, i.alt] })()"
    )
    largura_foto, altura_foto, ajuste, alternativo = foto
    assert ajuste == "cover"
    assert abs(largura_foto - altura_foto) < 1  # quadrada, recorte sem distorção
    assert 48 <= largura_foto <= 120  # compacta
    assert alternativo.startswith("Foto de referência: ")
    # Só o pedido com imagem tem foto.
    assert pagina.js("document.querySelectorAll('.pedido-tabela-foto').length") == 1
    assert pagina.js("document.documentElement.scrollWidth") <= largura


def centro(pagina, seletor):
    x, y, _, _ = pagina.js(
        f"(() => {{ const r = document.querySelector('{seletor}').getBoundingClientRect();"
        " return [r.left + r.width / 2, r.top + r.height / 2, r.width, r.height] })()"
    )
    return x, y


def mouse(pagina, x, y):
    pagina.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)


def previa_aberta(pagina):
    return pagina.js("!!document.querySelector('.foto-previa')")


@pytest.mark.parametrize("largura", [1280])
def test_previa_da_foto_abre_apos_2s_cancela_ao_sair_e_cabe_na_tela(
    pagina, servidor, repo_pedidos, largura
):
    pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(largura, 800)
    pagina.abrir("/pedidos")
    esperar(lambda: pagina.js("document.querySelector('.pedido-tabela-foto').naturalWidth > 0"))
    x, y = centro(pagina, ".foto-previa-botao")

    # Sai antes de 2 s: nada abre.
    mouse(pagina, x, y)
    time.sleep(0.8)
    mouse(pagina, 5, 5)
    time.sleep(1.6)
    assert not previa_aberta(pagina)

    # Fica 2 s: abre inteira dentro da tela; sair da foto e da prévia fecha; Esc também.
    mouse(pagina, x, y)
    esperar(lambda: previa_aberta(pagina), tempo=4, mensagem="a prévia não abriu")
    caixa = pagina.js(
        "(() => { const r = document.querySelector('.foto-previa').getBoundingClientRect();"
        " return [r.left, r.top, r.right, r.bottom, innerWidth, innerHeight] })()"
    )
    assert caixa[0] >= 0 and caixa[1] >= 0 and caixa[2] <= caixa[4] and caixa[3] <= caixa[5]
    mouse(pagina, 5, 5)
    esperar(lambda: not previa_aberta(pagina))

    mouse(pagina, x, y)
    esperar(lambda: previa_aberta(pagina), tempo=4)
    pagina.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
    esperar(lambda: not previa_aberta(pagina))


def test_previa_da_foto_abre_por_teclado_e_fecha_com_esc(pagina, servidor, repo_pedidos):
    pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(390, 800)
    pagina.abrir("/pedidos")
    esperar(lambda: pagina.js("document.querySelector('.pedido-tabela-foto').naturalWidth > 0"))

    pagina.js("document.querySelector('.foto-previa-botao').click()")
    assert previa_aberta(pagina)
    caixa = pagina.js(
        "(() => { const r = document.querySelector('.foto-previa').getBoundingClientRect();"
        " return [r.left, r.top, r.right, r.bottom, innerWidth, innerHeight] })()"
    )
    assert caixa[0] >= 0 and caixa[1] >= 0 and caixa[2] <= caixa[4] and caixa[3] <= caixa[5]
    assert pagina.js("document.activeElement.className").startswith("foto-previa-fechar")
    pagina.cmd("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape")
    esperar(lambda: not previa_aberta(pagina))
    assert pagina.js("document.activeElement.className") == "foto-previa-botao"


@pytest.mark.parametrize("largura", [390, 1280])
def test_lista_cards_acoes_com_icone_e_cabecalho_centralizado(
    pagina, servidor, repo_pedidos, largura
):
    pedidos_de_exemplo(repo_pedidos, servidor)
    pagina.tela(largura, 800)
    pagina.abrir("/pedidos")

    # Cards: título acima e número abaixo, centralizados, como o Total de Pedidos.
    cards = pagina.js(
        "[...document.querySelectorAll('.pedidos-total, .pedidos-filtro-item')].map(c => {"
        " const s = getComputedStyle(c); return [s.textAlign, s.alignItems, s.flexDirection] })"
    )
    assert cards and all(c == ["center", "center", "column"] for c in cards)
    assert "Pendentes" in pagina.texto(".pedidos-filtro")

    # Ações: ícone, nome acessível e área de toque mínima.
    for seletor in (".acao-icone[href$='/editar']", ".acao-icone.acao-perigo"):
        medidas = pagina.js(
            f'(() => {{ const a = document.querySelector("{seletor}");'
            " const r = a.getBoundingClientRect(); return [r.width, r.height,"
            " !!a.querySelector('svg'), a.title, a.getAttribute('aria-label')] })()"
        )
        assert medidas[0] >= 44 and medidas[1] >= 44 and medidas[2]
        assert medidas[3] and medidas[4]

    # Títulos das colunas no meio da altura da célula.
    desvios = pagina.js(
        "[...document.querySelectorAll('.tabela-pedidos thead th')].map(th => {"
        " const r = th.getBoundingClientRect(), c = th.firstElementChild || th;"
        " const t = document.createRange(); t.selectNodeContents(th);"
        " const q = t.getBoundingClientRect();"
        " return Math.abs((q.top + q.bottom) / 2 - (r.top + r.bottom) / 2) })"
    )
    assert all(d < 3 for d in desvios)
    assert (
        pagina.js("document.querySelector('.pedidos-etapa').getBoundingClientRect().height") >= 44
    )


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


# ---------- Status do pagamento ----------


def pagamento_js(pedido_id):
    return f"{card_js(pedido_id)}.querySelector('.producao-pagamento')"


def estado_pagamento(p, pedido_id):
    b = pagamento_js(pedido_id)
    return p.js(
        f"[{b}.dataset.status, {b}.textContent.trim(), {b}.getAttribute('aria-label'),"
        f" {b}.title, {b}.classList.contains('selo-status-' + {b}.dataset.status)]"
    )


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


# ---------- Ocultar e mostrar o comentário (card recolhido) ----------


def alternar_comentario_js(pedido_id):
    return f"{card_js(pedido_id)}.querySelector('.producao-comentario-alternar')"


def comentario_a_vista(p, pedido_id):
    """Texto (campo) e última atualização visíveis."""
    return visivel_no_card(p, pedido_id, ".producao-comentario-campo") and visivel_no_card(
        p, pedido_id, ".producao-comentario-info"
    )


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
        assert pagina.js("document.querySelectorAll('main section.secao').length") == 10, contexto
