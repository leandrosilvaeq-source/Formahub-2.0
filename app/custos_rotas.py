"""Tela Custos: cadastro persistente dos custos da empresa, com edição manual.

Sem JavaScript: "Editar" abre o formulário daquele registro (/custos?editar=...), "Cancelar"
volta ao /custos e "Salvar" envia um POST. Sucesso redireciona (PRG) com um aviso fixo; erro
mostra a página de novo, com o formulário aberto, o que foi digitado e as mensagens.

Segurança como nas demais telas: sessão obrigatória, CSRF + origem confiável, e o responsável
pela alteração é sempre o usuário da sessão (nada de usuário vindo do navegador).
"""

import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import sessoes
from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario, origem_confiavel
from app.custos import (
    AQUISICAO,
    CONSUMO,
    DIAS_MES,
    GRUPOS,
    HORAS_DIA,
    MANUTENCAO,
    MDO,
    PERDA,
    RESIDUAL,
    TARIFA,
    TIPOS_ITEM,
    TITULOS_ITEM,
    VIDA_UTIL,
    conferir_depreciacao,
    custo_por_hora,
    depreciacao,
    energia_por_hora,
    formatar_custo,
    formatar_custo_aprox,
    formatar_custo_curto,
    formatar_numero,
    ler_campo,
    ler_decimal,
    ler_nome,
    ler_quantidade,
    valor_do_campo,
)
from app.custos_repositorio import (
    CustoRecusado,
    Custos,
    FalhaAoConsultarCustos,
    FalhaAoSalvarCustos,
    RepositorioCustos,
    get_repositorio_custos,
)

TAMANHO_MAXIMO_FORMULARIO = 8192
ID_REGISTRO = re.compile(r"[0-9]{1,18}")
EDITAR = re.compile(rf"(?:filamento|item)-[0-9]{{1,18}}|valores-(?:{'|'.join(GRUPOS)})")

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["custo"] = formatar_custo
templates.env.filters["custo_aprox"] = formatar_custo_aprox
templates.env.filters["custo_curto"] = formatar_custo_curto
templates.env.filters["numero"] = formatar_numero

# Avisos de sucesso: o redirecionamento leva só um código, nunca texto.
AVISOS = {
    "filamento": "Valor do filamento atualizado.",
    "valores-energia": "Energia elétrica atualizada.",
    "valores-perdas": "Perdas atualizadas.",
    "valores-mdo": "Mão de Obra atualizada.",
    "valores-manutencao": "Manutenção atualizada.",
    "valores-depreciacao": "Depreciação atualizada.",
    "acessorios-novo": "Acessório cadastrado.",
    "acessorios-editado": "Acessório atualizado.",
    "embalagens-novo": "Embalagem cadastrada.",
    "embalagens-editado": "Embalagem atualizada.",
}
ANCORAS = {
    "filamento": "filamentos",
    "valores-energia": "energia",
    "valores-perdas": "perdas",
    "valores-mdo": "mdo",
    "valores-manutencao": "manutencao",
    "valores-depreciacao": "depreciacao",
    "acessorios-novo": "acessorios",
    "acessorios-editado": "acessorios",
    "embalagens-novo": "embalagens",
    "embalagens-editado": "embalagens",
}

MSG_EXPIRADA = "A página expirou. Confira os dados e salve de novo."
MSG_INDISPONIVEL = "Não foi possível salvar agora. Tente novamente em instantes."
MSG_CONSULTA = "Não foi possível carregar os custos agora. Tente novamente em instantes."
MSG_RECUSAS = {
    "usuario": "Seu usuário não pode fazer esta alteração.",
    "nao_encontrado": "Este registro não foi encontrado. Recarregue a página.",
    "invalido": "Os dados não foram aceitos. Confira os valores e tente de novo.",
}


class Estado:
    """O que a tela precisa além dos dados: formulários abertos, o que foi digitado e erros.

    Cada formulário tem um escopo: filamento-<id>, valores-<grupo>, item-<id> ou novo-<tipo>.
    """

    def __init__(self):
        self.abertos: set[str] = set()
        self.digitado: dict[str, dict[str, str]] = {}
        self.erros: dict[str, dict[str, str]] = {}  # escopo -> campo -> mensagem
        self.falhas: dict[str, str] = {}  # escopo -> mensagem geral do formulário
        self.aviso = ""
        self.erro_pagina = ""

    def erro(self, escopo: str, campo: str) -> str:
        return self.erros.get(escopo, {}).get(campo, "")

    def texto(self, escopo: str, campo: str, padrao: str = "") -> str:
        return self.digitado.get(escopo, {}).get(campo, padrao)


def _sem_cache(request: Request, contexto: dict, status_code: int = 200):
    resposta = templates.TemplateResponse(request, "custos.html", contexto, status_code=status_code)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


