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
        self.cmd("Page.navigate", url=self.base_url + caminho)
        self._esperar_carregar()

    def _esperar_carregar(self):
        esperar(
            lambda: self.js("document.readyState === 'complete' && !window.__antiga"),
            mensagem="página não carregou",
        )

    def enviar(self):
        self.js(
            "window.__antiga = true;"
            " document.querySelector('#form-pedido button[type=submit]').click()"
        )
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
    if (rola && cortado && !el.classList.contains('upload-nome'))
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
        "['#grupo-pagamento', '.secao-obs', '#grupo-entrega', '.resumo'].map(s => {"
        " const r = document.querySelector(s).getBoundingClientRect();"
        " return [Math.round(r.left * 10) / 10, Math.round(r.right * 10) / 10] })"
    )
    pagamento, obs, entrega, resumo = bordas
    assert obs == pagamento  # Informações complementares = largura de Forma de pagamento
    assert resumo == entrega  # Resumo = largura de Entrega


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

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza validado com sucesso")
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

    assert tela.texto(".alerta-sucesso").startswith("Pedido de Maria Souza validado com sucesso")
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
