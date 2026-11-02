"""Tela Custos: cadastro persistente dos custos da empresa, com edição manual.

Sem JavaScript: "Editar" abre o formulário daquele registro (/custos?editar=...), "Cancelar"
volta ao /custos e "Salvar" envia um POST. Sucesso redireciona (PRG) com um aviso fixo; erro
mostra a página de novo, com o formulário aberto, o que foi digitado e as mensagens.

Segurança como nas demais telas: sessão obrigatória, CSRF + origem confiável, e o responsável
pela alteração é sempre o usuário da sessão (nada de usuário vindo do navegador).
"""

import logging
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import sessoes
from app.auth.repositorio import Usuario
from app.auth.rotas import exigir_usuario, origem_confiavel
from app.custos import (
    AQUISICAO,
    CONSUMO_KWH,
    DEPRECIACAO_MANUAL,
    ENERGIA_MANUAL,
    GRUPOS,
    HORAS_MES,
    MANUTENCAO,
    MDO,
    MEI,
    PERDA,
    RESIDUAL,
    RETIRADA,
    TARIFA,
    TAXA_FIXA,
    TAXA_PERCENTUAL,
    VIDA_UTIL,
    VOLTAR_AO_CALCULADO,
    CustoHora,
    conferir_depreciacao,
    depreciacao,
    energia_por_hora,
    formatar_custo,
    formatar_custo_aprox,
    formatar_custo_curto,
    formatar_data,
    formatar_numero,
    insumos_filamento,
    insumos_itens,
    ler_campo,
    ler_data,
    ler_decimal,
    ler_horas_periodo,
    manutencao_por_hora,
    mdo_referencias,
    ordem_de_exibicao,
    revisao_das_horas,
    subtotal_maquina,
    taxa_de_venda,
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
from app.estoque_repositorio import (
    FalhaNoEstoque,
    RepositorioEstoque,
    get_repositorio_estoque,
)
from app.pedidos import formatar_brl

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO_FORMULARIO = 8192
EDITAR = re.compile(rf"insumo-[0-9]{{1,4}}|manutencao-periodo|valores-(?:{'|'.join(GRUPOS)})")
VENDA_EXEMPLO = Decimal(100)  # só para ilustrar a taxa de venda


def _guardar_estoque(
    request: Request, estoque: RepositorioEstoque | None = Depends(get_repositorio_estoque)
):
    """Deixa o estoque à mão de qualquer página de Custos (inclusive as que recusam um envio)."""
    request.state.estoque = estoque


router = APIRouter(dependencies=[Depends(_guardar_estoque)])
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
templates.env.filters["custo"] = formatar_custo
templates.env.filters["custo_aprox"] = formatar_custo_aprox
templates.env.filters["custo_curto"] = formatar_custo_curto
templates.env.filters["numero"] = formatar_numero
templates.env.filters["data"] = formatar_data
templates.env.filters["brl"] = formatar_brl

# Seção (âncora) de cada grupo de valores.
ANCORAS = {
    "perdas": "perdas",
    "taxas": "taxas",
    "energia": "energia",
    "energia-ajuste": "energia",
    "horas": "horas",
    "depreciacao": "depreciacao",
    "depreciacao-ajuste": "depreciacao",
    "mdo": "mdo",
    "mei": "mei",
    "retirada": "retirada",
    "manutencao-ajuste": "manutencao",
}
# Avisos de sucesso: o redirecionamento leva só um código, nunca texto.
AVISOS = {f"valores-{grupo}": "Valor atualizado." for grupo in GRUPOS}
AVISOS.update({f"calculado-{grupo}": "Voltou ao valor calculado." for grupo in VOLTAR_AO_CALCULADO})
AVISOS.update(
    {
        "insumo-ajuste": "Ajuste manual do insumo salvo. As compras originais não mudaram.",
        "insumo-calculado": "Ajuste manual removido.",
        "manutencao-periodo": "Período de referência da manutenção atualizado.",
    }
)
ANCORAS_AVISO = {
    **{f"valores-{g}": a for g, a in ANCORAS.items()},
    **{f"calculado-{g}": ANCORAS[g] for g in VOLTAR_AO_CALCULADO},
    "insumo-ajuste": "insumos",
    "insumo-calculado": "insumos",
    "manutencao-periodo": "manutencao",
}

MSG_EXPIRADA = "A página expirou. Confira os dados e salve de novo."
MSG_INDISPONIVEL = "Não foi possível salvar agora. Tente novamente em instantes."
MSG_CONSULTA = "Não foi possível carregar os custos agora. Tente novamente em instantes."
MSG_ESTOQUE = "Não foi possível carregar as compras do estoque agora."
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
        insumos = _insumos(request.state.estoque, custos)
        contexto.update(_montar(custos, insumos, fotos=_fotos(request.state.estoque, insumos)))
    return _sem_cache(request, contexto, status)


def _insumos(estoque: RepositorioEstoque | None, custos: Custos):
    """(filamentos, {categoria: itens}) das compras do estoque; None se o estoque não responder."""
    try:
        if estoque is None:
            raise FalhaNoEstoque
        dados = estoque.listar_estoque()
    except FalhaNoEstoque:
        return None
    return (
        insumos_filamento(dados.filamentos, custos.ajustes),
        insumos_itens(dados.itens, custos.ajustes),
    )


def _fotos(estoque: RepositorioEstoque | None, insumos) -> dict[str, str]:
    """Caminho -> URL assinada (privada e temporária) das fotos de acessórios e embalagens.

    Sem as fotos (estoque fora do ar, arquivo ausente) a tabela continua útil, com "Sem foto".
    """
    if estoque is None or insumos is None:
        return {}
    caminhos = [
        i.imagem_caminho for lista in insumos[1].values() for i in lista if i.imagem_caminho
    ]
    if not caminhos:
        return {}
    try:
        return estoque.assinar_imagens(caminhos)
    except FalhaNoEstoque:
        return {}


def _todos(insumos) -> list:
    """Os insumos numa ordem fixa (a posição identifica o formulário aberto)."""
    filamentos, itens = insumos
    return [*filamentos, *itens["acessorio"], *itens["embalagem"]]


def _montar(custos: Custos, insumos, hoje: date | None = None, fotos=None) -> dict:
    """Valores e cálculos mostrados na tela, sempre a partir do que está salvo."""
    p = custos.parametros
    # Se uma chave faltar (banco alterado fora do app), a tela mostra "—" em vez de inventar.
    horas = p.get(HORAS_MES)
    consumo, tarifa = p.get(CONSUMO_KWH), p.get(TARIFA)
    calculado = None
    if horas and consumo is not None and tarifa is not None:
        calculado = energia_por_hora(consumo, tarifa, horas)
    energia = CustoHora(calculado, p.get(ENERGIA_MANUAL), ENERGIA_MANUAL in custos.provisorios)

    criterios = [p.get(chave) for chave in (AQUISICAO, RESIDUAL, VIDA_UTIL)]
    deprec = None
    if horas and all(c is not None for c in criterios):
        deprec = depreciacao(*criterios, horas)
    deprec_hora = CustoHora(
        deprec.por_hora if deprec else None,
        p.get(DEPRECIACAO_MANUAL),
        DEPRECIACAO_MANUAL in custos.provisorios,
    )

    # Manutenção: custo/h = gastos do período / horas do mesmo período (sem dados: "A definir").
    periodo = custos.periodo
    gastos_periodo = [
        g for g in custos.gastos if periodo and periodo.inicio <= g.data <= periodo.fim
    ]
    manutencao = CustoHora(manutencao_por_hora(custos.gastos, periodo), p.get(MANUTENCAO))

    subtotal, completo = subtotal_maquina(energia.usado, deprec_hora.usado, manutencao.usado)
    mdo = p.get(MDO)
    mei, retirada = p.get(MEI), p.get(RETIRADA)
    fixa, percentual = p.get(TAXA_FIXA), p.get(TAXA_PERCENTUAL)
    perda = p.get(PERDA)

    data_base = custos.datas.get(HORAS_MES)
    proxima, vencida = revisao_das_horas(data_base, hoje or date.today())
    return {
        "parametros": p,
        "grupos": GRUPOS,
        "insumos": insumos,  # None = estoque indisponível
        "todos_insumos": _todos(insumos) if insumos else [],
        "fotos_itens": fotos or {},
        "filamentos_em_ordem": ordem_de_exibicao(insumos[0]) if insumos else [],
        "perda": perda,
        "energia": energia,
        "depreciacao": deprec,
        "depreciacao_hora": deprec_hora,
        "manutencao": manutencao,
        "periodo": periodo,
        "gastos": custos.gastos,
        "gastos_periodo": len(gastos_periodo),
        "total_gastos_periodo": sum((g.valor for g in gastos_periodo), Decimal(0)),
        # Manutenções do período com custo "A definir": ficam fora da soma (subtotal conhecido).
        "sem_custo_periodo": sum(
            1
            for d in custos.manutencoes_sem_custo
            if periodo and periodo.inicio <= d <= periodo.fim
        ),
        "subtotal_hora": subtotal,
        "subtotal_completo": completo,
        "mdo": mdo,
        "mdo_referencias": mdo_referencias(mdo) if mdo is not None else [],
        "horas": horas,
        "horas_data_base": data_base,
        "horas_proxima": proxima,
        "horas_vencida": vencida,
        "mei": mei,
        "retirada": retirada,
        "total_mensal": mei,  # único custo fixo indireto; a retirada é meta e fica de fora
        "taxa_fixa": fixa,
        "taxa_percentual": percentual,
        "taxa_exemplo": (
            taxa_de_venda(VENDA_EXEMPLO, fixa, percentual)
            if fixa is not None and percentual is not None
            else None
        ),
        "venda_exemplo": VENDA_EXEMPLO,
        "msg_estoque": MSG_ESTOQUE,
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
    return RedirectResponse(f"/custos?salvo={codigo}#{ANCORAS_AVISO[codigo]}", status_code=303)


def _falha_do_banco(erro: Exception) -> tuple[str, int]:
    if isinstance(erro, CustoRecusado):
        status = {"usuario": 403, "nao_encontrado": 404, "invalido": 422}.get(erro.motivo, 422)
        return MSG_RECUSAS.get(erro.motivo, MSG_INDISPONIVEL), status
    return MSG_INDISPONIVEL, 503


def _gravar(request, repo, estado, escopo, dados, campos, gravar, codigo):
    """Executa a gravação; recusa do banco mostra o formulário de novo, sucesso redireciona."""
    try:
        if repo is None:
            raise FalhaAoSalvarCustos
        gravar(repo)
    except (CustoRecusado, FalhaAoSalvarCustos) as falha:
        mensagem, status = _falha_do_banco(falha)
        return _recusar(request, repo, estado, escopo, dados, campos, mensagem, status)
    return _sucesso(codigo)


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
    if request.headers.get("x-requested-with") == "fetch" and grupo in CARDS_NO_LUGAR:
        return _valores_assincrono(request, usuario, repo, grupo, dados, calculado=False)
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
    return _gravar(
        request,
        repo,
        estado,
        escopo,
        dados,
        campos,
        lambda r: r.salvar_parametros(valores, usuario.id),
        escopo,
    )


@router.post("/custos/valores/{grupo}/calculado")
async def voltar_ao_calculado(
    request: Request,
    grupo: str,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    """Apaga o ajuste manual: o custo volta a ser o calculado (nada é zerado)."""
    if grupo not in VOLTAR_AO_CALCULADO:
        raise HTTPException(status_code=404)
    dados = await _formulario(request)
    if request.headers.get("x-requested-with") == "fetch" and grupo in CARDS_NO_LUGAR:
        return _valores_assincrono(request, usuario, repo, grupo, dados, calculado=True)
    estado, escopo = Estado(), f"valores-{grupo}"
    if not _confiavel(request, dados):
        return _recusar(request, repo, estado, escopo, dados, [], MSG_EXPIRADA, 403)
    chave = GRUPOS[grupo][0].chave
    return _gravar(
        request,
        repo,
        estado,
        escopo,
        dados,
        [],
        lambda r: r.salvar_parametros({chave: None}, usuario.id),
        f"calculado-{grupo}",
    )


def _escopo_do_insumo(request: Request, repo, chave: str):
    """(escopo, erro): o insumo precisa existir nas compras de hoje (a chave vem do navegador)."""
    try:
        custos_ = repo.listar() if repo is not None else None
    except FalhaAoConsultarCustos:
        custos_ = None
    insumos = _insumos(request.state.estoque, custos_) if custos_ is not None else None
    if insumos is None:
        return None, MSG_ESTOQUE
    for posicao, insumo in enumerate(_todos(insumos)):
        if insumo.chave == chave:
            return f"insumo-{posicao}", ""
    return None, MSG_RECUSAS["nao_encontrado"]


def _cartao_html(request: Request, repo, chave: str) -> str:
    """O card do filamento já atualizado (vazio se não der para reler: a tela então recarrega)."""
    try:
        custos_ = repo.listar()
    except FalhaAoConsultarCustos:
        return ""
    insumos = _insumos(request.state.estoque, custos_)
    if insumos is None:
        return ""
    insumo = next((i for i in _todos(insumos) if i.chave == chave), None)
    if insumo is None:
        return ""
    contexto = {"estado": Estado(), "csrf": request.state.csrf, "insumo": insumo}
    contexto.update(_montar(custos_, insumos))
    return templates.get_template("custos_cartao.html").render(contexto)


# Grupos editados no próprio card (por fetch): grupo -> nome do card na página.
CARDS_NO_LUGAR = {
    "energia-ajuste": "energia",
    "depreciacao-ajuste": "depreciacao",
    "manutencao-ajuste": "manutencao",
    "perdas": "perdas",
    "mdo": "mdo",
}


def _cartao_de_valor_html(request: Request, repo, grupo: str) -> str:
    """O card (Energia, Taxa de perda ou MDO) já atualizado; vazio se não der para montar."""
    try:
        custos_ = repo.listar()
    except FalhaAoConsultarCustos:
        return ""
    insumos = _insumos(request.state.estoque, custos_)
    contexto = {"estado": Estado(), "csrf": request.state.csrf, "cartao": CARDS_NO_LUGAR[grupo]}
    contexto.update(_montar(custos_, insumos))
    return templates.get_template("custos_cartao.html").render(contexto)


def _valores_assincrono(request, usuario, repo, grupo: str, dados, calculado: bool):
    """Salva (ou volta ao calculado) um valor de card por fetch: mesmas validações e segurança.

    Como no ajuste de insumos, gravar e montar o card são etapas separadas: gravado, uma falha
    ao montar o card não vira "não foi possível salvar".
    """

    def recusa(mensagem: str, status: int):
        return _resposta_json({"ok": False, "salvo": False, "erro": mensagem}, status)

    try:
        if not _confiavel(request, dados):
            return recusa(MSG_EXPIRADA, 403)
        campo = GRUPOS[grupo][0]
        if calculado:
            valor = None
        else:
            valor, erro = ler_campo(campo, dados.get(campo.nome, ""))
            if erro:
                return recusa(erro, 422)
        try:
            if repo is None:
                raise FalhaAoSalvarCustos
            repo.salvar_parametros({campo.chave: valor}, usuario.id)
        except (CustoRecusado, FalhaAoSalvarCustos) as falha:
            mensagem, status = _falha_do_banco(falha)
            return recusa(mensagem, status)
    except Exception as erro:  # antes de gravar: nada foi salvo; registra só o tipo do erro
        logger.error("Falha inesperada ao salvar o valor do card: %s", type(erro).__name__)
        return recusa(MSG_INDISPONIVEL, 500)

    try:
        html = _cartao_de_valor_html(request, repo, grupo)
    except Exception as erro:
        logger.error("Valor salvo, mas o card não foi montado: %s", type(erro).__name__)
        html = ""
    return _resposta_json({"ok": True, "salvo": True, "html": html})


def _resposta_json(corpo: dict, status: int = 200):
    return JSONResponse(corpo, status_code=status, headers={"Cache-Control": "no-store"})


def _ajuste_assincrono(request, usuario, repo, dados, com_valor: bool):
    """Mesma rota e mesmas validações, respondendo JSON para a edição no próprio card.

    "salvo" diz se a gravação foi concluída. Gravar e montar o card são etapas separadas: se a
    gravação deu certo e só a atualização visual falhou, a resposta continua sendo de sucesso
    (sem "html": a tela mostra o valor e avisa), nunca "não foi possível salvar".
    """
    try:
        return _ajuste_assincrono_gravando(request, usuario, repo, dados, com_valor)
    except Exception as erro:  # antes de gravar: nada foi salvo; registra só o tipo do erro
        logger.error("Falha inesperada no ajuste do insumo: %s", type(erro).__name__)
        return _resposta_json({"ok": False, "salvo": False, "erro": MSG_INDISPONIVEL}, 500)


def _ajuste_assincrono_gravando(request, usuario, repo, dados, com_valor: bool):
    def recusa(mensagem: str, status: int):
        return _resposta_json({"ok": False, "salvo": False, "erro": mensagem}, status)

    if not _confiavel(request, dados):
        return recusa(MSG_EXPIRADA, 403)
    chave = dados.get("chave", "")
    _escopo, erro_insumo = _escopo_do_insumo(request, repo, chave)
    if erro_insumo:
        return recusa(erro_insumo, 503 if erro_insumo == MSG_ESTOQUE else 404)
    valor = None
    if com_valor:
        valor, erro = ler_decimal(dados.get("valor", ""))
        if erro:
            return recusa(erro, 422)
    try:
        if repo is None:
            raise FalhaAoSalvarCustos
        repo.salvar_ajuste_insumo(chave, valor, usuario.id)
    except (CustoRecusado, FalhaAoSalvarCustos) as falha:
        mensagem, status = _falha_do_banco(falha)
        return recusa(mensagem, status)

    # Gravado. Daqui em diante, nada pode virar "falha ao salvar".
    try:
        html = _cartao_html(request, repo, chave)
    except Exception as erro:
        logger.error("Valor salvo, mas o card não foi montado: %s", type(erro).__name__)
        html = ""
    return _resposta_json({"ok": True, "salvo": True, "html": html})


async def _ajuste_de_insumo(request, usuario, repo, com_valor: bool):
    dados = await _formulario(request)
    if request.headers.get("x-requested-with") == "fetch":
        return _ajuste_assincrono(request, usuario, repo, dados, com_valor)
    estado, campos = Estado(), ["valor"]
    if not _confiavel(request, dados):
        estado.erro_pagina = MSG_EXPIRADA
        return _pagina(request, repo, estado, 403)
    chave = dados.get("chave", "")
    escopo, erro_insumo = _escopo_do_insumo(request, repo, chave)
    if erro_insumo:
        estado.erro_pagina = erro_insumo
        return _pagina(request, repo, estado, 404 if erro_insumo != MSG_ESTOQUE else 503)

    valor = None
    if com_valor:
        valor, erro = ler_decimal(dados.get("valor", ""))
        if erro:
            estado.erros[escopo] = {"valor": erro}
            return _recusar(request, repo, estado, escopo, dados, campos, "", 422)
    return _gravar(
        request,
        repo,
        estado,
        escopo,
        dados,
        campos,
        lambda r: r.salvar_ajuste_insumo(chave, valor, usuario.id),
        "insumo-ajuste" if com_valor else "insumo-calculado",
    )


@router.post("/custos/insumos/ajuste")
async def ajustar_insumo(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    return await _ajuste_de_insumo(request, usuario, repo, com_valor=True)


@router.post("/custos/insumos/calculado")
async def insumo_ao_calculado(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    return await _ajuste_de_insumo(request, usuario, repo, com_valor=False)


@router.post("/custos/manutencao/periodo")
async def definir_periodo(
    request: Request,
    usuario: Usuario = Depends(exigir_usuario),
    repo: RepositorioCustos | None = Depends(get_repositorio_custos),
):
    dados = await _formulario(request)
    estado, escopo, campos = Estado(), "manutencao-periodo", ["inicio", "fim", "horas"]
    if not _confiavel(request, dados):
        return _recusar(request, repo, estado, escopo, dados, campos, MSG_EXPIRADA, 403)
    inicio, erro_inicio = ler_data(dados.get("inicio", ""))
    fim, erro_fim = ler_data(dados.get("fim", ""))
    horas, erro_horas = ler_horas_periodo(dados.get("horas", ""))
    if not (erro_inicio or erro_fim) and fim < inicio:
        erro_fim = "A data final não pode ser anterior à inicial."
    erros = {
        campo: erro
        for campo, erro in (("inicio", erro_inicio), ("fim", erro_fim), ("horas", erro_horas))
        if erro
    }
    if erros:
        estado.erros[escopo] = erros
        return _recusar(request, repo, estado, escopo, dados, campos, "", 422)
    return _gravar(
        request,
        repo,
        estado,
        escopo,
        dados,
        campos,
        lambda r: r.definir_periodo_manutencao(inicio, fim, horas, usuario.id),
        escopo,
    )