def _pagina(request: Request, repo: RepositorioCustos | None, estado: Estado, status: int = 200):
    custos: Custos | None = None
    erro = estado.erro_pagina
    try:
        if repo is None:
            raise FalhaAoConsultarCustos
        custos = repo.listar()
    except FalhaAoConsultarCustos:
        erro = erro or MSG_CONSULTA
        status = 503 if status < 400 else status

    contexto = {"estado": estado, "erro": erro, "csrf": request.state.csrf}
    if custos is not None:
        contexto.update(_montar(custos))
    return _sem_cache(request, contexto, status)


def _montar(custos: Custos) -> dict:
    """Valores e cálculos mostrados na tela, sempre a partir do que está salvo."""
    p = custos.parametros
    # Se uma chave faltar (banco alterado fora do app), a tela mostra "—" em vez de inventar.
    tarifa, consumo = p.get(TARIFA), p.get(CONSUMO)
    mdo, manutencao = p.get(MDO), p.get(MANUTENCAO)  # manutenção None = "A definir"
    energia = (
        energia_por_hora(tarifa, consumo) if tarifa is not None and consumo is not None else None
    )
    criterios = [p.get(chave) for chave in (AQUISICAO, RESIDUAL, VIDA_UTIL, DIAS_MES, HORAS_DIA)]
    # O valor por hora da depreciação é sempre derivado dos critérios (nunca guardado).
    deprec = depreciacao(*criterios) if all(c is not None for c in criterios) else None
    total_hora, completo = None, False
    if energia is not None and mdo is not None and deprec is not None:
        total_hora, completo = custo_por_hora(energia, mdo, deprec.por_hora, manutencao)
    return {
        "filamentos": custos.filamentos,
        "parametros": p,
        "grupos": GRUPOS,
        "energia_hora": energia,
        "depreciacao": deprec,
        "total_hora": total_hora,
        "total_completo": completo,
        "perda": p.get(PERDA),
        "itens": {
            rota: [i for i in custos.itens if i.tipo == codigo]
            for rota, codigo in TIPOS_ITEM.items()
        },
        "titulos_item": TITULOS_ITEM,
        "valor_do_campo": valor_do_campo,
    }


