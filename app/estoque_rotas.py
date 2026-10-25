"""Rotas da tela Estoque (/estoque): consulta das três áreas, Registrar Compra e edição.

Busca, filtros e ordenação são parâmetros da URL (cada área com os seus), sem JavaScript.
Toda entrada no estoque é feita pela janela Registrar Compra (POST /estoque/compras, enviado
pelo JavaScript da tela, com resposta em JSON); não há cadastro direto de lotes. A edição de
um lote só corrige a descrição: o saldo, a data e o custo vêm da compra.
"""

import re
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from app.auth import sessoes
from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario, origem_confiavel
from app.custos import GRUPOS_CUSTO_KG, custo_medio_por_kg, formatar_custo_aprox
from app.custos_repositorio import (
    FalhaAoConsultarCustos,
    RepositorioCustos,
    get_repositorio_custos,
)
from app.estoque import (
    AREAS_ITENS,
    CATEGORIAS_TELA,
    TIPOS_FILAMENTO,
    Area,
    FormFilamento,
    FormItem,
    agrupar_filamentos,
    agrupar_itens,
    form_de_filamento,
    form_de_item,
    formatar_data,
    formatar_numero,
    formatar_peso,
    formatar_unidades,
    ler_filamento,
    ler_item,
    peso_total,
    validar_filamento,
    validar_item,
)
from app.estoque_compra import (
    CATEGORIAS,
    COLUNAS_COMPRA,
    LIMITE_ITENS,
    LIMITE_ROLOS,
    LOCAIS,
    LOCAIS_COM_NOME,
    LOGOS_LOCAIS,
    MATERIAIS,
    PESOS_ROLO_G,
    SECOES_COMPRA,
    CompraNaoSalva,
    cores_conhecidas,
    descrever_item,
    gravar_compra,
    local_da_compra,
    validar_compra,
)
from app.estoque_repositorio import (
    FalhaNoEstoque,
    LoteNaoEncontrado,
    RepositorioEstoque,
    get_repositorio_estoque,
)
from app.pedidos import LIMITE_IMAGEM_BYTES, LIMITE_IMAGEM_MB, formatar_brl

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["brl"] = formatar_brl
templates.env.filters["peso"] = formatar_peso
templates.env.filters["unidades"] = formatar_unidades
templates.env.filters["data_compra"] = formatar_data
templates.env.filters["numero"] = formatar_numero
templates.env.filters["custo"] = lambda v: "—" if v is None else formatar_custo_aprox(v)
templates.env.filters["tipo_filamento"] = lambda c: TIPOS_FILAMENTO.get(c, c)

# Id do lote na URL: só dígitos, dentro do limite de um bigint.
ID_LOTE = re.compile(r"[0-9]{1,18}")
# Foto da linha na posição n da janela Registrar Compra.
CAMPO_FOTO = re.compile(r"item_(\d{1,3})_foto")
# Prefixo dos parâmetros de busca de cada área na URL.
PREFIXOS = {"filamentos": "f", "acessorios": "a", "embalagens": "e"}
PARAMETROS_CONSULTA = ("f_busca", "f_material", "f_tipo", "f_ordem") + tuple(
    f"{p}_{campo}" for p in ("a", "e") for campo in ("busca", "ordem")
)

MENSAGEM_INDISPONIVEL = "Não foi possível carregar as compras agora. Tente novamente em instantes."
MENSAGEM_LOTE_INDISPONIVEL = (
    "Não foi possível carregar o estoque agora. Tente novamente em instantes."
)
MENSAGEM_NAO_SALVO = "Não foi possível salvar agora. Tente novamente em instantes."
MENSAGEM_EXPIRADA = "A página expirou. Confira os dados e salve de novo."
MENSAGEM_CORRIGIR = "Não foi possível salvar. Corrija os campos destacados."

