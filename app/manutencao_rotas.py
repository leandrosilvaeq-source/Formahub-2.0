"""Tela Manutenção: indicadores e tabela de manutenções da máquina, com edição na própria linha.

A mesma rota atende o formulário comum (redireciona) e o fetch do JavaScript (X-Requested-With:
fetch, responde JSON com a linha e os indicadores já atualizados). Login, CSRF e usuário da sessão
valem nos dois caminhos; o usuário enviado pelo navegador nunca é lido.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import sessoes
from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario, origem_confiavel
from app.custos import (
    custo_de_manutencao,
    formatar_data,
    formatar_numero,
    valor_do_campo,
)
from app.custos_repositorio import (
    FalhaAoConsultarCustos,
    RepositorioCustos,
    get_repositorio_custos,
)
from app.manutencao import (
    LIMITE_COMENTARIOS,
    LIMITE_PECAS,
    TIPOS,
    formatar_custo,
    formatar_custo_hora,
    formatar_data_curta,
    formatar_duracao,
    indicadores,
    ler_formulario,
    lubrificacao_pendente_desde,
)
from app.manutencao_repositorio import (
    FalhaAoConsultarManutencao,
    FalhaAoSalvarManutencao,
    Manutencao,
    ManutencaoRecusada,
    RepositorioManutencao,
    get_repositorio_manutencao,
)
from app.pedidos import formatar_brl

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO_FORMULARIO = 8192
ID_MANUTENCAO = re.compile(r"[0-9]{1,18}")
MSG_CONSULTA = "Não foi possível carregar as manutenções agora. Tente novamente em instantes."
MSG_EXPIRADA = "A página expirou. Confira os dados e salve de novo."
MSG_INDISPONIVEL = "Não foi possível salvar agora. Tente novamente em instantes."
MSG_CONFERIR = "Confira os campos destacados."
MSG_RECUSAS = {
    "usuario": ("Seu usuário não pode fazer esta alteração.", 403),
    "nao_encontrado": ("Esta manutenção não foi encontrada. Recarregue a página.", 404),
    "invalido": ("Os dados não foram aceitos. Confira os valores e tente de novo.", 422),
}

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["brl"] = formatar_brl
templates.env.filters["data_curta"] = formatar_data_curta
templates.env.filters["duracao"] = formatar_duracao
templates.env.filters["custo"] = formatar_custo
templates.env.filters["custo_hora"] = formatar_custo_hora
templates.env.filters["data"] = formatar_data
templates.env.filters["numero"] = formatar_numero
templates.env.globals["valor_custo"] = lambda c: valor_do_campo(c, "moeda")
templates.env.globals.update(
    TIPOS=TIPOS, LIMITE_PECAS=LIMITE_PECAS, LIMITE_COMENTARIOS=LIMITE_COMENTARIOS
)


@dataclass
class Edicao:
    """Linha em edição: `alvo` é "nova" ou o id; o que foi digitado, os erros e a falha geral."""

    alvo: str
    valores: dict[str, str]
    erros: dict[str, str] = field(default_factory=dict)
    falha: str = ""


def _valores_da_manutencao(m: Manutencao | None) -> dict[str, str]:
    """Texto de cada campo do editor: o que está gravado (ou o padrão de uma linha nova)."""
    if m is None:
        return {
            "data": date.today().isoformat(),
            "tipo": "",
            "tempo": "",
            "pecas": "",
            "custo": "",
            "comentarios": "",
        }
    return {
        "data": m.data.isoformat(),
        "tipo": m.tipo or "",
        "tempo": formatar_duracao(m.minutos_parados) if m.minutos_parados is not None else "",
        "pecas": m.pecas or "",
        "custo": valor_do_campo(m.custo, "moeda"),
        "comentarios": m.comentarios or "",
    }


def _base_manutencao(repo_custos: RepositorioCustos | None):
    """A base do custo por hora, a mesma da tela Custos; None se não foi possível ler."""
    try:
        if repo_custos is None:
            raise FalhaAoConsultarCustos
        return custo_de_manutencao(repo_custos.listar())[0]
    except FalhaAoConsultarCustos:
        return None


def _contexto(
    manutencoes: list[Manutencao],
    request: Request,
    repo_custos: RepositorioCustos | None,
    edicao: Edicao | None = None,
):
    return {
        "manutencoes": manutencoes,
        "ind": indicadores(manutencoes),
        "lubrificacao_desde": lubrificacao_pendente_desde(manutencoes, date.today()),
        "base_manutencao": _base_manutencao(repo_custos),
        "edicao": edicao,
        "csrf": request.state.csrf,
        "erro": "",
        "sucesso": "",
    }


def _sem_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _pagina(request: Request, contexto: dict, status_code: int = 200):
    return _sem_cache(
        templates.TemplateResponse(request, "manutencao.html", contexto, status_code=status_code)
    )


def _listar(repo: RepositorioManutencao | None) -> list[Manutencao]:
    if repo is None:
        raise FalhaAoConsultarManutencao
    return repo.listar()


def _json(corpo: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(corpo, status_code=status, headers={"Cache-Control": "no-store"})


def _fragmento(request: Request, modelo: str, contexto: dict) -> str:
    return templates.get_template(modelo).render({"request": request, **contexto})


def _linha_html(request: Request, m: Manutencao | None, edicao: Edicao | None = None) -> str:
    return _fragmento(
        request, "manutencao_linha.html", {"m": m, "edicao": edicao, "csrf": request.state.csrf}
    )


def _indicadores_html(
    request: Request, repo: RepositorioManutencao | None, repo_custos: RepositorioCustos | None
) -> str:
    """Indicadores atualizados; vazio se não foi possível ler (a manutenção já foi gravada)."""
    try:
        manutencoes = _listar(repo)
        return _fragmento(
            request,
            "manutencao_indicadores.html",
            {
                "ind": indicadores(manutencoes),
                "lubrificacao_desde": lubrificacao_pendente_desde(manutencoes, date.today()),
                "base_manutencao": _base_manutencao(repo_custos),
                "csrf": request.state.csrf,
            },
        )
    except FalhaAoConsultarManutencao:
        return ""


@router.get("/manutencao", response_class=HTMLResponse)
def manutencao(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioManutencao | None = Depends(get_repositorio_manutencao),
    repo_custos: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    try:
        manutencoes = _listar(repo)
    except FalhaAoConsultarManutencao:
        contexto = _contexto([], request, None) | {"erro": MSG_CONSULTA}
        return _pagina(request, contexto, 503)

    # Sem JavaScript, o lápis e o botão levam a ?editar=<id> ou ?editar=nova.
    contexto = _contexto(manutencoes, request, repo_custos)
    alvo = request.query_params.get("editar", "")
    if alvo == "nova":
        contexto["edicao"] = Edicao("nova", _valores_da_manutencao(None))
    elif ID_MANUTENCAO.fullmatch(alvo):
        existente = next((m for m in manutencoes if m.id == int(alvo)), None)
        if existente is not None:
            contexto["edicao"] = Edicao(alvo, _valores_da_manutencao(existente))
    if request.query_params.get("salvo"):
        contexto["sucesso"] = "Manutenção salva."
    return _pagina(request, contexto)


@router.get("/manutencao/linha", response_class=HTMLResponse)
def linha_de_edicao(
    request: Request,
    editar: str = "",
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioManutencao | None = Depends(get_repositorio_manutencao),
):
    """A linha da tabela já em modo de edição (a mesma do servidor, usada pelo JavaScript)."""
    if editar == "nova":
        return _sem_cache(
            HTMLResponse(_linha_html(request, None, Edicao("nova", _valores_da_manutencao(None))))
        )
    if not ID_MANUTENCAO.fullmatch(editar):
        raise HTTPException(status_code=404)
    try:
        existente = next((m for m in _listar(repo) if m.id == int(editar)), None)
    except FalhaAoConsultarManutencao:
        return _sem_cache(HTMLResponse(MSG_CONSULTA, status_code=503))
    if existente is None:
        raise HTTPException(status_code=404)
    edicao = Edicao(editar, _valores_da_manutencao(existente))
    return _sem_cache(HTMLResponse(_linha_html(request, existente, edicao)))


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


async def _salvar(
    request: Request,
    alvo: str,
    usuario: Usuario,
    repo: RepositorioManutencao | None,
    repo_custos: RepositorioCustos | None,
):
    dados = await _formulario(request)
    assincrono = request.headers.get("x-requested-with") == "fetch"
    digitado = {nome: dados.get(nome, "") for nome in _valores_da_manutencao(None)}

    def recusar(mensagem: str, status: int, erros: dict[str, str] | None = None):
        if assincrono:
            return _json(
                {"ok": False, "salvo": False, "erro": mensagem, "erros": erros or {}}, status
            )
        return _pagina_de_edicao(
            request, repo, repo_custos, Edicao(alvo, digitado, erros or {}, mensagem), status
        )

    if not _confiavel(request, dados):
        return recusar(MSG_EXPIRADA, 403)

    valores, erros = ler_formulario(dados)
    if erros:
        return recusar(MSG_CONFERIR, 422, erros)

    try:
        if repo is None:
            raise FalhaAoSalvarManutencao
        salva = repo.salvar(None if alvo == "nova" else int(alvo), valores, usuario.id)
    except ManutencaoRecusada as falha:
        mensagem, status = MSG_RECUSAS.get(falha.motivo, (MSG_INDISPONIVEL, 422))
        return recusar(mensagem, status)
    except FalhaAoSalvarManutencao:
        return recusar(MSG_INDISPONIVEL, 503)

    if not assincrono:
        return RedirectResponse("/manutencao?salvo=1", status_code=303)

    # Gravar e montar a resposta são etapas separadas: gravado, uma falha ao montar a linha ou os
    # indicadores não vira "não foi possível salvar".
    try:
        linha = _linha_html(request, salva)
    except Exception as erro:
        logger.error("Manutenção salva, mas a linha não foi montada: %s", type(erro).__name__)
        linha = ""
    try:
        indicadores_html = _indicadores_html(request, repo, repo_custos)
    except Exception as erro:
        logger.error("Manutenção salva, mas os indicadores falharam: %s", type(erro).__name__)
        indicadores_html = ""
    return _json(
        {
            "ok": True,
            "salvo": True,
            "id": salva.id,
            "data": salva.data.isoformat(),
            "linha": linha,
            "indicadores": indicadores_html,
        }
    )


def _pagina_de_edicao(request, repo, repo_custos, edicao: Edicao, status: int):
    """Página inteira com a linha em edição, o que foi digitado e os erros (sem JavaScript)."""
    try:
        manutencoes = _listar(repo)
    except FalhaAoConsultarManutencao:
        return _pagina(request, _contexto([], request, None) | {"erro": MSG_CONSULTA}, 503)
    return _pagina(request, _contexto(manutencoes, request, repo_custos, edicao), status)


@router.post("/manutencao")
async def criar_manutencao(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioManutencao | None = Depends(get_repositorio_manutencao),
    repo_custos: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    return await _salvar(request, "nova", usuario, repo, repo_custos)


@router.post("/manutencao/{manutencao_id}")
async def alterar_manutencao(
    request: Request,
    manutencao_id: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioManutencao | None = Depends(get_repositorio_manutencao),
    repo_custos: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    if not ID_MANUTENCAO.fullmatch(manutencao_id):
        raise HTTPException(status_code=404)
    return await _salvar(request, manutencao_id, usuario, repo, repo_custos)