@router.get("/custos", response_class=HTMLResponse)
def custos(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    estado = Estado()
    estado.aviso = AVISOS.get(request.query_params.get("salvo", ""), "")
    editar = request.query_params.get("editar", "")
    if EDITAR.fullmatch(editar):
        estado.abertos.add(editar)
    return _pagina(request, repo, estado)


# ---------- Gravação ----------


async def _formulario(request: Request) -> dict[str, str]:
    corpo = await request.body()
    if len(corpo) > TAMANHO_MAXIMO_FORMULARIO:
        raise HTTPException(status_code=413, detail="Formulário grande demais.")
    dados = parse_qs(corpo.decode(errors="replace"), keep_blank_values=True)
    return {chave: valores[0] for chave, valores in dados.items()}


def _confiavel(request: Request, dados: dict[str, str]) -> bool:
    return origem_confiavel(request) and sessoes.csrf_valido(
        request.state.csrf, dados.get("csrf", "")
    )


def _recusar(request, repo, estado: Estado, escopo: str, dados, campos, mensagem, status):
    """Mostra a página de novo com o formulário aberto, o que foi digitado e a mensagem."""
    estado.abertos.add(escopo)
    estado.digitado[escopo] = {campo: dados.get(campo, "") for campo in campos}
    if mensagem:
        estado.falhas[escopo] = mensagem
    return _pagina(request, repo, estado, status)


def _sucesso(codigo: str):
    return RedirectResponse(f"/custos?salvo={codigo}#{ANCORAS[codigo]}", status_code=303)


def _falha_do_banco(erro: Exception) -> tuple[str, int]:
    if isinstance(erro, CustoRecusado):
        status = {"usuario": 403, "nao_encontrado": 404, "invalido": 422}.get(erro.motivo, 422)
        return MSG_RECUSAS.get(erro.motivo, MSG_INDISPONIVEL), status
    return MSG_INDISPONIVEL, 503


@router.post("/custos/filamentos/{filamento_id}")
async def salvar_filamento(
    request: Request,
    filamento_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    dados = await _formulario(request)
    estado, escopo, campos = Estado(), f"filamento-{filamento_id}", ["valor_kg"]
    if not _confiavel(request, dados):
        return _recusar(request, repo, estado, escopo, dados, campos, MSG_EXPIRADA, 403)
    if not ID_REGISTRO.fullmatch(filamento_id):
        return _recusar(
            request, repo, estado, escopo, dados, campos, MSG_RECUSAS["nao_encontrado"], 404
        )

    valor, erro = ler_decimal(dados.get("valor_kg", ""))
    if erro:
        estado.erros[escopo] = {"valor_kg": erro}
        return _recusar(request, repo, estado, escopo, dados, campos, "", 422)
    try:
        if repo is None:
            raise FalhaAoSalvarCustos
        repo.salvar_filamento(int(filamento_id), valor, usuario.id)
    except (CustoRecusado, FalhaAoSalvarCustos) as falha:
        mensagem, status = _falha_do_banco(falha)
        return _recusar(request, repo, estado, escopo, dados, campos, mensagem, status)
    return _sucesso("filamento")


@router.post("/custos/valores/{grupo}")
async def salvar_valores(
    request: Request,
    grupo: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    if grupo not in GRUPOS:
        raise HTTPException(status_code=404)
    dados = await _formulario(request)
    estado, escopo = Estado(), f"valores-{grupo}"
    campos = [campo.nome for campo in GRUPOS[grupo]]
    if not _confiavel(request, dados):
        return _recusar(request, repo, estado, escopo, dados, campos, MSG_EXPIRADA, 403)

    valores: dict[str, Decimal | None] = {}
    erros: dict[str, str] = {}
    for campo in GRUPOS[grupo]:
        valor, erro = ler_campo(campo, dados.get(campo.nome, ""))
        if erro:
            erros[campo.nome] = erro
        else:
            valores[campo.chave] = valor
    if not erros and grupo == "depreciacao":
        erros = conferir_depreciacao(valores)  # o residual não passa da aquisição
    if erros:
        estado.erros[escopo] = erros
        return _recusar(request, repo, estado, escopo, dados, campos, "", 422)
    try:
        if repo is None:
            raise FalhaAoSalvarCustos
        repo.salvar_parametros(valores, usuario.id)
    except (CustoRecusado, FalhaAoSalvarCustos) as falha:
        mensagem, status = _falha_do_banco(falha)
        return _recusar(request, repo, estado, escopo, dados, campos, mensagem, status)
    return _sucesso(escopo)


async def _salvar_item(request, tipo_rota, item_id, usuario, repo):
    if tipo_rota not in TIPOS_ITEM:
        raise HTTPException(status_code=404)
    dados = await _formulario(request)
    estado = Estado()
    campos = ["nome", "valor_compra", "quantidade"]
    escopo = f"novo-{tipo_rota}" if item_id is None else f"item-{item_id}"
    if not _confiavel(request, dados):
        return _recusar(request, repo, estado, escopo, dados, campos, MSG_EXPIRADA, 403)
    if item_id is not None and not ID_REGISTRO.fullmatch(item_id):
        return _recusar(
            request, repo, estado, escopo, dados, campos, MSG_RECUSAS["nao_encontrado"], 404
        )

    nome, erro_nome = ler_nome(dados.get("nome", ""))
    valor, erro_valor = ler_decimal(dados.get("valor_compra", ""))
    quantidade, erro_quantidade = ler_quantidade(dados.get("quantidade", ""))
    erros = {
        campo: erro
        for campo, erro in (
            ("nome", erro_nome),
            ("valor_compra", erro_valor),
            ("quantidade", erro_quantidade),
        )
        if erro
    }
    if erros:
        estado.erros[escopo] = erros
        return _recusar(request, repo, estado, escopo, dados, campos, "", 422)

    try:
        if repo is None:
            raise FalhaAoSalvarCustos
        repo.salvar_item(
            None if item_id is None else int(item_id),
            TIPOS_ITEM[tipo_rota],
            nome,
            valor,
            quantidade,
            usuario.id,
        )
    except CustoRecusado as recusa:
        if recusa.motivo == "duplicado":
            rotulo = TITULOS_ITEM[TIPOS_ITEM[tipo_rota]].lower()
            estado.erros[escopo] = {"nome": f"Já existe um item em {rotulo} com este nome."}
            return _recusar(request, repo, estado, escopo, dados, campos, "", 409)
        mensagem, status = _falha_do_banco(recusa)
        return _recusar(request, repo, estado, escopo, dados, campos, mensagem, status)
    except FalhaAoSalvarCustos as falha:
        mensagem, status = _falha_do_banco(falha)
        return _recusar(request, repo, estado, escopo, dados, campos, mensagem, status)
    return _sucesso(f"{tipo_rota}-{'novo' if item_id is None else 'editado'}")


@router.post("/custos/itens/{tipo_rota}")
async def cadastrar_item(
    request: Request,
    tipo_rota: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    return await _salvar_item(request, tipo_rota, None, usuario, repo)


@router.post("/custos/itens/{tipo_rota}/{item_id}")
async def editar_item(
    request: Request,
    tipo_rota: str,
    item_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    return await _salvar_item(request, tipo_rota, item_id, usuario, repo)