# Sucesso: código na URL depois de salvar -> mensagem (nada do navegador vai para a tela).
SUCESSO = {
    "compra": "Compra registrada. Os itens já estão no estoque.",
    "filamento-editado": "Lote de filamento atualizado.",
}
for _area in AREAS_ITENS.values():
    SUCESSO[f"{_area.categoria}-editado"] = f"Lote de {_area.singular.lower()} atualizado."


def _pagina(request: Request, modelo: str, contexto: dict, status_code: int = 200):
    resposta = templates.TemplateResponse(request, modelo, contexto, status_code=status_code)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _nao_encontrado(request: Request):
    return _pagina(request, "estoque/nao_encontrado.html", {}, status_code=404)


def _indisponivel(request: Request):
    return _pagina(
        request, "estoque/aviso.html", {"erro": MENSAGEM_LOTE_INDISPONIVEL}, status_code=503
    )


# ---------- Consulta ----------


@router.get("/estoque", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def estoque(
    request: Request,
    categoria: str = "filamento",
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
    repo_custos: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    """Saldo atual por categoria (uma por vez), agrupado a partir dos lotes das compras."""
    if categoria not in CATEGORIAS_TELA:
        categoria = "filamento"
    contexto = {"erro": "", "categoria": categoria, "categorias": CATEGORIAS_TELA}
    try:
        if repo is None:
            raise FalhaNoEstoque
        lotes = repo.listar_estoque()
    except FalhaNoEstoque:
        contexto["erro"] = MENSAGEM_LOTE_INDISPONIVEL
        return _pagina(request, "estoque/estoque.html", contexto, status_code=503)

    if categoria == "filamento":
        grupos = agrupar_filamentos(lotes.filamentos)
        contexto["cards"] = {
            "peso_total": peso_total(lotes.filamentos),
            "rolos_total": sum((g.rolos for g in grupos), Decimal(0)),
            "rolos_por_material": _rolos_por_material(grupos),
            "medias": _medias(lotes.filamentos, _custos_de_referencia(repo_custos)),
        }
    else:
        area = AREAS_ITENS[categoria]
        grupos = agrupar_itens([i for i in lotes.itens if i.categoria == area.categoria])
    contexto["grupos"] = grupos
    contexto["fotos"] = _fotos_dos_grupos(repo, grupos)
    return _pagina(request, "estoque/estoque.html", contexto)


def _custos_de_referencia(repo: RepositorioCustos | None) -> dict[str, Decimal]:
    """R$/kg de cada filamento na tela Custos; sem o banco, os cards ficam sem o alerta."""
    try:
        if repo is None:
            raise FalhaAoConsultarCustos
        return {f.nome: f.valor_kg for f in repo.listar().filamentos}
    except FalhaAoConsultarCustos:
        return {}


def _rolos_por_material(grupos) -> list[tuple[str, Decimal]]:
    """Rolos disponíveis de PLA, PETG e PLA Multicolor (mesmos grupos de Custos); sem os zerados."""
    ordem = {"PLA": 0, "PETG": 1, "PLA Multicolor": 2}
    resultado = []
    for titulo, material, tipos in GRUPOS_CUSTO_KG:
        rolos = sum(
            (
                g.rolos
                for g in grupos
                if g.material == material and (tipos is None or g.tipo in tipos)
            ),
            Decimal(0),
        )
        if rolos:
            resultado.append((titulo.split(" (")[0], rolos))
    return sorted(resultado, key=lambda r: ordem[r[0]])


def _medias(filamentos, referencias: dict[str, Decimal]) -> list[dict]:
    """Custo médio/kg de PLA, PLA Multicolor e PETG (a regra da tela Custos).

    Cada card diz quanto a média está acima ou abaixo do R$/kg definido em Custos para o mesmo
    material (situacao "acima" ou "abaixo"; sem diferença ou sem referência, None).
    """
    ordem = {"PLA": 0, "PLA Multicolor": 1, "PETG": 2}
    cards = []
    for r in custo_medio_por_kg(filamentos):
        nome = r.titulo.split(" (")[0]
        referencia = referencias.get(nome)
        diferenca, situacao = Decimal(0), None
        if r.custo_kg is not None and referencia is not None and r.custo_kg != referencia:
            diferenca = abs(r.custo_kg - referencia)
            situacao = "acima" if r.custo_kg > referencia else "abaixo"
        cards.append(
            {"nome": nome, "custo": r.custo_kg, "situacao": situacao, "diferenca": diferenca}
        )
    return sorted(cards, key=lambda c: ordem[c["nome"]])


def _fotos_dos_grupos(repo: RepositorioEstoque, grupos) -> dict[str, str]:
    """Caminho -> URL assinada (privada, temporária); sem as fotos a lista continua útil."""
    try:
        return repo.assinar_imagens([g.imagem_caminho for g in grupos if g.imagem_caminho])
    except FalhaNoEstoque:
        return {}


@router.get("/compras", response_class=HTMLResponse, dependencies=[Depends(exigir_usuario)])
def compras(request: Request, repo: RepositorioEstoque | None = Depends(get_repositorio_estoque)):
    contexto = {
        "erro": "",
        "sucesso": SUCESSO.get(request.query_params.get("salvo", ""), ""),
        "tipos": TIPOS_FILAMENTO,
        # Janela Registrar Compra
        "locais": LOCAIS,
        "locais_com_nome": LOCAIS_COM_NOME,
        "logos_locais": LOGOS_LOCAIS,
        "categorias": CATEGORIAS,
        "secoes_compra": SECOES_COMPRA,
        "colunas_compra": COLUNAS_COMPRA,
        "limite_itens": LIMITE_ITENS,
        "limite_imagem_mb": LIMITE_IMAGEM_MB,
        "materiais_compra": MATERIAIS,
        "pesos_compra": PESOS_ROLO_G,
        "limite_rolos": LIMITE_ROLOS,
        "cores": [],
    }
    try:
        if repo is None:
            raise FalhaNoEstoque
        historico = repo.listar_compras()
    except FalhaNoEstoque:
        contexto["erro"] = MENSAGEM_INDISPONIVEL
        return _pagina(request, "estoque/compras.html", contexto, status_code=503)

    contexto["cores"] = cores_conhecidas(_cores_salvas(repo))
    contexto["resumo"] = historico
    contexto["ultimas"] = _ultimas_compras(repo, historico.recentes)
    return _pagina(request, "estoque/compras.html", contexto)


def _ultimas_compras(repo: RepositorioEstoque, recentes) -> list[dict]:
    """Cada compra pronta para a tela: local, itens descritos e fotos (URLs assinadas)."""
    caminhos = [i.imagem_caminho for c in recentes for i in c.itens if i.imagem_caminho]
    try:
        urls = repo.assinar_imagens(caminhos)
    except FalhaNoEstoque:
        urls = {}  # sem as miniaturas a lista continua útil
    return [
        {
            "data": c.data_compra,
            "local": local_da_compra(c.local_tipo, c.local_nome),
            "total": c.total,
            "frete": c.frete,
            "itens": [descrever_item(i) for i in c.itens],
            "fotos": [
                {"url": urls[i.imagem_caminho], "descricao": descrever_item(i)}
                for i in c.itens
                if i.imagem_caminho in urls
            ],
        }
        for c in recentes
    ]


# ---------- Registrar Compra ----------


def _json(conteudo: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(conteudo, status_code=status_code, headers={"Cache-Control": "no-store"})


def _cores_salvas(repo: RepositorioEstoque | None) -> list[str]:
    """Cores já gravadas (para reaproveitar a grafia); sem o banco, a compra segue sem isso."""
    try:
        return [] if repo is None else [f.cor for f in repo.listar_estoque().filamentos]
    except FalhaNoEstoque:
        return []


@router.post("/compras")
@router.post("/estoque/compras")
async def registrar_compra(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    async with request.form() as formulario:
        # Só os campos de texto e as fotos; qualquer usuário enviado pelo navegador é
        # ignorado: o responsável pela compra é sempre o usuário da sessão.
        textos = {c: v for c, v in formulario.multi_items() if isinstance(v, str)}
        valida = origem_confiavel(request) and sessoes.csrf_valido(
            request.state.csrf, textos.get("csrf", "")
        )
        if not valida:
            return _json({"erro": MENSAGEM_EXPIRADA}, status_code=403)
        imagens = {}
        for chave, valor in formulario.multi_items():
            encontrado = CAMPO_FOTO.fullmatch(chave)
            if encontrado and isinstance(valor, UploadFile):
                # Lê no máximo 1 byte além do limite: o excesso basta para recusar.
                imagens[int(encontrado.group(1))] = await valor.read(LIMITE_IMAGEM_BYTES + 1)
        compra = validar_compra(textos, imagens, _cores_salvas(repo))

    if compra.chave_envio is None:
        # Sem a chave da submissão não há proteção contra duplicação: a janela é antiga.
        return _json({"erro": MENSAGEM_EXPIRADA}, status_code=403)
    if compra.erros:
        return _json({"erro": MENSAGEM_CORRIGIR, "campos": compra.erros}, status_code=422)

    try:
        if repo is None:
            raise CompraNaoSalva
        gravar_compra(repo, compra, usuario.id)
    except CompraNaoSalva:
        return _json({"erro": MENSAGEM_NAO_SALVO}, status_code=503)
    return _json({"destino": "/compras?salvo=compra"})


# ---------- Edição da descrição do lote ----------


async def _ler_post(request: Request) -> tuple[dict[str, str], bool]:
    """(campos de texto, página válida): confere a origem e o token CSRF da sessão."""
    async with request.form() as formulario:
        dados = {chave: valor for chave, valor in formulario.items() if isinstance(valor, str)}
    valida = origem_confiavel(request) and sessoes.csrf_valido(
        request.state.csrf, dados.get("csrf", "")
    )
    return dados, valida


def _voltar(codigo: str, ancora: str) -> RedirectResponse:
    return RedirectResponse(f"/compras?salvo={codigo}#{ancora}", status_code=303)


def _lote_da_url(lote_id: str) -> int | None:
    return int(lote_id) if ID_LOTE.fullmatch(lote_id) else None


def _buscar_lote(repo: RepositorioEstoque | None, encontrar):
    """O lote procurado (None se não existir); FalhaNoEstoque se o banco não responder."""
    if repo is None:
        raise FalhaNoEstoque
    return encontrar(repo.listar_estoque())


def _lote_para_resumo(repo: RepositorioEstoque | None, encontrar):
    """Lote para o resumo da tela de edição; sem banco, a tela abre sem o resumo."""
    try:
        return _buscar_lote(repo, encontrar)
    except FalhaNoEstoque:
        return None


# ---------- Filamentos ----------


def _tela_filamento(
    request: Request, form: FormFilamento, lote, numero: int, erro: str = "", status_code=200
):
    contexto = {
        "form": form,
        "lote": lote,
        "lote_id": numero,
        "tipos": TIPOS_FILAMENTO,
        "erro": erro,
    }
    return _pagina(request, "estoque/filamento.html", contexto, status_code=status_code)


def _filamento(numero: int):
    return lambda estoque: next((f for f in estoque.filamentos if f.id == numero), None)


@router.get(
    "/estoque/filamentos/{lote_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def editar_filamento(
    request: Request,
    lote_id: str,
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    numero = _lote_da_url(lote_id)
    if numero is None:
        return _nao_encontrado(request)
    try:
        lote = _buscar_lote(repo, _filamento(numero))
    except FalhaNoEstoque:
        return _indisponivel(request)
    if lote is None:
        return _nao_encontrado(request)
    return _tela_filamento(request, form_de_filamento(lote), lote, numero)


@router.post("/estoque/filamentos/{lote_id}/editar", response_class=HTMLResponse)
async def salvar_filamento(
    request: Request,
    lote_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    numero = _lote_da_url(lote_id)
    if numero is None:
        return _nao_encontrado(request)

    # Só a descrição é lida: saldo, data ou custo enviados pelo navegador são ignorados.
    dados, valida = await _ler_post(request)
    form = ler_filamento(dados)
    if not valida or not validar_filamento(form):
        erro, status = (MENSAGEM_CORRIGIR, 422) if valida else (MENSAGEM_EXPIRADA, 403)
        lote = _lote_para_resumo(repo, _filamento(numero))
        return _tela_filamento(request, form, lote, numero, erro, status_code=status)

    try:
        if repo is None:
            raise FalhaNoEstoque
        repo.editar_filamento(numero, form.cor, form.material, form.tipo, form.marca, usuario.id)
    except LoteNaoEncontrado:
        return _nao_encontrado(request)
    except FalhaNoEstoque:
        lote = _lote_para_resumo(repo, _filamento(numero))
        return _tela_filamento(request, form, lote, numero, MENSAGEM_NAO_SALVO, status_code=503)
    return _voltar("filamento-editado", "filamentos")


# ---------- Acessórios e embalagens ----------


def _tela_item(
    request: Request, area: Area, form: FormItem, lote, numero: int, erro="", status_code=200
):
    contexto = {"area": area, "form": form, "lote": lote, "lote_id": numero, "erro": erro}
    return _pagina(request, "estoque/item.html", contexto, status_code=status_code)


def _item(area: Area, numero: int):
    return lambda estoque: next(
        (i for i in estoque.itens if i.id == numero and i.categoria == area.categoria), None
    )


@router.get(
    "/estoque/{slug}/{lote_id}/editar",
    response_class=HTMLResponse,
    dependencies=[Depends(exigir_usuario)],
)
def editar_item(
    request: Request,
    slug: str,
    lote_id: str,
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    area = AREAS_ITENS.get(slug)
    numero = _lote_da_url(lote_id)
    if area is None or numero is None:
        return _nao_encontrado(request)
    try:
        lote = _buscar_lote(repo, _item(area, numero))
    except FalhaNoEstoque:
        return _indisponivel(request)
    if lote is None:
        return _nao_encontrado(request)
    return _tela_item(request, area, form_de_item(lote), lote, numero)


@router.post("/estoque/{slug}/{lote_id}/editar", response_class=HTMLResponse)
async def salvar_item(
    request: Request,
    slug: str,
    lote_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioEstoque | None = Depends(get_repositorio_estoque),
):
    area = AREAS_ITENS.get(slug)
    numero = _lote_da_url(lote_id)
    if area is None or numero is None:
        return _nao_encontrado(request)

    # Só o nome é lido: quantidade, data ou custo enviados pelo navegador são ignorados.
    dados, valida = await _ler_post(request)
    form = ler_item(dados)
    if not valida or not validar_item(form):
        erro, status = (MENSAGEM_CORRIGIR, 422) if valida else (MENSAGEM_EXPIRADA, 403)
        lote = _lote_para_resumo(repo, _item(area, numero))
        return _tela_item(request, area, form, lote, numero, erro, status_code=status)

    try:
        if repo is None:
            raise FalhaNoEstoque
        repo.editar_item(numero, area.categoria, form.nome, usuario.id)
    except LoteNaoEncontrado:
        return _nao_encontrado(request)
    except FalhaNoEstoque:
        lote = _lote_para_resumo(repo, _item(area, numero))
        return _tela_item(request, area, form, lote, numero, MENSAGEM_NAO_SALVO, status_code=503)
    return _voltar(f"{area.categoria}-editado", area.slug)
