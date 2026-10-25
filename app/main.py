import re
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from app.auth import sessoes
from app.auth.repositorio import ServicoIndisponivel, Usuario
from app.auth.rotas import (
    NaoAutenticado,
    exigir_usuario,
    origem_confiavel,
    tratar_nao_autenticado,
    tratar_servico_indisponivel,
)
from app.auth.rotas import router as rotas_auth
from app.custos_rotas import router as rotas_custos
from app.estoque_rotas import router as rotas_estoque
from app.pedidos import (
    ALTERNANCIA_PAGAMENTO,
    ESCOLHA_OBRIGATORIA,
    ETAPAS_PRODUCAO,
    FILTROS_STATUS,
    FORMAS_ENTREGA,
    FORMAS_PAGAMENTO,
    LIMITE_COMENTARIO,
    LIMITE_IMAGEM_BYTES,
    MOVIMENTOS_ETAPA,
    MOVIMENTOS_PEDIDOS,
    ROTULOS_ENTREGA,
    ROTULOS_ETAPA,
    ROTULOS_PAGAMENTO,
    ROTULOS_STATUS,
    STATUS_PAGAMENTO,
    Item,
    PedidoNaoEditavel,
    PedidoNaoSalvo,
    editar_pedido_gravado,
    formatar_brl,
    formatar_contato,
    formatar_data_curta,
    gravar_pedido,
    ler_formulario,
    movimento_permitido,
    normalizar_comentario,
    pedido_do_banco,
    pedido_vazio,
    validar,
)
from app.pedidos_repositorio import (
    AlteracaoRecusada,
    ConflitoDeEtapa,
    FalhaAoAlterar,
    FalhaAoConsultar,
    FalhaAoMover,
    MovimentoInvalido,
    RepositorioPedidos,
    get_repositorio_pedidos,
)

BASE_DIR = Path(__file__).resolve().parent

# Imagem do item na posição n do formulário (enviada pelo JavaScript da tela).
CAMPO_IMAGEM = re.compile(r"item_imagem_(\d{1,5})")
# Id de pedido na URL: só dígitos, dentro do limite de um bigint.
ID_PEDIDO = re.compile(r"[0-9]{1,18}")
MENSAGEM_CONSULTA_INDISPONIVEL = (
    "Não foi possível carregar os pedidos agora. Tente novamente em instantes."
)

app = FastAPI(title="FormaHub 2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(rotas_auth)
app.include_router(rotas_custos)
app.include_router(rotas_estoque)
app.add_exception_handler(NaoAutenticado, tratar_nao_autenticado)
app.add_exception_handler(ServicoIndisponivel, tratar_servico_indisponivel)
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["brl"] = formatar_brl
templates.env.filters["contato"] = lambda texto: formatar_contato(texto) if texto else "—"
templates.env.filters["data_hora"] = lambda momento: momento.strftime("%d/%m/%Y às %H:%M")
templates.env.filters["data_curta"] = formatar_data_curta
templates.env.filters["pagamento"] = lambda codigo: ROTULOS_PAGAMENTO.get(codigo, codigo)
templates.env.filters["entrega"] = lambda codigo: ROTULOS_ENTREGA.get(codigo, codigo)
templates.env.filters["status_pagamento"] = lambda codigo: ROTULOS_STATUS.get(codigo, codigo)

MODULOS = [
    {"titulo": "Produtos", "descricao": "Cadastro e consulta dos produtos."},
    {
        "titulo": "Compras",
        "descricao": "Histórico de compras de insumos e registro de novas compras.",
        "links": [{"url": "/compras", "texto": "Ver compras", "principal": True}],
    },
    {
        "titulo": "Estoque",
        "descricao": "Saldo de filamentos, acessórios e embalagens, com o custo médio.",
        "links": [{"url": "/estoque", "texto": "Ver estoque", "principal": True}],
    },
    {
        "titulo": "Pedidos",
        "descricao": "Registro e acompanhamento de pedidos.",
        "links": [
            {"url": "/pedidos", "texto": "Ver pedidos", "principal": True},
            {"url": "/pedidos/novo", "texto": "Novo pedido"},
        ],
    },
    {
        "titulo": "Produção",
        "descricao": "Quadro com as etapas de produção dos pedidos.",
        "links": [{"url": "/producao", "texto": "Ver produção", "principal": True}],
    },
    {
        "titulo": "Custos",
        "descricao": "Custos diretos e indiretos da empresa.",
        "links": [{"url": "/custos", "texto": "Ver custos", "principal": True}],
    },
]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"modulos": MODULOS})


def _tela_pedido(
    request: Request,
    pedido,
    sucesso: str = "",
    erro_geral: str = "",
    status_code: int = 200,
    edicao=None,
):
    """Tela do formulário. `edicao`: o pedido gravado, quando é a tela de Editar pedido."""
    return templates.TemplateResponse(
        request,
        "pedidos/novo.html",
        {
            "pedido": pedido,
            "edicao": edicao,
            "novo_item": Item(),
            "sucesso": sucesso,
            "erro_geral": erro_geral,
            "formas_pagamento": FORMAS_PAGAMENTO,
            "status_pagamento": STATUS_PAGAMENTO,
            "escolha_obrigatoria": ESCOLHA_OBRIGATORIA,
            "formas_entrega": FORMAS_ENTREGA,
        },
        status_code=status_code,
    )


@app.get("/pedidos/novo", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def novo_pedido(request: Request):
    return _tela_pedido(request, pedido_vazio())


async def _ler_imagens(formulario) -> dict[int, bytes]:
    """Posição do item -> conteúdo da imagem (lê no máximo 1 byte além do limite)."""
    imagens = {}
    for chave, valor in formulario.multi_items():
        encontrado = CAMPO_IMAGEM.fullmatch(chave)
        if encontrado and isinstance(valor, UploadFile):
            imagens[int(encontrado.group(1))] = await valor.read(LIMITE_IMAGEM_BYTES + 1)
    return imagens


@app.post("/pedidos/novo", response_class=HTMLResponse)
async def salvar_pedido(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    async with request.form() as formulario:
        # Só os campos de texto; qualquer usuário enviado pelo navegador é ignorado:
        # o criador do pedido é sempre o usuário da sessão.
        textos: dict[str, list[str]] = {}
        for chave, valor in formulario.multi_items():
            if isinstance(valor, str):
                textos.setdefault(chave, []).append(valor)

        csrf = textos.get("csrf", [""])[0]
        if not origem_confiavel(request) or not sessoes.csrf_valido(request.state.csrf, csrf):
            return _tela_pedido(
                request,
                ler_formulario(textos),
                erro_geral="A página expirou. Confira os dados e salve de novo.",
                status_code=403,
            )

        pedido = validar(ler_formulario(textos, await _ler_imagens(formulario)))

    if not pedido.valido:
        return _tela_pedido(request, pedido, status_code=422)

    try:
        if repo is None:
            raise PedidoNaoSalvo
        gravar_pedido(repo, pedido, usuario.id)
    except PedidoNaoSalvo:
        return _tela_pedido(
            request,
            pedido,
            erro_geral="Não foi possível salvar o pedido agora. Tente novamente em instantes.",
            status_code=503,
        )

    unidades = "item" if pedido.quantidade_total == 1 else "itens"
    sucesso = (
        f"Pedido de {pedido.cliente} salvo com sucesso — "
        f"{pedido.quantidade_total} {unidades}, total {formatar_brl(pedido.total)}."
    )
    return _tela_pedido(request, pedido_vazio(), sucesso=sucesso)


# ---------- Consulta ----------


def _pagina_sem_cache(request: Request, modelo: str, contexto: dict, status_code: int = 200):
    """Telas de consulta: dados de clientes e URLs temporárias não ficam no cache."""
    resposta = templates.TemplateResponse(request, modelo, contexto, status_code=status_code)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.get("/pedidos", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def listar_pedidos(
    request: Request,
    status: str = "",
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    filtros = dict(FILTROS_STATUS)
    base = {"filtros": FILTROS_STATUS, "status": status if status in filtros else ""}
    try:
        if repo is None:
            raise FalhaAoConsultar
        todos = repo.listar_pedidos()
        # A etapa de cada pedido vem do quadro de produção (mesma fonte da aba Produção). O
        # quadro deixa de fora o pedido concluído (entregue e pago): ele conta como finalizado.
        # Pedido cancelado também fica fora do quadro: aparece só no filtro "Cancelado".
        no_quadro = {c.id: c.etapa for c in repo.listar_producao()}
        etapas = {
            p.id: "cancelado" if p.cancelado else no_quadro.get(p.id, "entregue") for p in todos
        }
    except FalhaAoConsultar:
        contexto = {
            **base,
            "pedidos": [],
            "imagens": {},
            "total": 0,
            "contagens": {},
            "erro": MENSAGEM_CONSULTA_INDISPONIVEL,
        }
        return _pagina_sem_cache(request, "pedidos/lista.html", contexto, status_code=503)

    contagens = {codigo: 0 for codigo in filtros}
    for p in todos:
        if etapas.get(p.id) in contagens:
            contagens[etapas[p.id]] += 1
    ativos = [p for p in todos if not p.cancelado]
    pedidos = [p for p in todos if etapas.get(p.id) == status] if base["status"] else ativos

    # No máximo uma foto por card (a de destaque, escolhida no banco), assinadas num só pedido
    # ao Storage. Se falhar, os cards aparecem sem foto, com um aviso discreto.
    caminhos = [p.imagem_caminho for p in pedidos if p.imagem_caminho]
    try:
        imagens = repo.assinar_imagens(caminhos) if caminhos else {}
    except FalhaAoConsultar:
        imagens = {}
    contexto = {
        **base,
        "pedidos": pedidos,
        "imagens": imagens,
        "total": len(ativos),
        "contagens": contagens,
        "etapas": etapas,
        "rotulos_etapa": filtros,
        "movimentos": MOVIMENTOS_PEDIDOS,
        "alternancia_pagamento": ALTERNANCIA_PAGAMENTO,
        "erro": "",
    }
    return _pagina_sem_cache(request, "pedidos/lista.html", contexto)


@app.get(
    "/pedidos/{pedido_id}", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)]
)
def ver_pedido(
    request: Request,
    pedido_id: str,
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    try:
        if repo is None:
            raise FalhaAoConsultar
        pedido = repo.consultar_pedido(int(pedido_id)) if ID_PEDIDO.fullmatch(pedido_id) else None
    except FalhaAoConsultar:
        contexto = {"erro": MENSAGEM_CONSULTA_INDISPONIVEL}
        return _pagina_sem_cache(request, "pedidos/indisponivel.html", contexto, status_code=503)
    if pedido is None:
        return _pagina_sem_cache(request, "pedidos/nao_encontrado.html", {}, status_code=404)

    # URLs assinadas só para as imagens que existem; sem elas o pedido aparece mesmo assim.
    caminhos = [item.imagem_caminho for item in pedido.itens if item.imagem_caminho]
    imagens_indisponiveis = False
    try:
        imagens = repo.assinar_imagens(caminhos) if caminhos else {}
    except FalhaAoConsultar:
        imagens, imagens_indisponiveis = {}, True
    contexto = {
        "pedido": pedido,
        "imagens": imagens,
        "imagens_indisponiveis": imagens_indisponiveis,
        "csrf": request.state.csrf,
        "quantidade_total": sum(item.quantidade for item in pedido.itens),
    }
    return _pagina_sem_cache(request, "pedidos/detalhe.html", contexto)


# ---------- Editar e cancelar ----------

NAO_EDITAVEL = {
    "entregue": "Este pedido já foi entregue e não pode mais ser editado nem cancelado.",
    "cancelado": "Este pedido foi cancelado e não pode mais ser alterado.",
}


def _pedido_para_alterar(request: Request, repo: RepositorioPedidos | None, pedido_id: str):
    """(pedido, None) se existe e pode ser alterado; senão (None, página de resposta)."""
    try:
        if repo is None:
            raise FalhaAoConsultar
        pedido = repo.consultar_pedido(int(pedido_id)) if ID_PEDIDO.fullmatch(pedido_id) else None
    except FalhaAoConsultar:
        contexto = {"erro": MENSAGEM_CONSULTA_INDISPONIVEL}
        return None, _pagina_sem_cache(request, "pedidos/indisponivel.html", contexto, 503)
    if pedido is None:
        return None, _pagina_sem_cache(request, "pedidos/nao_encontrado.html", {}, 404)
    if not pedido.editavel:
        motivo = "cancelado" if pedido.cancelado else "entregue"
        contexto = {"erro": NAO_EDITAVEL[motivo]}
        return None, _pagina_sem_cache(request, "pedidos/indisponivel.html", contexto, 409)
    return pedido, None


@app.get(
    "/pedidos/{pedido_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def editar_pedido_tela(
    request: Request,
    pedido_id: str,
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    consultado, resposta = _pedido_para_alterar(request, repo, pedido_id)
    if resposta is not None:
        return resposta
    return _tela_pedido(request, pedido_do_banco(consultado), edicao=consultado)


@app.post("/pedidos/{pedido_id}/editar", response_class=HTMLResponse)
async def editar_pedido(
    request: Request,
    pedido_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    consultado, resposta = _pedido_para_alterar(request, repo, pedido_id)
    if resposta is not None:
        return resposta

    async with request.form() as formulario:
        textos: dict[str, list[str]] = {}
        for chave, valor in formulario.multi_items():
            if isinstance(valor, str):
                textos.setdefault(chave, []).append(valor)

        csrf = textos.get("csrf", [""])[0]
        if not origem_confiavel(request) or not sessoes.csrf_valido(request.state.csrf, csrf):
            return _tela_pedido(
                request,
                ler_formulario(textos),
                erro_geral="A página expirou. Confira os dados e salve de novo.",
                status_code=403,
                edicao=consultado,
            )

        pedido = validar(ler_formulario(textos, await _ler_imagens(formulario)))

    if not pedido.valido:
        return _tela_pedido(request, pedido, status_code=422, edicao=consultado)

    atuais = {item.imagem_caminho for item in consultado.itens if item.imagem_caminho}
    try:
        editar_pedido_gravado(repo, consultado.id, pedido, usuario.id, atuais)
    except PedidoNaoEditavel:
        contexto = {
            "erro": "Este pedido foi alterado por outro usuário e não pode mais ser editado."
        }
        return _pagina_sem_cache(request, "pedidos/indisponivel.html", contexto, 409)
    except PedidoNaoSalvo:
        return _tela_pedido(
            request,
            pedido,
            erro_geral="Não foi possível salvar o pedido agora. Tente novamente em instantes.",
            status_code=503,
            edicao=consultado,
        )

    # Mostra o que ficou gravado (imagens mantidas e novas); o JavaScript segue para o detalhe.
    try:
        gravado = repo.consultar_pedido(consultado.id) or consultado
    except FalhaAoConsultar:
        gravado = consultado
    sucesso = f"Pedido de {pedido.cliente} atualizado — total {formatar_brl(pedido.total)}."
    tela = pedido_do_banco(gravado) if gravado is not consultado else pedido
    return _tela_pedido(request, tela, sucesso=sucesso, edicao=gravado)


@app.get(
    "/pedidos/{pedido_id}/cancelar",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def confirmar_cancelamento(
    request: Request,
    pedido_id: str,
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    pedido, resposta = _pedido_para_alterar(request, repo, pedido_id)
    if resposta is not None:
        return resposta
    return _pagina_sem_cache(
        request,
        "pedidos/cancelar.html",
        {"pedido": pedido, "csrf": request.state.csrf, "erro": ""},
    )


@app.post("/pedidos/{pedido_id}/cancelar", response_class=HTMLResponse)
async def cancelar_pedido(
    request: Request,
    pedido_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    pedido, resposta = _pedido_para_alterar(request, repo, pedido_id)
    if resposta is not None:
        return resposta

    async with request.form() as formulario:
        csrf = str(formulario.get("csrf", ""))
    if not origem_confiavel(request) or not sessoes.csrf_valido(request.state.csrf, csrf):
        contexto = {
            "pedido": pedido,
            "csrf": request.state.csrf,
            "erro": "A página expirou. Confirme o cancelamento de novo.",
        }
        return _pagina_sem_cache(request, "pedidos/cancelar.html", contexto, status_code=403)

    try:
        repo.cancelar_pedido(pedido.id, usuario.id)
    except AlteracaoRecusada:
        contexto = {"erro": "Este pedido foi alterado por outro usuário. Recarregue a lista."}
        return _pagina_sem_cache(request, "pedidos/indisponivel.html", contexto, 409)
    except FalhaAoAlterar:
        contexto = {
            "pedido": pedido,
            "csrf": request.state.csrf,
            "erro": "Não foi possível cancelar o pedido agora. Tente novamente em instantes.",
        }
        return _pagina_sem_cache(request, "pedidos/cancelar.html", contexto, status_code=503)
    return RedirectResponse(f"/pedidos/{pedido.id}", status_code=303)


# ---------- Produção ----------

MENSAGEM_PRODUCAO_INDISPONIVEL = (
    "Não foi possível carregar a produção agora. Tente novamente em instantes."
)
MOVIMENTO = {
    "conflito": (409, "Este pedido foi atualizado por outro usuário."),
    "invalido": (422, "Este movimento não é permitido para a etapa atual do pedido."),
    "indisponivel": (503, "Não foi possível mover o pedido agora. Tente novamente."),
    "expirada": (403, "A página expirou. Recarregue a página e tente de novo."),
}


@app.get("/producao", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def producao(request: Request, repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos)):
    contexto = {
        "colunas": [],
        "imagens": {},
        "total": 0,
        "movimentos": MOVIMENTOS_ETAPA,
        "alternancia_pagamento": ALTERNANCIA_PAGAMENTO,
        "limite_comentario": LIMITE_COMENTARIO,
        "erro": "",
    }
    try:
        if repo is None:
            raise FalhaAoConsultar
        cards = repo.listar_producao()
    except FalhaAoConsultar:
        contexto["erro"] = MENSAGEM_PRODUCAO_INDISPONIVEL
        return _pagina_sem_cache(request, "producao.html", contexto, status_code=503)

    # No máximo uma foto por card, assinadas num só pedido ao Storage; se falhar, o quadro
    # aparece sem fotos (com aviso discreto nos cards que teriam foto).
    caminhos = [c.imagem_caminho for c in cards if c.imagem_caminho]
    try:
        contexto["imagens"] = repo.assinar_imagens(caminhos) if caminhos else {}
    except FalhaAoConsultar:
        contexto["imagens"] = {}
    contexto["colunas"] = [
        {"etapa": codigo, "titulo": titulo, "cards": [c for c in cards if c.etapa == codigo]}
        for codigo, titulo in ETAPAS_PRODUCAO
    ]
    contexto["total"] = len(cards)
    return _pagina_sem_cache(request, "producao.html", contexto)


def _resposta_movimento(motivo: str) -> JSONResponse:
    return _resposta_erro(MOVIMENTO, motivo)


def _resposta_erro(mensagens: dict, motivo: str) -> JSONResponse:
    status, mensagem = mensagens[motivo]
    return JSONResponse(
        {"ok": False, "erro": motivo, "mensagem": mensagem},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/producao/{pedido_id}/mover")
async def mover_producao(
    request: Request,
    pedido_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    async with request.form() as formulario:
        # Só estes campos; qualquer usuário enviado pelo navegador é ignorado: quem move é
        # sempre o usuário da sessão.
        csrf = str(formulario.get("csrf", ""))
        esperada = str(formulario.get("etapa_esperada", ""))
        nova = str(formulario.get("nova_etapa", ""))

    if not origem_confiavel(request) or not sessoes.csrf_valido(request.state.csrf, csrf):
        return _resposta_movimento("expirada")
    if not ID_PEDIDO.fullmatch(pedido_id) or not movimento_permitido(esperada, nova):
        return _resposta_movimento("invalido")

    try:
        if repo is None:
            raise FalhaAoMover
        atualizada = repo.mover_etapa(int(pedido_id), esperada, nova, usuario.id)
    except ConflitoDeEtapa:
        return _resposta_movimento("conflito")
    except MovimentoInvalido:
        return _resposta_movimento("invalido")
    except FalhaAoMover:
        return _resposta_movimento("indisponivel")

    return JSONResponse(
        {
            "ok": True,
            "pedido_id": atualizada.id,
            "etapa": atualizada.etapa,
            "titulo": ROTULOS_ETAPA[atualizada.etapa],
            # Botões do card na nova etapa (vazio em Entregue).
            "movimentos": [
                {"destino": m.destino, "texto": m.texto, "retorno": m.retorno}
                for m in MOVIMENTOS_ETAPA.get(atualizada.etapa, [])
            ],
        },
        headers={"Cache-Control": "no-store"},
    )


# ---------- Produção: comentário e pagamento ----------

ALTERACAO = {
    "nao_encontrado": (404, "Este pedido não foi encontrado. Recarregue a página."),
    "usuario": (403, "Seu usuário não pode fazer esta alteração."),
    "expirada": MOVIMENTO["expirada"],
}
COMENTARIO = {
    **ALTERACAO,
    "conflito": (
        409,
        "O comentário deste pedido foi alterado por outro usuário. "
        "O quadro foi atualizado; seu texto continua no campo para você conferir.",
    ),
    "invalido": (
        422,
        f"O comentário pode ter no máximo {LIMITE_COMENTARIO:,} caracteres.".replace(",", "."),
    ),
    "indisponivel": (503, "Não foi possível salvar o comentário agora. Tente novamente."),
}
PAGAMENTO = {
    **ALTERACAO,
    "conflito": (409, "O pagamento deste pedido foi alterado por outro usuário."),
    "invalido": (422, "Esta alteração de pagamento não é permitida."),
    "indisponivel": (503, "Não foi possível alterar o pagamento agora. Tente novamente."),
}


def texto_auditoria(nome: str | None, momento) -> str:
    """'Atualizado por Kassia em 01/10/2026 às 14:32' (vazio se nunca foi atualizado)."""
    if not nome or momento is None:
        return ""
    return f"Atualizado por {nome} em {momento.strftime('%d/%m/%Y às %H:%M')}"


templates.env.globals["texto_auditoria"] = texto_auditoria


def _pedido_da_url(request: Request, pedido_id: str, csrf: str) -> str | None:
    """Motivo da recusa antes de chegar ao banco (página expirada ou id inválido), ou None."""
    if not origem_confiavel(request) or not sessoes.csrf_valido(request.state.csrf, csrf):
        return "expirada"
    if not ID_PEDIDO.fullmatch(pedido_id):
        return "nao_encontrado"
    return None


@app.post("/producao/{pedido_id}/comentario")
async def salvar_comentario_producao(
    request: Request,
    pedido_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    async with request.form() as formulario:
        # Só estes campos; qualquer usuário enviado pelo navegador é ignorado: quem comenta é
        # sempre o usuário da sessão.
        csrf = str(formulario.get("csrf", ""))
        esperado = normalizar_comentario(str(formulario.get("comentario_esperado", "")))
        novo = normalizar_comentario(str(formulario.get("comentario", "")))

    motivo = _pedido_da_url(request, pedido_id, csrf)
    if motivo is None and novo is not None and len(novo) > LIMITE_COMENTARIO:
        motivo = "invalido"
    if motivo:
        return _resposta_erro(COMENTARIO, motivo)

    try:
        if repo is None:
            raise FalhaAoAlterar
        salvo = repo.salvar_comentario_producao(int(pedido_id), esperado, novo, usuario.id)
    except AlteracaoRecusada as recusa:
        return _resposta_erro(COMENTARIO, recusa.motivo)
    except FalhaAoAlterar:
        return _resposta_erro(COMENTARIO, "indisponivel")

    return JSONResponse(
        {
            "ok": True,
            "pedido_id": salvo.id,
            "comentario": salvo.comentario or "",
            "auditoria": texto_auditoria(salvo.atualizado_por_nome, salvo.atualizado_em),
            "mensagem": "Comentário salvo." if salvo.comentario else "Comentário removido.",
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/producao/{pedido_id}/pagamento")
async def alterar_pagamento_producao(
    request: Request,
    pedido_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioPedidos | None = Depends(get_repositorio_pedidos),
):
    async with request.form() as formulario:
        # Quem altera é sempre o usuário da sessão (nenhum usuário do navegador é lido).
        csrf = str(formulario.get("csrf", ""))
        esperado = str(formulario.get("status_esperado", ""))
        novo = str(formulario.get("novo_status", ""))

    motivo = _pedido_da_url(request, pedido_id, csrf)
    if motivo is None and ALTERNANCIA_PAGAMENTO.get(esperado, ("",))[0] != novo:
        motivo = "invalido"
    if motivo:
        return _resposta_erro(PAGAMENTO, motivo)

    try:
        if repo is None:
            raise FalhaAoAlterar
        alterado = repo.alterar_status_pagamento(int(pedido_id), esperado, novo, usuario.id)
    except AlteracaoRecusada as recusa:
        return _resposta_erro(PAGAMENTO, recusa.motivo)
    except FalhaAoAlterar:
        return _resposta_erro(PAGAMENTO, "indisponivel")

    status = alterado.status_pagamento
    return JSONResponse(
        {
            "ok": True,
            "pedido_id": alterado.id,
            "status": status,
            "rotulo": ROTULOS_STATUS[status],
            "acao": ALTERNANCIA_PAGAMENTO[status][1],
            "mensagem": f"Pagamento marcado como {ROTULOS_STATUS[status].lower()}.",
        },
        headers={"Cache-Control": "no-store"},
    )
